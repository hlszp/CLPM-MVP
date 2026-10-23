"""七角色质量码全链透传 + 锚点 coverage_until 有效期（builder→provider→pipeline）。

契约（2026-10-23 用户口径）：
- 全测点按 COV 落库；质量与值同为状态，按 ZOH 因果保持，坏质量不得被
  前向洗白，也不得丢弃——``{role}_quality`` 与 ``{role}`` 信号同网格输出。
- ``PointStateAnchor.coverage_until`` 是半开有效终点（模型 §point_state_anchor）：
  锚点状态只保持到该时刻；之后无依据（未知），直到本点新事件提供恢复依据，
  不得回退到更旧状态越过到期边界。
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.contracts.data_types import ControlType, LoopPreprocessConfig, RawTimeSeries, TagGroup
from app.services.data_source import logical_wide_builder as lwb
from app.services.data_source.tdengine_provider import _point_or_mixed_query
from app.services.preprocessing.pipeline import PreprocessingPipeline

T0 = datetime(2026, 10, 23, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    original = socket.socket.connect

    def blocked(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            pytest.fail(f"unit test attempted network access: {address}")
        return original(sock, address)

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)


def _pipeline() -> PreprocessingPipeline:
    return PreprocessingPipeline(
        LoopPreprocessConfig(
            loop_id="L-QA", control_type=ControlType.FLOW, range_min=0, range_max=100
        )
    )


async def _build(
    monkeypatch,
    *,
    roles=("pv", "op"),
    end=9,
    events=None,
    anchors=None,
):
    """构造 builder 输入：events={pid: [(second, value, quality_class)]}，
    anchors={pid: SimpleNamespace(anchor_ts/value/quality_class/coverage_until)}。"""

    async def segments(_db, _loop, seg_roles, start, _end):
        return {
            role.upper(): [
                SimpleNamespace(point_id=role, start=_at(-86400), end=None, initial_check=start)
            ]
            for role in seg_roles
        }

    ev_map = dict.fromkeys(roles, [])
    for pid, evs in (events or {}).items():
        ev_map[pid] = [{"ts": _at(s), "value": v, "quality_class": q} for s, v, q in evs]

    async def read_events(point_ids, start, stop, **_kwargs):
        return {
            pid: [ev for ev in ev_map.get(pid, []) if start <= ev["ts"] <= stop]
            for pid in point_ids
        }

    monkeypatch.setattr(lwb, "_resolve_role_segments", segments)
    monkeypatch.setattr(lwb, "_load_gap_windows", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        lwb, "_held_limits_s", AsyncMock(return_value={"fast": 60.0, "slow": 7200.0})
    )
    monkeypatch.setattr(lwb.repo, "read_events", read_events)
    monkeypatch.setattr(lwb.repo, "read_last_states_before", AsyncMock(return_value={}))
    monkeypatch.setattr(lwb.meta, "applicable_anchors_batch", AsyncMock(return_value=anchors or {}))
    return await lwb.build_logical_wide(None, "L-QA", list(roles), _at(0), _at(end), 1)


# ---------------------------------------------------------------------------
# A. 七角色质量码全链透传
# ---------------------------------------------------------------------------


async def test_builder_emits_quality_codes_for_all_requested_roles(monkeypatch):
    """请求的角色都要有 ``{role}_quality``，与 legacy ``pv_quality`` 同键式。"""
    raw = await _build(
        monkeypatch,
        roles=("pv", "op", "sp", "mode"),
        events={
            "pv": [(s, 50.0, 1) for s in range(10)],
            "op": [(0, 40.0, 1)],
            "sp": [(0, 50.0, 0)],
            "mode": [(0, 1.0, 1)],
        },
    )

    assert set(raw.quality_codes) == {"pv_quality", "op_quality", "sp_quality", "mode_quality"}
    assert raw.quality_codes["sp_quality"] == [0] * 10  # Bad 随状态保持
    assert raw.quality_codes["op_quality"] == [1] * 10
    assert raw.quality_codes["mode_quality"] == [1] * 10


async def test_held_bad_quality_of_core_role_drops_loop_confidence(monkeypatch):
    """OP 唯一事件为 Bad：值保持可用但质量 Bad → op_valid 全 False → conf=E。"""
    raw = await _build(
        monkeypatch,
        events={
            "pv": [(s, 50.0, 1) for s in range(10)],
            "op": [(0, 40.0, 0)],
        },
    )
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))

    assert raw.signals["op"] == [40.0] * 10
    assert block.validity["op_valid"] == [False] * 10
    assert block.loop_valid_rate == 0.0
    assert block.loop_confidence_level == "E"


async def test_role_quality_flows_through_provider_passthrough(monkeypatch):
    """provider 合并层透传全部质量键（不只 pv_quality）。"""

    async def fake_build(_db, _loop, roles, start, end, _interval, **_kw):
        n = 10
        return RawTimeSeries(
            timestamps=[_at(i) for i in range(n)],
            signals={r: [50.0] * n for r in roles},
            quality_codes={f"{r}_quality": [1] * n if r != "op" else [0] * n for r in roles},
        )

    monkeypatch.setattr(lwb, "build_logical_wide", fake_build)
    raw = await _point_or_mixed_query(
        None,
        "L-QA",
        ["pv", "op"],
        _at(0),
        _at(9),
        1,
        [SimpleNamespace(start=_at(0), end=_at(9))],
    )

    assert set(raw.quality_codes) == {"pv_quality", "op_quality"}
    assert raw.quality_codes["op_quality"] == [0] * 10


async def test_quality_pseudo_role_request_still_returns_pv_quality_only(monkeypatch):
    """QUALITY_HF 伪角色请求（roles=["pv_quality"]）不新增角色质量键。"""
    raw = await _build(
        monkeypatch,
        roles=("pv_quality",),
        events={"pv": [(s, 50.0, 1) for s in range(10)]},
    )

    assert set(raw.signals) == {"pv_quality"}
    assert set(raw.quality_codes) == {"pv_quality"}
    assert raw.quality_codes["pv_quality"] == [1] * 10


# ---------------------------------------------------------------------------
# B. 锚点 coverage_until 有效期
# ---------------------------------------------------------------------------


def _anchor(seconds, coverage_until=None, *, value=50.0, quality=1):
    return SimpleNamespace(
        anchor_ts=_at(seconds),
        value=value,
        quality_class=quality,
        quality_raw=None,
        coverage_until=(_at(coverage_until) if coverage_until is not None else None),
    )


async def test_anchor_coverage_until_bounds_forward_hold(monkeypatch):
    """锚点 -10s、coverage_until=+3s：仅 0/1/2 秒已知，之后无依据。"""
    raw = await _build(
        monkeypatch,
        events={"pv": [(s, 50.0, 1) for s in range(10)]},
        anchors={"op": _anchor(-10, coverage_until=3)},
    )

    assert raw.signals["op"][:3] == [50.0] * 3
    assert raw.signals["op"][3:] == [None] * 7
    assert raw.series_context.known_slots("op") == 3
    assert raw.series_context.unknown_slots("op") == 7


async def test_anchor_without_coverage_until_holds_indefinitely(monkeypatch):
    """无 coverage_until 的锚点保持既有语义：全窗保持。"""
    raw = await _build(
        monkeypatch,
        events={"pv": [(s, 50.0, 1) for s in range(10)]},
        anchors={"op": _anchor(-10)},
    )

    assert raw.signals["op"] == [50.0] * 10
    assert raw.series_context.known_slots("op") == 10


async def test_own_event_after_anchor_expiry_restores_known(monkeypatch):
    """锚点 3s 到期、OP 6s 新事件：3~5s 未知，6s 起恢复已知。"""
    raw = await _build(
        monkeypatch,
        events={
            "pv": [(s, 50.0, 1) for s in range(10)],
            "op": [(6, 42.0, 1)],
        },
        anchors={"op": _anchor(-10, coverage_until=3)},
    )

    assert raw.signals["op"] == [50.0] * 3 + [None] * 3 + [42.0] * 4
    assert raw.series_context.known_slots("op") == 7
    assert raw.series_context.unknown_slots("op") == 3


async def test_expired_anchor_does_not_fall_back_to_older_event(monkeypatch):
    """锚点到期后不得回退到更旧的窗前事件（越不过到期边界）。"""

    async def read_last_states_before(point_ids, _before):
        return {pid: {"ts": _at(-50), "value": 111.0, "quality_class": 1} for pid in point_ids}

    monkeypatch.setattr(lwb.repo, "read_last_states_before", read_last_states_before)
    raw = await _build(
        monkeypatch,
        events={"pv": [(s, 50.0, 1) for s in range(10)]},
        anchors={"op": _anchor(-10, coverage_until=3, value=50.0)},
    )

    assert raw.signals["op"] == [50.0] * 3 + [None] * 7
    assert raw.series_context.known_slots("op") == 3


# ---------------------------------------------------------------------------
# C/D. UNKNOWN 槽不当观测 + QUALITY_HF 伪角色块可信度
# ---------------------------------------------------------------------------


def _ctx(pv_unknown=None, sp_unknown=None):
    """构造 point 上下文（默认全已知；unknown=IntervalRun 列表叠加）。"""
    from app.contracts.series_context import IntervalRun, RoleCoverage, SeriesContext

    pv = RoleCoverage(known=[IntervalRun(_at(0), _at(10))])
    sp = RoleCoverage(known=[IntervalRun(_at(0), _at(10))])
    for run in pv_unknown or []:
        pv.unknown.append(run)
    for run in sp_unknown or []:
        sp.unknown.append(run)
    return SeriesContext(
        layout="point",
        grid_start=_at(0),
        grid_end=_at(9),
        grid_period_s=1.0,
        expected_slots=10,
        role_coverage={"pv": pv, "sp": sp},
    )


def _raw(
    signals,
    quality_codes=None,
    ctx=None,
):
    return RawTimeSeries(
        timestamps=[_at(i) for i in range(10)],
        signals=signals,
        quality_codes=quality_codes or {},
        series_context=ctx,
    )


def test_quality_hf_pseudo_block_confidence_reflects_quality_codes():
    """QUALITY_HF 伪角色块：conf 由 PV 质量码+覆盖给出（全 Good→A），不再恒 E。"""
    from app.contracts.series_context import IntervalRun, RoleCoverage, SeriesContext

    cov = RoleCoverage(known=[IntervalRun(_at(0), _at(10))])
    ctx = SeriesContext(
        layout="point",
        grid_start=_at(0),
        grid_end=_at(9),
        expected_slots=10,
        role_coverage={"pv": cov},
    )
    raw = _raw({"pv_quality": [1] * 10}, {"pv_quality": [1] * 10}, ctx)
    block = _pipeline().process(raw, TagGroup.QUALITY_HF, (_at(0), _at(9)))

    assert block.quality_summary.good_value_rate == 1.0
    assert block.loop_confidence_level == "A"

    # 一半 Bad：conf=0.5→D（值与等级同向，不再值 100 级 E）
    raw_half = _raw({"pv_quality": [1] * 10}, {"pv_quality": [1] * 5 + [0] * 5}, ctx)
    block_half = _pipeline().process(raw_half, TagGroup.QUALITY_HF, (_at(0), _at(9)))
    assert block_half.quality_summary.good_value_rate == 0.5
    assert block_half.loop_confidence_level == "D"


def test_good_value_rate_excludes_unknown_slots():
    """gap 槽保持的旧质量码不进好值率分子/分母；全窗未知→不可判定。"""
    from app.contracts.series_context import IntervalRun

    gap = [IntervalRun(_at(5), _at(10), reason="gap")]
    ctx = _ctx(pv_unknown=gap)
    raw = _raw({"pv": [50.0] * 10}, {"pv_quality": [1] * 5 + [0] * 5}, ctx)
    block = _pipeline().process(raw, TagGroup.QUALITY_HF, (_at(0), _at(9)))

    # 已知 0~4s 全 Good → 好值率 1.0（legacy 全槽口径会得 0.5）
    assert block.quality_summary.good_value_rate == 1.0

    all_gap = _ctx(pv_unknown=[IntervalRun(_at(0), _at(10), reason="gap")])
    raw_all = _raw({"pv": [50.0] * 10}, {"pv_quality": [1] * 10}, all_gap)
    block_all = _pipeline().process(raw_all, TagGroup.QUALITY_HF, (_at(0), _at(9)))
    assert block_all.quality_summary.good_value_rate is None


def _bundle(block, metric_code, mask="pv_valid && sp_valid"):
    from app.contracts.data_types import DataLineage, MetricDataBundle
    from app.services.preprocessing.validity_mask import apply_mask

    indices = apply_mask(block, mask)
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=block,
        mask_expression=mask,
        masked_indices=indices,
        lineage=DataLineage(),
    )


def test_accuracy_excludes_unknown_slots():
    """SP 侧真实 gap 槽的保持陈值不进准确率均值（未知不进分子不缩分母）。"""
    from app.contracts.series_context import IntervalRun
    from app.services.metric_calculator import get_calculator

    gap = [IntervalRun(_at(5), _at(10), reason="gap")]
    # 0~4s PV=SP（零偏差）；5~9s 处于 gap 的保持值偏差巨大
    raw = _raw(
        {"pv": [50.0] * 5 + [90.0] * 5, "sp": [50.0] * 10},
        {"pv_quality": [1] * 10, "sp_quality": [1] * 10},
        _ctx(sp_unknown=gap),
    )
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))
    result = get_calculator("accuracy_rate").calculate(_bundle(block, "accuracy_rate"))

    assert result.value == 100.0
    assert result.details["sample_count"] == 5


def test_stability_excludes_unknown_slots():
    """PV 侧真实 gap 槽的保持陈值不进 σ（否则假波动拉低稳定率）。"""
    from app.contracts.series_context import IntervalRun
    from app.services.metric_calculator import get_calculator

    gap = [IntervalRun(_at(5), _at(10), reason="gap")]
    # 0~4s 平稳（偏差 0）；5~9s gap 内保持大偏差陈值
    raw = _raw(
        {"pv": [50.0] * 5 + [95.0] * 5, "sp": [50.0] * 10},
        {"pv_quality": [1] * 10, "sp_quality": [1] * 10},
        _ctx(pv_unknown=gap),
    )
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))
    result = get_calculator("stability_rate").calculate(_bundle(block, "stability_rate"))

    # 已知段 σ=0 且零偏差 → 满分（若计入 gap 陈值 σ≈25 → 大幅扣分）
    assert result.value == 100.0
    assert result.details["std_error"] == 0.0


def test_legacy_blocks_without_context_keep_full_mask_semantics():
    """legacy（无上下文）块不筛选：masked 点照旧全量参与（行为零回归）。"""
    from app.services.metric_calculator import get_calculator

    raw = _raw(
        {"pv": [50.0] * 5 + [90.0] * 5, "sp": [50.0] * 10},
        {"pv_quality": [1] * 10},
        ctx=None,
    )
    block = _pipeline().process(raw, TagGroup.BASE, (_at(0), _at(9)))
    result = get_calculator("accuracy_rate").calculate(_bundle(block, "accuracy_rate"))

    assert result.details["sample_count"] == 10
