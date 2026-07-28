"""P2 #39 TC2 边界条件测试.

覆盖原 57 问题清单 #39 要求的 4 类边界场景：
    1. 极端 PV 值（巨大数值 / 负数 / 零 / 极小正数）
    2. 100% Bad 质量（valid_rate=0）
    3. 低频振荡（周期 > 60s，采样窗口仅含 < 2 个完整周期）
    4. OP 饱和临界值（98 / 99 / 100，默认 epsilon=2 → op_high-epsilon=98）

设计依据：
    - 算法说明 §3.4-3.7（数据掩码与可信度评估）
    - 算法说明 §4.0.3（自控率）/ §4.1（好值率）/ §4.4（准确率）
    - 算法说明 §4.6（振荡率）/ §4.7（饱和率）
    - GB/T 44693.2-2024 附录 B.3 / F.1 / F.3 / F.6
    - DEFAULT_EPSILON=2.0, DEFAULT_OP_HIGH=100.0（saturation.py）

注：NaN/Inf 在 Python float 算术中传染，但 MetricDataBundle 信号经预处理
validity_mask 步骤已剔除；这里直接构造合法 float 极端值。
"""

from __future__ import annotations

import math

import pytest

from app.services.metric_calculator.accuracy import AccuracyRateCalculator
from app.services.metric_calculator.auto_mode import AutoModeRateCalculator
from app.services.metric_calculator.good_value import GoodValueRateCalculator
from app.services.metric_calculator.oscillation import OscillationRateCalculator
from app.services.metric_calculator.saturation import (
    DEFAULT_EPSILON,
    DEFAULT_OP_HIGH,
    DEFAULT_OP_LOW,
    SaturationRateCalculator,
)

from .conftest import make_bundle

# ---------------------------------------------------------------------------
# 1. 极端 PV 值边界
# ---------------------------------------------------------------------------


class TestExtremePvValues:
    """极端 PV 值不引发异常，准确率公式按指数衰减自洽."""

    @pytest.mark.parametrize(
        "pv_value,sp_value,desc",
        [
            (1e6, 0.0, "巨大正数 PV=1e6"),
            (-1e6, 0.0, "巨大负数 PV=-1e6"),
            (0.0, 0.0, "零值 PV=SP=0"),
            (1e-9, 0.0, "极小正数 PV=1e-9"),
            (-1e-9, 0.0, "极小负数 PV=-1e-9"),
            (100.0, 0.0, "量程上限 PV=100"),
            (-100.0, 0.0, "量程下限 PV=-100"),
        ],
    )
    def test_extreme_pv_no_crash_and_clamped(self, pv_value, sp_value, desc):
        """极端 PV 值：准确率不抛异常，值被 _clamp 限制在 [0, 100].

        恒定极端 PV 属恒定余差退化分支（e_max=0），需配置 pv_range 才能
        按量程百分比扣分；此处提供 pv_range=100 验证不溢出、不越界。
        """
        n = 100
        pv = [pv_value] * n
        sp = [sp_value] * n
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "pv_range": [100.0] * n},
            metric_code="accuracy_rate",
        )
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        # 不抛异常 + 值在 [0, 100]
        assert result.value is not None, f"{desc}: value 不应为 None"
        assert 0.0 <= result.value <= 100.0, f"{desc}: value={result.value} 越界"
        # mean_abs_error 应记录实际偏差
        assert result.details["mean_abs_error"] >= 0

    def test_huge_equal_pv_sp_yields_100(self):
        """PV=SP=1e6 时偏差为 0，准确率应为 100（极端值但零偏差）."""
        n = 100
        val = [1e6] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="accuracy_rate")
        result = AccuracyRateCalculator().calculate(bundle)
        assert result.value == 100.0

    def test_negative_mode_value_treated_as_manual(self):
        """负 MODE 值（异常输入）不计入自控率.

        _to_int 安全转换失败返回 -1，不在 AUTO_MODES={1,2,3} 中。
        """
        n = 100
        mode = [-1] * n  # 异常负值
        bundle = make_bundle({"mode": mode}, metric_code="auto_mode_rate")
        result = AutoModeRateCalculator().calculate(bundle)
        assert result.value == 0.0  # 无 auto 时长

    def test_mode_string_value_treated_as_manual(self):
        """MODE 字符串值安全转换为 -1，不计入自控率."""
        n = 100
        mode = ["Auto"] * n  # 字符串而非数字 1
        bundle = make_bundle({"mode": mode}, metric_code="auto_mode_rate")
        result = AutoModeRateCalculator().calculate(bundle)
        assert result.value == 0.0

    def test_auto_mode_mismatched_lengths_no_index_error(self):
        """mode 信号长于时间戳时按最短数组截断，不抛 IndexError."""
        n = 100
        mode = [1] * n
        bundle = make_bundle({"mode": mode}, metric_code="auto_mode_rate")
        # 时间戳截断到 50 点，模拟数组长度不一致
        bundle.data_block.timestamps = bundle.data_block.timestamps[:50]
        result = AutoModeRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["total_duration_s"] == 50.0


# ---------------------------------------------------------------------------
# 2. 100% Bad 质量
# ---------------------------------------------------------------------------


class TestFullBadQuality:
    """100% Bad 质量：valid_rate=0，所有指标应返回 INCONCLUSIVE 或 0."""

    def _make_full_bad_bundle(self, metric_code: str, signals: dict):
        """构造 100% Bad 质量数据包（所有 validity=False）."""
        n = len(next(iter(signals.values()))) if signals else 0
        validity = {f"{k}_valid": [False] * n for k in signals}
        # mask_expression 为空时 conftest 会取全部索引；
        # 这里手动设置 masked_indices 为空（validity 全 False）
        bundle = make_bundle(
            signals,
            validity=validity,
            metric_code=metric_code,
            mask_expression="pv_valid",  # 仅取 pv_valid=True 的索引（空集）
        )
        return bundle

    def test_accuracy_full_bad_returns_none(self):
        """100% Bad 质量：准确率 INCONCLUSIVE（无有效 PV-SP 对）."""
        n = 100
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = self._make_full_bad_bundle("accuracy_rate", {"pv": pv, "sp": sp})
        result = AccuracyRateCalculator().calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "no_valid_pv_sp_pairs"

    def test_oscillation_full_bad_returns_inconclusive(self):
        """100% Bad 质量：振荡率 INCONCLUSIVE（mask 后无数据点 n<4）."""
        n = 100
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = self._make_full_bad_bundle("oscillation_rate", {"pv": pv, "sp": sp})
        result = OscillationRateCalculator().calculate(bundle)
        # n<4 分支返回 INCONCLUSIVE（v6.1：数据不足时返回 None 而非 0）
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["is_oscillating"] is False

    def test_saturation_full_bad_returns_none(self):
        """100% Bad 质量：饱和率 INCONCLUSIVE（无有效 OP-MODE 对）."""
        n = 100
        mode = [1] * n
        op = [50.0] * n
        bundle = self._make_full_bad_bundle("saturation_rate", {"mode": mode, "op": op})
        result = SaturationRateCalculator().calculate(bundle)
        assert result.value is None

    def test_auto_mode_full_bad_returns_none(self):
        """100% Bad 质量：自控率 INCONCLUSIVE（无有效 MODE 数据）."""
        n = 100
        mode = [1] * n
        bundle = self._make_full_bad_bundle("auto_mode_rate", {"mode": mode})
        result = AutoModeRateCalculator().calculate(bundle)
        assert result.value is None

    def test_good_value_rate_below_threshold_inconclusive(self):
        """好值率 < 20% → INCONCLUSIVE（设计文档 §4.1 约束）.

        100% Bad 时 good_value_rate=0%，触发 INCONCLUSIVE_THRESHOLD=20.0。
        """
        n = 100
        pv = [50.0] * n
        validity = {"pv_valid": [False] * n}
        bundle = make_bundle(
            {"pv": pv},
            validity=validity,
            metric_code="good_value_rate",
        )
        result = GoodValueRateCalculator().calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "good_value_rate_below_threshold"
        assert result.details["good_value_rate"] == 0.0


# ---------------------------------------------------------------------------
# 3. 低频振荡（周期 > 60s）
# ---------------------------------------------------------------------------


class TestLowFrequencyOscillation:
    """低频振荡：周期 > 60s，采样窗口内仅含 < 2 个完整周期.

    设计预期：振荡率计算器要求 MIN_ZERO_CROSSINGS=4（至少 2 个完整周期）。
    若窗口内零交叉点不足，返回 0（非振荡）。
    """

    def test_period_120s_in_60s_window_no_oscillation(self):
        """周期 120s + 窗口 60s：仅含 0.5 个周期 → 0 个零交叉 → 非振荡."""
        n = 60  # 60s 窗口，1Hz 采样
        sp = [50.0] * n
        # 周期 120s → 角频率 ω = 2π/120
        # E(t) = 10 * sin(2π*t/120)，在 0~60s 内仅完成半个周期
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * t / 120) for t in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        result = OscillationRateCalculator().calculate(bundle)
        # 半个周期 → 1 个零交叉（t=60 处过零，但 t<60 未发生）或 0 个
        # MIN_ZERO_CROSSINGS=4，返回 0
        assert result.value == 0.0
        assert result.details["is_oscillating"] is False

    def test_period_90s_in_60s_window_no_oscillation(self):
        """周期 90s + 窗口 60s：仅含 0.67 个周期 → ≤1 个零交叉 → 非振荡."""
        n = 60
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * t / 90) for t in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        result = OscillationRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["is_oscillating"] is False

    def test_period_70s_in_60s_window_no_oscillation(self):
        """周期 70s + 窗口 60s：仅含 0.86 个周期 → ≤1 个零交叉 → 非振荡."""
        n = 60
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * t / 70) for t in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        result = OscillationRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["is_oscillating"] is False

    def test_period_120s_in_600s_window_oscillation_detected(self):
        """周期 120s + 窗口 600s：含 5 个完整周期 → 10 个零交叉 → 可识别振荡."""
        n = 600  # 600s 窗口，1Hz 采样
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * t / 120) for t in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        result = OscillationRateCalculator().calculate(bundle)
        # 正弦波零交叉规律，应被识别为振荡
        assert result.details["is_oscillating"] is True
        assert result.details["zero_crossings"] >= 8  # 5 周期 → 10 零交叉
        # 振荡周期应接近 120s（2 × 平均半周期）
        assert 100.0 <= result.details["oscillation_period"] <= 140.0

    def test_period_65s_boundary_just_above_60s(self):
        """周期 65s（刚超过 60s）+ 窗口 60s：仍 <1 个周期 → 非振荡.

        验证 60s 临界边界：65s 周期在 60s 窗口内未完成完整周期。
        """
        n = 60
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * t / 65) for t in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        result = OscillationRateCalculator().calculate(bundle)
        # 在 [0, 60] 内 sin(2π*t/65) 仅在 t≈32.5 处过零 1 次
        # MIN_ZERO_CROSSINGS=4，返回 0
        assert result.value == 0.0


# ---------------------------------------------------------------------------
# 4. OP 饱和临界值（98 / 99 / 100）
# ---------------------------------------------------------------------------


class TestOpSaturationBoundaryValues:
    """OP 饱和临界值 98 / 99 / 100（默认 epsilon=2 → op_high-epsilon=98）.

    设计依据：saturation.py
        DEFAULT_OP_HIGH = 100.0
        DEFAULT_EPSILON = 2.0
        判定条件：op_val >= op_high - epsilon → 100 - 2 = 98
                  op_val <= op_low + epsilon → 0 + 2 = 2
    """

    @pytest.mark.parametrize("op_value", [98.0, 99.0, 99.5, 100.0])
    def test_op_at_or_above_threshold_is_saturated(self, op_value):
        """OP >= 98（op_high-epsilon）→ 高饱和."""
        n = 100
        mode = [1] * n  # 全自动
        op = [op_value] * n
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"

    @pytest.mark.parametrize("op_value", [97.99, 97.0, 50.0, 3.0])
    def test_op_below_high_threshold_not_high_saturated(self, op_value):
        """OP < 98 → 不触发高饱和（50% 在中间，3% 未到低限 2）."""
        n = 100
        mode = [1] * n
        op = [op_value] * n
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        # OP=3.0 仍 > 2（op_low+epsilon），不饱和
        # OP=50.0 中间区域，不饱和
        # OP=97.0/97.99 < 98，不饱和
        assert result.value == 0.0
        assert result.details["saturation_type"] == "NONE"

    def test_op_boundary_exactly_at_threshold(self):
        """OP=98.0 恰好等于 op_high-epsilon → 触发饱和（>= 比较）."""
        n = 100
        mode = [1] * n
        op = [98.0] * n  # 恰好 100 - 2 = 98，>= 触发
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"

    def test_op_just_below_threshold(self):
        """OP=97.99 刚好低于阈值 → 不饱和（验证 >= 而非 >）."""
        n = 100
        mode = [1] * n
        op = [97.99] * n  # < 98
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        assert result.value == 0.0

    @pytest.mark.parametrize("op_value", [0.0, 1.0, 1.99, 2.0])
    def test_op_low_saturation_boundary(self, op_value):
        """OP <= 2（op_low+epsilon）→ 低饱和.

        边界值：OP=2.0 恰好等于阈值 → 触发（<=）；
                OP=1.99 < 2 → 触发；OP=0/1 → 触发。
        """
        n = 100
        mode = [1] * n
        op = [op_value] * n
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "LOW"

    def test_op_just_above_low_threshold(self):
        """OP=2.01 刚好高于低限阈值 → 不饱和（验证 <= 而非 <）."""
        n = 100
        mode = [1] * n
        op = [2.01] * n  # > 2
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["saturation_type"] == "NONE"

    def test_mixed_threshold_and_normal(self):
        """混合：30% OP=98（饱和）+ 70% OP=50（正常）→ rate=30%."""
        n = 100
        mode = [1] * n
        op = [98.0] * 30 + [50.0] * 70
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        # 30 个采样点饱和，70 个不饱和，auto_duration=100s
        # sat_high_duration=30s, rate=30/100*100=30
        assert abs(result.value - 30.0) < 0.5
        assert result.details["saturation_type"] == "HIGH"

    def test_op_exactly_98_with_custom_epsilon_5(self):
        """自定义 epsilon=5 时阈值变为 95，OP=98 仍触发饱和."""
        n = 100
        mode = [1] * n
        op = [98.0] * n
        # 通过 CONFIG 信号传入自定义 epsilon
        signals = {"mode": mode, "op": op, "saturation_epsilon": [5.0]}
        from app.contracts.data_types import DataLineage, MetricDataBundle

        from .conftest import make_data_block

        block = make_data_block(signals)
        bundle = MetricDataBundle(
            metric_code="saturation_rate",
            data_block=block,
            mask_expression="",
            masked_indices=list(range(n)),
            lineage=DataLineage(
                sampling_freq="1s",
                tag_group="BASE",
                valid_rate=1.0,
            ),
        )
        result = SaturationRateCalculator().calculate(bundle)
        # epsilon=5, op_high-epsilon=95, OP=98 >= 95 → 饱和
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"
        assert result.details["epsilon"] == 5.0

    def test_default_epsilon_constant_value(self):
        """DEFAULT_EPSILON 常量值校验（防止意外修改）."""
        assert DEFAULT_EPSILON == 2.0
        assert DEFAULT_OP_HIGH == 100.0
        assert DEFAULT_OP_LOW == 0.0

    def test_op_98_99_100_mixed_all_saturated(self):
        """OP=98/99/100 三种临界值混合 → 全部饱和（rate=100%）."""
        n = 99
        mode = [1] * n
        # 33 个 98 + 33 个 99 + 33 个 100
        op = [98.0] * 33 + [99.0] * 33 + [100.0] * 33
        bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
        result = SaturationRateCalculator().calculate(bundle)
        assert result.value == 100.0
        assert result.details["saturation_type"] == "HIGH"
        # 饱和时长应等于全部 auto 时长
        assert result.details["sat_high_duration_s"] == pytest.approx(
            result.details["auto_duration_s"]
        )
