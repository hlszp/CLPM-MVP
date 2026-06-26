"""稳定率计算器单元测试（算法说明 §4.3）.

测试用例覆盖：
- 零偏差（σ=0 → S=100）
- 小偏差（高稳定率）
- 大偏差（低稳定率）
- 振荡率修正
- 空数据
- 依赖注入（oscillation_rate）

设计依据：算法说明 §4.3；GB/T 44693.2-2024 附录 B.5
"""

from __future__ import annotations

import math

import pytest

from app.contracts.data_types import ConfidenceLevel, MetricResult, DataLineage
from app.services.metric_calculator.stability import StabilityRateCalculator

from .conftest import make_bundle


def _make_osc_result(rate: float) -> MetricResult:
    """构造振荡率 MetricResult 用于依赖注入。"""
    return MetricResult(
        metric_code="oscillation_rate",
        value=rate,
        confidence_level="A",
        lineage=DataLineage(),
        details={},
    )


class TestStabilityRate:
    """StabilityRateCalculator 测试。"""

    def test_zero_error_full_stability(self):
        """PV=SP（σ=0）→ S=100。"""
        n = 100
        val = [50.0] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(0.0)})
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_small_error_high_stability(self):
        """小偏差（σ 小）→ 高稳定率。"""
        n = 100
        sp = [50.0] * n
        pv = [50.0 + 0.5 for _ in range(n)]  # 恒定偏差 0.5
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(0.0)})
        result = calc.calculate(bundle)
        # σ=0（偏差恒定）→ S=100
        assert result.value == 100.0

    def test_large_error_low_stability(self):
        """大偏差波动 → 低稳定率。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 20.0 * math.sin(i * 0.1) for i in range(n)]  # 大幅波动
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(0.0)})
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value < 80.0  # 大波动 → 低稳定率

    def test_oscillation_reduces_stability(self):
        """振荡率 > 0 → 稳定率降低。"""
        n = 100
        val = [50.0] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(50.0)})
        result = calc.calculate(bundle)
        # σ=0, osc=50% → S = 100 * (1-0.5) = 50
        assert result.value == 50.0

    def test_full_oscillation_zero_stability(self):
        """振荡率=100% → 稳定率=0。"""
        n = 100
        val = [50.0] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(100.0)})
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_no_dependency_defaults_zero_osc(self):
        """无振荡率依赖 → 默认 osc=0。"""
        n = 100
        val = [50.0] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        # 不注入依赖
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_insufficient_data_inconclusive(self):
        """数据不足（< 2 点）→ INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [50.0], "sp": [50.0]}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_value_clamped_0_100(self):
        """值限制在 [0, 100]。"""
        n = 200
        sp = [50.0] * n
        # 极大波动
        pv = [50.0 + 80.0 * ((-1) ** i) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(0.0)})
        result = calc.calculate(bundle)
        assert 0.0 <= result.value <= 100.0
