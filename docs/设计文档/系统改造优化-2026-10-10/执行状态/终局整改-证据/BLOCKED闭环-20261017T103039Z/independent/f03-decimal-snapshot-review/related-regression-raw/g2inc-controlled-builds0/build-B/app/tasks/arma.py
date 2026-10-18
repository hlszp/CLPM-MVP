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

# ---------------------------------------------------------------------------
# P3-09b（EA-D03 采纳口径）：持续尾段判稳与最短支持条件
#
# 旧判据（convergence_rule="window"，legacy 保留）：首个连续
# ``max(3, 10/dt)`` 点低阈窗口即判稳——振荡衰减过程在正弦过零附近会产生
# 短暂低阈窗口（C07-EA2：k≈2393 处 11 点低阈窗），旧判据提前判稳。
#
# 新判据（sustained_tail=True，CANDIDATE_V3_RC1 profile）：
#   1. 从后向前扫描 O(G) 找最后一个 |G(k)| ≥ 阈值 的点，候选稳态点 =
#      该点 + 1——候选到推演时域结束**全部**低于阈值（10§3.3"从候选点到
#      当前有限模型推演时域结束全部低于阈值"）；
#   2. 尾段最短支持 = max(3×dt, 10s)；
#   3. 模型复极点给出可信振荡周期 P（所有被采用复极点对应周期的最大值）
#      时，尾段还须覆盖至少一个 P；近实根（角度过小）与超出推演时域的
#      周期不凭空生成周期条件（10§3.3"近实根/不可解析周期不凭空生成"）；
#   4. 尾段不足 → INSUFFICIENT_TAIL_SUPPORT：报告候选进入带时刻与尾段
#      支持信息，T 点估计仍为 null（不通过加长观察窗绕过预算）；
#   5. 真实观测（observation_seconds）与模型推演（simulation_horizon_
#      seconds）分开报告——不把模型外推写成真实观测，只声称"有限推演
#      时域内持续收敛"，不声称无限未来保证。
# ---------------------------------------------------------------------------
#: 尾段最短持续时间下限（秒，与 dt 无关的绝对下限；EA-D03 采纳口径
#: "最短持续条件为 max(3×dt,10秒)"）
MIN_TAIL_SUPPORT_SECONDS = 10.0
#: 尾段最短持续时间的 dt 倍数（max(3×dt, 10s) 的 3×dt 分量）
MIN_TAIL_SUPPORT_DT_MULTIPLE = 3.0
#: 可信极点振荡周期的角度下限（rad）：小于此角=近实根，不生成周期条件；
#: 2π/3600 ↔ 周期上限=Green 推演长度（超出推演时域的周期不可解析）
MIN_CREDIBLE_POLE_ANGLE = 2.0 * np.pi / MAX_GREEN_FUNC_LENGTH


class SettlingStatus(StrEnum):
    """稳态时间计算状态（P0-1：区分三种边界语义）.

    - SETTLED：Green 函数在窗口内衰减到阈值以下，稳态时间有效
    - ALREADY_STABLE：信号恒定，已处于稳态（稳态时间 = 0）
    - NEVER_SETTLES：Green 函数稳定但窗口内不衰减（持续振荡/近单位根）
    - IDENTIFICATION_FAILED：数据不足或所有尝试阶数的 Green 函数均发散
    - INSUFFICIENT_TAIL_SUPPORT：候选进入带但尾段不足最短支持条件
      （P3-09b 新增，仅 sustained_tail 判据产生；legacy window 判据不产生）
    """

    SETTLED = "settled"
    ALREADY_STABLE = "already_stable"
    NEVER_SETTLES = "never_settles"
    IDENTIFICATION_FAILED = "identification_failed"
    INSUFFICIENT_TAIL_SUPPORT = "insufficient_tail_support"


@dataclass(frozen=True)
class SettlingTimeResult:
    """compute_settling_time_detailed 的结构化结果.

    Attributes:
        status: 计算状态（区分 已稳态/不衰减/辨识失败/尾段不足）
        value: 实际稳态时间（秒）；仅 SETTLED/ALREADY_STABLE 有值，
            NEVER_SETTLES/IDENTIFICATION_FAILED/INSUFFICIENT_TAIL_SUPPORT
            为 None
        window_length_sec: Green 函数窗口长度（秒）=
            MAX_GREEN_FUNC_LENGTH × 采样周期，NEVER_SETTLES 时作为
            快速率计算的 actual_t 下界代入指数衰减公式
        candidate_seconds: 候选进入带时刻（秒，sustained_tail 判据）；
            尾段不足时仍报告（10§3.3"报告候选进入带时刻"）
        tail_seconds: 候选到推演结束的尾段时长（秒，sustained_tail 判据）
        required_tail_seconds: 尾段最短支持要求（秒；含极点周期条件时为
            max(3×dt, 10s, P)）
        pole_period_seconds: 模型复极点可信振荡周期（秒；被采用复极点对应
            周期的最大值；无可信复极点时 None=不生成周期条件）
        observation_seconds: 真实输入观测时长（秒，n×dt）——与模型推演
            时域分开（P3-09b：真实观测与模型推演分开报告）
    """

    status: SettlingStatus
    value: float | None
    window_length_sec: float
    candidate_seconds: float | None = None
    tail_seconds: float | None = None
    required_tail_seconds: float | None = None
    pole_period_seconds: float | None = None
    observation_seconds: float | None = None

    @property
    def simulation_horizon_seconds(self) -> float:
        """模型推演时域长度（秒）= window_length_sec（Green 窗口 × dt）。"""
        return self.window_length_sec


def _autocorr_bounded(signal: np.ndarray, max_lag: int) -> np.ndarray:
    """有界 lag 自相关 r(0..max_lag)（P5-01：替代 full 相关平方成本）.

    ``c[k] = Σᵢ signal[i+k]·signal[i]``（顺序累加，np.dot），与
    ``np.correlate(signal, signal, mode="full")[n-1:] / n`` 的前
    ``max_lag+1`` 项逐位一致（等价证据：tests/test_p5_01_shared_features.py）。
    Yule-Walker 只消费 ``r(0..order)``（order ≤ 10），full 相关计算了
    2n-1 个 lag 后丢弃 99% 以上——O(n²) → O(n·max_lag)。

    Args:
        signal: 输入信号（调用方保证已按 fit_ar_model 口径去均值）
        max_lag: 最大滞后（含）

    Returns:
        归一化自相关序列 [r(0), r(1), ..., r(max_lag)]（÷n）
    """
    n = len(signal)
    out = np.empty(max_lag + 1)
    for k in range(max_lag + 1):
        if k:
            out[k] = np.dot(signal[k:], signal[: n - k])
        else:
            out[k] = np.dot(signal, signal)
    return out / n


def fit_ar_model(
    signal: np.ndarray,
    order: int = DEFAULT_AR_ORDER,
    autocorr: np.ndarray | None = None,
) -> np.ndarray:
    """AR(p) 模型辨识 — Yule-Walker 方程求解。

    AR(p) 模型：x(t) + a₁·x(t-1) + ... + aₚ·x(t-p) = e(t)

    Yule-Walker 方程：R · a = -r
        R = Toeplitz 自相关矩阵 (p×p)
        r = 自相关向量 [r(1), r(2), ..., r(p)]
        a = AR 系数 [a₁, a₂, ..., aₚ]

    Args:
        signal: 输入信号（已去均值）
        order: AR 模型阶数
        autocorr: 预计算的有界 lag 自相关 r(0..≥order)（P5-01 AR 重试
            复用——同一信号多阶重试共享一次计算）；None 或长度不足时
            内部按有界 lag 直接计算（与 full 相关逐位一致）

    Returns:
        AR 系数数组 [a₁, a₂, ..., aₚ]
    """
    n = len(signal)
    if n < order + 1:
        order = max(1, n // 3)

    signal = signal - np.mean(signal)
    if autocorr is None or len(autocorr) < order + 1:
        autocorr = _autocorr_bounded(signal, order)

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


def _credible_pole_period_seconds(
    ar_coeffs: np.ndarray, sample_interval_sec: float
) -> float | None:
    """被采用复极点的可信振荡周期（秒；P3-09b）.

    取特征方程复根共轭对（|imag|>0）中角度在 [MIN_CREDIBLE_POLE_ANGLE,
    π/2] 内的最大周期：周期 = 2π/ω × dt。近实根（角度小于下限）与超出
    Green 推演时域的周期（角度下限保证周期 ≤ 3600 样本）不生成周期条件
    （10§3.3"近实根/不可解析周期不凭空生成周期条件"）；实根系统返回 None。
    """
    p = len(ar_coeffs)
    if p < 2 or sample_interval_sec <= 0:
        return None
    poly_coeffs = np.concatenate([[1.0], np.asarray(ar_coeffs, dtype=float)])
    roots = np.roots(poly_coeffs)
    max_period = None
    for r in roots:
        if abs(r.imag) <= 0.0:
            continue
        angle = abs(np.arctan2(r.imag, r.real))
        # 共轭对两根角度同值，天然只计一次
        if angle < MIN_CREDIBLE_POLE_ANGLE or angle > np.pi / 2.0 + 1e-12:
            continue
        period_s = (2.0 * np.pi / angle) * sample_interval_sec
        if max_period is None or period_s > max_period:
            max_period = period_s
    return max_period


def first_window_below_index(abs_green: np.ndarray, threshold: float, n_consecutive: int) -> int:
    """legacy window 判据（纯函数）：首个连续 n_consecutive 点低阈窗口起点.

    无命中返回 0（与旧内联实现一致：settling_index 初值 0 → never_settles）。
    """
    below = abs_green < threshold
    if len(below) < n_consecutive:
        return 0
    ones = np.ones(n_consecutive, dtype=int)
    window_sums = np.convolve(below.astype(int), ones, mode="valid")
    valid_starts = np.where(window_sums == n_consecutive)[0]
    return int(valid_starts[0]) if len(valid_starts) > 0 else 0


def sustained_tail_verdict(
    abs_green: np.ndarray,
    threshold: float,
    sample_interval_sec: float,
    pole_period_seconds: float | None = None,
) -> tuple[str, int, float, float, float]:
    """持续尾段判据（纯函数，P3-09b；C07-EA2 内核独立参考层）.

    从后向前 O(G) 扫描找最后一个 |G(k)| ≥ 阈值点，候选 = 其后首个点
    （候选到推演终点全部低阈）。三态裁决（边界登记：尾段连基础最短支持
    ``max(3×dt, 10s)`` 都不足 = 无法确立任何持续低阈段，与持续振荡不可
    区分 → never；达到基础支持但不足极点周期要求 → insufficient）：

    - ``"never"``：推演时域内不衰减（持续振荡/近单位根）——T 下界=推演
      时域长度，不是已测得的 T；
    - ``"insufficient"``：候选已进带但尾段不足 ``max(3×dt, 10s, P)``；
    - ``"settled"``：尾段满足全部最短支持条件，T = 候选时刻。

    Returns:
        (verdict, candidate_index, tail_seconds, base_required_seconds,
         total_required_seconds)
    """
    above = abs_green >= threshold
    # G(0)=1 ≥ 阈值恒成立（归一化前提），last_above 必存在；空数组防御
    last_above = int(len(above) - 1 - np.argmax(above[::-1])) if len(above) else -1
    candidate_index = last_above + 1
    tail_seconds = float((len(abs_green) - candidate_index) * sample_interval_sec)
    base_required = max(
        MIN_TAIL_SUPPORT_DT_MULTIPLE * sample_interval_sec,
        MIN_TAIL_SUPPORT_SECONDS,
    )
    total_required = base_required
    if pole_period_seconds is not None:
        total_required = max(total_required, pole_period_seconds)
    if tail_seconds + 1e-9 < base_required:
        verdict = "never"
    elif tail_seconds + 1e-9 < total_required:
        verdict = "insufficient"
    else:
        verdict = "settled"
    return (verdict, candidate_index, tail_seconds, base_required, total_required)


def compute_settling_time_detailed(
    signal: np.ndarray,
    sample_interval_sec: float = 1.0,
    threshold: float = DEFAULT_SETTLING_THRESHOLD,
    ar_order: int = DEFAULT_AR_ORDER,
    sustained_tail: bool = False,
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
        - INSUFFICIENT_TAIL_SUPPORT（仅 sustained_tail=True）：候选进入带
          但尾段不足最短支持条件（value=None，candidate_seconds 仍报告）

    Args:
        signal: PV 偏差序列（已去均值）
        sample_interval_sec: 采样周期（秒）
        threshold: 衰减阈值（5% 或 2%）
        ar_order: AR 模型阶数
        sustained_tail: P3-09b 持续尾段判据（CANDIDATE_V3_RC1 profile）；
            False=legacy 首个低阈窗判据（golden 基线，行为不变）

    Returns:
        SettlingTimeResult（status + value + window_length_sec + 尾段/观测字段）
    """
    window_length_sec = float(MAX_GREEN_FUNC_LENGTH * sample_interval_sec)
    observation_seconds = float(len(signal) * sample_interval_sec)
    n = len(signal)
    if n < MIN_DATA_POINTS:
        logger.warning("[ARMA] 数据点不足（%d < %d），无法辨识稳态时间", n, MIN_DATA_POINTS)
        return SettlingTimeResult(
            status=SettlingStatus.IDENTIFICATION_FAILED,
            value=None,
            window_length_sec=window_length_sec,
            observation_seconds=observation_seconds,
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
            observation_seconds=observation_seconds,
        )

    # 步骤 1-2：AR(p) 辨识 + Green 函数递推
    # P2 #34 偏差4：默认 ar_order=2（对齐设计文档 ARMA(2,1)），但 AR(2) 对接近单位根的
    # 慢速响应信号可能不稳定（Green 函数发散）。此时自动升级阶数重试：
    # [ar_order, 4, 6, 10]，首个稳定的阶数胜出。ARMA(2,1) 的 MA(1) 部分能更好
    # 表示近单位根过程，当前 AR 近似丢失 MA 部分，阶数升级作为工程回退。
    retry_orders = sorted({ar_order, 4, 6, 10})
    # P5-01（AR 重试复用 lag 特征）：重试链共享一次有界 lag 自相关
    # r(0..max_order)，各阶 Yule-Walker 只取前缀切片——与逐阶重算
    # full 自相关逐位一致（等价证据：tests/test_p5_01_shared_features.py
    # TestSettlingDetailedEquivalence）。fit_ar_model 内部会对传入信号
    # 再减一次均值，预计算复刻同一操作序列（二次均值为浮点残差，
    # 双方口径相同）以保证逐位一致。
    if retry_orders:
        shared_signal = signal - np.mean(signal)
        shared_autocorr = _autocorr_bounded(shared_signal, retry_orders[-1])
    else:  # pragma: no cover — retry_orders 至少含 ar_order，防御分支
        shared_autocorr = None
    ar_coeffs = None
    green_func = None
    for try_order in retry_orders:
        if try_order > n // 3:
            continue  # 数据点不足以支撑该阶数
        coeffs = fit_ar_model(signal, order=try_order, autocorr=shared_autocorr)
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
            observation_seconds=observation_seconds,
        )

    abs_green = np.abs(green_func)

    # P3-09b 持续尾段判据：候选到推演结束全程低阈 + 最短支持条件
    if sustained_tail:
        pole_period = _credible_pole_period_seconds(ar_coeffs, sample_interval_sec)
        verdict, candidate_index, tail_seconds, _base_req, required = sustained_tail_verdict(
            abs_green, threshold, sample_interval_sec, pole_period
        )
        candidate_seconds = float(candidate_index * sample_interval_sec)
        if verdict == "never":
            # 推演时域内不衰减（持续振荡/近单位根）：T 下界=推演时域长度
            logger.info(
                "[ARMA] 持续尾段判稳：尾段 %.1fs 不足基础最短支持（持续振荡/近单位根），"
                "标记为 never_settles",
                tail_seconds,
            )
            return SettlingTimeResult(
                status=SettlingStatus.NEVER_SETTLES,
                value=None,
                window_length_sec=window_length_sec,
                candidate_seconds=candidate_seconds,
                tail_seconds=tail_seconds,
                required_tail_seconds=required,
                pole_period_seconds=pole_period,
                observation_seconds=observation_seconds,
            )
        if verdict == "insufficient":
            logger.info(
                "[ARMA] 持续尾段判稳：候选进入带 %.1fs，尾段 %.1fs < 最短支持 %.1fs"
                "（极点周期 %ss）→ INSUFFICIENT_TAIL_SUPPORT，T=null",
                candidate_seconds,
                tail_seconds,
                required,
                f"{pole_period:.1f}" if pole_period is not None else "N/A",
            )
            return SettlingTimeResult(
                status=SettlingStatus.INSUFFICIENT_TAIL_SUPPORT,
                value=None,
                window_length_sec=window_length_sec,
                candidate_seconds=candidate_seconds,
                tail_seconds=tail_seconds,
                required_tail_seconds=required,
                pole_period_seconds=pole_period,
                observation_seconds=observation_seconds,
            )

        logger.debug(
            "[ARMA] 持续尾段判稳：候选 %.1fs，尾段 %.1fs ≥ 最短支持 %.1fs",
            candidate_seconds,
            tail_seconds,
            required,
        )
        return SettlingTimeResult(
            status=SettlingStatus.SETTLED,
            value=candidate_seconds,
            window_length_sec=window_length_sec,
            candidate_seconds=candidate_seconds,
            tail_seconds=tail_seconds,
            required_tail_seconds=required,
            pole_period_seconds=pole_period,
            observation_seconds=observation_seconds,
        )

    # 步骤 3（legacy window 判据）：找到 |G(k)| 首次持续低于 threshold 的时刻
    n_consecutive = max(3, int(10 / sample_interval_sec))
    settling_index = first_window_below_index(abs_green, threshold, n_consecutive)

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
            observation_seconds=observation_seconds,
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
        observation_seconds=observation_seconds,
    )


def compute_settling_time(
    signal: np.ndarray,
    sample_interval_sec: float = 1.0,
    threshold: float = DEFAULT_SETTLING_THRESHOLD,
    ar_order: int = DEFAULT_AR_ORDER,
    sustained_tail: bool = False,
) -> float:
    """计算实际稳态时间（数值兼容包装） — GB/T 44693.2-2024 附录 F.4。

    向后兼容的历史接口：仅返回数值，无法区分 已稳态/不衰减/辨识失败/尾段不足，
    失败、不衰减与尾段不足均返回 0.0。新代码请使用 compute_settling_time_detailed
    获取 SettlingStatus 语义（P0-1/P3-09b）。

    Returns:
        实际稳态时间（秒），不衰减、尾段不足或辨识失败返回 0.0
    """
    result = compute_settling_time_detailed(
        signal=signal,
        sample_interval_sec=sample_interval_sec,
        threshold=threshold,
        ar_order=ar_order,
        sustained_tail=sustained_tail,
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
