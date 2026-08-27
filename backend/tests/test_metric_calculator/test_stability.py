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

from app.contracts.data_types import DataLineage, MetricDataBundle, MetricResult
from app.services.algorithm_config import apply_runtime
from app.services.metric_calculator.stability import StabilityRateCalculator

from .conftest import make_bundle


def _make_osc_result(rate: float, is_oscillating: bool = True) -> MetricResult:
    """构造振荡率 MetricResult 用于依赖注入。"""
    return MetricResult(
        metric_code="oscillation_rate",
        value=rate,
        confidence_level="A",
        lineage=DataLineage(),
        details={"is_oscillating": is_oscillating},
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

    def test_non_oscillating_rate_does_not_reduce_stability(self):
        """is_oscillating=False 时不修正稳定率（P0 修复：非振荡回路误扣）。

        振荡率计算器恒输出 min(S_A,S_B)×100，未判振荡时该值仅为相似率
        连续值（<40%），不应作为 (1-Osc) 因子扣减稳定率；修正仅在
        is_oscillating=True 时生效。振荡率数值仍透传到 details 供展示。
        """
        n = 100
        val = [50.0] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(30.0, is_oscillating=False)})
        result = calc.calculate(bundle)
        # σ=0, osc=30% 但未判振荡 → S=100，不扣减
        assert result.value == 100.0
        assert result.details["osc_factor"] == 1.0
        assert result.details["oscillation_rate"] == 30.0

    def test_non_oscillating_full_rate_no_short_circuit(self):
        """is_oscillating=False 且振荡率=100% 时不触发稳定率归零短路。"""
        n = 100
        val = [50.0] * n
        bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(100.0, is_oscillating=False)})
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details.get("reason") != "osc_too_high"

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

    def test_huge_std_no_overflow(self):
        """大量程/大 σ 不溢出：normalized_std≈2e5 时 exp(-x) 稳定返回 0。

        修复前 1.0/math.exp(normalized_std) 在 normalized_std > ~709 时
        抛 OverflowError；改为 math.exp(-x) 后数学等价且数值稳定。
        """
        n = 200
        sp = [0.0] * n
        pv = [1e6 * ((-1) ** i) for i in range(n)]  # σ≈1e6，normalized_std≈2e5
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="stability_rate")
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(0.0)})
        result = calc.calculate(bundle)
        assert result.value == 0.0


@pytest.fixture()
def _stability_overrides():
    """测试后复位算法参数缓存到默认值。"""
    yield
    apply_runtime({})


class TestStabilityConfigParams:
    """配置链参数：decay_ratio / band_ratio / band_in_score_enabled。"""

    def test_decay_ratio_configurable(self, _stability_overrides):
        """decay_ratio 覆盖后衰减基准变化：d=0.10 → S=100·e^(−σ/10)。"""
        apply_runtime({"stability_rate": {"STABLE": {"decay_ratio": 0.10}}})
        bundle = make_bundle(
            {"pv": [52.0, 48.0, 52.0, 48.0], "sp": [50.0] * 4},
            metric_code="stability_rate",
        )
        calc = StabilityRateCalculator()
        result = calc.calculate(bundle)
        # σ(ddof=1)=sqrt(16/3)，U=100，d=0.10 → σ/(d·U)=2.3094/10
        expected = round(100.0 * math.exp(-math.sqrt(16.0 / 3.0) / 10.0), 2)
        assert result.value == expected
        assert result.details["decay_ratio"] == 0.10

    def test_band_in_rate_in_details_default(self):
        """默认模式：分值仍为指数公式，details 附带石化惯例带内率。"""
        # E=[+0.5,-0.5]，band=1.0（0.01×100）→ 全部带内 → band_in_rate=100
        bundle = make_bundle(
            {"pv": [50.5, 49.5, 50.5, 49.5], "sp": [50.0] * 4},
            metric_code="stability_rate",
        )
        calc = StabilityRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["score_mode"] == "exponential"
        assert result.details["band_in_rate"] == 100.0

        # E=[+2,-2]，全部带外 → band_in_rate=0
        bundle2 = make_bundle(
            {"pv": [52.0, 48.0, 52.0, 48.0], "sp": [50.0] * 4},
            metric_code="stability_rate",
        )
        result2 = calc.calculate(bundle2)
        assert result2.details["band_in_rate"] == 0.0
        # 指数公式不受影响（d 默认 0.05）
        expected = round(100.0 * math.exp(-math.sqrt(16.0 / 3.0) / 5.0), 2)
        assert result2.value == expected

    def test_band_ratio_configurable(self, _stability_overrides):
        """band_ratio 覆盖后平稳带变宽：0.05 → band=5，E=±2 全部带内。"""
        apply_runtime({"stability_rate": {"STABLE": {"band_ratio": 0.05}}})
        bundle = make_bundle(
            {"pv": [52.0, 48.0, 52.0, 48.0], "sp": [50.0] * 4},
            metric_code="stability_rate",
        )
        calc = StabilityRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["band_ratio"] == 0.05
        assert result.details["band_in_rate"] == 100.0

    def test_band_in_score_mode(self, _stability_overrides):
        """band_in_score_enabled=True：分值=带内率，不乘振荡修正。"""
        apply_runtime({"stability_rate": {"STABLE": {"band_in_score_enabled": True}}})
        bundle = make_bundle(
            {"pv": [50.5, 49.5, 52.0, 48.0], "sp": [50.0] * 4},
            metric_code="stability_rate",
        )
        calc = StabilityRateCalculator()
        calc.with_dependencies({"oscillation_rate": _make_osc_result(100.0)})
        result = calc.calculate(bundle)
        # band=1.0：|E|=[0.5,0.5,2,2] → 2/4 带内 → 50.0；osc=100% 不短路
        assert result.value == 50.0
        assert result.details["score_mode"] == "band_in"


def _make_sp_step_bundle() -> MetricDataBundle:
    """构造含 SP 阶跃的数据：0-14 稳态(SP=50)，15 起 SP 阶跃到 70 并跟踪 20 点，35-39 新稳态。"""
    sp = [50.0] * 15 + [70.0] * 25
    pv = (
        [50.0] * 15
        + [50.0 + 20.0 * (k + 1) / 20 for k in range(20)]  # 跟踪暂态：50→70 线性爬升
        + [70.0] * 5
    )
    return make_bundle({"pv": pv, "sp": sp}, metric_code="stability_rate")


class TestSpStepExclusion:
    """SP 阶跃剔除（sp_step_exclusion_enabled，2026-08-27 起默认开启）。"""

    def test_default_enabled_excludes_tracking_transient(self):
        """默认开启：剔除 SP 阶跃跟踪暂态后剩余均为稳态段（E=0）→ S=100。

        默认窗 60 点覆盖阶跃后全部 25 点（共 40 点），剩 15 点稳态。
        """
        calc = StabilityRateCalculator()
        result = calc.calculate(_make_sp_step_bundle())
        assert result.value == 100.0
        assert result.details["sp_step_exclusion"] is True
        assert result.details["sp_steps_detected"] == 1
        assert result.details["sp_excluded_points"] == 25
        assert result.details["sample_count"] == 15  # 40 - 25

    def test_explicit_disable_counts_transient(self, _stability_overrides):
        """显式关闭：跟踪暂态计入 σ，稳定率被拉低，details 标记未启用。"""
        apply_runtime({"stability_rate": {"STABLE": {"sp_step_exclusion_enabled": False}}})
        calc = StabilityRateCalculator()
        result = calc.calculate(_make_sp_step_bundle())
        assert result.value is not None
        assert result.value < 80.0  # 跟踪暂态大偏差 → 低稳定率
        assert result.details["sp_step_exclusion"] is False
        assert result.details["sp_excluded_points"] == 0

    def test_enabled_excludes_tracking_transient(self, _stability_overrides):
        """开启后剔除跟踪窗（20 点），剩余均为稳态段（E=0）→ S=100。"""
        apply_runtime(
            {
                "stability_rate": {
                    "STABLE": {"sp_step_exclusion_enabled": True, "sp_tracking_window": 20}
                }
            }
        )
        calc = StabilityRateCalculator()
        result = calc.calculate(_make_sp_step_bundle())
        assert result.value == 100.0
        assert result.details["sp_step_exclusion"] is True
        assert result.details["sp_steps_detected"] == 1
        assert result.details["sp_excluded_points"] == 20
        assert result.details["sample_count"] == 20  # 40 - 20

    def test_enabled_constant_sp_no_exclusion(self, _stability_overrides):
        """开启但 SP 恒定：无阶跃可检，不剔除任何点。"""
        apply_runtime({"stability_rate": {"STABLE": {"sp_step_exclusion_enabled": True}}})
        bundle = make_bundle(
            {"pv": [52.0, 48.0, 52.0, 48.0], "sp": [50.0] * 4},
            metric_code="stability_rate",
        )
        calc = StabilityRateCalculator()
        result = calc.calculate(bundle)
        expected = round(100.0 * math.exp(-math.sqrt(16.0 / 3.0) / 5.0), 2)
        assert result.value == expected
        assert result.details["sp_steps_detected"] == 0
        assert result.details["sp_excluded_points"] == 0

    def test_all_excluded_inconclusive(self, _stability_overrides):
        """阶跃剔后剩余点 < 2 → INCONCLUSIVE（原因 insufficient_data_after_sp_exclusion）。"""
        apply_runtime({"stability_rate": {"STABLE": {"sp_step_exclusion_enabled": True}}})
        # 第 1 个点后即阶跃，默认窗 60 点覆盖全部剩余 → 仅剩 1 点
        bundle = make_bundle(
            {"pv": [50.0] + [55.0 + i for i in range(9)], "sp": [50.0] + [70.0] * 9},
            metric_code="stability_rate",
        )
        calc = StabilityRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "insufficient_data_after_sp_exclusion"
        assert result.details["sp_steps_detected"] == 1
        assert result.details["sp_excluded_points"] == 9
