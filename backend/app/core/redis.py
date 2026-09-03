"""Async Redis client wrapper.

Provides a singleton ``redis_client`` and a FastAPI dependency ``get_redis``.

Celery 兼容：Celery AsyncTask 为每个任务创建新 event loop 并在结束后关闭。
Redis 客户端的连接池绑定到创建时的 loop，跨任务复用会导致
"Event loop is closed" 错误。使用 ``_RedisProxy`` 自动检测 loop 变化并重建客户端。

Used for:
- Login failure counting (``login_fail:{username}``)
- Token blacklist (``token_blacklist:{jti}``)
- User token tracking (``user_tokens:{user_id}``)
- L1 DataBlock cache
- Task tracker
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings


class _RedisProxy:
    """Redis 客户端代理：自动检测 event loop 变化并重建客户端.

    Celery AsyncTask 每个任务创建新 event loop，全局 Redis 客户端的连接池
    绑定到旧 loop 后会抛出 "Event loop is closed"。本代理在每次方法调用时
    检测当前 loop，若与客户端绑定的 loop 不一致则重建。

    对调用方透明：
    - 异步方法（get/set/hset/keys/scan 等）通过 async wrapper 委托
    - 同步方法（pipeline 返回 Pipeline 对象）通过 sync wrapper 委托
      （Redis.pipeline() 是 sync 方法，返回的 Pipeline 对象持有连接池引用，
       其 execute() 是 async；需确保 sync 调用时客户端已绑定当前 loop）
    """

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._client: aioredis.Redis | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _need_recreate(self) -> bool:
        """判断是否需要重建客户端（sync/async 共用）."""
        if self._client is None:
            return True
        if self._loop is None:
            return True
        if getattr(self._loop, "is_closed", False):
            return True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            # 不在 async 上下文中（如模块加载时），无需重建
            return False
        return self._loop is not current_loop

    def _recreate_sync(self) -> aioredis.Redis:
        """同步重建客户端（无法 await aclose 旧客户端，直接丢弃）.

        旧 loop 已关闭时其连接池也已失效，直接丢弃由 GC 回收即可。
        """
        self._client = aioredis.Redis(**self._kwargs)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        return self._client

    async def _ensure_client(self) -> aioredis.Redis:
        """获取当前 event loop 的 Redis 客户端，必要时重建（async 版本）.

        重建时仅当旧客户端绑定在当前 loop 上才 await aclose；旧客户端绑定
        其他/已关闭 loop 时直接丢弃（跨 loop await 其连接池清理回调会永久
        挂起——pre-push 门禁中间歇性 pytest 超时的根因，2026-09-03），
        由 GC 回收，与 _recreate_sync 的处置口径一致。
        """
        if self._need_recreate():
            if self._client is not None:
                old_loop_closed = getattr(self._loop, "is_closed", True)
                current_loop: asyncio.AbstractEventLoop | None = None
                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                same_loop = (
                    self._loop is not None
                    and not old_loop_closed
                    and current_loop is not None
                    and self._loop is current_loop
                )
                if same_loop:
                    try:
                        await self._client.aclose()
                    except Exception:  # noqa: BLE001
                        pass
                # else：旧客户端属于其他/已关闭 loop，直接丢弃由 GC 回收
            self._client = aioredis.Redis(**self._kwargs)
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
        return self._client  # type: ignore[return-value]

    def _ensure_client_sync(self) -> aioredis.Redis:
        """同步获取客户端（用于 sync 方法如 pipeline）.

        若需重建：直接丢弃旧客户端（无法 await aclose），新建并绑定当前 loop。
        """
        if self._need_recreate():
            self._recreate_sync()
        return self._client  # type: ignore[return-value]

    def __getattr__(self, name: str) -> Any:
        """代理所有 Redis 方法调用，按 sync/async 分别委托.

        - coroutine function（如 get/set/scan）：返回 async wrapper
        - 普通 method（如 pipeline）：返回 sync wrapper，返回值原样透传
        """
        # dunder 方法直接报错，避免被误代理
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        # 通过临时客户端检查方法类型（Redis 实例化本身不绑定 loop）
        probe = self._client or aioredis.Redis(**self._kwargs)
        attr = getattr(probe, name)
        is_async = inspect.iscoroutinefunction(attr)

        if is_async:
            # 异步方法：先 await _ensure_client 再 await 方法调用
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                client = await self._ensure_client()
                return await getattr(client, name)(*args, **kwargs)

            return _async_wrapper

        # 同步方法（如 pipeline）：用 _ensure_client_sync 获取客户端后直接调用
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            client = self._ensure_client_sync()
            return getattr(client, name)(*args, **kwargs)

        return _sync_wrapper


# Singleton async Redis client proxy (Celery-safe).
redis_client: _RedisProxy = _RedisProxy(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD or None,
    decode_responses=True,
)


async def get_redis() -> AsyncGenerator[Any, None]:
    """FastAPI dependency that yields the shared Redis client."""
    yield redis_client


async def close_redis() -> None:
    """Close the Redis connection pool (call on application shutdown)."""
    if redis_client._client is not None:
        await redis_client._client.aclose()
        redis_client._client = None
        redis_client._loop = None
