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


class TestB4C07NonDefaultToleranceBoundary:
    """C07（04 验收基线 / 台账 STD-02）：容差边界两侧连续.

    默认 profile（ratio=1.0、tolerance=0.0）下阈值=T'，公式退化为冻结口径
    e^−((T−T′)/T′)×100（见 TestB4FastRateFormula 全部既有锚点，零回归）。
    非默认容差（如 tolerance=0.2 → 阈值=1.2×T'）下，修复前阈值右侧一步
    从 100 跳到 e^−0.2×100≈81.87；修复后衰减指数锚定阈值：
        F = 100·e^−((T−阈值)/T′)
    边界连续、单调递减，分段结构（满分区+指数衰减）不变。
    """

    TOL = 0.2

    def _calc_tol(self, actual: float, ideal: float) -> float | None:
        ac.apply_runtime({"fast_rate": {"STABLE": {"settling_tolerance": self.TOL}}})
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {"settling_time": _settling(actual), "ideal_settling_time": _ideal(ideal)}
        )
        return calc.calculate(bundle).value

    def test_boundary_below_and_above_continuous(self, reset_algo_config_cache):
        """C07：阈值两侧——T=阈值→100；T=阈值+ε→≈100（连续，无 18 分跳变）."""
        ideal = 100.0
        threshold = ideal * (1.0 + self.TOL)  # = 120.0
        below = self._calc_tol(threshold - 1.0, ideal)
        at = self._calc_tol(threshold, ideal)
        above = self._calc_tol(threshold + 0.01, ideal)

        assert below == 100.0
        assert at == 100.0
        # 修复前 above = 100·e^−((120−100)/100) = 81.87（跳变 18.13 分）；
        # 修复后 = 100·e^−0.0001 ≈ 99.99（连续）
        assert above == pytest.approx(100.0 * math.exp(-0.0001), abs=0.01)
        assert above > 99.9  # 显式排除旧跳变行为

    def test_tolerance_decay_anchor_hand_computed(self, reset_algo_config_cache):
        """C07：容差 0.2、T=150、T'=100 → F=100·e^−((150−120)/100)=e^−0.3×100≈74.08.

        独立手算锚点（非实现输出反推）；旧公式（锚 T'）为 e^−0.5×100≈60.65，
        显式排除，证明锚点已移到阈值。
        """
        value = self._calc_tol(150.0, 100.0)
        expected = round(100.0 * math.exp(-0.3), 2)
        assert expected == 74.08  # 手算核实锚点
        assert value == expected
        assert value != round(100.0 * math.exp(-0.5), 2)

    def test_monotone_non_increasing_across_boundary(self, reset_algo_config_cache):
        """C07：单调性——阈值附近按 T 递增序列分数单调不增，且相邻差不跳变."""
        ideal = 100.0
        series = [110.0, 119.0, 120.0, 120.1, 121.0, 130.0, 150.0, 200.0]
        values = [self._calc_tol(t, ideal) for t in series]
        for prev, cur in zip(values, values[1:], strict=False):
            assert cur <= prev + 1e-9  # 单调不增
        # 边界相邻（120 → 120.1）跌幅 ≈0.1 分（旧实现一步跌 18+ 分）
        assert values[2] - values[3] < 0.5

    def test_default_profile_t_half_ideal_full_score(self, reset_algo_config_cache):
        """C07 默认 profile 扫描补全：T/T'=0.5 → 100（0/1/1.5/2 见既有用例）."""
        ac.apply_runtime({})
        result = _calc(30.0, 60.0)
        assert result.value == 100.0
