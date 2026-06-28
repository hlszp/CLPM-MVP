"""RealtimeSubscriber 单元测试.

覆盖：
- start/stop 生命周期
- SIGNALR_ENABLED=False 时不启动
- _cache_value 写入 Redis（key 格式 + TTL）
- get_cached_values 从 Redis 批量读取
- _get_active_tags 查询数据库
- 单例管理
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source.realtime_subscriber import (
    RealtimeSubscriber,
    _REDIS_KEY_PREFIX,
    _REDIS_TTL,
    get_subscriber,
    start_subscriber,
    stop_subscriber,
)


# ---------------------------------------------------------------------------
# Fake Redis（轻量级，仅支持 setex/mget）
# ---------------------------------------------------------------------------


class _FakeRedis:
    """轻量级 Redis mock，支持 setex/mget."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._data.get(k) for k in keys]


# ---------------------------------------------------------------------------
# 生命周期测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_skips_when_disabled():
    """SIGNALR_ENABLED=False 时不应启动后台任务."""
    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        mock_settings.SIGNALR_ENABLED = False
        sub = RealtimeSubscriber()
        await sub.start()
        assert sub._running is False
        assert sub._task is None


@pytest.mark.asyncio
async def test_start_skips_when_hub_url_empty():
    """SIGNALR_HUB_URL 为空时不应启动."""
    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        mock_settings.SIGNALR_ENABLED = True
        mock_settings.SIGNALR_HUB_URL = ""
        sub = RealtimeSubscriber()
        await sub.start()
        assert sub._running is False


@pytest.mark.asyncio
async def test_stop_is_safe_when_not_started():
    """未启动时调用 stop 不应报错."""
    sub = RealtimeSubscriber()
    await sub.stop()  # 不应抛出异常


@pytest.mark.asyncio
async def test_stop_cancels_running_task():
    """stop 应取消正在运行的任务."""
    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        mock_settings.SIGNALR_ENABLED = True
        mock_settings.SIGNALR_HUB_URL = "ws://localhost:8100/signalr/realValueForClpmHub"
        mock_settings.SIGNALR_RECONNECT_INTERVAL = 1

        sub = RealtimeSubscriber()

        # Mock _run 为长时间运行的任务
        async def _long_run():
            import asyncio
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise

        sub._run = _long_run
        await sub.start()
        assert sub._task is not None
        assert sub._running is True

        await sub.stop()
        assert sub._running is False
        assert sub._task is None


# ---------------------------------------------------------------------------
# _cache_value 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_value_writes_to_redis():
    """_cache_value 应以正确 key 格式和 TTL 写入 Redis."""
    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()

    item = {
        "tagCode": "LIC-101.PV",
        "value": "50.5",
        "quality": 0,
        "collectTime": "2026-06-28T08:00:00Z",
    }

    with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
        await sub._cache_value(item)

    expected_key = f"{_REDIS_KEY_PREFIX}LIC-101.PV"
    assert expected_key in fake_redis._data
    cached = json.loads(fake_redis._data[expected_key])
    assert cached["tagCode"] == "LIC-101.PV"
    assert cached["value"] == "50.5"
    assert cached["quality"] == 0


@pytest.mark.asyncio
async def test_cache_value_skips_empty_tag_code():
    """tagCode 为空时不应写入 Redis."""
    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()

    item = {"tagCode": "", "value": "50.5"}

    with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
        await sub._cache_value(item)

    assert len(fake_redis._data) == 0


# ---------------------------------------------------------------------------
# get_cached_values 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cached_values_returns_cached_items():
    """get_cached_values 应返回已缓存的实时值."""
    fake_redis = _FakeRedis()
    fake_redis._data[f"{_REDIS_KEY_PREFIX}LIC-101.PV"] = json.dumps({
        "tagCode": "LIC-101.PV",
        "value": "50.5",
        "quality": 0,
        "collectTime": "2026-06-28T08:00:00Z",
    })

    sub = RealtimeSubscriber()
    with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
        result = await sub.get_cached_values(["LIC-101.PV", "MISSING.TAG"])

    assert len(result) == 1
    assert result[0]["tagCode"] == "LIC-101.PV"
    assert result[0]["value"] == "50.5"


@pytest.mark.asyncio
async def test_get_cached_values_empty_input():
    """空输入应返回空列表."""
    sub = RealtimeSubscriber()
    result = await sub.get_cached_values([])
    assert result == []


@pytest.mark.asyncio
async def test_get_cached_values_handles_invalid_json():
    """损坏的 JSON 应被跳过."""
    fake_redis = _FakeRedis()
    fake_redis._data[f"{_REDIS_KEY_PREFIX}BAD.TAG"] = "not-a-valid-json{{"

    sub = RealtimeSubscriber()
    with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
        result = await sub.get_cached_values(["BAD.TAG"])

    assert result == []


# ---------------------------------------------------------------------------
# _get_active_tags 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_tags_returns_tag_names():
    """_get_active_tags 应返回 is_linked=True 的 tag_name 列表."""
    sub = RealtimeSubscriber()

    mock_result = MagicMock()
    mock_result.all.return_value = [("LIC-101.PV",), ("TIC-101.PV",)]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.core.db.AsyncSessionLocal", return_value=mock_session_ctx):
        tags = await sub._get_active_tags()

    assert tags == ["LIC-101.PV", "TIC-101.PV"]


@pytest.mark.asyncio
async def test_get_active_tags_returns_empty_on_error():
    """数据库异常时应返回空列表."""
    sub = RealtimeSubscriber()

    with patch("app.core.db.AsyncSessionLocal", side_effect=Exception("DB error")):
        tags = await sub._get_active_tags()

    assert tags == []


# ---------------------------------------------------------------------------
# 单例管理测试
# ---------------------------------------------------------------------------


def test_get_subscriber_returns_singleton():
    """get_subscriber 应返回全局单例."""
    # 重置单例
    import app.services.data_source.realtime_subscriber as mod
    original = mod._subscriber
    mod._subscriber = None
    try:
        s1 = get_subscriber()
        s2 = get_subscriber()
        assert s1 is s2
        assert isinstance(s1, RealtimeSubscriber)
    finally:
        mod._subscriber = original


@pytest.mark.asyncio
async def test_start_subscriber_delegates_to_singleton():
    """start_subscriber 应委托到全局单例的 start."""
    import app.services.data_source.realtime_subscriber as mod
    original = mod._subscriber
    mod._subscriber = None
    try:
        with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
            mock_settings.SIGNALR_ENABLED = False
            await start_subscriber()
            sub = get_subscriber()
            assert isinstance(sub, RealtimeSubscriber)
    finally:
        if mod._subscriber is not None:
            await mod._subscriber.stop()
        mod._subscriber = original


@pytest.mark.asyncio
async def test_stop_subscriber_resets_singleton():
    """stop_subscriber 应停止并重置单例."""
    import app.services.data_source.realtime_subscriber as mod
    original = mod._subscriber
    mod._subscriber = None
    try:
        # 先创建一个单例
        sub = get_subscriber()
        assert mod._subscriber is sub

        await stop_subscriber()
        assert mod._subscriber is None
    finally:
        mod._subscriber = original
