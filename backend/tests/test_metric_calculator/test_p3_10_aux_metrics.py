"""P3-10 辅助指标语义与工程解释测试（10 号文 §4.3 / §5 EA 子例）.

覆盖验收子例（独立预期先行，实现对照）：
- C04-EA3：QC 全 Good + 20 槽 PV 越界 → Good=100%、usable=80%；
  无 QC 的 Good 不可判定，不能回退 usable 冒充（新 profile）。
- C15-EA1：粘滞消费契约（assess_stiction_features——P3-01 唯一写入已落，
  本卡只消费）：静稳=方法不适用（reason=no_limit_cycle，不判阴性）、
  纯滞后不误归粘滞、支持范围内滞环样本有正确证据。
- C15-EA2：PV 冻结 + 缺 OP → 未确认候选（不计故障、不冒充已确认）；
  OP 恒定不能靠冻结确诊；OP 有变化仅按声明条件输出疑似。
- C16-EA1：PV/OP 常量 → 线性/非线性均 null（零方差不可判定非 0/1）；
  有确定滞后的线性过程 → 代理值 + 明确代理语义。
- C16-EA2：零平台穿越（[-1,0,+1]→1）、gap 不连接（[-1,?,+1]→0）、
  死区吸收噪声不虚增、死区 0 不跨缺口。
- C16-EA3：平均绝对偏差（固定偏差 5→5；正弦峰值 10→2A/π≈6.37）
  不冒充真实振幅；OP 跨 gap 往返不加跨 gap 差并显示未知活动。
- C16-EA4：时间常数=相关响应时间粗估（搜索秒数/截断记录，τ/θ 不混名）；
  统计样本均值 6.67 与 [0,10) 时间均值 9 分开。

每个新 profile 行为均带 legacy 对照（golden 基线零回归证明）。
"""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from app.contracts.data_types import ControlType, QualitySummary
from app.contracts.data_types import OutlierReason as OR
from app.services.assessment_profile import AssessmentProfileId
from app.services.metric_calculator.good_value import GoodValueRateCalculator
from app.services.metric_calculator.instrument_fault import InstrumentFaultRateCalculator
from app.services.metric_calculator.output_trip import OutputTripIndexCalculator
from app.services.metric_calculator.setpoint_crossing import (
    OscillationAmplitudeCalculator,
    SetpointCrossingCountCalculator,
)
from app.services.metric_calculator.statistics import (
    ErrorMeanCalculator,
    OpMeanCalculator,
    PvMeanCalculator,
    PvStdCalculator,
)
from app.services.metric_calculator.stiction import assess_stiction_features
from app.services.metric_calculator.time_constant import TimeConstantCalculator
from app.services.metric_calculator.valve_diagnosis import (
    ValveLinearityCalculator,
    ValveNonlinearityCalculator,
    ValveOperatingRangeCalculator,
)
from app.services.preprocessing.thresholds import get_threshold

from .conftest import make_bundle

_V3 = AssessmentProfileId.CANDIDATE_V3_RC1


def _new(calc):
    """以新 profile（CANDIDATE_V3_RC1）注入计算器."""
    return calc.with_assessment_profile(_V3)


@pytest.fixture
def low_frozen_fault_minutes(monkeypatch):
    """frozen_fault_min_minutes=0.05 分钟（3 秒），短数据可触发复合判据（同既有套件）."""
    patched = dataclasses.replace(get_threshold(ControlType.FLOW), frozen_fault_min_minutes=0.05)
    monkeypatch.setattr(
        "app.services.metric_calculator.instrument_fault.get_threshold_by_sampling_freq",
        lambda _label: patched,
    )


# ---------------------------------------------------------------------------
# C04-EA3：Good 与 usable 分开；缺质量码不可判定
# ---------------------------------------------------------------------------


class TestGoodValueSeparation:
    def _bundle(self, *, qc_rate: float | None, pv_valid_20_bad: bool):
        n = 100
        validity = {"pv_valid": [False] * 20 + [True] * 80} if pv_valid_20_bad else None
        summary = QualitySummary(
            total_count=n,
            valid_count=n,
            bad_count=0,
            valid_rate=1.0,
            good_value_rate=qc_rate,
        )
        return make_bundle(
            {"pv": [50.0] * n},
            validity,
            metric_code="good_value_rate",
            quality_summary=summary,
            loop_confidence_level="A",
        )

    def test_qc_all_good_usable_80(self):
        """100 槽 QC 全 Good、20 槽 PV 越界 → Good=100%，usable=80%（分开可查）."""
        bundle = self._bundle(qc_rate=1.0, pv_valid_20_bad=True)

        result = _new(GoodValueRateCalculator()).calculate(bundle)
        assert result.value == 100.0
        assert result.details["good_value_rate"] == 100.0
        assert result.details["usable_rate"] == 80.0
        assert result.details["assessmentState"] == "COMPLETE"

        # legacy：同值但无 usable 语义（对照）
        legacy = GoodValueRateCalculator().calculate(bundle)
        assert legacy.value == 100.0
        assert "usable_rate" not in legacy.details

    def test_usable_rate_from_summary_increment(self):
        """usable_rate 优先消费 quality_summary 增量字段（与本地计算同值）."""
        n = 100
        summary = QualitySummary(
            total_count=n,
            valid_count=n,
            good_value_rate=1.0,
            usable_rate=0.8,
        )
        bundle = make_bundle(
            {"pv": [50.0] * n},
            None,
            metric_code="good_value_rate",
            quality_summary=summary,
        )
        result = _new(GoodValueRateCalculator()).calculate(bundle)
        assert result.details["usable_rate"] == 80.0
        assert result.details["usable_rate_source"] == "quality_summary"

    def test_no_qc_good_undecidable_not_masqueraded(self):
        """无 QC：Good 不可判定（value=None，数据质量保留），不用 pv_valid 冒充."""
        bundle = self._bundle(qc_rate=None, pv_valid_20_bad=True)

        result = _new(GoodValueRateCalculator()).calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "no_quality_codes"
        assert result.details["assessmentState"] == "UNASSESSABLE"
        # 数据好与可判定分开：回路级质量 A 保留（09a 同口径）
        assert result.confidence_level == "A"
        # usable 单独可查（80%），但不冒充 QC Good
        assert result.details["usable_rate"] == 80.0

        # legacy 对照：pv_valid 回退冒充仍存在（EA-04 问题行为，legacy 保留）
        legacy = GoodValueRateCalculator().calculate(bundle)
        assert legacy.value == 80.0


# ---------------------------------------------------------------------------
# C15-EA1：粘滞消费契约（assess_stiction_features——P3-01 供给，本卡只消费）
# ---------------------------------------------------------------------------


class TestStictionFeatureConsumption:
    N = 600
    OMEGA = 2.0 * math.pi / 50.0

    def _pv(self) -> np.ndarray:
        return np.sin(self.OMEGA * np.arange(self.N))

    def _hysteresis_op(self, pv: np.ndarray, stickband: float) -> np.ndarray:
        return pv + stickband * np.sign(np.cos(self.OMEGA * np.arange(len(pv))))

    def test_static_window_method_not_applicable(self):
        """静稳无动作：reason=no_limit_cycle——方法不适用，消费方不得渲染为"无粘滞"阴性."""
        pv = np.full(300, 50.0)
        op = np.full(300, 50.0)
        info = assess_stiction_features(pv, op, sample_interval=1.0)
        assert info["is_limit_cycle"] is False
        assert info["reason"] == "no_limit_cycle"

    def test_pure_lag_not_attributed_to_stiction(self):
        """纯滞后（相位滞后单值曲线）经 θ 补偿不误归粘滞."""
        phi = math.acos(0.8)
        pv = self._pv()
        op = np.sin(self.OMEGA * np.arange(self.N) - phi)
        info = assess_stiction_features(pv, op, sample_interval=1.0)
        # 纯滞后可进极限环门控，但补偿后 b/a 低——证据不足不确诊或低值
        assert info.get("is_limit_cycle") is False or info["stiction_index"] < 0.05

    def test_hysteresis_sample_has_correct_evidence(self):
        """支持范围内滞环样本：有正确证据（极限环 + 拟合度 + 短长轴比）."""
        pv = self._pv()
        op = self._hysteresis_op(pv, 0.3)
        info = assess_stiction_features(pv, op, sample_interval=1.0)
        assert info["is_limit_cycle"] is True
        assert info["fitting_score"] >= 0.5
        assert 0.0 < info["stiction_index"] <= 1.0
        # 证据不足分支（reason 键）不出现——该窗有足够证据
        assert "reason" not in info or info.get("reason") is None

    def test_low_correlation_is_insufficient_not_detected(self):
        """低相关随机散点：insufficient_fit——不确诊也不冒称"未检出"（D3 语义）."""
        rng = np.random.default_rng(7)
        pv = self._pv()
        op = rng.normal(0.0, 1.0, self.N)
        info = assess_stiction_features(pv, op, sample_interval=1.0)
        assert info["is_limit_cycle"] is True
        assert info["reason"] == "insufficient_fit"
        assert info["fitting_score"] < 0.5


# ---------------------------------------------------------------------------
# C15-EA2：仪表故障——FROZEN 缺依据不确诊
# ---------------------------------------------------------------------------


class TestInstrumentFaultConfirmation:
    def _bundle(self, op_values: list[float] | None, n: int = 10):
        signals: dict = {"pv": [50.0] * n}
        if op_values is not None:
            signals["op"] = op_values
        return make_bundle(
            signals,
            outlier_reasons={"pv": [[OR.FROZEN.value]] * n},
            metric_code="instrument_fault_rate",
            loop_confidence_level="A",
        )

    def test_missing_op_unconfirmed_candidates(self):
        """缺 OP：FROZEN 候选未确认、不计故障（legacy 回落旧口径直接计=对照）."""
        bundle = self._bundle(op_values=None)

        result = _new(InstrumentFaultRateCalculator()).calculate(bundle)
        assert result.value == 0.0
        assert result.details["freeze_count"] == 0
        assert result.details["frozen_marked_count"] == 10
        assert result.details["frozen_unconfirmed_count"] == 10
        assert result.details["confirmation_basis"] == "missing_op"
        assert result.details["assessmentState"] == "PARTIAL"  # 有候选未定论，非"无故障"

        legacy = InstrumentFaultRateCalculator().calculate(bundle)
        assert legacy.value == 100.0  # legacy 回落旧口径（对照）

    def test_constant_op_cannot_confirm_by_freeze_alone(self, low_frozen_fault_minutes):
        """OP 恒定：控制器未动作，不能靠 PV 冻结确诊仪表卡死."""
        bundle = self._bundle(op_values=[52.0] * 10)

        result = _new(InstrumentFaultRateCalculator()).calculate(bundle)
        assert result.value == 0.0
        assert result.details["frozen_unconfirmed_count"] == 10
        assert result.details["unconfirmed_breakdown"].get("op_inactive") == 10
        assert result.details["assessmentState"] == "PARTIAL"

    def test_active_op_suspected_under_declared_criteria(self, low_frozen_fault_minutes):
        """OP 有变化 + 持续达标：按声明条件输出疑似（非物理确诊），判据可追溯."""
        op = [10.0, 90.0, 10.0, 90.0, 10.0, 90.0, 10.0, 90.0, 10.0, 90.0]
        bundle = self._bundle(op_values=op)

        result = _new(InstrumentFaultRateCalculator()).calculate(bundle)
        assert result.value == 100.0
        assert result.details["freeze_count"] == 10
        assert result.details["frozen_unconfirmed_count"] == 0
        assert result.details["assessmentState"] == "COMPLETE"
        # 疑似性质 + 实际生效判据（可追溯）
        assert result.details["faultNature"] == "suspected_under_declared_criteria"
        criteria = result.details["applied_criteria"]
        assert criteria["min_duration_seconds"] == 3.0  # 0.05min monkeypatch
        assert criteria["op_active_std_epsilon"] > 0

    def test_out_of_range_still_confirmed(self, low_frozen_fault_minutes):
        """OUT_OF_RANGE 直接计确认故障，不受复合判据影响（口径不变）."""
        n = 10
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [50.0] * n},
            outlier_reasons={"pv": [[OR.OUT_OF_RANGE.value]] + [[]] * (n - 1)},
            metric_code="instrument_fault_rate",
        )
        result = _new(InstrumentFaultRateCalculator()).calculate(bundle)
        assert result.value == 10.0
        assert result.details["overrange_count"] == 1
        assert result.details["assessmentState"] == "COMPLETE"


# ---------------------------------------------------------------------------
# C16-EA1：线性相关代理零方差不可判定
# ---------------------------------------------------------------------------


class TestValveCorrelationProxy:
    def _const_bundle(self):
        n = 10
        return make_bundle(
            {"pv": [50.0] * n, "op": [50.0] * n},
            None,
            mask_expression="pv_valid && op_valid",
            metric_code="valve_linearity",
            loop_confidence_level="A",
        )

    def test_constant_signals_both_null(self):
        """PV/OP 常量：线性/非线性均 null（零方差→不可判定，非 0/1）."""
        bundle = self._const_bundle()

        lin = _new(ValveLinearityCalculator()).calculate(bundle)
        assert lin.value is None
        assert lin.details["reason"] == "zero_variance_undecidable"
        assert lin.details["assessmentState"] == "UNASSESSABLE"
        assert lin.confidence_level == "A"  # 数据质量保留
        assert lin.details["proxy"] == "pv_op_pearson_correlation_proxy"

        nonlinear = _new(ValveNonlinearityCalculator()).calculate(bundle)
        assert nonlinear.value is None
        assert nonlinear.details["reason"] == "zero_variance_undecidable"

        # legacy 对照：0.0 / 1.0（把静稳解释成完全非线性=EA-10 问题行为）
        assert ValveLinearityCalculator().calculate(bundle).value == 0.0
        assert ValveNonlinearityCalculator().calculate(bundle).value == 1.0

    def test_nonlinearity_dependency_path_transmits_undecidable(self):
        """依赖注入路径：linearity 不可判定 → nonlinearity 同样 None（不输出 1）."""
        bundle = self._const_bundle()
        lin = _new(ValveLinearityCalculator()).calculate(bundle)
        calc = _new(ValveNonlinearityCalculator())
        calc.dependencies = {"valve_linearity": lin}
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "zero_variance_undecidable"

    def test_lagged_linear_process_proxy_semantics(self):
        """有确定滞后的线性过程：代理值有效 + 代理语义显式（低相关不升级故障）."""
        n = 200
        rng = np.random.default_rng(3)
        op = 50.0 + np.cumsum(rng.normal(0.0, 1.0, n))  # 随机游走激励
        pv = np.empty(n)
        pv[:5] = 50.0
        for k in range(5, n):  # 一阶跟随时滞 5 采样
            pv[k] = pv[k - 1] + 0.2 * (op[k - 5] - pv[k - 1])
        bundle = make_bundle(
            {"pv": [float(v) for v in pv], "op": [float(v) for v in op]},
            None,
            mask_expression="pv_valid && op_valid",
            metric_code="valve_linearity",
        )
        result = _new(ValveLinearityCalculator()).calculate(bundle)
        assert result.value is not None and result.value > 0.3
        assert result.details["assessmentState"] == "COMPLETE"
        assert result.details["proxy"] == "pv_op_pearson_correlation_proxy"
        assert "not a valve characteristic" in result.details["proxyNote"]

    def test_operating_range_command_semantics(self):
        """运行区间：数值不变，名称解释为输出指令范围（实际阀位另证）."""
        bundle = make_bundle(
            {"op": [30.0, 40.0, 50.0, 60.0]},
            None,
            mask_expression="op_valid",
            metric_code="valve_operating_range",
        )
        result = _new(ValveOperatingRangeCalculator()).calculate(bundle)
        assert result.value == 30.0
        assert result.details["op_min"] == 30.0
        assert result.details["op_max"] == 60.0
        assert result.details["quantity"] == "output_command_range"
        assert "actual valve position" in result.details["quantityNote"]


# ---------------------------------------------------------------------------
# C16-EA2：零平台穿越 + gap 不连接 + 死区
# ---------------------------------------------------------------------------


def _crossing_bundle(pv: list[float], pv_valid: list[bool] | None = None):
    n = len(pv)
    validity = {"pv_valid": pv_valid, "sp_valid": [True] * n} if pv_valid else None
    return make_bundle(
        {"pv": pv, "sp": [50.0] * n},
        validity,
        mask_expression="pv_valid && sp_valid",
        metric_code="setpoint_crossing_count",
    )


class TestSetpointCrossing:
    def test_zero_platform_counted(self):
        """偏差 [-1,0,+1]：新口径 1 次穿越（零平台=恰在 SP，非符号不变）；legacy 0."""
        bundle = _crossing_bundle([49.0, 50.0, 51.0])

        result = _new(SetpointCrossingCountCalculator()).calculate(bundle)
        assert result.value == 1.0
        assert result.details["zero_platform_points"] == 1

        assert SetpointCrossingCountCalculator().calculate(bundle).value == 0.0

    def test_gap_not_connected(self):
        """偏差 [-1, gap, +1]：新口径 0 次穿越（gap 两侧不连接）；legacy 1."""
        bundle = _crossing_bundle(
            [49.0, 49.0, 50.0, 51.0, 51.0, 51.0],
            pv_valid=[True, True, False, True, True, True],
            # masked=[0,1,3,4,5] → diffs=[-1,-1,+1,+1,+1]：唯一符号变化跨 gap
        )

        result = _new(SetpointCrossingCountCalculator()).calculate(bundle)
        assert result.value == 0.0
        assert result.details["segment_count"] == 2
        assert result.details["cross_gap_excluded"] is True

        assert SetpointCrossingCountCalculator().calculate(bundle).value == 1.0

    def test_deadband_absorbs_noise(self):
        """死区内噪声不虚增：±0.3 噪声，死区 0.5 → 0 次；死区 0（默认）→ 4 次."""
        pv = [50.3, 49.7, 50.3, 49.7, 50.3]
        bundle = _crossing_bundle(pv)

        calc_deadband = _new(SetpointCrossingCountCalculator()).with_pinned_params(
            {("setpoint_crossing_count", "STABLE"): {"crossing_deadband": 0.5}}
        )
        result = calc_deadband.calculate(bundle)
        assert result.value == 0.0
        assert result.details["deadband"] == 0.5
        assert result.details["zero_platform_points"] == 5  # 全部 5 个差分在死区内

        result_default = _new(SetpointCrossingCountCalculator()).calculate(bundle)
        assert result_default.value == 4.0  # 默认死区 0：精确统计（4 对符号交替）
        assert result_default.details["deadband"] == 0.0

    def test_deadband_zero_still_not_bridging_gap(self):
        """死区为 0 时也不跨缺口连接（死区与缺口独立）."""
        bundle = _crossing_bundle(
            [50.3, 50.0, 49.7, 50.0, 50.3],
            pv_valid=[True, False, True, True, True],
        )
        calc = _new(SetpointCrossingCountCalculator()).with_pinned_params(
            {("setpoint_crossing_count", "STABLE"): {"crossing_deadband": 0.0}}
        )
        result = calc.calculate(bundle)
        # masked=[0,2,3,4] → 段 [(0),(2..4)]；段1 差分 +0.3→-0.7 → 1 次穿越；段0 单点无穿越
        assert result.value == 1.0
        assert result.details["cross_gap_excluded"] is True


# ---------------------------------------------------------------------------
# C16-EA3：平均绝对偏差正名 + 输出行程不跨 gap
# ---------------------------------------------------------------------------


class TestAmplitudeAndTrip:
    def test_fixed_deviation_and_sine_mean_abs(self):
        """固定偏差 5 → 5；正弦峰值 10 → 2A/π≈6.37；均不冒充真实振幅."""
        n = 100
        fixed = make_bundle(
            {"pv": [55.0] * n, "sp": [50.0] * n},
            None,
            mask_expression="pv_valid && sp_valid",
            metric_code="oscillation_amplitude",
        )
        result = _new(OscillationAmplitudeCalculator()).calculate(fixed)
        assert result.value == 5.0
        assert result.details["quantity"] == "mean_absolute_deviation"

        t = np.arange(n)
        sine_pv = 50.0 + 10.0 * np.sin(2.0 * math.pi * t / 50.0)
        sine = make_bundle(
            {"pv": [float(v) for v in sine_pv], "sp": [50.0] * n},
            None,
            mask_expression="pv_valid && sp_valid",
            metric_code="oscillation_amplitude",
        )
        result_sine = _new(OscillationAmplitudeCalculator()).calculate(sine)
        assert abs(result_sine.value - 20.0 / math.pi) < 0.05  # 独立解析：mean|A sin|=2A/π
        assert "NOT oscillation amplitude" in result_sine.details["quantityNote"]

    def _gap_trip_bundle(self):
        """OP 跨 gap 往返：段1 [50,60,55]，段2 [40,45]，跨 gap 差 |40-55|=15 被排除."""
        op = [50.0, 60.0, 55.0, None, None, None, None, 40.0, 45.0, 45.0]
        validity = {
            "op_valid": [v is not None for v in op],
        }
        return make_bundle(
            {"op": op},
            validity,
            mask_expression="op_valid",
            metric_code="output_trip_index",
        )

    def test_trip_excludes_cross_gap_diff(self):
        bundle = self._gap_trip_bundle()

        result = _new(OutputTripIndexCalculator()).calculate(bundle)
        # 段内行程 |60-50|+|55-60| + |45-40| = 15+5 = 20（跨 gap 的 |40-55| 不加）
        assert result.details["total_trip"] == 20.0
        assert result.details["segment_count"] == 2
        assert result.details["cross_gap_excluded_pairs"] == 1
        assert result.details["unknown_activity"] is True
        assert result.details["quantity"] == "output_command_activity"

        # legacy 对照：跨 gap 差计入（20+15=35）
        legacy = OutputTripIndexCalculator().calculate(bundle)
        assert legacy.details["total_trip"] == 35.0

    def test_trip_continuous_no_gap_unchanged(self):
        """无缺口时新旧行为一致（分段累加=连续累加）."""
        op = [50.0, 60.0, 55.0, 40.0, 45.0]
        bundle = make_bundle(
            {"op": op}, None, mask_expression="op_valid", metric_code="output_trip_index"
        )
        new = _new(OutputTripIndexCalculator()).calculate(bundle)
        legacy = OutputTripIndexCalculator().calculate(bundle)
        assert new.details["total_trip"] == legacy.details["total_trip"] == 35.0
        assert new.details["cross_gap_excluded_pairs"] == 0
        assert new.details["unknown_activity"] is False


# ---------------------------------------------------------------------------
# C16-EA4：时间常数粗估正名/截断 + 样本均值与时间均值分开
# ---------------------------------------------------------------------------


def _first_order_bundle(n: int, tau_s: float, delay: int = 5, seed: int = 11, amp: float = 2.0):
    """一阶+时滞系统合成（独立解析参考，禁实现自证）.

    快系统（tau_s<100）用随机游走激励；慢系统用白噪声高信噪比激励
    （相关分析的近白输入前提，保证截断判据可复现）。
    """
    rng = np.random.default_rng(seed)
    if tau_s < 100.0:
        op = 50.0 + np.cumsum(rng.normal(0.0, amp, n))
    else:
        op = 50.0 + rng.normal(0.0, amp, n)
    pv = np.empty(n)
    pv[:delay] = 50.0
    dt = 1.0
    for k in range(delay, n):
        pv[k] = pv[k - 1] + (dt / tau_s) * (op[k - delay] - pv[k - 1])
    return make_bundle(
        {
            "pv": [float(v) for v in pv],
            "op": [float(v) for v in op],
        },
        None,
        mask_expression="pv_valid && op_valid",
        metric_code="time_constant",
        sampling_freq="1s",
    )


class TestTimeConstantRoughEstimate:
    def test_naming_search_window_and_basis(self):
        """相关响应时间粗估：τ+θ 质心口径 + 实际搜索秒数（独立手算 n=200→100s）."""
        bundle = _first_order_bundle(200, tau_s=30.0)
        result = _new(TimeConstantCalculator()).calculate(bundle)

        assert result.value is not None and result.value > 0
        d = result.details
        assert d["quantity"] == "correlation_response_time_rough_estimate"
        assert d["estimateBasis"] == "centroid_lag_tau_plus_theta"
        # 独立手算：max_lag = min(n//2,300)=100 lags → 搜索窗 = 99×1s + 1s = 100s
        assert d["searchLagSamples"] == 100
        assert d["searchWindowSeconds"] == 100.0
        assert d["searchLimitingFactor"] == "data_length"  # n//2=100 < 引擎 300 上限
        assert d["assessmentState"] == "COMPLETE"
        # 快系统（τ=30 ≪ 窗 100）：不触发截断标记
        assert d["truncated"] is False
        # τ/θ 不混名说明
        assert "tau/theta separation" in d["quantityNote"]

    def test_slow_system_truncation_flagged(self):
        """慢于搜索窗系统（τ=2000s ≫ 窗 300s）：截断明确（不冒充精确 τ）.

        独立预期：ir∝e^{-k/2000} 截断在 W=300 → 理想质心 ≈144s ≥ 0.4×W=120
        （窗末仍余 e^{-0.15}≈86% 响应）；固定 seed 的估计质心 ~126s 稳定过阈。
        """
        bundle = _first_order_bundle(1200, tau_s=2000.0, seed=42, amp=40.0)
        result = _new(TimeConstantCalculator()).calculate(bundle)

        assert result.value is not None
        assert result.value >= 120.0  # 质心 ≥ 0.4×窗（独立手算下界）
        assert result.details["truncated"] is True
        note = result.details["truncationNote"]
        assert "not decayed within the correlation search window" in note
        assert result.details["centroidToWindowRatio"] >= 0.4

    def test_insufficient_excitation_unassessable_keeps_quality(self):
        """激励不足：UNASSESSABLE 保留回路级质量（数据好 ≠ 可估计）."""
        n = 150
        bundle = make_bundle(
            {"pv": [50.0] * n, "op": [50.0] * n},  # 全静稳 → 无激励
            None,
            mask_expression="pv_valid && op_valid",
            metric_code="time_constant",
            loop_confidence_level="A",
        )
        result = _new(TimeConstantCalculator()).calculate(bundle)
        assert result.value is None
        assert result.details["assessmentState"] == "UNASSESSABLE"
        assert result.confidence_level == "A"
        # legacy 对照：INCONCLUSIVE（confidence=E）
        legacy = TimeConstantCalculator().calculate(bundle)
        assert legacy.value is None
        assert legacy.confidence_level == "E"


class TestStatisticsBasis:
    def _irregular_bundle(self):
        """不规则样本 0@0 / 10@1 / 10@10（C16-EA4 独立预期：样本均值 6.67、时间均值 9）."""
        bundle = make_bundle(
            {"pv": [0.0, 10.0, 10.0]},
            None,
            mask_expression="pv_valid",
            metric_code="pv_mean",
        )
        t0 = datetime(2024, 1, 1)
        bundle.data_block.timestamps = [t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=10)]
        return bundle

    def test_sample_mean_value_time_mean_details(self):
        bundle = self._irregular_bundle()

        result = _new(PvMeanCalculator()).calculate(bundle)
        assert result.value == pytest.approx(20.0 / 3.0, abs=0.01)  # 样本均值 6.67
        d = result.details
        assert d["statisticBasis"] == "sample_mean"
        assert d["uniformGrid"] is False
        assert d["timeWeightedMean"] == 9.0  # [0,10) 保持时间均值（末点持有 0）
        assert d["timeWeightedBasis"] == "zero_order_hold_half_open_last_slot_zero"

        # legacy 对照：无口径/时间均值键
        legacy = PvMeanCalculator().calculate(bundle)
        assert legacy.value == pytest.approx(20.0 / 3.0, abs=0.01)
        assert "statisticBasis" not in legacy.details
        assert "timeWeightedMean" not in legacy.details

    def test_uniform_grid_flag(self):
        """均匀网格：uniformGrid=true，不重复输出时间均值（样本均值即时间均值）."""
        bundle = make_bundle(
            {"pv": [50.0, 51.0, 49.0, 50.0]},
            None,
            mask_expression="pv_valid",
            metric_code="pv_mean",
        )
        result = _new(PvMeanCalculator()).calculate(bundle)
        assert result.details["uniformGrid"] is True
        assert "timeWeightedMean" not in result.details

    def test_std_and_error_mean_basis(self):
        """std 口径正名 + error_mean 非均匀网格时间均值（pairs 路径）."""
        bundle = make_bundle(
            {"pv": [0.0, 10.0, 10.0], "sp": [0.0, 0.0, 0.0]},
            None,
            mask_expression="pv_valid && sp_valid",
            metric_code="error_mean",
        )
        t0 = datetime(2024, 1, 1)
        bundle.data_block.timestamps = [t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=10)]

        std = _new(PvStdCalculator()).calculate(
            make_bundle({"pv": [1.0, 2.0, 3.0]}, None, mask_expression="pv_valid")
        )
        assert std.details["statisticBasis"] == "population_std_over_samples"

        err = _new(ErrorMeanCalculator()).calculate(bundle)
        assert err.value == pytest.approx(20.0 / 3.0, abs=0.01)
        assert err.details["timeWeightedMean"] == 9.0

    def test_op_mean_irregular(self):
        """op_mean 同口径（单信号基类覆盖）."""
        bundle = make_bundle(
            {"op": [0.0, 10.0, 10.0]}, None, mask_expression="op_valid", metric_code="op_mean"
        )
        t0 = datetime(2024, 1, 1)
        bundle.data_block.timestamps = [t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=10)]
        result = _new(OpMeanCalculator()).calculate(bundle)
        assert result.details["timeWeightedMean"] == 9.0
