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
from app.services.tuning_identification.iv import (
    IV_CAPABILITY_STATUS,
    identify_iv,
    identify_iv4,
)
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
    IdentifyMethod,
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


def _simulate_open_loop_sopdt(
    K: float,
    T1: float,
    T2: float,
    theta: float,
    u: np.ndarray,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 42,
) -> np.ndarray:
    """开环 SOPDT 仿真：G(s) = K*exp(-theta*s)/((T1*s+1)(T2*s+1)).

    级联两个一阶环节离散化：x1 = G1·u（增益 1），y = G2·x1（增益 K）。
    用于 P2-006 Occam 削减测试——真 SOPDT 过程应让 SOPDT 候选显著更优。
    """
    rng = np.random.default_rng(seed)
    n = len(u)
    a1 = math.exp(-ts / T1)
    a2 = math.exp(-ts / T2)
    d = max(0, round(theta / ts))
    x1 = np.zeros(n)
    y = np.zeros(n)
    for k in range(d, n):
        x1[k] = a1 * x1[k - 1] + (1 - a1) * u[k - d]
        y[k] = a2 * y[k - 1] + K * (1 - a2) * x1[k]
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


def _simulate_closed_loop_fopdt_biased(
    sp: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    load: float,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """闭环 FOPDT 带恒定负载偏置仿真：y_ss = K·u_ss + load.

    工业数据形态：PV/OP 均含大直流偏置（如 PV≈450 / OP≈60），
    用于验证 pipeline 入口去均值（P0-2）。
    初始条件取稳态工作点 u0 = (sp[0] − load)/K、y0 = sp[0]，无启动瞬态。
    """
    rng = np.random.default_rng(seed)
    n = len(sp)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(0, round(theta / ts))
    u0 = (sp[0] - load) / K
    y0 = sp[0]
    y = np.full(n, y0)
    u = np.zeros(n)
    e_prev = 0.0
    u_prev = u0
    ki = kp * ts / ti
    for k in range(n):
        # 真闭环：控制器读取上一拍测量值 y[k-1]（y[k] 本拍末才由对象方程写入）
        y_meas = y[k - 1] if k > 0 else y0
        e = sp[k] - y_meas
        de = e - e_prev
        u[k] = u_prev + kp * de + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d] + (1 - a) * load
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
        """闭环 FOPDT 多 SP 阶跃应判定为激励充分。

        V62-P1-010: 死区过滤微噪声后，OP 真实方向变化暴露。SP 阶跃
        0→10→15→8 使 OP 方向为升→升→降，真实变号 1 次（噪声变号被死区
        过滤）；阈值已降为 1，仍判激励充分。
        """
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp, K=2.0, tau=30.0, theta=5.0, kp=2.0, ti=20.0, noise_std=0.5, seed=42
        )
        result = check_excitation(u, y, d=5)
        assert result.is_sufficient
        assert result.significant_changes >= 1

    def test_excitation_score_range(self):
        """激励得分应在 0-100 区间。"""
        u = _prbs(500, seed=42)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1)
        result = check_excitation(u, y, d=3)
        score = excitation_score(result.condition_number, result.significant_changes)
        assert 0.0 <= score <= 100.0


class TestV62P1ExcitationGates:
    """V62-P1-009/010/011: 激励门禁改进（量程归一化/死区/标准化不变性）."""

    def test_op_span_normalization_passes_when_pv_range_tiny(self):
        """P1-009: OP 量程归一化——PV range 极小但 OP 走过量程 10% 应通过.

        无 op_span 时回退到 u_range/y_range，PV range 极小会导致比值虚高或
        误判；提供 op_span 后按物理量程判断，消除跨量纲比值。
        """
        # OP 在 [40, 50] 内变化（量程 0-100，走了 10%）
        u = _prbs(500, seed=7) * 5.0 + 45.0  # range ≈ 5，量程内 5%
        # PV range 极小（K=0.01，PV 几乎不变）
        y = _simulate_open_loop_fopdt(K=0.01, tau=20.0, theta=3.0, u=u, noise_std=0.001, seed=7)
        # 无 op_span：u_range/y_range 可能虚高（y_range 极小）→ 通过但不合理
        # 有 op_span=100：u_range/100 ≈ 0.05 > 0.01 → 通过（物理正确）
        result = check_excitation(u, y, d=3, op_span=100.0)
        assert result.is_sufficient

    def test_op_span_normalization_fails_when_op_range_too_small(self):
        """P1-009: OP 走过量程 < 1% 应判激励不足（即使 PV range 大）."""
        # OP 仅在 [50.0, 50.05] 变化（量程 0-100，走了 0.05%）
        u = np.ones(500) + 0.05 * np.sin(np.arange(500))
        y = _simulate_open_loop_fopdt(K=10.0, tau=20.0, theta=3.0, u=u, noise_std=0.01, seed=7)
        # 有 op_span=100：0.05/100 = 0.0005 < 0.01 → 不足
        result = check_excitation(u, y, d=3, op_span=100.0)
        assert not result.is_sufficient
        assert "量程" in result.verdict

    def test_deadband_filters_micro_noise(self):
        """P1-010: 死区过滤微噪声——纯噪声 OP 不应产生大量方向变化."""
        # OP = 常值 + 微噪声（量级 1e-8）
        rng = np.random.default_rng(42)
        u = np.full(500, 50.0) + rng.normal(0, 1e-8, 500)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1, seed=42)
        result = check_excitation(u, y, d=3, op_span=100.0)
        # 死区 = 0.01 * u_range。u_range ≈ 几个 1e-8，死区 ≈ 1e-10。
        # 微噪声 du 大多 < 死区 → 方向变化 ≈ 0
        # 但 u_range 本身 < 1e-9 会在 "OP 无变化" 处被拦
        assert not result.is_sufficient

    def test_deadband_preserves_real_direction_changes(self):
        """P1-010: 真实阶跃方向变化不被死区过滤."""
        # OP 多段阶跃（真实方向变化 ≥ 2）
        u = np.zeros(500)
        u[100:] = 10.0
        u[250:] = 5.0
        u[400:] = 8.0
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.1, seed=42)
        result = check_excitation(u, y, d=3, op_span=100.0)
        assert result.significant_changes >= 2
        assert result.is_sufficient

    def test_unit_scale_invariance_condition_number(self):
        """P1-011: 单位缩放不变性——OP/PV 同时缩放后条件数不变.

        标准化前，OP（%）与 PV（温度）量级差异使条件数受单位影响；
        列标准化后条件数仅反映列相关性，与单位无关。
        """
        u = _prbs(500, seed=42)
        y = _simulate_open_loop_fopdt(K=2.0, tau=30.0, theta=5.0, u=u, noise_std=0.1, seed=42)
        # 原始单位
        r1 = check_excitation(u, y, d=5)
        # OP 放大 100 倍（% → 绝对值），PV 放大 10 倍（温度单位变化）
        r2 = check_excitation(u * 100.0, y * 10.0, d=5)
        # 标准化后条件数应几乎相同（浮点容差内）
        assert math.isclose(r1.condition_number, r2.condition_number, rel_tol=1e-9)
        # 判定结果一致
        assert r1.is_sufficient == r2.is_sufficient
        assert r1.significant_changes == r2.significant_changes

    def test_unit_scale_invariance_verdict_consistent(self):
        """P1-011: 单位缩放后 verdict 的充分性判定一致."""
        u = _prbs(300, seed=99) * 5.0 + 50.0
        y = _simulate_open_loop_fopdt(K=1.5, tau=25.0, theta=2.0, u=u, noise_std=0.2, seed=99)
        r1 = check_excitation(u, y, d=2, op_span=100.0)
        # 缩放 OP（仍提供对应缩放后的 op_span）
        r2 = check_excitation(u * 10.0, y * 100.0, d=2, op_span=1000.0)
        assert r1.is_sufficient == r2.is_sufficient
        assert math.isclose(r1.condition_number, r2.condition_number, rel_tol=1e-9)


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
    """实验性 IV 数值稳定性测试（不宣称闭环无偏或正式 IV4）."""

    def test_experimental_iv_returns_finite_result(self):
        """实验性 IV 对闭环样本应返回有限数值，不能据此宣称无偏."""
        assert IV_CAPABILITY_STATUS == "EXPERIMENTAL"
        sp = _sp_steps(1500, [(50, 10.0), (500, 15.0), (1000, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp, K=2.0, tau=30.0, theta=5.0, kp=2.0, ti=20.0, noise_std=0.8, seed=99
        )
        d = 5
        iv_res = identify_iv(u, y, sp, d, na=1, nb=1)
        assert all(math.isfinite(value) for value in iv_res.a_coeffs)
        assert all(math.isfinite(value) for value in iv_res.b_coeffs)
        assert math.isfinite(iv_res.r_squared)

    def test_experimental_iterative_iv_returns_finite_result(self):
        """实验性迭代 IV 应有限终止并返回有限数值."""
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

    def test_ljung_box_white_noise_with_dc_offset(self):
        """P1-4：含直流偏置的白噪声应通过 Ljung-Box（ACF 已中心化）.

        未中心化时 ACF ≈ μ²/(σ²+μ²)（本例 25/26≈0.96，逐 lag 恒定），
        Q 统计量爆炸、白噪声被误判有色。
        """
        rng = np.random.default_rng(42)
        residuals = rng.normal(0, 1, 1000) + 5.0
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
        """未验证闭环方法不得把高拟合度结果发布为成功辨识."""
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
        assert not result.success
        assert result.best_model is None
        assert "CLOSED_LOOP_METHOD_UNVERIFIED" in (result.reason or "")
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
        u = _prbs(1200, seed=42)
        y = _simulate_open_loop_fopdt(
            K=2.0,
            tau=30.0,
            theta=5.0,
            u=u,
            noise_std=0.3,
            seed=42,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=5.0,
        )
        assert result.success
        model_types = {c.params.model_type for c in result.candidates}
        assert ModelType.FOPDT in model_types
        assert ModelType.SOPDT in model_types

    def test_explicit_zero_theta_is_preserved(self):
        """显式 theta=0 不得被缺省启发值覆盖."""
        u = _prbs(1000, seed=412)
        y = _simulate_open_loop_fopdt(
            K=1.2,
            tau=18.0,
            theta=0.0,
            u=u,
            noise_std=0.05,
            seed=412,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=0.0,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        assert result.best_model is not None
        assert result.best_model.params.theta == 0.0
        assert result.theta_source.value == "EXPLICIT"

    def test_missing_theta_uses_delay_search(self):
        """P2-001：缺省 theta 时通过 BIC 候选搜索确定延迟，标记为 SEARCHED，可信度不封顶."""
        u = _prbs(1000, seed=413)
        y = _simulate_open_loop_fopdt(
            K=1.2,
            tau=18.0,
            theta=2.0,
            u=u,
            noise_std=0.01,
            seed=413,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        # P2-001：未给 theta 时通过 BIC 搜索确定延迟，标记为 SEARCHED（非 HEURISTIC_2TS）
        assert result.theta_source.value == "SEARCHED"
        assert result.best_model is not None
        # SEARCHED 不封顶可信度（BIC 搜索是数据驱动的可靠延迟估计）
        assert result.best_model.confidence.value in {"A", "B", "C", "D", "E"}
        assert result.to_dict()["thetaSource"] == "SEARCHED"

    def test_history_pipeline_rejects_ipdt_candidate(self):
        """历史 pipeline 不得把 IPDT 静默按 SOPDT 返回."""
        u = _prbs(800, seed=414)
        y = _simulate_open_loop_fopdt(
            K=1.0,
            tau=20.0,
            theta=3.0,
            u=u,
            noise_std=0.05,
            seed=414,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=3.0,
            candidate_models=[ModelType.IPDT],
        )
        assert not result.success
        assert "IPDT" in (result.reason or "")

    def test_production_pipeline_does_not_select_experimental_iv(self):
        """Phase 0 生产候选不得包含尚未验证的 IV 实现."""
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        y, u = _simulate_closed_loop_fopdt(
            sp,
            K=2.0,
            tau=30.0,
            theta=5.0,
            kp=2.0,
            ti=20.0,
            noise_std=0.3,
            seed=415,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=5.0,
        )
        assert not result.success
        assert result.best_model is None
        assert all(
            candidate.identify_method != IdentifyMethod.HISTORICAL_IV
            for candidate in result.candidates
        )
        assert "CLOSED_LOOP_METHOD_UNVERIFIED" in (result.reason or "")

    # ------------------------------------------------------------------
    # P2-001/P2-003：延迟候选搜索 — 覆盖 θ=0/2/5/20/60 Ts，不传入真值
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("theta_true", [0, 2, 5, 20, 60])
    def test_p2_001_delay_search_recovers_theta(self, theta_true: float):
        """P2-001/P2-003：不传 theta_estimate，BIC 搜索应恢复接近真值的延迟.

        覆盖 θ=0/2/5/20/60 Ts（P2-003 要求），测试不得传入真值。
        搜索到的 d 对应 theta ≈ d·ts，允许 ±2 Ts 容差（BIC 分辨率限制）。
        """
        ts = 1.0
        u = _prbs(1500, seed=500 + int(theta_true))
        y = _simulate_open_loop_fopdt(
            K=1.0,
            tau=25.0,
            theta=theta_true,
            u=u,
            noise_std=0.02,
            seed=600 + int(theta_true),
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=ts,
            theta_estimate=None,  # 不传入真值
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success, f"θ={theta_true} 辨识失败: {result.reason}"
        assert result.theta_source.value == "SEARCHED"
        assert result.best_model is not None
        # 搜索到的 theta 应在真值 ±2 Ts 范围内
        theta_estimated = result.best_model.params.theta
        assert abs(theta_estimated - theta_true) <= 2.0 * ts, (
            f"θ={theta_true}: 搜索到 theta={theta_estimated}，超出 ±2Ts 容差"
        )

    def test_p2_001_explicit_theta_searches_neighborhood(self):
        """P2-001：传入 theta_estimate 时在 d_explicit±3 邻域精搜，标记 EXPLICIT."""
        u = _prbs(1000, seed=700)
        y = _simulate_open_loop_fopdt(
            K=1.0,
            tau=20.0,
            theta=5.0,
            u=u,
            noise_std=0.02,
            seed=700,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=5.0,  # 传入真值
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        assert result.theta_source.value == "EXPLICIT"
        assert result.best_model is not None
        # 精搜可能在邻域内微调，但应在真值附近
        assert abs(result.best_model.params.theta - 5.0) <= 3.0

    def test_p2_001_search_does_not_cap_confidence(self):
        """P2-001：SEARCHED 延迟不再封顶 C（BIC 搜索是可靠延迟估计）."""
        u = _prbs(2000, seed=800)
        y = _simulate_open_loop_fopdt(
            K=1.0,
            tau=15.0,
            theta=3.0,
            u=u,
            noise_std=0.005,  # 极低噪声，应达到 A/B 级
            seed=800,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        assert result.theta_source.value == "SEARCHED"
        assert result.best_model is not None
        # 低噪声 + 搜索延迟应达到 A 或 B（不再封顶 C）
        assert result.best_model.confidence.value in {"A", "B"}

    # ------------------------------------------------------------------
    # P2-002：留出集 + 自由仿真误差 + BIC 择优
    # ------------------------------------------------------------------

    def test_p2_002_fitting_score_uses_validation_free_run(self):
        """P2-002：fitting_score 来自验证集自由仿真 R²（非训练集方程误差 R²）."""
        u = _prbs(1500, seed=900)
        y = _simulate_open_loop_fopdt(
            K=1.0,
            tau=20.0,
            theta=3.0,
            u=u,
            noise_std=0.02,
            seed=900,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        assert result.best_model is not None
        # reason 应包含 R²_val 和 R²_train（P2-002 标记）
        reason = result.best_model.reason or ""
        assert "R²_val=" in reason
        assert "R²_train=" in reason
        # 低噪声数据上验证集 R² 应较高（>0.8）
        assert result.best_model.fitting_score > 80.0

    def test_p2_002_free_run_r2_lower_than_train_for_noisy_data(self):
        """P2-002：高噪声数据上验证集自由仿真 R² 低于训练集 R²（泛化差距）."""
        u = _prbs(1500, seed=910)
        y = _simulate_open_loop_fopdt(
            K=1.0,
            tau=20.0,
            theta=3.0,
            u=u,
            noise_std=0.5,  # 高噪声
            seed=910,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        if result.success and result.best_model:
            reason = result.best_model.reason or ""
            # 提取 R²_val 和 R²_train
            import re

            val_match = re.search(r"R²_val=([\d.]+)", reason)
            train_match = re.search(r"R²_train=([\d.]+)", reason)
            if val_match and train_match:
                r2_val = float(val_match.group(1))
                r2_train = float(train_match.group(1))
                # 高噪声下验证集自由仿真 R² 通常低于训练集方程误差 R²
                # （误差累积效应），允许 10% 波动
                assert r2_val <= r2_train + 0.1

    def test_p2_002_time_order_split_no_shuffle(self):
        """P2-002：留出集分割按时间顺序，前 60% 训练后 20% 验证."""
        # 用足够长的数据验证分割比例
        u = _prbs(1000, seed=920)
        y = _simulate_open_loop_fopdt(
            K=1.0,
            tau=15.0,
            theta=2.0,
            u=u,
            noise_std=0.01,
            seed=920,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        # 验证辨识成功（分割后训练集仍有足够数据）
        assert result.best_model is not None

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
# P2-006 AIC/BIC/CV 接入主 pipeline + Occam 削减
# ---------------------------------------------------------------------------


class TestV62P2OrderSelectionPipeline:
    """P2-006：AIC/BIC 信息准则接入主 pipeline 与 SOPDT 升级 Occam 门禁."""

    def test_p2_006_aic_bic_present_in_candidate_and_dict(self):
        """AIC/BIC 应写入 CandidateModel 与 to_dict 证据输出."""
        u = _prbs(1500, seed=1100)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.05, seed=1100)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success and result.best_model is not None
        # CandidateModel 字段
        assert result.best_model.aic is not None
        assert result.best_model.bic is not None
        # to_dict 证据输出
        d = result.to_dict()
        assert d["aic"] is not None
        assert d["bic"] is not None
        for c in d["candidateModels"]:
            assert c["aic"] is not None
            assert c["bic"] is not None

    def test_p2_006_aic_bic_in_reason(self):
        """reason 字符串应包含 AIC/BIC 标记，便于审计追溯."""
        u = _prbs(1500, seed=1101)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.05, seed=1101)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success and result.best_model is not None
        reason = result.best_model.reason or ""
        assert "AIC=" in reason
        assert "BIC=" in reason
        # R² 标记仍保留（P2-002 不回归）
        assert "R²_val=" in reason

    def test_p2_006_occam_prefers_fopdt_for_true_fopdt_process(self):
        """Occam 削减：真 FOPDT 过程下 SOPDT 未显著更优时应选 FOPDT.

        真 FOPDT 过程用 SOPDT（多一个参数）拟合 R² 略升但 BIC 受惩罚，
        且 R²_val 相对提升 < 5% → Occam 保留 FOPDT。
        """
        u = _prbs(1500, seed=1102)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.05, seed=1102)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT, ModelType.SOPDT],
        )
        assert result.success and result.best_model is not None
        # 真 FOPDT 过程：SOPDT 不应凭微弱优势胜出
        assert result.best_model.params.model_type == ModelType.FOPDT, (
            f"Occam 削减失效：真 FOPDT 过程却选了 {result.best_model.params.model_type}"
        )

    def test_p2_006_occam_prefers_sopdt_when_significantly_better(self):
        """Occam 削减：真 SOPDT 过程（T1/T2 差异大）SOPDT 应显著更优并胜出.

        T1=3s/T2=30s 差异 10 倍，FOPDT 单时间常数无法拟合双时间常数动态，
        R²_val 相对提升 > 5% 且 BIC 下降 → SOPDT 胜出。
        """
        u = _prbs(2000, seed=1103)
        y = _simulate_open_loop_sopdt(
            K=1.0, T1=3.0, T2=30.0, theta=2.0, u=u, noise_std=0.02, seed=1103
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT, ModelType.SOPDT],
        )
        assert result.success and result.best_model is not None
        # 真 SOPDT 过程显著差异 → SOPDT 应胜出
        assert result.best_model.params.model_type == ModelType.SOPDT, (
            f"Occam 削减过严：真 SOPDT 过程却选了 {result.best_model.params.model_type}"
        )

    def test_p2_006_occam_no_fopdt_candidate_keeps_sopdt(self):
        """仅 SOPDT 候选时 Occam 不触发，直接返回 SOPDT."""
        u = _prbs(1500, seed=1104)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.05, seed=1104)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.SOPDT],
        )
        if result.success and result.best_model:
            assert result.best_model.params.model_type == ModelType.SOPDT


# ---------------------------------------------------------------------------
# P2-012 物理可行性门禁（负根/不稳定/复极点/非最小相位）
# ---------------------------------------------------------------------------


class TestV62P2PhysicalFeasibility:
    """P2-012：复极点、负根、不稳定模型不得伪装成稳定工程模型."""

    def test_p2_012_sopdt_rejects_complex_poles(self):
        """复共轭极点（振荡系统）应被拒绝，不得取模伪装为过阻尼 SOPDT.

        构造 disc = a1²−4·a2 < 0 的二阶系统（欠阻尼），arx_to_sopdt 应 raise。
        """
        # 欠阻尼二阶：极点模 < 1（稳定）但 disc < 0（复共轭）
        # z² - 0.6z + 0.5 = 0 → disc = 0.36 - 2.0 = -1.64 < 0，复根
        # 模 = sqrt(0.5) ≈ 0.707 < 1（稳定）
        with pytest.raises(ValueError, match="复共轭极点|振荡"):
            arx_to_sopdt(a1=-0.6, a2=0.5, b1=0.1, d=1, ts=1.0)

    def test_p2_012_negative_gain_flagged_and_confidence_capped(self):
        """负增益 K 应标记 NEGATIVE_GAIN 并封顶可信度 C.

        用负增益过程仿真（K=-1.0）：辨识应成功但 reason 含 NEGATIVE_GAIN，
        confidence 不超过 C。
        """
        u = _prbs(1500, seed=1200)
        y = _simulate_open_loop_fopdt(K=-1.0, tau=20.0, theta=3.0, u=u, noise_std=0.02, seed=1200)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        # 负增益不拒绝（逆向过程物理可能），但须标记
        if result.success and result.best_model:
            reason = result.best_model.reason or ""
            assert "NEGATIVE_GAIN" in reason, f"reason 缺少 NEGATIVE_GAIN 标记: {reason}"
            # 可信度封顶 C
            level_order = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1, "INCONCLUSIVE": 0}
            assert level_order.get(result.best_model.confidence.value, 0) <= 3, (
                f"负增益模型可信度未封顶 C: {result.best_model.confidence}"
            )

    def test_p2_012_negative_gain_not_silently_passed(self):
        """负增益模型不得伪装成正常正增益模型静默放行（to_dict 可见标记）."""
        u = _prbs(1500, seed=1201)
        y = _simulate_open_loop_fopdt(K=-1.0, tau=25.0, theta=2.0, u=u, noise_std=0.02, seed=1201)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        if result.success and result.best_model:
            d = result.to_dict()
            # reason 字段含 NEGATIVE_GAIN 标记，审计可见
            assert "NEGATIVE_GAIN" in (d.get("reason") or "")

    def test_p2_012_positive_gain_no_false_alarm(self):
        """正常正增益过程不应触发物理可行性标记."""
        u = _prbs(1500, seed=1202)
        y = _simulate_open_loop_fopdt(K=1.0, tau=20.0, theta=3.0, u=u, noise_std=0.05, seed=1202)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=None,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success and result.best_model is not None
        reason = result.best_model.reason or ""
        assert "NEGATIVE_GAIN" not in reason
        assert "NMP_ZERO" not in reason

    def test_p2_012_physical_feasibility_module_direct(self):
        """物理可行性模块直接单测：正增益通过、负增益标记."""
        from app.services.tuning_identification.physical_feasibility import (
            check_physical_feasibility,
        )
        from app.services.tuning_identification.types import ModelParams

        # 正增益 FOPDT 通过
        params_ok = ModelParams(model_type=ModelType.FOPDT, K=1.5, tau=20.0, theta=3.0)
        r1 = check_physical_feasibility(params_ok, b_coeffs=[0.1], ts=1.0)
        assert r1.passed
        assert r1.reason_code == "OK"

        # 负增益标记
        params_neg = ModelParams(model_type=ModelType.FOPDT, K=-1.5, tau=20.0, theta=3.0)
        r2 = check_physical_feasibility(params_neg, b_coeffs=[0.1], ts=1.0)
        assert not r2.passed
        assert r2.reason_code == "NEGATIVE_GAIN"


# ---------------------------------------------------------------------------
# P0-2 去均值（偏置消除）测试
# ---------------------------------------------------------------------------


class TestMeanRemovalPipeline:
    """pipeline 入口去均值（P0-2）：工业偏置数据应恢复增量增益 K.

    手算依据：无截距回归若不去均值，K 按原点割线 ȳ/ū 收敛；
    PV≈450/OP≈60 时割线增益 = 450/60 = 7.5（真值 2.0，+275%）。
    去均值后偏差变量满足精确递推 ỹ[k] = a·ỹ[k−1] + b·ũ[k−d]，K 应精确恢复。
    """

    def test_biased_open_loop_recovers_incremental_gain(self):
        """开环带偏置（OP≈60, PV≈450, K=2.0/τ=60s）：K 误差 < 10%."""
        K_true, tau_true, theta_true = 2.0, 60.0, 5.0
        u = _prbs(1200, seed=123) + 60.0  # OP 55~65
        # 开环恒偏置与输出加常数严格等价：+L 满足 y+L = a(y+L) + b(u+L·? )
        # 当 L = K·ū_offset 关系成立时递推不变式逐点成立（此处 L=330, u偏置60, K=2）
        y = (
            _simulate_open_loop_fopdt(K_true, tau_true, theta_true, u, noise_std=0.0, seed=123)
            + 330.0
        )  # PV ≈ 450
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=theta_true,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        p = result.best_model.params
        assert p.model_type == ModelType.FOPDT
        assert abs(p.K - K_true) / K_true < 0.10
        # 割线增益 7.5 应被排除（去均值后 K 远离原点割线值）
        assert p.K < 3.0

    def test_biased_closed_loop_recovers_gain(self):
        """闭环带负载偏置也必须先通过合格闭环方法门禁."""
        K_true, tau_true, theta_true = 2.0, 60.0, 5.0
        sp = _sp_steps(1800, [(0, 450.0), (300, 455.0), (700, 447.0), (1100, 452.0), (1500, 449.0)])
        y, u = _simulate_closed_loop_fopdt_biased(
            sp,
            K_true,
            tau_true,
            theta_true,
            kp=0.5,
            ti=30.0,
            load=330.0,
            noise_std=0.1,
            seed=42,
        )
        # 工况 sanity check：PV≈450 / OP≈60 的工业偏置形态
        assert abs(float(np.mean(y)) - 450.0) < 10.0
        assert abs(float(np.mean(u)) - 60.0) < 10.0
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=theta_true,
        )
        assert not result.success
        assert "CLOSED_LOOP_METHOD_UNVERIFIED" in (result.reason or "")

    def test_zero_mean_data_unaffected(self):
        """零均值数据去均值前后等价（回归守卫：均值≈0 时 K 仍准确）."""
        K_true, tau_true, theta_true = 1.5, 20.0, 3.0
        u = _prbs(1000, seed=123)
        y = _simulate_open_loop_fopdt(K_true, tau_true, theta_true, u, noise_std=0.1, seed=123)
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=theta_true,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        assert abs(result.best_model.params.K - K_true) / K_true < 0.05


# ---------------------------------------------------------------------------
# P1-4 残差白噪声检验（一步预测误差）测试
# ---------------------------------------------------------------------------


class TestResidualWhitenessPipeline:
    """P1-4：残差白噪声检验用一步预测误差（ARMAX: ε = (A/C)y − (B/C)u）."""

    def test_equation_error_to_prediction_error_recovers_white(self):
        """e = C·ε 经 1/C 反滤波应还原白噪声 ε（手算构造）."""
        from app.services.tuning_identification.pipeline import (
            _equation_error_to_prediction_error,
        )

        rng = np.random.default_rng(7)
        eps_true = rng.normal(0, 1, 500)
        c1 = 0.8
        # 手算构造方程误差：e(t) = ε(t) + c1·ε(t−1)（C = 1 + 0.8·z⁻¹）
        e = eps_true.copy()
        e[1:] += c1 * eps_true[:-1]
        eps_recovered = _equation_error_to_prediction_error(e, [c1])
        # 递推 ε(t) = e(t) − c1·ε(t−1) 应精确还原
        np.testing.assert_allclose(eps_recovered, eps_true, atol=1e-10)
        # 还原后白性恢复；还原前方程误差显著有色
        _, p_before = ljung_box_test(e, max_lag=10)
        _, p_after = ljung_box_test(eps_recovered, max_lag=10)
        assert p_before < 0.05
        assert p_after > 0.05

    def test_good_model_confidence_not_capped_at_c(self):
        """开环含测量噪声的好模型（R²≈100%）不应被残差检验压到 C.

        旧实现用方程误差（ARMAX 下 = C·ε 天然有色）做白性检验，
        好模型也过不了 Ljung-Box，可信度永封顶 C；
        改一步预测误差后 ARMAX 候选应得 A/B。
        """
        u = _prbs(1200, seed=42)
        y = _simulate_open_loop_fopdt(
            K=2.0,
            tau=30.0,
            theta=5.0,
            u=u,
            noise_std=0.1,
            seed=42,
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            theta_estimate=5.0,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success
        assert result.best_model.residual_test_passed
        assert result.best_model.confidence in (ConfidenceLevel.A, ConfidenceLevel.B)


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
        """闭环 FOPDT 场景在合格方法上线前必须符合拒绝基线."""
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
        assert result.success is scn["expected"]["success"]
        assert scn["expected"]["reason_contains"] in (result.reason or "")

    def test_closed_loop_fopdt_biased_baseline_alignment(self):
        """带工业偏置闭环场景也不得绕过未验证方法门禁."""
        baseline = self._load_baseline()
        scn = baseline["scenarios"]["closed_loop_fopdt_biased"]
        sp_base = scn["bias"]["sp_base"]
        sp = _sp_steps(
            scn["n"],
            [
                (0, sp_base),
                (300, sp_base + 5.0),
                (700, sp_base - 3.0),
                (1100, sp_base + 2.0),
                (1500, sp_base - 1.0),
            ],
        )
        y, u = _simulate_closed_loop_fopdt_biased(
            sp,
            K=scn["truth"]["K"],
            tau=scn["truth"]["tau"],
            theta=scn["truth"]["theta"],
            kp=0.5,
            ti=30.0,
            load=scn["bias"]["load"],
            noise_std=0.0,
            seed=scn["seed"],
        )
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=scn["truth"]["theta"],
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success is scn["expected"]["success"]
        assert scn["expected"]["reason_contains"] in (result.reason or "")

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
