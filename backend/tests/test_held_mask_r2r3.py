"""R2（HELD 超阈值计 unknown）+ R3（冻结只看 OBSERVED）回归用例（2026-09-25）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from app.contracts import series_context as sc
from app.services.data_source import logical_wide_builder as lwb
from app.services.diagnosis_operators import OperatorInput
from app.services.diagnosis_operators.sensor import detect_sensor_fault

LOOP = "loop-test"


# --------------------------------------------------------------------------
# R2：构造"事件间隔超过阈值"与"正常连续事件"两种序列，验证槽位状态
# --------------------------------------------------------------------------
class _Seg:
    """最小 segment 替身（_active_segment 需要 start/end/point_id）。"""

    def __init__(self, pid: str, start: datetime, end: datetime) -> None:
        self.point_id = pid
        self.start = start
        self.end = end
        # initial_check 非 None 才会把该点纳入 all_points（否则不读事件）
        self.initial_check = start
        self.binding_version = 1


async def _build(monkeypatch, events: list[tuple[datetime, float]], t0: datetime, t1: datetime):
    pid = "point-1"

    async def _fake_segments(_db, _loop_id, roles, grid_start, grid_end):
        # 键必须大写：builder 内部按 role.upper() 取段
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

    async def _fake_gap_windows(_db):
        # 2026-09-26：1a 会把**真实缺口**登记到 PG（history_coverage_segment.status='gap'），
        # 若本用例窗口恰好被某条真实 gap 覆盖，槽位会先被判 gap（更准确的原因），
        # held_too_long 归零 → 用例变成"依赖库里有没有真实缺口"的 flaky 测试。
        # 本用例只验证 held_too_long 这一语义，故把缺口来源隔离为空。
        # （"PG 登记的缺口优先于 held_too_long" 的行为已由父会话实测确认：
        #   被真实 gap 覆盖的窗口 unknown_reasons 为 {'gap': N}，见 2026-09-26 记录；
        #   该优先级如需回归固化，另立用例补，勿在本用例里依赖 DB 状态。）
        return []

    monkeypatch.setattr(lwb, "_resolve_role_segments", _fake_segments)
    monkeypatch.setattr(lwb, "_load_gap_windows", _fake_gap_windows)
    monkeypatch.setattr(lwb.repo, "read_events", _fake_read_events)
    monkeypatch.setattr(lwb.repo, "read_last_states_before", _fake_last_states)
    monkeypatch.setattr(lwb.meta, "applicable_anchors_batch", _fake_anchors)
    return await lwb.build_logical_wide(None, LOOP, {"PV": pid}, t0, t1, 1)


class TestR2HeldTooLong:
    async def test_held_beyond_threshold_marked_unknown(self, monkeypatch) -> None:
        """事件间隔 600s（> max(3×1s, 60s)）→ 保持段必须暴露为 unknown/held_too_long。"""
        t0 = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=1199)
        events = [(t0, 1.0), (t0 + timedelta(seconds=600), 2.0)]
        raw = await _build(monkeypatch, events, t0, t1)
        ctx = raw.series_context
        pv_unknown = ctx.role_coverage["pv"].unknown
        assert pv_unknown, "超阈值保持段未暴露为 unknown"
        assert ctx.unknown_reasons.get(sc.UNKNOWN_REASON_HELD_TOO_LONG, 0) > 0
        # 值不受影响：保持段仍带旧值（显示不变）
        vals = [v for v in raw.signals["pv"] if v is not None]
        assert 1.0 in vals and 2.0 in vals

    async def test_dense_events_not_marked(self, monkeypatch) -> None:
        """每秒一个事件（远小于阈值）→ 不得误标 unknown。"""
        t0 = datetime(2026, 9, 25, 0, 0, 0, tzinfo=UTC)
        t1 = t0 + timedelta(seconds=599)
        events = [(t0 + timedelta(seconds=i), float(i)) for i in range(600)]
        raw = await _build(monkeypatch, events, t0, t1)
        # 只断言"不被 R2 误标"：首事件前可能仍有 no_initial 之类的既有 unknown，
        # 因此这里查 held_too_long 归因，而不是要求 unknown 为空。
        assert raw.series_context.unknown_reasons.get(sc.UNKNOWN_REASON_HELD_TOO_LONG, 0) == 0


# --------------------------------------------------------------------------
# R3：冻结判据只看 OBSERVED
# --------------------------------------------------------------------------
def _input(pv: np.ndarray, mask: np.ndarray | None) -> OperatorInput:
    n = len(pv)
    meta: dict = {"sample_interval": 1.0, "total_points": n, "point_axis": True}
    if mask is not None:
        meta["observed_mask"] = mask
    return OperatorInput(
        loop_id=LOOP,
        signals={"pv": pv, "sp": np.full(n, 50.0)},
        timestamps=np.arange(n, dtype=float),
        meta=meta,
        kpi_context={},
    )


class TestR3FrozenUsesObservedOnly:
    def test_held_plateau_not_frozen(self) -> None:
        """HELD 平台（掩码标为非 OBSERVED）→ 不再判 frozen。"""
        n = 7200
        pv = np.concatenate(
            [
                np.linspace(49.0, 51.0, 1825),
                np.full(2375, 50.1002),
                np.linspace(49.0, 51.0, n - 1825 - 2375),
            ]
        )
        mask = np.ones(n, dtype=bool)
        mask[1825 : 1825 + 2375] = False  # 缺口段 → HELD
        res = detect_sensor_fault(_input(pv, mask), {})
        assert res.executed is True
        assert res.error is None
        assert res.detected is False, "缺口 HELD 段不得判冻结"
        assert res.features["frozen_segment_ratio"] < 0.2

    def test_observed_constant_still_frozen(self) -> None:
        """OBSERVED 真实恒定段（全槽 observed）→ 仍能判 frozen（不把真故障一起修掉）。"""
        n = 7200
        pv = np.concatenate(
            [
                np.linspace(49.0, 51.0, 1825),
                np.full(2375, 50.1002),
                np.linspace(49.0, 51.0, n - 1825 - 2375),
            ]
        )
        res = detect_sensor_fault(_input(pv, np.ones(n, dtype=bool)), {})
        assert res.detected is True
        assert res.features["frozen_segment_ratio"] > 0.2
