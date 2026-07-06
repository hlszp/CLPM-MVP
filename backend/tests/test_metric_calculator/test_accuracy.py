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

    def test_normal_small_error(self):
        """偏差小幅波动（数据驱动 e_max），大部分点偏差 0.1，一点偏差 1.0 → A 接近 100。

        v2.1 数据驱动 e_max：e_max = max(|E|) - mean(|E|) = 1.0 - 0.109 = 0.891
        r = 0.109 / 0.891 ≈ 0.122 → A ≈ 98.6
        """
        n = 100
        sp = [50.0] * n
        # 99 个点偏差 0.1，最后一个点偏差 1.0，制造离散度
        pv = [50.1] * (n - 1) + [51.0]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert 95.0 < result.value < 100.0
        assert result.details["mean_abs_error"] == 0.109

    def test_large_error_low_accuracy(self):
        """大偏差波动（数据驱动 e_max）→ r > 1 → A 接近 0。

        v2.1 数据驱动 e_max：errors=[78,79,77,80,76], mean=78, max=80
        e_max = 80 - 78 = 2, r = 78/2 = 39 → A ≈ 0
        """
        n = 100
        sp = [10.0] * n
        # 偏差在 76-80 间波动，mean=78, max=80, e_max=2, r=39
        pv = [88.0, 89.0, 87.0, 90.0, 86.0] * 20
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="accuracy_rate")
        calc = AccuracyRateCalculator()
        result = calc.calculate(bundle)
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
