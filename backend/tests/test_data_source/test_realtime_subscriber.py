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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from app.services.data_source.realtime_subscriber import (
    _CONTROL_CHANNEL,
    _GAP_BACKFILL_LOCK_KEY,
    _PUBSUB_CHANNEL,
    _REDIS_KEY_PREFIX,
    _REFRESH_RESULT_KEY,
    _SUBSCRIBER_LEADER_LOCK_KEY,
    RealtimeSubscriber,
    _normalize_ts,
    _ShardState,
    get_subscriber,
    notify_subscription_changed,
    request_subscription_refresh,
    start_subscriber,
    stop_subscriber,
)

# ---------------------------------------------------------------------------
# Fake Redis（轻量级，仅支持 setex/mget）
# ---------------------------------------------------------------------------


class _FakePubSub:
    """轻量级 Pub/Sub mock：预置消息队列，get_message 逐条弹出，空时短睡眠返回 None."""

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages: list[dict] = list(messages or [])
        self.subscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        pass

    async def get_message(
        self, ignore_subscribe_messages: bool = True, timeout: float = 1.0
    ) -> dict | None:
        import asyncio as _asyncio

        if self.messages:
            return self.messages.pop(0)
        await _asyncio.sleep(0.01)
        return None

    async def aclose(self) -> None:
        self.closed = True


class _FakePipeline:
    """模拟 redis-py pipeline：调用仅缓冲命令，execute 时顺序应用到 _FakeRedis."""

    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._ops.append(("setex", key, ttl, value))

    def publish(self, channel: str, message: str) -> None:
        self._ops.append(("publish", channel, message))

    def lpush(self, key: str, value: str) -> None:
        self._ops.append(("lpush", key, value))

    def ltrim(self, key: str, start: int, stop: int) -> None:
        self._ops.append(("ltrim", key, start, stop))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    def delete(self, key: str) -> None:
        self._ops.append(("delete", key))

    async def execute(self) -> list:
        for op in self._ops:
            name, *args = op
            await getattr(self._redis, name)(*args)
        return []


class _FakeRedis:
    """轻量级 Redis mock.

    支持 setex/mget/publish/set(nx)/eval/pipeline/pubsub；S2 起补齐 list/hash
    真实存储（lpush/lrange/ltrim/lindex/delete/hset/hgetall），供 R02/R08 的
    历史缓存三重限制与持久待补缺口列表测试使用。
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self.published: list[tuple[str, str]] = []
        self.pubsub_instances: list[_FakePubSub] = []
        # 测试可替换为返回预置消息的 _FakePubSub 的 callable
        self.pubsub_factory = None

    def pubsub(self) -> _FakePubSub:
        """返回 fake PubSub（默认空消息队列，经 pubsub_factory 可预置消息）."""
        ps = self.pubsub_factory() if self.pubsub_factory is not None else _FakePubSub()
        self.pubsub_instances.append(ps)
        return ps

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = value

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._data.get(k) for k in keys]

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._data:
            return False
        self._data[key] = value
        return True

    async def delete(self, key: str) -> int:
        deleted = 0
        if key in self._data:
            del self._data[key]
            deleted += 1
        if key in self._lists:
            del self._lists[key]
            deleted += 1
        if key in self._hashes:
            del self._hashes[key]
            deleted += 1
        return deleted

    async def eval(self, script: str, numkeys: int, *args: str) -> Any:
        """简化 Lua 脚本执行：支持 SETNX 锁释放（CAS DEL）与续期（CAS EXPIRE）."""
        key = args[0]
        token = args[1] if len(args) > 1 else ""
        if self._data.get(key) != token:
            return 0
        if "DEL" in script:
            del self._data[key]
            return 1
        if "EXPIRE" in script:
            return 1
        return 0

    def pipeline(self) -> _FakePipeline:
        """返回缓冲型 pipeline（对齐 redis-py 语义：调用缓冲，execute 时应用）."""
        return _FakePipeline(self)

    async def lpush(self, key: str, value: str) -> int:
        lst = self._lists.setdefault(key, [])
        lst.insert(0, value)
        return len(lst)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self._lists.get(key, [])
        if stop < 0:
            stop = len(lst) + stop
        return lst[start : stop + 1]

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        lst = self._lists.get(key, [])
        if stop < 0:
            stop = len(lst) + stop
        self._lists[key] = lst[start : stop + 1]

    async def lindex(self, key: str, index: int) -> str | None:
        lst = self._lists.get(key, [])
        if index < 0:
            index = len(lst) + index
        if 0 <= index < len(lst):
            return lst[index]
        return None

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def hset(self, key: str, *, mapping: dict | None = None, **kwargs) -> int:
        h = self._hashes.setdefault(key, {})
        fields = {**(mapping or {}), **kwargs}
        for field, value in fields.items():
            h[field] = value if isinstance(value, str) else str(value)
        return len(fields)

    async def hgetall(self, key: str) -> dict:
        return dict(self._hashes.get(key, {}))

    async def zadd(self, key: str, mapping: dict) -> int:
        return 1

    async def zrange(self, key: str, start: int, stop: int) -> list:
        return []

    async def zrem(self, key: str, member: str) -> int:
        return 1


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
    fake_redis = _FakeRedis()
    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        mock_settings.SIGNALR_ENABLED = True
        mock_settings.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
        mock_settings.SIGNALR_RECONNECT_INTERVAL = 1
        mock_settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS = 30

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
    """_cache_value 经显示批量发送路径以正确 key/TTL 写入 Redis + Pub/Sub.

    R03 重排：接收路径不 await Redis——先入待发字典，_flush_display_pending
    批量发送；载荷在既有 4 字段上增量携带 valueValid/recvAt/stale。
    """
    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    # 映射缓存保持空（点号兜底口径，仿真场景）；避免测试依赖真实 DB
    sub._refresh_loop_meta_cache = AsyncMock()

    item = {
        "tagCode": "LIC-101.PV",
        "value": "50.5",
        "quality": 0,
        "collectTime": "2026-06-28T08:00:00Z",
    }

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        mock_settings.REALTIME_WRITEBACK_ENABLED = False
        mock_settings.DATA_SOURCE_TYPE = "remote_api"
        accepted = await sub._cache_value(item)
        assert accepted is True
        # R03：_cache_value 本身不写 Redis（不 await），快照进待发字典
        expected_key = f"{_REDIS_KEY_PREFIX}LIC-101.PV"
        assert expected_key not in fake_redis._data
        assert "LIC-101.PV" in sub._display_pending
        # 历史缓冲在同步段已就绪（不依赖 Redis）
        assert "LIC-101" in sub._buffer
        await sub._flush_display_pending()

    assert expected_key in fake_redis._data
    cached = json.loads(fake_redis._data[expected_key])
    assert cached["tagCode"] == "LIC-101.PV"
    assert cached["value"] == "50.5"
    assert cached["quality"] == 0
    assert cached["collectTime"] == "2026-06-28T08:00:00Z"
    # 增量可选字段（S3 消费侧容错缺省）
    assert cached["valueValid"] is True
    assert cached["stale"] is False
    assert "recvAt" in cached
    # Pub/Sub 频道广播同一载荷
    assert any(ch == _PUBSUB_CHANNEL for ch, _m in fake_redis.published)


@pytest.mark.asyncio
async def test_cache_value_buffers_writeback_when_enabled():
    """开启写回且数据源为 tdengine 时，保留旧宽表缓冲逻辑（条目结构含 R11 元数据）."""
    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._refresh_loop_meta_cache = AsyncMock()

    item = {
        "tagCode": "LIC-101.PV",
        "value": "50.5",
        "quality": 1,
        "collectTime": "2026-06-28T08:00:00Z",
    }

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        mock_settings.REALTIME_WRITEBACK_ENABLED = True
        mock_settings.DATA_SOURCE_TYPE = "tdengine"
        await sub._cache_value(item)

    entry = sub._buffer["LIC-101"]["PV"]
    assert entry["value"] == "50.5"
    assert entry["quality"] == 1
    assert entry["ts"] == "2026-06-28T08:00:00Z"
    # R11 绑定代次元数据（S0 契约 §4.1/§4.3）
    assert entry["tag"] == "LIC-101.PV"
    assert entry["recvAt"] > 0
    assert entry["epoch"] == 0


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
    fake_redis._data[f"{_REDIS_KEY_PREFIX}LIC-101.PV"] = json.dumps(
        {
            "tagCode": "LIC-101.PV",
            "value": "50.5",
            "quality": 0,
            "collectTime": "2026-06-28T08:00:00Z",
        }
    )

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

    with patch(
        "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
        return_value=mock_session_ctx,
    ):
        tags = await sub._get_active_tags()

    assert tags == ["LIC-101.PV", "TIC-101.PV"]


@pytest.mark.asyncio
async def test_get_active_tags_returns_empty_on_error():
    """数据库异常时应返回空列表."""
    sub = RealtimeSubscriber()

    with patch(
        "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
        side_effect=Exception("DB error"),
    ):
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


# ---------------------------------------------------------------------------
# 断点续传（Gap Backfill）测试
# ---------------------------------------------------------------------------


def _gap_settings(mock_settings) -> None:
    """为 mock settings 补齐断点续传配置项."""
    mock_settings.GAP_BACKFILL_ENABLED = True
    mock_settings.GAP_BACKFILL_MIN_GAP_SECONDS = 60
    mock_settings.GAP_BACKFILL_MAX_HOURS = 24
    mock_settings.GAP_BACKFILL_RETRY_BASE_SECONDS = 300
    mock_settings.GAP_BACKFILL_RETRY_MAX_SECONDS = 1800
    mock_settings.GAP_BACKFILL_LOCK_TTL_SECONDS = 7200
    mock_settings.SIGNALR_STALL_TIMEOUT_SECONDS = 300
    mock_settings.SIGNALR_RESUBSCRIBE_INTERVAL = 1800
    mock_settings.SIGNALR_PING_INTERVAL = 30
    mock_settings.SIGNALR_PING_TIMEOUT = 60
    mock_settings.SIGNALR_OPEN_TIMEOUT = 15


def _mock_loop_db(loop_ids: list[str]) -> AsyncMock:
    """构造返回指定回路 ID 列表的 mock AsyncSessionLocal 上下文."""
    mock_result = MagicMock()
    mock_result.all.return_value = [(lid,) for lid in loop_ids]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_session_ctx


def _all_mapped_loop_data(loop_ids: list[str]) -> dict[str, dict]:
    """构造 _batch_get_loop_data 返回值：所有回路均有有效 tag 映射."""
    return {
        lid: {
            "role_tag_map": {"PV": f"LIC-{i}.PV"},
            "unit_id": "unit-1",
            "subtable": f"t_lic_{i}",
        }
        for i, lid in enumerate(loop_ids)
    }


async def _cancel_retry_task(sub: RealtimeSubscriber) -> None:
    """取消补数重试定时器，避免 300s 睡眠任务泄漏到事件循环 teardown."""
    import asyncio

    task = sub._backfill_retry_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_cache_value_updates_last_data_at():
    """_cache_value 收到数据时应更新 _last_data_at."""
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    assert sub._last_data_at is None

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        mock_settings.REALTIME_WRITEBACK_ENABLED = False
        before = _time.time()
        await sub._cache_value({"tagCode": "LIC-101.PV", "value": "1", "collectTime": "t"})
        after = _time.time()

    assert sub._last_data_at is not None
    assert before <= sub._last_data_at <= after


@pytest.mark.asyncio
async def test_load_checkpoint_restores_from_redis():
    """start 时从 Redis checkpoint 恢复 _last_flushed_at 和 _last_data_at."""
    from app.services.data_source.realtime_subscriber import _GAP_CHECKPOINT_KEY

    fake_redis = _FakeRedis()
    fake_redis._data[_GAP_CHECKPOINT_KEY] = "1700000000.5"

    sub = RealtimeSubscriber()
    with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
        await sub._load_checkpoint()

    assert sub._last_flushed_at == 1700000000.5
    assert sub._last_data_at == 1700000000.5


@pytest.mark.asyncio
async def test_load_checkpoint_ignores_invalid_value():
    """checkpoint 格式无效时忽略且不抛异常."""
    from app.services.data_source.realtime_subscriber import _GAP_CHECKPOINT_KEY

    fake_redis = _FakeRedis()
    fake_redis._data[_GAP_CHECKPOINT_KEY] = "not-a-float"

    sub = RealtimeSubscriber()
    with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
        await sub._load_checkpoint()

    assert sub._last_flushed_at is None
    assert sub._last_data_at is None


@pytest.mark.asyncio
async def test_maybe_save_checkpoint_throttled():
    """checkpoint 写入应按 30s 节流，force=True 时立即写（使用落库点）."""
    from app.services.data_source.realtime_subscriber import _GAP_CHECKPOINT_KEY

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = 1700000000.0

    with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
        await sub._maybe_save_checkpoint()
        assert fake_redis._data[_GAP_CHECKPOINT_KEY] == "1700000000.0"

        # 30s 内再次调用被节流
        sub._last_flushed_at = 1700000001.0
        await sub._maybe_save_checkpoint()
        assert fake_redis._data[_GAP_CHECKPOINT_KEY] == "1700000000.0"

        # force 立即写
        await sub._maybe_save_checkpoint(force=True)
        assert fake_redis._data[_GAP_CHECKPOINT_KEY] == "1700000001.0"


@pytest.mark.asyncio
async def test_gap_backfill_skipped_when_gap_too_small():
    """缺口小于 MIN_GAP 时不触发补数（R08：无缺口也尝试消费在途待补，需 fake Redis）."""
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 10  # 10s < 60s

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        _gap_settings(mock_settings)
        await sub._maybe_trigger_gap_backfill()

    assert sub._backfill_task is None


# ---------------------------------------------------------------------------
# 自愈测试（2026-08-21 事故修复：远端重启掐断连接后主任务静默死亡）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_loop_revives_dead_main_task():
    """主任务已退出时，刷新循环应自动重建（自愈 1）."""
    import asyncio

    sub = RealtimeSubscriber()
    sub._running = True

    # 主任务已死（已完成）
    async def _dead():
        return

    dead_task = asyncio.create_task(_dead())
    await dead_task
    sub._task = dead_task

    # 新主任务占位：自愈会以新 create_task(self._run()) 替换
    run_calls = []

    async def _fake_run():
        run_calls.append(1)
        await asyncio.sleep(30)

    sub._run = _fake_run

    with patch("app.services.data_source.realtime_subscriber._SIGNALR_REFRESH_INTERVAL", 0.05):
        refresh = asyncio.create_task(sub._refresh_loop())
        try:
            await asyncio.sleep(0.3)  # 越过多个刷新周期
        finally:
            refresh.cancel()
            try:
                await refresh
            except asyncio.CancelledError:
                pass

    assert run_calls, "自愈应重建主任务（调用 _run）"
    assert sub._task is not None and sub._task is not dead_task
    if sub._task is not None:
        sub._task.cancel()
        try:
            await sub._task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_refresh_loop_recycles_stuck_main_task():
    """数据停滞超阈值且看门狗未生效（主任务卡死）时，强制取消重建（自愈 2）."""
    import asyncio
    import time as _time

    sub = RealtimeSubscriber()
    sub._running = True
    sub._last_data_at = _time.time() - 999  # 停滞远超阈值

    # 主任务"活着"但卡死（长时间 sleep，模拟 recv 卡住）
    async def _stuck():
        await asyncio.sleep(100)

    sub._task = asyncio.create_task(_stuck())

    rebuilds = []

    async def _fake_run():
        rebuilds.append(1)
        await asyncio.sleep(30)

    sub._run = _fake_run

    with (
        patch("app.services.data_source.realtime_subscriber._SIGNALR_REFRESH_INTERVAL", 0.05),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        mock_settings.SIGNALR_STALL_TIMEOUT_SECONDS = 300
        refresh = asyncio.create_task(sub._refresh_loop())
        try:
            await asyncio.sleep(0.3)
        finally:
            refresh.cancel()
            try:
                await refresh
            except asyncio.CancelledError:
                pass

    assert rebuilds, "自愈应强制重建卡死的主任务"
    assert sub._task is not None and sub._task.done() is False
    if sub._task is not None:
        sub._task.cancel()
        try:
            await sub._task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_gap_backfill_skipped_when_disabled():
    """GAP_BACKFILL_ENABLED=False 时只登记缺口不调用远端（R08），不创建补数任务."""
    import time as _time

    from app.services.data_source.realtime_subscriber import _GAP_PENDING_KEY

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 3600

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        _gap_settings(mock_settings)
        mock_settings.GAP_BACKFILL_ENABLED = False
        await sub._maybe_trigger_gap_backfill()

    assert sub._backfill_task is None
    # 缺口已登记到持久待补列表（开关关闭不调远端，但登记可见）
    assert len(fake_redis._lists.get(_GAP_PENDING_KEY, [])) == 1
    assert sub._metrics["gap_windows_registered"] == 1


@pytest.mark.asyncio
async def test_gap_backfill_triggered_on_reconnect():
    """缺口 ≥ MIN_GAP 时创建补数任务，窗口为 [last_flushed_at, now-2s]."""
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    last_flushed_at = _time.time() - 300  # 5 分钟缺口
    sub._last_flushed_at = last_flushed_at

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_backfill,
    ):
        _gap_settings(mock_settings)
        before = _time.time()
        await sub._maybe_trigger_gap_backfill()
        after = _time.time()

        assert sub._backfill_task is not None
        await sub._backfill_task

    mock_backfill.assert_awaited_once()
    gap_start, gap_end = mock_backfill.await_args.args
    assert gap_start == last_flushed_at
    assert before - 2 <= gap_end <= after - 2


@pytest.mark.asyncio
async def test_gap_backfill_window_truncated_to_max_hours():
    """缺口超过 MAX_HOURS 时窗口截断为最近 MAX_HOURS."""
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 48 * 3600  # 48h 缺口，上限 24h

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_backfill,
    ):
        _gap_settings(mock_settings)
        now = _time.time()
        await sub._maybe_trigger_gap_backfill()
        assert sub._backfill_task is not None
        await sub._backfill_task

    gap_start, _gap_end = mock_backfill.await_args.args
    # 截断后起点 ≈ now - 24h（而非 48h 前）
    assert abs(gap_start - (now - 24 * 3600)) < 5


@pytest.mark.asyncio
async def test_gap_backfill_dedup_while_running():
    """已有补数任务在执行时不重复触发."""
    import asyncio
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 3600

    async def _pending():
        await asyncio.sleep(100)

    running_task = asyncio.create_task(_pending())
    sub._backfill_task = running_task

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        _gap_settings(mock_settings)
        await sub._maybe_trigger_gap_backfill()

    # 任务未被替换
    assert sub._backfill_task is running_task
    running_task.cancel()
    try:
        await running_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_run_gap_backfill_calls_import_with_skip_strategy():
    """补数成功：import(skip+trigger_backfill)、任务登记 auto-backfill、落库点推进."""
    import time as _time

    from app.schemas.task import TaskStatus, TaskType

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    gap_start = _time.time() - 600
    gap_end = _time.time() - 2
    sub._last_flushed_at = gap_start

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch(
            "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
            return_value=_mock_loop_db(["loop-1", "loop-2"]),
        ),
        patch(
            "app.services.data_import._batch_get_loop_data",
            new=AsyncMock(return_value=_all_mapped_loop_data(["loop-1", "loop-2"])),
        ),
        patch(
            "app.services.data_import.import_history_data",
            new=AsyncMock(return_value={"total": 2, "succeeded": 2, "failed": 0, "errors": []}),
        ) as mock_import,
        patch(
            "app.services.task_tracker.create_task",
            new=AsyncMock(return_value="task-1"),
        ) as mock_create,
        patch("app.services.task_tracker.update_status", new=AsyncMock()) as mock_update,
    ):
        await sub._run_gap_backfill(gap_start, gap_end)

    mock_import.assert_awaited_once()
    kwargs = mock_import.await_args.kwargs
    assert kwargs["conflict_strategy"] == "skip"
    assert kwargs["trigger_backfill"] is True
    loop_ids = mock_import.await_args.args[0]
    assert loop_ids == ["loop-1", "loop-2"]
    # 落库点推进到窗口末端（接收点不推进——补数不等于收到新实时数据）
    assert sub._last_flushed_at == gap_end
    # 任务登记：BACKFILL + auto-backfill 来源标记 + 系统创建
    mock_create.assert_awaited_once()
    create_kwargs = mock_create.await_args.kwargs
    assert create_kwargs["task_type"] == TaskType.BACKFILL
    assert create_kwargs["triggered_by"] == "auto-backfill"
    assert create_kwargs["created_by"] == "system"
    assert create_kwargs["loops_total"] == 2
    # 状态流转：RUNNING → SUCCESS
    statuses = [c.args[1] for c in mock_update.await_args_list]
    assert statuses == [TaskStatus.RUNNING, TaskStatus.SUCCESS]
    success_call = mock_update.await_args_list[-1]
    assert success_call.kwargs["progress"] == 1.0
    assert success_call.kwargs["loops_done"] == 2
    # 全部成功，不安排重试
    assert sub._backfill_retry_task is None
    assert sub._backfill_retry_count == 0


@pytest.mark.asyncio
async def test_run_gap_backfill_error_does_not_raise():
    """补数异常被吞掉（记日志），不抛出、落库点不推进、安排重试."""
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    gap_start = _time.time() - 600
    gap_end = _time.time() - 2
    sub._last_flushed_at = gap_start

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch(
            "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
            side_effect=Exception("DB error"),
        ),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch("app.services.task_tracker.update_status", new=AsyncMock()),
        patch("app.services.alerting.send_alert", new=AsyncMock()) as mock_alert,
    ):
        _gap_settings(mock_settings)
        await sub._run_gap_backfill(gap_start, gap_end)  # 不应抛出
        # 重试定时器已安排（连接在线也生效），测试后取消避免泄漏
        assert sub._backfill_retry_task is not None
        assert sub._backfill_retry_count == 1
        assert sub._retry_window_start == gap_start
        await _cancel_retry_task(sub)

    assert sub._last_flushed_at == gap_start
    mock_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_gap_backfill_partial_failure_keeps_checkpoint():
    """部分失败：落库点不推进，任务记 FAILED 并告警，安排延迟重试."""
    import time as _time

    from app.schemas.task import TaskStatus, TaskType

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    gap_start = _time.time() - 600
    gap_end = _time.time() - 2
    sub._last_flushed_at = gap_start

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch(
            "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
            return_value=_mock_loop_db(["loop-1", "loop-2"]),
        ),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch(
            "app.services.data_import._batch_get_loop_data",
            new=AsyncMock(return_value=_all_mapped_loop_data(["loop-1", "loop-2"])),
        ),
        patch(
            "app.services.data_import.import_history_data",
            new=AsyncMock(
                return_value={
                    "total": 2,
                    "succeeded": 1,
                    "failed": 1,
                    "errors": ["loop-2: HTTP 504"],
                }
            ),
        ),
        patch(
            "app.services.task_tracker.create_task",
            new=AsyncMock(return_value="task-1"),
        ) as mock_create,
        patch("app.services.task_tracker.update_status", new=AsyncMock()) as mock_update,
        patch("app.services.alerting.send_alert", new=AsyncMock()) as mock_alert,
    ):
        _gap_settings(mock_settings)
        await sub._run_gap_backfill(gap_start, gap_end)
        retry_task = sub._backfill_retry_task
        await _cancel_retry_task(sub)

    # 落库点不推进，缺口保留待重试
    assert sub._last_flushed_at == gap_start
    # 任务登记仍带 auto-backfill 来源标记
    assert mock_create.await_args.kwargs["task_type"] == TaskType.BACKFILL
    assert mock_create.await_args.kwargs["triggered_by"] == "auto-backfill"
    # 状态流转：RUNNING → FAILED（含失败摘要与部分进度）
    statuses = [c.args[1] for c in mock_update.await_args_list]
    assert statuses == [TaskStatus.RUNNING, TaskStatus.FAILED]
    failed_call = mock_update.await_args_list[-1]
    assert failed_call.kwargs["loops_done"] == 1
    assert failed_call.kwargs["progress"] == 0.5
    assert "1/2" in failed_call.kwargs["error_message"]
    # 告警已发送
    mock_alert.assert_awaited_once()
    assert "部分失败" in mock_alert.await_args.args[0]
    # 重试已安排（窗口起点为失败缺口起点）
    assert retry_task is not None
    assert sub._backfill_retry_count == 1
    assert sub._retry_window_start == gap_start


# ---------------------------------------------------------------------------
# 补数失败重试（指数退避定时器）测试
# ---------------------------------------------------------------------------


def test_backfill_retry_delay_exponential_backoff():
    """退避间隔：base 起步指数翻倍，封顶 cap（5min 起步、30min 上限语义）."""
    from app.services.data_source.realtime_subscriber import backfill_retry_delay

    assert backfill_retry_delay(1, base=300, cap=1800) == 300
    assert backfill_retry_delay(2, base=300, cap=1800) == 600
    assert backfill_retry_delay(3, base=300, cap=1800) == 1200
    assert backfill_retry_delay(4, base=300, cap=1800) == 1800  # 封顶
    assert backfill_retry_delay(10, base=300, cap=1800) == 1800  # 持续封顶


@pytest.mark.asyncio
async def test_schedule_backfill_retry_recreates_timer_and_keeps_earliest_window():
    """多次失败：定时器取消重建（退避按最新次数），重试窗口起点保持最早."""
    import asyncio

    sub = RealtimeSubscriber()
    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        _gap_settings(mock_settings)

        sub._schedule_backfill_retry(1000.0)
        first_task = sub._backfill_retry_task
        assert first_task is not None
        assert sub._backfill_retry_count == 1
        assert sub._retry_window_start == 1000.0

        # 第二次失败（更晚的缺口起点）：旧定时器取消，新定时器重建，起点仍取最早
        sub._schedule_backfill_retry(2000.0)
        second_task = sub._backfill_retry_task
        assert second_task is not None
        assert second_task is not first_task
        assert sub._backfill_retry_count == 2
        assert sub._retry_window_start == 1000.0
        with pytest.raises(asyncio.CancelledError):
            await first_task

        await _cancel_retry_task(sub)


@pytest.mark.asyncio
async def test_schedule_backfill_retry_skipped_when_disabled():
    """GAP_BACKFILL_ENABLED=False 时不安排重试."""
    sub = RealtimeSubscriber()
    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        _gap_settings(mock_settings)
        mock_settings.GAP_BACKFILL_ENABLED = False
        sub._schedule_backfill_retry(1000.0)

    assert sub._backfill_retry_task is None
    assert sub._backfill_retry_count == 0


@pytest.mark.asyncio
async def test_retry_gap_backfill_triggers_backfill_after_delay():
    """重试定时器到期后对失败缺口重新执行补数（窗口末端取触发时刻）."""
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._running = True
    gap_start = _time.time() - 900
    sub._retry_window_start = gap_start
    sub._backfill_retry_count = 1

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_backfill,
    ):
        await sub._retry_gap_backfill(0.01)
        assert sub._backfill_task is not None
        await sub._backfill_task

    mock_backfill.assert_awaited_once()
    called_start, called_end = mock_backfill.await_args.args
    assert called_start == gap_start
    assert called_end > gap_start  # 末端取触发时刻（覆盖等待期间新缺口）


@pytest.mark.asyncio
async def test_retry_gap_backfill_reschedules_when_backfill_running():
    """定时器到期时已有补数在执行：按同延迟原地重排，不并发补数."""
    import asyncio
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    sub._running = True
    sub._retry_window_start = _time.time() - 900
    sub._backfill_retry_count = 1

    async def _pending():
        await asyncio.sleep(100)

    running_task = asyncio.create_task(_pending())
    sub._backfill_task = running_task
    try:
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_backfill,
        ):
            await sub._retry_gap_backfill(0.01)
            # 未触发新补数，而是重排了定时器
            mock_backfill.assert_not_awaited()
            assert sub._backfill_retry_task is not None
            assert sub._backfill_retry_task is not running_task
            await _cancel_retry_task(sub)
    finally:
        running_task.cancel()
        try:
            await running_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_stop_cancels_backfill_retry_timer():
    """stop 应取消待执行的补数重试定时器并重置状态（防泄漏）."""
    import asyncio

    sub = RealtimeSubscriber()

    async def _pending():
        await asyncio.sleep(100)

    sub._backfill_retry_task = asyncio.create_task(_pending())
    sub._backfill_retry_count = 3
    sub._retry_window_start = 1234.0

    await sub.stop()

    assert sub._backfill_retry_task is None
    assert sub._backfill_retry_count == 0
    assert sub._retry_window_start is None


@pytest.mark.asyncio
async def test_clear_backfill_retry_resets_state():
    """缺口成功补全后清除重试状态（取消定时器、归零失败计数）."""
    import asyncio

    sub = RealtimeSubscriber()

    async def _pending():
        await asyncio.sleep(100)

    sub._backfill_retry_task = asyncio.create_task(_pending())
    sub._backfill_retry_count = 2
    sub._retry_window_start = 1000.0

    sub._clear_backfill_retry()

    assert sub._backfill_retry_task is None
    assert sub._backfill_retry_count == 0
    assert sub._retry_window_start is None


# ---------------------------------------------------------------------------
# 时区显式转换测试（WS-B2 阶段 3）
# ---------------------------------------------------------------------------


class TestNormalizeTs:
    """_normalize_ts 时区显式转换测试。"""

    def test_utc_z_suffix_converts_to_cst(self):
        """带 Z 后缀的 UTC 时间应转换到 Asia/Shanghai（+8h）。"""
        result = _normalize_ts("2026-07-15T10:00:00Z")
        # 10:00 UTC → 18:00 CST
        assert "2026-07-15 18:00:00" in result

    def test_utc_offset_converts_to_cst(self):
        """带 +00:00 偏移的 UTC 时间应转换到 Asia/Shanghai。"""
        result = _normalize_ts("2026-07-15T10:00:00+00:00")
        assert "2026-07-15 18:00:00" in result

    def test_naive_treated_as_target_tz(self):
        """naive（无时区）视为已在目标时区，不偏移。"""
        result = _normalize_ts("2026-07-15T10:00:00")
        assert "2026-07-15 10:00:00" in result

    def test_non_utc_offset_converts(self):
        """非 UTC 偏移（如 -05:00）应正确转换到 Asia/Shanghai。"""
        result = _normalize_ts("2026-07-15T05:00:00-05:00")
        # 05:00-05:00 = 10:00 UTC → 18:00 CST
        assert "2026-07-15 18:00:00" in result

    def test_empty_string_returns_now(self):
        """空字符串应返回当前目标时区时间。"""
        result = _normalize_ts("")
        assert len(result) > 0
        # 格式应为 YYYY-MM-DD HH:MM:SS.fff
        assert result[4] == "-" and result[7] == "-"

    def test_none_returns_now(self):
        """None 应返回当前目标时区时间。"""
        result = _normalize_ts(None)  # type: ignore[arg-type]
        assert len(result) > 0

    def test_invalid_string_returns_now(self):
        """无效字符串应返回当前目标时区时间。"""
        result = _normalize_ts("not-a-date")
        assert len(result) > 0

    def test_output_format_milliseconds(self):
        """输出格式应为毫秒精度（.fff 结尾）。"""
        result = _normalize_ts("2026-07-15T10:00:00Z")
        # 格式 YYYY-MM-DD HH:MM:SS.fff（3 位毫秒）
        parts = result.split(".")
        assert len(parts) == 2
        assert len(parts[1]) == 3


# ---------------------------------------------------------------------------
# SETNX 分布式锁测试（WS-B2 阶段 9）
# ---------------------------------------------------------------------------


class TestBackfillLock:
    """_acquire_backfill_lock / _release_backfill_lock SETNX 分布式锁测试。"""

    @pytest.mark.asyncio
    async def test_acquire_lock_succeeds_when_free(self):
        """锁未被持有时 SETNX 应成功。"""
        fake_redis = _FakeRedis()
        sub = RealtimeSubscriber()
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _gap_settings(mock_s)
            ok = await sub._acquire_backfill_lock("token-abc")

        assert ok is True
        assert fake_redis._data[_GAP_BACKFILL_LOCK_KEY] == "token-abc"

    @pytest.mark.asyncio
    async def test_acquire_lock_fails_when_held(self):
        """锁已被其他副本持有时 SETNX 应失败。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_GAP_BACKFILL_LOCK_KEY] = "other-token"
        sub = RealtimeSubscriber()
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _gap_settings(mock_s)
            ok = await sub._acquire_backfill_lock("token-abc")

        assert ok is False
        # 锁未被覆盖
        assert fake_redis._data[_GAP_BACKFILL_LOCK_KEY] == "other-token"

    @pytest.mark.asyncio
    async def test_acquire_lock_degrades_on_redis_error(self):
        """Redis 异常时降级为无锁执行（返回 True）。"""
        sub = RealtimeSubscriber()
        with patch("app.services.data_source.realtime_subscriber.redis_client") as mock_redis:
            mock_redis.set = AsyncMock(side_effect=Exception("Redis down"))
            with patch("app.services.data_source.realtime_subscriber.settings") as mock_s:
                _gap_settings(mock_s)
                ok = await sub._acquire_backfill_lock("token-abc")

        assert ok is True

    @pytest.mark.asyncio
    async def test_release_lock_succeeds_with_correct_token(self):
        """token 匹配时 CAS DEL 应释放锁。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_GAP_BACKFILL_LOCK_KEY] = "token-abc"
        sub = RealtimeSubscriber()
        with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
            await sub._release_backfill_lock("token-abc")

        assert _GAP_BACKFILL_LOCK_KEY not in fake_redis._data

    @pytest.mark.asyncio
    async def test_release_lock_noop_with_wrong_token(self):
        """token 不匹配时 CAS DEL 不应释放锁。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_GAP_BACKFILL_LOCK_KEY] = "token-abc"
        sub = RealtimeSubscriber()
        with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
            await sub._release_backfill_lock("wrong-token")

        assert fake_redis._data[_GAP_BACKFILL_LOCK_KEY] == "token-abc"

    @pytest.mark.asyncio
    async def test_release_lock_swallows_error(self):
        """Redis 异常时释放锁不应抛出（TTL 兜底）。"""
        sub = RealtimeSubscriber()
        with patch("app.services.data_source.realtime_subscriber.redis_client") as mock_redis:
            mock_redis.eval = AsyncMock(side_effect=Exception("Redis down"))
            # 不应抛出异常
            await sub._release_backfill_lock("token-abc")


# ---------------------------------------------------------------------------
# 多 worker 进程订阅单例（Leader 锁）测试
# ---------------------------------------------------------------------------


def _leader_settings(mock_s) -> None:
    """为 mock settings 补齐 Leader 锁/start 路径所需配置项."""
    mock_s.SIGNALR_ENABLED = True
    mock_s.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
    mock_s.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS = 1
    mock_s.TDENGINE_FLUSH_INTERVAL = 60
    mock_s.SIGNALR_STALL_TIMEOUT_SECONDS = 300


async def _idle_loop():
    """空转协程：替代 _run/_flush_loop/_refresh_loop，避免真实 WS 连接."""
    import asyncio

    try:
        await asyncio.sleep(100)
    except asyncio.CancelledError:
        raise


def _stub_sub_tasks(sub: RealtimeSubscriber) -> None:
    """将订阅三任务替换为空转协程."""
    sub._run = _idle_loop
    sub._flush_loop = _idle_loop
    sub._refresh_loop = _idle_loop


class TestLeaderLock:
    """_acquire/_renew/_release_leader_lock 与 start/stop Leader 选举测试。"""

    @pytest.mark.asyncio
    async def test_acquire_leader_lock_succeeds_when_free(self):
        """锁未被持有时 SETNX 应成功。"""
        fake_redis = _FakeRedis()
        sub = RealtimeSubscriber()
        sub._leader_token = "host:1:1"
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _leader_settings(mock_s)
            ok = await sub._acquire_leader_lock()

        assert ok is True
        assert fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] == "host:1:1"

    @pytest.mark.asyncio
    async def test_acquire_leader_lock_fails_when_held(self):
        """锁已被其他 worker 持有时 SETNX 应失败，且不覆盖原锁。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] = "other:2:2"
        sub = RealtimeSubscriber()
        sub._leader_token = "host:1:1"
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _leader_settings(mock_s)
            ok = await sub._acquire_leader_lock()

        assert ok is False
        assert fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] == "other:2:2"

    @pytest.mark.asyncio
    async def test_acquire_leader_lock_returns_false_on_redis_error(self):
        """R04：Redis 异常时保持待命（返回 False），不得因异常成为 Leader.

        原 fail-open 行为（异常返回 True）会使 Redis 断网期间 4 个 worker
        全部启动订阅重复采集，已按 S0 契约 §5 改写为正确行为断言。
        """
        sub = RealtimeSubscriber()
        sub._leader_token = "host:1:1"
        with patch("app.services.data_source.realtime_subscriber.redis_client") as mock_redis:
            mock_redis.set = AsyncMock(side_effect=Exception("Redis down"))
            with patch("app.services.data_source.realtime_subscriber.settings") as mock_s:
                _leader_settings(mock_s)
                ok = await sub._acquire_leader_lock()

        assert ok is False

    @pytest.mark.asyncio
    async def test_renew_leader_lock_succeeds_with_correct_token(self):
        """token 匹配时 CAS EXPIRE 续期应成功。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] = "host:1:1"
        sub = RealtimeSubscriber()
        sub._leader_token = "host:1:1"
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _leader_settings(mock_s)
            ok = await sub._renew_leader_lock()

        assert ok is True

    @pytest.mark.asyncio
    async def test_renew_leader_lock_fails_when_lock_lost(self):
        """锁已易主（token 不匹配）时续期应失败，调用方据此转待命。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] = "other:2:2"
        sub = RealtimeSubscriber()
        sub._leader_token = "host:1:1"
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _leader_settings(mock_s)
            ok = await sub._renew_leader_lock()

        assert ok is False

    @pytest.mark.asyncio
    async def test_renew_leader_lock_keeps_leadership_on_redis_error(self):
        """R04：Redis 异常时续期返回 True 保持现状，但租约期限不延长.

        租约（lease_expires_at）只由确认成功推进；超出租约仍无法确认时由
        _maintain_leadership 退位（见 test_realtime_subscriber_s1.py）。
        """
        sub = RealtimeSubscriber()
        sub._leader_token = "host:1:1"
        sub._lease_expires_at = 1000.0
        with patch("app.services.data_source.realtime_subscriber.redis_client") as mock_redis:
            mock_redis.eval = AsyncMock(side_effect=Exception("Redis down"))
            with patch("app.services.data_source.realtime_subscriber.settings") as mock_s:
                _leader_settings(mock_s)
                ok = await sub._renew_leader_lock()

        assert ok is True
        assert sub._lease_expires_at == 1000.0, "续租异常不得延长租约期限"

    @pytest.mark.asyncio
    async def test_release_leader_lock_cas(self):
        """token 匹配时释放成功；不匹配时不误释放。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] = "host:1:1"
        sub = RealtimeSubscriber()
        with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
            sub._leader_token = "wrong"
            await sub._release_leader_lock()
            assert _SUBSCRIBER_LEADER_LOCK_KEY in fake_redis._data

            sub._leader_token = "host:1:1"
            await sub._release_leader_lock()
            assert _SUBSCRIBER_LEADER_LOCK_KEY not in fake_redis._data

    @pytest.mark.asyncio
    async def test_start_acquires_lock_and_becomes_leader(self):
        """锁空闲时 start 应抢锁成功并启动订阅三任务。"""
        fake_redis = _FakeRedis()
        sub = RealtimeSubscriber()
        _stub_sub_tasks(sub)
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _leader_settings(mock_s)
            await sub.start()
            assert sub._is_leader is True
            assert sub._task is not None
            assert sub._flush_task is not None
            assert sub._refresh_task is not None
            assert sub._leader_task is not None
            assert fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] == sub._leader_token
            await sub.stop()

        # stop 后 Leader 锁已释放，其他进程可接管
        assert _SUBSCRIBER_LEADER_LOCK_KEY not in fake_redis._data
        assert sub._task is None
        assert sub._leader_task is None

    @pytest.mark.asyncio
    async def test_start_standby_when_lock_held_by_other(self):
        """锁被其他 worker 持有时 start 应进入待命（不启动订阅任务）。"""
        fake_redis = _FakeRedis()
        fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] = "other:2:2"
        sub = RealtimeSubscriber()
        _stub_sub_tasks(sub)
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _leader_settings(mock_s)
            await sub.start()
            assert sub._running is True
            assert sub._is_leader is False
            assert sub._task is None
            assert sub._leader_task is not None  # 抢锁循环仍在运行
            await sub.stop()

        # 待命进程 stop 不应误释放他人锁
        assert fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] == "other:2:2"

    @pytest.mark.asyncio
    async def test_standby_takes_over_after_lock_released(self):
        """持锁进程退出（锁释放/过期）后，待命进程应在下个周期抢锁接管。"""
        import asyncio

        fake_redis = _FakeRedis()
        fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] = "other:2:2"
        sub = RealtimeSubscriber()
        _stub_sub_tasks(sub)
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            _leader_settings(mock_s)  # TTL=1s → 抢锁周期 1s
            await sub.start()
            assert sub._is_leader is False

            # 模拟持锁进程退出：锁释放
            del fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY]
            await asyncio.sleep(1.5)

            assert sub._is_leader is True
            assert sub._task is not None
            assert fake_redis._data[_SUBSCRIBER_LEADER_LOCK_KEY] == sub._leader_token
            await sub.stop()

        assert _SUBSCRIBER_LEADER_LOCK_KEY not in fake_redis._data


# ---------------------------------------------------------------------------
# _build_row 时区转换测试（WS-B2 阶段 3）
# ---------------------------------------------------------------------------


class TestBuildRowTimezone:
    """_build_row 对 collectTime 的时区显式转换测试。"""

    def test_build_row_converts_utc_to_cst(self):
        """_build_row 应将 UTC 时间戳转换为 Asia/Shanghai。"""
        sub = RealtimeSubscriber()
        roles_data = {
            "PV": {"value": "50.5", "quality": 1, "ts": "2026-07-15T10:00:00Z"},
            "SP": {"value": "60.0", "quality": 1, "ts": "2026-07-15T10:00:00Z"},
        }
        row = sub._build_row(roles_data)
        # ts 是第一个元素
        assert "2026-07-15 18:00:00" in row[0]
        # PV 值
        assert row[1] == 50.5

    def test_build_row_naive_treated_as_cst(self):
        """naive 时间戳视为已在目标时区，不偏移。"""
        sub = RealtimeSubscriber()
        roles_data = {
            "PV": {"value": "50.5", "quality": 1, "ts": "2026-07-15T10:00:00"},
        }
        row = sub._build_row(roles_data)
        assert "2026-07-15 10:00:00" in row[0]

    def test_build_row_empty_ts_returns_none(self):
        """R05：整行无任何已知 sourceTime → 不落行（返回 None），不伪造 now()."""
        sub = RealtimeSubscriber()
        roles_data = {
            "PV": {"value": "50.5", "quality": 1, "ts": ""},
            "SP": {"value": "60.0", "quality": 1, "ts": None},
        }
        assert sub._build_row(roles_data) is None

    def test_build_row_partial_missing_ts_uses_known_only(self):
        """R05：部分角色无 ts 时行 ts 只用已知 sourceTime（不伪造未知角色时间）."""
        sub = RealtimeSubscriber()
        roles_data = {
            "PV": {"value": "50.5", "quality": 1, "ts": "2026-07-15T10:00:00Z"},
            "SP": {"value": "60.0", "quality": 1, "ts": ""},
        }
        row = sub._build_row(roles_data)
        # 行 ts = PV 的 sourceTime（SP 无 ts 不参与，也不挡行）
        assert row[0] == "2026-07-15 18:00:00.000"
        assert row[2] == 60.0  # SP 值照常携带（last-known 合并口径）


# ---------------------------------------------------------------------------
# 数据停滞看门狗测试（WS-B2 阶段 9）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_triggers_disconnect_on_stall():
    """数据停滞看门狗：分片超过 stall_timeout 无数据时应主动断开。"""
    import time as _time

    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])

    # 模拟本分片上次收到数据是 10 分钟前（超过 stall_timeout=1s）
    state.last_data_at = _time.time() - 600

    # 构造 mock WebSocket
    mock_ws = AsyncMock()
    # 握手响应
    mock_ws.recv = AsyncMock(
        side_effect=[
            "{}\x1e",  # 握手响应
            '{"code": 200, "data": []}\x1e',  # 初始响应（空数据）
            TimeoutError("recv timeout"),  # 触发看门狗检查
        ]
    )
    mock_ws.send = AsyncMock()
    mock_ws.close = AsyncMock()

    with (
        patch(
            "app.services.data_source.realtime_subscriber.websockets.connect",
            new=AsyncMock(return_value=mock_ws),
        ),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        patch.object(sub, "_maybe_trigger_gap_backfill", return_value=None),
    ):
        _gap_settings(mock_s)
        mock_s.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
        mock_s.SIGNALR_STALL_TIMEOUT_SECONDS = 1  # 1 秒超时
        mock_s.SIGNALR_PING_INTERVAL = 30
        mock_s.SIGNALR_PING_TIMEOUT = 60
        mock_s.SIGNALR_OPEN_TIMEOUT = 15

        await sub._connect_and_subscribe(state)

    # 看门狗触发后分片 ws 应被置 None（_close_shard_ws 调用）
    assert state.ws is None
    # mock_ws.close 应被调用（_close_shard_ws 内部）
    mock_ws.close.assert_called()


@pytest.mark.asyncio
async def test_watchdog_no_disconnect_when_data_recent():
    """数据停滞看门狗：分片数据在 stall_timeout 内时不断开，继续接收。"""
    import time as _time

    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])

    # 模拟本分片上次收到数据是刚刚（未超时）
    state.last_data_at = _time.time()

    # 构造 mock WebSocket
    mock_ws = AsyncMock()
    # 第三次 recv 超时后，第四次返回一条正常消息然后设 running=False 退出循环
    call_count = 0

    async def _mock_recv():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "{}\x1e"  # 握手响应
        elif call_count == 2:
            return '{"code": 200, "data": []}\x1e'  # 初始响应
        elif call_count == 3:
            raise TimeoutError("recv timeout")  # 看门狗超时，但数据未停滞
        elif call_count == 4:
            # 返回正常消息，然后停止
            sub._running = False
            return '{"target": "updateRealValues", "data": []}\x1e'
        else:
            raise TimeoutError("recv timeout")

    mock_ws.recv = _mock_recv
    mock_ws.send = AsyncMock()
    mock_ws.close = AsyncMock()

    with (
        patch(
            "app.services.data_source.realtime_subscriber.websockets.connect",
            new=AsyncMock(return_value=mock_ws),
        ),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        patch.object(sub, "_get_active_tags", return_value=["LIC-101.PV"]),
        patch.object(sub, "_maybe_trigger_gap_backfill", return_value=None),
    ):
        _gap_settings(mock_s)
        mock_s.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
        mock_s.SIGNALR_STALL_TIMEOUT_SECONDS = 300  # 5 分钟超时（不会触发）
        mock_s.SIGNALR_PING_INTERVAL = 30
        mock_s.SIGNALR_PING_TIMEOUT = 60
        mock_s.SIGNALR_OPEN_TIMEOUT = 15

        await sub._connect_and_subscribe(state)

    # 未触发断开：分片 ws 仍非 None（running=False 自然退出，非看门狗触发）
    mock_ws.close.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_no_disconnect_when_no_last_data():
    """看门狗：分片无停滞时不触发断开（初始帧即更新片级接收点）。"""
    sub = RealtimeSubscriber()
    sub._running = True
    state = _ShardState(index=0, total=1, tags=["LIC-101.PV"])
    state.last_data_at = None  # 从未收到数据

    mock_ws = AsyncMock()
    call_count = 0

    async def _mock_recv():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "{}\x1e"
        elif call_count == 2:
            return '{"code": 200, "data": []}\x1e'
        elif call_count == 3:
            raise TimeoutError("recv timeout")
        else:
            sub._running = False
            raise TimeoutError("recv timeout")

    mock_ws.recv = _mock_recv
    mock_ws.send = AsyncMock()
    mock_ws.close = AsyncMock()

    with (
        patch(
            "app.services.data_source.realtime_subscriber.websockets.connect",
            new=AsyncMock(return_value=mock_ws),
        ),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        patch.object(sub, "_get_active_tags", return_value=["LIC-101.PV"]),
        patch.object(sub, "_maybe_trigger_gap_backfill", return_value=None),
    ):
        _gap_settings(mock_s)
        mock_s.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
        mock_s.SIGNALR_STALL_TIMEOUT_SECONDS = 1  # 1 秒超时
        mock_s.SIGNALR_PING_INTERVAL = 30
        mock_s.SIGNALR_PING_TIMEOUT = 60
        mock_s.SIGNALR_OPEN_TIMEOUT = 15

        await sub._connect_and_subscribe(state)

    # 片级接收点由初始帧更新后未停滞，不应触发断开（close 不被看门狗调用）
    mock_ws.close.assert_not_called()


# ---------------------------------------------------------------------------
# gap backfill 无映射回路过滤测试（P1：未配置映射回路不得阻塞 checkpoint/告警）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_gap_backfill_excludes_unmapped_loops():
    """无 tag 映射回路被剔出补数口径：不参与 import、不阻塞 checkpoint、不告警."""
    import time as _time

    from app.schemas.task import TaskStatus

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    gap_start = _time.time() - 600
    gap_end = _time.time() - 2
    sub._last_flushed_at = gap_start

    # loop-1 有映射，loop-2 无映射
    loop_data_map = {
        "loop-1": {"role_tag_map": {"PV": "LIC-1.PV"}, "unit_id": "u1", "subtable": "t1"},
        "loop-2": {"role_tag_map": {}, "unit_id": "", "subtable": ""},
    }

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch(
            "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
            return_value=_mock_loop_db(["loop-1", "loop-2"]),
        ),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch(
            "app.services.data_import._batch_get_loop_data",
            new=AsyncMock(return_value=loop_data_map),
        ),
        patch(
            "app.services.data_import.import_history_data",
            new=AsyncMock(return_value={"total": 1, "succeeded": 1, "failed": 0, "errors": []}),
        ) as mock_import,
        patch(
            "app.services.task_tracker.create_task",
            new=AsyncMock(return_value="task-1"),
        ) as mock_create,
        patch("app.services.task_tracker.update_status", new=AsyncMock()) as mock_update,
        patch("app.services.alerting.send_alert", new=AsyncMock()) as mock_alert,
    ):
        _gap_settings(mock_settings)
        await sub._run_gap_backfill(gap_start, gap_end)

    # import 只收到有映射的回路
    assert mock_import.await_args.args[0] == ["loop-1"]
    # 任务登记 loops_total 只计有映射回路
    assert mock_create.await_args.kwargs["loops_total"] == 1
    # failed==0 → checkpoint 推进到窗口末端，不安排重试、不告警
    assert sub._last_flushed_at == gap_end
    assert sub._backfill_retry_task is None
    mock_alert.assert_not_awaited()
    statuses = [c.args[1] for c in mock_update.await_args_list]
    assert statuses == [TaskStatus.RUNNING, TaskStatus.SUCCESS]


@pytest.mark.asyncio
async def test_run_gap_backfill_all_unmapped_skips_without_alert():
    """全部活跃回路都无映射：直接跳过补数（不登记任务、不告警、不安排重试）."""
    import time as _time

    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()
    gap_start = _time.time() - 600
    gap_end = _time.time() - 2
    sub._last_flushed_at = gap_start

    loop_data_map = {
        "loop-1": {"role_tag_map": {}, "unit_id": "", "subtable": ""},
    }

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch(
            "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
            return_value=_mock_loop_db(["loop-1"]),
        ),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch(
            "app.services.data_import._batch_get_loop_data",
            new=AsyncMock(return_value=loop_data_map),
        ),
        patch(
            "app.services.data_import.import_history_data",
            new=AsyncMock(),
        ) as mock_import,
        patch(
            "app.services.task_tracker.create_task",
            new=AsyncMock(return_value="task-1"),
        ) as mock_create,
        patch("app.services.alerting.send_alert", new=AsyncMock()) as mock_alert,
    ):
        _gap_settings(mock_settings)
        await sub._run_gap_backfill(gap_start, gap_end)

    mock_import.assert_not_awaited()
    mock_create.assert_not_awaited()
    mock_alert.assert_not_awaited()
    assert sub._backfill_retry_task is None


# ---------------------------------------------------------------------------
# 实时写回治理测试（P2：flush TAGS 带真实 loop_id/unit_id；行 ts 取 PV collectTime）
# ---------------------------------------------------------------------------


class _FakePipe:
    """轻量级 Redis pipeline mock."""

    def __init__(self) -> None:
        self.ops: list[tuple] = []

    def lpush(self, *args: Any) -> None:
        self.ops.append(("lpush", *args))

    def ltrim(self, *args: Any) -> None:
        self.ops.append(("ltrim", *args))

    def expire(self, *args: Any) -> None:
        self.ops.append(("expire", *args))

    async def execute(self) -> list:
        return []


@pytest.mark.asyncio
async def test_flush_buffer_writeback_carries_real_loop_id_and_unit_id():
    """flush 写回 TDengine 时 USING TAGS 携带真实 loop_id/unit_id（非空串）."""
    fake_redis = _FakeRedis()
    fake_pipe = _FakePipe()
    fake_redis.pipeline = lambda: fake_pipe  # type: ignore[attr-defined]

    sub = RealtimeSubscriber()
    sub._buffer = {
        "LIC-101": {
            "PV": {"value": "50.5", "quality": 1, "ts": "2026-07-15T10:00:00Z"},
        },
    }

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch(
            "app.services.data_source.realtime_subscriber.batch_insert_multi",
            new=AsyncMock(return_value=1),
        ) as mock_insert,
        patch.object(
            sub,
            "_get_loop_meta_map",
            new=AsyncMock(return_value={"LIC-101": ("loop-uuid-1", "unit-uuid-1")}),
        ),
    ):
        mock_settings.REALTIME_WRITEBACK_ENABLED = True
        await sub._flush_buffer()

    mock_insert.assert_awaited_once()
    tables_rows = mock_insert.await_args.args[0]
    assert len(tables_rows) == 1
    assert tables_rows[0]["loop_id"] == "loop-uuid-1"
    assert tables_rows[0]["unit_id"] == "unit-uuid-1"


@pytest.mark.asyncio
async def test_flush_buffer_writeback_unknown_loop_part_falls_back_to_empty():
    """未配置映射的 loop_part 查不到 meta 时回退空串，不阻塞 flush."""
    fake_redis = _FakeRedis()
    fake_pipe = _FakePipe()
    fake_redis.pipeline = lambda: fake_pipe  # type: ignore[attr-defined]

    sub = RealtimeSubscriber()
    sub._buffer = {
        "UNKNOWN-1": {
            "PV": {"value": "1.0", "quality": 1, "ts": "2026-07-15T10:00:00Z"},
        },
    }

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
        patch(
            "app.services.data_source.realtime_subscriber.batch_insert_multi",
            new=AsyncMock(return_value=1),
        ) as mock_insert,
        patch.object(sub, "_get_loop_meta_map", new=AsyncMock(return_value={})),
    ):
        mock_settings.REALTIME_WRITEBACK_ENABLED = True
        await sub._flush_buffer()

    tables_rows = mock_insert.await_args.args[0]
    assert tables_rows[0]["loop_id"] == ""
    assert tables_rows[0]["unit_id"] == ""


@pytest.mark.asyncio
async def test_get_loop_meta_map_builds_cache_from_db():
    """_get_loop_meta_map 构建两个缓存：loop_part→(loop_id, unit_id) 与 tag→(loop_part, role).

    loop_part 权威来源 = 回路台账 tag_name（2026-08-20 子表名 bug 修复后口径）。
    """
    sub = RealtimeSubscriber()

    # 第一次 execute：活跃回路 (id, tag_name, unit_id)；第二次：映射 (loop_id, role, tag_name)
    loops_result = MagicMock()
    loops_result.all.return_value = [
        ("loop-1", "LIC-101", "unit-1"),
        ("loop-2", "", "unit-2"),  # 无 tag_name，不入缓存
    ]
    mapping_result = MagicMock()
    mapping_result.all.return_value = [
        ("loop-1", "PV", "LIC-101.PV"),
        ("loop-1", "SP", "LIC-101.SP"),
    ]
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[loops_result, mapping_result])
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.data_source.realtime_subscriber.AsyncSessionLocal",
        return_value=mock_session_ctx,
    ):
        meta = await sub._get_loop_meta_map(["LIC-101", "UNKNOWN"])

    assert meta["LIC-101"] == ("loop-1", "unit-1")
    assert meta["UNKNOWN"] == ("", "")
    # 无 tag_name 回路不进缓存
    assert sub._loop_meta_cache == {"LIC-101": ("loop-1", "unit-1")}
    # tag → (loop_part, role) 映射：loop_part 为回路台账 tag_name（无角色后缀）
    assert sub._tag_role_cache == {
        "LIC-101.PV": ("LIC-101", "PV"),
        "LIC-101.SP": ("LIC-101", "SP"),
    }


def test_build_row_ts_is_max_of_role_source_times():
    """R05：行 ts = 合并进该行的所有角色 sourceTime 的最大值（不再 PV 优先）.

    PV 恰为最新角色时行为与旧口径一致；低频角色更新（如 PID 参数晚于 PV）
    时行 ts 取该角色时间——新事件不得沿用旧 PV 时间改写旧行。
    """
    sub = RealtimeSubscriber()
    roles_data = {
        # PID_P 晚于 PV → 行 ts 必须取 PID_P 的 sourceTime
        "PID_P": {"value": "1.2", "quality": 1, "ts": "2026-07-15T10:02:00Z"},
        "PV": {"value": "50.5", "quality": 1, "ts": "2026-07-15T10:00:00Z"},
        "SP": {"value": "60.0", "quality": 1, "ts": "2026-07-15T09:59:00Z"},
    }
    row = sub._build_row(roles_data)
    # PID_P collectTime 10:02:00Z → Asia/Shanghai 18:02:00（max 口径）
    assert "2026-07-15 18:02:00" in row[0]


def test_build_row_ts_falls_back_when_pv_missing():
    """PV 缺失（或无 ts）时行 ts 取其余角色 sourceTime 的最大值."""
    sub = RealtimeSubscriber()
    roles_data = {
        "SP": {"value": "60.0", "quality": 1, "ts": "2026-07-15T10:00:00Z"},
    }
    row = sub._build_row(roles_data)
    assert "2026-07-15 18:00:00" in row[0]
    assert row[1] is None  # pv 为 NULL
    assert row[2] == 60.0  # sp


# ---------------------------------------------------------------------------
# 订阅手工/事件刷新（Redis Pub/Sub 控制频道）
# ---------------------------------------------------------------------------


def _make_leader_sub(fake_redis: _FakeRedis, tags: list[str]) -> RealtimeSubscriber:
    """构造 Leader 态 + 单分片 mock WS 的订阅器（连接池化适配）.

    分片 tags 预置为入参 tags（排序后），``sub._shard_states[0].ws`` 为 mock WS。
    """
    sub = RealtimeSubscriber()
    sub._running = True
    sub._is_leader = True
    st = _ShardState(index=0, total=1, tags=sorted(tags))
    st.ws = AsyncMock()
    st.ws.send = AsyncMock()
    sub._shard_states = [st]
    sub._subscribed_tags = set(tags)
    return sub


class TestSubscriptionRefresh:
    """refresh_subscription：diff 计算 / 重发 SubscribeAsync / 结果 key 写入."""

    @pytest.mark.asyncio
    async def test_refresh_resends_with_diff(self):
        """重查活跃 Tag → diff → 现有 WS 上全量重发（新 invocationId），结果写 Redis."""
        fake_redis = _FakeRedis()
        sub = _make_leader_sub(fake_redis, ["LIC-101.PV", "LIC-101.SP", "LIC-102.PV"])
        sub._invocation_counter = 5
        # 预置落库映射缓存，验证刷新后主动清空（不等 300s TTL）
        sub._tag_role_cache = {"LIC-101.PV": ("LIC-101", "PV")}
        sub._loop_meta_cache = {"LIC-101": ("loop-1", "unit-1")}
        sub._loop_meta_cache_at = 123.0

        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            # LIC-102.PV 移除，LIC-103.PV/LIC-103.SP 新增
            patch.object(
                sub,
                "_get_active_tags",
                return_value=["LIC-101.PV", "LIC-101.SP", "LIC-103.PV", "LIC-103.SP"],
            ),
        ):
            result = await sub.refresh_subscription(
                source="tag-mapping", request_id="req-1", requested_at="2026-09-02T00:00:00+00:00"
            )

        assert result["error"] is None
        assert result["total"] == 4
        assert result["added"] == ["LIC-103.PV", "LIC-103.SP"]
        assert result["removed"] == ["LIC-102.PV"]
        assert result["invocationId"] == "manual_refresh_6"  # 计数器递增
        assert result["leaderPid"] is not None
        assert result["finishedAt"] is not None
        assert result["requestId"] == "req-1"

        # 分片在其现有连接上重发自身位号（连接池化：新增位号走池重建）
        st = sub._shard_states[0]
        sent = st.ws.send.call_args.args[0]
        payload = json.loads(sent.rstrip("\x1e"))
        assert payload["type"] == 1
        assert payload["invocationId"] == "manual_refresh_6"
        assert payload["target"] == "SubscribeAsync"
        assert payload["arguments"] == [["LIC-101.PV", "LIC-101.SP", "LIC-102.PV"]]
        # 新增位号触发连接池重建信号
        assert sub._rebuild_event.is_set()
        assert sub._subscribed_tags == {
            "LIC-101.PV",
            "LIC-101.SP",
            "LIC-103.PV",
            "LIC-103.SP",
        }

        # 落库映射缓存已主动清空
        assert sub._tag_role_cache == {}
        assert sub._loop_meta_cache == {}
        assert sub._loop_meta_cache_at == 0.0

        # 结果写入 Redis key（TTL 60s）
        raw = fake_redis._data[_REFRESH_RESULT_KEY]
        stored = json.loads(raw)
        assert stored["requestId"] == "req-1"
        assert stored["added"] == ["LIC-103.PV", "LIC-103.SP"]
        assert stored["removed"] == ["LIC-102.PV"]
        assert stored["error"] is None

    @pytest.mark.asyncio
    async def test_refresh_writes_error_when_not_leader(self):
        """非 Leader：不产生 WS 动作，写入带 error 的结果."""
        fake_redis = _FakeRedis()
        sub = RealtimeSubscriber()
        sub._running = True
        sub._is_leader = False
        st = _ShardState(index=0, total=1, tags=["LIC-101.PV"])
        st.ws = AsyncMock()
        sub._shard_states = [st]

        with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
            result = await sub.refresh_subscription(source="manual-api", request_id="req-2")

        assert result["error"] is not None
        assert "Leader" in result["error"]
        st.ws.send.assert_not_called()
        stored = json.loads(fake_redis._data[_REFRESH_RESULT_KEY])
        assert stored["error"] == result["error"]

    @pytest.mark.asyncio
    async def test_refresh_writes_error_when_ws_not_connected(self):
        """Leader 但 WS 未连接：写入带 error 的结果."""
        fake_redis = _FakeRedis()
        sub = RealtimeSubscriber()
        sub._running = True
        sub._is_leader = True
        sub._shard_states = []  # 无任何已连接分片

        with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
            result = await sub.refresh_subscription(source="manual-api", request_id="req-3")

        assert result["error"] is not None
        assert "WebSocket" in result["error"]

    @pytest.mark.asyncio
    async def test_refresh_empty_active_tags_skips_send(self):
        """刷新后无活跃 Tag：跳过重发，订阅集合清空，invocationId 为 None."""
        fake_redis = _FakeRedis()
        sub = _make_leader_sub(fake_redis, ["LIC-101.PV"])

        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch.object(sub, "_get_active_tags", return_value=[]),
        ):
            result = await sub.refresh_subscription(source="loop-delete")

        assert result["error"] is None
        assert result["total"] == 0
        assert result["removed"] == ["LIC-101.PV"]
        assert result["invocationId"] is None
        sub._shard_states[0].ws.send.assert_not_called()
        assert sub._rebuild_event.is_set()  # 空集合触发池重建
        assert sub._subscribed_tags == set()

    @pytest.mark.asyncio
    async def test_refresh_result_key_write_failure_swallowed(self):
        """结果 key 写入失败仅记日志，不影响刷新本身."""
        sub = _make_leader_sub(_FakeRedis(), ["LIC-101.PV"])

        with (
            patch("app.services.data_source.realtime_subscriber.redis_client") as mock_redis,
            patch.object(sub, "_get_active_tags", return_value=["LIC-101.PV"]),
        ):
            mock_redis.set = AsyncMock(side_effect=Exception("Redis down"))
            result = await sub.refresh_subscription(source="manual-api")

        assert result["error"] is None
        sub._shard_states[0].ws.send.assert_called_once()


class TestChunkedSubscription:
    """分块订阅（2026-09-03 生产事故加固）。

    8600+ 位号单条订阅消息约 200KB 会被远端 Hub 立即关闭连接（code 1000），
    订阅必须按 _SUBSCRIBE_CHUNK_SIZE 分块发送；≤ 块大小时保持单条兼容旧行为。
    """

    @pytest.mark.asyncio
    async def test_send_subscribe_invocations_chunks_large_list(self):
        """1200 个位号 → 3 块（500/500/200），块间不重不漏、invocationId 唯一递增."""
        sub = RealtimeSubscriber()
        ws = AsyncMock()
        sub._invocation_counter = 0
        tags = [f"T-{i:05d}" for i in range(1200)]

        ids = await sub._send_subscribe_invocations(ws, tags, "sub")

        assert len(ids) == 3
        assert len(set(ids)) == 3  # 唯一
        payloads = [json.loads(c.args[0].rstrip("\x1e")) for c in ws.send.call_args_list]
        assert all(p["type"] == 1 and p["target"] == "SubscribeAsync" for p in payloads)
        assert [len(p["arguments"][0]) for p in payloads] == [500, 500, 200]
        # 块拼接后与原清单完全一致（不重不漏、保序）
        assert [t for p in payloads for t in p["arguments"][0]] == tags
        # invocationId 逐块递增
        assert ids == [p["invocationId"] for p in payloads]

    @pytest.mark.asyncio
    async def test_send_subscribe_invocations_small_list_single_message(self):
        """≤ 块大小时单条发送，格式与旧实现一致（兼容 Completion 初始值链路）."""
        sub = RealtimeSubscriber()
        ws = AsyncMock()
        sub._invocation_counter = 2
        tags = ["LIC-101.PV", "LIC-101.SP"]

        ids = await sub._send_subscribe_invocations(ws, tags, "sub")

        assert ids == ["sub_3"]
        ws.send.assert_called_once()
        payload = json.loads(ws.send.call_args.args[0].rstrip("\x1e"))
        assert payload["type"] == 1
        assert payload["invocationId"] == "sub_3"
        assert payload["arguments"] == [["LIC-101.PV", "LIC-101.SP"]]

    @pytest.mark.asyncio
    async def test_refresh_subscription_chunks_when_over_limit(self):
        """手工刷新：分片按块重发自身 1200 位号（3 条消息）；新增位号触发池重建."""
        fake_redis = _FakeRedis()
        shard_tags = [f"T-{i:05d}" for i in range(1200)]
        sub = _make_leader_sub(fake_redis, shard_tags)
        new_tags = shard_tags + ["T-NEW"]

        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch.object(sub, "_get_active_tags", return_value=new_tags),
        ):
            result = await sub.refresh_subscription(source="manual-api")

        assert result["error"] is None
        assert result["total"] == 1201
        assert result["added"] == ["T-NEW"]
        assert result["invocationId"] == "manual_refresh_1"
        assert sub._shard_states[0].ws.send.call_count == 3  # 1200/500 → 3 块
        assert sub._rebuild_event.is_set()
        assert sub._subscribed_tags == set(new_tags)


class TestControlLoop:
    """_control_loop：控制频道消息的监听与分发."""

    @pytest.mark.asyncio
    async def test_control_message_triggers_refresh_resend(self):
        """收到 refresh 消息后重发 SubscribeAsync（invocationId 递增、新列表）."""
        import asyncio

        fake_redis = _FakeRedis()
        pubsub = _FakePubSub(
            messages=[
                {
                    "type": "message",
                    "channel": _CONTROL_CHANNEL,
                    "data": json.dumps(
                        {
                            "type": "refresh",
                            "requestId": "req-c1",
                            "source": "loop-import",
                            "requestedAt": "2026-09-02T00:00:00+00:00",
                        }
                    ),
                }
            ]
        )
        fake_redis.pubsub_factory = lambda: pubsub

        sub = _make_leader_sub(fake_redis, ["OLD.PV"])
        sub._invocation_counter = 1

        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch.object(sub, "_get_active_tags", return_value=["OLD.PV", "NEW.PV"]),
        ):
            task = asyncio.create_task(sub._control_loop())
            try:
                # 等待消息被消费并完成刷新（结果 key 出现）
                for _ in range(100):
                    if _REFRESH_RESULT_KEY in fake_redis._data:
                        break
                    await asyncio.sleep(0.02)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # 控制频道已订阅，连接已清理
        assert pubsub.subscribed == [_CONTROL_CHANNEL]
        assert pubsub.closed is True

        # 分片重发自身位号（新增位号 NEW.PV 触发池重建），invocationId 递增
        sent = sub._shard_states[0].ws.send.call_args.args[0]
        payload = json.loads(sent.rstrip("\x1e"))
        assert payload["invocationId"] == "manual_refresh_2"
        assert payload["arguments"] == [["OLD.PV"]]
        assert sub._rebuild_event.is_set()

        # 结果透传 requestId/source
        stored = json.loads(fake_redis._data[_REFRESH_RESULT_KEY])
        assert stored["requestId"] == "req-c1"
        assert stored["source"] == "loop-import"
        assert stored["added"] == ["NEW.PV"]
        assert stored["removed"] == []

    @pytest.mark.asyncio
    async def test_control_loop_ignores_non_refresh_messages(self):
        """非 JSON / 非 refresh 类型消息被忽略，不触发刷新."""
        import asyncio

        fake_redis = _FakeRedis()
        pubsub = _FakePubSub(
            messages=[
                {"type": "message", "channel": _CONTROL_CHANNEL, "data": "not-json"},
                {
                    "type": "message",
                    "channel": _CONTROL_CHANNEL,
                    "data": json.dumps({"type": "other"}),
                },
                {"type": "subscribe", "channel": _CONTROL_CHANNEL, "data": 1},
            ]
        )
        fake_redis.pubsub_factory = lambda: pubsub

        sub = _make_leader_sub(fake_redis, [])
        sub.refresh_subscription = AsyncMock()

        with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
            task = asyncio.create_task(sub._control_loop())
            try:
                for _ in range(50):
                    if not pubsub.messages:
                        break
                    await asyncio.sleep(0.02)
                await asyncio.sleep(0.05)  # 等可能的误触发
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        sub.refresh_subscription.assert_not_called()

    @pytest.mark.asyncio
    async def test_become_leader_starts_control_task_and_resign_stops(self):
        """Leadership 切换启停控制监听：成为 Leader 启动，卸任取消；非 Leader 不监听."""
        sub = RealtimeSubscriber()
        sub._running = True
        assert sub._control_task is None  # 待命进程不监听

        sub._run = _idle_loop
        sub._flush_loop = _idle_loop
        sub._refresh_loop = _idle_loop
        sub._control_loop = _idle_loop

        sub._become_leader()
        assert sub._control_task is not None

        await sub._resign_leader()
        assert sub._control_task is None
        assert sub._is_leader is False


class TestNotifyAndRequestRefresh:
    """notify_subscription_changed / request_subscription_refresh helper 测试."""

    @pytest.mark.asyncio
    async def test_notify_publishes_refresh_message(self):
        """变更写路径的通知发布到控制频道（fire-and-forget）."""
        fake_redis = _FakeRedis()
        with patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis):
            await notify_subscription_changed("tag-mapping")

        assert len(fake_redis.published) == 1
        channel, message = fake_redis.published[0]
        assert channel == _CONTROL_CHANNEL
        payload = json.loads(message)
        assert payload["type"] == "refresh"
        assert payload["source"] == "tag-mapping"
        assert payload["requestId"] is None
        assert payload["requestedAt"]

    @pytest.mark.asyncio
    async def test_notify_swallows_redis_error(self):
        """publish 失败仅记日志，不影响业务主流程."""
        with patch("app.services.data_source.realtime_subscriber.redis_client") as mock_redis:
            mock_redis.publish = AsyncMock(side_effect=Exception("Redis down"))
            await notify_subscription_changed("loop-import")  # 不应抛出

    @pytest.mark.asyncio
    async def test_request_refresh_success(self):
        """发布带 requestId 的指令后轮询到匹配结果即返回."""
        fake_redis = _FakeRedis()

        async def _leader_answer(channel: str, message: str) -> int:
            """模拟 Leader：按指令 requestId 写入结果 key."""
            payload = json.loads(message)
            result = {
                "requestId": payload["requestId"],
                "requestedAt": payload["requestedAt"],
                "finishedAt": "2026-09-02T00:00:01+00:00",
                "source": payload["source"],
                "total": 3,
                "added": ["NEW.PV"],
                "removed": [],
                "invocationId": "manual_refresh_1",
                "leaderPid": 12345,
                "error": None,
            }
            await fake_redis.set(_REFRESH_RESULT_KEY, json.dumps(result), ex=60)
            return 1

        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=SimpleNamespace(_running=True),
            ),
        ):
            mock_s.SIGNALR_ENABLED = True
            fake_redis.publish = _leader_answer  # type: ignore[method-assign]
            result = await request_subscription_refresh(timeout=2.0, interval=0.05)

        assert result["total"] == 3
        assert result["added"] == ["NEW.PV"]
        assert result["invocationId"] == "manual_refresh_1"
        assert result["error"] is None
        # 结果 key 已被清除后再由 Leader 写入（非残留）
        assert _REFRESH_RESULT_KEY in fake_redis._data

    @pytest.mark.asyncio
    async def test_request_refresh_ignores_foreign_result_and_times_out(self):
        """requestId 不匹配的结果（如事件驱动刷新 requestId=None）不被误取."""
        fake_redis = _FakeRedis()

        async def _foreign_answer(channel: str, message: str) -> int:
            result = {
                "requestId": None,  # 事件驱动刷新结果
                "requestedAt": "2026-09-02T00:00:00+00:00",
                "finishedAt": "2026-09-02T00:00:01+00:00",
                "source": "tag-mapping",
                "total": 1,
                "added": [],
                "removed": [],
                "invocationId": "manual_refresh_9",
                "leaderPid": 12345,
                "error": None,
            }
            await fake_redis.set(_REFRESH_RESULT_KEY, json.dumps(result), ex=60)
            return 1

        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=SimpleNamespace(_running=True),
            ),
        ):
            mock_s.SIGNALR_ENABLED = True
            fake_redis.publish = _foreign_answer  # type: ignore[method-assign]
            with pytest.raises(BizError) as exc_info:
                await request_subscription_refresh(timeout=0.3, interval=0.05)

        assert exc_info.value.code == "ERR_SUBSCRIPTION_REFRESH_TIMEOUT"
        assert exc_info.value.status_code == 504

    @pytest.mark.asyncio
    async def test_request_refresh_rejected_when_signalr_disabled(self):
        """SIGNALR_ENABLED=False：直接返回明确错误，不发布指令."""
        fake_redis = _FakeRedis()
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
        ):
            mock_s.SIGNALR_ENABLED = False
            with pytest.raises(BizError) as exc_info:
                await request_subscription_refresh(timeout=0.2, interval=0.05)

        assert exc_info.value.code == "ERR_SIGNALR_DISABLED"
        assert fake_redis.published == []

    @pytest.mark.asyncio
    async def test_request_refresh_rejected_when_subscriber_not_running(self):
        """订阅器未运行（Hub URL 未配置等）：返回明确错误."""
        fake_redis = _FakeRedis()
        with (
            patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=SimpleNamespace(_running=False),
            ),
        ):
            mock_s.SIGNALR_ENABLED = True
            with pytest.raises(BizError) as exc_info:
                await request_subscription_refresh(timeout=0.2, interval=0.05)

        assert exc_info.value.code == "ERR_SUBSCRIBER_NOT_RUNNING"
        assert fake_redis.published == []
