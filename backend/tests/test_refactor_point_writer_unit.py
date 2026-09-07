"""P2 单元测试：PointHistoryWriter 事件语义与质量解码（fake 写入层）.

覆盖（计划 P2 / 必测矩阵）：
- T02/V07：同 tick 多事件不覆盖、纯质量变化保留、合法迟到入历史；
- V05：质量解码唯一入口——未知码 → UNKNOWN（绝不 Good）；
- 队列满 → 计数 + 缺口登记（禁止默填）；
- 非有限值 → value=None（质量照记）；坏时间 → 丢弃计数。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_source import point_history_writer as pw


@pytest.fixture
async def writer(monkeypatch):
    """拦截仓储写入（fake），返回 (writer, captured)."""
    captured: dict[str, list] = {"events": [], "results": []}

    class _FakeResult:
        def __init__(self):
            self.inserted = 0
            self.identical_skipped = 0
            self.conflicts = []
            self.failed = False
            self.error = None

    async def fake_write(events, **kw):  # noqa: ARG001
        captured["events"].extend(events)
        r = _FakeResult()
        r.inserted = len(events)
        captured["results"].append(r)
        return r

    monkeypatch.setattr(
        "app.services.data_source.point_history_repository.write_events", fake_write
    )
    w = pw.PointHistoryWriter(max_queue=10)
    yield w, captured


def _ts(s: float) -> datetime:
    return datetime(2026, 9, 6, tzinfo=UTC) + timedelta(seconds=s)


class TestSubmitRaw:
    def _prime(self, w, tag="TAG_A"):
        w._tag_points[tag] = "11111111-2222-3333-4444-555555555555"

    async def test_same_tick_multiple_events_preserved(self, writer):
        w, captured = writer
        self._prime(w)
        # 同一 tick（同秒内不同源时刻）两次值变化——都以独立事件入队
        assert w.submit_raw("TAG_A", 1.0, 1, _ts(0).isoformat())
        assert w.submit_raw("TAG_A", 2.0, 1, (_ts(0) + timedelta(milliseconds=300)).isoformat())
        await w._flush_once(final=True)
        values = [e.value for e in captured["events"]]
        assert values == [1.0, 2.0]  # 中间事件不被覆盖（T02）

    async def test_quality_only_change_preserved(self, writer):
        w, captured = writer
        self._prime(w)
        assert w.submit_raw("TAG_A", 5.0, 1, _ts(1).isoformat())
        assert w.submit_raw("TAG_A", 5.0, 0, _ts(2).isoformat())  # 值不变质量变
        await w._flush_once(final=True)
        qs = [(e.value, e.quality_class) for e in captured["events"]]
        assert qs == [(5.0, 1), (5.0, 0)]  # 质量事件独立持久化（V05）

    async def test_late_event_accepted(self, writer):
        w, captured = writer
        self._prime(w)
        w.submit_raw("TAG_A", 1.0, 1, _ts(100).isoformat())
        w.submit_raw("TAG_A", 2.0, 1, _ts(50).isoformat())  # 合法迟到（真实历史时刻）
        await w._flush_once(final=True)
        assert len(captured["events"]) == 2  # 迟到不丢（最新值门控只在显示缓存）

    async def test_nonfinite_value_null_quality_kept(self, writer):
        w, captured = writer
        self._prime(w)
        w.submit_raw("TAG_A", "nan", 0, _ts(3).isoformat())
        w.submit_raw("TAG_A", "-1.#QNAN0", 1, _ts(4).isoformat())
        await w._flush_once(final=True)
        assert [e.value for e in captured["events"]] == [None, None]
        assert [e.quality_class for e in captured["events"]] == [0, 1]

    async def test_bad_ts_dropped(self, writer):
        w, captured = writer
        self._prime(w)
        assert not w.submit_raw("TAG_A", 1.0, 1, "")
        assert not w.submit_raw("TAG_A", 1.0, 1, "not-a-time")
        assert w.metrics["events_dropped_bad_ts"] == 2

    async def test_unknown_point_dropped(self, writer):
        w, _ = writer
        assert not w.submit_raw("NOPE", 1.0, 1, _ts(0).isoformat())
        assert w.metrics["events_dropped_no_point"] == 1

    async def test_queue_full_registers_gap(self, writer):
        w, _ = writer
        self._prime(w)
        for i in range(10):
            assert w.submit_raw("TAG_A", float(i), 1, _ts(i).isoformat())
        assert not w.submit_raw("TAG_A", 99.0, 1, _ts(99).isoformat())  # 队列满
        assert w.metrics["events_dropped_queue_full"] == 1
        assert w.metrics["gap_windows"] == 1
        assert w._gap_windows  # 缺口窗口内存登记（不默填）


class TestDecodeQuality:
    def test_aas_known_codes(self):
        assert pw.decode_quality(1, 1) == (1, 1)
        assert pw.decode_quality(0, 1) == (0, 0)
        assert pw.decode_quality(2, 1) == (-1, 2)
        assert pw.decode_quality(3, 1) == (-1, 3)

    def test_aas_unknown_never_good(self):
        assert pw.decode_quality(7, 1) == (-1, 7)
        assert pw.decode_quality(None, 1) == (-1, None)
        assert pw.decode_quality("x", 1) == (-1, None)

    def test_opcda(self):
        assert pw.decode_quality(192, 2) == (1, 192)
        assert pw.decode_quality(0, 2) == (0, 0)
        assert pw.decode_quality(63, 2) == (0, 63)
        assert pw.decode_quality(64, 2) == (-1, 64)  # uncertain 段

    def test_history_quality(self):
        assert pw.decode_history_quality(1) == (1, 1)
        assert pw.decode_history_quality(2) == (0, 2)
        assert pw.decode_history_quality(0) == (-1, 0)
        assert pw.decode_history_quality(None) == (-1, None)

    def test_unknown_schema_never_good(self):
        assert pw.decode_quality(1, 99) == (-1, 1)


class TestParseSourceTs:
    def test_naive_treated_as_utc(self):
        dt = pw.parse_source_ts("2026-09-06T08:00:00")
        assert dt == datetime(2026, 9, 6, 8, tzinfo=UTC)

    def test_z_suffix(self):
        dt = pw.parse_source_ts("2026-09-06T00:00:00Z")
        assert dt == datetime(2026, 9, 6, tzinfo=UTC)

    def test_bad(self):
        assert pw.parse_source_ts("") is None
        assert pw.parse_source_ts(None) is None


class TestEnsureSchemaWiring:
    """zpdev 2026-09-07 实测缺口回归：全新环境 st_point_data_v1 不存在时
    flush 持续 0x2603——_flush_loop 启动须先幂等建表，表缺失异常须自愈。"""

    async def test_flush_loop_ensures_schema_on_start(self, writer, monkeypatch):
        w, _ = writer
        calls: list[int] = []

        async def fake_ensure():
            calls.append(1)

        async def fake_refresh(force=False):  # noqa: ARG001
            pass

        monkeypatch.setattr(
            "app.services.data_source.point_history_repository.ensure_schema", fake_ensure
        )
        monkeypatch.setattr(w, "refresh_tag_points", fake_refresh)
        w._running = False  # 立即退出主循环（启动段已执行）
        await w._flush_loop()
        assert len(calls) == 1

    async def test_table_missing_error_triggers_self_heal(self, writer, monkeypatch):
        w, _ = writer
        ensures: list[int] = []

        async def fake_ensure():
            ensures.append(1)

        async def failing_flush(*a, **kw):  # noqa: ARG001
            raise RuntimeError("[0x2603]: Fail to get table info, error: Table does not exist")

        async def ok_flush(*a, **kw):  # noqa: ARG001
            return None

        monkeypatch.setattr(
            "app.services.data_source.point_history_repository.ensure_schema", fake_ensure
        )
        monkeypatch.setattr(w, "_flush_once", failing_flush)
        # 模拟循环两拍：第 1 拍表缺失异常（触发自愈），第 2 拍成功退出
        monkeypatch.setattr(pw.asyncio, "sleep", self._sleep_once_then_stop(w))
        await w._flush_loop()
        assert len(ensures) == 1  # 启动 1 次 + 异常自愈 1 次

    @staticmethod
    def _sleep_once_then_stop(w):
        state = {"n": 0}

        async def _sleep(_interval):
            state["n"] += 1
            if state["n"] >= 2:
                w._running = False

        return _sleep
