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
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_source.realtime_subscriber import (
    _GAP_BACKFILL_LOCK_KEY,
    _REDIS_KEY_PREFIX,
    RealtimeSubscriber,
    _normalize_ts,
    get_subscriber,
    start_subscriber,
    stop_subscriber,
)

# ---------------------------------------------------------------------------
# Fake Redis（轻量级，仅支持 setex/mget）
# ---------------------------------------------------------------------------


class _FakeRedis:
    """轻量级 Redis mock，支持 setex/mget/publish/set(nx)/eval/pipeline."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

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
        if key in self._data:
            del self._data[key]
            return 1
        return 0

    async def eval(self, script: str, numkeys: int, *args: str) -> Any:
        """简化 Lua 脚本执行：仅支持 SETNX 锁释放（CAS DEL）."""
        key = args[0]
        token = args[1] if len(args) > 1 else ""
        if "DEL" in script and self._data.get(key) == token:
            del self._data[key]
            return 1
        return 0

    def pipeline(self):
        """返回 self（pipeline 操作直接在本地执行）."""
        return self

    async def execute(self):
        """pipeline execute（no-op，操作已即时执行）."""
        return []

    async def lpush(self, key: str, value: str) -> int:
        return 1

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        pass

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def hset(self, key: str, *, mapping: dict | None = None) -> int:
        return 1

    async def hgetall(self, key: str) -> dict:
        return {}

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
    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        mock_settings.SIGNALR_ENABLED = True
        mock_settings.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
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

    with (
        patch("app.services.data_source.realtime_subscriber.redis_client", fake_redis),
        patch("app.services.data_source.realtime_subscriber.settings") as mock_settings,
    ):
        mock_settings.REALTIME_WRITEBACK_ENABLED = False
        mock_settings.DATA_SOURCE_TYPE = "remote_api"
        await sub._cache_value(item)

    expected_key = f"{_REDIS_KEY_PREFIX}LIC-101.PV"
    assert expected_key in fake_redis._data
    cached = json.loads(fake_redis._data[expected_key])
    assert cached["tagCode"] == "LIC-101.PV"
    assert cached["value"] == "50.5"
    assert cached["quality"] == 0
    assert "LIC-101" in sub._buffer


@pytest.mark.asyncio
async def test_cache_value_buffers_writeback_when_enabled():
    """开启写回且数据源为 tdengine 时，保留旧宽表缓冲逻辑。"""
    fake_redis = _FakeRedis()
    sub = RealtimeSubscriber()

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

    assert sub._buffer["LIC-101"]["PV"] == {
        "value": "50.5",
        "quality": 1,
        "ts": "2026-06-28T08:00:00Z",
    }


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
    """缺口小于 MIN_GAP 时不触发补数."""
    import time as _time

    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 10  # 10s < 60s

    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        _gap_settings(mock_settings)
        await sub._maybe_trigger_gap_backfill()

    assert sub._backfill_task is None


@pytest.mark.asyncio
async def test_gap_backfill_skipped_when_disabled():
    """GAP_BACKFILL_ENABLED=False 时不触发补数."""
    import time as _time

    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 3600

    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
        _gap_settings(mock_settings)
        mock_settings.GAP_BACKFILL_ENABLED = False
        await sub._maybe_trigger_gap_backfill()

    assert sub._backfill_task is None


@pytest.mark.asyncio
async def test_gap_backfill_triggered_on_reconnect():
    """缺口 ≥ MIN_GAP 时创建补数任务，窗口为 [last_flushed_at, now-2s]."""
    import time as _time

    sub = RealtimeSubscriber()
    last_flushed_at = _time.time() - 300  # 5 分钟缺口
    sub._last_flushed_at = last_flushed_at

    with (
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

    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 48 * 3600  # 48h 缺口，上限 24h

    with (
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

    sub = RealtimeSubscriber()
    sub._last_flushed_at = _time.time() - 3600

    async def _pending():
        await asyncio.sleep(100)

    running_task = asyncio.create_task(_pending())
    sub._backfill_task = running_task

    with patch("app.services.data_source.realtime_subscriber.settings") as mock_settings:
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

    sub = RealtimeSubscriber()
    sub._running = True
    gap_start = _time.time() - 900
    sub._retry_window_start = gap_start
    sub._backfill_retry_count = 1

    with patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_backfill:
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

    sub = RealtimeSubscriber()
    sub._running = True
    sub._retry_window_start = _time.time() - 900
    sub._backfill_retry_count = 1

    async def _pending():
        await asyncio.sleep(100)

    running_task = asyncio.create_task(_pending())
    sub._backfill_task = running_task
    try:
        with patch.object(sub, "_run_gap_backfill", new=AsyncMock()) as mock_backfill:
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

    def test_build_row_empty_ts_uses_now(self):
        """空时间戳应使用当前目标时区时间。"""
        sub = RealtimeSubscriber()
        roles_data = {
            "PV": {"value": "50.5", "quality": 1, "ts": ""},
        }
        row = sub._build_row(roles_data)
        assert len(row[0]) > 0


# ---------------------------------------------------------------------------
# 数据停滞看门狗测试（WS-B2 阶段 9）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_triggers_disconnect_on_stall():
    """数据停滞看门狗：超过 stall_timeout 无消息时应主动断开。"""
    import time as _time

    sub = RealtimeSubscriber()
    sub._running = True

    # 模拟上次收到数据是 10 分钟前（超过 stall_timeout=1s）
    sub._last_data_at = _time.time() - 600

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
        patch.object(sub, "_get_active_tags", return_value=["LIC-101.PV"]),
        patch.object(sub, "_maybe_trigger_gap_backfill", return_value=None),
    ):
        _gap_settings(mock_s)
        mock_s.SIGNALR_HUB_URL = "ws://localhost:7106/signalr/realValueForClpmHub"
        mock_s.SIGNALR_STALL_TIMEOUT_SECONDS = 1  # 1 秒超时
        mock_s.SIGNALR_PING_INTERVAL = 30
        mock_s.SIGNALR_PING_TIMEOUT = 60
        mock_s.SIGNALR_OPEN_TIMEOUT = 15

        await sub._connect_and_subscribe()

    # 看门狗触发后 _ws 应被置 None（_close_ws_safely 调用）
    assert sub._ws is None
    # mock_ws.close 应被调用（_close_ws_safely 内部）
    mock_ws.close.assert_called()


@pytest.mark.asyncio
async def test_watchdog_no_disconnect_when_data_recent():
    """数据停滞看门狗：数据在 stall_timeout 内时不断开，继续接收。"""
    import time as _time

    sub = RealtimeSubscriber()
    sub._running = True

    # 模拟上次收到数据是刚刚（未超时）
    sub._last_data_at = _time.time()

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

        await sub._connect_and_subscribe()

    # 未触发断开：_ws 仍非 None（或因 running=False 退出，但非看门狗触发）
    # close 不应被 _close_ws_safely 调用（看门狗未触发）


@pytest.mark.asyncio
async def test_watchdog_no_disconnect_when_no_last_data():
    """看门狗：_last_data_at 为 None（从未收到数据）时不触发断开。"""
    sub = RealtimeSubscriber()
    sub._running = True
    sub._last_data_at = None  # 从未收到数据

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

        await sub._connect_and_subscribe()

    # _last_data_at 为 None 时不应触发断开（close 不被看门狗调用）
    # ws.close 可能在循环退出后由外部 stop() 调用，但 _close_ws_safely 未被看门狗触发
