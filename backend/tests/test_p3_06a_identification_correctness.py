"""P3-06a 被动辨识·正确性子卡 oracle 测试（执行包「系统改造优化-2026-10-10」）.

正本：执行状态/P3-06-07-oracle冻结草案-20261011.md §2（用户 2026-10-11
批复"全部按推荐"——R1/R6/R7/R16 冻结值一字不差实施，R15 特别条款：
数值不得事后迁就实现结果调整）。

oracle 独立性纪律（§0.2）：
- 全部为**合成已知真系统 + 独立手算/解析参考**，禁止被测实现自产期望值；
- 合成数据生成器独立于被测估计器：FOPDT 用 scipy ZOH 精确离散化
  （cont2discrete+dlsim，非 ARX 递归），并先用**解析阶跃响应**核对生成器
  自身（TestOracleGeneratorChecks）；闭环用独立增量式 PI 仿真器
  （非 tuning_algorithms.simulate_closed_loop）；IPDT 漂移用独立解析斜率
  参考核对；
- 9 窗回归种子不是成功率证明；本文全部为合成反例 + 独立参考。

冻结容差（R16，§2.3）：
- O-A1：K ≤5%（SNR≥20dB）/ ≤10%（10dB）；τ/T1/T2 ≤10%/≤20%；
  θ ≤ max(2×ts, 10%×θ)；
- O-A2/O-A4：闭环/含漂移场景 K ≤10%（O-A4）/ 闭环 ≤15%（R16 组）；
- O-A3：A/B/C 系数相对误差 ≤10%；创新序列 Ljung-Box p>0.05。

停点登记（P3-06a-S1，任务书 §5 冻结值实施冲突）：
- O-A1 SOPDT 档（T1/T2 ≤10%/≤20% @ 白输入 + 白量测噪声 20/10dB）在
  冻结方法集（R9/P-20：仅 ARX/ARMAX/CLIVC，SRIVC 不引入）内系统性
  不可达：ARX na=2 对输出量测噪声有渐近 errors-in-variables 偏差
  （与样本量无关），经慢极点共线放大后第二极点稳定偏负/复（实测 12+
  种真值/采样配置，ARX 全数负极点转换失败、ARMAX K 偏差 15%~124%）；
  噪声less 数据恢复精确（<0.1%）证明模型类与实现正确，缺陷在噪声下的
  估计方法本身。按 R15/R16 冻结条款不改容差、不引新方法（R9 归 06c/
  裁决），测试按冻结容差编写并 xfail(strict=True) 登记，待主协调裁决。
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import signal as sps

from app.services.tuning_identification.armax import identify_armax
from app.services.tuning_identification.order_selection import ljung_box_test
from app.services.tuning_identification.pipeline import (
    ALGORITHM_VERSION,
    _u_near_white,
    identify_from_history,
)
from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    ModelType,
)

# ---------------------------------------------------------------------------
# 冻结容差（R16，一字不差；禁失败后调整——R15）
# ---------------------------------------------------------------------------
OA1_K_TOL = {20.0: 0.05, 10.0: 0.10}  # K 相对误差：20dB ≤5% / 10dB ≤10%
OA1_TAU_TOL = {20.0: 0.10, 10.0: 0.20}  # τ/T1/T2 相对误差：≤10% / ≤20%
OA4_K_TOL = 0.10  # O-A4 IPDT 含漂移场景 K ≤10%
OA2_CLOSED_LOOP_TOL = 0.15  # R16 闭环场景参数恢复 ≤15%
OA3_COEFF_TOL = 0.10  # O-A3 A/B/C 系数相对误差 ≤10%


def _theta_tol(ts: float, theta: float) -> float:
    """θ 恢复容差（R16 冻结）：≤ max(2×ts, 10%×θ)."""
    return max(2.0 * ts, 0.10 * theta)


# ---------------------------------------------------------------------------
# 独立 oracle 生成器（§0.2：独立于被测估计器）
# ---------------------------------------------------------------------------


def _white_u(n: int, seed: int, amp: float = 5.0) -> np.ndarray:
    """白高斯输入（O-A1 白输入档）."""
    return np.random.default_rng(seed).normal(0.0, amp, n)


def _zoh_fopdt(
    K: float,
    tau: float,
    theta: float,
    u: np.ndarray,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    """FOPDT 精确 ZOH 仿真（scipy cont2discrete + dlsim，非 ARX 递归）.

    G(s) = K·exp(-θs)/(τs+1)；连续实现 num=K/τ, den=[1, 1/τ]；
    死时间用输入纯延迟实现（θ/ts 样本）。
    """
    rng = np.random.default_rng(seed)
    b, a, _ = sps.cont2discrete(([K / tau], [1.0, 1.0 / tau]), ts, method="zoh")
    b = np.atleast_1d(b.flatten())
    a = np.atleast_1d(a.flatten())
    d = int(round(theta / ts))
    n = len(u)
    ud = np.zeros(n)
    ud[d:] = u[: n - d]
    _, y0 = sps.dlsim((b, a, ts), ud)
    y = y0.flatten()
    if noise_std > 0:
        y = y + rng.normal(0, noise_std, n)
    return y


def _cascade_sopdt(
    K: float,
    T1: float,
    T2: float,
    theta: float,
    u: np.ndarray,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    """SOPDT 仿真：两个一阶环节级联（每块对采样输入精确一阶差分）.

    离散系统 B(z⁻¹)=b1·z⁻ᵈ（nb=1）——恰为被测估计器的模型类真系统族
    （无 ZOH 分子零点失配）；生成器仍独立于估计器（状态递推 vs 回归）。
    连续域一致性由 TestOracleGeneratorChecks 以解析阶跃响应核对。
    """
    rng = np.random.default_rng(seed)
    a1 = math.exp(-ts / T1)
    a2 = math.exp(-ts / T2)
    d = max(0, round(theta / ts))
    x1 = np.zeros(len(u))
    y = np.zeros(len(u))
    for k in range(d, len(u)):
        x1[k] = a1 * x1[k - 1] + (1 - a1) * u[k - d]
        y[k] = a2 * y[k - 1] + K * (1 - a2) * x1[k]
    if noise_std > 0:
        y = y + rng.normal(0, noise_std, len(u))
    return y


def _independent_pi_closed_loop(
    sp: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """独立增量式 PI 闭环仿真器（O-A2；非 tuning_algorithms.simulate_closed_loop）.

    控制器读上一拍测量（真实反馈时序）；对象为 FOPDT 一阶差分。
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
        # 控制器读上一拍测量（DCS 扫描周期现实；读同期 y[k] 会注入
        # u[k]←y[k] 代数相关，方程误差 BIC 系统性偏好 d=0）
        y_meas = y[k - 1] if k > 0 else 0.0
        e = sp[k] - y_meas
        de = e - e_prev
        u[k] = u_prev + kp * de + ki * e
        u_prev = u[k]
        e_prev = e
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d]
    if noise_std > 0:
        y = y + rng.normal(0, noise_std, n)
    return y, u


def _armax_form_signal(
    a1: float,
    b1: float,
    c1: float,
    d: int,
    u: np.ndarray,
    seed: int,
    ar2_c2: float = 0.0,
) -> np.ndarray:
    """ARMAX 形合成（O-A3）：A·y = B·u + C·ε，C = 1 + c1·z⁻¹ (+ c2·z⁻²).

    噪声通道 v = C·ε 为 MA 形独立递推（v[k] = ε[k] + c1·ε[k-1] +
    c2·ε[k-2]）——标准 ARMAX 约定下方程误差恰为有限阶 C·ε，真值 C 已知
    有限，估计恢复可对 10% 冻结容差逐系数判定。被测 identify_armax 的
    C 系数约定同此（e = C·ε，estimator c-step 拟合 e(t)+c1·e(t-1)=ε）。
    """
    rng = np.random.default_rng(seed)
    n = len(u)
    eps = rng.normal(0, 1.0, n)
    v = np.zeros(n)
    for k in range(1, n):
        v[k] = eps[k] + c1 * eps[k - 1] + ar2_c2 * eps[k - 2]
    y = np.zeros(n)
    for k in range(d + 1, n):
        y[k] = -a1 * y[k - 1] + b1 * u[k - d] + v[k]
    return y


def _ipdt_with_drift(
    K: float,
    theta: float,
    op_bias: float,
    load_ramp_pv_per_s: float,
    u: np.ndarray,
    ts: float,
    noise_std: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    """O-A4 真积分 + 恒小值 OP 偏置 + 负载斜坡（独立递推生成器）.

    G(s)=K·e^(-θs)/s（ZOH：y[k]=y[k-1]+K·ts·u[k-d]）；OP 偏置与负载
    斜坡直接叠加进增量（负载斜坡 = 每 PV 单位/秒的恒速率）。
    """
    rng = np.random.default_rng(seed)
    d = max(0, round(theta / ts))
    n = len(u)
    y = np.zeros(n)
    for k in range(1, n):
        ui = (u[k - d] + op_bias) if k - d >= 0 else op_bias
        y[k] = y[k - 1] + K * ts * ui + load_ramp_pv_per_s * ts
    if noise_std > 0:
        y = y + rng.normal(0, noise_std, n)
    return y


def _snr_noise_std(y_clean: np.ndarray, snr_db: float) -> float:
    """白量测噪声 std = σ(信号)/10^(SNR/20)（SNR 定义在信号标准差上）."""
    return float(np.std(y_clean - np.mean(y_clean))) / (10.0 ** (snr_db / 20.0))


# ---------------------------------------------------------------------------
# 生成器独立解析核对（§0.2：合成器自身先对独立参考校验）
# ---------------------------------------------------------------------------


class TestOracleGeneratorChecks:
    def test_zoh_fopdt_step_response_matches_analytic(self):
        """精确 ZOH 生成器 vs 解析阶跃响应 K·(1-e^{-(t-θ)/τ}).

        ZOH 离散化保持阶跃响应采样值精确等于连续系统阶跃响应采样值——
        这是解析核对的理论依据。
        """
        K, tau, theta, ts = 1.2, 25.0, 8.0, 1.0
        n = 400
        u = np.zeros(n)
        u[50:] = 1.0  # 单位阶跃
        y = _zoh_fopdt(K, tau, theta, u, ts)
        # 解析参考（独立手算公式；阶跃时刻 k_step=50：t_eff = (k-k_step-d)·ts）
        k_step, d = 50, int(round(theta / ts))
        for k in (60, 80, 120, 200, 399):
            t_eff = (k - k_step - d) * ts
            y_ref = K * (1.0 - math.exp(-t_eff / tau)) if t_eff > 0 else 0.0
            assert abs(y[k] - y_ref) < 1e-9, (k, y[k], y_ref)

    def test_cascade_sopdt_step_response_close_to_analytic(self):
        """级联 SOPDT 生成器 vs 解析阶跃响应（ts≪T 时级联差分近似连续）.

        解析解：y(t) = K·[1 - (T1·e^{-t/T1} - T2·e^{-t/T2})/(T1-T2)]（t≥θ）。
        """
        K, T1, T2, theta, ts = 1.0, 40.0, 10.0, 6.0, 1.0
        n = 600
        u = np.zeros(n)
        u[50:] = 1.0
        y = _cascade_sopdt(K, T1, T2, theta, u, ts)
        k_step = 50
        d = max(0, round(theta / ts))
        for k in (80, 120, 200, 400):
            t = (k - k_step - d) * ts
            y_ref = K * (1.0 - (T1 * math.exp(-t / T1) - T2 * math.exp(-t / T2)) / (T1 - T2))
            # ts/T2=0.1 的级联差分近似误差 ~O(ts/T²)（早期段实测 ≤3%）
            assert abs(y[k] - y_ref) / K < 0.03, (k, y[k], y_ref)

    def test_ipdt_drift_slope_matches_analytic(self):
        """IPDT+漂移生成器 vs 解析斜率参考：恒 u 段斜率 = K·(ū+bias) + r."""
        K, theta, bias, ramp, ts = 0.05, 6.0, 2.0, 0.004, 1.0
        n = 1500
        u = np.full(n, 3.0)  # 恒输入段：解析斜率直接可算
        y = _ipdt_with_drift(K, theta, bias, ramp, u, ts)
        # 段 [100, 1400]：避开死时间与端点，线性拟合斜率
        seg = y[100:1400]
        t = np.arange(100, 1400, dtype=float)
        slope = float(np.polyfit(t, seg, 1)[0])
        slope_ref = K * (3.0 + bias) + ramp  # 解析参考（独立手算）
        assert abs(slope - slope_ref) < 0.02 * abs(slope_ref)


# ---------------------------------------------------------------------------
# O-A1 FOPDT/SOPDT 参数恢复（白输入开环）
# ---------------------------------------------------------------------------


class TestOA1FopdtRecovery:
    """O-A1 FOPDT 档：精确 ZOH + 白量测噪声两档，冻结容差恢复真值."""

    @staticmethod
    def _run_case(snr_db: float, seed_u: int, seed_n: int):
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=seed_u)
        y0 = _zoh_fopdt(K, tau, theta, u, ts)
        noise = _snr_noise_std(y0, snr_db)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=noise, seed=seed_n)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        p = result.best_model.params
        assert p.model_type == ModelType.FOPDT
        return p, (K, tau, theta, ts)

    def test_fopdt_20db(self):
        p, (K, tau, theta, ts) = self._run_case(20.0, seed_u=401, seed_n=402)
        assert abs(p.K - K) / K <= OA1_K_TOL[20.0]
        assert abs(p.tau - tau) / tau <= OA1_TAU_TOL[20.0]
        assert abs(p.theta - theta) <= _theta_tol(ts, theta)

    def test_fopdt_10db(self):
        p, (K, tau, theta, ts) = self._run_case(10.0, seed_u=411, seed_n=412)
        assert abs(p.K - K) / K <= OA1_K_TOL[10.0]
        assert abs(p.tau - tau) / tau <= OA1_TAU_TOL[10.0]
        assert abs(p.theta - theta) <= _theta_tol(ts, theta)

    def test_fopdt_second_seed_robustness(self):
        """第二组种子（随机性稳健；同档容差）."""
        p, (K, tau, theta, ts) = self._run_case(20.0, seed_u=431, seed_n=432)
        assert abs(p.K - K) / K <= OA1_K_TOL[20.0]
        assert abs(p.tau - tau) / tau <= OA1_TAU_TOL[20.0]
        assert abs(p.theta - theta) <= _theta_tol(ts, theta)


class TestOA1SopdtRecovery:
    """O-A1 SOPDT 档（冻结容差）——停点 P3-06a-S1 登记.

    xfail(strict)：当前冻结方法集（ARX/ARMAX，R9 不引入 SRIVC）下白量测
    噪声场景系统性不可恢复（ARX 渐近 EIV 偏差 + 慢极点共线放大；ARMAX
    nc=1 模型失配），噪声less 数据恢复 <0.1% 证明实现本身正确。详见模块
    docstring 停点登记；任何修复落地后本测试转 XPASS 将被 strict 模式
    拦截提示更新登记。
    """

    SOPDT_TRUTH = (K, T1, T2, theta) = (1.0, 40.0, 10.0, 6.0)  # type: ignore[misc]

    @staticmethod
    def _run_case(snr_db: float, seed_u: int, seed_n: int):
        K, T1, T2, theta = 1.0, 40.0, 10.0, 6.0
        ts, n = 1.0, 5000
        u = _white_u(n, seed=seed_u)
        y0 = _cascade_sopdt(K, T1, T2, theta, u, ts)
        noise = _snr_noise_std(y0, snr_db)
        y = _cascade_sopdt(K, T1, T2, theta, u, ts, noise_std=noise, seed=seed_n)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.SOPDT]
        )
        assert result.success, result.reason
        p = result.best_model.params
        assert p.model_type == ModelType.SOPDT
        # 冻结容差（T1/T2 顺序歧义取两种配对误差最小者）
        k_err = abs(p.K - K) / K
        opt1 = max(abs(p.T1 - T1) / T1, abs(p.T2 - T2) / T2)
        opt2 = max(abs(p.T1 - T2) / T2, abs(p.T2 - T1) / T1)
        return k_err, min(opt1, opt2), abs(p.theta - theta)

    @pytest.mark.xfail(strict=True, reason="停点 P3-06a-S1：冻结容差与冻结方法集冲突，报主协调裁决")
    def test_sopdt_20db(self):
        k_err, t_err, th_err = self._run_case(20.0, seed_u=601, seed_n=602)
        assert k_err <= OA1_K_TOL[20.0]
        assert t_err <= OA1_TAU_TOL[20.0]
        assert th_err <= _theta_tol(1.0, 6.0)

    @pytest.mark.xfail(strict=True, reason="停点 P3-06a-S1：冻结容差与冻结方法集冲突，报主协调裁决")
    def test_sopdt_10db(self):
        k_err, t_err, th_err = self._run_case(10.0, seed_u=611, seed_n=612)
        assert k_err <= OA1_K_TOL[10.0]
        assert t_err <= OA1_TAU_TOL[10.0]
        assert th_err <= _theta_tol(1.0, 6.0)

    def test_sopdt_noiseless_recovery_proves_implementation_correct(self):
        """噪声less 恢复精确（<1%）——证明模型类/实现正确，缺陷仅在噪声下估计方法.

        本测试不停点：它锚定"SOPDT 档失败原因 = 噪声下估计方法（归 R9
        裁决），非本卡实现错误"的停点论证。
        """
        K, T1, T2, theta = 1.0, 40.0, 10.0, 6.0
        ts, n = 1.0, 5000
        u = _white_u(n, seed=601)
        y = _cascade_sopdt(K, T1, T2, theta, u, ts)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.SOPDT]
        )
        assert result.success, result.reason
        p = result.best_model.params
        assert abs(p.K - K) / K <= 0.01
        t_err = min(
            max(abs(p.T1 - T1) / T1, abs(p.T2 - T2) / T2),
            max(abs(p.T1 - T2) / T2, abs(p.T2 - T1) / T1),
        )
        assert t_err <= 0.01
        assert abs(p.theta - theta) <= 1.0


# ---------------------------------------------------------------------------
# O-A2 有色 OP 粗增益不误降（R6）
# ---------------------------------------------------------------------------


class TestOA2ColoredOpNoFalseCap:
    """O-A2：闭环有色 OP 下准确保持输入模型不因无效粗估误降."""

    @staticmethod
    def _closed_loop_case():
        K, tau, theta = 2.0, 30.0, 5.0
        n = 1200
        sp = np.zeros(n)
        sp[50:] = 10.0
        sp[400:] = 15.0
        sp[800:] = 8.0
        y, u = _independent_pi_closed_loop(
            sp, K, tau, theta, kp=2.0, ti=20.0, ts=1.0, noise_std=0.5, seed=42
        )
        return u, y, sp, (K, tau, theta)

    def test_clivc_recovery_within_closed_loop_tolerance(self):
        """闭环 CLIVC 参数恢复 ≤15%（R16 闭环容差）."""
        u, y, sp, (K, tau, theta) = self._closed_loop_case()
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=theta,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success, result.reason
        iv = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_IV"]
        assert iv, "闭环 SP 激励下应产出 CLIVC 候选"
        best_iv = max(iv, key=lambda c: c.fitting_score)
        p = best_iv.params
        assert abs(p.K - K) / K <= OA2_CLOSED_LOOP_TOL
        assert abs(p.tau - tau) / tau <= OA2_CLOSED_LOOP_TOL
        assert abs(p.theta - theta) <= _theta_tol(1.0, theta)

    def test_accurate_model_not_capped_by_biased_rough_gain(self):
        """准确模型可信度不得因 NEGATIVE/MAGNITUDE 误判下调（R6 行为断言）.

        有色 OP（闭环 PI 输出）下相关分析 K 粗估有偏（实测偏 200+ 倍），
        校验降级为记录性证据：verdict 记录在 nonparamCheck，可信度不受
        该误判封顶（reason_codes 不含 SIGN/MAGNITUDE 封顶语义码）。
        """
        u, y, sp, (K, tau, theta) = self._closed_loop_case()
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            sp=sp.tolist(),
            ts=1.0,
            theta_estimate=theta,
            candidate_models=[ModelType.FOPDT],
        )
        assert result.success, result.reason
        iv = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_IV"]
        best_iv = max(iv, key=lambda c: c.fitting_score)
        assert best_iv.evidence is not None
        ev = best_iv.evidence
        # 结构化记录：粗估有偏 + 降级事实
        assert ev.nonparam_check is not None
        assert ev.nonparam_check["verdict"] in {"MAGNITUDE_MISMATCH", "SIGN_MISMATCH"}
        assert ev.nonparam_check["mode"] == "RECORD_ONLY"
        assert ev.nonparam_check["basis"] == "COLORED_OP_CLOSED_LOOP"
        # 封顶语义码不在（不再作为可信度信号）
        assert "MAGNITUDE_MISMATCH" not in ev.reason_codes
        assert "SIGN_MISMATCH" not in ev.reason_codes
        assert "NP_CHECK_RECORD_ONLY" in ev.reason_codes

    def test_open_loop_colored_op_keeps_cap_semantics(self):
        """对照：开环有色 OP（无 SP 激励=未检出闭环）维持现行封顶语义.

        R6 冻结方向"仅 OP 近白/外生时封顶"的保守实现边界：有色但无闭环
        证据（无 SP 激励）时保守维持封顶（mode=CAP），不因"无法证明外生"
        而全面弃用粗增益校验。
        """
        u = _white_u(2000, seed=701)
        u_ar1 = np.zeros_like(u)  # 有色（AR(1)）开环输入
        for k in range(1, len(u)):
            u_ar1[k] = 0.9 * u_ar1[k - 1] + u[k]
        K, tau, theta = 1.2, 25.0, 8.0
        y = _zoh_fopdt(K, tau, theta, u_ar1, 1.0, noise_std=0.05, seed=702)
        result = identify_from_history(
            op=u_ar1.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.nonparam_check is not None
        assert ev.nonparam_check["mode"] == "CAP"

    def test_u_near_white_helper(self):
        """近白判定：白噪声 → True；随机游走/AR(1) 光滑 → False."""
        rng = np.random.default_rng(1)
        assert _u_near_white(rng.normal(0, 1, 4000)) is True
        assert _u_near_white(np.cumsum(rng.normal(0, 1, 4000))) is False
        ar1 = np.zeros(4000)
        w = rng.normal(0, 1, 4000)
        for k in range(1, 4000):
            ar1[k] = 0.9 * ar1[k - 1] + w[k]
        assert _u_near_white(ar1) is False


# ---------------------------------------------------------------------------
# O-A3 ARMAX 创新多项式契约
# ---------------------------------------------------------------------------


class TestOA3ArmaxInnovationContract:
    """O-A3：已知 A/B/C 真值恢复 + 独立 C 反滤波一步预测误差白性 + 类型不混用."""

    @staticmethod
    def _case(c1: float, c2: float = 0.0):
        a1_t, b1_t, d, n = -0.95, 0.06, 5, 4000
        u = _white_u(n, seed=105, amp=5.0)
        y = _armax_form_signal(a1_t, b1_t, c1, d, u, seed=11, ar2_c2=c2)
        res = identify_armax(u - u.mean(), y - y.mean(), d, na=1, nb=1, nc=2 if c2 else 1)
        return res, (a1_t, b1_t, c1, c2)

    @staticmethod
    def _refilter_prediction_error(
        u: np.ndarray, y: np.ndarray, a: list[float], b: list[float], c: list[float], d: int
    ) -> np.ndarray:
        """独立手算：ε̂ = (A/C)·y − (B/C)·u（测试侧独立递推，非实现函数）.

        e(t) = A·y − B·u（方程误差）；ε(t) = e(t) − Σ c_j·ε(t−j)。
        """
        na, nb = len(a), len(b)
        max_lag = max(na, nb + d)
        rows = len(y) - max_lag
        e = np.zeros(rows)
        for i in range(rows):
            idx = max_lag + i
            v = y[idx]
            for j in range(na):
                v += a[j] * y[idx - 1 - j]
            for j in range(nb):
                v -= b[j] * u[idx - d - j]
            e[i] = v
        eps = np.zeros_like(e)
        for i in range(len(e)):
            v = e[i]
            for j, cj in enumerate(c):
                if i - 1 - j >= 0:
                    v -= cj * eps[i - 1 - j]
            eps[i] = v
        return eps

    def test_ar1_coefficients_recovered(self):
        """C=AR(1) 真值：A/B/C 系数相对误差 ≤10%（冻结值）."""
        res, (a1_t, b1_t, c1_t, _c2) = self._case(c1=0.7)
        assert abs(res.a_coeffs[0] - a1_t) / abs(a1_t) <= OA3_COEFF_TOL
        assert abs(res.b_coeffs[0] - b1_t) / abs(b1_t) <= OA3_COEFF_TOL
        assert abs(res.c_coeffs[0] - c1_t) / abs(c1_t) <= OA3_COEFF_TOL

    def test_ar2_coefficients_recovered(self):
        """C=AR(2) 真值：nc=2 同契约（P-04 候选 {1,2} 中的 2 档）."""
        res, (a1_t, b1_t, c1_t, c2_t) = self._case(c1=0.5, c2=0.2)
        assert abs(res.a_coeffs[0] - a1_t) / abs(a1_t) <= OA3_COEFF_TOL
        assert abs(res.b_coeffs[0] - b1_t) / abs(b1_t) <= OA3_COEFF_TOL
        assert abs(res.c_coeffs[0] - c1_t) / abs(c1_t) <= OA3_COEFF_TOL
        assert abs(res.c_coeffs[1] - c2_t) / abs(c2_t) <= OA3_COEFF_TOL

    def test_sign_and_type_not_mixed(self):
        """符号/多项式类型不混用（C19-ID1 行为断言）.

        B 与 C 各自恢复自身动态（B 未吸收 C、C 未吸收 B）——以两者系数
        符号与幅度同时恢复为断言（ARMAX 形合成下 C 真值为正时估计须为正）。
        """
        res, (a1_t, b1_t, c1_t, _c2) = self._case(c1=0.7)
        assert res.c_coeffs[0] > 0, "C 系数符号不得翻转（B/C 混用会翻号或幅度错位）"
        assert res.b_coeffs[0] > 0
        # 幅度互不侵占：两者相对误差均在 10% 内（系数被侵占时其一必超）
        assert abs(res.c_coeffs[0] - c1_t) / abs(c1_t) <= OA3_COEFF_TOL
        assert abs(res.b_coeffs[0] - b1_t) / abs(b1_t) <= OA3_COEFF_TOL

    def test_innovation_whiteness_via_independent_refilter(self):
        """独立 C 反滤波一步预测误差 → Ljung-Box p>0.05（轨 2 复核）."""
        res, _truth = self._case(c1=0.7)
        a1_t, b1_t, d, n = -0.95, 0.06, 5, 4000
        u = _white_u(n, seed=105, amp=5.0)
        y = _armax_form_signal(a1_t, b1_t, 0.7, d, u, seed=11)
        um, ym = u - u.mean(), y - y.mean()
        eps = self._refilter_prediction_error(
            um, ym, res.a_coeffs, res.b_coeffs, res.c_coeffs, res.d
        )
        _, lb_p = ljung_box_test(eps, max_lag=10, n_params=2 + 1)
        assert lb_p > 0.05, f"创新序列非白（LB p={lb_p:.4f}）"

    def test_pipeline_armax_candidate_lb_track(self):
        """管线级：ARMAX 候选走轨 2（LB 检验）且创新白性通过.

        真值取较快过程（a1=−0.75，τ≈3.5s）——避免 PV 谱主峰触发的低通
        预处理切进信号带宽（慢过程场景 fc 会落在过程频带内干扰本测试目的）。
        """
        a1_t, b1_t, c1_t, d, n = -0.75, 0.25, 0.5, 2, 3000
        u = _white_u(n, seed=115, amp=5.0)
        y = _armax_form_signal(a1_t, b1_t, c1_t, d, u, seed=16)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        armax = [c for c in result.candidates if c.identify_method.value == "HISTORICAL_ARMAX"]
        assert armax
        ev = armax[0].evidence
        assert ev is not None
        assert "LB_p=" in ev.residual_test_note
        assert armax[0].residual_test_passed is True


# ---------------------------------------------------------------------------
# O-A4 IPDT 积分+漂移（R7）
# ---------------------------------------------------------------------------


class TestOA4IpdtDrift:
    """O-A4：真积分 + OP 偏置 + 负载斜坡；漂移吸收并记录（P-07 方向 A）."""

    @staticmethod
    def _case():
        K, theta, op_bias, ramp = 0.05, 6.0, 2.0, 0.004
        ts, n = 1.0, 2500
        rng = np.random.default_rng(107)
        u = np.ones(n)
        idx = sorted(rng.choice(n, size=n // 10, replace=False))
        sign, prev = 1.0, 0
        for i in idx:
            u[prev:i] = sign * 5.0
            sign = -sign
            prev = i
        u[prev:] = sign * 5.0
        y = _ipdt_with_drift(K, theta, op_bias, ramp, u, ts, noise_std=0.01, seed=3)
        return u, y, (K, theta, op_bias, ramp, ts)

    def test_k_and_theta_recovered(self):
        """K 相对误差 ≤10%（冻结值）；θ 同 O-A1 容差."""
        u, y, (K, theta, _bias, _ramp, ts) = self._case()
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.IPDT]
        )
        assert result.success, result.reason
        p = result.best_model.params
        assert p.model_type == ModelType.IPDT
        assert abs(p.K - K) / K <= OA4_K_TOL
        assert abs(p.theta - theta) <= _theta_tol(ts, theta)

    def test_drift_absorbed_and_recorded(self):
        """漂移被显式吸收并记录：pvRatePerSecond ≈ K·ū原始 + r（解析组成）.

        差分回归截距吸收的恒值漂移速率 = 积分对象对原始 OP 均值偏置的
        响应速率（K·ū原始）+ 负载斜坡速率 r——两者在增量域不可分，回归
        吸收其总和并如实记录（R7 方向 A："吸收并记录"）。
        """
        u, y, (K, theta, bias, ramp, ts) = self._case()
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.IPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.ipdt_drift is not None
        drift = ev.ipdt_drift
        assert drift["absorbed"] is True
        rate_ref = K * (float(np.mean(u)) + bias) + ramp  # 解析参考（独立手算）
        rate_est = drift["pvRatePerSecond"]
        assert abs(rate_est - rate_ref) <= max(0.002, 0.3 * abs(rate_ref)), (
            f"漂移速率记录 {rate_est} vs 解析参考 {rate_ref}"
        )

    def test_drift_no_longer_poisons_residual_test(self):
        """漂移吸收后残差-输入独立性检验通过（R7 修复的缺陷锚定）.

        v1.4 行为（缺陷）：漂移残留在方程误差 → 互相关越界 → 检验失败 →
        可信度崩至 E。R7 后残差已扣除漂移项，检验恢复判别力。
        """
        u, y, _truth = self._case()
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.IPDT]
        )
        assert result.success, result.reason
        assert result.best_model.residual_test_passed is True
        level = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
        assert level.get(result.best_model.confidence.value, 0) >= 2


# ---------------------------------------------------------------------------
# O-A5 时滞物理边界（C14-ID2 正例，R1）
# ---------------------------------------------------------------------------


class TestOA5DelayPhysicalBoundary:
    """O-A5：真 θ=120s，ts∈{1,2,5,10}s 同物理数据；θ 恢复 ≤max(2ts,10%θ).

    行为断言：搜索以物理时间约束，不受隐含 60 样本边界限制——ts=1s 档
    θ=120s>60 样本，旧 min(n/4,60) 边界下不可恢复（实测恢复 54s 垃圾）。
    """

    @staticmethod
    def _case(ts: float):
        K, tau, theta = 1.0, 40.0, 120.0
        duration = 5000.0
        n = int(duration / ts)
        rng = np.random.default_rng(109)
        hold = max(3, int(20 / ts))
        u = np.ones(n)
        idx = sorted(rng.choice(n, size=max(2, n // hold), replace=False))
        sign, prev = 1.0, 0
        for i in idx:
            u[prev:i] = sign * 5.0
            sign = -sign
            prev = i
        u[prev:] = sign * 5.0
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.02, seed=205)
        return u, y, (K, tau, theta, ts)

    @pytest.mark.parametrize("ts", [1.0, 2.0, 5.0, 10.0])
    def test_theta_recovered_at_all_sampling_rates(self, ts: float):
        u, y, (K, tau, theta, ts) = self._case(ts)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        p = result.best_model.params
        tol = _theta_tol(ts, theta)
        assert abs(p.theta - theta) <= tol, f"ts={ts}: θ̂={p.theta} vs {theta}（容差 {tol}）"
        assert abs(p.K - K) / K <= 0.10

    def test_ts1_exceeds_legacy_60_sample_boundary(self):
        """ts=1s、θ=120s：恢复值 >60 样本边界（行为断言，C14-ID2）."""
        u, y, (K, tau, theta, ts) = self._case(1.0)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=ts, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        assert result.best_model.params.theta > 60.0

    def test_delay_bound_recorded(self):
        """R1 生效事实入 preprocessing.delaySearchBound（公式事实+审计）."""
        u, y, _truth = self._case(1.0)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        bound = (result.preprocessing or {}).get("delaySearchBound")
        assert bound is not None and bound["applied"] is True
        assert bound["mode"] == "R1_FORMULA"
        assert bound["roughTauPlusThetaSeconds"] is not None
        # 公式自洽：max(300, 5×rough) ≤ duration/4 = 1250
        expect = min(max(300.0, 5.0 * bound["roughTauPlusThetaSeconds"]), 1250.0)
        assert abs(bound["maxSecondsFormula"] - expect) < 0.01
        # B-01：候选数 ≤ 241
        assert bound["candidateCount"] <= 241


# ---------------------------------------------------------------------------
# O-A7 分数三段独立（C19-ID2，R4/R5）
# ---------------------------------------------------------------------------


class TestOA7FinalTestStates:
    """O-A7：train/selection-val/final-test 分数独立；终验四态不伪称."""

    @staticmethod
    def _time_varying_gain_case():
        """训练≈高拟合 + 终验段动态失败：真系统增益在最后 20% 段改变."""
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=801, amp=10.0)
        n_change = n - int(n * 0.2)  # 增益改变点 = final-test 段起点
        y1 = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.02, seed=802)
        y2 = _zoh_fopdt(K / 3.0, tau, theta, u, ts, noise_std=0.02, seed=803)
        y = np.concatenate([y1[:n_change], y2[n_change:]])
        return u, y, (K, tau, theta, ts)

    def test_final_test_failed_not_promoted(self):
        """终验失败：status=FAILED、reason code、可信度封顶 C 不晋升."""
        u, y, _truth = self._time_varying_gain_case()
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.final_test is not None
        assert ev.final_test["status"] == "FAILED"
        assert "FINAL_TEST_FAILED" in ev.reason_codes
        level = {
            AlgorithmConfidenceLevel.A: 5,
            AlgorithmConfidenceLevel.B: 4,
            AlgorithmConfidenceLevel.C: 3,
            AlgorithmConfidenceLevel.D: 2,
            AlgorithmConfidenceLevel.E: 1,
        }
        assert level.get(result.best_model.confidence, 0) <= 3, "终验失败不得晋升 A/B"

    def test_final_test_no_dynamics_not_claimed_passed(self):
        """近稳态终验：NO_DYNAMICS 不伪称通过（无动态=评估不可用）."""
        K, tau, theta, ts = 1.2, 25.0, 8.0, 1.0
        n = 3000
        u = _white_u(n, seed=811)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.02, seed=812)
        # 终验段（尾部 20%）替换为近稳态（ptp < 1.5% 归一化域）
        n_test = n - int(n * 0.8)
        y[-n_test:] = y[-n_test] + 0.1 * np.sin(np.arange(n_test) * 0.01)
        u[-n_test:] = u[-n_test] * 0.01  # 输入近稳态（保持物理一致性）
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.final_test is not None
        assert ev.final_test["status"] == "NO_DYNAMICS"
        assert "FINAL_TEST_NO_DYNAMICS" in ev.reason_codes

    def test_final_test_not_run_for_short_data(self):
        """短数据（70/30 退化）：NOT_RUN、final_test=None（不与 NO_DYNAMICS 混淆）."""
        K, tau, theta, ts = 1.2, 8.0, 2.0, 1.0
        n = 90  # 60/20/20 分割 n_val=18 < 20 → 退化为 70/30（无终验段）
        u = _white_u(n, seed=821)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=0.01, seed=822)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None
        assert ev.final_test is None
        assert ev.n_test == 0

    def test_three_scores_are_independent_fields(self):
        """分数字段互不覆盖：r2_train / r2_val / finalTest.r2 三处独立落值."""
        u, y, _truth = self._time_varying_gain_case()
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None
        d = ev.to_dict()
        # 三段分数键全部存在且互异（时变增益场景 train 高、final 低）
        assert d["r2Train"] >= 0.9
        assert d["finalTest"]["r2"] < d["r2Train"]
        assert d["split"]["test"] > 0

    def test_final_test_passed_on_consistent_system(self):
        """一致系统：PASSED 状态可达（O-A1 数据复用）."""
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=401)
        y0 = _zoh_fopdt(K, tau, theta, u, ts)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=_snr_noise_std(y0, 20.0), seed=402)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.final_test is not None
        assert ev.final_test["status"] == "PASSED"


# ---------------------------------------------------------------------------
# R1 边界命中与预算审计（B-01）
# ---------------------------------------------------------------------------


class TestR1BoundaryAndAudit:
    def test_boundary_hit_upper_recorded(self):
        """真 θ 超出显式收紧的搜索上界 → best_d 命中上边界并记录 UPPER."""
        K, tau, theta_true, ts = 1.0, 40.0, 40.0, 1.0
        n = 5000
        rng = np.random.default_rng(109)
        u = np.ones(n)
        idx = sorted(rng.choice(n, size=n // 20, replace=False))
        sign, prev = 1.0, 0
        for i in idx:
            u[prev:i] = sign * 5.0
            sign = -sign
            prev = i
        u[prev:] = sign * 5.0
        y = _zoh_fopdt(K, tau, theta_true, u, ts, noise_std=0.02, seed=205)
        # 显式收紧至 30s（B1 载体：调用方仅能收窄）→ 真 θ=120s 超界
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=ts,
            candidate_models=[ModelType.FOPDT],
            delay_search_max_seconds=30.0,
        )
        assert result.success, result.reason
        ev = result.best_model.evidence
        assert ev is not None and ev.delay_search_info is not None
        assert ev.delay_search_info["boundaryHit"] == "UPPER"
        bound = (result.preprocessing or {}).get("delaySearchBound")
        assert bound is not None and bound["mode"] == "R1_FORMULA_EXPLICIT_CAP"
        assert bound["maxSeconds"] == 30.0  # 显式 cap 收窄生效

    def test_explicit_cap_only_tightens(self):
        """显式 cap 仅能收窄：R1 公式给出更小上界时按公式（min 语义）."""
        # ts=1、n=1000 → 公式 ≤ 250s；显式给 600s 不放宽
        rng = np.random.default_rng(123)
        n = 1000
        u = rng.choice([-5.0, 5.0], size=n)
        a = math.exp(-1.0 / 20.0)
        y = np.zeros(n)
        for k in range(3, n):
            y[k] = a * y[k - 1] + 1.5 * (1 - a) * u[k - 3]
        result = identify_from_history(
            op=u.tolist(),
            pv=y.tolist(),
            ts=1.0,
            candidate_models=[ModelType.FOPDT],
            delay_search_max_seconds=600.0,
        )
        assert result.success, result.reason
        bound = (result.preprocessing or {}).get("delaySearchBound")
        assert bound is not None
        assert bound["maxSeconds"] == 250.0  # 公式 250 < 显式 600 → min 取公式
        assert bound["maxSecondsFormula"] == 250.0

    def test_candidate_budget_within_241(self):
        """B-01：任何 d_max 下候选总数 ≤ 241."""
        from app.services.tuning_identification.pipeline import _delay_candidate_grid

        for d_max in (60, 240, 300, 800, 2000):
            grid, sparse = _delay_candidate_grid(d_max)
            assert len(grid) <= 241
            assert grid[0] == 0 and grid[-1] == d_max
            if d_max + 1 > 241:
                assert sparse is True


# ---------------------------------------------------------------------------
# R17 证据 schema 承接（schemaVersion 1.3，只加不改）
# ---------------------------------------------------------------------------


class TestR17EvidenceSchema:
    def test_evidence_additive_fields_present(self):
        """evidence 加性字段（finalTest/nonparamCheck/estimatorProfile）落 to_dict."""
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=401)
        y0 = _zoh_fopdt(K, tau, theta, u, ts)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=_snr_noise_std(y0, 20.0), seed=402)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        d = result.to_dict()
        ev = d["evidence"]
        assert set(ev.keys()) >= {
            "finalTest",
            "nonparamCheck",
            "estimatorProfile",
            "delaySearchInfo",
        }
        assert ev["estimatorProfile"]["profileVersion"] == "TUNE_IDENT_PROFILE_v1"
        assert ev["estimatorProfile"]["structure"] == "FOPDT"
        assert ev["nonparamCheck"]["mode"] in {"CAP", "RECORD_ONLY"}
        assert result.algorithm_version == ALGORITHM_VERSION == "TUNE_IDENT_v1.6"

    def test_ipdt_drift_field_none_for_fopdt(self):
        """非 IPDT 结构 ipdtDrift=None（显式未记录，不伪补）."""
        K, tau, theta, ts, n = 1.2, 25.0, 8.0, 1.0, 3000
        u = _white_u(n, seed=401)
        y0 = _zoh_fopdt(K, tau, theta, u, ts)
        y = _zoh_fopdt(K, tau, theta, u, ts, noise_std=_snr_noise_std(y0, 20.0), seed=402)
        result = identify_from_history(
            op=u.tolist(), pv=y.tolist(), ts=1.0, candidate_models=[ModelType.FOPDT]
        )
        assert result.success, result.reason
        assert result.best_model.evidence is not None
        assert result.best_model.evidence.ipdt_drift is None
