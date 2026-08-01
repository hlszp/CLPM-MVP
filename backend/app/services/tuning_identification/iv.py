"""工具变量辨识（闭环 CLIVC + 实验性原型）.

P2-009 新增 ``identify_clivc`` / ``identify_clivc4``：可证明闭环一致的工具
变量法，使用外生设定值 SP 作为工具变量源，满足 ``E[Z·ε] = 0``。这两者已
进入生产候选集，可用于闭环历史辨识。

早期 ``identify_iv`` / ``identify_iv4`` 保留为对照原型：它们把内生 OP 放入
工具变量矩阵，不能证明闭环一致性，生产 pipeline 不调用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app.services.tuning_identification.arx import identify_arx

logger = logging.getLogger(__name__)

# 机器可读能力标记。
# CLIVC = 可证明闭环一致的 IV（P2-009，生产可用）
# EXPERIMENTAL = 早期原型，不进入生产选模
IV_CAPABILITY_STATUS = "CLIVC_PRODUCTION_READY"


@dataclass
class IVResult:
    """IV 辨识结果（结构同 ARXResult）."""

    a_coeffs: list[float]
    b_coeffs: list[float]
    d: int
    residual_var: float
    n_samples: int
    r_squared: float
    iterations: int


def identify_clivc(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
) -> IVResult:
    """P2-009：闭环工具变量法（CLIVC）— 使用外生 SP 作为工具变量.

    闭环一致性证明：
        闭环下 u(k)=C·[sp(k)−y(k)]，u 经 y 与扰动 ν 相关，ARX 有偏。
        SP 由操作员设定，外生于过程扰动：E[sp(k)·ν(j)] = 0 ∀k,j。
        方程误差 e(k)=A(z⁻¹)·ν(k)，故 E[sp(k)·e(k)] = A·E[sp·ν] = 0。

    工具变量矩阵 Z 的构造（对每个回归量用 SP 替换内生量）：
        回归量 -y(k-j)  → 工具 -sp(k-j)   （SP 外生，与 y 相关经闭环）
        回归量 u(k-d-j) → 工具 sp(k-d-j)  （SP 驱动控制器，与 u 相关）

    θ_IV = (Z^T Φ)^−1 Z^T y，满足一致性（E[Z·ε]=0）与相关性（SP 驱动闭环）。

    Args:
        u: 输入（OP，闭环下内生）
        y: 输出（PV）
        sp: 设定值（外生工具变量源）
        d: 纯滞后（采样数）
        na: A 阶次
        nb: B 阶次

    Returns:
        IVResult（一致性 IV 估计）
    """
    n = len(y)
    max_lag = max(na, nb + d)
    rows = n - max_lag
    n_params = na + nb
    if rows < n_params + 5:
        raise ValueError(f"CLIVC 数据不足：{rows} 行，需 ≥ {n_params + 5}")

    Phi = np.zeros((rows, n_params))
    Z = np.zeros((rows, n_params))
    y_reg = np.zeros(rows)
    for i in range(rows):
        idx = max_lag + i
        for j in range(na):
            Phi[i, j] = -y[idx - 1 - j]
            # 工具变量：外生 SP 替换内生 y
            Z[i, j] = -sp[idx - 1 - j]
        for j in range(nb):
            Phi[i, na + j] = u[idx - d - j]
            # 工具变量：外生 SP 替换内生 u（闭环下 u 经控制器与 SP 相关）
            Z[i, na + j] = sp[idx - d - j]
        y_reg[i] = y[idx]

    # IV 解：θ = (Z^T Φ)^-1 Z^T y
    ZtPhi = Z.T @ Phi
    Zty = Z.T @ y_reg
    try:
        theta = np.linalg.solve(ZtPhi, Zty)
    except np.linalg.LinAlgError:
        logger.warning("CLIVC 矩阵奇异，回退 lstsq")
        theta, _, _, _ = np.linalg.lstsq(ZtPhi, Zty, rcond=None)

    y_pred = Phi @ theta
    residuals = y_reg - y_pred
    res_var = float(np.var(residuals)) if rows > 1 else 0.0
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_reg - np.mean(y_reg)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return IVResult(
        a_coeffs=[float(t) for t in theta[:na]],
        b_coeffs=[float(t) for t in theta[na:]],
        d=d,
        residual_var=res_var,
        n_samples=rows,
        r_squared=r2,
        iterations=1,
    )


def identify_clivc4(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
    max_iter: int = 5,
    tol: float = 1e-6,
) -> IVResult:
    """P2-009：精炼闭环 IV（IV4 迭代）— 用模型预测 ŷ_f 作工具变量提升效率.

    CLIVC（``identify_clivc``）已一致（无偏），但用原始 SP 作工具方差较大。
    IV4 迭代用上一步模型滤波 SP 得到无扰预测 ŷ_f，作为 y 的工具变量，
    提升工具与回归量的相关性 → 降低方差（更高效），一致性不变。

    流程：
    1. 初始 CLIVC 估计 θ̂₀
    2. 用 θ̂ 滤波 SP 得 ŷ_f（无扰仿真，不含 ν）
    3. 用 ŷ_f 作 y 的工具，SP 作 u 的工具，重新 IV 求解
    4. 收敛则停止

    Args:
        u, y, sp, d, na, nb: 同 identify_clivc
        max_iter: 最大迭代次数
        tol: 收敛阈值（参数相对变化）

    Returns:
        IVResult（精炼 IV 估计）
    """
    # 步骤 1：初始 CLIVC 估计
    result0 = identify_clivc(u, y, sp, d, na, nb)
    theta = np.array(result0.a_coeffs + result0.b_coeffs)

    n = len(y)
    max_lag = max(na, nb + d)
    rows = n - max_lag

    iterations_done = 1
    for _ in range(1, max_iter):
        iterations_done += 1
        a_coeffs = theta[:na]
        b_coeffs = theta[na:]

        # 用当前模型对 SP 做无扰自由仿真 → ŷ_f（不含 ν，可作工具）
        y_f = _simulate_disturbance_free(sp, a_coeffs, b_coeffs, d, na, nb, max_lag, n)

        # 构建矩阵：Φ 用真实 y/u，Z 用 ŷ_f 和 SP
        Phi = np.zeros((rows, na + nb))
        Z = np.zeros((rows, na + nb))
        y_reg = np.zeros(rows)
        for i in range(rows):
            idx = max_lag + i
            for j in range(na):
                Phi[i, j] = -y[idx - 1 - j]
                Z[i, j] = -y_f[idx - 1 - j]  # 无扰预测替换 SP（更高相关性）
            for j in range(nb):
                Phi[i, na + j] = u[idx - d - j]
                Z[i, na + j] = sp[idx - d - j]  # u 的工具仍用外生 SP
            y_reg[i] = y[idx]

        ZtPhi = Z.T @ Phi
        Zty = Z.T @ y_reg
        try:
            theta_new = np.linalg.solve(ZtPhi, Zty)
        except np.linalg.LinAlgError:
            theta_new, _, _, _ = np.linalg.lstsq(ZtPhi, Zty, rcond=None)

        if np.max(np.abs(theta_new - theta)) < tol:
            theta = theta_new
            break
        theta = theta_new

    # 最终结果
    y_pred = Phi @ theta
    residuals = y_reg - y_pred
    res_var = float(np.var(residuals)) if rows > 1 else 0.0
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_reg - np.mean(y_reg)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return IVResult(
        a_coeffs=[float(t) for t in theta[:na]],
        b_coeffs=[float(t) for t in theta[na:]],
        d=d,
        residual_var=res_var,
        n_samples=rows,
        r_squared=r2,
        iterations=iterations_done,
    )


def _simulate_disturbance_free(
    sp: np.ndarray,
    a_coeffs: np.ndarray | list[float],
    b_coeffs: np.ndarray | list[float],
    d: int,
    na: int,
    nb: int,
    max_lag: int,
    n: int,
) -> np.ndarray:
    """用当前模型对 SP 做无扰自由仿真（IV4 工具变量源）.

    ŷ_f(k) = -Σ a_j·ŷ_f(k-1-j) + Σ b_j·sp(k-d-j)
    初始条件置零（无扰仿真从零开始）。
    """
    y_f = np.zeros(n)
    for i in range(max_lag, n):
        val = 0.0
        for j in range(na):
            val -= a_coeffs[j] * y_f[i - 1 - j]
        for j in range(nb):
            idx_u = i - d - j
            if idx_u >= 0:
                val += b_coeffs[j] * sp[idx_u]
        # 发散保护：不稳定模型自由仿真会溢出，截断避免 NaN 污染工具变量
        if not np.isfinite(val) or abs(val) > 1e12:
            y_f[i] = 0.0
        else:
            y_f[i] = val
    return y_f


# ---------------------------------------------------------------------------
# 以下为早期实验性原型（不进入生产选模，保留供对照测试）
# ---------------------------------------------------------------------------


def identify_iv(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
) -> IVResult:
    """运行早期 IV 数值原型.

    注意：``u`` 同时出现在回归量和工具变量中，闭环一致性条件尚不成立。
    此函数只供离线对照测试，生产 pipeline 不调用。

    Args:
        u: 输入信号（OP 时序）
        y: 输出信号（PV 时序）
        sp: 设定值时序（作为工具变量源）
        d: 纯滞后
        na: A 阶次
        nb: B 阶次

    Returns:
        IVResult
    """
    n = len(y)
    max_lag = max(na, nb + d)
    rows = n - max_lag
    n_params = na + nb

    # 构建回归矩阵 Phi 和工具变量矩阵 Z
    Phi = np.zeros((rows, n_params))
    Z = np.zeros((rows, n_params))
    y_reg = np.zeros(rows)
    for i in range(rows):
        idx = max_lag + i
        for j in range(na):
            Phi[i, j] = -y[idx - 1 - j]
            # 工具变量：用 SP 延迟替代 y 延迟
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
        logger.warning("IV 矩阵奇异，回退 lstsq")
        theta, _, _, _ = np.linalg.lstsq(ZtPhi, Zty, rcond=None)

    y_pred = Phi @ theta
    residuals = y_reg - y_pred
    res_var = float(np.var(residuals)) if rows > 1 else 0.0
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_reg - np.mean(y_reg)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return IVResult(
        a_coeffs=[float(t) for t in theta[:na]],
        b_coeffs=[float(t) for t in theta[na:]],
        d=d,
        residual_var=res_var,
        n_samples=rows,
        r_squared=r2,
        iterations=1,
    )


def identify_iv4(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
    max_iter: int = 5,
    tol: float = 1e-6,
) -> IVResult:
    """运行早期“IV4”循环原型（非合格 IV4 实现）.

    当前循环重复同一组未加权法方程，没有估计噪声模型或更新工具变量；
    名称仅为兼容既有调用。只供离线对照测试，生产 pipeline 不调用。
    """
    # 步骤 1：ARX 初始估计
    arx = identify_arx(u, y, d, na, nb)
    theta = np.array(arx.a_coeffs + arx.b_coeffs)

    n = len(y)
    max_lag = max(na, nb + d)
    rows = n - max_lag

    iterations_done = 0
    for _ in range(1, max_iter + 1):
        iterations_done += 1
        # 构建矩阵
        Phi = np.zeros((rows, na + nb))
        Z = np.zeros((rows, na + nb))
        y_reg = np.zeros(rows)
        for i in range(rows):
            idx = max_lag + i
            for j in range(na):
                Phi[i, j] = -y[idx - 1 - j]
                Z[i, j] = -sp[idx - 1 - j]
            for j in range(nb):
                Phi[i, na + j] = u[idx - d - j]
                Z[i, na + j] = u[idx - d - j]
            y_reg[i] = y[idx]
        # IV 求解
        ZtPhi = Z.T @ Phi
        Zty = Z.T @ y_reg
        try:
            theta_new = np.linalg.solve(ZtPhi, Zty)
        except np.linalg.LinAlgError:
            theta_new, _, _, _ = np.linalg.lstsq(ZtPhi, Zty, rcond=None)
        # 收敛判断
        if np.max(np.abs(theta_new - theta)) < tol:
            theta = theta_new
            break
        theta = theta_new

    # 最终结果
    y_pred = Phi @ theta
    residuals = y_reg - y_pred
    res_var = float(np.var(residuals)) if rows > 1 else 0.0
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_reg - np.mean(y_reg)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return IVResult(
        a_coeffs=[float(t) for t in theta[:na]],
        b_coeffs=[float(t) for t in theta[na:]],
        d=d,
        residual_var=res_var,
        n_samples=rows,
        r_squared=r2,
        iterations=iterations_done,
    )
