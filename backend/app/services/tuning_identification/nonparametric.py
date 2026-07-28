"""非参数粗估（算法栈层 2）.

相关分析法估计脉冲响应 → K 粗估、tau+theta 粗估。
Welch 谱分析估计频率响应 → Bode 形状、阶次先验。

作用：
1. 提供独立于 ARX 的 K 估计用于交叉校验
2. 为参数化辨识提供初值
3. 直观判断阶次
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy import signal

logger = logging.getLogger(__name__)


@dataclass
class NonparametricEstimate:
    """非参数估计结果."""

    gain_estimate: float  # K 粗估
    time_constant_estimate: float  # tau + theta 粗估（秒）
    cross_correlation: np.ndarray  # 互相关函数
    lags: np.ndarray
    coherence: float | None  # 相干函数均值（频带内）


def correlation_analysis(
    u: np.ndarray,
    y: np.ndarray,
    ts: float = 1.0,
) -> NonparametricEstimate:
    """相关分析法估脉冲响应.

    对白噪声输入，互相关 R_uy(τ) ∝ 脉冲响应 g(τ)。
    对任意输入，提供 K 粗估和时间常数粗估。
    """
    n = len(u)
    u_centered = u - np.mean(u)
    y_centered = y - np.mean(y)
    # 互相关（归一化）
    max_lag = min(n // 2, 300)
    corr = np.correlate(y_centered, u_centered, mode="full")
    mid = len(corr) // 2
    # 只取正滞后部分（因果响应）
    pos_corr = corr[mid : mid + max_lag]
    lags = np.arange(max_lag) * ts
    # 归一化
    norm = np.sum(u_centered**2)
    if norm > 1e-12:
        impulse_response = pos_corr / norm
    else:
        impulse_response = pos_corr * 0.0
    # K 粗估：脉冲响应积分（稳态增益 = 脉冲响应面积）
    gain_est = float(np.sum(impulse_response) * ts)
    # tau + theta 粗估：质心时间
    total = np.sum(impulse_response)
    if abs(total) > 1e-12:
        centroid_lag = float(np.sum(lags * impulse_response) / total)
    else:
        centroid_lag = 0.0
    return NonparametricEstimate(
        gain_estimate=gain_est,
        time_constant_estimate=centroid_lag,
        cross_correlation=impulse_response,
        lags=lags,
        coherence=None,
    )


def welch_spectral_analysis(
    u: np.ndarray,
    y: np.ndarray,
    ts: float = 1.0,
) -> dict:
    """Welch 谱分析估频率响应.

    返回频率响应 Ĝ(jω) = S_uy(ω)/S_uu(ω)。
    """
    nperseg = min(len(u), 256)
    if nperseg < 16:
        return {"frequencies": np.array([]), "gain": np.array([]), "phase": np.array([])}
    f, Puu = signal.welch(u, fs=1.0 / ts, nperseg=nperseg)
    f, Puy = signal.csd(u, y, fs=1.0 / ts, nperseg=nperseg)
    # 频率响应
    G = Puy / np.where(Puu > 1e-12, Puu, 1e-12)
    gain = np.abs(G)
    phase = np.angle(G, deg=True)
    # 相干函数
    f, Pyy = signal.welch(y, fs=1.0 / ts, nperseg=nperseg)
    coherence = np.abs(Puy) ** 2 / np.where(Puu * Pyy > 1e-12, Puu * Pyy, 1e-12)
    return {
        "frequencies": f,
        "gain": gain,
        "phase": phase,
        "coherence": coherence,
    }
