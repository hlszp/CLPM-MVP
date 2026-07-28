"""ARMAX 辨识（预测误差法 PEM，算法栈层 3）.

模型：A(z⁻¹)·y(t) = B(z⁻¹)·u(t-d) + C(z⁻¹)·e(t)

C 多项式显式建模扰动通道，适合负载扰动主导的场景。
用迭代 PEM：固定 C 估 A/B，固定 A/B 估 C，交替至收敛。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app.services.tuning_identification.arx import identify_arx

logger = logging.getLogger(__name__)


@dataclass
class ARMAXResult:
    """ARMAX 辨识结果."""

    a_coeffs: list[float]
    b_coeffs: list[float]
    c_coeffs: list[float]
    d: int
    residual_var: float
    n_samples: int
    r_squared: float
    iterations: int


def identify_armax(
    u: np.ndarray,
    y: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
    nc: int = 1,
    max_iter: int = 200,
    tol: float = 1e-6,
) -> ARMAXResult:
    """ARMAX 辨识：A·y = B·u + C·e.

    迭代 PEM：
    1. ARX 得初始 A/B
    2. 计算残差 e
    3. 估 C：e(t) = -c1*e(t-1) - ... + eps
    4. 重估 A/B：y + a1*y(t-1) = b1*u(t-d) + c1*e(t-1) + eps
    5. 收敛判断
    """
    arx = identify_arx(u, y, d, na, nb)
    a_coeffs = list(arx.a_coeffs)
    b_coeffs = list(arx.b_coeffs)
    c_coeffs = [0.0] * nc

    n = len(y)
    max_lag = max(na, nb + d)
    rows = n - max_lag
    if rows < 5:
        raise ValueError(f"ARMAX 数据不足：{rows} 行")

    iterations_done = 0
    for _ in range(1, max_iter + 1):
        iterations_done += 1
        # 计算残差 e（按回归行序号 i 索引）
        e = np.zeros(rows)
        for i in range(rows):
            idx = max_lag + i
            e[i] = y[idx]
            for j in range(na):
                e[i] += a_coeffs[j] * y[idx - 1 - j]
            for j in range(nb):
                e[i] -= b_coeffs[j] * u[idx - d - j]

        # 估 C：e(t) = -c1*e(t-1) - ... + eps
        if nc > 0:
            E_phi = np.zeros((rows, nc))
            for i in range(rows):
                for j in range(nc):
                    E_phi[i, j] = -e[i - 1 - j] if (i - 1 - j) >= 0 else 0.0
            c_theta, _, _, _ = np.linalg.lstsq(E_phi, e, rcond=None)
            c_coeffs = [float(t) for t in c_theta]

        # 重估 A/B：y + a1*y(t-1) = b1*u(t-d) + c1*e(t-1) + eps
        Phi = np.zeros((rows, na + nc + nb))
        y_reg = np.zeros(rows)
        for i in range(rows):
            idx = max_lag + i
            for j in range(na):
                Phi[i, j] = -y[idx - 1 - j]
            for j in range(nc):
                Phi[i, na + j] = -e[i - 1 - j] if (i - 1 - j) >= 0 else 0.0
            for j in range(nb):
                Phi[i, na + nc + j] = u[idx - d - j]
            y_reg[i] = y[idx]
        theta_new, _, _, _ = np.linalg.lstsq(Phi, y_reg, rcond=None)
        a_new = [float(t) for t in theta_new[:na]]
        b_new = [float(t) for t in theta_new[na + nc :]]

        # 收敛判断
        max_diff = max(
            max(abs(a_new[k] - a_coeffs[k]) for k in range(na)) if na > 0 else 0.0,
            max(abs(b_new[k] - b_coeffs[k]) for k in range(nb)) if nb > 0 else 0.0,
        )
        a_coeffs, b_coeffs = a_new, b_new
        if max_diff < tol:
            break

    # 最终残差与 R²
    e_final = np.zeros(rows)
    for i in range(rows):
        idx = max_lag + i
        e_final[i] = y[idx]
        for j in range(na):
            e_final[i] += a_coeffs[j] * y[idx - 1 - j]
        for j in range(nb):
            e_final[i] -= b_coeffs[j] * u[idx - d - j]
    res_var = float(np.var(e_final)) if rows > 1 else 0.0
    ss_res = float(np.sum(e_final**2))
    ss_tot = float(np.sum((y_reg - np.mean(y_reg)) ** 2)) if rows > 0 else 0.0
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return ARMAXResult(
        a_coeffs=a_coeffs,
        b_coeffs=b_coeffs,
        c_coeffs=c_coeffs,
        d=d,
        residual_var=res_var,
        n_samples=rows,
        r_squared=r2,
        iterations=iterations_done,
    )
