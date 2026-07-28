"""ARMA 模型辨识与 Green 函数稳态时间计算。

对齐 GB/T 44693.2-2024 附录 F.4 与设计文档 §4.5.2：
    基于 ARMA(p,q) 模型辨识系统单位脉冲响应函数（Green 函数），
    将单位脉冲响应函数稳定在 5% 或 2% 中的时间作为实际稳态时间。

实现方案：
    用 AR(p) 模型近似 ARMA(p,q)（Yule-Walker 方程求解），
    通过 Green 函数递推计算脉冲响应衰减时间。
    避免引入 statsmodels 依赖，仅用 numpy + scipy。

P2 #34 偏差4 修正：默认 AR 阶数从 10 降至 2，对齐设计文档 §4.5.3
输入输出规范 `arma_order` 默认 (2,1) 的 p=2。AR(2) 是 ARMA(2,1) 的
AR 近似（未实现 MA(1) 部分），避免高阶 AR(10) 在有限数据点上过拟合。
`DEFAULT_MA_ORDER = 1` 常量记录设计文档要求的 q=1，供未来实现真正
ARMA(2,1) 辨识（如 Hannan-Rissanen 两步法）时使用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy.linalg import solve_toeplitz, toeplitz

logger = logging.getLogger(__name__)

# 默认参数（对齐设计文档 §4.5.3 arma_order 默认 (2,1)）
DEFAULT_AR_ORDER = 2  # p=2，对齐 ARMA(2,1) 的自回归阶数
DEFAULT_MA_ORDER = 1  # q=1，设计文档要求（当前 AR 近似未实现 MA 部分，保留常量记录设计意图）
DEFAULT_SETTLING_THRESHOLD = 0.05  # Green 函数衰减阈值（5%）
MAX_GREEN_FUNC_LENGTH = 3600  # Green 函数最大长度（1 小时 @ 1Hz）
MIN_DATA_POINTS = 30  # 最少数据点数


class SettlingStatus(StrEnum):
    """稳态时间计算状态（P0-1：区分三种边界语义）.

    - SETTLED：Green 函数在窗口内衰减到阈值以下，稳态时间有效
    - ALREADY_STABLE：信号恒定，已处于稳态（稳态时间 = 0）
    - NEVER_SETTLES：Green 函数稳定但窗口内不衰减（持续振荡/近单位根）
    - IDENTIFICATION_FAILED：数据不足或所有尝试阶数的 Green 函数均发散
    """

    SETTLED = "settled"
    ALREADY_STABLE = "already_stable"
    NEVER_SETTLES = "never_settles"
    IDENTIFICATION_FAILED = "identification_failed"


@dataclass(frozen=True)
class SettlingTimeResult:
    """compute_settling_time_detailed 的结构化结果.

    Attributes:
        status: 计算状态（区分 已稳态/不衰减/辨识失败）
        value: 实际稳态时间（秒）；仅 SETTLED/ALREADY_STABLE 有值，
            NEVER_SETTLES/IDENTIFICATION_FAILED 为 None
        window_length_sec: Green 函数窗口长度（秒）=
            MAX_GREEN_FUNC_LENGTH × 采样周期，NEVER_SETTLES 时作为
            快速率计算的 actual_t 下界代入指数衰减公式
    """

    status: SettlingStatus
    value: float | None
    window_length_sec: float


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
    # P2 #34: 直接传 1D 第一列给 solve_toeplitz（scipy 内部用 Levinson 递归），
    # 避免传入 2D toeplitz 矩阵导致返回 2D 结果（order=2 时曾返回 2x2=4 个系数）
    first_column = autocorr[:order]

    try:
        ar_coeffs = solve_toeplitz(first_column, -r_vector)
    except np.linalg.LinAlgError:
        r_matrix = toeplitz(first_column)
        ar_coeffs = np.linalg.lstsq(r_matrix, -r_vector, rcond=None)[0]

    return np.asarray(ar_coeffs).flatten()


def compute_green_function(
    ar_coeffs: np.ndarray, length: int = MAX_GREEN_FUNC_LENGTH
) -> np.ndarray:
    """计算 AR(p) 模型的 Green 函数（单位脉冲响应）— 解析解 + 递推回退.

    优先使用特征根解析解（O(length)），避免双重循环。
    解析解失败时（重根/数值不稳定）回退到递推。

    Green 函数递推公式：
        G(0) = 1
        G(k) = -Σᵢ₌₁ᵖ aᵢ · G(k-i)    for k ≥ 1

    解析解（基于特征方程根）：
        特征方程：z^p + a₁·z^(p-1) + ... + aₚ = 0
        根 r₁, r₂, ..., rₚ（可含复根，共轭成对）
        G(k) = Σᵢ cᵢ · rᵢᵏ
        系数 cᵢ 由初始条件 G(0)=1, G(k<0)=0 确定

    Args:
        ar_coeffs: AR 系数 [a₁, a₂, ..., aₚ]
        length: Green 函数计算长度

    Returns:
        Green 函数序列 [G(0), G(1), ..., G(length-1)]
    """
    p = len(ar_coeffs)
    if p == 0:
        g = np.zeros(length)
        g[0] = 1.0
        return g

    # 尝试解析解
    g = _green_function_analytic(ar_coeffs, length)
    if g is not None:
        return g

    # 回退到递推
    return _green_function_recursive(ar_coeffs, length)


def _green_function_analytic(ar_coeffs: np.ndarray, length: int) -> np.ndarray | None:
    """Green 函数解析解（基于特征根）.

    对 AR(p) 模型，特征方程为：z^p + a₁·z^(p-1) + ... + aₚ = 0
    注意：AR 模型 x(t) + a₁·x(t-1) + ... + aₚ·x(t-p) = e(t) 的
    特征多项式是 z^p + a₁·z^(p-1) + ... + aₚ，对应 np.roots([1, a₁, ..., aₚ])。

    Green 函数：G(k) = Σᵢ cᵢ · rᵢᵏ
    初始条件：G(0) = 1, G(-1) = G(-2) = ... = 0

    对于重根，解析解形式不同，此处返回 None 触发回退。
    """
    p = len(ar_coeffs)
    # 特征多项式系数：[1, a₁, a₂, ..., aₚ]
    poly_coeffs = np.concatenate([[1.0], ar_coeffs])
    roots = np.roots(poly_coeffs)

    # 检查是否有重根（重根需要不同形式的解析解）
    if len(roots) != len(set(np.round(roots, decimals=10))):
        return None

    # 求解系数 cᵢ：G(k) = Σ cᵢ · rᵢᵏ
    # 初始条件：G(0) = 1, G(1) = -a₁, G(2) = -a₁·G(1) - a₂·G(0), ...
    # 前 p 个 G 值确定 p 个系数
    # G(0) = 1
    # G(k) = -Σᵢ₌₁ᵏ aᵢ · G(k-i)  for 1 ≤ k < p
    g_init = np.zeros(p, dtype=complex)
    g_init[0] = 1.0
    for k in range(1, p):
        s = 0.0
        for i in range(1, k + 1):
            s += float(ar_coeffs[i - 1]) * g_init[k - i]
        g_init[k] = -s

    # 构造 Vandermonde 矩阵：V[i][j] = roots[j]^i
    vander = np.vander(roots, p, increasing=True).T
    try:
        coeffs = np.linalg.solve(vander, g_init)
    except np.linalg.LinAlgError:
        return None

    # 计算 Green 函数：G(k) = Σ cᵢ · rᵢᵏ
    # 向量化：构造 power 矩阵 [length × p]
    k_arr = np.arange(length)
    # power_matrix[k, i] = roots[i]^k
    power_matrix = np.power(roots[np.newaxis, :], k_arr[:, np.newaxis])
    g = np.real(np.sum(coeffs[np.newaxis, :] * power_matrix, axis=1))
    return g


def _green_function_recursive(ar_coeffs: np.ndarray, length: int) -> np.ndarray:
    """Green 函数递推（回退方案，双重循环）."""
    p = len(ar_coeffs)
    g = np.zeros(length)
    g[0] = 1.0

    for k in range(1, length):
        s = 0.0
        for i in range(1, min(p, k) + 1):
            s += ar_coeffs[i - 1] * g[k - i]
        g[k] = -s

    return g


def compute_settling_time_detailed(
    signal: np.ndarray,
    sample_interval_sec: float = 1.0,
    threshold: float = DEFAULT_SETTLING_THRESHOLD,
    ar_order: int = DEFAULT_AR_ORDER,
) -> SettlingTimeResult:
    """计算实际稳态时间（结构化结果） — GB/T 44693.2-2024 附录 F.4。

    算法流程：
        1. AR(p) 模型辨识（Yule-Walker）
        2. Green 函数递推
        3. 找到 |G(k)| 首次持续低于 threshold 的时刻
        4. 实际稳态时间 = k × 采样周期

    P0-1：不再用 0.0 混淆三种边界语义，通过 SettlingTimeResult.status 区分：
        - ALREADY_STABLE：信号恒定，真已稳态（value=0.0）
        - NEVER_SETTLES：Green 函数稳定但窗口内不衰减，持续振荡/近单位根
          回路（value=None，调用方应以 window_length_sec 作为稳态时间下界）
        - IDENTIFICATION_FAILED：数据不足或所有阶数辨识发散（value=None）

    Args:
        signal: PV 偏差序列（已去均值）
        sample_interval_sec: 采样周期（秒）
        threshold: 衰减阈值（5% 或 2%）
        ar_order: AR 模型阶数

    Returns:
        SettlingTimeResult（status + value + window_length_sec）
    """
    window_length_sec = float(MAX_GREEN_FUNC_LENGTH * sample_interval_sec)
    n = len(signal)
    if n < MIN_DATA_POINTS:
        logger.warning("[ARMA] 数据点不足（%d < %d），无法辨识稳态时间", n, MIN_DATA_POINTS)
        return SettlingTimeResult(
            status=SettlingStatus.IDENTIFICATION_FAILED,
            value=None,
            window_length_sec=window_length_sec,
        )

    signal = np.asarray(signal, dtype=float)
    signal = signal - np.mean(signal)

    std = np.std(signal)
    if std < 1e-9:
        logger.debug("[ARMA] 信号恒定，稳态时间为 0（已处于稳态）")
        return SettlingTimeResult(
            status=SettlingStatus.ALREADY_STABLE,
            value=0.0,
            window_length_sec=window_length_sec,
        )

    # 步骤 1-2：AR(p) 辨识 + Green 函数递推
    # P2 #34 偏差4：默认 ar_order=2（对齐设计文档 ARMA(2,1)），但 AR(2) 对接近单位根的
    # 慢速响应信号可能不稳定（Green 函数发散）。此时自动升级阶数重试：
    # [ar_order, 4, 6, 10]，首个稳定的阶数胜出。ARMA(2,1) 的 MA(1) 部分能更好
    # 表示近单位根过程，当前 AR 近似丢失 MA 部分，阶数升级作为工程回退。
    retry_orders = sorted({ar_order, 4, 6, 10})
    ar_coeffs = None
    green_func = None
    for try_order in retry_orders:
        if try_order > n // 3:
            continue  # 数据点不足以支撑该阶数
        coeffs = fit_ar_model(signal, order=try_order)
        # 抑制发散时的浮点溢出警告（发散检测会跳过该阶数）
        with np.errstate(over="ignore", invalid="ignore"):
            g = compute_green_function(coeffs, length=MAX_GREEN_FUNC_LENGTH)
        # 归一化（G(0) = 1）
        if abs(g[0]) > 0:
            g = g / g[0]
        # 检测发散（含 NaN/Inf）
        if not np.all(np.isfinite(g)):
            logger.debug("[ARMA] AR(%d) Green 函数含 NaN/Inf（发散），尝试更高阶", try_order)
            continue
        max_abs_g = float(np.max(np.abs(g)))
        if max_abs_g <= 1e6:
            ar_coeffs = coeffs
            green_func = g
            logger.debug(
                "[ARMA] AR(%d) 稳定（max|G|=%.2e），系数: %s",
                try_order,
                max_abs_g,
                np.round(coeffs, 4),
            )
            break
        logger.debug(
            "[ARMA] AR(%d) Green 函数发散（max|G|=%.2e），尝试更高阶",
            try_order,
            max_abs_g,
        )

    if ar_coeffs is None or green_func is None:
        logger.warning(
            "[ARMA] 所有尝试阶数 %s 的 Green 函数均发散，模型不稳定，辨识失败",
            retry_orders,
        )
        return SettlingTimeResult(
            status=SettlingStatus.IDENTIFICATION_FAILED,
            value=None,
            window_length_sec=window_length_sec,
        )

    # 步骤 3：找到 |G(k)| 首次持续低于 threshold 的时刻 — 向量化
    n_consecutive = max(3, int(10 / sample_interval_sec))
    abs_green = np.abs(green_func)

    # 向量化：标记低于阈值的点，查找连续 n_consecutive 个 True 的起始位置
    below = abs_green < threshold
    settling_index = 0
    if len(below) >= n_consecutive:
        # 使用滑动窗口和：窗口内全部为 True 则和 = n_consecutive
        ones = np.ones(n_consecutive, dtype=int)
        window_sums = np.convolve(below.astype(int), ones, mode="valid")
        # 找到第一个和为 n_consecutive 的窗口
        valid_starts = np.where(window_sums == n_consecutive)[0]
        if len(valid_starts) > 0:
            settling_index = int(valid_starts[0])

    # 显式转换为 Python float，避免 numpy float64 在后续 Decimal(str(...)) 转换中
    # 引入多余精度（numpy float64 的 str 表示可能与 Python float 不同）
    settling_time = float(settling_index * sample_interval_sec)

    if settling_index == 0:
        logger.info(
            "[ARMA] Green 函数在窗口 %.0f 秒内未收敛到阈值 %.0f%%（持续振荡或近单位根），"
            "标记为 never_settles",
            window_length_sec,
            threshold * 100,
        )
        return SettlingTimeResult(
            status=SettlingStatus.NEVER_SETTLES,
            value=None,
            window_length_sec=window_length_sec,
        )

    logger.debug(
        "[ARMA] Green 函数稳态时间 = %.1f 秒（阈值 %.0f%%，连续 %d 点）",
        settling_time,
        threshold * 100,
        n_consecutive,
    )
    return SettlingTimeResult(
        status=SettlingStatus.SETTLED,
        value=settling_time,
        window_length_sec=window_length_sec,
    )


def compute_settling_time(
    signal: np.ndarray,
    sample_interval_sec: float = 1.0,
    threshold: float = DEFAULT_SETTLING_THRESHOLD,
    ar_order: int = DEFAULT_AR_ORDER,
) -> float:
    """计算实际稳态时间（数值兼容包装） — GB/T 44693.2-2024 附录 F.4。

    向后兼容的历史接口：仅返回数值，无法区分 已稳态/不衰减/辨识失败，
    失败与不衰减均返回 0.0。新代码请使用 compute_settling_time_detailed
    获取 SettlingStatus 语义（P0-1）。

    Returns:
        实际稳态时间（秒），不衰减或辨识失败返回 0.0
    """
    result = compute_settling_time_detailed(
        signal=signal,
        sample_interval_sec=sample_interval_sec,
        threshold=threshold,
        ar_order=ar_order,
    )
    return result.value if result.value is not None else 0.0


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
