"""快速率计算器单元测试（算法说明 §4.5）.

测试用例覆盖：
- T ≤ T'（快速率=100）
- T > T'（指数衰减）
- T=0（已稳态，快速率=100）
- T' 无效（INCONCLUSIVE）
- 依赖注入

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 B.4
"""

from __future__ import annotations

import math

import pytest

from app.contracts.data_types import DataLineage, MetricResult
from app.services.metric_calculator.fast_rate import FastRateCalculator

from .conftest import make_bundle


def _make_settling_result(t: float) -> MetricResult:
    """构造稳态时间 MetricResult。"""
    return MetricResult(
        metric_code="settling_time",
        value=t,
        confidence_level="A",
        lineage=DataLineage(),
        details={"actual_settling_time": t},
    )


def _make_ideal_result(t: float) -> MetricResult:
    """构造理想稳态时间 MetricResult。"""
    return MetricResult(
        metric_code="ideal_settling_time",
        value=t,
        confidence_level="A",
        lineage=DataLineage(),
        details={},
    )


class TestFastRate:
    """FastRateCalculator 测试。"""

    def test_actual_below_ideal_returns_100(self):
        """T < T' → 快速率 100。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(30.0),
            "ideal_settling_time": _make_ideal_result(60.0),
        })
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_actual_equals_ideal_returns_100(self):
        """T = T' → 快速率 100（边界）。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(60.0),
            "ideal_settling_time": _make_ideal_result(60.0),
        })
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_actual_above_ideal_exponential_decay(self):
        """T > T' → F = 1/e^((T-T')/T') × 100。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(120.0),
            "ideal_settling_time": _make_ideal_result(60.0),
        })
        result = calc.calculate(bundle)
        # ratio = (120-60)/60 = 1.0, F = 1/e^1 × 100 ≈ 36.79
        expected = round(1.0 / math.exp(1.0) * 100.0, 2)
        assert result.value == expected

    def test_zero_settling_time_returns_100(self):
        """T=0（已稳态）→ 快速率 100。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(0.0),
            "ideal_settling_time": _make_ideal_result(60.0),
        })
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["reason"] == "already_stable"

    def test_invalid_ideal_inconclusive(self):
        """T' 无效（None 或 ≤ 0）→ INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(30.0),
            "ideal_settling_time": _make_ideal_result(0.0),
        })
        result = calc.calculate(bundle)
        assert result.value is None

    def test_missing_ideal_dependency(self):
        """缺少 ideal_settling_time 依赖 → INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(30.0),
        })
        result = calc.calculate(bundle)
        assert result.value is None

    def test_large_ratio_low_rate(self):
        """T >> T' → 快速率接近 0。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(600.0),
            "ideal_settling_time": _make_ideal_result(60.0),
        })
        result = calc.calculate(bundle)
        # ratio = 9, F = 1/e^9 × 100 ≈ 0.012
        assert result.value is not None
        assert result.value < 1.0

    def test_value_clamped_0_100(self):
        """值限制在 [0, 100]。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies({
            "settling_time": _make_settling_result(30.0),
            "ideal_settling_time": _make_ideal_result(60.0),
        })
        result = calc.calculate(bundle)
        assert 0.0 <= result.value <= 100.0
