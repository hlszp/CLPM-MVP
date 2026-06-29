"""准确率计算器单元测试（算法说明 §4.4）.

测试用例覆盖：
- 正常偏差（PV 略偏离 SP）
- 零偏差（PV=SP → A=100）
- 大偏差（偏差超 e_max）
- 空数据（INCONCLUSIVE）
- 边界：e_max=0
- 可信度判定（valid_rate 影响）
- CONFIG 中 e_max 配置

设计依据：算法说明 §4.4；GB/T 44693.2-2024 附录 B.3
"""

from __future__ import annotations

from app.contracts.data_types import ConfidenceLevel
from app.services.metric_calculator.accuracy import AccuracyRateCalculator

from .conftest import make_bundle


class TestAccuracyRate:
    """AccuracyRateCalculator 测试。"""

    def test_zero_error_returns_100(self, zero_error_bundle):
        """PV=SP 零偏差 → 准确率 100%。"""
        calc = AccuracyRateCalculator()
        result = calc.calculate(zero_error_bundle)
        assert result.value == 100.0
        assert result.confidence_level == ConfidenceLevel.A.value

    def test_normal_small_error(self, normal_pv_sp_bundle):
        """PV 恒定偏离 SP 0.5（归一化），e_max=5 → r=0.1，A 接近 100。"""
        calc = AccuracyRateCalculator()
        result = calc.calculate(normal_pv_sp_bundle)
        # r = 0.5/5 = 0.1, decay = 1 - 1/e^0.1 ≈ 0.0952
        # A = (1 - 0.1 * 0.0952) * 100 ≈ 99.05
        assert result.value is not None
        assert 99.0 < result.value < 100.0
        assert result.details["mean_abs_error"] == 0.5

    def test_large_error_low_accuracy(self, large_error_bundle):
        """偏差 80 远超 e_max=5 → r=16，A 接近 0。"""
        calc = AccuracyRateCalculator()
        result = calc.calculate(large_error_bundle)
        assert result.value is not None
        assert result.value < 5.0  # 大偏差 → 低准确率
        assert result.details["r"] > 1.0

    def test_empty_data_inconclusive(self, empty_bundle):
        """空数据 → INCONCLUSIVE。"""
        calc = AccuracyRateCalculator()
        result = calc.calculate(empty_bundle)
        assert result.value is None
        assert result.confidence_level == ConfidenceLevel.E.value

    def test_custom_e_max_from_config(self):
        """CONFIG 信号中 e_max 配置生效。"""
        n = 50
        pv = [55.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"pv": pv, "sp": sp, "e_max": [10.0] * n},
            metric_code="accuracy_rate",
        )
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        # e_max=10, mean_abs_error=5, r=0.5
        assert result.value is not None
        assert result.details["e_max"] == 10.0

    def test_confidence_level_based_on_valid_rate(self):
        """valid_rate < 0.95 → 可信度降级。"""
        n = 100
        pv = [50.0] * n
        sp = [50.0] * n
        # 50% 有效
        validity = {"pv_valid": [True] * 50 + [False] * 50, "sp_valid": [True] * n}
        bundle = make_bundle(
            {"pv": pv, "sp": sp},
            validity,
            mask_expression="pv_valid && sp_valid",
            metric_code="accuracy_rate",
        )
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        # valid_rate = 50/100 = 0.5 → D 级
        assert result.confidence_level == ConfidenceLevel.D.value

    def test_value_clamped_to_100(self):
        """负偏差时值仍限制在 [0, 100]。"""
        n = 50
        pv = [50.0] * n
        sp = [55.0] * n  # 负偏差
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert 0.0 <= result.value <= 100.0

    def test_single_point(self):
        """单点数据可计算（不报错）。"""
        bundle = make_bundle({"pv": [55.0], "sp": [50.0]}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
