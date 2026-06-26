"""好值率计算器单元测试（算法说明 §4.1）.

测试用例覆盖：
- 全部好值（rate=100）
- 部分好值
- 好值率 < 20%（INCONCLUSIVE）
- 空数据
- quality_summary 有值 vs 无值（回退 pv_valid）
- 可信度判定

设计依据：算法说明 §4.1；GB/T 44693.2-2024 附录 F.6
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import ConfidenceLevel, QualitySummary
from app.services.metric_calculator.good_value import GoodValueRateCalculator

from .conftest import make_bundle


class TestGoodValueRate:
    """GoodValueRateCalculator 测试。"""

    def test_all_good_value(self):
        """全部好值 → rate=100。"""
        n = 100
        bundle = make_bundle(
            {"pv": [50.0] * n},
            metric_code="good_value_rate",
            quality_summary=QualitySummary(
                total_count=n, valid_count=n, good_value_rate=1.0
            ),
        )
        calc = GoodValueRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_partial_good_value(self):
        """80% 好值 → rate=80。"""
        n = 100
        bundle = make_bundle(
            {"pv": [50.0] * n},
            metric_code="good_value_rate",
            quality_summary=QualitySummary(
                total_count=n, valid_count=80, good_value_rate=0.8
            ),
        )
        calc = GoodValueRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 80.0

    def test_below_threshold_inconclusive(self):
        """好值率 < 20% → INCONCLUSIVE。"""
        n = 100
        bundle = make_bundle(
            {"pv": [50.0] * n},
            metric_code="good_value_rate",
            quality_summary=QualitySummary(
                total_count=n, valid_count=10, good_value_rate=0.1
            ),
        )
        calc = GoodValueRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.confidence_level == ConfidenceLevel.E.value

    def test_empty_data_inconclusive(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({}, metric_code="good_value_rate")
        calc = GoodValueRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_fallback_to_pv_valid(self):
        """quality_summary.good_value_rate=None → 回退 pv_valid 计算。"""
        n = 100
        validity = {"pv_valid": [True] * 80 + [False] * 20}
        bundle = make_bundle(
            {"pv": [50.0] * n},
            validity,
            metric_code="good_value_rate",
            quality_summary=QualitySummary(
                total_count=n, valid_count=80, good_value_rate=None
            ),
        )
        calc = GoodValueRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 80.0
        assert result.details["source"] == "pv_valid"

    def test_threshold_boundary(self):
        """好值率恰好 20% → 不触发 INCONCLUSIVE（>= 20）。"""
        n = 100
        bundle = make_bundle(
            {"pv": [50.0] * n},
            metric_code="good_value_rate",
            quality_summary=QualitySummary(
                total_count=n, valid_count=20, good_value_rate=0.2
            ),
        )
        calc = GoodValueRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 20.0
        assert result.confidence_level != ConfidenceLevel.E.value

    def test_value_rounded_2_decimals(self):
        """值四舍五入到 2 位小数。"""
        n = 3
        bundle = make_bundle(
            {"pv": [50.0] * n},
            metric_code="good_value_rate",
            quality_summary=QualitySummary(
                total_count=3, valid_count=2, good_value_rate=2 / 3
            ),
        )
        calc = GoodValueRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == round(2 / 3 * 100, 2)
