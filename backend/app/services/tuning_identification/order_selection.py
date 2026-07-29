"""阶次选择与模型择优（算法栈层 4）.

准则：
- AIC/BIC：信息准则，选最小者
- Ljung-Box Q 检验：残差白噪声检验
- 交叉验证：前 70% 辨识 / 后 30% 验证 R²
- Occam 削减：SOPDT 优于 FOPDT 当且仅当 R² 提升 >5% 且 BIC 下降
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

logger = logging.getLogger(__name__)


@dataclass
class OrderSelectionResult:
    """阶次选择结果."""

    selected_na: int
    selected_nb: int
    aic: float
    bic: float
    ljung_box_p: float
    residual_white: bool
    cv_r_squared: float | None


def compute_aic(n_samples: int, residual_var: float, n_params: int) -> float:
    """AIC = N*ln(σ²) + 2*p."""
    if residual_var <= 0:
        residual_var = 1e-12
    return n_samples * math.log(residual_var) + 2 * n_params


def compute_bic(n_samples: int, residual_var: float, n_params: int) -> float:
    """BIC = N*ln(σ²) + p*ln(N)."""
    if residual_var <= 0:
        residual_var = 1e-12
    return n_samples * math.log(residual_var) + n_params * math.log(max(n_samples, 2))


def ljung_box_test(
    residuals: np.ndarray,
    max_lag: int = 10,
) -> tuple[float, float]:
    """Ljung-Box Q 检验：残差是否白噪声.

    Returns:
        (Q 统计量, p 值)；p > 0.05 则残差白噪声（模型充分）
    """
    n = len(residuals)
    if n < max_lag + 5:
        return 0.0, 1.0
    # ACF 必须先中心化：np.var 自身会去均值，但乘积项 residuals[k:]*residuals[:-k]
    # 不会——非零均值 μ 会注入 μ²/σ² 的恒定伪相关，把白噪声误判为有色。
    r = residuals - np.mean(residuals)
    var = np.var(r)
    if var < 1e-12:
        return 0.0, 1.0
    acf = np.zeros(max_lag)
    for k in range(1, max_lag + 1):
        acf[k - 1] = np.mean(r[k:] * r[:-k]) / var
    # Q 统计量
    Q = n * (n + 2) * sum(acf[k - 1] ** 2 / (n - k) for k in range(1, max_lag + 1))
    # p 值（卡方分布）
    p_value = 1.0 - stats.chi2.cdf(Q, max_lag)
    return float(Q), float(p_value)


def cross_validate(
    u: np.ndarray,
    y: np.ndarray,
    identify_fn,
    train_ratio: float = 0.7,
) -> float | None:
    """交叉验证 R²：前 train_ratio 辨识，后段验证.

    Args:
        identify_fn: callable(u, y, d) -> result，result 含 a_coeffs/b_coeffs/d
    """
    n = len(y)
    split = int(n * train_ratio)
    if split < 20 or n - split < 10:
        return None
    try:
        result = identify_fn(u[:split], y[:split])
        # 在验证段预测
        a = result.a_coeffs
        b = result.b_coeffs
        d = result.d
        na = len(a)
        max_lag = max(na, len(b) + d)
        y_pred = np.zeros(n - split)
        for i in range(n - split):
            idx = split + i
            if idx < max_lag:
                continue
            val = 0.0
            for j in range(na):
                val -= a[j] * y[idx - 1 - j]
            for j in range(len(b)):
                val += b[j] * u[idx - d - j]
            y_pred[i] = val
        y_val = y[split:]
        ss_res = float(np.sum((y_val - y_pred) ** 2))
        ss_tot = float(np.sum((y_val - np.mean(y_val)) ** 2))
        return 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    except Exception:
        logger.debug("交叉验证失败", exc_info=True)
        return None


def select_order(
    residuals: np.ndarray,
    n_samples: int,
    residual_var: float,
    n_params: int,
    max_lag: int = 10,
) -> OrderSelectionResult:
    """阶次选择综合判定."""
    aic = compute_aic(n_samples, residual_var, n_params)
    bic = compute_bic(n_samples, residual_var, n_params)
    Q, p_value = ljung_box_test(residuals, max_lag)
    return OrderSelectionResult(
        selected_na=1,  # 由调用方覆盖
        selected_nb=1,
        aic=aic,
        bic=bic,
        ljung_box_p=p_value,
        residual_white=p_value > 0.05,
        cv_r_squared=None,
    )
