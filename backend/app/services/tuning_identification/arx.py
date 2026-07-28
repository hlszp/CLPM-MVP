"""ARX 辨识（线性最小二乘，算法栈层 3）.

模型：A(z⁻¹)·y(t) = B(z⁻¹)·u(t-d) + e(t)

定位：
- 数据干净时单独使用
- 给 ARMAX/IV 提供初值
- 阶次扫描快速试算
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ARXResult:
    """ARX 辨识结果：y(t) + a1*y(t-1) + ... = b1*u(t-d) + ... + e(t)."""

    a_coeffs: list[float]  # A 多项式系数 [a1, a2, ...]（不含 a0=1）
    b_coeffs: list[float]  # B 多项式系数 [b1, b2, ...]
    d: int  # 纯滞后（采样数）
    residual_var: float
    n_samples: int
    r_squared: float

    @property
    def is_stable(self) -> bool:
        """稳定性：一阶时 a1 < 0 即稳定."""
        if len(self.a_coeffs) == 1:
            return self.a_coeffs[0] < 0
        # 高阶：检查极点是否在单位圆内（简化判定）
        return all(abs(a) < 1.0 for a in self.a_coeffs)


def identify_arx(
    u: np.ndarray,
    y: np.ndarray,
    d: int,
    na: int = 1,
    nb: int = 1,
) -> ARXResult:
    """ARX 辨识：y(t) + a1*y(t-1) + ... = b1*u(t-d) + ... + e(t).

    最小二乘解析解：theta = (Phi^T Phi)^-1 Phi^T y_reg

    Args:
        u: 输入信号（OP 时序）
        y: 输出信号（PV 时序）
        d: 纯滞后（采样数）
        na: A 多项式阶次
        nb: B 多项式阶次

    Returns:
        ARXResult

    Raises:
        ValueError: 数据不足或数值异常
    """
    n = len(y)
    max_lag = max(na, nb + d)
    min_samples = max_lag + 10
    if n < min_samples:
        raise ValueError(f"ARX 数据不足：{n} 点，需 ≥ {min_samples}")

    rows = n - max_lag
    n_params = na + nb
    Phi = np.zeros((rows, n_params))
    y_reg = np.zeros(rows)
    for i in range(rows):
        idx = max_lag + i
        for j in range(na):
            Phi[i, j] = -y[idx - 1 - j]
        for j in range(nb):
            Phi[i, na + j] = u[idx - d - j]
        y_reg[i] = y[idx]

    # 最小二乘
    theta, _, _, _ = np.linalg.lstsq(Phi, y_reg, rcond=None)
    y_pred = Phi @ theta
    residuals = y_reg - y_pred
    res_var = float(np.var(residuals)) if rows > 1 else 0.0
    # R²
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_reg - np.mean(y_reg)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return ARXResult(
        a_coeffs=[float(t) for t in theta[:na]],
        b_coeffs=[float(t) for t in theta[na:]],
        d=d,
        residual_var=res_var,
        n_samples=rows,
        r_squared=r2,
    )
