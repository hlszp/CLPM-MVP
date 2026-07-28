"""附录 B.4 快速率 公式级验证（任务 G2）.

公式事实来源：算法说明 §4.5（对齐 GB/T 44693.2-2024 附录 B.4）：
    F = 100%                          当 T ≤ T'
    F = 1/e^((T-T')/T') × 100%        当 T > T'

P0-1 三语义分流（实现 fast_rate.py）：
    already_stable（T≤0）→ 100
    never_settles（窗口内不衰减）→ 以 Green 函数窗口长度代入指数衰减（不得满分）
    identification_failed → INCONCLUSIVE

扰动覆盖分支（P2，anti_disturbance_enabled=True）：
    检测到扰动 → 以扰动平均恢复时间 t_disturb 替代 ARMA 稳态时间代入同一公式。
    本套件扰动场景手算（1s 采样，n=40）：
        PV=[50]*20 + [60]*10 + [50]*10，SP=50
        偏差 pstdev = sqrt(750/40) = 4.330127，band = 2σ = 8.660254
        扰动段 i=20..29（|E|=10 > band），持续 10 s ≥ min_disturbance_duration 3 s
        恢复确认：i=30..34 连续 5 点带内 → t_disturb = Σdurations[20..34] = 15.0 s
"""

from __future__ import annotations

import math

import pytest

from app.services import algorithm_config as ac
from app.services.metric_calculator.fast_rate import FastRateCalculator

from .g2_helpers import (  # noqa: F401
    make_bundle,
    make_metric_result,
    reset_algo_config_cache,
)


def _settling(t: float):
    return make_metric_result("settling_time", t, details={"actual_settling_time": t})


def _ideal(t: float):
    return make_metric_result("ideal_settling_time", t)


def _calc(actual: float, ideal: float, bundle=None) -> float | None:
    if bundle is None:
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
    calc = FastRateCalculator()
    calc.with_dependencies(
        {"settling_time": _settling(actual), "ideal_settling_time": _ideal(ideal)}
    )
    return calc.calculate(bundle)


class TestB4FastRateFormula:
    """附录 B.4：T≤T' 满分边界与 T>T' 指数衰减边界点."""

    def test_t_equals_ideal_full_score_boundary(self, reset_algo_config_cache):
        """附录 B.4：T = T' 边界 → F = 100（满分边界，≤ 含等号）."""
        result = _calc(60.0, 60.0)
        assert result.value == 100.0

    def test_t_below_ideal_full_score(self, reset_algo_config_cache):
        """附录 B.4：T < T' → F = 100."""
        result = _calc(30.0, 60.0)
        assert result.value == 100.0

    def test_t_double_ideal_decay_point(self, reset_algo_config_cache):
        """附录 B.4：T = 2T' → (T-T')/T' = 1 → F = 100/e ≈ 36.79（指数衰减基准点）."""
        result = _calc(120.0, 60.0)
        expected = round(100.0 / math.e, 2)
        assert expected == 36.79  # 手算核实锚点（禁止实现输出反推）
        assert result.value == expected
        assert result.details["ratio"] == pytest.approx(1.0, abs=1e-4)

    def test_t_one_point_five_ideal_decay_point(self, reset_algo_config_cache):
        """附录 B.4：T = 1.5T' → ratio=0.5 → F = 100·e^(-0.5) ≈ 60.65."""
        result = _calc(90.0, 60.0)
        expected = round(100.0 * math.exp(-0.5), 2)
        assert expected == 60.65  # 手算核实锚点
        assert result.value == expected
        assert result.details["ratio"] == pytest.approx(0.5, abs=1e-4)

    def test_already_stable_full_score(self, reset_algo_config_cache):
        """附录 B.4：already_stable（T≤0）→ F = 100（P0-1 三语义分流）."""
        result = _calc(0.0, 60.0)
        assert result.value == 100.0
        assert result.details["reason"] == "already_stable"

    def test_invalid_ideal_inconclusive(self, reset_algo_config_cache):
        """附录 B.4：T' 缺失/非法（≤0）→ INCONCLUSIVE（§4.5.4 步骤 26 前判）."""
        result = _calc(120.0, 0.0)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "invalid_ideal_settling_time"


class TestB4NeverSettles:
    """附录 B.4：永不收敛（never_settles）Green 函数用例——不得满分."""

    def test_never_settles_decay_by_window_length(self, reset_algo_config_cache):
        """附录 B.4：never_settles 以 Green 窗口长度代入指数衰减，不得满分.

        settling_time 返回 value=None + reason=never_settles + 窗口长度 300 s，
        T'=60 → ratio = (300-60)/60 = 4 → F = 100·e^(-4) ≈ 1.83
        （P0-1 修复固化：永不收敛不得误判 100 分）
        """
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": make_metric_result(
                    "settling_time",
                    None,
                    confidence="E",
                    details={"reason": "never_settles", "actual_settling_time": 300.0},
                ),
                "ideal_settling_time": _ideal(60.0),
            }
        )
        result = calc.calculate(bundle)

        expected = round(100.0 * math.exp(-4.0), 2)
        assert expected == 1.83  # 手算核实锚点
        assert result.value == expected
        assert result.value != 100.0  # 永不收敛禁止满分
        assert result.details["reason"] == "never_settles"

    def test_never_settles_missing_window_inconclusive(self, reset_algo_config_cache):
        """附录 B.4：never_settles 但缺窗口长度 → 按辨识失败 INCONCLUSIVE."""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": make_metric_result(
                    "settling_time",
                    None,
                    confidence="E",
                    details={"reason": "never_settles"},
                ),
                "ideal_settling_time": _ideal(60.0),
            }
        )
        result = calc.calculate(bundle)

        assert result.value is None
        assert result.details["reason"] == "identification_failed"

    def test_identification_failed_inconclusive(self, reset_algo_config_cache):
        """附录 B.4：ARMA 辨识失败 → INCONCLUSIVE（不得给分）."""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": make_metric_result(
                    "settling_time",
                    None,
                    confidence="E",
                    details={"reason": "identification_failed"},
                ),
                "ideal_settling_time": _ideal(60.0),
            }
        )
        result = calc.calculate(bundle)

        assert result.value is None
        assert result.confidence_level == "E"


class TestB4DisturbanceOverride:
    """附录 B.4：扰动覆盖分支——扰动恢复时间替代 ARMA 稳态时间代入同一公式."""

    @staticmethod
    def _disturbance_bundle():
        """手算扰动场景：t_disturb 恒为 15.0 s（推导见模块 docstring）."""
        pv = [50.0] * 20 + [60.0] * 10 + [50.0] * 10
        sp = [50.0] * 40
        return make_bundle({"pv": pv, "sp": sp}, metric_code="fast_rate")

    def test_disturbance_recovery_time_decay(self, reset_algo_config_cache):
        """附录 B.4：扰动覆盖 + T_disturb=15 > T'=7.5 → ratio=1 → F=100/e≈36.79.

        ARMA 路径稳态时间 120 s（若不覆盖会得 36.79 by ratio=(120-7.5)/7.5=15），
        覆盖后 T 取扰动恢复时间 15 s，source='disturbance'。
        """
        ac.apply_runtime({"fast_rate": {"STABLE": {"anti_disturbance_enabled": True}}})
        bundle = self._disturbance_bundle()
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _settling(120.0),
                "ideal_settling_time": _ideal(7.5),
            }
        )
        result = calc.calculate(bundle)

        expected = round(100.0 / math.e, 2)
        assert result.details["source"] == "disturbance"
        assert result.details["mean_recovery_time"] == pytest.approx(15.0, abs=1e-9)
        assert result.value == expected

    def test_disturbance_recovery_within_ideal_full_score(self, reset_algo_config_cache):
        """附录 B.4：扰动覆盖 + T_disturb=15 ≤ T'=15 边界 → F = 100."""
        ac.apply_runtime({"fast_rate": {"STABLE": {"anti_disturbance_enabled": True}}})
        bundle = self._disturbance_bundle()
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _settling(120.0),
                "ideal_settling_time": _ideal(15.0),
            }
        )
        result = calc.calculate(bundle)

        assert result.details["source"] == "disturbance"
        assert result.value == 100.0

    def test_disturbance_switch_off_uses_arma(self, reset_algo_config_cache):
        """附录 B.4：扰动开关关闭（默认）→ 走 ARMA 路径，source='arma'（零回归）."""
        ac.apply_runtime({})
        bundle = self._disturbance_bundle()
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _settling(120.0),
                "ideal_settling_time": _ideal(60.0),
            }
        )
        result = calc.calculate(bundle)

        assert result.details["source"] == "arma"
        assert result.value == round(100.0 / math.e, 2)
