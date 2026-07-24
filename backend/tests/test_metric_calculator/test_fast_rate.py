"""快速率计算器单元测试（算法说明 §4.5）.

测试用例覆盖：
- T ≤ T'（快速率=100）
- T > T'（指数衰减）
- T=0（已稳态，快速率=100）
- T' 无效（INCONCLUSIVE）
- 依赖注入
- P2 抗扰性分析分支（开关控制、零回归、扰动覆盖、回落 ARMA）

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 B.4
"""

from __future__ import annotations

import math

import pytest

from app.contracts.data_types import DataLineage, MetricResult
from app.services import algorithm_config as ac
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
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(30.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_actual_equals_ideal_returns_100(self):
        """T = T' → 快速率 100（边界）。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(60.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value == 100.0

    def test_actual_above_ideal_exponential_decay(self):
        """T > T' → F = 1/e^((T-T')/T') × 100。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(120.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        # ratio = (120-60)/60 = 1.0, F = 1/e^1 × 100 ≈ 36.79
        expected = round(1.0 / math.exp(1.0) * 100.0, 2)
        assert result.value == expected

    def test_zero_settling_time_returns_100(self):
        """T=0（已稳态）→ 快速率 100。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(0.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["reason"] == "already_stable"

    def test_invalid_ideal_inconclusive(self):
        """T' 无效（None 或 ≤ 0）→ INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(30.0),
                "ideal_settling_time": _make_ideal_result(0.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value is None

    def test_missing_ideal_dependency(self):
        """缺少 ideal_settling_time 依赖 → INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(30.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value is None

    def test_large_ratio_low_rate(self):
        """T >> T' → 快速率接近 0。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(600.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        # ratio = 9, F = 1/e^9 × 100 ≈ 0.012
        assert result.value is not None
        assert result.value < 1.0

    def test_value_clamped_0_100(self):
        """值限制在 [0, 100]。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(30.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert 0.0 <= result.value <= 100.0


# ---------------------------------------------------------------------------
# P2 抗扰性分析分支测试
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_algo_config_cache():
    """每个测试前后保存/恢复算法参数缓存，避免污染其他测试。"""
    saved = dict(ac._merged_cache)
    ac._merged_cache = {}
    yield
    ac._merged_cache = saved


def _enable_anti_disturbance() -> None:
    """配置 fast_rate STABLE 控制类型开启抗扰分析。"""
    ac.apply_runtime({"fast_rate": {"STABLE": {"anti_disturbance_enabled": True}}})


class TestFastRateAntiDisturbance:
    """P2 抗扰性分析分支测试（开关控制、零回归、扰动覆盖、回落 ARMA）。"""

    def test_disabled_zero_regression(self, reset_algo_config_cache):
        """开关关闭（默认）→ 走原 ARMA 逻辑，details.source='arma'。"""
        ac.apply_runtime({})  # 仅用默认值（anti_disturbance_enabled=False）
        n = 10
        bundle = make_bundle({"pv": [50.0] * n, "sp": [50.0] * n}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(120.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        # 与 test_actual_above_ideal_exponential_decay 一致
        expected = round(1.0 / math.exp(1.0) * 100.0, 2)
        assert result.value == expected
        assert result.details["source"] == "arma"

    def test_enabled_with_disturbance_uses_recovery_time(self, reset_algo_config_cache):
        """开关开启 + 检测到扰动 → 用恢复时间替代 ARMA，source='disturbance'。"""
        _enable_anti_disturbance()
        n = 100
        # PV 在 30-39 偏离 SP 10，其余贴近 SP → 扰动恢复时间约 15s
        sp = [50.0] * n
        pv = [50.0] * 30 + [60.0] * 10 + [50.0] * 60
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="fast_rate")
        calc = FastRateCalculator()
        # ARMA 稳态时间 120s（若无抗扰会指数衰减到 ~36.8%）
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(120.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        # 扰动恢复时间 ~15s < ideal_t=60 → 快速率 100
        assert result.value == 100.0
        assert result.details["source"] == "disturbance"
        assert result.details["disturbance_count"] == 1
        # actual_settling_time 为恢复时间（~15s），非 ARMA 的 120s
        assert result.details["actual_settling_time"] < 60.0

    def test_enabled_no_disturbance_falls_back_arma(self, reset_algo_config_cache):
        """开关开启 + 无扰动 → 回落 ARMA，source='arma_fallback'。"""
        _enable_anti_disturbance()
        n = 100
        # PV 全程贴近 SP（微波动），无扰动可检测
        sp = [50.0] * n
        pv = [50.0 + (0.1 if i % 2 == 0 else -0.1) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(120.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        # 回落 ARMA：120 > 60 → 指数衰减
        expected = round(1.0 / math.exp(1.0) * 100.0, 2)
        assert result.value == expected
        assert result.details["source"] == "arma_fallback"
        assert result.details["reason"] == "no_disturbance_detected"

    def test_enabled_overrides_settling_inconclusive(self, reset_algo_config_cache):
        """开关开启 + 有扰动 + settling_time INCONCLUSIVE → 仍用恢复时间（跳过 INCONCLUSIVE）。"""
        _enable_anti_disturbance()
        n = 100
        sp = [50.0] * n
        pv = [50.0] * 30 + [60.0] * 10 + [50.0] * 60
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="fast_rate")
        calc = FastRateCalculator()
        # settling_time 返回 INCONCLUSIVE（value=None）
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result_inconclusive(),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        # 扰动覆盖跳过 settling_time_inconclusive，仍出值
        assert result.value == 100.0
        assert result.details["source"] == "disturbance"


def _make_settling_result_inconclusive() -> MetricResult:
    """构造 INCONCLUSIVE 稳态时间 MetricResult（value=None）。"""
    return MetricResult(
        metric_code="settling_time",
        value=None,
        confidence_level="E",
        lineage=DataLineage(),
        details={"reason": "arma_identification_failed"},
    )
