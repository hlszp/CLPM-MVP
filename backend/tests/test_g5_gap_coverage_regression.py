"""G5 缺口回归：按真实网格计覆盖，UNKNOWN 不计回路有效率。

预期由显式格点/窗口独立给出；仅运行内存接口，禁止对外网络。
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.contracts.data_types import ControlType, LoopPreprocessConfig, RawTimeSeries, TagGroup
from app.contracts.series_context import IntervalRun, RoleCoverage, SeriesContext
from app.services.data_source import logical_wide_builder as lwb
from app.services.preprocessing.pipeline import PreprocessingPipeline

T0 = datetime(2026, 10, 14, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    original_connect = socket.socket.connect

    def blocked_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            pytest.fail(f"G5 regression attempted outbound network: {address}")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_connect)


def test_five_second_coverage_counts_slots_not_seconds():
    """格点 0,5,...,60：前 3 槽 known，后 10 槽 UNKNOWN；分母固定 13。"""
    ctx = SeriesContext(
        layout="point",
        grid_start=_at(0),
        grid_end=_at(60),
        grid_period_s=5,
        expected_slots=13,
        role_coverage={
            "pv": RoleCoverage(
                known=[IntervalRun(_at(0), _at(15))],
                unknown=[IntervalRun(_at(15), _at(65), reason="gap")],
            )
        },
    )

    assert ctx.known_slots("pv") == 3
    assert ctx.unknown_slots("pv") == 10
    assert ctx.source_coverage_ratio("pv") == pytest.approx(3 / 13)


def test_fractional_grid_origin_and_runs_are_discrete_and_deduplicated():
    """独立格点 .25,2.75,5.25,7.75,10.25；known 除 7.75，其余 4 槽。"""
    ctx = SeriesContext(
        layout="point",
        grid_start=_at(0.25),
        grid_end=_at(10.25),
        grid_period_s=2.5,
        expected_slots=5,
        role_coverage={
            "pv": RoleCoverage(
                known=[
                    IntervalRun(_at(10.25), _at(20)),
                    IntervalRun(_at(2.9), _at(7.75)),
                    IntervalRun(_at(-5), _at(0.26)),
                    IntervalRun(_at(2.75), _at(5.25)),
                    IntervalRun(_at(2.75), _at(5.25)),
                ],
                unknown=[IntervalRun(_at(7.75), _at(10.25))],
            )
        },
    )

    assert ctx.known_slots("pv") == 4
    assert ctx.unknown_slots("pv") == 1
    assert ctx.source_coverage_ratio("pv") == pytest.approx(0.8)


def test_role_coverage_default_period_remains_one_second():
    cov = RoleCoverage(known=[IntervalRun(_at(0), _at(10))])
    assert cov.known_slots(_at(0), _at(9)) == 10
    # 起点不是整秒时，格点仍锚定起点：.25,1.25,2.25。
    cov = RoleCoverage(known=[IntervalRun(_at(0.25), _at(2.26))])
    assert cov.known_slots(_at(0.25), _at(2.25)) == 3
    # 尾格点之后的小数游程不增加覆盖，半开终点本身不计入。
    cov = RoleCoverage(known=[IntervalRun(_at(9.1), _at(9.9))])
    assert cov.known_slots(_at(0), _at(9)) == 0


def _stub_builder(monkeypatch, event_seconds, *, gaps=()):
    async def segments(_db, _loop, roles, start, end):
        seg = SimpleNamespace(
            point_id="p1", start=start, end=end + timedelta(seconds=1), initial_check=start
        )
        return {role.upper(): [seg] for role in roles}

    async def events(point_ids, start, end, **_kwargs):
        return {
            pid: [
                {"ts": _at(s), "value": 50.0 + s * 0.1, "quality_class": 1}
                for s in event_seconds
                if start <= _at(s) <= end
            ]
            for pid in point_ids
        }

    monkeypatch.setattr(lwb, "_resolve_role_segments", segments)
    monkeypatch.setattr(lwb, "_load_gap_windows", AsyncMock(return_value=list(gaps)))
    monkeypatch.setattr(
        lwb, "_held_limits_s", AsyncMock(return_value={"fast": 60.0, "slow": 7200.0})
    )
    monkeypatch.setattr(lwb.repo, "read_events", events)
    monkeypatch.setattr(lwb.repo, "read_last_states_before", AsyncMock(return_value={}))
    monkeypatch.setattr(lwb.meta, "applicable_anchors_batch", AsyncMock(return_value={}))


@pytest.mark.parametrize(("period", "end", "points", "run_end"), [(5, 11, 3, 15), (5, 0, 1, 5)])
async def test_builder_records_actual_period_and_complete_tail(
    monkeypatch, period, end, points, run_end
):
    """实际 5s 构建（含单点）明确携带周期，不从时间戳差猜测周期。"""
    _stub_builder(monkeypatch, [0, 5, 10])
    raw = await lwb.build_logical_wide(None, "L-G5", ["pv"], _at(0), _at(end), period)
    ctx = raw.series_context

    assert ctx.grid_period_s == period
    assert ctx.expected_slots == points
    assert ctx.known_slots("pv") == points
    assert ctx.unknown_slots("pv") == 0
    assert ctx.role_coverage["pv"].known[-1].end == _at(run_end)


def _pipeline() -> PreprocessingPipeline:
    return PreprocessingPipeline(
        LoopPreprocessConfig(
            loop_id="L-G5", control_type=ControlType.FLOW, range_min=0.0, range_max=100.0
        )
    )


def _raw_with_coverage(n=20, *, gap_role="pv", gap_start=5, gap_end=20):
    known = []
    if gap_start > 0:
        known.append(IntervalRun(_at(0), _at(gap_start)))
    if gap_end < n:
        known.append(IntervalRun(_at(gap_end), _at(n)))
    cov = RoleCoverage(
        known=known,
        unknown=[IntervalRun(_at(gap_start), _at(gap_end), reason="gap")],
    )
    ctx = SeriesContext(
        layout="point",
        grid_start=_at(0),
        grid_end=_at(n - 1),
        expected_slots=n,
        role_coverage={gap_role: cov},
    )
    signals = {tag: [50.0 + i * 0.1 for i in range(n)] for tag in ("pv", "sp", "op")}
    signals["mode"] = [1] * n
    if gap_role == "pid_p":
        signals["pid_p"] = [1.0] * n
    return RawTimeSeries(
        timestamps=[_at(i).replace(tzinfo=None) for i in range(n)],
        signals=signals,
        quality_codes={"pv_quality": [1] * n},
        series_context=ctx,
    )


@pytest.mark.parametrize("gap_role", ["pv", "sp", "op", "mode"])
def test_pipeline_unknown_with_good_values_does_not_count_as_valid(gap_role):
    """PV/OP 缺口限制硬门禁；SP/MODE 缺口保留供对应指标消费。"""
    raw = _raw_with_coverage(gap_role=gap_role)
    block = _pipeline().process(raw, TagGroup.BASE)

    assert block.loop_valid_rate == pytest.approx(0.25 if gap_role in ("pv", "op") else 1.0)
    assert block.point_count == 20
    assert block.timestamps == raw.timestamps
    assert block.signals["pv"] == pytest.approx(raw.signals["pv"], rel=1e-12, abs=1e-12)
    assert all(block.validity["pv_valid"])
    assert raw.quality_codes["pv_quality"] == [1] * 20
    assert block.quality_summary.good_value_rate == 1.0


def test_pipeline_known_short_held_segments_remain_valid():
    """HELD 已知，无新观测；短于连续段门禁仍有 8/8 个有效点。"""
    raw = _raw_with_coverage(n=8, gap_start=8, gap_end=8)
    raw.series_context.role_coverage["pv"].unknown = []
    block = _pipeline().process(raw, TagGroup.BASE)

    assert block.loop_valid_rate == 1.0
    assert block.consecutive_segments == []
    assert raw.series_context.role_coverage["pv"].observed == []


def test_pipeline_noncore_unknown_keeps_core_valid_rate():
    """PID 覆盖不进入 pv/sp/op/mode 核心有效率，维持既有共享内核语义。"""
    raw = _raw_with_coverage(gap_role="pid_p")
    block = _pipeline().process(raw, TagGroup.BASE)
    assert block.loop_valid_rate == 1.0


def test_pipeline_legacy_keeps_existing_good_point_semantics():
    raw = _raw_with_coverage()
    raw.series_context = None
    block = _pipeline().process(raw, TagGroup.BASE)
    assert block.loop_valid_rate == 1.0


def test_pipeline_core_unknown_union_uses_full_denominator():
    """PV 5..9、OP 10..14 未知：有效为其余 10 槽，而不是逐角色率相乘。"""
    raw = _raw_with_coverage(gap_start=5, gap_end=10)
    raw.series_context.role_coverage["op"] = RoleCoverage(
        known=[IntervalRun(_at(0), _at(10)), IntervalRun(_at(15), _at(20))],
        unknown=[IntervalRun(_at(10), _at(15), reason="held_too_long")],
    )
    block = _pipeline().process(raw, TagGroup.BASE)
    assert block.loop_valid_rate == pytest.approx(0.5)
    assert block.point_count == 20


def test_pipeline_all_unknown_with_good_values_has_zero_valid_rate():
    raw = _raw_with_coverage(gap_start=0, gap_end=20)
    block = _pipeline().process(raw, TagGroup.BASE)
    assert block.loop_valid_rate == 0.0
    assert block.loop_confidence_level == "E"
    assert all(block.validity["pv_valid"])


async def test_coarse_builder_gap_keeps_values_and_counts_coverage_consistently(monkeypatch):
    """5s 网格 21 槽、gap 从 25s 开始：known=5，unknown=16，不抹 Good 值。"""
    _stub_builder(monkeypatch, list(range(0, 101, 5)), gaps=[(_at(25), _at(101))])
    raw = await lwb.build_logical_wide(None, "L-G5", ["pv"], _at(0), _at(100), 5)
    pipeline = PreprocessingPipeline(
        LoopPreprocessConfig(
            loop_id="L-G5", control_type=ControlType.TEMPERATURE, range_min=0, range_max=100
        )
    )
    block = pipeline.process(raw, TagGroup.BASE, (_at(0), _at(100)))

    assert raw.signals["pv"] == [50.0 + i * 0.5 for i in range(21)]
    assert raw.quality_codes["pv_quality"] == [1] * 21
    assert raw.series_context.expected_slots == 21
    assert raw.series_context.known_slots("pv") == 5
    assert raw.series_context.unknown_slots("pv") == 16
    assert raw.series_context.source_coverage_ratio("pv") == pytest.approx(5 / 21)
    assert block.quality_summary.missing_count == 16
    assert block.quality_summary.missing_rate == pytest.approx(0.7619)
    assert block.loop_valid_rate == pytest.approx(5 / 21)
    assert all(block.validity["pv_valid"])
