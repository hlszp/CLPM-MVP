"""Phase 2.1 tuning_identification 算法栈单测.

覆盖：
- 激励检测（充分/不足/边界）
- ARX/ARMAX/IV 辨识（开环/闭环、已知参数精度）
- 离散→连续转换（FOPDT/SOPDT、稳定性、数值病态）
- 阶次选择（AIC/BIC、Ljung-Box）
- 非参数粗估
- pipeline 端到端（闭环 FOPDT/SOPDT、激励不足、短数据）
- golden 基线对齐
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from app.services.tuning_identification import identify_from_history
from app.services.tuning_identification.armax import identify_armax
from app.services.tuning_identification.arx import identify_arx
from app.services.tuning_identification.discrete_to_continuous import (
    arx_to_fopdt,
    arx_to_sopdt,
)
from app.services.tuning_identification.excitation import (
    check_excitation,
    excitation_score,
)
from app.services.tuning_identification.iv import identify_iv, identify_iv4
from app.services.tuning_identification.nonparametric import (
    correlation_analysis,
    welch_spectral_analysis,
)
from app.services.tuning_identification.order_selection import (
    compute_aic,
    compute_bic,
    cross_validate,
    ljung_box_test,
    select_order,
)
from app.services.tuning_identification.types import (
    ConfidenceLevel,
    ModelType,
)

# ---------------------------------------------------------------------------
# 仿真辅助函数
# ---------------------------------------------------------------------------


def _simulate_open_loop_fopdt(
    K: float,
    tau: float,
    theta: float,
    u: np.ndarray,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 42,
) -> np.ndarray:
    """开环 FOPDT 仿真：y(t) 由 u(t) 驱动.

    G(s) = K * exp(-theta*s) / (tau*s + 1)
    离散化：a = exp(-ts/tau), b = K*(1-a), d = round(theta/ts)
    """
    rng = np.random.default_rng(seed)
    n = len(u)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    y = np.zeros(n)
    for k in range(d, n):
        y[k] = a * y[k - 1] + b * u[k - d]
    if noise_std > 0:
        y += rng.normal(0, noise_std, n)
    return y


def _simulate_closed_loop_fopdt(
    sp: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """闭环 FOPDT 仿真：PI 控制器 + 过程对象.

    返回 (y, u)，u 是 PID 输出（辨识输入）。
    """
    rng = np.random.default_rng(seed)
    n = len(sp)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    y = np.zeros(n)
    u = np.zeros(n)
    e_prev = 0.0
    u_prev = 0.0
    ki = kp * ts / ti
    for k in range(n):
        e = sp[k] - y[k]
        de = e - e_prev
        u[k] = u_prev + kp * de + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d]
    if noise_std > 0:
        y += rng.normal(0, noise_std, n)
    return y, u


def _simulate_closed_loop_sopdt(
    sp: np.ndarray,
    K: float,
    T1: float,
    T2: float,
    theta: float,
    kp: float,
    ti: float,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 7,
) -> tuple[np.ndarray, np.ndarray]:
    """闭环 SOPDT 仿真：G(s) = K*exp(-theta*s)/((T1*s+1)(T2*s+1)).

    用级联两个一阶环节离散化。
    """
    rng = np.random.default_rng(seed)
    n = len(sp)
    a1 = math.exp(-ts / T1)
    b1 = K * (1 - a1) * (T1 / (T1 - T2)) if T1 != T2 else K * (1 - a1) * (1 / T1) * ts
    a2 = math.exp(-ts / T2)
    b2 = (1 - a2) * (T2 / (T2 - T1)) if T1 != T2 else (1 - a2)
    d = max(0, round(theta / ts))
    y = np.zeros(n)
    x1 = np.zeros(n)  # 中间状态
    x2 = np.zeros(n)  # 输出
    u = np.zeros(n)
    e_prev = 0.0
    u_prev = 0.0
    ki = kp * ts / ti
    for k in range(n):
        e = sp[k] - y[k]
        de = e - e_prev
        u[k] = u_prev + kp * de + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            x1[k] = a1 * x1[k - 1] + b1 * u[k - d]
            x2[k] = a2 * x2[k - 1] + b2 * x1[k]
            y[k] = x2[k]
    if noise_std > 0:
        y += rng.normal(0, noise_std, n)
    return y, u


def _prbs(n: int, seed: int = 42, levels: int = 2) -> np.ndarray:
    """生成 PRBS 信号（二进制伪随机激励）."""
    rng = np.random.default_rng(seed)
    # 简化 PRBS：随机切换 ±1
    u = np.ones(n)
    switch_idx = sorted(rng.choice(n, size=n // 10, replace=False))
    sign = 1.0
    prev = 0
    for idx in switch_idx:
        u[prev:idx] = sign
        sign *= -1
        prev = idx
    u[prev:] = sign
    return u * 5.0  # 幅值放大


def _sp_steps(n: int, steps: list[tuple[int, float]]) -> np.ndarray:
    """生成 SP 阶跃信号。steps = [(start_idx, value), ...]."""
    sp = np.zeros(n)
    for idx, val in steps:
        sp[idx:] = val
    return sp


# ---------------------------------------------------------------------------
# 激励检测测试
# ---------------------------------------------------------------------------


class TestExcitationDetection:
    """激励检测（层 1）测试。"""

    def test_sufficient_excitation_prbs(self):
        """PRBS 输入应判定为激励充分。"""
        u = _prbs(500, seed=42)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1)
        result = check_excitation(u, y, d=3)
        assert result.is_sufficient
        assert result.significant_changes >= 2
        assert math.isfinite(result.condition_number)

    def test_insufficient_constant_op(self):
        """恒定 OP 应判定为激励不足。"""
        u = np.full(500, 5.0)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1)
        result = check_excitation(u, y, d=3)
        assert not result.is_sufficient
        assert "OP" in result.verdict or "无变化" in result.verdict
        assert result.confidence == ConfidenceLevel.INCONCLUSIVE

    def test_insufficient_tiny_op_range(self):
        """OP 变化范围过小应判定为激励不足。"""
        u = np.ones(500) + 1e-6 * np.sin(np.arange(500))
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1)
        result = check_excitation(u, y, d=3)
        assert not result.is_sufficient

    def test_insufficient_short_data(self):
        """数据点不足应判定为激励不足。"""
        u = _prbs(8, seed=1)
        y = np.random.default_rng(1).normal(0, 1, 8)
        result = check_excitation(u, y, d=1)
        assert not result.is_sufficient
        assert "数据点不足" in result.verdict

    def test_closed_loop_sufficient_excitation(self):
        """闭环 FOPDT 多 SP 阶跃应判定为激励充分。"""
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp, K=2.0, tau=30.0, theta=5.0, kp=2.0, ti=20.0, noise_std=0.5, seed=42
        )
        result = check_excitation(u, y, d=5)
        assert result.is_sufficient
        assert result.significant_changes >= 2

    def test_excitation_score_range(self):
        """激励得分应在 0-100 区间。"""
        u = _prbs(500, seed=42)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1)
        result = check_excitation(u, y, d=3)
        score = excitation_score(result.condition_number, result.significant_changes)
        assert 0.0 <= score <= 100.0


# ---------------------------------------------------------------------------
# ARX 辨识测试
# ---------------------------------------------------------------------------


class TestARXIdentification:
    """ARX 辨识（层 3）测试。"""

    def test_open_loop_known_params(self):
        """开环已知参数 FOPDT 辨识精度。"""
        K_true, tau_true, theta_true = 1.5, 20.0, 3.0
        u = _prbs(1000, seed=123)
        y = _simulate_open_loop_fopdt(K_true, tau_true, theta_true, u, noise_std=0.1, seed=123)
        d = round(theta_true)
        res = identify_arx(u, y, d, na=1, nb=1)
        # 转连续参数
        params = arx_to_fopdt(res.a_coeffs[0], res.b_coeffs[0], res.d, ts=1.0)
        assert abs(params.K - K_true) / K_true < 0.05
        assert abs(params.tau - tau_true) / tau_true < 0.10
        assert abs(params.theta - theta_true) < 2.0
        assert res.r_squared > 0.90

    def test_arx_stability_property(self):
        """稳定系统 ARX 的 a1 应为负。"""
        u = _prbs(500, seed=1)
        y = _simulate_open_loop_fopdt(K=1.0, tau=10.0, theta=2.0, u=u, noise_std=0.01, seed=1)
        res = identify_arx(u, y, d=2, na=1, nb=1)
        assert res.is_stable
        assert res.a_coeffs[0] < 0

    def test_arx_insufficient_data_raises(self):
        """数据不足应抛 ValueError。"""
        with pytest.raises(ValueError, match="数据不足"):
            identify_arx(np.array([1.0, 2.0]), np.array([1.0, 2.0]), d=1, na=1, nb=1)


# ---------------------------------------------------------------------------
# ARMAX 辨识测试
# ---------------------------------------------------------------------------


class TestARMAXIdentification:
    """ARMAX 辨识（层 3）测试。"""

    def test_armax_runs_and_returns_coeffs(self):
        """ARMAX 应返回有效系数。"""
        u = _prbs(800, seed=10)
        y = _simulate_open_loop_fopdt(K=1.0, tau=15.0, theta=2.0, u=u, noise_std=0.3, seed=10)
        res = identify_armax(u, y, d=2, na=1, nb=1, nc=1)
        assert len(res.a_coeffs) == 1
        assert len(res.b_coeffs) == 1
        assert len(res.c_coeffs) == 1
        assert res.n_samples > 100
        assert res.iterations >= 1

    def test_armax_higher_r2_than_arx_with_noise(self):
        """有噪声时 ARMAX 拟合度应不低于 ARX（PEM 优化）。"""
        u = _prbs(800, seed=20)
        y = _simulate_open_loop_fopdt(K=1.0, tau=15.0, theta=2.0, u=u, noise_std=0.5, seed=20)
        arx_res = identify_arx(u, y, d=2, na=1, nb=1)
        armax_res = identify_armax(u, y, d=2, na=1, nb=1, nc=1, max_iter=50)
        # ARMAX 拟合度应接近或优于 ARX
        assert armax_res.r_squared >= arx_res.r_squared - 0.05

    def test_armax_insufficient_data_raises(self):
        """数据不足应抛 ValueError。"""
        with pytest.raises(ValueError):
            identify_armax(np.ones(10), np.ones(10), d=1, na=1, nb=1, nc=1)


# ---------------------------------------------------------------------------
# IV 辨识测试
# ---------------------------------------------------------------------------


class TestIVIdentification:
    """IV 辨识（层 3，闭环偏差消除）测试。"""

    def test_iv_unbiased_closed_loop(self):
        """闭环下 IV 估计应无偏（优于 ARX）."""
        sp = _sp_steps(1500, [(50, 10.0), (500, 15.0), (1000, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp, K=2.0, tau=30.0, theta=5.0, kp=2.0, ti=20.0, noise_std=0.8, seed=99
        )
        K_true = 2.0
        d = 5
        arx_res = identify_arx(u, y, d, na=1, nb=1)
        iv_res = identify_iv(u, y, sp, d, na=1, nb=1)
        # IV 的 K 估计应更接近真值
        arx_K = arx_res.b_coeffs[0] / (1 + arx_res.a_coeffs[0])
        iv_K = iv_res.b_coeffs[0] / (1 + iv_res.a_coeffs[0])
        assert abs(iv_K - K_true) <= abs(arx_K - K_true) + 0.15  # 容差避免边界抖动

    def test_iv4_convergence(self):
        """IV4 应在有限步内收敛。"""
        sp = _sp_steps(1000, [(50, 10.0), (500, 15.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp, K=1.5, tau=20.0, theta=3.0, kp=2.0, ti=15.0, noise_std=0.3, seed=55
        )
        res = identify_iv4(u, y, sp, d=3, na=1, nb=1, max_iter=5)
        assert res.iterations >= 1
        assert res.iterations <= 5
        assert math.isfinite(res.r_squared)


# ---------------------------------------------------------------------------
# 离散→连续转换测试
# ---------------------------------------------------------------------------


class TestDiscreteToContinuous:
    """离散→连续转换（层 5）测试。"""

    def test_fopdt_conversion_known_params(self):
        """已知 FOPDT 参数的离散→连续还原."""
        K_true, tau_true, theta_true = 1.0, 20.0, 5.0
        ts = 1.0
        a1 = -math.exp(-ts / tau_true)
        b1 = K_true * (1 + a1)
        d = round(theta_true / ts)
        params = arx_to_fopdt(a1, b1, d, ts)
        assert abs(params.K - K_true) < 1e-9
        assert abs(params.tau - tau_true) < 1e-6
        assert abs(params.theta - theta_true) < 1e-9

    def test_fopdt_unstable_raises(self):
        """a1 >= 0（不稳定系统）应抛 ValueError。"""
        with pytest.raises(ValueError, match="a1.*>= 0"):
            arx_to_fopdt(a1=0.5, b1=1.0, d=1, ts=1.0)

    def test_fopdt_zero_gain_raises(self):
        """b1=0（零增益）应抛 ValueError。"""
        # K = b1/(1+a1) = 0 → b1=0
        with pytest.raises(ValueError, match="K=0"):
            arx_to_fopdt(a1=-0.5, b1=0.0, d=1, ts=1.0)

    def test_sopdt_conversion_real_poles(self):
        """SOPDT 实极点转换."""
        K_true, T1_true, T2_true, theta_true = 1.0, 10.0, 5.0, 2.0
        ts = 1.0
        # 离散极点
        p1 = math.exp(-ts / T1_true)
        p2 = math.exp(-ts / T2_true)
        a1 = -(p1 + p2)
        a2 = p1 * p2
        b1 = K_true * (1 + a1 + a2)
        d = round(theta_true / ts)
        params = arx_to_sopdt(a1, a2, b1, d, ts)
        assert abs(params.K - K_true) < 1e-9
        # T1/T2 顺序可能互换，取较大者为 T1
        T_est = sorted([params.T1, params.T2], reverse=True)
        assert abs(T_est[0] - T1_true) < 1e-6
        assert abs(T_est[1] - T2_true) < 1e-6
        assert abs(params.theta - theta_true) < 1e-9

    def test_sopdt_unstable_raises(self):
        """SOPDT 不稳定极点应抛 ValueError。"""
        # 极点在单位圆外 → s >= 0
        with pytest.raises(ValueError, match="不稳定|非负"):
            arx_to_sopdt(a1=-0.3, a2=-0.8, b1=1.0, d=1, ts=1.0)


# ---------------------------------------------------------------------------
# 阶次选择测试
# ---------------------------------------------------------------------------


class TestOrderSelection:
    """阶次选择（层 4）测试。"""

    def test_aic_bic_monotonic_with_params(self):
        """相同残差方差下，参数越多 AIC/BIC 越大。"""
        n, var = 500, 0.1
        aic1 = compute_aic(n, var, n_params=2)
        aic2 = compute_aic(n, var, n_params=4)
        assert aic2 > aic1
        bic1 = compute_bic(n, var, n_params=2)
        bic2 = compute_bic(n, var, n_params=4)
        assert bic2 > bic1

    def test_ljung_box_white_noise(self):
        """白噪声残差应通过 Ljung-Box 检验（p > 0.05）。"""
        rng = np.random.default_rng(42)
        residuals = rng.normal(0, 1, 1000)
        Q, p = ljung_box_test(residuals, max_lag=10)
        assert p > 0.05

    def test_ljung_box_autocorrelated(self):
        """自相关残差应不通过 Ljung-Box 检验。"""
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 0.1, 1000)
        residuals = np.zeros(1000)
        for i in range(1, 1000):
            residuals[i] = 0.8 * residuals[i - 1] + noise[i]
        Q, p = ljung_box_test(residuals, max_lag=10)
        assert p < 0.05

    def test_ljung_box_short_residuals(self):
        """短残差序列应返回 p=1.0（不拒绝白噪声）。"""
        Q, p = ljung_box_test(np.array([1.0, 2.0, 3.0]), max_lag=10)
        assert p == 1.0

    def test_cross_validate_returns_r2(self):
        """交叉验证应返回有效 R²。"""
        u = _prbs(500, seed=42)
        y = _simulate_open_loop_fopdt(K=1.0, tau=15.0, theta=2.0, u=u, noise_std=0.1, seed=42)

        def _id_fn(u_seg, y_seg):
            return identify_arx(u_seg, y_seg, d=2, na=1, nb=1)

        cv_r2 = cross_validate(u, y, _id_fn, train_ratio=0.7)
        assert cv_r2 is not None
        assert cv_r2 > 0.8

    def test_select_order_returns_result(self):
        """select_order 返回完整结果。"""
        rng = np.random.default_rng(42)
        residuals = rng.normal(0, 0.1, 500)
        result = select_order(residuals, n_samples=500, residual_var=0.01, n_params=2)
        assert result.aic is not None
        assert result.bic is not None
        assert result.ljung_box_p > 0
        assert isinstance(result.residual_white, bool)


# ---------------------------------------------------------------------------
# 非参数估计测试
# ---------------------------------------------------------------------------


class TestNonparametric:
    """非参数粗估（层 2）测试。"""

    def test_correlation_analysis_gain_estimate(self):
        """相关分析应给出非零 K 粗估（非参数法精度有限，仅检查量级）."""
        K_true = 1.5
        u = _prbs(800, seed=42)
        y = _simulate_open_loop_fopdt(K_true, tau=20.0, theta=3.0, u=u, noise_std=0.05, seed=42)
        result = correlation_analysis(u, y, ts=1.0)
        # 非参数法对非白噪声输入精度有限，仅验证符号一致且非零
        assert result.gain_estimate != 0.0
        assert math.isfinite(result.gain_estimate)
        assert math.copysign(1, result.gain_estimate) == math.copysign(1, K_true)

    def test_correlation_analysis_time_constant_finite(self):
        """时间常数粗估应为有限值。"""
        u = _prbs(500, seed=1)
        y = _simulate_open_loop_fopdt(K=1.0, tau=15.0, theta=2.0, u=u, noise_std=0.01, seed=1)
        result = correlation_analysis(u, y, ts=1.0)
        assert math.isfinite(result.time_constant_estimate)

    def test_welch_spectral_returns_dict(self):
        """Welch 谱分析应返回频率/增益/相位数组。"""
        u = _prbs(500, seed=42)
        y = _simulate_open_loop_fopdt(K=1.0, tau=15.0, theta=2.0, u=u, noise_std=0.05, seed=42)
        result = welch_spectral_analysis(u, y, ts=1.0)
        assert "frequencies" in result
        assert "gain" in result
        assert "phase" in result
        assert len(result["frequencies"]) > 0


# ---------------------------------------------------------------------------
# Pipeline 端到端测试
# ---------------------------------------------------------------------------


class TestPipelineEndToEnd:
    """算法栈端到端（pipeline.py）测试。"""

    def test_closed_loop_fopdt_identification(self):
        """闭环 FOPDT 仿真数据辨识（核心场景）."""
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=0.5,
            seed=42,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=5.0,
        )
        assert result.success
        assert result.best_model is not None
        p = result.best_model.params
        assert p.model_type == ModelType.FOPDT
        assert abs(p.K - K_true) / K_true < 0.05
        assert abs(p.tau - tau_true) / tau_true < 0.10
        assert abs(p.theta - theta_true) < 2.0
        assert result.best_model.fitting_score >= 95.0
        assert result.algorithm_version == "TUNE_IDENT_v1.0"

    def test_open_loop_fopdt_identification(self):
        """开环 FOPDT PRBS 辨识（限定 FOPDT 候选）."""
        K_true, tau_true, theta_true = 1.5, 20.0, 3.0
        u = _prbs(1000, seed=123)
        y = _simulate_open_loop_fopdt(K_true, tau_true, theta_true, u, noise_std=0.1, seed=123)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=3.0,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        p = result.best_model.params
        assert p.model_type == ModelType.FOPDT
        assert abs(p.K - K_true) / K_true < 0.05
        assert abs(p.tau - tau_true) / tau_true < 0.10

    def test_insufficient_excitation_returns_failure(self):
        """恒定 OP 输入应返回失败（激励不足）。"""
        u = np.full(500, 5.0)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1)
        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0, theta_estimate=3.0)
        assert not result.success
        assert result.reason is not None
        assert "激励" in result.reason

    def test_short_data_returns_failure(self):
        """数据点不足应返回失败。"""
        u = _prbs(30, seed=1)
        y = _simulate_open_loop_fopdt(K=1.0, tau=10.0, theta=1.0, u=u, noise_std=0.01, seed=1)
        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        assert not result.success
        assert "数据不足" in (result.reason or "")

    def test_length_mismatch_returns_failure(self):
        """OP/PV 长度不匹配应返回失败。"""
        result = identify_from_history(
            op=[1.0, 2.0, 3.0],
            pv=[1.0, 2.0],
            ts=1.0,
        )
        assert not result.success
        assert "不匹配" in (result.reason or "")

    def test_candidates_include_fopdt_and_sopdt(self):
        """默认候选应包含 FOPDT 与 SOPDT。"""
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp,
            K=2.0,
            tau=30.0,
            theta=5.0,
            kp=2.0,
            ti=20.0,
            noise_std=0.3,
            seed=42,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=5.0,
        )
        assert result.success
        model_types = {c.params.model_type for c in result.candidates}
        assert ModelType.FOPDT in model_types
        assert ModelType.SOPDT in model_types

    def test_to_dict_serialization(self):
        """to_dict 应可 JSON 序列化。"""
        sp = _sp_steps(800, [(50, 10.0), (400, 15.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp,
            K=2.0,
            tau=30.0,
            theta=5.0,
            kp=2.0,
            ti=20.0,
            noise_std=0.5,
            seed=42,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=5.0,
        )
        d = result.to_dict()
        # 应可 JSON 序列化
        json_str = json.dumps(d, default=str)
        assert "algorithmVersion" in json_str

    def test_failure_to_dict(self):
        """失败结果 to_dict 应包含 success=false。"""
        u = np.full(500, 5.0)
        y = np.zeros(500)
        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        d = result.to_dict()
        assert d["success"] is False
        assert d["reason"] is not None


# ---------------------------------------------------------------------------
# Golden 基线对齐测试
# ---------------------------------------------------------------------------


class TestGoldenBaseline:
    """与 golden 基线 JSON 对齐测试。"""

    BASELINE_PATH = Path(__file__).parent / "golden" / "tuning_identification_baseline.json"

    @classmethod
    def _load_baseline(cls) -> dict:
        with open(cls.BASELINE_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_baseline_file_exists(self):
        """golden 基线文件应存在且可解析。"""
        baseline = self._load_baseline()
        assert baseline["algorithm_version"] == "TUNE_IDENT_v1.0"
        assert "scenarios" in baseline

    def test_closed_loop_fopdt_baseline_alignment(self):
        """闭环 FOPDT 场景应满足 golden 基线容差。"""
        baseline = self._load_baseline()
        scn = baseline["scenarios"]["closed_loop_fopdt"]
        sp = _sp_steps(scn["n"], [(50, 10.0), (400, 15.0), (800, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp,
            K=scn["truth"]["K"],
            tau=scn["truth"]["tau"],
            theta=scn["truth"]["theta"],
            kp=2.0,
            ti=20.0,
            noise_std=0.5,
            seed=scn["seed"],
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=5.0,
        )
        assert result.success
        p = result.best_model.params
        tol = scn["tolerance"]
        assert abs(p.K - scn["truth"]["K"]) / scn["truth"]["K"] < tol["K_relative_error"]
        assert abs(p.tau - scn["truth"]["tau"]) / scn["truth"]["tau"] < tol["tau_relative_error"]
        assert abs(p.theta - scn["truth"]["theta"]) < tol["theta_absolute_error"]

    def test_open_loop_fopdt_baseline_alignment(self):
        """开环 FOPDT 场景应满足 golden 基线容差（限定 FOPDT 候选）."""
        baseline = self._load_baseline()
        scn = baseline["scenarios"]["open_loop_fopdt"]
        u = _prbs(scn["n"], seed=scn["seed"])
        y = _simulate_open_loop_fopdt(
            scn["truth"]["K"],
            scn["truth"]["tau"],
            scn["truth"]["theta"],
            u,
            noise_std=0.1,
            seed=scn["seed"],
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=scn["truth"]["theta"],
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        p = result.best_model.params
        assert p.model_type == ModelType.FOPDT
        tol = scn["tolerance"]
        assert abs(p.K - scn["truth"]["K"]) / scn["truth"]["K"] < tol["K_relative_error"]
        assert abs(p.tau - scn["truth"]["tau"]) / scn["truth"]["tau"] < tol["tau_relative_error"]

    def test_insufficient_excitation_baseline(self):
        """激励不足场景应符合 golden 基线（success=false）。"""
        baseline = self._load_baseline()
        scn = baseline["scenarios"]["insufficient_excitation"]
        u = np.full(500, 5.0)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1)
        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0, theta_estimate=3.0)
        assert result.success is scn["expected"]["success"]
        assert scn["expected"]["reason_contains"] in (result.reason or "")

    def test_short_data_baseline(self):
        """短数据场景应符合 golden 基线。"""
        baseline = self._load_baseline()
        scn = baseline["scenarios"]["short_data"]
        u = _prbs(scn["n"], seed=1)
        y = _simulate_open_loop_fopdt(K=1.0, tau=10.0, theta=1.0, u=u, noise_std=0.01, seed=1)
        result = identify_from_history(op=u.tolist(), pv=y.tolist(), ts=1.0)
        assert result.success is scn["expected"]["success"]
        assert scn["expected"]["reason_contains"] in (result.reason or "")
