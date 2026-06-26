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
from collections.abc import AsyncGenerator
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings


class _RedisProxy:
    """Redis 客户端代理：自动检测 event loop 变化并重建客户端.

    Celery AsyncTask 每个任务创建新 event loop，全局 Redis 客户端的连接池
    绑定到旧 loop 后会抛出 "Event loop is closed"。本代理在每次方法调用时
    检测当前 loop，若与客户端绑定的 loop 不一致则重建。

    对调用方透明：所有 Redis 异步方法（get/set/hset/keys 等）均可直接使用。
    """

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._client: aioredis.Redis | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def _ensure_client(self) -> aioredis.Redis:
        """获取当前 event loop 的 Redis 客户端，必要时重建."""
        current_loop = asyncio.get_running_loop()
        need_recreate = (
            self._client is None
            or self._loop is not current_loop
            or (self._loop is not None and getattr(self._loop, "is_closed", False))
        )
        if need_recreate:
            if self._client is not None:
                try:
                    await self._client.aclose()
                except Exception:  # noqa: BLE001
                    pass
            self._client = aioredis.Redis(**self._kwargs)
            self._loop = current_loop
        return self._client

    def __getattr__(self, name: str) -> Any:
        """代理所有 Redis 方法调用，确保客户端有效后再委托."""
        # dunder 方法直接报错，避免被误代理
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )

        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            client = await self._ensure_client()
            return await getattr(client, name)(*args, **kwargs)

        return _wrapper


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
