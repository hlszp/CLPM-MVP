"""P5-01 相关分析与重复特征共享——等价基线证据测试（10 号文）.

等价红线（任务书【2 范围】4 /【核心要求】）：
    同输入优化前后 result 逐字段一致，容差按既有 golden 容差口径；
    REFACTOR_UPDATE_GOLDEN 禁用于本批（分值不许变）。

方法：测试内不复用生产实现路径，以 **独立直接参考**（full 相关
``np.correlate`` 手写复刻优化前行为）锚定语义；生产实现优化为有界
lag 直接计算后，与该参考仍须一致（本机 BLAS ddot 求和顺序与
numpy correlate C 循环一致，实测逐位相等；容差口径取既有
refactor baseline 的 ``max(1e-9, |v|·1e-9)`` 收紧为 1e-12 级）。

四类反例（03 号文 §8 P5-01 验收）：短窗 / 长窗 / 常量 / 含缺口有效段。
"""

from __future__ import annotations

import dataclasses
import math
import random
from typing import Any

import numpy as np
import pytest

from app.services.metric_calculator.stiction import StictionIndexCalculator
from app.tasks import arma as arma_mod
from app.tasks.arma import compute_settling_time_detailed, fit_ar_model

# ---------------------------------------------------------------------------
# 独立直接参考（优化前行为的逐字复刻，不经生产代码）
# ---------------------------------------------------------------------------


def _ref_fit_ar_model_full(
    signal: np.ndarray,
    order: int,
    autocorr: np.ndarray | None = None,  # noqa: ARG001
) -> np.ndarray:
    """优化前 fit_ar_model 主体复刻（full 自相关 + Yule-Walker）.

    autocorr 参数为 P5-01 优化后签名兼容——参考恒忽略之（始终走 full
    相关），以锚定优化前行为。
    """
    from scipy.linalg import solve_toeplitz, toeplitz

    signal = np.asarray(signal, dtype=float)
    n = len(signal)
    if n < order + 1:
        order = max(1, n // 3)

    signal = signal - np.mean(signal)
    autocorr = np.correlate(signal, signal, mode="full")
    autocorr = autocorr[n - 1 :] / n

    if autocorr[0] == 0:
        return np.zeros(order)

    r_vector = autocorr[1 : order + 1]
    first_column = autocorr[:order]
    try:
        ar_coeffs = solve_toeplitz(first_column, -r_vector)
    except np.linalg.LinAlgError:
        r_matrix = toeplitz(first_column)
        ar_coeffs = np.linalg.lstsq(r_matrix, -r_vector, rcond=None)[0]
    return np.asarray(ar_coeffs).flatten()


def _ref_estimate_lag_full(pv: np.ndarray, op: np.ndarray, max_lag: int) -> tuple[int, float]:
    """优化前 StictionIndexCalculator._estimate_lag 复刻（full 互相关）."""
    if max_lag < 1:
        return 0, 0.0
    x = pv - float(np.mean(pv))
    y = op - float(np.mean(op))
    denom = math.sqrt(float(np.sum(x**2) * np.sum(y**2)))
    if denom <= 0:
        return 0, 0.0
    n = len(x)
    corr = np.correlate(x, y, mode="full")
    window = corr[n - 1 : n + max_lag]
    lag = int(np.argmax(window))
    peak = float(window[lag]) / denom
    if peak < 0.3:
        return 0, peak
    return lag, peak


# ---------------------------------------------------------------------------
# 信号工厂（确定性）
# ---------------------------------------------------------------------------


def _ar2_signal(n: int, a1: float = -1.8, a2: float = 0.85, seed: int = 42) -> np.ndarray:
    np.random.seed(seed)
    sig = np.zeros(n)
    for i in range(2, n):
        sig[i] = -a1 * sig[i - 1] - a2 * sig[i - 2] + np.random.randn() * 0.01
    return sig


def _random_walk(n: int, seed: int = 7) -> np.ndarray:
    np.random.seed(seed)
    return np.cumsum(np.random.randn(n) * 0.05)


def _lagged_pv_op(
    n: int, lag_samples: int, seed: int = 42, noise: float = 0.2
) -> tuple[np.ndarray, np.ndarray]:
    np.random.seed(seed)
    t = np.linspace(0, 40 * np.pi, n)
    op = 30.0 + 5.0 * np.sin(t) + np.random.randn(n) * noise
    pv = np.empty(n)
    pv[:lag_samples] = 30.0 + np.random.randn(lag_samples) * noise
    pv[lag_samples:] = op[: n - lag_samples] + np.random.randn(n - lag_samples) * noise
    return pv, op


def _gapped_signal(n: int, seed: int = 5) -> np.ndarray:
    """含缺口有效段：两段不同相位/幅值的正弦拼接（模拟 mask 压缩后数组）."""
    half = n // 2
    t1 = np.linspace(0, 20 * np.pi, half)
    t2 = np.linspace(0, 20 * np.pi, n - half)
    seg1 = 10.0 * np.sin(t1) + 3.0
    seg2 = 6.0 * np.sin(t2 + 1.3) - 2.0
    rng = np.random.RandomState(seed)
    return np.concatenate([seg1, seg2]) + rng.randn(n) * 0.1


def _assert_float_close(got: float, exp: float, label: str) -> None:
    """既有 golden 容差口径收紧版（refactor baseline 1e-9 → 1e-12）."""
    assert abs(got - exp) <= max(1e-12, abs(exp) * 1e-12), f"{label}: {exp} → {got}"


# ---------------------------------------------------------------------------
# 1. ARMA：自相关有界 lag 化 + AR 重试复用
# ---------------------------------------------------------------------------


class TestArmaBoundedAutocorrelation:
    """fit_ar_model 有界 lag 化与 full 自相关参考的等价."""

    @pytest.mark.parametrize("order", [2, 4, 6, 10])
    @pytest.mark.parametrize(
        ("label", "signal"),
        [
            ("long_ar2", _ar2_signal(3600)),
            ("long_walk", _random_walk(3600)),
            ("short", _ar2_signal(50, seed=3)),
            ("gapped", _gapped_signal(3600)),
        ],
    )
    def test_fit_coeffs_match_full_reference(self, label: str, signal: np.ndarray, order: int):
        got = fit_ar_model(signal, order=order)
        exp = _ref_fit_ar_model_full(signal, order)
        assert np.allclose(got, exp, atol=1e-12, rtol=0), f"{label}/order={order}: {exp} → {got}"

    def test_short_signal_order_degrade_semantics_kept(self):
        """n < order+1 时降阶语义保持（order→max(1, n//3)）."""
        signal = _ar2_signal(29, seed=11)
        for order in (2, 6, 10):
            got = fit_ar_model(signal, order=order)
            exp = _ref_fit_ar_model_full(signal, order)
            assert np.allclose(got, exp, atol=1e-12, rtol=0)

    def test_constant_signal_zero_coeffs(self):
        """常量信号：autocorr[0]==0 → 全零系数（退化路径语义保持）."""
        signal = np.full(200, 5.0)
        got = fit_ar_model(signal, order=2)
        exp = _ref_fit_ar_model_full(signal, 2)
        assert np.allclose(got, exp, atol=1e-12, rtol=0)
        assert np.allclose(got, 0, atol=1e-6)


class TestSettlingDetailedEquivalence:
    """compute_settling_time_detailed 全链等价：monkeypatch full 版 fit 后逐字段一致.

    锚定方式：把 arma 模块的 fit_ar_model 引用替换为独立参考（full 自相关）
    版本，对四类反例逐字段对比——优化后的有界 lag 路径与 full 路径在
    同输入下 result 完全一致（容差口径 _assert_float_close）。
    """

    CASES = {
        "short_failed": _ar2_signal(29, seed=11),  # 短窗：n<30 → IDENTIFICATION_FAILED
        "short_50": _ar2_signal(50, seed=3),
        "long_ar2": _ar2_signal(3600),
        "long_walk": _random_walk(3600),  # 近单位根：触发阶数重试链
        "constant": np.full(3600, 7.3),  # 常量：ALREADY_STABLE
        "gapped": _gapped_signal(3600),  # 含缺口有效段拼接
    }

    def _fields(self, r: Any) -> dict[str, Any]:
        return dataclasses.asdict(r)

    @pytest.mark.parametrize("sustained_tail", [False, True])
    @pytest.mark.parametrize("label", list(CASES.keys()))
    def test_result_fields_identical_vs_full_fit(self, label: str, sustained_tail: bool):
        signal = self.CASES[label]
        kwargs: dict[str, Any] = {
            "sample_interval_sec": 1.0,
            "sustained_tail": sustained_tail,
        }
        current = compute_settling_time_detailed(signal, **kwargs)
        original = arma_mod.fit_ar_model
        try:
            arma_mod.fit_ar_model = _ref_fit_ar_model_full
            ref = compute_settling_time_detailed(signal, **kwargs)
        finally:
            arma_mod.fit_ar_model = original

        got, exp = self._fields(current), self._fields(ref)
        assert got["status"] == exp["status"], f"{label}: status {exp['status']} → {got['status']}"
        for key in (
            "value",
            "window_length_sec",
            "candidate_seconds",
            "tail_seconds",
            "required_tail_seconds",
            "pole_period_seconds",
            "observation_seconds",
        ):
            g, e = got[key], exp[key]
            if e is None or g is None:
                assert g == e, f"{label}/{key}: None 漂移 {e} → {g}"
            else:
                _assert_float_close(g, e, f"{label}/{key}")


# ---------------------------------------------------------------------------
# 2. stiction：互相关 θ 估计有界 lag 化
# ---------------------------------------------------------------------------


class TestStictionCrossCorrelation:
    """_estimate_lag 有界 lag 化与 full 互相关参考的等价."""

    @pytest.mark.parametrize(
        ("label", "pv", "op", "max_lag"),
        [
            ("long_lag25", *_lagged_pv_op(3600, 25), 300),
            ("long_zero_lag", *_lagged_pv_op(3600, 0, seed=9), 300),
            ("long_maxlag_short", *_lagged_pv_op(3600, 25), 1),
            ("short_window", *_lagged_pv_op(120, 8), 30),
            ("gapped_pv", _gapped_signal(3600), _gapped_signal(3600, seed=6), 300),
        ],
    )
    def test_estimate_lag_matches_full_reference(
        self, label: str, pv: np.ndarray, op: np.ndarray, max_lag: int
    ):
        lag_got, peak_got = StictionIndexCalculator._estimate_lag(pv, op, max_lag)
        lag_exp, peak_exp = _ref_estimate_lag_full(pv, op, max_lag)
        assert lag_got == lag_exp, f"{label}: lag {lag_exp} → {lag_got}"
        if peak_exp != 0.0:
            _assert_float_close(peak_got, peak_exp, f"{label}/peak")
        else:
            assert peak_got == peak_exp, f"{label}: peak {peak_exp} → {peak_got}"

    def test_constant_signals_early_return(self):
        """常量信号：denom=0 早退 (0, 0.0)（退化路径语义保持）."""
        pv = np.full(500, 42.0)
        op = np.full(500, 30.0)
        assert StictionIndexCalculator._estimate_lag(pv, op, 300) == (0, 0.0)
        assert _ref_estimate_lag_full(pv, op, 300) == (0, 0.0)

    def test_uncorrelated_white_noise_pair(self):
        """不相关白噪声对：peak < 0.3 → lag=0 不补偿（显著性回退语义保持）."""
        np.random.seed(21)
        pv = np.random.randn(3600) * 5 + 50
        op = np.random.randn(3600) * 3 + 20
        lag_got, peak_got = StictionIndexCalculator._estimate_lag(pv, op, 300)
        lag_exp, peak_exp = _ref_estimate_lag_full(pv, op, 300)
        assert lag_got == lag_exp == 0
        _assert_float_close(peak_got, peak_exp, "peak")


class TestStictionCalculatorFourCases:
    """StictionIndexCalculator.calculate 四类反例：θ 估计细节与独立参考一致."""

    @staticmethod
    def _make_bundle(pv: list[float], op: list[float]):
        from tests.test_metric_calculator.conftest import make_bundle

        return make_bundle({"pv": pv, "op": op}, metric_code="stiction_index")

    def test_short_window_insufficient(self):
        """短窗（<100 点）→ INCONCLUSIVE(insufficient_data)."""
        pv, op = _lagged_pv_op(50, 5)
        result = StictionIndexCalculator().calculate(self._make_bundle(list(pv), list(op)))
        assert result.value is None
        assert result.details["reason"] == "insufficient_data"

    def test_long_window_theta_details_match_reference(self):
        """长窗：details.theta_hat_seconds/corr_peak 与 full 参考一致."""
        pv, op = _lagged_pv_op(3600, 25, seed=7, noise=0.1)
        result = StictionIndexCalculator().calculate(self._make_bundle(list(pv), list(op)))
        assert result.value is not None
        dt = 1.0
        max_lag = min(int(300 / dt), 3600 // 4)
        lag_exp, peak_exp = _ref_estimate_lag_full(pv, op, max_lag)
        lag_got = int(round(result.details["theta_hat_seconds"] / dt))
        assert lag_got == lag_exp, f"lag {lag_exp} → {lag_got}"
        _assert_float_close(result.details["corr_peak"], round(peak_exp, 4), "corr_peak")

    def test_constant_signals_no_stiction(self):
        """常量：非极限环 → value=0 / NONE / no_limit_cycle."""
        pv = np.full(400, 50.0)
        op = np.full(400, 30.0)
        result = StictionIndexCalculator().calculate(self._make_bundle(list(pv), list(op)))
        assert result.value == 0.0
        assert result.details["stiction_level"] == "NONE"
        assert result.details["reason"] == "no_limit_cycle"

    def test_gapped_segments_theta_details_match_reference(self):
        """含缺口有效段：拼接段上 θ 估计仍与 full 参考一致.

        缺口拼接信号两段相位跳变 → 极限环门控常判 no_limit_cycle（此时
        提前返回、无 θ 字段——该分支语义由 test_constant/no_limit_cycle
        用例锚定）；门控通过时 θ 细节必须与 full 参考一致。
        """
        pv = _gapped_signal(3600)
        op = _gapped_signal(3600, seed=6)
        result = StictionIndexCalculator().calculate(self._make_bundle(list(pv), list(op)))
        if "theta_hat_seconds" not in result.details:
            assert result.details.get("reason") == "no_limit_cycle"
            return
        max_lag = min(300, 3600 // 4)
        lag_exp, peak_exp = _ref_estimate_lag_full(pv, op, max_lag)
        lag_got = int(round(result.details["theta_hat_seconds"]))
        assert lag_got == lag_exp, f"lag {lag_exp} → {lag_got}"
        _assert_float_close(result.details["corr_peak"], round(peak_exp, 4), "corr_peak")


# ---------------------------------------------------------------------------
# 3. 随机一致性压力（随机信号下新旧路径一致性抽样）
# ---------------------------------------------------------------------------


class TestRandomizedConsistency:
    """随机信号抽样：有界 lag 与 full 参考在随机输入下一致（容差口径）."""

    def test_random_pairs_estimate_lag(self):
        rng = random.Random(20261013)
        for trial in range(30):
            n = rng.choice([150, 600, 3600])
            lag_true = rng.choice([0, 5, 25, 60])
            noise = rng.choice([0.05, 0.2, 0.5])
            np.random.seed(trial)
            t = np.linspace(0, 40 * np.pi, n)
            op = 30.0 + 5.0 * np.sin(t) + np.random.randn(n) * noise
            pv = np.empty(n)
            pv[:lag_true] = 30.0 + np.random.randn(lag_true) * noise
            pv[lag_true:] = op[: n - lag_true] + np.random.randn(n - lag_true) * noise
            max_lag = min(300, n // 4)
            got = StictionIndexCalculator._estimate_lag(pv, op, max_lag)
            exp = _ref_estimate_lag_full(pv, op, max_lag)
            assert got[0] == exp[0], f"trial={trial} n={n}: lag {exp} → {got}"
            if exp[1] != 0.0:
                _assert_float_close(got[1], exp[1], f"trial={trial} peak")

    def test_random_signals_fit_ar(self):
        for trial in range(20):
            n = 3600
            np.random.seed(100 + trial)
            sig = np.cumsum(np.random.randn(n) * 0.03) + np.random.randn(n) * 0.01
            order = 2 + (trial % 4) * 2
            got = fit_ar_model(sig, order=order)
            exp = _ref_fit_ar_model_full(sig, order)
            assert np.allclose(got, exp, atol=1e-12, rtol=0), f"trial={trial} order={order}"


# ---------------------------------------------------------------------------
# 4. P5-01 结构性断言：AR 重试复用（lag 特征只算一次）
# ---------------------------------------------------------------------------


class TestArRetryReuse:
    """AR 阶数重试链共享一次有界自相关（不逐阶重算）.

    Yule-Walker 拟合的 AR(2) 根几乎总在单位圆内（Toeplitz 正定性质），
    真实信号难以触发 Green 发散重试——注入低阶 Green 发散强制走完整
    重试链（[2,4,6,10] 逐档尝试），断言 lag 特征仍只算一次。
    """

    def test_retry_chain_computes_autocorr_once(self, monkeypatch):
        import app.tasks.arma as arma_module

        walk = _random_walk(3600)
        fit_orders: list[int] = []
        original_fit = arma_module.fit_ar_model
        real_green = arma_module.compute_green_function

        def counting_fit(signal, order=2, autocorr=None):
            fit_orders.append(order)
            return original_fit(signal, order=order, autocorr=autocorr)

        def diverging_green(ar_coeffs, length=3600):
            # 前 3 档（2/4/6）强制发散 → 链路重试至第 4 档（10）。
            # 注：不能等比放大（detailed 归一化 g/g[0] 会抵消），直接
            # 返回 inf 使归一化后为 NaN → 发散检测命中。
            if fit_orders and len(fit_orders) <= 3:
                return np.full(length, np.inf)
            return real_green(ar_coeffs, length)

        bounded_calls: list[int] = []
        real_bounded = arma_module._autocorr_bounded

        def counting_bounded(signal: np.ndarray, max_lag: int) -> np.ndarray:
            bounded_calls.append(max_lag)
            return real_bounded(signal, max_lag)

        monkeypatch.setattr(arma_module, "fit_ar_model", counting_fit)
        monkeypatch.setattr(arma_module, "compute_green_function", diverging_green)
        monkeypatch.setattr(arma_module, "_autocorr_bounded", counting_bounded)

        compute_settling_time_detailed(walk, sustained_tail=True)

        assert len(fit_orders) >= 2, f"注入未触发阶数重试（fit 调用 {fit_orders}）"
        assert len(bounded_calls) == 1, (
            f"重试链应只算一次有界自相关（最大阶），实际调用 {bounded_calls}"
        )
        assert bounded_calls[0] == 10, f"预计算应覆盖最大重试阶（10），实际 {bounded_calls}"

    def test_retry_reuse_result_unchanged(self):
        """重试复用路径与逐阶独立 full 拟合结果一致."""
        walk = _random_walk(3600)
        got = compute_settling_time_detailed(walk, sustained_tail=True)
        import app.tasks.arma as arma_module

        original = arma_module.fit_ar_model
        try:
            arma_module.fit_ar_model = _ref_fit_ar_model_full
            ref = compute_settling_time_detailed(walk, sustained_tail=True)
        finally:
            arma_module.fit_ar_model = original
        assert got.status == ref.status
        assert got.value == ref.value


# ---------------------------------------------------------------------------
# 5. P5-01 极限环门控复用（gate 参数注入等价）
# ---------------------------------------------------------------------------


class TestGateReuse:
    """assess_stiction_features / _ellipse_kernel / _choudhury_kernel 的
    limit_cycle_gate 注入与内部自算在四类反例下输出全等."""

    PV_CASES = {
        "short": _lagged_pv_op(120, 8, seed=3)[0],
        "long": _lagged_pv_op(3600, 25, seed=7, noise=0.1)[0],
        "constant": np.full(400, 50.0),
        "gapped": _gapped_signal(3600),
    }
    OP_CASES = {
        "short": _lagged_pv_op(120, 8, seed=4)[1],
        "long": _lagged_pv_op(3600, 25, seed=7, noise=0.1)[1],
        "constant": np.full(400, 30.0),
        "gapped": _gapped_signal(3600, seed=6),
    }

    def test_assess_features_gate_param_equivalence(self):
        from app.services.metric_calculator.stiction import assess_stiction_features

        for label, pv in self.PV_CASES.items():
            op = self.OP_CASES[label]
            n = min(len(pv), len(op))
            pv_n, op_n = pv[:n], op[:n]
            gate = StictionIndexCalculator._detect_limit_cycle(np.asarray(pv_n, dtype=float))
            with_gate = assess_stiction_features(
                pv_n, op_n, limit_cycle_gate=gate, pv_range=100.0, op_range=100.0
            )
            without = assess_stiction_features(pv_n, op_n, pv_range=100.0, op_range=100.0)
            assert with_gate == without, f"{label}: gate 注入与自算输出漂移"

    def test_ellipse_kernel_gate_param_equivalence(self):
        from app.services.diagnosis_operators.stiction import _ellipse_kernel

        for label, pv in self.PV_CASES.items():
            op = self.OP_CASES[label]
            n = min(len(pv), len(op))
            pv_n = np.asarray(pv[:n], dtype=float)
            op_n = np.asarray(op[:n], dtype=float)
            gate = StictionIndexCalculator._detect_limit_cycle(pv_n)
            with_gate = _ellipse_kernel(pv_n, op_n, 1.0, limit_cycle_gate=gate)
            without = _ellipse_kernel(pv_n, op_n, 1.0)
            assert with_gate == without, f"{label}: 椭圆内核 gate 注入与自算漂移"

    def test_choudhury_kernel_gate_reuse_equivalence(self):
        """Choudhury 内核（内部已传 gate）与独立直接参考（旧逻辑全链）全等."""
        from scipy import stats as sp_stats

        from app.services.diagnosis_operators.stiction import (
            _choudhury_kernel,
            _compute_max_bicoherence,
            _ellipse_kernel,
        )

        for label in ("long", "gapped"):
            pv = np.asarray(self.PV_CASES[label], dtype=float)
            op = np.asarray(self.OP_CASES[label], dtype=float)
            n = min(len(pv), len(op))
            pv_arr, op_arr = pv[:n], op[:n]

            got = _choudhury_kernel(pv_arr, op_arr, sample_interval=1.0)

            # 独立直接参考：优化前逻辑（门控自算 + 椭圆内核自算门控）
            is_lc, gate_info = StictionIndexCalculator._detect_limit_cycle(pv_arr)
            if not is_lc:
                exp = {
                    "detected": False,
                    "confidence": 0.0,
                    "ngi": 0.0,
                    "nli": 0.0,
                    "stiction_index": 0.0,
                    "fitting_score": 0.0,
                    "reason": "no_limit_cycle",
                    **gate_info,
                }
            else:
                op_diff = np.diff(op_arr)
                op_diff_c = op_diff - np.mean(op_diff)
                ngi = (
                    abs(float(sp_stats.kurtosis(op_diff_c, fisher=True))) / 6.0
                    + (float(sp_stats.skew(op_diff_c)) ** 2) / 24.0
                )
                nli = _compute_max_bicoherence(op_diff_c)
                fit = _ellipse_kernel(pv_arr, op_arr, 1.0)
                fitting = float(fit.get("fitting_score", 0.0))
                detected = bool(ngi > 1.0 and nli > 0.01)
                exp = {
                    "detected": detected,
                    "confidence": min(1.0, ngi * 0.5 + nli * 0.3 + fitting * 0.2)
                    if detected
                    else 0.0,
                    "ngi": ngi,
                    "nli": nli,
                    "stiction_index": float(fit.get("stiction_index", 0.0)),
                    "fitting_score": fitting,
                    "reason": (
                        fit.get("reason") if fit.get("reason") != "insufficient_fit" else None
                    ),
                    "bicoherence_segments": 64,
                    "bicoherence_freq_pairs": 105,
                }
            assert got == exp, f"{label}: Choudhury gate 复用与参考全等失败"


# ---------------------------------------------------------------------------
# 6. P5-01 point 轴算子输入切片共享（scoped_cache 等价）
# ---------------------------------------------------------------------------


def _make_point_axis_input(n: int = 3600, seed: int = 11) -> Any:
    from app.services.diagnosis_operators.base import OperatorInput

    pv, op = _lagged_pv_op(n, 25, seed=seed)
    ts = np.arange(n, dtype=float)
    pv_obj = np.array([None if i % 997 == 0 else pv[i] for i in range(n)], dtype=object)
    op_obj = np.array([None if i % 811 == 0 else op[i] for i in range(n)], dtype=object)
    sp_obj = np.array(
        [None if i % 701 == 0 else 50.0 + 0.1 * pv[i] for i in range(n)], dtype=object
    )
    return OperatorInput(
        loop_id="P501-TEST",
        signals={
            "pv": pv_obj,
            "sp": sp_obj,
            "op": op_obj,
            "mode": np.array(["AUTO"] * n, dtype=object),
            "pv_quality": np.zeros(n, dtype=int),
        },
        timestamps=ts,
        meta={
            "point_axis": True,
            "signals_valid": None,
            "sample_interval": 1.0,
            "total_points": n,
            "pv_range": (0.0, 100.0),
            "op_range": (0.0, 100.0),
        },
    )


class TestScopedInputSharing:
    """_run_operators 的 scoped 切片缓存与逐算子独立切片结果全等."""

    def test_run_operators_equals_per_operator_scoped(self):
        from app.services.diagnosis_operators import OPERATOR_REGISTRY
        from app.services.diagnosis_orchestrator import _run_operators, _scoped_operator_input

        op_input = _make_point_axis_input()
        snapshots = {k: np.array(v, dtype=object) for k, v in op_input.signals.items()}
        ts_snapshot = np.array(op_input.timestamps)

        results, _fusions = _run_operators(op_input, {}, "full")

        # 独立参考：逐算子独立切片（优化前口径）执行
        for name, (meta, fn) in OPERATOR_REGISTRY.items():
            ref_input, skip = _scoped_operator_input(op_input, meta)
            if skip is not None:
                assert not results[name].executed, f"{name}: 缓存路径应同样 skip"
                continue
            ref_result = fn(ref_input, {})
            got = results[name]
            assert got.executed == ref_result.executed, f"{name}: executed 漂移"
            assert got.detected == ref_result.detected, f"{name}: detected 漂移"
            assert got.error == ref_result.error, f"{name}: error 漂移"
            assert got.features == ref_result.features, f"{name}: features 漂移"

        # 无状态守护：共享切片未被算子原地修改（原输入数组逐位不变）
        for k, v in snapshots.items():
            assert all((a is b) or (a == b) for a, b in zip(op_input.signals[k], v, strict=True)), (
                f"输入信号 {k} 被算子原地修改"
            )
        assert np.array_equal(op_input.timestamps, ts_snapshot)

    def test_run_operators_idempotent_on_same_input(self):
        from app.services.diagnosis_orchestrator import _run_operators

        op_input = _make_point_axis_input()
        r1, f1 = _run_operators(op_input, {}, "full")
        r2, f2 = _run_operators(op_input, {}, "full")
        for name in r1:
            assert r1[name].features == r2[name].features, f"{name}: 两次执行漂移"
            assert r1[name].assessment_state == r2[name].assessment_state
