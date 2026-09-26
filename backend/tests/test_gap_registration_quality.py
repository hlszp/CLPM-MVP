"""1a 缺口登记质量回归（2026-09-26）：会话身份、重叠合并、并集计时长。

背景：1a 上线后实测发现
1. session_id 恒为 realtime:unknown（token 未生成时写死 unknown）→ 身份丢失且
   绕过去重键；
2. 每次重连都新增一行近重复 gap（start/now 微差）→ 表随重启膨胀；
3. 跨会话重叠行在 observedRatio 里被朴素累加 → 覆盖率被压低甚至归零。
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_source import point_history_metadata as phm
from app.services.data_source.realtime_subscriber import gap_session_id
from app.services.trend_service import attach_gap_info

T0 = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)


def _dt(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class _Row:
    """既有 gap 行替身。"""

    def __init__(self, start: datetime, end: datetime) -> None:
        self.seg_start = start
        self.seg_end = end


class _Scalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _Scalars:
        return _Scalars(self._rows)

    def all(self) -> list:
        return self._rows


class _FakeDB:
    """按测试给定的重叠过滤器返回既有行（模拟 SQL 的 where 语义）。"""

    def __init__(self, rows: list | None = None, window: tuple | None = None) -> None:
        self.rows = list(rows or [])
        self.window = window
        self.added: list = []
        self.flushed = 0

    async def execute(self, *_a, **_k) -> _Result:
        if self.window is None:
            return _Result(list(self.rows))
        s, e = self.window
        # 与 SQL where 同语义：seg_start <= 新 end AND seg_end >= 新 start
        # （相邻 a.end == b.start 也算命中，与 Redis 待补列表口径一致）
        return _Result([r for r in self.rows if r.seg_start <= e and r.seg_end >= s])

    def add(self, obj) -> None:
        self.added.append(obj)
        self.rows.append(_Row(obj.seg_start, obj.seg_end))

    async def flush(self) -> None:
        self.flushed += 1


class TestGapSessionId:
    def test_missing_token_is_not_unknown_and_is_stable(self) -> None:
        sid1 = gap_session_id(None)
        sid2 = gap_session_id(None)
        assert sid1 == sid2, "同一进程内必须稳定（否则去重键失效）"
        assert "unknown" not in sid1
        assert socket.gethostname() in sid1
        assert len(sid1) <= 64, "列宽约束"

    def test_token_preserved_and_truncated(self) -> None:
        assert gap_session_id("host:1:2") == "realtime:host:1:2"
        assert len(gap_session_id("x" * 200)) == 64


class TestGapRegistrationMerge:
    async def test_same_window_twice_keeps_single_row(self) -> None:
        db = _FakeDB(window=(_dt(0), _dt(10)))
        first = await phm.register_gap_segment(
            db, session_id="s1", seg_start=_dt(0), seg_end=_dt(10)
        )
        db.window = (_dt(0), _dt(10))
        second = await phm.register_gap_segment(
            db, session_id="s1", seg_start=_dt(0), seg_end=_dt(10)
        )
        assert first is True and second is False
        assert len(db.added) == 1, "同窗口重复登记不得新增行"

    async def test_overlapping_window_extends_existing_row(self) -> None:
        head = _Row(_dt(0), _dt(10))
        db = _FakeDB(rows=[head], window=(_dt(5), _dt(20)))
        changed = await phm.register_gap_segment(
            db, session_id="s1", seg_start=_dt(5), seg_end=_dt(20)
        )
        assert changed is True
        assert db.added == [], "重叠窗口不得新增行"
        assert head.seg_start == _dt(0) and head.seg_end == _dt(20), "应扩展为并集"

    async def test_adjacent_window_merges_like_redis_semantics(self) -> None:
        head = _Row(_dt(0), _dt(10))
        db = _FakeDB(rows=[head], window=(_dt(10), _dt(20)))
        changed = await phm.register_gap_segment(
            db, session_id="s1", seg_start=_dt(10), seg_end=_dt(20)
        )
        assert changed is True and head.seg_end == _dt(20)

    async def test_window_inside_existing_row_is_noop(self) -> None:
        head = _Row(_dt(0), _dt(60))
        db = _FakeDB(rows=[head], window=(_dt(10), _dt(20)))
        changed = await phm.register_gap_segment(
            db, session_id="s1", seg_start=_dt(10), seg_end=_dt(20)
        )
        assert changed is False and db.added == []
        assert head.seg_start == _dt(0) and head.seg_end == _dt(60)

    async def test_disjoint_window_inserts_new_row(self) -> None:
        db = _FakeDB(window=(_dt(100), _dt(110)))
        changed = await phm.register_gap_segment(
            db, session_id="s1", seg_start=_dt(100), seg_end=_dt(110)
        )
        assert changed is True and len(db.added) == 1


class TestObservedRatioUnion:
    async def test_overlapping_gap_rows_are_not_double_counted(self) -> None:
        """两段重叠缺口（10-40min 与 30-50min）并集 = 40min → 覆盖率 1-2400/3600。"""
        ts = ["2026-09-26T00:00:00.000Z", "2026-09-26T01:00:00.000Z"]
        rows = [(_dt(10), _dt(40)), (_dt(30), _dt(50))]
        db = _FakeDB(rows=rows)

        result = await attach_gap_info(db, "loop-1", {"timestamps": ts})

        assert len(result["gaps"]) == 1, "重叠段应并成一段"
        assert result["gaps"][0]["seconds"] == pytest.approx(2400.0)
        assert result["observedRatio"] == pytest.approx(1 - 2400 / 3600, abs=1e-4)

    async def test_no_gap_means_full_observed_ratio(self) -> None:
        db = _FakeDB(rows=[])
        result = await attach_gap_info(
            db, "loop-1", {"timestamps": ["2026-09-26T00:00:00.000Z", "2026-09-26T01:00:00.000Z"]}
        )
        assert result["gaps"] == []
        assert result["observedRatio"] == pytest.approx(1.0)
