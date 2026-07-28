"""ARMA 稳态时间三语义测试（P0-1）.

修复前：arma.py 对 Green 函数不衰减（持续振荡/近单位根）与辨识发散
均返回 0.0，fast_rate 将 actual_t<=0 一律判为 already_stable 给 100 分，
导致持续振荡/近不稳定回路快速率满分。

修复后区分三语义：
- already_stable（真已稳态）→ settling_time value=0.0，fast_rate 100 分
- never_settles（窗口内不衰减）→ settling_time value=None，
  fast_rate 以 Green 函数窗口长度代入指数衰减公式（不得满分）
- identification_failed（辨识失败）→ settling_time value=None，
  fast_rate INCONCLUSIVE

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 F.4 / B.4
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.contracts.data_types import DataLineage, MetricResult
from app.services import algorithm_config as ac
from app.services.metric_calculator.fast_rate import FastRateCalculator
from app.services.metric_calculator.settling_time import SettlingTimeCalculator
from app.tasks.arma import (
    MAX_GREEN_FUNC_LENGTH,
    SettlingStatus,
    SettlingTimeResult,
    compute_settling_time,
    compute_settling_time_detailed,
)

from .conftest import make_bundle


def _sustained_oscillation(n: int = 2000, period: int = 100, amp: float = 10.0) -> np.ndarray:
    """持续等幅低频振荡信号（AR 拟合近单位根，Green 函数窗口内不衰减）。"""
    return amp * np.sin(2 * math.pi * np.arange(n) / period)


class TestArmaSettlingSemantics:
    """arma 层 compute_settling_time_detailed 三语义。"""

    def test_sustained_oscillation_never_settles(self):
        """持续等幅振荡 → NEVER_SETTLES，value=None，窗口长度=3600 秒。"""
        signal = _sustained_oscillation()
        result = compute_settling_time_detailed(signal, sample_interval_sec=1.0)
        assert result.status is SettlingStatus.NEVER_SETTLES
        assert result.value is None
        assert result.window_length_sec == float(MAX_GREEN_FUNC_LENGTH)

    def test_compat_wrapper_returns_zero_for_never_settles(self):
        """兼容包装 compute_settling_time 对不衰减仍返回 0.0（向后兼容）。"""
        signal = _sustained_oscillation()
        assert compute_settling_time(signal, sample_interval_sec=1.0) == 0.0

    def test_decaying_signal_settled(self):
        """衰减信号 → SETTLED，稳态时间 > 0。"""
        np.random.seed(7)
        n = 500
        signal = np.zeros(n)
        for t in range(1, n):
            signal[t] = -0.3 * signal[t - 1] + np.random.randn() * 0.1
        result = compute_settling_time_detailed(signal, sample_interval_sec=1.0)
        assert result.status is SettlingStatus.SETTLED
        assert result.value is not None
        assert 0 < result.value < result.window_length_sec

    def test_constant_signal_already_stable(self):
        """恒定信号 → ALREADY_STABLE，value=0.0（真已稳态）。"""
        result = compute_settling_time_detailed(np.ones(100) * 5.0)
        assert result.status is SettlingStatus.ALREADY_STABLE
        assert result.value == 0.0

    def test_insufficient_data_identification_failed(self):
        """数据点不足 → IDENTIFICATION_FAILED，value=None。"""
        result = compute_settling_time_detailed(np.array([1.0, 2.0, 3.0]))
        assert result.status is SettlingStatus.IDENTIFICATION_FAILED
        assert result.value is None

    def test_divergent_signal_identification_failed(self, monkeypatch):
        """故障注入：辨识恒发散（mock fit_ar_model 返回不稳定系数）→ IDENTIFICATION_FAILED。"""
        import app.tasks.arma as arma_module

        def _unstable_fit(signal, order=2) -> np.ndarray:
            # 特征根 |r| > 1（如 z - 2 = 0），Green 函数必发散
            return np.concatenate([[-2.0], np.zeros(order - 1)])

        monkeypatch.setattr(arma_module, "fit_ar_model", _unstable_fit)

        n = 200
        signal = np.sin(2 * math.pi * np.arange(n) / 50)
        result = compute_settling_time_detailed(signal, sample_interval_sec=1.0)
        assert result.status is SettlingStatus.IDENTIFICATION_FAILED
        assert result.value is None


class TestSettlingTimeCalculatorSemantics:
    """settling_time 计算器层三语义映射。"""

    def test_never_settles_returns_none_with_window_length(self):
        """持续振荡 → value=None，reason=never_settles，details 携带窗口长度。"""
        n = 2000
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 100) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        result = SettlingTimeCalculator().calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "never_settles"
        assert result.details["actual_settling_time"] == float(MAX_GREEN_FUNC_LENGTH)

    def test_identification_failed_returns_none(self, monkeypatch):
        """故障注入：mock arma 辨识失败 → value=None，reason=identification_failed。"""
        import app.tasks.arma as arma_module

        def _fake_detailed(**kwargs) -> SettlingTimeResult:
            return SettlingTimeResult(
                status=SettlingStatus.IDENTIFICATION_FAILED,
                value=None,
                window_length_sec=float(MAX_GREEN_FUNC_LENGTH),
            )

        monkeypatch.setattr(arma_module, "compute_settling_time_detailed", _fake_detailed)

        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 50) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        result = SettlingTimeCalculator().calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "identification_failed"

    def test_already_stable_returns_zero(self):
        """恒定偏差 → value=0.0，reason=already_stable。"""
        n = 100
        bundle = make_bundle({"pv": [50.5] * n, "sp": [50.0] * n}, metric_code="settling_time")
        result = SettlingTimeCalculator().calculate(bundle)
        assert result.value == 0.0
        assert result.details["reason"] == "already_stable"


# ---------------------------------------------------------------------------
# fast_rate 层三语义分流
# ---------------------------------------------------------------------------


def _make_ideal_result(t: float) -> MetricResult:
    return MetricResult(
        metric_code="ideal_settling_time",
        value=t,
        confidence_level="A",
        lineage=DataLineage(),
        details={},
    )


def _make_settling_result(
    value: float | None,
    reason: str,
    actual_settling_time: float | None = None,
) -> MetricResult:
    details: dict = {"reason": reason}
    if actual_settling_time is not None:
        details["actual_settling_time"] = actual_settling_time
    return MetricResult(
        metric_code="settling_time",
        value=value,
        confidence_level="A" if value is not None else "E",
        lineage=DataLineage(),
        details=details,
    )


class TestFastRateSemantics:
    """fast_rate 对 settling_time 三语义的分流。"""

    def test_never_settles_not_full_score(self):
        """never_settles → 以窗口长度代入指数衰减公式，不得满分。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(
                    None, "never_settles", actual_settling_time=3600.0
                ),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value < 100.0
        # 窗口 3600s >> ideal 60s → 指数衰减到接近 0
        assert result.value < 1.0
        assert result.details["reason"] == "never_settles"
        assert result.details["actual_settling_time"] == 3600.0

    def test_identification_failed_inconclusive(self):
        """identification_failed → fast_rate INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(None, "identification_failed"),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "identification_failed"

    def test_already_stable_full_score(self):
        """already_stable（真已稳态）→ 保持 100 分。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(0.0, "already_stable", 0.0),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["reason"] == "already_stable"

    def test_never_settles_missing_window_inconclusive(self):
        """边界：never_settles 但 details 缺窗口长度 → 按辨识失败 INCONCLUSIVE。"""
        bundle = make_bundle({"pv": [50.0] * 10, "sp": [50.0] * 10}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(None, "never_settles"),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "identification_failed"


@pytest.fixture
def reset_algo_config_cache():
    """每个测试前后保存/恢复算法参数缓存，避免污染其他测试。"""
    saved = dict(ac._merged_cache)
    ac._merged_cache = {}
    yield
    ac._merged_cache = saved


class TestFastRateDisturbanceOverrideCompat:
    """disturbance_override 分支与三语义的兼容性。"""

    def test_disturbance_override_skips_never_settles(self, reset_algo_config_cache):
        """抗扰开关开启 + 检测到扰动 + settling never_settles → 扰动恢复时间覆盖，仍出值。"""
        ac.apply_runtime({"fast_rate": {"STABLE": {"anti_disturbance_enabled": True}}})
        n = 100
        sp = [50.0] * n
        pv = [50.0] * 30 + [60.0] * 10 + [50.0] * 60
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": _make_settling_result(
                    None, "never_settles", actual_settling_time=3600.0
                ),
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(bundle)
        # 扰动覆盖跳过 never_settles 分流，用恢复时间（~15s < 60s）→ 100
        assert result.value == 100.0
        assert result.details["source"] == "disturbance"


class TestSettlingToFastRateIntegration:
    """端到端：持续振荡回路经 settling_time → fast_rate 不得满分。"""

    def test_oscillating_loop_not_full_score(self):
        n = 2000
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 100) for i in range(n)]

        settling_bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        settling_result = SettlingTimeCalculator().calculate(settling_bundle)
        assert settling_result.value is None
        assert settling_result.details["reason"] == "never_settles"

        fast_bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": settling_result,
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(fast_bundle)
        assert result.value is not None
        assert result.value < 100.0
        assert result.details["reason"] == "never_settles"

    def test_stable_loop_full_score(self):
        """端到端：恒定偏差回路（真已稳态）→ fast_rate 100 分。"""
        n = 100
        pv = [50.5] * n
        sp = [50.0] * n

        settling_bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="settling_time")
        settling_result = SettlingTimeCalculator().calculate(settling_bundle)
        assert settling_result.value == 0.0

        fast_bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="fast_rate")
        calc = FastRateCalculator()
        calc.with_dependencies(
            {
                "settling_time": settling_result,
                "ideal_settling_time": _make_ideal_result(60.0),
            }
        )
        result = calc.calculate(fast_bundle)
        assert result.value == 100.0
        assert result.details["reason"] == "already_stable"
