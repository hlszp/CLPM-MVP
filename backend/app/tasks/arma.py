"""ARMA 模型辨识与 Green 函数稳态时间计算。

对齐 GB/T 44693.2-2024 附录 F.4：
    基于 ARMA(p,q) 模型辨识系统单位脉冲响应函数（Green 函数），
    将单位脉冲响应函数稳定在 5% 或 2% 中的时间作为实际稳态时间。

实现方案：
    用高阶 AR(p) 模型近似 ARMA(p,q)（Yule-Walker 方程求解），
    通过 Green 函数递推计算脉冲响应衰减时间。
    避免引入 statsmodels 依赖，仅用 numpy + scipy。
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.linalg import solve_toeplitz, toeplitz

logger = logging.getLogger(__name__)

# 默认参数
DEFAULT_AR_ORDER = 10
DEFAULT_SETTLING_THRESHOLD = 0.05  # Green 函数衰减阈值（5%）
MAX_GREEN_FUNC_LENGTH = 3600  # Green 函数最大长度（1 小时 @ 1Hz）
MIN_DATA_POINTS = 30  # 最少数据点数


def fit_ar_model(signal: np.ndarray, order: int = DEFAULT_AR_ORDER) -> np.ndarray:
    """AR(p) 模型辨识 — Yule-Walker 方程求解。

    AR(p) 模型：x(t) + a₁·x(t-1) + ... + aₚ·x(t-p) = e(t)

    Yule-Walker 方程：R · a = -r
        R = Toeplitz 自相关矩阵 (p×p)
        r = 自相关向量 [r(1), r(2), ..., r(p)]
        a = AR 系数 [a₁, a₂, ..., aₚ]

    Args:
        signal: 输入信号（已去均值）
        order: AR 模型阶数

    Returns:
        AR 系数数组 [a₁, a₂, ..., aₚ]
    """
    n = len(signal)
    if n < order + 1:
        order = max(1, n // 3)

    signal = signal - np.mean(signal)
    autocorr = np.correlate(signal, signal, mode="full")
    autocorr = autocorr[n - 1 :] / n  # r(0), r(1), ..., r(2n-1)

    if autocorr[0] == 0:
        return np.zeros(order)

    r_vector = autocorr[1 : order + 1]
    r_matrix = toeplitz(autocorr[:order])

    try:
        ar_coeffs = solve_toeplitz(r_matrix, -r_vector)
    except np.linalg.LinAlgError:
        ar_coeffs = np.linalg.lstsq(r_matrix, -r_vector, rcond=None)[0]

    return np.asarray(ar_coeffs).flatten()


def compute_green_function(
    ar_coeffs: np.ndarray, length: int = MAX_GREEN_FUNC_LENGTH
) -> np.ndarray:
    """计算 AR(p) 模型的 Green 函数（单位脉冲响应）。

    Green 函数递推公式：
        G(0) = 1
        G(k) = -Σᵢ₌₁ᵖ aᵢ · G(k-i)    for k ≥ 1

    Args:
        ar_coeffs: AR 系数 [a₁, a₂, ..., aₚ]
        length: Green 函数计算长度

    Returns:
        Green 函数序列 [G(0), G(1), ..., G(length-1)]
    """
    p = len(ar_coeffs)
    g = np.zeros(length)
    g[0] = 1.0

    for k in range(1, length):
        s = 0.0
        for i in range(1, min(p, k) + 1):
            s += ar_coeffs[i - 1] * g[k - i]
        g[k] = -s

    return g


def compute_settling_time(
    signal: np.ndarray,
    sample_interval_sec: float = 1.0,
    threshold: float = DEFAULT_SETTLING_THRESHOLD,
    ar_order: int = DEFAULT_AR_ORDER,
) -> float:
    """计算实际稳态时间 — GB/T 44693.2-2024 附录 F.4。

    算法流程：
        1. AR(p) 模型辨识（Yule-Walker）
        2. Green 函数递推
        3. 找到 |G(k)| 首次持续低于 threshold 的时刻
        4. 实际稳态时间 = k × 采样周期

    Args:
        signal: PV 偏差序列（已去均值）
        sample_interval_sec: 采样周期（秒）
        threshold: 衰减阈值（5% 或 2%）
        ar_order: AR 模型阶数

    Returns:
        实际稳态时间（秒），辨识失败返回 0
    """
    n = len(signal)
    if n < MIN_DATA_POINTS:
        logger.warning("[ARMA] 数据点不足（%d < %d），无法辨识稳态时间", n, MIN_DATA_POINTS)
        return 0.0

    signal = np.asarray(signal, dtype=float)
    signal = signal - np.mean(signal)

    std = np.std(signal)
    if std < 1e-9:
        logger.debug("[ARMA] 信号恒定，稳态时间为 0（已处于稳态）")
        return 0.0

    # 步骤 1：AR(p) 模型辨识
    ar_coeffs = fit_ar_model(signal, order=ar_order)
    logger.debug("[ARMA] AR(%d) 系数: %s", ar_order, np.round(ar_coeffs, 4))

    # 步骤 2：Green 函数递推
    green_func = compute_green_function(ar_coeffs, length=MAX_GREEN_FUNC_LENGTH)

    # 归一化（G(0) = 1）
    if abs(green_func[0]) > 0:
        green_func = green_func / green_func[0]

    # 发散检测：若 Green 函数幅值发散（不稳定模型），浮点累积误差会导致稳态时间计算错误
    # 此时直接返回 0，由调用方兜底处理（actual_settling <= 0 → 快速率返回 100）
    max_abs_green = float(np.max(np.abs(green_func))) if len(green_func) > 0 else 0.0
    if max_abs_green > 1e6:
        logger.warning(
            "[ARMA] Green 函数发散（max|G|=%.2e），模型不稳定，稳态时间返回 0",
            max_abs_green,
        )
        return 0.0

    # 步骤 3：找到 |G(k)| 首次持续低于 threshold 的时刻
    n_consecutive = max(3, int(10 / sample_interval_sec))
    abs_green = np.abs(green_func)

    settling_index = 0
    consecutive_count = 0
    for k in range(1, len(abs_green)):
        if abs_green[k] < threshold:
            consecutive_count += 1
            if consecutive_count >= n_consecutive:
                settling_index = k - n_consecutive + 1
                break
        else:
            consecutive_count = 0

    # 显式转换为 Python float，避免 numpy float64 在后续 Decimal(str(...)) 转换中
    # 引入多余精度（numpy float64 的 str 表示可能与 Python float 不同）
    settling_time = float(settling_index * sample_interval_sec)

    if settling_index == 0:
        logger.debug(
            "[ARMA] Green 函数未收敛到阈值 %.0f%%（可能为不稳定系统或数据噪声过大），"
            "稳态时间返回 0",
            threshold * 100,
        )
    else:
        logger.debug(
            "[ARMA] Green 函数稳态时间 = %.1f 秒（阈值 %.0f%%，连续 %d 点）",
            settling_time,
            threshold * 100,
            n_consecutive,
        )

    return settling_time


def compute_ideal_settling_time(
    pv_range: float,
    control_type: str = "STABLE",
) -> float:
    """计算理想稳态时间 — 基于控制类型的经验基准。

    | 控制类型 | 理想稳态时间 | 适用场景 |
    |---------|------------|---------|
    | STABLE  | 60 秒      | 温度、压力控制 |
    | SLOW    | 120 秒     | 缓慢调节回路 |
    | FAST    | 10 秒      | 副回路、流量控制 |
    | LOGIC   | 30 秒      | 逻辑控制 |
    """
    ideal_times = {
        "STABLE": 60.0,
        "SLOW": 120.0,
        "FAST": 10.0,
        "LOGIC": 30.0,
    }
    return ideal_times.get(control_type, 60.0)
