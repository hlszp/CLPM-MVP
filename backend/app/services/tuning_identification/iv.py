"""实验性工具变量原型（不进入生产选模）.

本模块保留早期数值原型，便于后续与合格闭环 IV/OE/PEM 实现做对照。当前
``identify_iv`` 把内生 OP 回归项同时放入工具变量矩阵，不能证明
``E[Z·ε] = 0``；``identify_iv4`` 也没有实现噪声模型估计和加权工具更新。
因此二者不得宣称闭环无偏，不得作为模型发布或 PID 推荐依据。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app.services.tuning_identification.arx import identify_arx

logger = logging.getLogger(__name__)

# 机器可读能力标记：发布与整定门禁不得把本模块视为生产能力。
IV_CAPABILITY_STATUS = "EXPERIMENTAL"


@dataclass
class IVResult:
    """实验性 IV 数值结果（结构同 ARXResult）."""

    a_coeffs: list[float]
    b_coeffs: list[float]
    d: int
    residual_var: float
    n_samples: int
    r_squared: float
    iterations: int


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
