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
    """ARMAX 辨识：A·y = B·u + C·e（e = C·ε，C 为 MA 多项式）.

    迭代 PEM（P3-06a O-A3 一致性修复）：
    1. ARX 得初始 A/B
    2. 计算方程误差 e = A·y − B·u
    3. 由当前 C 反滤波得一步预测误差估计 ε̂（ε̂ = e/C，递推）
    4. 估 C：e(t) = Σ c_j·ε̂(t−j) + ε —— **与消费语义一致**（e = C·ε）；
       原实现按 AR 形回归 e(t) = −Σ c_j·e(t−j)，与 e = C·ε 的前向消费
       （ε = e/C 反滤波、轨 2 LB 检验）不一致：对一阶噪声反滤波会二次
       注入自相关（实测 ε̂ acf₁≈0.95，创新契约系统性失败——C19-ID1）
    5. 重估 A/B：y + a1·y(t-1) = b1·u(t-d) + c1·ε̂(t-1) + ε
    6. 收敛判断
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

        # 一步预测误差估计 ε̂ = e/C（当前 C 反滤波；首迭代 C=0 → ε̂=e）
        eps_hat = np.zeros(rows)
        for i in range(rows):
            val = e[i]
            for j, cj in enumerate(c_coeffs):
                if i - 1 - j >= 0:
                    val -= cj * eps_hat[i - 1 - j]
            eps_hat[i] = val

        # 估 C（MA 形，与 e = C·ε 消费语义一致）：e(t) = Σ c_j·ε̂(t−j) + ε
        if nc > 0:
            E_phi = np.zeros((rows, nc))
            if type(u) is np.ndarray and type(y) is np.ndarray and u.ndim == y.ndim == 1:
                for j in range(min(nc, rows - 1)):
                    E_phi[j + 1 :, j] = eps_hat[: rows - 1 - j]
            else:
                for i in range(rows):
                    for j in range(nc):
                        E_phi[i, j] = eps_hat[i - 1 - j] if (i - 1 - j) >= 0 else 0.0
            c_theta, _, _, _ = np.linalg.lstsq(E_phi, e, rcond=None)
            c_coeffs = [float(t) for t in c_theta]

        # 重估 A/B：y + a1*y(t-1) = b1*u(t-d) + c1*ε̂(t-1) + eps
        Phi = np.zeros((rows, na + nc + nb))
        y_reg = np.zeros(rows)
        if (
            type(u) is np.ndarray
            and type(y) is np.ndarray
            and u.ndim == y.ndim == 1
            and na >= 0
            and nb >= 0
            and nc >= 0
            and (nb == 0 or len(u) >= n - d)
        ):
            for j in range(na):
                Phi[:, j] = -y[max_lag - 1 - j : n - 1 - j]
            for j in range(min(nc, rows - 1)):
                Phi[j + 1 :, na + j] = eps_hat[: rows - 1 - j]
            for j in range(nb):
                Phi[:, na + nc + j] = u[max_lag - d - j : n - d - j]
            y_reg[:] = y[max_lag:]
        else:
            for i in range(rows):
                idx = max_lag + i
                for j in range(na):
                    Phi[i, j] = -y[idx - 1 - j]
                for j in range(nc):
                    Phi[i, na + j] = eps_hat[i - 1 - j] if (i - 1 - j) >= 0 else 0.0
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
