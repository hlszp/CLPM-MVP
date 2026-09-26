"""P0/P1 回归（2026-09-26）：gap 标记不得抹掉点表实有数据 + 登记前点表核对。

P0 背景（父代理实测）：某个被登记为 gap 的窗口里，点表实有 6,213 行、质量码全为
Good，但 build_logical_wide 给 pv 全 None、valid_rate=0.0 → KPI/诊断/整定整窗失明
（趋势走 TD 端 FILL(PREV) 不受影响，于是又出现"趋势有数、算法无数"的不一致）。

P1 背景：登记前先确认点表在该区间确实没有行，避免把有数据的窗口标成无数据。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.contracts import series_context as sc
from app.services.data_source import logical_wide_builder as lwb
from app.services.data_source.realtime_subscriber import point_table_has_rows

LOOP = "loop-test"


class _Seg:
    """最小 segment 替身（builder 需要 point_id/start/end/initial_check）。"""

    def __init__(self, pid: str, start: datetime, end: datetime) -> None:
        self.point_id = pid
        self.start = start
        self.end = end
        self.initial_check = start
        self.binding_version = 1


async def _build(monkeypatch, events, t0, t1, gaps):
    pid = "point-1"

    async def _fake_segments(_db, _loop_id, roles, grid_start, grid_end):
        return {r.upper(): [_Seg(pid, grid_start, grid_end)] for r in roles}

    async def _fake_read_events(point_ids, start, end, **_kw):
        out = {p: [] for p in point_ids}
        for ts, val in events:
            if start <= ts <= end:
                out[pid].append({"ts": ts, "value": val, "quality_class": 1})
        return out

    async def _fake_last_states(_pids, _before):
        return {}

    async def _fake_anchors(_db, **_kw):
        return {}

    async def _fake_gaps(_db):
        return gaps

    monkeypatch.setattr(lwb, "_resolve_role_segments", _fake_segments)
    monkeypatch.setattr(lwb, "_load_gap_windows", _fake_gaps)
    monkeypatch.setattr(lwb.repo, "read_events", _fake_read_events)
    monkeypatch.setattr(lwb.repo, "read_last_states_before", _fake_last_states)
    monkeypatch.setattr(lwb.meta, "applicable_anchors_batch", _fake_anchors)
    return await lwb.build_logical_wide(None, LOOP, {"PV": pid}, t0, t1, 1)


class TestGapKeepsValues:
    async def test_gap_does_not_change_values(self, monkeypatch) -> None:
        """P0 核心不变量：**有无 gap 登记，值序列必须完全一致**（只多一个 unknown 标记）。

        直接断言"无 None"会被网格边界的既有 None（与本缺陷无关）干扰，
        因此用同事件、同窗口的两次构建做对照 —— 这才是"gap 不得抹值"的严谨表述。
        """
        t0 = datetime(2026, 9, 26, 0, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=599)
        events = [(t0 + timedelta(seconds=i), float(i)) for i in range(0, 600, 1)]
        gaps = [(t0 + timedelta(seconds=100), t0 + timedelta(seconds=200))]

        without_gap = await _build(monkeypatch, events, t0, t1, [])
        with_gap = await _build(monkeypatch, events, t0, t1, gaps)

        assert with_gap.signals["pv"] == without_gap.signals["pv"], (
            "gap 登记改变了值序列（P0 回归：gap 只应标 unknown，不得抹值）"
        )
        assert with_gap.series_context.unknown_reasons.get(sc.UNKNOWN_REASON_GAP, 0) > 0, (
            "缺口段应被标为 unknown/gap"
        )
        assert with_gap.signals["pv"][150] == 150.0

    async def test_gap_window_without_events_still_none(self, monkeypatch) -> None:
        """缺口段且**没有任何事件**可保持 → 值只能是 None，原因仍为 gap。"""
        t0 = datetime(2026, 9, 26, 0, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=599)
        gaps = [(t0, t1)]  # 全窗缺口

        raw = await _build(monkeypatch, [], t0, t1, gaps)

        assert all(v is None for v in raw.signals["pv"]), "确实无数据时应为 None"
        assert raw.series_context.unknown_reasons.get(sc.UNKNOWN_REASON_GAP, 0) > 0

    async def test_no_gap_means_values_and_known(self, monkeypatch) -> None:
        """无缺口登记时行为不变（对照组，防止 P0 改动放宽了 unknown 语义）。"""
        t0 = datetime(2026, 9, 26, 0, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=299)
        events = [(t0 + timedelta(seconds=i), float(i)) for i in range(300)]

        raw = await _build(monkeypatch, events, t0, t1, [])

        # 只断言"没有被标成缺口"（网格边界可能存在与本缺陷无关的既有 None）
        assert raw.series_context.unknown_reasons.get(sc.UNKNOWN_REASON_GAP, 0) == 0
        assert raw.signals["pv"][150] == 150.0


class TestPointTableHasRows:
    async def test_returns_true_when_rows_exist(self, monkeypatch) -> None:
        async def _fake_execute(_sql, **_kw):
            return [{"ts": "2026-09-26T00:00:00.000Z"}]

        from app.core import tdengine

        monkeypatch.setattr(tdengine, "execute_sql", _fake_execute)
        assert (
            await point_table_has_rows(
                datetime(2026, 9, 26, 0, 0, tzinfo=UTC), datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
            )
            is True
        )

    async def test_returns_false_when_empty(self, monkeypatch) -> None:
        async def _fake_execute(_sql, **_kw):
            return []

        from app.core import tdengine

        monkeypatch.setattr(tdengine, "execute_sql", _fake_execute)
        assert (
            await point_table_has_rows(
                datetime(2026, 9, 26, 0, 0, tzinfo=UTC), datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
            )
            is False
        )

    async def test_returns_none_on_probe_failure(self, monkeypatch) -> None:
        async def _boom(_sql, **_kw):
            raise RuntimeError("TD 不可达")

        from app.core import tdengine

        monkeypatch.setattr(tdengine, "execute_sql", _boom)
        assert (
            await point_table_has_rows(
                datetime(2026, 9, 26, 0, 0, tzinfo=UTC), datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
            )
            is None
        )

    async def test_sql_uses_iso_z_literals(self, monkeypatch) -> None:
        """时间字面量必须是 ISO-Z（裸字面量会按会话时区解释，静默偏移 8 小时）。"""
        seen: list[str] = []

        async def _capture(sql, **_kw):
            seen.append(sql)
            return []

        from app.core import tdengine

        monkeypatch.setattr(tdengine, "execute_sql", _capture)
        await point_table_has_rows(
            datetime(2026, 9, 26, 0, 0, tzinfo=UTC), datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
        )
        assert seen, "应执行一次探测 SQL"
        assert "Z'" in seen[0] or 'Z"' in seen[0], seen[0]
        assert "LIMIT 1" in seen[0], "应为存在性探测（LIMIT 1）"
