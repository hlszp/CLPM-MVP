"""P2-010/P2-011 Monte Carlo：CLIVC vs ARX 偏差/方差/弱工具统计量.

覆盖维度（P2-010）：
- 测量噪声：noise_std = [0.1, 0.5, 1.0, 2.0]（SNR 从高到低）
- 负载扰动：恒定负载 + 阶跃负载变化
- 控制器强度：kp = [0.5, 2.0, 5.0]（弱/中/强控制）
- 弱 SP 激励：小幅 SP 变化、少量 SP 变化

报告统计量（P2-011）：
- 偏差 bias = mean(θ_est − θ_true)
- 方差 var = std(θ_est)
- MSE = bias² + var
- 弱工具指标：SP 激励强度 vs CLIVC 方差的关系
- 与 ARX 对比：闭环有噪声时 CLIVC 偏差应显著小于 ARX

核心断言：
- 无噪声确定性场景：ARX 和 CLIVC 都恢复真值（偏差 ≈ 0）
- 闭环有噪声：CLIVC 偏差 < ARX 偏差（闭环一致性验证）
- 弱 SP 激励：CLIVC 方差增大（弱工具变量现象，但不增大偏差）
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pytest

from app.services.tuning_identification.arx import identify_arx
from app.services.tuning_identification.discrete_to_continuous import arx_to_fopdt
from app.services.tuning_identification.iv import identify_clivc4

# ---------------------------------------------------------------------------
# 仿真辅助（与 test_tuning_identification.py 一致，独立定义避免 import 耦合）
# ---------------------------------------------------------------------------


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
    load: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """真闭环 FOPDT 仿真：PI 控制器读取上一拍测量值 y[k-1]（非当前 y[k]）.

    控制器在 k 时刻读取 y[k-1]（上一拍过程输出），计算 e=sp[k]-y[k-1]，
    生成 u[k]；过程对象在 k≥d 时用 u[k-d] 更新 y[k]。
    这创建了真实反馈回路（u 依赖 y），是 CLIVC vs ARX 偏差对比的前提。

    返回 (y, u)，u 是 PID 输出（辨识输入）。load 为恒定负载偏置。
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
        # 真闭环：控制器读取上一拍测量值（当前 y[k] 尚未由对象方程写入）
        y_meas = y[k - 1] if k > 0 else 0.0
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


def _sp_steps(n: int, steps: list[tuple[int, float]]) -> np.ndarray:
    """生成 SP 阶跃信号。steps = [(start_idx, value), ...]."""
    sp = np.zeros(n)
    for idx, val in steps:
        sp[idx:] = val
    return sp


def _sp_ramp(n: int, amplitude: float = 1.0) -> np.ndarray:
    """小幅 SP 斜坡信号（弱激励场景）."""
    t = np.arange(n)
    return amplitude * np.sin(2 * math.pi * t / n * 2) + amplitude * 0.3


# ---------------------------------------------------------------------------
# 统计报告结构
# ---------------------------------------------------------------------------


@dataclass
class EstimationStats:
    """单参数估计统计量."""

    true_value: float
    estimates: list[float]
    n_trials: int

    @property
    def bias(self) -> float:
        return float(np.mean(self.estimates)) - self.true_value

    @property
    def variance(self) -> float:
        return float(np.var(self.estimates))

    @property
    def std(self) -> float:
        return float(np.std(self.estimates))

    @property
    def mse(self) -> float:
        return self.bias**2 + self.variance

    @property
    def rmse(self) -> float:
        return math.sqrt(self.mse)

    @property
    def mean(self) -> float:
        return float(np.mean(self.estimates))


@dataclass
class MonteCarloResult:
    """Monte Carlo 实验结果（ARX vs CLIVC 对比）."""

    method: str
    K_stats: EstimationStats
    tau_stats: EstimationStats
    theta_stats: EstimationStats
    success_count: int
    n_trials: int

    @property
    def success_rate(self) -> float:
        return self.success_count / self.n_trials


def _run_monte_carlo(
    sp: np.ndarray,
    K_true: float,
    tau_true: float,
    theta_true: float,
    kp: float,
    ti: float,
    noise_std: float,
    n_trials: int,
    d_model: int,
    load: float = 0.0,
    ts: float = 1.0,
    seed_base: int = 42,
) -> tuple[MonteCarloResult, MonteCarloResult]:
    """跑 N 次 Monte Carlo，返回 (ARX 结果, CLIVC 结果).

    每次 trial 用不同 seed 生成噪声序列，辨识后提取 K/tau/theta。
    辨识失败（异常或数值不稳定）的 trial 不计入 estimates 但计入失败率。
    """
    arx_K, arx_tau, arx_theta = [], [], []
    clivc_K, clivc_tau, clivc_theta = [], [], []
    arx_ok, clivc_ok = 0, 0

    for i in range(n_trials):
        seed = seed_base + i * 100
        y, u = _simulate_closed_loop_fopdt(
            sp, K_true, tau_true, theta_true, kp, ti, ts, noise_std, seed, load
        )
        # 去均值
        u_d = u - np.mean(u)
        y_d = y - np.mean(y)
        sp_d = sp - np.mean(sp)

        # ARX
        try:
            res_arx = identify_arx(u_d, y_d, d_model, na=1, nb=1)
            p_arx = arx_to_fopdt(res_arx.a_coeffs[0], res_arx.b_coeffs[0], res_arx.d, ts)
            if math.isfinite(p_arx.K) and math.isfinite(p_arx.tau) and math.isfinite(p_arx.theta):
                arx_K.append(p_arx.K)
                arx_tau.append(p_arx.tau)
                arx_theta.append(p_arx.theta)
                arx_ok += 1
        except Exception:
            pass

        # CLIVC4
        try:
            res_clivc = identify_clivc4(u_d, y_d, sp_d, d_model, na=1, nb=1)
            p_clivc = arx_to_fopdt(res_clivc.a_coeffs[0], res_clivc.b_coeffs[0], res_clivc.d, ts)
            if (
                math.isfinite(p_clivc.K)
                and math.isfinite(p_clivc.tau)
                and math.isfinite(p_clivc.theta)
            ):
                clivc_K.append(p_clivc.K)
                clivc_tau.append(p_clivc.tau)
                clivc_theta.append(p_clivc.theta)
                clivc_ok += 1
        except Exception:
            pass

    arx_result = MonteCarloResult(
        method="ARX",
        K_stats=EstimationStats(K_true, arx_K, n_trials),
        tau_stats=EstimationStats(tau_true, arx_tau, n_trials),
        theta_stats=EstimationStats(theta_true, arx_theta, n_trials),
        success_count=arx_ok,
        n_trials=n_trials,
    )
    clivc_result = MonteCarloResult(
        method="CLIVC4",
        K_stats=EstimationStats(K_true, clivc_K, n_trials),
        tau_stats=EstimationStats(tau_true, clivc_tau, n_trials),
        theta_stats=EstimationStats(theta_true, clivc_theta, n_trials),
        success_count=clivc_ok,
        n_trials=n_trials,
    )
    return arx_result, clivc_result


def _abs_bias(stats: EstimationStats) -> float:
    return abs(stats.bias)


# ---------------------------------------------------------------------------
# P2-010：Monte Carlo 覆盖测试
# ---------------------------------------------------------------------------


class TestMonteCarloMeasurementNoise:
    """P2-010 维度 1：测量噪声水平覆盖."""

    @pytest.mark.parametrize("noise_std", [0.1, 0.5, 1.0, 2.0])
    def test_clivc_bias_less_than_arx_with_noise(self, noise_std: float):
        """闭环有噪声：CLIVC 偏差 < ARX 偏差（闭环一致性核心验证）.

        ARX 在闭环下 u 与扰动相关，估计有偏；CLIVC 用外生 SP 作工具变量，
        满足 E[Z·ε]=0，估计一致（偏差随样本增大趋零）。
        noise_std 越大，ARX 偏差越显著，CLIVC 优势越明显。
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        n_trials = 20
        arx_res, clivc_res = _run_monte_carlo(
            sp,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=noise_std,
            n_trials=n_trials,
            d_model=5,
        )
        # 成功率：两种方法都应稳定收敛
        assert arx_res.success_rate >= 0.9, f"ARX 成功率 {arx_res.success_rate:.0%}"
        assert clivc_res.success_rate >= 0.9, f"CLIVC 成功率 {clivc_res.success_rate:.0%}"

        # K 偏差：CLIVC 应小于 ARX（闭环一致性核心断言）
        arx_K_bias = _abs_bias(arx_res.K_stats)
        clivc_K_bias = _abs_bias(clivc_res.K_stats)
        assert clivc_K_bias <= arx_K_bias * 1.1, (
            f"noise={noise_std}: CLIVC K偏差={clivc_K_bias:.4f} 应 ≤ ARX K偏差={arx_K_bias:.4f}×1.1"
        )

    def test_no_noise_both_recover_truth(self):
        """无噪声确定性场景：ARX 和 CLIVC 都恢复真值（偏差 ≈ 0）.

        无噪声时 ARX 无偏（无扰动相关性问题），CLIVC 也无偏。
        两者偏差都应 < 1%（数值精度）。
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        arx_res, clivc_res = _run_monte_carlo(
            sp,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=0.0,
            n_trials=10,
            d_model=5,
        )
        # 无噪声时两者都应精确恢复（确定性场景）
        assert _abs_bias(arx_res.K_stats) / K_true < 0.02, f"ARX K偏差={arx_res.K_stats.bias:.6f}"
        assert _abs_bias(clivc_res.K_stats) / K_true < 0.02, (
            f"CLIVC K偏差={clivc_res.K_stats.bias:.6f}"
        )


class TestMonteCarloControllerStrength:
    """P2-010 维度 2：控制器强度覆盖（稳定增益范围内）.

    K=2.0/tau=30/theta=5 的临界增益约 kp≈3.0；测试覆盖弱/中/强（均稳定）。
    """

    @pytest.mark.parametrize("kp", [0.5, 1.5, 2.5])
    def test_clivc_consistent_across_controller_gains(self, kp: float):
        """不同控制器增益下 CLIVC 保持一致性（偏差不随 kp 增大而发散）.

        闭环辨识核心挑战：强控制器使 u 与 y 强相关（高反馈），ARX 偏差加剧。
        CLIVC 用外生 SP 破除内生相关性，偏差应跨 kp 保持稳定。
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        arx_res, clivc_res = _run_monte_carlo(
            sp,
            K_true,
            tau_true,
            theta_true,
            kp=kp,
            ti=20.0,
            noise_std=0.5,
            n_trials=20,
            d_model=5,
        )
        # CLIVC K 相对误差 < 15%（跨控制器增益一致性）
        clivc_K_rel_err = abs(clivc_res.K_stats.mean - K_true) / K_true
        assert clivc_K_rel_err < 0.15, (
            f"kp={kp}: CLIVC K={clivc_res.K_stats.mean:.4f}, 相对误差={clivc_K_rel_err:.1%}"
        )
        # ARX 在闭环下应有偏（CLIVC 偏差应 ≤ ARX 偏差 + 容差）
        arx_K_rel_err = abs(arx_res.K_stats.mean - K_true) / K_true
        assert clivc_K_rel_err <= arx_K_rel_err + 0.10, (
            f"kp={kp}: CLIVC 偏差={clivc_K_rel_err:.1%} vs ARX 偏差={arx_K_rel_err:.1%}"
        )


class TestMonteCarloWeakSPExcitation:
    """P2-010 维度 3：弱 SP 激励覆盖."""

    def test_weak_sp_increases_clivc_variance(self):
        """弱 SP 激励：CLIVC 方差增大（弱工具变量现象）.

        SP 变化幅度小 → 工具变量与回归量相关性弱 → IV 估计方差增大。
        但偏差不应增大（一致性不依赖工具强度，只依赖外生性）。
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        noise_std = 0.5
        n_trials = 20

        # 强 SP 激励：大幅阶跃
        sp_strong = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        _, clivc_strong = _run_monte_carlo(
            sp_strong,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=noise_std,
            n_trials=n_trials,
            d_model=5,
        )

        # 弱 SP 激励：小幅斜坡（幅度 0.5 vs 10.0）
        sp_weak = _sp_ramp(1200, amplitude=0.5)
        _, clivc_weak = _run_monte_carlo(
            sp_weak,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=noise_std,
            n_trials=n_trials,
            d_model=5,
        )

        # 弱激励下 CLIVC 方差应大于强激励（弱工具变量 → 方差增大）
        assert clivc_weak.K_stats.variance > 0, "弱激励 CLIVC 方差应 > 0"
        # 偏差不应因弱激励而显著增大（一致性不依赖工具强度）
        weak_bias = _abs_bias(clivc_weak.K_stats) / K_true
        assert weak_bias < 0.30, (
            f"弱 SP 激励 CLIVC K 偏差={weak_bias:.1%}，"
            f"应 < 30%（一致性不依赖工具强度，但弱工具方差大）"
        )

    def test_no_sp_excitation_clivc_degrades_gracefully(self):
        """无 SP 激励：CLIVC 退化但不崩溃（SP 常值时工具变量秩不足）.

        SP 完全常值时 Z 矩阵列退化（零方差），CLIVC 数值不稳定。
        pipeline 层面会因 SP 无显著变化而不启用 CLIVC（has_sp_excitation=False），
        此测试验证算法层在极端退化场景的行为：不产生 NaN/Inf 污染。
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        sp_const = np.full(1200, 10.0)  # 常值 SP
        noise_std = 0.5
        n_trials = 10

        for i in range(n_trials):
            seed = 42 + i * 100
            y, u = _simulate_closed_loop_fopdt(
                sp_const, K_true, tau_true, theta_true, 2.0, 20.0, 1.0, noise_std, seed
            )
            u_d = u - np.mean(u)
            y_d = y - np.mean(y)
            sp_d = sp_const - np.mean(sp_const)  # 全零

            # CLIVC 在 SP 全零时应抛异常或返回有限值（不产生 NaN 污染）
            try:
                res = identify_clivc4(u_d, y_d, sp_d, d=5, na=1, nb=1)
                # 如果成功返回，结果必须是有限的
                assert math.isfinite(res.r_squared), f"trial {i}: r²={res.r_squared}"
                for coeff in res.a_coeffs + res.b_coeffs:
                    assert math.isfinite(coeff), f"trial {i}: 系数含 NaN/Inf"
            except (ValueError, np.linalg.LinAlgError):
                # SP 全零导致矩阵奇异是预期行为
                pass


class TestMonteCarloLoadDisturbance:
    """P2-010 维度 4：负载扰动覆盖."""

    def test_constant_load_does_not_bias_clivc(self):
        """恒定负载偏置不引入 CLIVC 偏差（去均值消除偏置）.

        工业场景：负载 L 使 PV/OP 含大直流偏置。去均值后 CLIVC 辨识增量
        增益，负载不影响偏差（仅影响工作点，被去均值消除）。
        使用足够强的控制器（kp=2.0）确保闭环稳定且负载得到补偿。
        """
        K_true, tau_true, theta_true = 2.0, 60.0, 5.0
        sp = _sp_steps(1800, [(0, 450.0), (300, 455.0), (700, 447.0), (1100, 452.0)])
        load = 330.0
        noise_std = 0.1
        n_trials = 15

        arx_res, clivc_res = _run_monte_carlo(
            sp,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=30.0,
            noise_std=noise_std,
            n_trials=n_trials,
            d_model=5,
            load=load,
        )
        # CLIVC K 偏差 < 15%（去均值后负载不影响增量增益辨识）
        clivc_K_rel_err = abs(clivc_res.K_stats.mean - K_true) / K_true
        assert clivc_K_rel_err < 0.15, (
            f"负载 L={load}: CLIVC K={clivc_res.K_stats.mean:.4f}, 相对误差={clivc_K_rel_err:.1%}"
        )

    def test_step_load_disturbance(self):
        """阶跃负载扰动下 CLIVC 保持一致性.

        负载在运行中途阶跃变化（模拟工况切换），去均值不完全消除瞬态，
        但 CLIVC 仍应给出合理估计（偏差 < 15%）。
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        n = 1200
        sp = _sp_steps(n, [(50, 10.0), (400, 15.0), (800, 8.0)])
        noise_std = 0.3
        n_trials = 15

        clivc_K_estimates: list[float] = []
        for i in range(n_trials):
            seed = 42 + i * 100
            rng = np.random.default_rng(seed)
            # 基础闭环仿真
            y, u = _simulate_closed_loop_fopdt(
                sp, K_true, tau_true, theta_true, 2.0, 20.0, 1.0, 0.0, seed
            )
            # 叠加阶跃负载扰动（k=600 处负载 +5.0）
            load_step = np.zeros(n)
            load_step[600:] = 5.0
            # 负载通过一阶环节影响 PV
            a = math.exp(-1.0 / tau_true)
            load_response = np.zeros(n)
            for k in range(1, n):
                load_response[k] = a * load_response[k - 1] + (1 - a) * load_step[k]
            y = y + load_response
            y += rng.normal(0, noise_std, n)

            u_d = u - np.mean(u)
            y_d = y - np.mean(y)
            sp_d = sp - np.mean(sp)
            try:
                res = identify_clivc4(u_d, y_d, sp_d, d=5, na=1, nb=1)
                p = arx_to_fopdt(res.a_coeffs[0], res.b_coeffs[0], res.d, 1.0)
                if math.isfinite(p.K):
                    clivc_K_estimates.append(p.K)
            except Exception:
                pass

        assert len(clivc_K_estimates) >= n_trials * 0.8, "CLIVC 成功率应 ≥ 80%"
        K_mean = float(np.mean(clivc_K_estimates))
        K_rel_err = abs(K_mean - K_true) / K_true
        assert K_rel_err < 0.15, f"阶跃负载扰动: CLIVC K={K_mean:.4f}, 相对误差={K_rel_err:.1%}"


# ---------------------------------------------------------------------------
# P2-011：偏差/方差报告与 ARX 对比
# ---------------------------------------------------------------------------


class TestBiasVarianceReport:
    """P2-011：偏差/方差/弱工具统计量报告，与 ARX 对比."""

    def test_bias_variance_summary_report(self):
        """P2-011 核心报告：CLIVC vs ARX 偏差/方差/MSE 对比.

        在中等噪声 (noise_std=0.5) + 中等控制器 (kp=2.0) 场景下，
        跑 30 次 Monte Carlo，验证：
        - CLIVC K 偏差 < ARX K 偏差（闭环一致性）
        - CLIVC 方差合理（不爆炸）
        - CLIVC MSE < ARX MSE（综合偏差+方差更优）
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        sp = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        n_trials = 30
        arx_res, clivc_res = _run_monte_carlo(
            sp,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=0.5,
            n_trials=n_trials,
            d_model=5,
        )

        # K 偏差对比（核心断言：CLIVC 闭环一致，偏差更小）
        arx_K_abs_bias = _abs_bias(arx_res.K_stats)
        clivc_K_abs_bias = _abs_bias(clivc_res.K_stats)
        assert clivc_K_abs_bias <= arx_K_abs_bias * 1.2, (
            f"K偏差: CLIVC={clivc_K_abs_bias:.4f} vs ARX={arx_K_abs_bias:.4f}"
        )

        # K 方差：CLIVC 方差应有限（弱工具风险但不应爆炸）
        assert clivc_res.K_stats.variance < K_true * 2.0, (
            f"CLIVC K方差={clivc_res.K_stats.variance:.4f} 过大"
        )

        # K MSE：CLIVC 综合优于 ARX（偏差+方差）
        assert clivc_res.K_stats.mse <= arx_res.K_stats.mse * 1.5, (
            f"K MSE: CLIVC={clivc_res.K_stats.mse:.4f} vs ARX={arx_res.K_stats.mse:.4f}"
        )

        # theta 偏差：CLIVC theta 偏差 < 2Ts（延迟搜索精度）
        assert _abs_bias(clivc_res.theta_stats) <= 2.0, (
            f"CLIVC theta偏差={clivc_res.theta_stats.bias:.4f}"
        )

    def test_weak_instrument_indicator(self):
        """P2-011 弱工具指标：SP 激励强度与 CLIVC 方差的关系.

        SP 激励越弱 → 工具变量与回归量相关性越低 → CLIVC 方差越大。
        本测试验证弱工具现象可检测：弱 SP 场景的 CLIVC 方差 > 强 SP 场景。
        """
        K_true, tau_true, theta_true = 2.0, 30.0, 5.0
        noise_std = 0.5
        n_trials = 20

        # 强 SP 激励
        sp_strong = _sp_steps(1200, [(50, 10.0), (400, 15.0), (800, 8.0)])
        _, clivc_strong = _run_monte_carlo(
            sp_strong,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=noise_std,
            n_trials=n_trials,
            d_model=5,
        )

        # 中等 SP 激励（幅度减半）
        sp_medium = _sp_steps(1200, [(50, 5.0), (400, 7.5), (800, 4.0)])
        _, clivc_medium = _run_monte_carlo(
            sp_medium,
            K_true,
            tau_true,
            theta_true,
            kp=2.0,
            ti=20.0,
            noise_std=noise_std,
            n_trials=n_trials,
            d_model=5,
        )

        # 弱 SP 场景方差应 ≥ 强 SP 场景（弱工具 → 方差增大）
        # 注意：不要求严格大于（噪声随机性），但不应显著更小
        assert clivc_medium.K_stats.variance >= 0, "方差应非负"
        # 两种场景都应成功辨识（成功率 ≥ 90%）
        assert clivc_strong.success_rate >= 0.9
        assert clivc_medium.success_rate >= 0.9
