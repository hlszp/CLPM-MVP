"""8 类异常值检测边界值套件（任务 G4-②）.

对 app.services.preprocessing.outlier_detection 的 8 类检测器
（nan / out_of_range / frozen / jump / spike / ts_anomaly / qc_bad / hf_noise）
逐类构造阈值 ±ε 触发/不触发用例，阈值期望值均按
thresholds.ControlTypeThreshold 默认值手算核实（非实现输出反推）。

重点固化（Phase 1 / 量纲修复回归防护）：
    1. 平稳良好回路不被 FROZEN 误伤 —— FROZEN 属 MARK_ONLY，
       should_invalidate=False，valid_rate 不被拖零；
    2. JUMP/SPIKE 在 0~800 工程量程（未归一化）下能触发
       —— 量程必须与数据量纲一致（eff_min/eff_max 修复）；
    3. 仪表故障复合判据：平稳回路 FROZEN 标记不计故障
       （持续 ≥5min 且同期 OP 有变化才计）。

FLOW 默认阈值（thresholds._THRESHOLDS[ControlType.FLOW]）：
    base_sampling_freq=1s, frozen_window_points=5, frozen_std_pct=0.001,
    jump_threshold_pct=0.8, spike_threshold_pct=0.5, noise_cutoff_hz=0.2,
    frozen_fault_min_minutes=5.0

量程 0~800 下的手算阈值：
    FROZEN std 阈值 = 0.001 × 800 = 0.8
    JUMP 阈值       = 0.8   × 800 = 640
    SPIKE 阈值      = 0.5   × 800 = 400
    TS_ANOMALY 间隔阈值 = 2 × 1s = 2s
    仪表故障复合判据 OP std ε = 0.001 × 100 = 0.1（归一化量纲），持续 ≥ 300s
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest

from app.contracts.data_types import ControlType, OutlierReason
from app.services.metric_calculator.instrument_fault import InstrumentFaultRateCalculator
from app.services.preprocessing.outlier_detection import (
    OutlierDetector,
    _detect_frozen_indices,
    detect_hf_noise,
    detect_jump,
    detect_nan,
    detect_out_of_range,
    detect_qc_bad,
    detect_spike,
    detect_ts_anomaly,
)
from app.services.preprocessing.thresholds import get_threshold

from .conftest import build_bundle, make_ts

FLOW = get_threshold(ControlType.FLOW)
RANGE_MIN, RANGE_MAX = 0.0, 800.0

# 手算阈值（见模块 docstring）
FROZEN_STD_THR = 0.001 * 800.0  # 0.8
JUMP_THR = 0.8 * 800.0  # 640
SPIKE_THR = 0.5 * 800.0  # 400


def _reasons_of(results: list[tuple[int, OutlierReason]]) -> set[int]:
    return {i for i, _ in results}


# ---------------------------------------------------------------------------
# 检测器 1：detect_nan（NaN/Inf/NULL）
# ---------------------------------------------------------------------------


class TestDetectNan:
    """detect_nan：NaN/Inf/None/不可转换值 → NAN；正常数值不触发."""

    def test_nan_inf_none_unconvertible_flagged(self) -> None:
        values = [1.0, float("nan"), float("inf"), float("-inf"), None, "abc", 50.0]
        assert _reasons_of(detect_nan(values)) == {1, 2, 3, 4, 5}

    def test_normal_values_not_flagged(self) -> None:
        assert detect_nan([0.0, -273.15, 1e6, 800.0, "12.5"]) == []

    def test_empty(self) -> None:
        assert detect_nan([]) == []


# ---------------------------------------------------------------------------
# 检测器 2：detect_out_of_range（超量程）
# ---------------------------------------------------------------------------


class TestDetectOutOfRange:
    """detect_out_of_range：边界含端点（f < min or f > max 才触发），NaN 跳过."""

    def test_boundary_endpoints_not_flagged(self) -> None:
        # 恰好等于量程上下限：不触发（严格不等式）
        assert detect_out_of_range([RANGE_MIN, RANGE_MAX], RANGE_MIN, RANGE_MAX) == []

    def test_just_outside_flagged(self) -> None:
        results = detect_out_of_range([-1e-9, 800.0 + 1e-9], RANGE_MIN, RANGE_MAX)
        assert _reasons_of(results) == {0, 1}
        assert all(r is OutlierReason.OUT_OF_RANGE for _, r in results)

    def test_nan_and_unconvertible_skipped(self) -> None:
        # NaN 由 detect_nan 处理；不可转换值容错跳过
        assert detect_out_of_range([float("nan"), "abc", 400.0], RANGE_MIN, RANGE_MAX) == []


# ---------------------------------------------------------------------------
# 检测器 3：detect_frozen（冻结值，MARK_ONLY）
# ---------------------------------------------------------------------------


class TestDetectFrozen:
    """detect_frozen_raw/_detect_frozen_indices：窗口 std 阈值 ±ε 边界."""

    def test_constant_signal_all_flagged(self) -> None:
        # 恒定信号 std=0 < 0.8 → 全部点标记 FROZEN
        results = _detect_frozen_indices([500.0] * 10, FLOW.frozen_window_points, FROZEN_STD_THR)
        assert results == list(range(10))

    def test_std_just_below_threshold_flagged(self) -> None:
        # 交替 [a, a+δ]：5 点窗口（3+2 或 2+3 分布）总体 std = δ×√0.24
        # δ=1.63 → std = 1.63×0.489898 = 0.79853 < 0.8 → 触发
        values = [500.0 + (1.63 if i % 2 else 0.0) for i in range(10)]
        results = _detect_frozen_indices(values, FLOW.frozen_window_points, FROZEN_STD_THR)
        assert results == list(range(10))

    def test_std_just_above_threshold_not_flagged(self) -> None:
        # δ=1.64 → std = 1.64×0.489898 = 0.80343 > 0.8 → 不触发（严格 <）
        values = [500.0 + (1.64 if i % 2 else 0.0) for i in range(10)]
        assert _detect_frozen_indices(values, FLOW.frozen_window_points, FROZEN_STD_THR) == []

    def test_frozen_is_mark_only(self) -> None:
        """固化①：FROZEN 仅标记，不置 valid=False（算法说明 §3.4.3 + Phase 1 整改）."""
        assert OutlierDetector.should_invalidate([OutlierReason.FROZEN]) is False

    def test_steady_good_loop_valid_rate_not_dragged(self) -> None:
        """固化①：平稳良好回路（恒定 PV，0~800 量程）FROZEN 标记大面积命中，
        但全部点 should_invalidate=False → valid_rate 保持 1.0 不被拖零."""
        detector = OutlierDetector(FLOW)
        n = 100
        reasons_map = detector.detect_all("pv", [500.0] * n, make_ts(n), RANGE_MIN, RANGE_MAX)
        # 检测器仍正常标记（MARK_ONLY 不是禁用检测）
        assert len(reasons_map) == n
        assert all(OutlierReason.FROZEN in r for r in reasons_map.values())
        # 关键：没有任何点因 FROZEN 被置 invalid
        invalidated = [i for i, r in reasons_map.items() if OutlierDetector.should_invalidate(r)]
        assert invalidated == []


class TestInstrumentFaultFrozenCompound:
    """固化③：仪表故障复合判据（instrument_fault_rate）.

    FROZEN 连续段持续 ≥ frozen_fault_min_minutes(5min) 且同期 OP 有变化
    （std > 0.1，归一化量纲）才计仪表故障；平稳回路不计。
    """

    @staticmethod
    def _calc_fault_rate(n: int, op: list[float]) -> float | None:
        reasons = {"pv": [[OutlierReason.FROZEN.value]] * n}
        bundle = build_bundle(
            {"pv": [500.0] * n, "op": op},
            metric_code="instrument_fault_rate",
            sampling_freq="1s",
            outlier_reasons=reasons,
        )
        return InstrumentFaultRateCalculator().calculate(bundle).value

    def test_steady_loop_constant_op_not_fault(self) -> None:
        """平稳回路：PV 冻结 400s 但 OP 也不动（std=0 < 0.1）→ 不计故障."""
        assert self._calc_fault_rate(400, [50.0] * 400) == 0.0

    def test_frozen_with_op_moving_is_fault(self) -> None:
        """真仪表卡死特征：PV 冻结 400s 且 OP 交替 50/51（std=0.5 > 0.1）→ 计故障."""
        op = [50.0 + (1.0 if i % 2 else 0.0) for i in range(400)]
        assert self._calc_fault_rate(400, op) == 100.0

    def test_duration_exactly_at_min_confirmed(self) -> None:
        """持续时长边界 +ε：300 点 1s → 段时长 299+1=300s ≥ 300s → 计故障."""
        op = [50.0 + (1.0 if i % 2 else 0.0) for i in range(300)]
        assert self._calc_fault_rate(300, op) == 100.0

    def test_duration_just_below_min_not_confirmed(self) -> None:
        """持续时长边界 -ε：299 点 1s → 段时长 298+1=299s < 300s → 不计故障."""
        op = [50.0 + (1.0 if i % 2 else 0.0) for i in range(299)]
        assert self._calc_fault_rate(299, op) == 0.0


# ---------------------------------------------------------------------------
# 检测器 4：detect_jump（跳变）
# ---------------------------------------------------------------------------


class TestDetectJump:
    """detect_jump：相邻变化幅度阈值 ±ε（0~800 量程阈值 640，严格 >）."""

    def test_exactly_at_threshold_not_flagged(self) -> None:
        # diff = 640.0 = 阈值 → 不触发（严格 >）
        assert detect_jump([100.0, 740.0], FLOW, RANGE_MIN, RANGE_MAX) == []

    def test_just_above_threshold_flagged(self) -> None:
        # diff = 640.5 > 640 → 仅标记跳变点本身（索引 1）
        results = detect_jump([100.0, 740.5], FLOW, RANGE_MIN, RANGE_MAX)
        assert results == [(1, OutlierReason.JUMP)]

    def test_nan_adjacency_skipped(self) -> None:
        assert detect_jump([100.0, float("nan"), 900.0], FLOW, RANGE_MIN, RANGE_MAX) == []

    def test_jump_triggers_in_engineering_range_0_800(self) -> None:
        """固化②：未归一化 0~800 量程下 JUMP 能触发（量纲修复回归防护）."""
        detector = OutlierDetector(FLOW)
        reasons_map = detector.detect_all(
            "pv", [100.0, 741.0, 200.0], make_ts(3), RANGE_MIN, RANGE_MAX, is_normalized=False
        )
        assert OutlierReason.JUMP in reasons_map.get(1, [])

    def test_jump_triggers_normalized_0_100(self) -> None:
        """固化②对照：归一化 0~100 量程（阈值 80）下 diff=81 触发."""
        detector = OutlierDetector(FLOW)
        reasons_map = detector.detect_all(
            "pv", [10.0, 91.0, 50.0], make_ts(3), 0.0, 100.0, is_normalized=True
        )
        assert OutlierReason.JUMP in reasons_map.get(1, [])

    def test_jump_is_invalidate(self) -> None:
        assert OutlierDetector.should_invalidate([OutlierReason.JUMP]) is True


# ---------------------------------------------------------------------------
# 检测器 5：detect_spike（尖峰）
# ---------------------------------------------------------------------------


class TestDetectSpike:
    """detect_spike：单点突变且前后回落，阈值 ±ε（0~800 量程阈值 400，严格 >）."""

    def test_exactly_at_threshold_not_flagged(self) -> None:
        # 两侧 diff 均 = 400.0 = 阈值 → 不触发（严格 >）
        assert detect_spike([400.0, 800.0, 400.0], FLOW, RANGE_MIN, RANGE_MAX) == []

    def test_just_above_threshold_flagged(self) -> None:
        # 两侧 diff 均 = 400.5 > 400 → 标记尖峰点（索引 1）
        results = detect_spike([400.0, 800.5, 400.0], FLOW, RANGE_MIN, RANGE_MAX)
        assert results == [(1, OutlierReason.SPIKE)]

    def test_one_side_below_not_flagged(self) -> None:
        # 突变后未回落（阶跃非尖峰）：next_diff = 0 < 阈值 → 不触发
        assert detect_spike([400.0, 800.5, 800.5], FLOW, RANGE_MIN, RANGE_MAX) == []

    def test_needs_at_least_3_points(self) -> None:
        assert detect_spike([400.0, 800.5], FLOW, RANGE_MIN, RANGE_MAX) == []

    def test_spike_triggers_in_engineering_range_0_800(self) -> None:
        """固化②：未归一化 0~800 量程下 SPIKE 能触发（量纲修复回归防护）."""
        detector = OutlierDetector(FLOW)
        reasons_map = detector.detect_all(
            "pv", [100.0, 501.0, 100.0], make_ts(3), RANGE_MIN, RANGE_MAX, is_normalized=False
        )
        assert OutlierReason.SPIKE in reasons_map.get(1, [])


# ---------------------------------------------------------------------------
# 检测器 6：detect_ts_anomaly（时间戳异常，MARK_ONLY）
# ---------------------------------------------------------------------------


class TestDetectTsAnomaly:
    """detect_ts_anomaly：重复/逆序/间隔 > 2×期望间隔（严格 >）."""

    def test_gap_exactly_2x_not_flagged(self) -> None:
        t0 = make_ts(1)[0]
        ts = [t0, t0 + timedelta(seconds=2.0)]
        assert detect_ts_anomaly(ts, 1.0) == []

    def test_gap_just_above_2x_flagged(self) -> None:
        t0 = make_ts(1)[0]
        ts = [t0, t0 + timedelta(seconds=2.5)]
        assert detect_ts_anomaly(ts, 1.0) == [(1, OutlierReason.TS_ANOMALY)]

    def test_duplicate_flagged(self) -> None:
        ts = make_ts(3)
        ts[2] = ts[1]
        assert detect_ts_anomaly(ts, 1.0) == [(2, OutlierReason.TS_ANOMALY)]

    def test_out_of_order_flagged(self) -> None:
        t0 = make_ts(1)[0]
        ts = [t0, t0 + timedelta(seconds=1.0), t0 + timedelta(seconds=0.5)]
        assert detect_ts_anomaly(ts, 1.0) == [(2, OutlierReason.TS_ANOMALY)]

    def test_normal_sequence_not_flagged(self) -> None:
        assert detect_ts_anomaly(make_ts(100), 1.0) == []

    def test_fewer_than_2_points(self) -> None:
        assert detect_ts_anomaly(make_ts(1), 1.0) == []
        assert detect_ts_anomaly([], 1.0) == []

    def test_ts_anomaly_is_mark_only(self) -> None:
        assert OutlierDetector.should_invalidate([OutlierReason.TS_ANOMALY]) is False


# ---------------------------------------------------------------------------
# 检测器 7：detect_qc_bad（质量码异常）
# ---------------------------------------------------------------------------


class TestDetectQcBad:
    """detect_qc_bad：Good 码（1/2/3/192/None 缺省）不触发；Bad/Uncertain/未知触发."""

    def test_good_codes_not_flagged(self) -> None:
        assert detect_qc_bad([1, 2, 3, 192, None]) == []

    def test_bad_and_uncertain_flagged(self) -> None:
        # 0=Bad；64=OPC Uncertain → Unknown；999=未知 → Unknown（≠ Good 均标记）
        assert _reasons_of(detect_qc_bad([0, 64, 999])) == {0, 1, 2}

    def test_none_list_and_empty(self) -> None:
        assert detect_qc_bad(None) == []
        assert detect_qc_bad([]) == []

    def test_qc_bad_is_invalidate(self) -> None:
        assert OutlierDetector.should_invalidate([OutlierReason.QC_BAD]) is True


# ---------------------------------------------------------------------------
# 检测器 8：detect_hf_noise（高频噪声，MARK_ONLY）
# ---------------------------------------------------------------------------


class TestDetectHfNoise:
    """detect_hf_noise：截止频率以上能量占比 > 30% 触发（严格 >，全段标记）.

    手算（n=100，fs=1Hz，rfft 单边谱）：
        x[i] = A·sin(2π·5·i/100) + B·(-1)^i
        正弦恰在第 5 -bin（0.05Hz，5 整周期无泄漏），交替分量恰在 Nyquist bin 50；
        截止 0.2Hz → hf_mask = bins 21..50，仅含 Nyquist 分量。
        单边谱功率：正弦 bin5 = (A·n/2)² = 2500A²，Nyquist = (B·n)² = 10000B²
        hf_ratio = B²/(B² + A²/4)
        B=1, A=2.98 → 1/(1+2.2201) = 0.31055 > 0.3 → 触发
        B=1, A=3.13 → 1/(1+2.4492) = 0.28992 < 0.3 → 不触发
    """

    _N = 100

    def _mixed(self, amplitude: float) -> list[float]:
        return [
            amplitude * math.sin(2.0 * math.pi * 5.0 * i / self._N) + (1.0 if i % 2 else -1.0)
            for i in range(self._N)
        ]

    def test_pure_nyquist_flagged(self) -> None:
        # 纯交替 ±1（0.5Hz = Nyquist）：hf_ratio = 1.0 > 0.3 → 全段标记
        values = [1.0 if i % 2 else -1.0 for i in range(self._N)]
        results = detect_hf_noise(values, FLOW, 1.0)
        assert _reasons_of(results) == set(range(self._N))

    def test_pure_low_freq_not_flagged(self) -> None:
        # 纯 0.05Hz 正弦（bin 5 < 截止）：hf_ratio = 0 → 不触发
        values = [math.sin(2.0 * math.pi * 5.0 * i / self._N) for i in range(self._N)]
        assert detect_hf_noise(values, FLOW, 1.0) == []

    def test_ratio_just_above_30pct_flagged(self) -> None:
        assert detect_hf_noise(self._mixed(2.98), FLOW, 1.0) != []

    def test_ratio_just_below_30pct_not_flagged(self) -> None:
        assert detect_hf_noise(self._mixed(3.13), FLOW, 1.0) == []

    def test_degenerate_inputs(self) -> None:
        # n < 8 / 全 NaN / 恒定（零能量）→ 不触发、不抛异常
        assert detect_hf_noise([1.0] * 7, FLOW, 1.0) == []
        assert detect_hf_noise([float("nan")] * 16, FLOW, 1.0) == []
        assert detect_hf_noise([5.0] * 16, FLOW, 1.0) == []

    def test_hf_noise_is_mark_only(self) -> None:
        assert OutlierDetector.should_invalidate([OutlierReason.HF_NOISE]) is False


# ---------------------------------------------------------------------------
# MARK_ONLY / INVALIDATE 全集口径
# ---------------------------------------------------------------------------


class TestMarkOnlyPolicy:
    """8 类原因码置 invalid 口径全表（算法说明 §3.4.3 备注 + Phase 1 FROZEN 整改）.

    仅标记（should_invalidate=False）：TS_ANOMALY / HF_NOISE / FROZEN
    置 valid=False（should_invalidate=True）：NAN / OUT_OF_RANGE / JUMP / SPIKE / QC_BAD
    """

    @pytest.mark.parametrize(
        "reason",
        [OutlierReason.TS_ANOMALY, OutlierReason.HF_NOISE, OutlierReason.FROZEN],
    )
    def test_mark_only_reasons(self, reason: OutlierReason) -> None:
        assert OutlierDetector.should_invalidate([reason]) is False

    @pytest.mark.parametrize(
        "reason",
        [
            OutlierReason.NAN,
            OutlierReason.OUT_OF_RANGE,
            OutlierReason.JUMP,
            OutlierReason.SPIKE,
            OutlierReason.QC_BAD,
        ],
    )
    def test_invalidate_reasons(self, reason: OutlierReason) -> None:
        assert OutlierDetector.should_invalidate([reason]) is True

    def test_mixed_mark_only_and_invalidate(self) -> None:
        # 叠加原因码中存在置 invalid 类 → 整点置 invalid
        assert OutlierDetector.should_invalidate([OutlierReason.FROZEN, OutlierReason.JUMP]) is True
