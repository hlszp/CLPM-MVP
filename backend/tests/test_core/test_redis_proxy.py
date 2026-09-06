"""_RedisProxy 客户端生命周期测试（数据链路整改 R01）.

审查报告 §5 复现：``is_closed`` 方法对象被当布尔 → 同一 loop 内每次 GET/pipeline
都重建客户端（5 GET + 5 pipeline = 11 个客户端）。修复后应：
- 同 loop 连续 5 GET + 5 pipeline 仅建 1 个客户端（复用连接池）；
- 换 loop 重建（不跨 loop await 旧池，旧客户端由 GC 回收）；
- 旧 loop 关闭后在新 loop 重建，不抛跨循环异常；
- 同步 ``pipeline()`` 入口与异步方法共用同一客户端。

测试调用真实 ``_RedisProxy`` 实现，仅注入 Fake Redis 类（不连真实 Redis）。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import patch

from app.core.redis import _RedisProxy


class _FakeRedisClient:
    """记录实例创建/关闭的假 aioredis.Redis（不建连接、不绑 loop）."""

    made: list[_FakeRedisClient] = []
    closed: int = 0

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        type(self).made.append(self)

    async def aclose(self) -> None:
        type(self).closed += 1

    async def get(self, key: str) -> str:
        return "ok"

    def pipeline(self) -> _FakeRedisClient:
        return self


def _make_proxy() -> _RedisProxy:
    _FakeRedisClient.made = []
    _FakeRedisClient.closed = 0
    return _RedisProxy(host="fake", port=6379, decode_responses=True)


def _patched_aioredis():
    """把 app.core.redis 命名空间内的 aioredis 替换为只含 Fake 类的命名空间."""
    return patch("app.core.redis.aioredis", SimpleNamespace(Redis=_FakeRedisClient))


def _run_in_thread(coro_factory) -> asyncio.AbstractEventLoop:
    """在独立线程 + 全新事件循环中执行协程（模拟 Celery 每任务新 loop）.

    Returns:
        该线程使用的（已随线程结束而关闭的）事件循环对象。
    """
    holder: dict[str, asyncio.AbstractEventLoop] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        holder["loop"] = loop
        try:
            loop.run_until_complete(coro_factory())
        finally:
            loop.close()

    t = threading.Thread(target=_runner)
    t.start()
    t.join(timeout=10)
    return holder["loop"]


async def test_same_loop_reuses_single_client_across_get_and_pipeline():
    """同 loop 连续 5 GET + 5 pipeline 仅建 1 个客户端（R01 复现的反向断言）."""
    proxy = _make_proxy()
    with _patched_aioredis():
        first = await proxy._ensure_client()
        assert proxy._need_recreate() is False, "同 loop 未关闭时不应重建"

        for _ in range(5):
            assert await proxy.get("tag") == "ok"
        for _ in range(5):
            pipe = proxy.pipeline()
            assert pipe is first, "pipeline 应复用当前客户端"

        assert len(_FakeRedisClient.made) == 1, "5 GET + 5 pipeline 只允许 1 个客户端"
        assert proxy._client is first
        assert _FakeRedisClient.closed == 0


async def test_new_loop_rebuilds_client_without_cross_loop_aclose():
    """换 loop 后重建客户端；不跨 loop await 旧池（旧客户端由 GC 回收）."""
    proxy = _make_proxy()

    async def create_client() -> None:
        await proxy._ensure_client()

    async def use_client() -> None:
        await proxy.get("tag")

    with _patched_aioredis():
        # 线程 A（独立 loop）：创建首个客户端
        _run_in_thread(create_client)
        first = proxy._client
        assert first is not None

        # 线程 B（另一独立 loop）：检测到 loop 变化 → 重建
        _run_in_thread(use_client)

        assert len(_FakeRedisClient.made) == 2, "换 loop 必须重建客户端"
        assert proxy._client is not first
        assert proxy._client is _FakeRedisClient.made[-1]
        # 旧客户端绑定旧 loop：不跨 loop await aclose（丢弃由 GC 回收）
        assert _FakeRedisClient.closed == 0


async def test_closed_loop_rebuild_does_not_raise_cross_loop_error():
    """旧 loop 已关闭时，在新 loop 上重建不抛跨循环异常."""
    proxy = _make_proxy()

    async def create_client() -> None:
        await proxy._ensure_client()

    async def use_client() -> None:
        # 旧 loop 已关闭：重建路径不得 await 旧客户端（否则跨循环异常）
        assert await proxy.get("tag") == "ok"

    with _patched_aioredis():
        loop1 = _run_in_thread(create_client)
        first = proxy._client
        assert loop1.is_closed(), "线程退出时旧 loop 应已关闭"

        _run_in_thread(use_client)

        assert proxy._client is not first
        assert len(_FakeRedisClient.made) == 2
        assert _FakeRedisClient.closed == 0, "旧 loop 客户端直接丢弃，不跨 loop aclose"


async def test_sync_pipeline_reuses_current_client():
    """同步 pipeline() 入口复用当前 loop 已绑定的客户端，不重建."""
    proxy = _make_proxy()
    with _patched_aioredis():
        first = await proxy._ensure_client()
        pipe = proxy.pipeline()
        assert pipe is first
        assert len(_FakeRedisClient.made) == 1


async def test_fresh_proxy_pipeline_creates_client_then_reuses():
    """全新 proxy 的同步 pipeline() 首次调用创建客户端（含探测用临时实例），
    之后同 loop 的异步调用与再次 pipeline 不再创建新实例（复用生效）."""
    proxy = _make_proxy()
    with _patched_aioredis():
        pipe = proxy.pipeline()
        assert pipe is proxy._client
        assert proxy._loop is asyncio.get_running_loop()
        baseline = len(_FakeRedisClient.made)  # 首次访问含方法探测用的临时实例
        await proxy.get("k")
        assert proxy.pipeline() is proxy._client
        assert len(_FakeRedisClient.made) == baseline, "后续调用不得再新建客户端"
