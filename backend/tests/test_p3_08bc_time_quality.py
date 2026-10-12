"""P3-08b/c 评估数据基础：时间轴半开窗/未知/连续段与质量门禁反例（10 号文 §5）.

覆盖验收子例（04 号文/10§5 层级列）：
- C03-EA1：HF 短事件保真（1s 手动不被 5s BASE 抹掉；Auto=90%）；
- C03-EA2：未知 MODE 不补时——已证实 Auto=40%、范围 40~60%、unknown=20s，
  禁止报确定 60%；
- C03-EA3：坏 PV（保留有限数值）不进 R 分子、仍留全窗分母（R=80%）；
- C04-EA1：半开窗口末槽截断（3+3+3+1=10s，末点未知 1s，不越窗、边界只归
  一个窗）；
- C04-EA2：OP 工程限位 20/80 → Sat=20%、R 相应扣除；epsilon 不扩大；
- C04-EA4：正常 COV 保持（HELD）可用、超期 HELD/硬 gap 未知（六因归因）；
- 跨 gap 禁拼接：管道层段切割（unknown 槽 + 分段边界切断，不删除不拼接）；
- OBSERVED/HELD/UNKNOWN 三态流转（builder 上下文游程）；
- legacy 回退：无窗口/无上下文 bundle → 时间视图 None，计算器原口径不变。

设计依据：D0+P3-08a 契约草案 §2；10 号文 §3.1/§5；O-2 定稿（P3-08a 交接）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.contracts import series_context as sc
from app.contracts.data_types import (
    DataBlock,
    DataLineage,
    MetricDataBundle,
    QualitySummary,
    RawTimeSeries,
    TagGroup,
)
from app.services.metric_calculator.auto_mode import AutoModeRateCalculator
from app.services.metric_calculator.effective_auto import EffectiveAutoRateCalculator
from app.services.metric_calculator.saturation import SaturationRateCalculator
from app.services.metric_calculator.time_axis import (
    SlotState,
    build_metric_time_view,
    to_half_open,
)
from app.services.preprocessing.pipeline import PreprocessingPipeline
from app.services.preprocessing.quality_summary import split_segments_at_boundaries

T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def _ts(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _naive(seconds: float) -> datetime:
    return (_ts(seconds)).replace(tzinfo=None)


def make_block(
    signals: dict[str, list[Any]],
    *,
    timestamps: list[datetime] | None = None,
    validity: dict[str, list[bool]] | None = None,
    query_window: tuple[datetime, datetime] | None = None,
    series_context: sc.SeriesContext | None = None,
    mask_expression: str = "",
) -> MetricDataBundle:
    """构造携带查询窗/上下文的 bundle（08b/c 时间契约测试专用）.

    timestamps 默认 1s 等间隔 naive UTC（与生产 DataBlock 同口径）；
    query_window 为含尾窗口（TimeWindow 语义），半开换算只在 time_axis。
    """
    n = len(next(iter(signals.values()))) if signals else 0
    ts = timestamps if timestamps is not None else [_naive(i) for i in range(n)]
    if validity is None:
        validity = {f"{k}_valid": [True] * len(v) for k, v in signals.items()}
    block = DataBlock(
        data_block_id="db_test_p308bc",
        loop_id="L-TEST",
        tag_group="MODE_HF",
        sampling_freq="1s",
        timestamps=ts,
        signals=signals,
        validity=validity,
        outlier_reasons={},
        quality_summary=QualitySummary(total_count=n, valid_count=n, valid_rate=1.0),
        consecutive_segments=[(0, n - 1)] if n else [],
        point_count=n,
        loop_confidence_level="A",
        series_context=series_context,
        query_window_start=query_window[0] if query_window else None,
        query_window_end=query_window[1] if query_window else None,
    )
    masked: list[int]
    if not mask_expression:
        masked = list(range(n))
    else:
        keys = [t.strip() for t in mask_expression.split("&&")]
        masked = [
            i
            for i in range(n)
            if all(i < len(block.validity.get(k, [])) and block.validity[k][i] for k in keys)
        ]
    return MetricDataBundle(
        metric_code="auto_mode_rate",
        data_block=block,
        mask_expression=mask_expression,
        masked_indices=masked,
        lineage=DataLineage(sampling_freq="1s", tag_group="MODE_HF", valid_rate=1.0),
    )


# ---------------------------------------------------------------------------
# C04-EA1：半开窗口、末槽截断、边界只归一个窗
# ---------------------------------------------------------------------------


class TestC04EA1HalfOpenTailTruncation:
    """[0,10) 采样 0/3/6/9；末点未知：秒数 3+3+3+1=10、未知 1s、不越窗。"""

    def _bundle(self) -> MetricDataBundle:
        # 含尾 TimeWindow(0, 9) ≡ 半开 [0, 10)；C04-EA1 的窗口是半开表述
        timestamps = [_naive(s) for s in (0, 3, 6, 9)]
        return make_block(
            {"mode": [1, 1, 1, None]},  # 末点未知（无值=无依据）
            timestamps=timestamps,
            query_window=(_naive(0), _naive(9)),
        )

    def test_slot_seconds_truncated_to_window_end(self):
        view = build_metric_time_view(self._bundle(), None, ("mode",))
        assert view is not None
        assert view.slot_seconds == [3.0, 3.0, 3.0, 1.0]
        assert view.window_seconds == 10.0
        # 末点未知 → 未知恰 1s（任何比率不得把末点按 3s 越窗）
        assert view.uncovered_window_seconds == 0.0
        acc = view.ratio_accounting(lambda i: True, roles=("mode",))
        assert acc.unknown_seconds == 1.0
        assert acc.numerator_seconds == 9.0
        assert acc.denominator_seconds == 10.0

    def test_auto_ratio_not_overrun_by_tail(self):
        result = AutoModeRateCalculator().calculate(self._bundle())
        # 已证实 9/10=90%；可能区间 90%~100%（未知 1s 不当手动）
        assert result.value == 90.0
        assert result.details["unknownSeconds"] == 1.0
        assert result.details["possibleRange"] == [90.0, 100.0]
        assert result.details["denominatorBasis"] == "FULL_REQUEST_WINDOW"

    def test_boundary_second_belongs_to_exactly_one_window(self):
        """边界秒只归一个窗：A=[0,10) 的末槽止于 10，B=[10,20) 自 10 起。"""
        # 窗 A：[0,10)，采样 0/3/6/9（含尾 0..9）
        block_a = make_block(
            {"mode": [1, 1, 1, 1]},
            timestamps=[_naive(s) for s in (0, 3, 6, 9)],
            query_window=(_naive(0), _naive(9)),
        )
        # 窗 B：[10,20)，采样 10/13/16/19（含尾 10..19）
        block_b = make_block(
            {"mode": [1, 1, 1, 1]},
            timestamps=[_naive(s) for s in (10, 13, 16, 19)],
            query_window=(_naive(10), _naive(19)),
        )
        view_a = build_metric_time_view(block_a, None, ("mode",))
        view_b = build_metric_time_view(block_b, None, ("mode",))
        assert view_a is not None and view_b is not None
        assert view_a.window_end == view_b.window_start  # 无缝不重叠
        assert view_a.window_seconds + view_b.window_seconds == 20.0
        assert sum(view_a.slot_seconds) == 10.0  # 末槽截到 10，不吞 B 的首秒
        assert sum(view_b.slot_seconds) == 10.0

    def test_to_half_open_inclusive_to_exclusive(self):
        s, e = to_half_open(_naive(0), _naive(9))
        assert (e - s).total_seconds() == 10.0
        s2, e2 = to_half_open(_naive(0), _naive(0))
        assert (e2 - s2).total_seconds() == 1.0  # 含尾单秒窗 = 半开 1s


# ---------------------------------------------------------------------------
# C03-EA2：未知 MODE 不补时（Auto 已证实 40%，范围 40~60%）
# ---------------------------------------------------------------------------


class TestC03EA2UnknownNotBackfilled:
    """ "[0,100)：40s 自动 + 20s MODE unknown + 40s 手动。"""

    def _bundle(self) -> MetricDataBundle:
        mode = [1] * 40 + [None] * 20 + [0] * 40
        return make_block(
            {"mode": mode},
            query_window=(_naive(0), _naive(99)),
        )

    def test_auto_proven_ratio_with_possible_range(self):
        result = AutoModeRateCalculator().calculate(self._bundle())
        # 已证实 Auto=40%（40s/100s），不得报确定的 60%
        assert result.value == 40.0
        d = result.details
        assert d["numeratorSeconds"] == 40.0
        assert d["denominatorSeconds"] == 100.0
        assert d["unknownSeconds"] == 20.0
        assert d["knownSeconds"] == 80.0
        assert d["coverageRatio"] == 0.8
        assert d["possibleRange"] == [40.0, 60.0]

    def test_unknown_not_counted_as_manual(self):
        view = build_metric_time_view(self._bundle(), None, ("mode",))
        assert view is not None
        unknown_states = [view.role_states["mode"][i] for i in range(40, 60)]
        assert all(s is SlotState.UNKNOWN for s in unknown_states)
        # 手动段（60..99）与未知段分开：手动 40s 已知，未知 20s 不是手动
        acc = view.ratio_accounting(lambda i: True, roles=("mode",))
        assert acc.known_seconds == 80.0


# ---------------------------------------------------------------------------
# C03-EA3：坏 PV/SP 不进 R 分子、留在全窗分母（R=80%）
# ---------------------------------------------------------------------------


class TestC03EA3BadQualityNotEffective:
    """100s 全自动、OP 合格、PV=SP；前 20s PV Bad（保留有限数值）。"""

    def _bundle(self) -> MetricDataBundle:
        n = 100
        pv_valid = [False] * 20 + [True] * 80
        return make_block(
            {
                "mode": [1] * n,
                "op": [50.0] * n,
                "pv": [50.0] * n,
                "sp": [50.0] * n,
            },
            validity={
                "mode_valid": [True] * n,
                "op_valid": [True] * n,
                "pv_valid": pv_valid,  # 前 20s PV 质量坏但数值保留
                "sp_valid": [True] * n,
            },
            query_window=(_naive(0), _naive(99)),
        )

    def test_r_is_80_with_full_window_denominator(self):
        result = EffectiveAutoRateCalculator().calculate(self._bundle())
        # 坏 PV 有数值不算偏差合格：R=80%，不是 100%
        assert result.value == 80.0
        d = result.details
        assert d["numeratorSeconds"] == 80.0
        assert d["denominatorSeconds"] == 100.0  # 坏 PV 仍留全窗分母
        assert d["unknownSeconds"] == 0.0
        assert d["bad_quality_points"] == 20
        assert d["time_basis"] == "half_open_window"

    def test_stability_only_uses_legal_points(self):
        """A/S 只使用合法点（σ 在合法样本上；带内对账字段落地）。"""
        from app.services.metric_calculator.stability import StabilityRateCalculator

        n = 100
        block = make_block(
            {"pv": [50.0] * n, "sp": [50.0] * n},
            validity={
                "pv_valid": [False] * 20 + [True] * 80,
                "sp_valid": [True] * n,
            },
            query_window=(_naive(0), _naive(99)),
            mask_expression="pv_valid && sp_valid",
        )
        block.metric_code = "stability_rate"
        result = StabilityRateCalculator().calculate(block)
        assert result.value is not None
        # 合法点 80 个（坏 PV 不参与 σ）
        assert result.details["sample_count"] == 80
        assert result.details["band_in_denominator_seconds"] == 80.0
        assert result.details["unknown_seconds"] == 0.0


# ---------------------------------------------------------------------------
# C04-EA2：OP 工程限位 20/80（Sat=20%、R 相应扣除；epsilon 不扩大）
# ---------------------------------------------------------------------------


class TestC04EA2EngineeringLimits:
    """自动 100s 中 20s OP 在 80（工程限位 20/80）。"""

    def _bundle(self, *, op_high: float = 80.0) -> MetricDataBundle:
        n = 100
        return make_block(
            {
                "mode": [1] * n,
                "op": [80.0] * 20 + [50.0] * 80,
                "op_low": [20.0],
                "op_high": [op_high],
            },
            validity={"mode_valid": [True] * n, "op_valid": [True] * n},
            query_window=(_naive(0), _naive(99)),
        )

    def test_saturation_is_20_percent(self):
        result = SaturationRateCalculator().calculate(self._bundle())
        assert result.value == 20.0
        d = result.details
        assert d["sat_high_duration_s"] == 20.0
        assert d["denominatorSeconds"] == 100.0
        assert d["epsilon"] == 0.0  # epsilon 仍按批准配置（默认 0），不扩大

    def test_r_reduced_by_saturation(self):
        n = 100
        bundle = make_block(
            {
                "mode": [1] * n,
                "op": [80.0] * 20 + [50.0] * 80,
                "pv": [50.0] * n,
                "sp": [50.0] * n,
                "op_low": [20.0],
                "op_high": [80.0],
            },
            validity={
                "mode_valid": [True] * n,
                "op_valid": [True] * n,
                "pv_valid": [True] * n,
                "sp_valid": [True] * n,
            },
            query_window=(_naive(0), _naive(99)),
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)
        # 饱和 20s 不计有效：R = 80/100
        assert result.value == 80.0
        assert result.details["numeratorSeconds"] == 80.0


# ---------------------------------------------------------------------------
# C04-EA4：正常 COV 保持（HELD）可用；超期 HELD/硬 gap 未知
# ---------------------------------------------------------------------------


def _coverage_context(
    known_runs: list[tuple[float, float]],
    unknown_runs: list[tuple[float, float, str]],
    observed_runs: list[tuple[float, float]] | None = None,
    *,
    grid_start_s: float = 0.0,
    grid_end_s: float = 99.0,
    boundaries: list[float] | None = None,
    unknown_reasons: dict[str, int] | None = None,
) -> sc.SeriesContext:
    """构造带覆盖游程的 SeriesContext（半开秒偏移 → UTC 游程）。"""
    cov = sc.RoleCoverage(
        known=[sc.IntervalRun(_ts(a), _ts(b)) for a, b in known_runs],
        unknown=[sc.IntervalRun(_ts(a), _ts(b), reason=r) for a, b, r in unknown_runs],
        observed=[sc.IntervalRun(_ts(a), _ts(b)) for a, b in (observed_runs or [])],
    )
    return sc.SeriesContext(
        layout=sc.LAYOUT_POINT,
        grid_start=_ts(grid_start_s),
        grid_end=_ts(grid_end_s),
        grid_period_s=1.0,
        expected_slots=int(grid_end_s - grid_start_s) + 1,
        role_coverage={"pv": cov},
        unknown_reasons=unknown_reasons or {},
        segment_boundaries=[
            sc.SegmentBoundary(at=_ts(b), kind="rebind") for b in (boundaries or [])
        ],
    )


class TestC04EA4HeldUsableOverlongGapUnknown:
    """正常 COV 保持、超期 HELD、硬 gap 三段。"""

    def _bundle(self) -> MetricDataBundle:
        n = 90
        ctx = _coverage_context(
            known_runs=[(0, 30)],  # 正常 COV 保持（合法 HELD）
            unknown_runs=[(30, 60, "held_too_long"), (60, 90, "gap")],
        )
        return make_block(
            {"pv": [50.0] * n, "sp": [50.0] * n},
            series_context=ctx,
            query_window=(_naive(0), _naive(89)),
        )

    def test_three_state_transition(self):
        view = build_metric_time_view(self._bundle(), None, ("pv",))
        assert view is not None
        states = view.role_states["pv"]
        # 段 1（0-29s）：合法保持 → HELD（可用，不当缺失）
        assert all(s is SlotState.HELD for s in states[0:30])
        # 段 2（30-59s）：超期保持 → UNKNOWN(held_too_long)
        assert all(s is SlotState.UNKNOWN for s in states[30:60])
        assert all(r == "held_too_long" for r in view.role_unknown_reasons["pv"][30:60])
        # 段 3（60-89s）：硬缺口 → UNKNOWN(gap)
        assert all(s is SlotState.UNKNOWN for s in states[60:90])
        assert all(r == "gap" for r in view.role_unknown_reasons["pv"][60:90])

    def test_unknown_accounting_does_not_shrink_denominator(self):
        view = build_metric_time_view(self._bundle(), None, ("pv",))
        assert view is not None
        acc = view.ratio_accounting(lambda i: True, roles=("pv",))
        assert acc.denominator_seconds == 90.0  # 完整请求窗口
        assert acc.unknown_seconds == 60.0
        assert acc.known_seconds == 30.0
        breakdown = view.unknown_reason_breakdown()
        assert breakdown.get("held_too_long") == 30.0
        assert breakdown.get("gap") == 30.0


# ---------------------------------------------------------------------------
# OBSERVED/HELD 三态流转（builder 上下文游程生成）
# ---------------------------------------------------------------------------


class TestBuilderContextThreeStates:
    """_build_context：observed（新观测）⊆ known；unknown 游程携带六因。"""

    def test_observed_and_held_runs(self):
        from app.services.data_source.logical_wide_builder import _build_context

        grid = [_ts(i) for i in range(6)]
        # 槽 0=新事件，槽 1-3=保持，槽 4=gap，槽 5=新事件
        slot_status = {
            "pv": [
                (True, None, True),  # OBSERVED
                (True, None, False),  # HELD
                (True, None, False),  # HELD
                (True, None, False),  # HELD
                (False, sc.UNKNOWN_REASON_GAP, False),  # UNKNOWN(gap)
                (True, None, True),  # OBSERVED
            ]
        }
        ctx = _build_context(
            loop_id="L1",
            grid=grid,
            roles=["pv"],
            slot_status=slot_status,
            rebind_boundaries=[],
            segments={"PV": []},
            gap_windows=[(_ts(4), _ts(5))],
        )
        cov = ctx.role_coverage["pv"]
        # observed 游程：[0,1) 与 [5,6)
        assert [(r.start, r.end) for r in cov.observed] == [(_ts(0), _ts(1)), (_ts(5), _ts(6))]
        # known 游程 ≡ OBSERVED∪HELD：observed 段同时计入 known（[0,1) 与
        # [1,4) 相邻、[5,6)），known_slots/source_coverage_ratio 语义不变
        assert [(r.start, r.end) for r in cov.known] == [
            (_ts(0), _ts(1)),
            (_ts(1), _ts(4)),
            (_ts(5), _ts(6)),
        ]
        assert ctx.known_slots("pv") == 5  # 0-3 与 5（覆盖计数合并相邻游程）
        # unknown 游程：[4,5) 且携带六因
        assert len(cov.unknown) == 1
        assert cov.unknown[0].reason == "gap"
        assert ctx.unknown_reasons.get("gap") == 1

    def test_series_context_roundtrip_additive(self):
        """序列化增量兼容：observed/reason 键旧缓存缺失不炸、往返保真。"""
        cov = sc.RoleCoverage(
            known=[sc.IntervalRun(_ts(0), _ts(4))],
            unknown=[sc.IntervalRun(_ts(4), _ts(6), reason="no_initial")],
            observed=[sc.IntervalRun(_ts(0), _ts(1))],
        )
        data = cov.to_dict()
        assert data["unknown"][0]["r"] == "no_initial"
        restored = sc.RoleCoverage.from_dict(data)
        assert restored.unknown[0].reason == "no_initial"
        assert restored.observed == cov.observed
        # 旧缓存（无 observed 键 / 无 r 键）兼容
        legacy = sc.RoleCoverage.from_dict(
            {
                "known": [{"s": _ts(0).isoformat(), "e": _ts(4).isoformat()}],
                "unknown": [{"s": _ts(4).isoformat(), "e": _ts(6).isoformat()}],
            }
        )
        assert legacy.observed == []
        assert legacy.unknown[0].reason is None


# ---------------------------------------------------------------------------
# 跨 gap 禁拼接：管道层段切割强制
# ---------------------------------------------------------------------------


class TestNoStitchPipelineEnforcement:
    """缺口/改绑/解释切换形成动态片段边界——不得删除后拼成连续信号。"""

    def _pipeline(self) -> PreprocessingPipeline:
        from app.contracts.data_types import ControlType, LoopPreprocessConfig

        return PreprocessingPipeline(
            LoopPreprocessConfig(
                loop_id="L1",
                control_type=ControlType.FLOW,
                range_min=0.0,
                range_max=100.0,
            )
        )

    def test_unknown_slot_cuts_segments(self):
        """gap 槽保留 COV 值（valid=True）但覆盖未知 → 段必须切断。

        这正是 P0 修复后的形态：gap 内值/质量保留（valid=True），若无
        覆盖切割，动态算子会把 gap 两侧拼成连续信号（EA-02 漏洞）。
        n=40（两段各 15/20 点 ≥ FLOW min_consecutive_points=30 之外的
        最短段演示：段长取 [0,29]/[35,39]，均 ≥30 需更长窗——取 60 槽）。
        """
        n = 75
        # 槽 30-34 覆盖未知（gap），值全保留、全有效
        ctx = _coverage_context(
            known_runs=[(0, 30), (35, 75)],
            unknown_runs=[(30, 35, "gap")],
            grid_end_s=74.0,
            unknown_reasons={"gap": 5},
        )
        raw = RawTimeSeries(
            timestamps=[_naive(i) for i in range(n)],
            signals={"pv": [50.0] * n, "sp": [50.0] * n},
            quality_codes={},
            series_context=ctx,
        )
        block = self._pipeline().process(raw, TagGroup.BASE, (_naive(0), _naive(74)))
        # 段被切成 [0,29] 与 [35,74]（各 ≥30 点），不跨 gap 拼接
        assert block.consecutive_segments == [(0, 29), (35, 74)]
        # 时间口径摘要落地（质量摘要与比率分母可对账）
        assert block.quality_summary.window_seconds == 75.0
        assert block.quality_summary.unknown_seconds == 5.0
        # 查询窗随块传递（含尾口径；半开换算只在 time_axis）
        assert block.query_window_start == _naive(0)
        assert block.query_window_end == _naive(74)
        # 跨 gap 禁拼接四条落字段（契约 §2.3）全部可查：
        # ① 公共有效连续段（consecutive_segments，已切断不拼接）；
        ctx = block.series_context
        # ② 分段边界目录（segment_boundaries：改绑/解释切换/布局切换）；
        assert list(ctx.segment_boundaries) == []
        # ③ 逐角色未知覆盖游程（role_coverage.unknown，六因随游程）；
        unknown_runs = ctx.role_coverage["pv"].unknown
        assert any(r.reason == "gap" for r in unknown_runs)
        # ④ 网格级未知归因计数（unknown_reasons）——四类字段同块共存可对账。
        assert ctx.unknown_reasons.get("gap") == 5

    def test_segment_boundary_splits_not_deletes(self):
        """改绑边界把段一分为二（两侧保留，不删除也不拼接）。"""
        n = 70
        ctx = _coverage_context(
            known_runs=[(0, 70)],
            unknown_runs=[],
            grid_end_s=69.0,
            boundaries=[35.0],  # t=35 改绑
        )
        raw = RawTimeSeries(
            timestamps=[_naive(i) for i in range(n)],
            signals={"pv": [50.0] * n, "sp": [50.0] * n},
            quality_codes={},
            series_context=ctx,
        )
        block = self._pipeline().process(raw, TagGroup.BASE, (_naive(0), _naive(69)))
        assert block.consecutive_segments == [(0, 34), (35, 69)]

    def test_legacy_without_context_unchanged(self):
        """legacy（无上下文）：段切割保持原 all_valid 口径。"""
        n = 60
        raw = RawTimeSeries(
            timestamps=[_naive(i) for i in range(n)],
            signals={"pv": [50.0] * n, "sp": [50.0] * n},
            quality_codes={},
        )
        block = self._pipeline().process(raw, TagGroup.BASE, (_naive(0), _naive(59)))
        assert block.consecutive_segments == [(0, 59)]
        # legacy 无覆盖上下文：未知无依据可判（未覆盖=0）；窗口秒数有记录
        assert block.quality_summary.window_seconds == 60.0
        assert block.quality_summary.unknown_seconds == 0.0

    def test_split_segments_at_boundaries_unit(self):
        ts = [_naive(i) for i in range(6)]
        segs = [(0, 5)]
        out = split_segments_at_boundaries(segs, ts, [_ts(3)])
        assert out == [(0, 2), (3, 5)]
        # 无边界/边界在段外不改
        assert split_segments_at_boundaries([(0, 5)], ts, []) == [(0, 5)]
        assert split_segments_at_boundaries([(0, 5)], ts, [_ts(10)]) == [(0, 5)]


# ---------------------------------------------------------------------------
# C03-EA1：HF 短事件保真（1s 手动不被粗 BASE 抹掉）
# ---------------------------------------------------------------------------


class TestC03EA1HFShortEventFidelity:
    """温度 BASE=5s；事件 AUTO@0、MANUAL@2、AUTO@3；窗 [0,10)。"""

    def test_hf_1s_grid_preserves_1s_manual_auto_90(self):
        """独立 1s HF 网格保留 1s 手动段：Auto = 9/10 = 90%。"""
        mode = [1, 1, 0, 1, 1, 1, 1, 1, 1, 1]  # AUTO@0 / MANUAL@2 / AUTO@3
        bundle = make_block(
            {"mode": mode},
            query_window=(_naive(0), _naive(9)),
        )
        result = AutoModeRateCalculator().calculate(bundle)
        assert result.value == 90.0
        assert result.details["numeratorSeconds"] == 9.0

    def test_coarse_base_5s_would_lose_event(self):
        """反证：5s 粗网格（0/5 两槽全 AUTO）看不到手动段 → Auto=100%（错）。

        这就是 EA-01 缺陷形态——禁止把粗 BASE 标成 HF 使用（data_planner
        已禁止派生，独立 HF 读取的接线见 test_data_planner）。
        """
        timestamps = [_naive(0), _naive(5)]
        bundle = make_block(
            {"mode": [1, 1]},
            timestamps=timestamps,
            query_window=(_naive(0), _naive(9)),
        )
        result = AutoModeRateCalculator().calculate(bundle)
        assert result.value == 100.0  # 粗网格伪结果（保真判据的反例）


# ---------------------------------------------------------------------------
# legacy 回退：无窗口/无上下文 → 视图 None，计算器原口径不变
# ---------------------------------------------------------------------------


class TestLegacyFallback:
    def test_view_none_without_window_or_context(self):
        bundle = make_block({"mode": [1] * 10})  # 无 query_window、无 context
        assert build_metric_time_view(bundle, None, ("mode",)) is None

    def test_calculators_keep_legacy_basis(self):
        bundle = make_bundle_like_legacy()
        result = AutoModeRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["time_basis"] == "legacy_masked_extent"
        r = EffectiveAutoRateCalculator().calculate(legacy_full_effective_bundle())
        assert r.value == 100.0
        assert r.details["time_basis"] == "legacy_masked_extent"


def make_bundle_like_legacy() -> MetricDataBundle:
    n = 10
    return make_block({"mode": [1] * n})


def legacy_full_effective_bundle() -> MetricDataBundle:
    n = 10
    return make_block(
        {"mode": [1] * n, "op": [50.0] * n, "pv": [50.0] * n, "sp": [50.0] * n},
    )


# ---------------------------------------------------------------------------
# L1/L2 缓存往返：查询窗与时间口径摘要不丢
# ---------------------------------------------------------------------------


class TestCacheRoundtrip:
    def test_l1_roundtrip_window_fields(self):
        from app.services.cache.l1_datablock import _data_block_from_dict, _data_block_to_dict

        n = 5
        block = DataBlock(
            data_block_id="db_rt",
            loop_id="L1",
            tag_group="BASE",
            sampling_freq="1s",
            timestamps=[_naive(i) for i in range(n)],
            signals={"pv": [50.0] * n},
            validity={"pv_valid": [True] * n},
            outlier_reasons={},
            quality_summary=QualitySummary(
                total_count=n,
                valid_count=n,
                valid_rate=1.0,
                window_seconds=5.0,
                unknown_seconds=1.0,
            ),
            consecutive_segments=[(0, n - 1)],
            point_count=n,
            query_window_start=_naive(0),
            query_window_end=_naive(4),
        )
        data = _data_block_to_dict(block)
        restored = _data_block_from_dict(data)
        assert restored.query_window_start == _naive(0)
        assert restored.query_window_end == _naive(4)
        assert restored.quality_summary.window_seconds == 5.0
        assert restored.quality_summary.unknown_seconds == 1.0
        # 旧缓存（无新键）兼容
        for key in ("query_window_start", "query_window_end"):
            data.pop(key)
        data["quality_summary"].pop("window_seconds")
        legacy = _data_block_from_dict(data)
        assert legacy.query_window_start is None
        assert legacy.quality_summary.window_seconds is None
