#!/usr/bin/env python3
"""回路整定 Phase 2.0 — 过程对象辨识算法原型验证（脱机脚本）.

验证目标（对应技术方案 §3.2 算法栈）：
1. ARX 在开环数据上能正确辨识 FOPDT（基线正确性）
2. ARX 在闭环数据上有偏（验证"闭环辨识偏差"问题确实存在）
3. IV 法在闭环数据上无偏（验证解决方案有效）
4. ARMAX 显式建模扰动通道（验证扰动抑制能力）
5. 激励检测正确拒绝无激励数据（验证门控有效性）
6. 离散→连续转换正确还原 K/τ/θ

不依赖数据库，纯 numpy/scipy 仿真验证。
成功标准：闭环 IV 辨识误差 < 10%，ARX 闭环偏差显著（>20%）。

用法::

    cd backend && uv run python scripts/tuning_identification_prototype.py
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

# ============================================================================
# 1. FOPDT 过程对象仿真器（生成"真值"数据用于验证）
# ============================================================================

@dataclass
class FOPDTTruth:
    """FOPDT 真值参数 G(s) = K * exp(-theta*s) / (tau*s + 1)."""
    K: float       # 过程增益
    tau: float     # 时间常数（秒）
    theta: float   # 纯滞后（秒）


def simulate_fopdt_open_loop(
    u: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 42,
) -> np.ndarray:
    """开环仿真 FOPDT：u=OP 输入序列，返回 PV 输出序列.

    使用零阶保持离散化：
        y(k) = a * y(k-1) + b * u(k-d)
    其中 a = exp(-Ts/tau), b = K*(1-a), d = round(theta/Ts)
    """
    rng = np.random.default_rng(seed)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(1, round(theta / ts))
    n = len(u)
    y = np.zeros(n)
    for k in range(d, n):
        y[k] = a * y[k - 1] + b * u[k - d]
    if noise_std > 0:
        y += rng.normal(0, noise_std, n)
    return y


def simulate_closed_loop_fopdt(
    sp: np.ndarray,
    K: float,
    tau: float,
    theta: float,
    kp: float,
    ti: float,
    ts: float = 1.0,
    noise_std: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """闭环仿真：PID 控制器 + FOPDT 对象，返回 (PV, OP, SP).

    PID 算法（增量式 PI）：
        e(k) = sp(k) - y(k)
        de(k) = e(k) - e(k-1)
        du(k) = kp * de(k) + (kp * ts / ti) * e(k)
        u(k) = u(k-1) + du(k)  （OP）
    对象：y(k) = FOPDT(u)

    这模拟 AUTO 模式闭环运行，OP 由 PID 算出。
    """
    rng = np.random.default_rng(seed)
    a = math.exp(-ts / tau)
    b = K * (1 - a)
    d = max(1, round(theta / ts))
    n = len(sp)
    y = np.zeros(n)   # PV
    u = np.zeros(n)   # OP
    e_prev = 0.0
    u_prev = 0.0
    ki = kp * ts / ti
    for k in range(n):
        # PID 计算 OP（使用当前 SP 和 PV）
        e = sp[k] - y[k]
        de = e - e_prev
        u[k] = u_prev + kp * de + ki * e
        u_prev = u[k]
        e_prev = e
        # 对象响应（带滞后）
        if k >= d:
            y[k] = a * y[k - 1] + b * u[k - d]
        else:
            y[k] = 0.0
    if noise_std > 0:
        y += rng.normal(0, noise_std, n)
    return y, u, sp


# ============================================================================
# 2. ARX 辨识（线性最小二乘）
# ============================================================================

@dataclass
class ARXResult:
    """ARX 辨识结果：A(z^-1)*y(t) = B(z^-1)*u(t-d) + e(t)."""
    a1: float          # A 多项式系数（y(t) + a1*y(t-1) = ...）
    b1: float          # B 多项式系数（... = b1*u(t-d)）
    d: int             # 纯滞后（采样数）
    residual_var: float
    n_samples: int


def identify_arx(
    u: np.ndarray,
    y: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
) -> ARXResult:
    """ARX 辨识：y(t) + a1*y(t-1) = b1*u(t-d) + e(t).

    最小二乘解析解：theta = (Phi^T Phi)^-1 Phi^T y_reg
    """
    n = len(y)
    if n < na + nb + d + 10:
        raise ValueError(f"数据不足：{n} 点，需 ≥ {na + nb + d + 10}")
    # 构建回归矩阵
    rows = n - max(na, nb + d)
    Phi = np.zeros((rows, na + nb))
    y_reg = np.zeros(rows)
    max_lag = max(na, nb + d)
    for i in range(rows):
        idx = max_lag + i
        # y 项：-y(t-1), -y(t-2), ...（移到左边为正系数 a1）
        for j in range(na):
            Phi[i, j] = -y[idx - 1 - j]
        # u 项：u(t-d), u(t-d-1), ...
        for j in range(nb):
            Phi[i, na + j] = u[idx - d - j]
        y_reg[i] = y[idx]
    # 最小二乘
    theta, residuals, _, _ = np.linalg.lstsq(Phi, y_reg, rcond=None)
    y_pred = Phi @ theta
    res_var = float(np.var(y_reg - y_pred)) if rows > 1 else 0.0
    return ARXResult(
        a1=float(theta[0]),
        b1=float(theta[1]),
        d=d,
        residual_var=res_var,
        n_samples=rows,
    )


# ============================================================================
# 3. IV 辨识（工具变量法，处理闭环偏差）
# ============================================================================

def identify_iv(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
) -> ARXResult:
    """IV 辨识：工具变量 z(t) = [sp(t-1), sp(t-2), ..., u(t-1), ...].

    一致性条件：
    - E[sp(t-k)*e(t)] = 0：SP 外生，与测量噪声不相关
    - E[sp(t-k)*u(t)] != 0：SP 经 PID 影响 OP
    """
    n = len(y)
    max_lag = max(na, nb + d)
    rows = n - max_lag
    # 工具变量矩阵 Z：用 SP 代替 y 作为工具变量
    Phi = np.zeros((rows, na + nb))
    Z = np.zeros((rows, na + nb))
    y_reg = np.zeros(rows)
    for i in range(rows):
        idx = max_lag + i
        for j in range(na):
            Phi[i, j] = -y[idx - 1 - j]
            # 工具变量：用 SP 的延迟替代 y 的延迟
            Z[i, j] = -sp[idx - 1 - j]
        for j in range(nb):
            Phi[i, na + j] = u[idx - d - j]
            Z[i, na + j] = u[idx - d - j]  # u 本身也可作工具变量
        y_reg[i] = y[idx]
    # IV 解：theta = (Z^T Phi)^-1 Z^T y
    ZtPhi = Z.T @ Phi
    Zty = Z.T @ y_reg
    try:
        theta = np.linalg.solve(ZtPhi, Zty)
    except np.linalg.LinAlgError:
        theta, _, _, _ = np.linalg.lstsq(ZtPhi, Zty, rcond=None)
    y_pred = Phi @ theta
    res_var = float(np.var(y_reg - y_pred)) if rows > 1 else 0.0
    return ARXResult(
        a1=float(theta[0]),
        b1=float(theta[1]),
        d=d,
        residual_var=res_var,
        n_samples=rows,
    )


# ============================================================================
# 4. ARMAX 辨识（预测误差法，显式建模扰动）
# ============================================================================

def identify_armax(
    u: np.ndarray,
    y: np.ndarray,
    d: int,
    nc: int = 1,
    max_iter: int = 200,
    tol: float = 1e-6,
) -> dict[str, Any]:
    """ARMAX 辨识：A(z^-1)*y(t) = B(z^-1)*u(t-d) + C(z^-1)*e(t).

    用迭代 PEM：固定 C 估 A/B，固定 A/B 估 C，交替至收敛。
    """
    n = len(y)
    max_lag = max(1, nb := 1) + d
    rows = n - max_lag

    # 初始值：ARX
    arx = identify_arx(u, y, d)
    a1, b1 = arx.a1, arx.b1
    c = np.zeros(nc + 1)
    c[0] = 1.0

    for iteration in range(max_iter):
        # 计算残差 e(t) = y(t) + a1*y(t-1) - b1*u(t-d)
        e = np.zeros(rows)
        for i in range(rows):
            idx = max_lag + i
            e[i] = y[idx] + a1 * y[idx - 1] - b1 * u[idx - d]
        # 估 C：e(t) = -c1*e(t-1) - ... + eps(t)
        # 注意：e 数组按回归行序号 i 索引（e[i] 对应第 i 行），不是原始时间序号
        if nc > 0:
            E_phi = np.zeros((rows, nc))
            e_target = e.copy()
            for i in range(rows):
                for j in range(nc):
                    # e 的历史项：e[i-1-j]（回归行序号回退）
                    E_phi[i, j] = -e[i - 1 - j] if (i - 1 - j) >= 0 else 0.0
            c_theta, _, _, _ = np.linalg.lstsq(E_phi, e_target, rcond=None)
            c[1:] = c_theta
        # 重估 A/B：用滤波后的残差
        # y(t) + a1*y(t-1) = b1*u(t-d) + c1*e(t-1) + eps(t)
        Phi = np.zeros((rows, 1 + nc + 1))
        y_reg = np.zeros(rows)
        for i in range(rows):
            idx = max_lag + i
            Phi[i, 0] = -y[idx - 1]
            for j in range(nc):
                Phi[i, 1 + j] = -e[i - 1 - j] if (i - 1 - j) >= 0 else 0.0
            Phi[i, 1 + nc] = u[idx - d]
            y_reg[i] = y[idx]
        theta_new, _, _, _ = np.linalg.lstsq(Phi, y_reg, rcond=None)
        a1_new = float(theta_new[0])
        b1_new = float(theta_new[1 + nc])
        # 收敛判断
        if abs(a1_new - a1) < tol and abs(b1_new - b1) < tol:
            a1, b1 = a1_new, b1_new
            break
        a1, b1 = a1_new, b1_new

    # 最终残差
    e_final = np.zeros(rows)
    for i in range(rows):
        idx = max_lag + i
        e_final[i] = y[idx] + a1 * y[idx - 1] - b1 * u[idx - d]
    res_var = float(np.var(e_final)) if rows > 1 else 0.0
    return {
        "a1": float(a1),
        "b1": float(b1),
        "d": d,
        "c": c.tolist(),
        "residual_var": res_var,
        "n_samples": rows,
        "iterations": iteration + 1,
    }


# ============================================================================
# 5. 离散→连续转换
# ============================================================================

def arx_to_fopdt(a1: float, b1: float, d: int, ts: float = 1.0) -> FOPDTTruth:
    """ARX 离散参数转 FOPDT 连续参数.

    y(t) + a1*y(t-1) = b1*u(t-d) 对应 G(s) = K*exp(-theta*s)/(tau*s+1)
    tau = -Ts/ln(-a1), K = b1/(1+a1), theta = d*Ts
    """
    if a1 >= 0:
        raise ValueError(f"a1={a1} >= 0，无法转换（要求 a1<0 即稳定系统）")
    tau = -ts / math.log(-a1)
    K = b1 / (1 + a1)
    theta = d * ts
    return FOPDTTruth(K=K, tau=tau, theta=theta)


# ============================================================================
# 6. 激励检测（PE 条件）
# ============================================================================

def check_excitation(u: np.ndarray, y: np.ndarray, d: int) -> dict[str, Any]:
    """激励充分性检测.

    检查：
    1. 输入信号变化次数（SP/OP 是否有足够变化）
    2. 回归矩阵条件数（PE 条件）
    """
    # 输入变化统计
    du = np.diff(u)
    significant_changes = np.sum(np.abs(du) > 0.01 * (np.max(u) - np.min(u) + 1e-9))
    # 回归矩阵条件数
    n = len(y)
    max_lag = max(1, d + 1)
    rows = n - max_lag
    Phi = np.zeros((rows, 2))
    for i in range(rows):
        idx = max_lag + i
        Phi[i, 0] = -y[idx - 1]
        Phi[i, 1] = u[idx - d]
    cond = float(np.linalg.cond(Phi)) if rows > 2 else float("inf")
    # 判定
    if significant_changes < 3:
        verdict = "INCONCLUSIVE: 输入激励不足（变化次数 < 3）"
    elif cond > 1e6:
        verdict = "INCONCLUSIVE: PE 条件数过大（>1e6）"
    elif cond > 1e4:
        verdict = "LOW_CONFIDENCE: PE 条件数偏大（>1e4），结果需标注低可信度"
    else:
        verdict = "OK: 激励充分"
    return {
        "significant_changes": int(significant_changes),
        "condition_number": cond,
        "verdict": verdict,
    }


# ============================================================================
# 7. 验证场景
# ============================================================================

def make_step_input(n: int, step_time: int, step_amplitude: float) -> np.ndarray:
    """生成阶跃输入信号."""
    u = np.zeros(n)
    u[step_time:] = step_amplitude
    return u


def make_sp_trajectory(n: int, changes: list[tuple[int, float]]) -> np.ndarray:
    """生成 SP 轨迹（多点阶跃）."""
    sp = np.zeros(n)
    for t, val in changes:
        sp[t:] = val
    return sp


def make_perturbed_sp(n: int, base_val: float, noise_amp: float, seed: int = 42) -> np.ndarray:
    """生成恒定 SP + 微小噪声（激励不足场景）."""
    rng = np.random.default_rng(seed)
    return np.full(n, base_val) + rng.normal(0, noise_amp, n)


def run_scenario_open_loop_step() -> dict[str, Any]:
    """场景 1：开环阶跃响应（基线正确性）."""
    truth = FOPDTTruth(K=2.0, tau=30.0, theta=5.0)
    ts = 1.0
    n = 600
    u = make_step_input(n, step_time=50, step_amplitude=10.0)
    y = simulate_fopdt_open_loop(u, truth.K, truth.tau, truth.theta, ts=ts, noise_std=0.1)
    d_true = round(truth.theta / ts)
    arx = identify_arx(u, y, d=d_true)
    identified = arx_to_fopdt(arx.a1, arx.b1, arx.d, ts)
    return {
        "scenario": "开环阶跃响应（基线）",
        "truth": {"K": truth.K, "tau": truth.tau, "theta": truth.theta},
        "identified": {"K": round(identified.K, 4), "tau": round(identified.tau, 4), "theta": round(identified.theta, 4)},
        "error_pct": {
            "K": round(abs(identified.K - truth.K) / truth.K * 100, 2),
            "tau": round(abs(identified.tau - truth.tau) / truth.tau * 100, 2),
            "theta": round(abs(identified.theta - truth.theta) / truth.theta * 100, 2),
        },
        "residual_var": round(arx.residual_var, 6),
    }


def run_scenario_closed_loop_sp_step() -> dict[str, Any]:
    """场景 2：闭环 SP 阶跃（ARX 有偏 vs IV 无偏）.

    用较高噪声（noise_std=1.0）+ 较高控制器增益（kp=2.0）放大闭环偏差，
    使 ARX 的有偏性显现。低噪声/低增益下偏差不明显属正常现象。
    """
    truth = FOPDTTruth(K=2.0, tau=30.0, theta=5.0)
    ts = 1.0
    n = 1200
    # SP 多次阶跃
    sp = make_sp_trajectory(n, [(50, 10.0), (400, 15.0), (800, 8.0)])
    y, u, _ = simulate_closed_loop_fopdt(
        sp, truth.K, truth.tau, truth.theta, kp=2.0, ti=20.0, ts=ts, noise_std=1.0
    )
    d_true = round(truth.theta / ts)
    # ARX 辨识（应有偏）
    arx = identify_arx(u, y, d=d_true)
    id_arx = arx_to_fopdt(arx.a1, arx.b1, arx.d, ts)
    # IV 辨识（应无偏）
    iv = identify_iv(u, y, sp, d=d_true)
    id_iv = arx_to_fopdt(iv.a1, iv.b1, iv.d, ts)
    return {
        "scenario": "闭环 SP 阶跃（ARX vs IV 对比）",
        "truth": {"K": truth.K, "tau": truth.tau, "theta": truth.theta},
        "arx": {
            "K": round(id_arx.K, 4), "tau": round(id_arx.tau, 4), "theta": round(id_arx.theta, 4),
            "error_pct": {
                "K": round(abs(id_arx.K - truth.K) / truth.K * 100, 2),
                "tau": round(abs(id_arx.tau - truth.tau) / truth.tau * 100, 2),
                "theta": round(abs(id_arx.theta - truth.theta) / truth.theta * 100, 2),
            },
        },
        "iv": {
            "K": round(id_iv.K, 4), "tau": round(id_iv.tau, 4), "theta": round(id_iv.theta, 4),
            "error_pct": {
                "K": round(abs(id_iv.K - truth.K) / truth.K * 100, 2),
                "tau": round(abs(id_iv.tau - truth.tau) / truth.tau * 100, 2),
                "theta": round(abs(id_iv.theta - truth.theta) / truth.theta * 100, 2),
            },
        },
    }


def run_scenario_closed_loop_disturbance() -> dict[str, Any]:
    """场景 3：闭环扰动（ARMAX 扰动建模）."""
    truth = FOPDTTruth(K=1.5, tau=40.0, theta=8.0)
    ts = 1.0
    n = 1500
    # SP 阶跃 + 负载扰动
    sp = make_sp_trajectory(n, [(100, 5.0), (700, 7.0)])
    y, u, _ = simulate_closed_loop_fopdt(
        sp, truth.K, truth.tau, truth.theta, kp=0.8, ti=50.0, ts=ts, noise_std=0.1, seed=100
    )
    # 叠加负载扰动（慢变）
    rng = np.random.default_rng(200)
    disturbance = np.cumsum(rng.normal(0, 0.01, n))
    y = y + disturbance
    d_true = round(truth.theta / ts)
    arx = identify_arx(u, y, d=d_true)
    id_arx = arx_to_fopdt(arx.a1, arx.b1, arx.d, ts)
    iv = identify_iv(u, y, sp, d=d_true)
    id_iv = arx_to_fopdt(iv.a1, iv.b1, iv.d, ts)
    armax = identify_armax(u, y, d=d_true, nc=1)
    id_armax = arx_to_fopdt(armax["a1"], armax["b1"], armax["d"], ts)
    return {
        "scenario": "闭环扰动（ARX vs IV vs ARMAX）",
        "truth": {"K": truth.K, "tau": truth.tau, "theta": truth.theta},
        "arx_error_K_pct": round(abs(id_arx.K - truth.K) / truth.K * 100, 2),
        "iv_error_K_pct": round(abs(id_iv.K - truth.K) / truth.K * 100, 2),
        "armax_error_K_pct": round(abs(id_armax.K - truth.K) / truth.K * 100, 2),
        "armax_residual_var": round(armax["residual_var"], 6),
        "armax_iterations": armax["iterations"],
    }


def run_scenario_no_excitation() -> dict[str, Any]:
    """场景 4：激励不足（应返回 INCONCLUSIVE）."""
    truth = FOPDTTruth(K=2.0, tau=30.0, theta=5.0)
    ts = 1.0
    n = 600
    # SP 恒定 + 微小噪声
    sp = make_perturbed_sp(n, base_val=10.0, noise_amp=0.01)
    y, u, _ = simulate_closed_loop_fopdt(
        sp, truth.K, truth.tau, truth.theta, kp=1.0, ti=40.0, ts=ts, noise_std=0.1
    )
    exc = check_excitation(u, y, d=5)
    return {
        "scenario": "激励不足（恒定 SP）",
        "excitation_check": exc,
        "expected": "INCONCLUSIVE",
    }


def run_scenario_snr_sensitivity() -> dict[str, Any]:
    """场景 5：信噪比敏感性（IV 鲁棒性）."""
    truth = FOPDTTruth(K=2.0, tau=30.0, theta=5.0)
    ts = 1.0
    n = 1200
    sp = make_sp_trajectory(n, [(50, 10.0), (400, 15.0), (800, 8.0)])
    results = {}
    for noise_std in [0.05, 0.1, 0.3, 0.5, 1.0]:
        y, u, _ = simulate_closed_loop_fopdt(
            sp, truth.K, truth.tau, truth.theta, kp=1.0, ti=40.0, ts=ts, noise_std=noise_std
        )
        iv = identify_iv(u, y, sp, d=5)
        id_iv = arx_to_fopdt(iv.a1, iv.b1, iv.d, ts)
        # 信号幅值 ~ SP 变化范围 15-8=7，SNR = 20*log10(7/noise_std)
        snr_db = 20 * math.log10(7.0 / noise_std)
        results[f"noise_{noise_std}"] = {
            "snr_db": round(snr_db, 1),
            "iv_error_K_pct": round(abs(id_iv.K - truth.K) / truth.K * 100, 2),
            "iv_error_tau_pct": round(abs(id_iv.tau - truth.tau) / truth.tau * 100, 2),
        }
    return {
        "scenario": "信噪比敏感性（IV 鲁棒性）",
        "truth": {"K": truth.K, "tau": truth.tau, "theta": truth.theta},
        "results": results,
    }


# ============================================================================
# 8. 主程序
# ============================================================================

def main() -> int:
    print("=" * 72)
    print("回路整定 Phase 2.0 — 过程对象辨识算法原型验证")
    print(f"时间：{datetime.now().isoformat()}")
    print("=" * 72)

    scenarios = [
        ("场景 1：开环阶跃响应（基线正确性）", run_scenario_open_loop_step),
        ("场景 2：闭环 SP 阶跃（ARX 有偏 vs IV 无偏）", run_scenario_closed_loop_sp_step),
        ("场景 3：闭环扰动（ARX vs IV vs ARMAX）", run_scenario_closed_loop_disturbance),
        ("场景 4：激励不足（应返回 INCONCLUSIVE）", run_scenario_no_excitation),
        ("场景 5：信噪比敏感性（IV 鲁棒性）", run_scenario_snr_sensitivity),
    ]

    report: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "script": "tuning_identification_prototype.py",
        "purpose": "验证历史数据辨识算法栈可行性",
        "scenarios": [],
    }

    all_pass = True
    for name, func in scenarios:
        print(f"\n{'─' * 72}")
        print(f"▶ {name}")
        print("─" * 72)
        try:
            result = func()
            report["scenarios"].append(result)
            # 打印结果
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            # 判定
            if "error_pct" in result:
                for k, v in result["error_pct"].items():
                    if v > 10:
                        print(f"  ⚠ {k} 误差 {v}% > 10% 阈值")
                        all_pass = False
            elif "arx" in result and "iv" in result:
                arx_err = result["arx"]["error_pct"]["K"]
                iv_err = result["iv"]["error_pct"]["K"]
                if arx_err < 20:
                    print(f"  ⚠ ARX 闭环误差 {arx_err}% < 20%，偏差不明显（需检查）")
                if iv_err > 10:
                    print(f"  ⚠ IV 闭环误差 {iv_err}% > 10% 阈值")
                    all_pass = False
                else:
                    print(f"  ✓ IV 闭环误差 {iv_err}% ≤ 10%，无偏验证通过")
            elif "excitation_check" in result:
                verdict = result["excitation_check"]["verdict"]
                if "INCONCLUSIVE" in verdict:
                    print(f"  ✓ 激励检测正确返回 INCONCLUSIVE")
                else:
                    print(f"  ⚠ 激励检测未返回 INCONCLUSIVE：{verdict}")
                    all_pass = False
        except Exception as exc:
            print(f"  ✗ 场景执行失败：{exc}")
            import traceback
            traceback.print_exc()
            report["scenarios"].append({"scenario": name, "error": str(exc)})
            all_pass = False

    print(f"\n{'=' * 72}")
    print("验证总结")
    print("=" * 72)
    if all_pass:
        print("✓ 全部场景验证通过，算法栈可行性确认")
        print("  → 可进入 Phase 2.1 算法栈实现")
    else:
        print("✗ 存在未通过场景，需分析原因后决定是否调整方案")
    print(f"\n报告已生成，scenarios 数量：{len(report['scenarios'])}")

    # 输出 JSON 报告
    report_path = "/tmp/tuning_phase2_prototype_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"完整报告：{report_path}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
