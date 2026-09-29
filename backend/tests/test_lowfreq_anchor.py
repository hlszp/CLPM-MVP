"""低频锚点补点任务单测（0929 SP/MODE 趋势空白治理）。

口径锁定：仅低频角色（SP/MODE/PID_*）进入候选；最后状态值为 None 的点位
不延展（诚实化：无效值不前向填充）；锚点 source_kind=SNAPSHOT。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.tasks import lowfreq_anchor


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._rows)


class _FakeSessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture
def patch_deps(monkeypatch):
    """替换 PG 查询与 repository 读写两依赖。"""

    def _patch(candidates, last_states):
        db = _FakeDb([tuple(c) for c in candidates])
        import app.core.db as db_mod

        monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _FakeSessionCtx(db))
        m_read = AsyncMock(return_value=last_states)
        m_write = AsyncMock(return_value=SimpleNamespace(inserted=len(last_states)))
        monkeypatch.setattr(
            "app.services.data_source.point_history_repository.read_last_states_before",
            m_read,
        )
        monkeypatch.setattr(
            "app.services.data_source.point_history_repository.write_events",
            m_write,
        )
        return m_read, m_write

    return _patch


@pytest.mark.asyncio
async def test_anchor_builds_snapshot_events(patch_deps):
    """有值的低频位号 → SNAPSHOT 锚点事件；写入走 dedup=False 热路径。"""
    candidates = [
        ("p-sp", "TAG_SP"),
        ("p-mode", "TAG_MODE"),
    ]
    last_states = {
        "p-sp": {
            "value": 5.0,
            "quality_class": 1,
            "quality_raw": None,
            "quality_schema": None,
        },
        "p-mode": {
            "value": 2.0,
            "quality_class": 1,
            "quality_raw": None,
            "quality_schema": None,
        },
    }
    m_read, m_write = patch_deps(candidates, last_states)

    result = await lowfreq_anchor._do_anchor()

    assert result["candidates"] == 2
    assert result["anchors_built"] == 2
    assert result["anchors_written"] == 2
    assert result["skipped_no_value"] == 0
    # 全部走 SNAPSHOT 锚点语义
    for call in m_write.await_args_list:
        events = call.args[0] if call.args else call.kwargs["events"]
        for ev in events:
            assert ev.source_kind == 2  # SOURCE_KIND_SNAPSHOT
        assert call.kwargs.get("dedup") is False


@pytest.mark.asyncio
async def test_anchor_skips_none_value(patch_deps):
    """最后状态值为 None（无效值）不延展——诚实化：无效值不前向填充。"""
    candidates = [("p-sp", "TAG_SP"), ("p-bad", "TAG_MODE")]
    last_states = {
        "p-sp": {
            "value": 5.0,
            "quality_class": 1,
            "quality_raw": None,
            "quality_schema": None,
        },
        "p-bad": None,  # 从无有效值
    }
    m_read, m_write = patch_deps(candidates, last_states)

    result = await lowfreq_anchor._do_anchor()

    assert result["candidates"] == 2
    assert result["anchors_built"] == 1
    assert result["skipped_no_value"] == 1
    written_events = m_write.await_args.args[0]
    assert [ev.point_id for ev in written_events] == ["p-sp"]


@pytest.mark.asyncio
async def test_anchor_backfill_fills_hourly_between_last_and_now(patch_deps):
    """回填模式：从最后有值点的下一整点到当前整点每小时一个（COV 语义还原）。"""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    last_ts = now - timedelta(hours=72)  # 最后有值点在 3 天前
    candidates = [("p-sp", "TAG_SP")]
    last_states = {
        "p-sp": {
            "value": 5.0,
            "quality_class": 1,
            "quality_raw": None,
            "quality_schema": None,
            "ts": last_ts,
        },
    }
    m_read, m_write = patch_deps(candidates, last_states)

    result = await lowfreq_anchor._do_anchor(backfill_hours=720)

    written = m_write.await_args.args[0]
    ts_list = [ev.ts for ev in written]
    # 起点 = last_ts 下一整点；终点 = 当前整点；每小时一个
    assert ts_list[0] == last_ts.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    assert ts_list[-1] == now
    assert len(ts_list) == 72  # now-71h..now 含两端共 72 个整点
    assert result["anchors_built"] == len(ts_list)


@pytest.mark.asyncio
async def test_anchor_no_candidates_early_return(patch_deps):
    """无活跃低频位号：早退且不触碰 repository。"""
    m_read, m_write = patch_deps([], {})

    result = await lowfreq_anchor._do_anchor()

    assert result["candidates"] == 0
    m_read.assert_not_awaited()
    m_write.assert_not_awaited()
