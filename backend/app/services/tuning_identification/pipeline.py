"""算法栈编排（层 1→6 串联）.

入口函数 identify_from_history：接收 OP/PV/SP/MODE 时序，
执行激励检测→非参数粗估→参数化辨识→阶次选择→离散转换→可信度评估，
返回 IdentificationResult。
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any

import numpy as np

from app.services.tuning_identification.armax import identify_armax
from app.services.tuning_identification.arx import identify_arx
from app.services.tuning_identification.discrete_to_continuous import (
    arx_to_fopdt,
    arx_to_sopdt,
)
from app.services.tuning_identification.excitation import (
    check_excitation,
    excitation_score,
)
from app.services.tuning_identification.iv import identify_clivc4
from app.services.tuning_identification.nonparametric import (
    correlation_analysis,
    welch_spectral_analysis,
)
from app.services.tuning_identification.order_selection import (
    compute_aic,
    compute_bic,
    ljung_box_test,
)
from app.services.tuning_identification.physical_feasibility import (
    check_physical_feasibility,
)
from app.services.tuning_identification.types import (
    AlgorithmConfidenceLevel,
    CandidateModel,
    IdentificationResult,
    IdentifyMethod,
    ModelEvidence,
    ModelParams,
    ModelType,
    ParameterUncertainty,
    ThetaSource,
)

logger = logging.getLogger(__name__)

# 算法版本单一真相源在 types.IdentificationResult（v1.2=2026-10-10 IPDT 默认候选+val 稳态防护）
ALGORITHM_VERSION = "TUNE_IDENT_v1.4"

# 可信度阈值
_R2_A = 0.90
_R2_B = 0.80
_R2_C = 0.70
_R2_D = 0.50

# 残差-输入互相关检验（P1-4）：95% 置信界 ±1.96/√N 下允许的越界 lag 占比
_XCORR_MAX_LAG = 20
_XCORR_EXCEED_TOLERANCE = 0.10
# 残差可忽略阈值（P1-4 轨 1）：R² 高于此值时残差量级远低于信号，直接判通过
_R2_RESIDUAL_NEGLIGIBLE = 0.999

# P2-006 Occam 削减：SOPDT 升级门禁
# SOPDT 优于 FOPDT 当且仅当 R²_val 相对提升 > 5% 且 BIC 下降（更复杂模型须显著更优）
_OCCAM_R2_RELATIVE_GAIN = 0.05
# P2-005：Welch 相干辅助门禁阈值（低于此值提示弱线性/低信噪比，封顶可信度 C）
_LOW_COHERENCE_THRESHOLD = 0.3
# P2-019：坏点清洗参数
_MAX_INTERP_GAP = 5  # 连续 NaN < 此值时线性插值；≥ 此值按大缺口取最长段

# P2-020（2026-10-04 预处理增强）：自动选段 / 去趋势 / 低通滤波
_OP_SPAN_NORMALIZED = 100.0  # 预处理链输出 OP 已归一化 0~100（V62-P1-009）
# 去趋势总开关（2026-10-10 用户裁决：默认关闭）。保留判定与实现，关闭原因：
# 慢自衡过程的长阶跃响应（τ≥30min 级，化工常态）在有限窗口内呈爬升形状，
# 与工作点漂移在线性形状上不可分——误去趋势摧毁信号的风险 > 漏去趋势的
# 残差代价。未来改进方向（登记）：基于 SP/OP 因果的解释性去趋势（PV 趋势
# 无法被输入变化解释时才去除）。
_DETREND_ENABLED = False
# 去趋势保守门槛（四条件与，见 _detrend_signals 文档）：
# PV 全窗线性漂移 < 5% 量程跳过；PV 线性拟合 R² < 0.9（非趋势形状）跳过；
# OP 漂移 < 1% 量程视为积分特性（IPDT 保护）跳过；输入阶跃事件存在跳过
_DETREND_MIN_DRIFT_PCT = 5.0
_DETREND_MIN_LINEARITY = 0.9
_DETREND_MIN_OP_DRIFT_PCT = 1.0
_DETREND_INPUT_STEP_SP = 0.5  # SP 相邻变化 > 0.5% 量程 = 设定变更事件（SP 为操作员设定值
# 非测量值、无传感器噪声，可低于 OP 类阈值；41LIC 实测有 0.4% 微调未命中 1% 档）
# v1.3 动态感知子窗选取（auto-window）
_AUTOWIN_MIN_FULL_PTP = 3.0  # 整窗 PV ptp < 3% 量程 = 近稳态，无动态可挖不扫描
_AUTOWIN_MIN_POINTS = 600  # 子窗最短点数（val 段需仍有动态）
_AUTOWIN_RATE_GAIN = 1.5  # 子窗平均变化率须 > 整窗 × 此倍数才切换
# 低通必要性判据：滤波前后 PV 差异 std 占比 < 10% 视为高频噪声不显著，
# 不替换原信号（保证干净/低噪数据零影响，仅在确有测量噪声时介入）
_LOWPASS_MIN_DIFF_RATIO = 0.10
_LOWPASS_WN_MIN = 0.005  # 归一化截止频率下限（相对 Nyquist）
_LOWPASS_WN_MAX = 0.4  # 上限：≥ 此值说明快动态无安全余量，跳过滤波
_LOWPASS_FREQ_MARGIN = 20.0  # 截止 = PV 谱主峰频率 × 此倍数（覆盖瞬态/高阶特征）
_LOWPASS_PEAK_PROMINENCE = 5.0  # 主峰显著性：PSD 峰值 > 此倍数 × 中位值
# v1.2 验证段近稳态判定：val 段 PV 极差 < 1.5%（归一化 0~100 域）视为死水段，
# 自由仿真 R² 分母趋零假性归零 → 回退 R²_train 评估并封顶 C（41LIC12422 案例）
_VAL_STATIONARY_MIN_PTP = 1.5


def _find_contiguous_segments(valid: np.ndarray) -> list[tuple[int, int]]:
    """在布尔数组中找连续 True 段的 [(start, end), ...]（闭区间）."""
    idx = np.where(valid)[0]
    if len(idx) == 0:
        return []
    segments: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for i in idx[1:]:
        if i == prev + 1:
            prev = i
        else:
            segments.append((start, prev))
            start = prev = i
    segments.append((start, prev))
    return segments


def _clean_nan_segments(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    max_interp_gap: int = _MAX_INTERP_GAP,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, dict | None]:
    """P2-019 坏点清洗：小缺口线性插值，大缺口取最长连续有效段.

    OP/PV 任一为 NaN/Inf 视为坏点（辨识需配对时序）；SP 同步处理。

    策略：
    1. 小缺口（连续坏点 < max_interp_gap 且两端有有效值）：线性插值填补；
    2. 大缺口（连续坏点 ≥ max_interp_gap 或在端点）：按大缺口切分，取最长连续有效段；
    3. 清洗后点数 < 1 时返回 None（由调用方再判 < 50）。

    Args:
        u, y: OP/PV 时序（已被 np.array 转换，可被原地修改）
        sp: SP 时序（可选，同步处理）
        max_interp_gap: 可插值的最大连续坏点数

    Returns:
        (u_clean, y_clean, sp_clean, stats)
        stats: {original_points, interpolated_points, dropped_points,
                valid_points, valid_rate, n_large_gaps}
        若原始数据无坏点，stats 的 interpolated/dropped 均为 0。
        sp_clean 为 None 当且仅当输入 sp 为 None。
    """
    n = len(u)
    bad = ~np.isfinite(u) | ~np.isfinite(y)
    n_bad = int(bad.sum())

    # 无坏点：直接返回（sp 也原样返回）
    if n_bad == 0:
        stats = {
            "original_points": n,
            "interpolated_points": 0,
            "dropped_points": 0,
            "valid_points": n,
            "valid_rate": 1.0,
            "n_large_gaps": 0,
        }
        return u, y, sp, stats

    # SP 的坏点也纳入（SP 有 NaN 时也需清洗，但 OP/PV 是主信号）
    if sp is not None:
        bad = bad | ~np.isfinite(sp)

    # 找连续坏段（_find_contiguous_segments 对 bad 掩码找连续 True 段）
    gap_segments = _find_contiguous_segments(bad)

    interpolated = 0
    large_gaps: list[tuple[int, int]] = []

    for g_start, g_end in gap_segments:
        gap_len = g_end - g_start + 1
        # 小缺口且两端有有效值才插值（端点处的坏段无法插值）
        if gap_len < max_interp_gap and g_start > 0 and g_end < n - 1:
            left_u, right_u = float(u[g_start - 1]), float(u[g_end + 1])
            left_y, right_y = float(y[g_start - 1]), float(y[g_end + 1])
            for i in range(g_start, g_end + 1):
                frac = (i - g_start + 1) / (gap_len + 1)
                u[i] = left_u + frac * (right_u - left_u)
                y[i] = left_y + frac * (right_y - left_y)
            if sp is not None:
                left_s = float(sp[g_start - 1])
                right_s = float(sp[g_end + 1])
                for i in range(g_start, g_end + 1):
                    frac = (i - g_start + 1) / (gap_len + 1)
                    sp[i] = left_s + frac * (right_s - left_s)
            interpolated += gap_len
        else:
            large_gaps.append((g_start, g_end))

    # 大缺口：取最长连续有效段
    dropped = 0
    if large_gaps:
        valid = np.isfinite(u) & np.isfinite(y)
        if sp is not None:
            valid = valid & np.isfinite(sp)
        segments = _find_contiguous_segments(valid)
        if not segments:
            # 全坏：返回空
            stats = {
                "original_points": n,
                "interpolated_points": interpolated,
                "dropped_points": n,
                "valid_points": 0,
                "valid_rate": 0.0,
                "n_large_gaps": len(large_gaps),
            }
            return u[:0], y[:0], (sp[:0] if sp is not None else None), stats
        best_start, best_end = max(segments, key=lambda s: s[1] - s[0])
        u = u[best_start : best_end + 1]
        y = y[best_start : best_end + 1]
        if sp is not None:
            sp = sp[best_start : best_end + 1]
        dropped = n - len(u)

    stats = {
        "original_points": n,
        "interpolated_points": interpolated,
        "dropped_points": dropped,
        "valid_points": len(u),
        "valid_rate": len(u) / n if n > 0 else 0.0,
        "n_large_gaps": len(large_gaps),
    }
    return u, y, sp, stats


# ---------------------------------------------------------------------------
# P2-020（2026-10-04 预处理增强）：自动选段 / 去趋势 / 低通滤波
# 层 0.5：坏点清洗之后、激励检测之前；三项均可独立跳过、互不阻断。
# ---------------------------------------------------------------------------


def _auto_select_segment(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    mode: list[int] | None,
    op_limits: tuple[float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, str | None]:
    """自动选段：按 MODE/缺口/饱和切分，激励评分选最优可辨识片段.

    此前正式辨识为整窗一锅进——窗口混入手动/饱和/稳态死段时整窗回归被
    稀释（片段切分能力已存在但仅用于预览端点，P2-020 接入正式链）。

    分级回退（与 G24 裁决兼容：手动段可辨识，仅降权，不作硬门控）：
    1. 可辨识片段（exclusion_reason=None）中激励分最高者（并列取点数多）；
    2. 无 → 手动模式片段（MANUAL_MODE）中激励分最高者；
    3. 无 → 回退整窗（不因选段失败而拒绝辨识）。

    mode=None 时 segment_signals 不切分（假设全 AUTO），等价整窗。
    op_limits=(min, max)：OP 工程量程（饱和检测用）；None 跳过饱和检测
    ——算法栈不假设输入量纲（生产链 OP 已归一化 0~100，由包装层显式传入）。
    """
    from app.services.tuning_identification.segmentation import segment_signals

    op_min, op_max = op_limits if op_limits is not None else (None, None)
    try:
        specs = segment_signals(
            y.tolist(),
            u.tolist(),
            mode=mode,
            op_min=op_min,
            op_max=op_max,
        )
    except Exception:
        logger.debug("自动选段切分失败，回退整窗", exc_info=True)
        return u, y, sp, None

    identifiable = [s for s in specs if s.exclusion_reason is None]
    manual_fallback = [s for s in specs if s.exclusion_reason == "MANUAL_MODE"]
    pool = identifiable or manual_fallback
    if not pool:
        return u, y, sp, "auto-segment: 无可辨识片段，回退整窗"

    # 激励评分选优（d=1 粗滞后，同预览端点口径；正式延迟由参数化阶段搜索）
    scored: list[tuple[float, int, int, object]] = []
    for i, s in enumerate(pool):
        exc = check_excitation(
            u[s.start_idx : s.end_idx],
            y[s.start_idx : s.end_idx],
            1,
            op_span=_OP_SPAN_NORMALIZED,
        )
        scored.append(
            (excitation_score(exc.condition_number, exc.significant_changes), s.point_count, i, s)
        )
    best_score, _, best_i, best = max(scored)

    # 单段且为可辨识段 = 整窗，无选段事实（手动降级路径即使单段也标注）
    if identifiable and len(specs) == 1 and best is specs[0]:
        return u, y, sp, None
    su = u[best.start_idx : best.end_idx]
    sy = y[best.start_idx : best.end_idx]
    ssp = sp[best.start_idx : best.end_idx] if sp is not None else None
    degrade = "" if identifiable else "（手动段降级使用，G24）"
    note = (
        f"auto-segment: {best_i + 1}/{len(pool)} [{best.start_idx}:{best.end_idx}] "
        f"{best.mode_label} 激励分{best_score:.0f}{degrade}"
    )
    return su, sy, ssp, note


def _scan_dynamic_windows(
    y: np.ndarray,
    ts: float,
) -> list[tuple[float, int, int, float, float]]:
    """滑窗扫描动态窗（v1.4）：返回按评分降序的候选窗列表.

    启动条件与单窗版一致：整窗 val 位（60%~80%）PV ptp < 1.5% 量程
    （整窗验证段是死水才需要挖子窗）。
    每窗条件：整体 PV ptp ≥ 3% 量程（有实际幅度）；窗内 val 位 ptp ≥ 1.5%
    （切出的窗做 60/20/20 分割后验证段仍有动态）；长度 ≥ 600 点。
    窗长候选：120/60/30 分钟（按数据时长自适应向下取）；步长 = 窗长 / 6。
    评分 = val 位 ptp + 0.5 × 整体 ptp（幅度大的段优先）。

    Returns:
        [(score, start, end, val_ptp, seg_ptp), ...] 评分降序；整窗验证段
        有动态时返回空列表（无需挖子窗）。
    """
    n = len(y)
    val_lo, val_hi = int(n * 0.6), int(n * 0.8)
    full_val_ptp = float(np.ptp(y[val_lo:val_hi]))
    if full_val_ptp >= _VAL_STATIONARY_MIN_PTP:
        return []  # 整窗验证段有动态，无需挖子窗
    dur_minutes = n * ts / 60.0
    win_minutes = [m for m in (120.0, 60.0, 30.0) if m <= dur_minutes / 2.0]
    if not win_minutes:
        return []

    found: list[tuple[float, int, int, float, float]] = []
    for wm in win_minutes:
        w = int(wm * 60.0 / ts)
        if w < _AUTOWIN_MIN_POINTS:
            continue
        step = max(1, w // 6)
        for start in range(0, n - w + 1, step):
            seg = y[start : start + w]
            seg_ptp = float(np.ptp(seg))
            if seg_ptp < _AUTOWIN_MIN_FULL_PTP:
                continue
            v_lo = start + int(w * 0.6)
            v_hi = start + int(w * 0.8)
            v_ptp = float(np.ptp(y[v_lo:v_hi]))
            if v_ptp < _VAL_STATIONARY_MIN_PTP:
                continue
            found.append((v_ptp + 0.5 * seg_ptp, start, start + w, v_ptp, seg_ptp))
    found.sort(key=lambda t: t[0], reverse=True)
    return found


def _collect_disjoint_windows(
    windows: list[tuple[float, int, int, float, float]],
    k: int = 3,
) -> list[tuple[float, int, int, float, float]]:
    """贪心选 top-K 互不重叠的动态窗（v1.4 多窗辨识用）."""
    picked: list[tuple[float, int, int, float, float]] = []
    for win in windows:
        if len(picked) >= k:
            break
        if any(not (win[2] <= p[1] or win[1] >= p[2]) for p in picked):
            continue  # 与已选窗重叠
        picked.append(win)
    return picked


def _auto_select_window(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    ts: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, str | None]:
    """动态感知子窗选取（v1.3，v1.4 改为扫描核心的 单窗消费方）.

    生产现实（41LIC12422 教训）：阶跃稀少且集中在窗口局部，时序分割
    60/20/20 的 val 段恰好落在死水 → R² 假性归零。本函数选出验证段仍有
    动态的最优单窗（多窗场景由 identify_from_history 顶部编排逐窗辨识
    择优，见 _scan_dynamic_windows 文档）。
    """
    n = len(y)
    val_lo, val_hi = int(n * 0.6), int(n * 0.8)
    full_val_ptp = float(np.ptp(y[val_lo:val_hi]))
    wins = _scan_dynamic_windows(y, ts)
    if not wins:
        return u, y, sp, None
    _score, s, e, v_ptp, seg_ptp = wins[0]
    su = u[s:e]
    sy = y[s:e]
    ssp = sp[s:e] if sp is not None else None
    note = (
        f"auto-window: [{s}:{e}] W={round((e - s) * ts / 60)}min "
        f"PV跨度 {seg_ptp:.1f}%·验证位跨度 {v_ptp:.1f}%"
        f"（整窗验证位 {full_val_ptp:.1f}% 为死水已绕开）"
    )
    return su, sy, ssp, note


def _detrend_signals(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, str | None]:
    """去趋势：各信号去线性趋势（含均值；应对工作点缓变/传感器漂移）.

    执行条件四缺一不可（保守，误去代价 >> 漏去代价）：
    1. PV 全窗线性漂移 ≥ 5% 量程（漂移显著）；
    2. PV 对线性拟合的 R² ≥ 0.9（数据形状本质是缓变趋势）——SP 阶跃下
       PV 的"稳态跟随"是信号而非趋势（阶梯状对直线拟合差），排除；
    3. OP 也有可感漂移 ≥ 1% 量程（工作点漂移佐证）——OP 平稳而 PV 持续
       斜坡是积分过程（IPDT）的本质特征，跳过保护其可辨识性；
    4. 无显著输入阶跃事件（v1.3，41LIC12422 教训）——SP 相邻变化 >1%
       或 OP 相邻跳变 >2% 量程时，PV 的爬升是阶跃响应的过渡段而非
       漂移（单次大阶跃后的慢爬升在 1~3 的形状判据上与漂移不可分）。

    漏去趋势仅残差略增；误去趋势则摧毁信号本身（阶跃稳态/积分特性/
    阶跃后爬升过渡段）。
    """
    n = len(u)
    if n < 2:
        return u, y, sp, None
    t = np.arange(n, dtype=float)

    slope_y = float(np.polyfit(t, y, 1)[0])
    drift_pct = abs(slope_y) * n / _OP_SPAN_NORMALIZED * 100.0
    if drift_pct < _DETREND_MIN_DRIFT_PCT:
        return u, y, sp, None
    # 线性形状判据：直线解释 PV 变化的比例（阶跃/振荡主导的数据 R² 低）
    y_centered = y - float(np.mean(y))
    sst = float(np.sum(y_centered**2))
    if sst <= 1e-12:
        return u, y, sp, None
    lin_fit = slope_y * (t - float(np.mean(t)))
    r2_lin = 1.0 - float(np.sum((y_centered - lin_fit) ** 2)) / sst
    if r2_lin < _DETREND_MIN_LINEARITY:
        return u, y, sp, None
    slope_u = float(np.polyfit(t, u, 1)[0])
    drift_u_pct = abs(slope_u) * n / _OP_SPAN_NORMALIZED * 100.0
    if drift_u_pct < _DETREND_MIN_OP_DRIFT_PCT:
        return u, y, sp, None
    # SP 阶跃事件判据（v1.3）：SP 设定变更后的 PV 慢爬升是响应不是漂移
    # （41LIC12422 动态段：SP 阶跃→爬升，前三个形状条件全部放行，唯此
    # 判据可救）。只检 SP 不检 OP——OP 方波切换是激励不是工作点事件，
    # 且 PRBS 类数据本就被线性 R² 条件（振荡形状拟合差）拦住。
    sp_step = (
        bool(np.any(np.abs(np.diff(sp)) > _DETREND_INPUT_STEP_SP))
        if sp is not None and len(sp) == n
        else False
    )
    if sp_step:
        return u, y, sp, None

    def _detrend(x: np.ndarray) -> np.ndarray:
        coeff = np.polyfit(t, x, 1)
        return x - np.polyval(coeff, t)

    u2 = _detrend(u)
    y2 = _detrend(y)
    sp2 = _detrend(sp) if sp is not None else None
    note = f"detrend: PV 全窗漂移 {drift_pct:.1f}% 量程已去除"
    return u2, y2, sp2, note


def _lowpass_signals(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    ts: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, str | None, dict | None]:
    """低通滤波：PV 谱主导频率自适应截止，Butterworth-2 + filtfilt 零相位.

    两条铁律：
    - OP/PV/SP 必须同一截止频率滤波——传递函数估计 G_hat=(H·Y)/(H·U)
      中 H 在通带内抵消；不同截止会引入相对相位差导致增益/滞后错配；
    - 必须零相位（filtfilt）——因果滤波的群延迟会被延迟搜索误认为纯滞后 θ。

    截止频率 = PV Welch 谱主峰频率 × 10 倍安全余量（过程主导动态的频带
    覆盖其全部慢模态；峰不显著 = 无主导频率的宽带信号，多为纯噪声或快
    动态，跳过滤波）。主峰判定：PSD 峰值 > 5×中位值（排除平坦谱的随机
    峰），谱估计 detrend='constant' 且跳过 DC bin。Wn 夹在
    [0.005, 0.4]×Nyquist，≥ 上限说明快动态无安全余量 → 跳过。
    任一信号含非有限值时跳过（filtfilt 对 NaN 不稳健；清洗后不应出现）。

    P2-03：第 5 返回值为结构化参数（appliedWn 等实际生效值，供辨识证据
    契约的结构化预处理字段；note 字符串保持不变）。
    """
    arrs = [u, y] + ([sp] if sp is not None else [])
    if any(not np.isfinite(a).all() for a in arrs) or len(y) < 16:
        return u, y, sp, None, None

    from scipy import signal as _sp_signal

    fs = 1.0 / ts
    nperseg = int(min(len(y), 256))
    freqs, psd = _sp_signal.welch(y, fs=fs, nperseg=nperseg, detrend="constant")
    if len(psd) < 3:
        return u, y, sp, None, None
    psd_ac = psd[1:]  # 跳过 DC bin（残余均值不为谱峰）
    med = float(np.median(psd_ac))
    peak_i = int(np.argmax(psd_ac))
    if med <= 0 or float(psd_ac[peak_i]) <= _LOWPASS_PEAK_PROMINENCE * med:
        return u, y, sp, None, None  # 无显著主峰：宽带信号（纯噪声/快动态），跳过
    fc = float(freqs[peak_i + 1]) * _LOWPASS_FREQ_MARGIN
    wn = fc / (fs / 2.0)
    if wn >= _LOWPASS_WN_MAX:
        return u, y, sp, None, None
    wn = max(wn, _LOWPASS_WN_MIN)
    b, a = _sp_signal.butter(2, wn)
    fu = _sp_signal.filtfilt(b, a, u)
    fy = _sp_signal.filtfilt(b, a, y)
    # 必要性判据：滤波实际去除的高频成分不显著（干净数据）→ 不替换原信号，
    # 保证预处理对无噪/低噪数据零影响（Occam/一致性等既有行为不变）
    y_std = float(np.std(y - np.mean(y)))
    diff_ratio = float(np.std(fy - y)) / y_std if y_std > 1e-12 else 0.0
    if diff_ratio < _LOWPASS_MIN_DIFF_RATIO:
        return u, y, sp, None, None
    fsp = _sp_signal.filtfilt(b, a, sp) if sp is not None else None
    note = f"lowpass: fc={wn * fs / 2:.4g}Hz(Wn={wn:.3f}) butter2·filtfilt"
    meta = {"applied": True, "appliedWn": round(float(wn), 6), "appliedFcHz": round(float(fc), 8)}
    return fu, fy, fsp, note, meta


def _feasibility_dict(feasibility) -> dict | None:
    """T2（2026-10-01）：物理可行性检查结构化（原仅拼进 reason 字符串）。"""
    if feasibility is None:
        return None
    return {
        "passed": bool(feasibility.passed),
        "reasonCode": feasibility.reason_code,
        "details": feasibility.details,
    }


def _evidence_cleaning_stats(cleaning_stats: dict) -> dict | None:
    """仅在有清洗时返回统计（无坏点时 None，避免证据冗余）."""
    if cleaning_stats["interpolated_points"] > 0 or cleaning_stats["dropped_points"] > 0:
        return cleaning_stats
    return None


def _build_preprocessing_record(
    *,
    cleaning_stats: dict,
    seg_note: str | None,
    win_note: str | None,
    detrend_note: str | None,
    lowpass_meta: dict | None,
    mode_provided: bool,
    op_limits: tuple[float, float] | None,
) -> dict[str, Any]:
    """P2-03（R2 §三 DEC-10 补表述）：辨识预处理链结构化记录.

    把原先只拼进 reason 字符串的三件套（自动选段 / 动态感知子窗 / 去趋势+
    低通滤波）开关与实际生效参数提升为结构化字段；reason 字符串与既有
    evidence 字段（data_hash 等）保持不变——evidence.data_hash 仍按预处理
    后数据取，原始输入与预处理参数由不可变输入包 manifest.preprocess 承载
    （app/services/calc_snapshot.py::save_identification_snapshot）。

    字段口径（调用侧不可得时为显式 None，不伪补）：
    - ``cleaning``：坏点清洗统计（P2-019）；
    - ``autoSegment``：MODE/缺口/饱和切分（applied=note 非空）；
    - ``autoWindow``：动态感知子窗（applied=note 非空）；
    - ``detrend``：去趋势（enabled=全局开关，applied=实际生效）；
    - ``lowpass``：低通滤波（applied=False 或含 appliedWn/appliedFcHz）；
    - ``multiWindow``：v1.4 多窗编排窗位（外层编排器回填，含被检/入选/
      胜出窗位与各窗拟合度）。
    """
    return {
        "cleaning": {
            "originalPoints": int(cleaning_stats.get("original_points", 0)),
            "validPoints": int(cleaning_stats.get("valid_points", 0)),
            "interpolatedPoints": int(cleaning_stats.get("interpolated_points", 0)),
            "droppedPoints": int(cleaning_stats.get("dropped_points", 0)),
        },
        "autoSegment": {
            "modeProvided": bool(mode_provided),
            "opLimits": list(op_limits) if op_limits is not None else None,
            "applied": bool(seg_note),
            "note": seg_note,
        },
        "autoWindow": {"applied": bool(win_note), "note": win_note},
        "detrend": {
            "enabled": bool(_DETREND_ENABLED),
            "applied": bool(detrend_note),
            "note": detrend_note,
        },
        "lowpass": dict(lowpass_meta) if lowpass_meta else {"applied": False},
        "multiWindow": None,  # 外层多窗编排回填（无编排时显式 None=未运行）
    }


def identify_from_history(
    op: list[float],
    pv: list[float],
    sp: list[float] | None = None,
    ts: float = 1.0,
    theta_estimate: float | None = None,
    candidate_models: list[ModelType] | None = None,
    mode: list[int] | None = None,
    op_limits: tuple[float, float] | None = None,
    _internal_call: bool = False,
) -> IdentificationResult:
    """基于历史数据辨识过程对象 G_plant = PV/OP.

    入口对 OP/PV（及 SP）做去均值处理：过程模型描述增量关系 Δy = G·Δu，
    无截距回归要求偏差变量输入；输出的 K/tau/theta 为增量参数，无需还原。
    偏置量（去均值前的样本均值）记录在 best_model.reason 中。

    v1.4（2026-10-10）多窗辨识编排：整窗验证段为死水且存在 ≥2 个不重叠
    动态窗时，对每个动态窗独立完整辨识（multi-experiment 实践的稳健落
    地——段内回归不跨段边界，避免拼接引入伪样本），按（可信度, 拟合度）
    择优返回并在 reason 汇总各窗结果。全窗失败时落回整窗既有流程。

    P2-020（2026-10-04 预处理增强）：坏点清洗后依次执行自动选段（按
    MODE/缺口/饱和切分并按激励评分选最优段；mode=None 不切分）→ 去线性
    趋势 → 低通滤波（PV 谱自适应截止 + 零相位，OP/PV/SP 同截止），
    三项均可独立跳过、处理标注拼入 reason 透明化。mode 参数与 G24 裁决
    的关系：不作辨识门控（手动段降级使用而非拒绝），仅用于选段切分。

    Args:
        op: OP 时序（过程对象输入）
        pv: PV 时序（过程对象输出）
        sp: SP 时序（保留用于后续经验证的闭环辨识方法；Phase 0 不参与生产选模）
        ts: 采样周期（秒）
        theta_estimate: 纯滞后预估值（秒），None 时使用 2Ts 启发值并将可信度封顶 C
        candidate_models: 候选模型阶次列表，默认 [FOPDT, SOPDT, IPDT]
            （v1.2：积分对象纳入默认候选——液位等积分过程此前只能被
            FOPDT 病态拟合，K 符号随机且 tau→∞；Occam 择优保证自衡
            过程仍选 FOPDT/SOPDT）
        mode: MODE 时序（可选；自动选段的切分依据，None 时假设全 AUTO 不切分）

    Returns:
        IdentificationResult
    """
    # v1.4 多窗编排：仅外层调用（递归窗辨识跳过，防无限递归）
    if not _internal_call and len(op) == len(pv) and len(op) >= _AUTOWIN_MIN_POINTS * 2:
        try:
            _wins = _collect_disjoint_windows(_scan_dynamic_windows(np.array(pv, dtype=float), ts))
        except Exception:
            _wins = []
        if len(_wins) >= 2:
            _order = {
                AlgorithmConfidenceLevel.A: 5,
                AlgorithmConfidenceLevel.B: 4,
                AlgorithmConfidenceLevel.C: 3,
                AlgorithmConfidenceLevel.D: 2,
                AlgorithmConfidenceLevel.E: 1,
                AlgorithmConfidenceLevel.INCONCLUSIVE: 0,
            }
            _ok: list[tuple[tuple, IdentificationResult, int, int]] = []
            for _s, _e in ((w[1], w[2]) for w in _wins):
                _r = identify_from_history(
                    op=op[_s:_e],
                    pv=pv[_s:_e],
                    sp=sp[_s:_e] if sp is not None else None,
                    ts=ts,
                    theta_estimate=theta_estimate,
                    candidate_models=candidate_models,
                    mode=mode[_s:_e] if mode is not None else None,
                    op_limits=op_limits,
                    _internal_call=True,
                )
                if _r.success and _r.best_model is not None:
                    _key = (
                        _order.get(_r.best_model.confidence, 0),
                        _r.best_model.fitting_score,
                        1 if _r.best_model.residual_test_passed else 0,
                    )
                    _ok.append((_key, _r, _s, _e))
            if _ok:
                _ok.sort(key=lambda t: t[0], reverse=True)
                _key, best_r, _bs, _be = _ok[0]
                best_r.reason = (
                    f"multi-window: {len(_ok)}/{len(_wins)} 段动态窗辨识成功，"
                    f"最优段 [{_bs}:{_be}]"
                    f"（各段 fit={[round(t[1].best_model.fitting_score, 1) for t in _ok]}）；"
                    + (best_r.reason or "")
                )
                # P2-03（DEC-10）：多窗编排窗位结构化——被检/入选/胜出窗位
                # 与各窗拟合度（reason 字符串保持不变；预算终止状态 P3 填入）
                if best_r.preprocessing is None:
                    best_r.preprocessing = {}
                best_r.preprocessing["multiWindow"] = {
                    "checkedWindows": [{"start": int(w[1]), "end": int(w[2])} for w in _wins],
                    "succeededWindows": [
                        {
                            "start": int(s),
                            "end": int(e),
                            "fittingScore": round(r.best_model.fitting_score, 2),
                        }
                        for _k, r, s, e in _ok
                    ],
                    "selectedWindow": {"start": int(_bs), "end": int(_be)},
                    "budgetTermination": None,
                }
                return best_r
            # 全窗失败 → 落回整窗既有流程（下方）

    u_raw = np.array(op, dtype=float)
    y_raw = np.array(pv, dtype=float)
    theta_source = ThetaSource.EXPLICIT if theta_estimate is not None else ThetaSource.HEURISTIC_2TS
    if len(u_raw) != len(y_raw):
        return IdentificationResult(
            success=False,
            reason=f"OP/PV 长度不匹配（{len(u_raw)} vs {len(y_raw)}）",
            theta_source=theta_source,
        )
    if len(u_raw) < 50:
        return IdentificationResult(
            success=False,
            reason=f"数据不足（{len(u_raw)} 点，需 ≥ 50）",
            theta_source=theta_source,
        )

    candidates = candidate_models or [ModelType.FOPDT, ModelType.SOPDT, ModelType.IPDT]
    if not math.isfinite(ts) or ts <= 0:
        return IdentificationResult(
            success=False,
            reason="采样周期 ts 必须为有限正数",
            theta_source=theta_source,
        )
    if theta_estimate is not None and (not math.isfinite(theta_estimate) or theta_estimate < 0):
        return IdentificationResult(
            success=False,
            reason="纯滞后预估值必须为有限非负数",
            theta_source=theta_source,
        )

    # P2-009：闭环 SP 激励检测 — 不再拒绝，而是启用 CLIVC（可证明闭环一致 IV）
    sp_raw: np.ndarray | None = None
    has_sp_excitation = False
    if sp is not None:
        sp_raw = np.array(sp, dtype=float)
        if len(sp_raw) != len(u_raw):
            return IdentificationResult(
                success=False,
                reason=f"SP 与 OP/PV 长度不匹配（{len(sp_raw)} vs {len(u_raw)}）",
                theta_source=theta_source,
            )

    # P2-019：坏点清洗 — 小缺口线性插值，大缺口取最长连续有效段。
    # 替代旧 np.isfinite 硬拒绝（任何坏点就放弃辨识）；工业数据传感器故障/通信
    # 中断/采样缺失常见，硬拒绝导致辨识完全不可用。清洗后继续辨识，证据记录统计。
    u_raw, y_raw, sp_raw, cleaning_stats = _clean_nan_segments(u_raw, y_raw, sp_raw)
    if cleaning_stats["valid_points"] < 50:
        return IdentificationResult(
            success=False,
            reason=(
                f"清洗后有效数据不足（{cleaning_stats['valid_points']} 点，需 ≥ 50；"
                f"原始 {cleaning_stats['original_points']} 点，"
                f"插值 {cleaning_stats['interpolated_points']} 点，"
                f"剔除 {cleaning_stats['dropped_points']} 点）"
            ),
            theta_source=theta_source,
        )

    # P2-020（2026-10-04 预处理增强）：自动选段 → 去趋势 → 低通滤波。
    # 位于 SP 激励判定之前（切出的最优段决定 CLIVC 是否启用更准确）；
    # 三项均可独立跳过，标注拼入 reason 透明化（不静默改变辨识输入）。
    # 激励检测（层 1）使用滤波前副本：滤波会抹平 OP 微小方向变化，
    # 在滤波后数据上检测会系统性偏严（闭环 OP 本就渐进少变号）。
    # P2-03（R2 §三 DEC-10）：同步产出结构化预处理参数（steps 开关/参数/
    # applied 事实），reason 字符串保持不变；data_hash 仍按预处理后数据取。
    preprocess_notes: list[str] = []
    u_raw, y_raw, sp_raw, seg_note = _auto_select_segment(u_raw, y_raw, sp_raw, mode, op_limits)
    if seg_note:
        preprocess_notes.append(seg_note)
    # v1.3：动态感知子窗（大窗口内定位阶跃响应段；阶跃稀少是生产常态）
    u_raw, y_raw, sp_raw, win_note = _auto_select_window(u_raw, y_raw, sp_raw, ts)
    if win_note:
        preprocess_notes.append(win_note)
    detrend_note: str | None = None
    if _DETREND_ENABLED:
        u_raw, y_raw, sp_raw, detrend_note = _detrend_signals(u_raw, y_raw, sp_raw)
        if detrend_note:
            preprocess_notes.append(detrend_note)
    u_exc_probe, y_exc_probe = u_raw.copy(), y_raw.copy()
    u_raw, y_raw, sp_raw, lowpass_note, lowpass_meta = _lowpass_signals(u_raw, y_raw, sp_raw, ts)
    if lowpass_note:
        preprocess_notes.append(lowpass_note)
    preprocess_note = "; ".join(preprocess_notes)
    preprocess_structured = _build_preprocessing_record(
        cleaning_stats=cleaning_stats,
        seg_note=seg_note,
        win_note=win_note,
        detrend_note=detrend_note,
        lowpass_meta=lowpass_meta,
        mode_provided=mode is not None,
        op_limits=op_limits,
    )

    if sp_raw is not None:
        sp_range = float(np.ptp(sp_raw))
        change_threshold = max(1e-9, 0.01 * sp_range)
        significant_sp_changes = int(np.sum(np.abs(np.diff(sp_raw)) > change_threshold))
        # P2-009：SP 有显著变化时启用 CLIVC（外生 SP 作工具变量，闭环一致）
        # 旧 Phase 0 门禁（直接拒绝）已由可证明 CLIVC 方法替代
        has_sp_excitation = significant_sp_changes > 0

    # P0-2：入口去均值（偏置消除）。
    # ARX/ARMAX/IV 回归均无截距项，工业数据（如 PV≈450/OP≈60）若不去均值，
    # 增益 K 会按原点割线（≈ȳ/ū）收敛而非增量增益，闭环+偏置场景全灭。
    # 物理模型是增量关系 Δy = G·Δu，去均值后 K 无需还原。
    u_mean = float(np.mean(u_raw))
    y_mean = float(np.mean(y_raw))
    u = u_raw - u_mean
    y = y_raw - y_mean
    # P2-009：SP 去均值（CLIVC 工具变量需偏差变量）
    sp_demeaned: np.ndarray | None = None
    if sp_raw is not None:
        sp_mean = float(np.mean(sp_raw))
        sp_demeaned = sp_raw - sp_mean
    # Phase 0：缺省 theta 仅作为显式标注的 2Ts 启发值；不能冒充已估计参数。
    # 使用 is not None 保留调用方明确给出的 theta=0。
    theta_seconds = theta_estimate if theta_estimate is not None else 2 * ts
    d_explicit = max(0, round(theta_seconds / ts))

    # P2-001：延迟候选搜索范围
    # 用户给 theta 时在 d_explicit ±3 邻域精搜；未给时全域搜索 0..d_max。
    # d_max 上限 60 覆盖 P2-003 的 θ=60 Ts 场景，同时不超过数据长度 1/4。
    n_u = len(u)
    if theta_estimate is not None:
        d_search_max = min(d_explicit + 3, max(0, n_u // 4))
        theta_source = ThetaSource.EXPLICIT
    else:
        d_search_max = min(max(0, n_u // 4), 60)
        theta_source = ThetaSource.SEARCHED

    # 激励检测用 d_explicit（粗略估计，搜索在参数化阶段做）
    d = d_explicit

    # P2-002：时间顺序留出集分割（不随机打乱，保留时序自相关）
    # 60% train / 20% val / 20% test；数据不足时退化为 70/30 train/val（无 test）
    n_total = len(y)
    n_train = int(n_total * 0.6)
    n_val = int(n_total * 0.2)
    n_test = n_total - n_train - n_val
    if n_train < 50 or n_val < 20:
        # 短数据退化为 70/30 train/val
        n_train = int(n_total * 0.7)
        n_val = n_total - n_train
        n_test = 0
    u_train, y_train = u[:n_train], y[:n_train]
    u_val, y_val = u[n_train : n_train + n_val], y[n_train : n_train + n_val]
    # P2-009：SP 训练集分割（CLIVC 工具变量；验证集自由仿真只需 u_val/y_val）
    sp_train = sp_demeaned[:n_train] if sp_demeaned is not None else None

    # P2-016：数据快照哈希（输入 OP/PV + ts + 算法版本，可追溯辨识输入）
    data_hash = _compute_data_hash(u_raw, y_raw, ts)

    results: list[CandidateModel] = []

    # ── 层 1：激励检测 ──
    # V62-P1-009 接线（2026-10-01）：planner 输出 OP 已归一化 0~100，
    # op_span=100.0 走量程归一化路径（原不传恒走"u/y 跨量纲"回退）。
    # P2-020：用滤波前副本检测（滤波会抹平 OP 微变号，见预处理块注释）
    exc = check_excitation(u_exc_probe, y_exc_probe, d, op_span=100.0)
    if not exc.is_sufficient:
        _fail_note = f"{preprocess_note}; " if preprocess_note else ""
        return IdentificationResult(
            success=False,
            reason=f"{_fail_note}激励不足：{exc.verdict}",
            segments=[],
            theta_source=theta_source,
            preprocessing=preprocess_structured,
        )
    exc_score = excitation_score(exc.condition_number, exc.significant_changes)

    # ── 层 2：非参数粗估（用于初值和交叉校验）──
    # P2-005：Welch 相干仅作辅助门禁，不宣称闭环对象频响无偏——
    # 闭环下输入与扰动相关，谱估计 Ĝ=S_uy/S_uu 有偏；相干低只作可信度辅助信号。
    try:
        nonparam = correlation_analysis(u, y, ts)
        K_rough = nonparam.gain_estimate
        logger.debug("非参数粗估 K=%s, tau+theta=%s", K_rough, nonparam.time_constant_estimate)
    except Exception:
        logger.debug("非参数粗估失败，跳过", exc_info=True)
        K_rough = None
    # P2-005：Welch 相干辅助门禁（低相干 = 弱线性 / 低信噪比，封顶可信度）
    mean_coherence: float | None = None
    try:
        spectrum = welch_spectral_analysis(u, y, ts)
        coh = spectrum.get("coherence")
        if coh is not None and len(coh) > 0:
            mean_coherence = float(np.mean(coh))
    except Exception:
        logger.debug("Welch 谱分析失败，跳过相干辅助门禁", exc_info=True)

    # ── 层 3：参数化辨识（生产候选）──
    # P2-009：CLIVC（可证明闭环一致 IV）在有 SP 激励时进入生产候选集，
    # 取代旧 Phase 0 对闭环 SP 的直接拒绝。ARX/ARMAX 在闭环下有偏但仍作基线对照。
    identification_runs: list[tuple[IdentifyMethod, object]] = []
    # 总是跑 ARX（初值 + 基线）
    identification_runs.append((IdentifyMethod.HISTORICAL_ARX, "arx"))
    # 总是跑 ARMAX（扰动建模）
    identification_runs.append((IdentifyMethod.HISTORICAL_ARMAX, "armax"))
    # P2-009：SP 有激励时跑 CLIVC（闭环一致 IV，外生 SP 作工具变量）
    if has_sp_excitation and sp_demeaned is not None:
        identification_runs.append((IdentifyMethod.HISTORICAL_IV, "clivc"))

    # ── 层 4-5：阶次选择 + 离散→连续 ──
    for model_type in candidates:
        # P2-008：IPDT 积分过程走专用差分辨识分支（不与 FOPDT/SOPDT 共用 ARX→连续链）
        if model_type == ModelType.IPDT:
            ipdt_candidate = _identify_ipdt_candidate(
                u_train=u_train,
                y_train=y_train,
                u_val=u_val,
                y_val=y_val,
                d_search_max=d_search_max,
                ts=ts,
                theta_source=theta_source,
                exc=exc,
                exc_score=exc_score,
                K_rough=K_rough,
                mean_coherence=mean_coherence,
                n_train=n_train,
                n_val=n_val,
                n_test=n_test,
                data_hash=data_hash,
                cleaning_stats=cleaning_stats,
            )
            if ipdt_candidate is not None:
                results.append(ipdt_candidate)
            continue

        na = 1 if model_type == ModelType.FOPDT else 2
        nb = 1

        # P2-001：延迟候选搜索 — 对 d=0..d_max 跑 ARX，用 BIC 选最优 d
        # P2-002：搜索用训练集（不泄漏留出集信息）
        #
        # 已知局限（P2-017 标注集验证暴露）：SOPDT（na=2）的延迟/极点存在退化——
        # ARX na=2 可用极快极点补偿过大 d，且慢过阻尼过程 y[k-1]≈y[k-2] 使回归
        # 矩阵病态，T2 崩塌、a2 失去物理意义。彻底修复需 SRIVC（连续时间工具变量），
        # 属后续工作；当前 SOPDT 候选仍输出供审计，但 T1/T2 精度不作门禁。
        best_d, delay_search_trace = _search_delay(
            u_train, y_train, na=na, nb=nb, d_max=d_search_max
        )
        d_model = best_d
        logger.debug(
            "P2-001 %s 延迟搜索: d_max=%d → best_d=%d, trace=%s",
            model_type,
            d_search_max,
            d_model,
            delay_search_trace[:5],
        )

        # P2-009：保留所有成功候选（透明化算法对比，CLIVC/ARX/ARMAX 并列可审计）
        model_candidates: list[CandidateModel] = []
        for method, algo_key in identification_runs:
            try:
                if algo_key == "arx":
                    res = identify_arx(u_train, y_train, d_model, na=na, nb=nb)
                    method_used = method
                elif algo_key == "armax":
                    res = identify_armax(u_train, y_train, d_model, na=na, nb=nb, nc=1)
                    method_used = method
                elif algo_key == "clivc":
                    # P2-009：CLIVC 需要外生 SP 作工具变量
                    if sp_train is None:
                        continue
                    res = identify_clivc4(u_train, y_train, sp_train, d_model, na=na, nb=nb)
                    method_used = method
                else:
                    continue
            except Exception as exc_err:
                logger.debug("%s 辨识失败：%s", method, exc_err)
                continue

            # 离散→连续转换
            try:
                if model_type == ModelType.FOPDT:
                    params = arx_to_fopdt(res.a_coeffs[0], res.b_coeffs[0], res.d, ts)
                else:
                    if len(res.a_coeffs) < 2:
                        continue
                    params = arx_to_sopdt(
                        res.a_coeffs[0], res.a_coeffs[1], res.b_coeffs[0], res.d, ts
                    )
            except Exception as conv_err:
                logger.debug("%s 离散→连续转换失败：%s", method, conv_err)
                continue

            # P2-012：物理可行性门禁 —— 负增益/NMP 零点不拒绝但封顶可信度并标记
            feasibility = check_physical_feasibility(params, res.b_coeffs, ts)
            physical_flag = "" if feasibility.passed else f", {feasibility.reason_code}"

            # P2-004：非参数一致性检查（符号 + 量级）—— 与独立的相关分析粗估交叉校验
            np_passed, np_reason = _check_nonparam_consistency(params.K, K_rough)
            if not np_passed:
                physical_flag += f", {np_reason}"

            # P2-002：验证集自由仿真 R²（留出集泛化能力，替代训练集方程误差 R²）
            r2_train = max(0.0, min(1.0, res.r_squared))
            y_val_pred, r2_val = _free_run_simulation(
                u_val, y_val, res.a_coeffs, res.b_coeffs, d_model
            )
            # v1.2 验证段近稳态防护（41LIC12422 案例）：动态集中在前部的
            # 窗口（SP 阶跃后长稳态），时序分割的 val 段可能整段死水
            # （PV ptp 极小），自由仿真 R² 分母趋零而假性归零——模型未必
            # 全错而是验证方法失效。处置：R² 评估回退 R²_train、可信度
            # 封顶 C（验证不可用，需人工复核）、reason_codes 标注
            # VAL_STATIONARY（evidence.r2_val 保留真值供审计）。
            val_stationary = float(np.ptp(y_val)) < _VAL_STATIONARY_MIN_PTP
            r2_eval = r2_train if val_stationary else r2_val
            # P2-013：验证集残差序列（详细审计用）
            residuals_val_arr = y_val - y_val_pred
            # P2-014：NRMSE = RMSE / range(y_val)
            y_val_range = float(np.ptp(y_val))
            if len(residuals_val_arr):
                rmse_val = float(np.sqrt(np.mean(residuals_val_arr**2)))
            else:
                rmse_val = 0.0
            nrmse_val = rmse_val / y_val_range if y_val_range > 1e-12 else 0.0

            # 残差检验（P1-4：三轨制，在训练集方程误差上做）
            # 旧实现对一切算法用方程误差 e = A·y − B·u 做 Ljung-Box 白性检验；
            # 闭环下 e = A·ν（ARMAX 中 e = C·ε）天然有色，好模型也过不了，
            # 可信度永封顶 C，排名退化为 R² 使闭环有偏的 ARX 反而胜出。
            #   轨 1（残差可忽略）：R² ≥ 0.999 时模型已解释几乎全部方差，
            #     残差仅是去均值常数偏置 c 与数值残渣（量级 ≪ 信号），
            #     对其做相关检验只会误判，直接判通过。
            #   轨 2（ARMAX，有噪声模型 C）：检验一步预测误差白性
            #     ε = (A/C)·y − (B/C)·u = e/C（C 反滤波方程误差）。
            #   轨 3（ARX/IV，无噪声模型，C=1）：白性检验不适用（e 恒有色），
            #     改残差-输入独立性检验——模型充分 ⇔ 残差不再含输入动态。
            max_lag_na = max(na, nb + d_model)
            rows = len(y_train) - max_lag_na
            residuals = np.zeros(max(rows, 0))
            if rows > 0:
                for i in range(rows):
                    idx = max_lag_na + i
                    val = y_train[idx]
                    for j in range(na):
                        val += res.a_coeffs[j] * y_train[idx - 1 - j]
                    for j in range(nb):
                        val -= res.b_coeffs[j] * u_train[idx - d_model - j]
                    residuals[i] = val
            c_coeffs = getattr(res, "c_coeffs", None)
            if r2_train >= _R2_RESIDUAL_NEGLIGIBLE:
                residual_white = True
                test_note = f"residual_negligible(R²_train={r2_train:.4f})"
            elif rows > 0 and c_coeffs:
                eps = _equation_error_to_prediction_error(residuals, c_coeffs)
                _, lb_p = ljung_box_test(eps, max_lag=min(10, len(eps) // 3), n_params=na + nb)
                residual_white = lb_p > 0.05
                test_note = f"LB_p={lb_p:.3f}"
            else:
                exceed_ratio = _residual_input_exceed_ratio(residuals, u_train)
                residual_white = exceed_ratio <= _XCORR_EXCEED_TOLERANCE
                test_note = f"xcorr_exceed={exceed_ratio:.3f}"

            # 可信度评估（v1.2：val 段近稳态时以 R²_train 回退评估并封顶 C）
            confidence = _assess_confidence(exc, r2_eval, residual_white, exc_score)
            if val_stationary:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
                physical_flag += ", VAL_STATIONARY"
            if theta_source == ThetaSource.HEURISTIC_2TS:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
            # P2-012：物理可行性未通过（负增益/NMP 零点）封顶 C，需人工复核
            if not feasibility.passed:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
                physical_flag = f", {feasibility.reason_code}({feasibility.details.split(',')[0]})"
            # P2-004：非参数一致性未通过封顶 C（符号/量级矛盾，参数化模型可疑）
            if not np_passed:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
            # P2-005：Welch 相干辅助门禁（低相干 = 弱线性/低信噪比，仅辅助信号不拒绝）
            low_coherence = mean_coherence is not None and mean_coherence < _LOW_COHERENCE_THRESHOLD
            if low_coherence:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)

            # P2-006：AIC/BIC 信息准则（训练集残差方差，用于 Occam 削减与证据输出）
            # n_params = na + nb（+ nc for ARMAX），复杂模型须用更小残差补偿参数惩罚
            n_params = na + nb + len(c_coeffs) if c_coeffs else na + nb
            aic_val = compute_aic(res.n_samples, res.residual_var, n_params)
            bic_val = compute_bic(res.n_samples, res.residual_var, n_params)

            # P2-013~016：构建辨识证据
            reason_codes: list[str] = []
            if not feasibility.passed:
                reason_codes.append(feasibility.reason_code)
            if not np_passed:
                reason_codes.append(np_reason)
            if low_coherence:
                reason_codes.append("LOW_COHERENCE")
            if theta_source == ThetaSource.HEURISTIC_2TS:
                reason_codes.append("HEURISTIC_2TS")
            if val_stationary:
                reason_codes.append("VAL_STATIONARY")
            evidence = ModelEvidence(
                n_train=n_train,
                n_val=n_val,
                n_test=n_test,
                r2_val=round(r2_val, 4),
                r2_train=round(r2_train, 4),
                nrmse_val=round(nrmse_val, 4),
                residual_test_note=test_note,
                mean_coherence=mean_coherence,
                algorithm_version=ALGORITHM_VERSION,
                data_hash=data_hash,
                theta_source=theta_source.value,
                delay_search_trace=delay_search_trace,
                reason_codes=reason_codes,
                y_val_observed=[round(float(v), 6) for v in y_val],
                y_val_predicted=[round(float(v), 6) for v in y_val_pred],
                residuals_val=[round(float(v), 6) for v in residuals_val_arr],
                parameter_uncertainty=_compute_parameter_uncertainty(
                    a_coeffs=res.a_coeffs,
                    b_coeffs=res.b_coeffs,
                    d=d_model,
                    res_var=res.residual_var,
                    u_train=u_train,
                    y_train=y_train,
                    sp_train=sp_train,
                    method=method_used,
                    model_type=model_type,
                    ts=ts,
                ),
                cleaning_stats=_evidence_cleaning_stats(cleaning_stats),
                physical_feasibility=_feasibility_dict(feasibility),
            )

            candidate = CandidateModel(
                params=params,
                fitting_score=round(r2_eval * 100, 2),  # v1.2：val 稳态回退口径
                confidence=confidence,
                identify_method=method_used,
                residual_test_passed=residual_white,
                excitation_score=exc_score,
                reason=(
                    f"R²_val={r2_val:.3f}, R²_train={r2_train:.3f},"
                    f" {test_note}, AIC={aic_val:.1f}, BIC={bic_val:.1f},"
                    f" iters={getattr(res, 'iterations', 1)}{physical_flag}"
                ),
                aic=round(aic_val, 2),
                bic=round(bic_val, 2),
                evidence=evidence,
            )
            # P2-009：保留所有成功候选（透明化，CLIVC 与 ARX 并列可审计）
            model_candidates.append(candidate)

        # P2-009：所有方法候选进入 results，_select_with_occam 负责择优
        results.extend(model_candidates)

    if not results:
        _fail_note = f"{preprocess_note}; " if preprocess_note else ""
        return IdentificationResult(
            success=False,
            reason=f"{_fail_note}所有算法/阶次辨识均失败",
            theta_source=theta_source,
            preprocessing=preprocess_structured,
        )

    # P2-006：Occam 削减 — SOPDT 优于 FOPDT 当且仅当 R²_val 相对提升 > 5% 且 BIC 下降
    best = _select_with_occam(results)

    # 整体可信度检查
    if best.confidence == AlgorithmConfidenceLevel.INCONCLUSIVE:
        _fail_note = f"{preprocess_note}; " if preprocess_note else ""
        return IdentificationResult(
            success=False,
            reason=f"{_fail_note}辨识可信度不足：{best.reason}",
            candidates=results,
            theta_source=theta_source,
            preprocessing=preprocess_structured,
        )

    # P0-2：记录去均值偏置量（增量模型的零位基准，供结果审计追溯）
    offset_note = f"去均值偏置 PV={y_mean:.6g}, OP={u_mean:.6g}"
    best.reason = f"{best.reason}; {offset_note}" if best.reason else offset_note

    _ok_note = f"{preprocess_note}; " if preprocess_note else ""
    return IdentificationResult(
        success=True,
        best_model=best,
        candidates=results,
        reason=f"{_ok_note}辨识成功（{offset_note}）",
        theta_source=theta_source,
        preprocessing=preprocess_structured,
    )


def _compute_data_hash(u: np.ndarray, y: np.ndarray, ts: float) -> str:
    """P2-016：计算输入数据快照哈希（SHA256，前 16 位用于可追溯标识）.

    对 OP/PV 原始值 + 采样周期取哈希，确保辨识结果可追溯到具体输入数据。
    """
    buf = u.tobytes() + y.tobytes() + repr(float(ts)).encode()
    return hashlib.sha256(buf).hexdigest()[:16]


def _compute_parameter_uncertainty(
    a_coeffs: list[float],
    b_coeffs: list[float],
    d: int,
    res_var: float,
    u_train: np.ndarray,
    y_train: np.ndarray,
    sp_train: np.ndarray | None,
    method: IdentifyMethod,
    model_type: ModelType,
    ts: float,
) -> ParameterUncertainty | None:
    """P2-015：解析协方差 + Monte Carlo 传播计算参数 95% 置信区间.

    ARX:  cov(θ) = σ² · (ΦᵀΦ)⁻¹
    CLIVC: cov(θ) = σ² · (ZᵀΦ)⁻¹ · (ZᵀZ) · (ΦᵀZ)⁻¹
    ARMAX/IPDT: 无解析协方差（需 bootstrap），返回 None

    Monte Carlo 传播：从 N(θ̂, cov) 采样 200 次 → 转换为连续域参数 → 取 2.5/97.5 分位。
    """
    na = len(a_coeffs)
    nb = len(b_coeffs)
    n_params = na + nb
    max_lag = max(na, nb + d)
    rows = len(y_train) - max_lag
    if rows < n_params + 10 or res_var <= 0:
        return None

    # 构建回归矩阵 Φ（与 ARX/CLIVC 一致）
    Phi = np.zeros((rows, n_params))
    y_reg = np.zeros(rows)
    for i in range(rows):
        idx = max_lag + i
        for j in range(na):
            Phi[i, j] = -y_train[idx - 1 - j]
        for j in range(nb):
            Phi[i, na + j] = u_train[idx - d - j]
        y_reg[i] = y_train[idx]

    theta = np.array(a_coeffs + b_coeffs, dtype=float)

    if method == IdentifyMethod.HISTORICAL_ARX:
        # ARX: cov(θ) = σ² · (ΦᵀΦ)⁻¹
        PtP = Phi.T @ Phi
        try:
            cov_theta = res_var * np.linalg.inv(PtP)
        except np.linalg.LinAlgError:
            return None
    elif method == IdentifyMethod.HISTORICAL_IV:
        # CLIVC: cov(θ) = σ² · (ZᵀΦ)⁻¹ · (ZᵀZ) · (ΦᵀZ)⁻¹
        if sp_train is None:
            return None
        Z = np.zeros((rows, n_params))
        for i in range(rows):
            idx = max_lag + i
            for j in range(na):
                Z[i, j] = -sp_train[idx - 1 - j]
            for j in range(nb):
                Z[i, na + j] = sp_train[idx - d - j]
        ZtPhi = Z.T @ Phi
        ZtZ = Z.T @ Z
        try:
            ZtPhi_inv = np.linalg.inv(ZtPhi)
            cov_theta = res_var * ZtPhi_inv @ ZtZ @ ZtPhi_inv.T
        except np.linalg.LinAlgError:
            return None
    else:
        # ARMAX/IPDT/STEP：无解析协方差，需 bootstrap（后续按需补）
        return None

    # 数值稳定性：确保协方差矩阵正定
    try:
        cov_theta = 0.5 * (cov_theta + cov_theta.T)  # 对称化
        eigvals = np.linalg.eigvalsh(cov_theta)
        if np.min(eigvals) < 0:
            cov_theta = cov_theta + np.eye(n_params) * (abs(np.min(eigvals)) + 1e-12)
    except np.linalg.LinAlgError:
        return None

    # Monte Carlo 传播：采样 → 转换为连续域 → 置信区间
    rng = np.random.default_rng(42)
    n_mc = 200
    try:
        samples = rng.multivariate_normal(theta, cov_theta, size=n_mc)
    except (ValueError, np.linalg.LinAlgError):
        return None

    K_samples: list[float] = []
    tau_samples: list[float] = []
    theta_samples: list[float] = []
    t1_samples: list[float] = []
    t2_samples: list[float] = []
    for s in samples:
        s_a = s[:na].tolist()
        s_b = s[na:].tolist()
        try:
            if model_type == ModelType.FOPDT:
                p = arx_to_fopdt(s_a[0], s_b[0], d, ts)
                K_samples.append(p.K)
                tau_samples.append(p.tau)
                theta_samples.append(p.theta)
            elif model_type == ModelType.SOPDT and na >= 2:
                p = arx_to_sopdt(s_a[0], s_a[1], s_b[0], d, ts)
                K_samples.append(p.K)
                # T3（2026-10-01）：SOPDT 时间常数在 T1/T2（ModelParams.tau
                # 恒 0），采样进 tau 会产出恒 [0,0] 的假置信区间
                t1_samples.append(p.T1)
                t2_samples.append(p.T2)
                theta_samples.append(p.theta)
        except Exception:
            continue  # 不稳定采样点跳过

    n_valid = len(K_samples)
    if n_valid < 20:
        return None

    return ParameterUncertainty(
        K_ci_lower=float(np.percentile(K_samples, 2.5)),
        K_ci_upper=float(np.percentile(K_samples, 97.5)),
        tau_ci_lower=float(np.percentile(tau_samples, 2.5)) if tau_samples else 0.0,
        tau_ci_upper=float(np.percentile(tau_samples, 97.5)) if tau_samples else 0.0,
        theta_ci_lower=float(np.percentile(theta_samples, 2.5)),
        theta_ci_upper=float(np.percentile(theta_samples, 97.5)),
        t1_ci_lower=float(np.percentile(t1_samples, 2.5)) if t1_samples else 0.0,
        t1_ci_upper=float(np.percentile(t1_samples, 97.5)) if t1_samples else 0.0,
        t2_ci_lower=float(np.percentile(t2_samples, 2.5)) if t2_samples else 0.0,
        t2_ci_upper=float(np.percentile(t2_samples, 97.5)) if t2_samples else 0.0,
        n_mc_samples=n_valid,
    )


def _check_nonparam_consistency(
    k_param: float,
    k_rough: float | None,
) -> tuple[bool, str]:
    """P2-004：非参数一致性检查（符号 + 量级）.

    非参数相关分析给出的 K 粗估独立于 ARX 回归，用于交叉校验参数化结果：
    - 符号不一致 → 参数化模型可能拟合了错误方向（如闭环有偏）
    - 量级差超过一个数量级 → 参数化模型可能过拟合或数值病态

    Args:
        k_param: 参数化辨识增益 K
        k_rough: 非参数粗估增益（None 时跳过）

    Returns:
        (passed, reason_code) — reason_code 为空字符串表示通过
    """
    if k_rough is None or abs(k_rough) < 1e-9:
        return True, ""  # 无有效非参数估计，跳过
    if abs(k_param) < 1e-9:
        return False, "K_PARAM_ZERO"
    # 符号一致性
    if k_param * k_rough < 0:
        return False, "SIGN_MISMATCH"
    # 量级一致性（同一数量级，0.1×~10×）
    ratio = abs(k_param) / abs(k_rough)
    if ratio < 0.1 or ratio > 10.0:
        return False, "MAGNITUDE_MISMATCH"
    return True, ""


def _search_delay(
    u: np.ndarray,
    y: np.ndarray,
    na: int,
    nb: int,
    d_max: int,
) -> tuple[int, list[tuple[int, float]]]:
    """延迟候选搜索（P2-001）— 对 d=0..d_max 跑 ARX，用 BIC 选最优 d.

    BIC = n·ln(σ²) + k·ln(n)，其中 σ² 为残差方差，n 为样本数，k=na+nb。
    BIC 越小越好：惩罚模型复杂度，避免过大的 d 过拟合。

    Args:
        u: 输入信号（已去均值）
        y: 输出信号（已去均值）
        na: A 多项式阶次
        nb: B 多项式阶次
        d_max: 最大延迟候选（采样数）

    Returns:
        (best_d, search_trace) — search_trace = [(d, bic), ...] 供证据输出
    """
    # S3 修复（G27#1）：BIC 必须在**同一样本窗**上比较。
    #
    # BIC = n·ln(σ²) + k·ln(n) 的似然项随 n 线性缩放，只有 n 相同才可比。
    # 原实现对每个候选 d 各用 res.n_samples（d 越大回归行越少），跨样本量比较无效：
    # 实测（真值 d=8、1200 点、σ² 基本不变）"错误候选"的 BIC 随 d 单调变差
    # （d=0 时 −6059 → d=12 时 −5991），artifact 约每单位 d 5.7，
    # 与合法复杂度罚项 ln(n)≈7.1 量级相当——对大延迟的保守度约翻倍。
    #
    # 修法：取对所有候选都成立的公共窗 t0 = max(na, d_max+nb)，用各自拟合系数
    # 在该窗上重算残差方差，并以同一 n_common 计罚。
    n_total = len(y)
    t0 = max(na, d_max + nb)
    n_common = max(1, n_total - t0)
    k = na + nb

    best_d = 0
    best_bic = float("inf")
    search_trace: list[tuple[int, float]] = []
    for d in range(d_max + 1):
        try:
            res = identify_arx(u, y, d, na=na, nb=nb)
            sigma2 = _residual_var_on_common_window(u, y, res.a_coeffs, res.b_coeffs, d, t0)
            bic = n_common * math.log(max(sigma2, 1e-12)) + k * math.log(n_common)
            bic_rounded = round(bic, 2)
            search_trace.append((d, bic_rounded))
            if bic < best_bic:
                best_bic = bic
                best_d = d
        except Exception:
            search_trace.append((d, float("inf")))
            continue
    return best_d, search_trace


def _residual_var_on_common_window(
    u: np.ndarray,
    y: np.ndarray,
    a_coeffs: list[float],
    b_coeffs: list[float],
    d: int,
    t0: int,
) -> float:
    """在公共样本窗 [t0, N) 上重算方程误差残差方差（S3/G27#1）.

    回归约定与 identify_arx / cross_validate **严格一致**（后者给出了权威索引形式）：
        a[j]*y[t-1-j]、b[j]*u[t-d-j]，j 均从 0 起。

    注：首版实现把 b 的索引写成 u[t-d-j] 而 j 从 1 起（多减一拍），验证时真值 d
    落败；本版以 cross_validate 的写法为准。

    调用方保证 t0 >= max(na, d + nb)，故窗内索引恒非负。
    """
    na, nb = len(a_coeffs), len(b_coeffs)
    total = 0.0
    count = 0
    for t in range(t0, len(y)):
        pred = 0.0
        for j in range(na):
            pred -= a_coeffs[j] * y[t - 1 - j]
        for j in range(nb):
            pred += b_coeffs[j] * u[t - d - j]
        err = y[t] - pred
        total += err * err
        count += 1
    return total / count if count else float("inf")


def _free_run_simulation(
    u: np.ndarray,
    y: np.ndarray,
    a_coeffs: list[float],
    b_coeffs: list[float],
    d: int,
) -> tuple[np.ndarray, float]:
    """自由仿真（P2-002）— 用预测输出反馈计算留出集 R².

    自由仿真：ŷ(t) = -Σ a_j·ŷ(t-1-j) + Σ b_j·u(t-d-j)
    初始条件用真实 y 值（前 max_lag 个点），避免初始瞬态污染。

    与训练集方程误差 R² 的区别：
    - 方程误差用真实 y(t-1) 反馈，只检验回归拟合，偏乐观
    - 自由仿真用预测 ŷ(t-1) 反馈，误差会累积，检验泛化能力

    Args:
        u: 留出集输入
        y: 留出集输出
        a_coeffs: ARX A 多项式系数 [a1, a2, ...]
        b_coeffs: ARX B 多项式系数 [b1, b2, ...]
        d: 延迟（采样数）

    Returns:
        (y_pred, r2_free) — r2_free 为自由仿真 R²（[0, 1]）
    """
    na = len(a_coeffs)
    nb = len(b_coeffs)
    n = len(y)
    max_lag = max(na, nb + d)
    if n < max_lag + 5:
        return np.zeros(n), 0.0

    y_pred = np.zeros(n)
    # 初始条件：前 max_lag 个点用真实值
    y_pred[:max_lag] = y[:max_lag]

    for i in range(max_lag, n):
        val = 0.0
        for j in range(na):
            val -= a_coeffs[j] * y_pred[i - 1 - j]
        for j in range(nb):
            idx_u = i - d - j
            if idx_u >= 0:
                val += b_coeffs[j] * u[idx_u]
        y_pred[i] = val

    residuals = y[max_lag:] - y_pred[max_lag:]
    ss_res = float(np.sum(residuals**2))
    y_val = y[max_lag:]
    ss_tot = float(np.sum((y_val - np.mean(y_val)) ** 2))
    r2_free = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return y_pred, max(0.0, min(1.0, r2_free))


def _ipdt_regress(
    u: np.ndarray,
    y: np.ndarray,
    d: int,
) -> tuple[float, float, float, int] | None:
    """P2-008：IPDT 差分线性回归 dy(k) = b1·u(k-d).

    积分过程 G(s)=K·e^(-θs)/s 离散化（ZOH 近似）：
        y(k) - y(k-1) = K·ts·u(k-d)
    差分去积分器后做无截距最小二乘（数据已去均值），b1 = K·ts。

    Args:
        u: 输入（已去均值）
        y: 输出（已去均值）
        d: 延迟（采样数）

    Returns:
        (b1, residual_var, r2, n_samples) 或 None（数据不足）
    """
    dy = np.diff(y)  # dy[i] = y[i+1]-y[i], i=0..n-2
    n_dy = len(dy)
    # 对齐：dy[i] = b1·u[i+1-d]，要求 i+1-d >= 0 → i >= d-1（d>=1）或 i>=0（d=0）
    if d <= 0:
        y_dep = dy
        u_indep = u[1 : 1 + n_dy]
    else:
        start = d - 1
        if n_dy - start < 5:
            return None
        y_dep = dy[start:]
        u_indep = u[: len(y_dep)]
    m = len(y_dep)
    if m < 5 or len(u_indep) < m:
        return None
    denom = float(np.dot(u_indep, u_indep))
    if denom < 1e-12:
        return None
    b1 = float(np.dot(u_indep, y_dep) / denom)
    residuals = y_dep - b1 * u_indep
    res_var = float(np.var(residuals)) if m > 1 else 0.0
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_dep - np.mean(y_dep)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return b1, res_var, max(0.0, min(1.0, r2)), m


def _ipdt_free_run(
    u: np.ndarray,
    y: np.ndarray,
    b1: float,
    d: int,
) -> tuple[np.ndarray, float]:
    """P2-008：IPDT 验证集自由仿真 y_pred(k) = y_pred(k-1) + b1·u(k-d).

    积分过程自由仿真：预测输出累积积分，误差会随时间累积，
    是 IPDT 泛化能力的严格检验（K 偏差线性放大）。

    Returns:
        (y_pred, r2_free) — r2_free 为自由仿真 R²（可能为负，预测漂移时）
    """
    n = len(y)
    if n < d + 5:
        return np.zeros(n), 0.0
    y_pred = np.zeros(n)
    y_pred[0] = y[0]  # 初始条件用真实值
    for k in range(1, n):
        idx_u = k - d
        if idx_u >= 0:
            y_pred[k] = y_pred[k - 1] + b1 * u[idx_u]
        else:
            y_pred[k] = y_pred[k - 1]
    # R² 在全段（除首点）上计算；积分过程 ss_tot 较大，R² 对斜率敏感
    residuals = y[1:] - y_pred[1:]
    ss_res = float(np.sum(residuals**2))
    y_seg = y[1:]
    ss_tot = float(np.sum((y_seg - np.mean(y_seg)) ** 2))
    r2_free = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return y_pred, max(0.0, min(1.0, r2_free))


def _identify_ipdt_candidate(
    u_train: np.ndarray,
    y_train: np.ndarray,
    u_val: np.ndarray,
    y_val: np.ndarray,
    d_search_max: int,
    ts: float,
    theta_source: ThetaSource,
    exc,
    exc_score: float,
    K_rough: float | None,
    mean_coherence: float | None,
    n_train: int,
    n_val: int,
    n_test: int,
    data_hash: str,
    cleaning_stats: dict,
) -> CandidateModel | None:
    """P2-008：IPDT 历史辨识 G(s) = K·exp(-theta·s)/s.

    积分过程（如液位、累积量）PV 不回归稳态而是持续斜坡变化。
    差分去积分器后线性回归辨识 K 与 theta，复用 BIC 延迟搜索与留出集自由仿真。

    流程：
    1. 差分 y_train → dy，对 d=0..d_max 回归 dy=b1·u(k-d)，BIC 选最优 d
    2. K = b1/ts，theta = d·ts
    3. 验证集自由仿真 R²（积分累积误差）
    4. 物理可行性（负增益）/相干辅助门禁
    """
    # 1. 延迟搜索（BIC 准则，k=1 参数）
    best_d = 0
    best_bic = float("inf")
    delay_search_trace: list[tuple[int, float]] = []
    for d in range(d_search_max + 1):
        reg = _ipdt_regress(u_train, y_train, d)
        if reg is None:
            delay_search_trace.append((d, float("inf")))
            continue
        b1_d, res_var_d, _, n_d = reg
        bic = n_d * math.log(max(res_var_d, 1e-12)) + 1 * math.log(max(n_d, 2))
        bic_rounded = round(bic, 2)
        delay_search_trace.append((d, bic_rounded))
        if bic < best_bic:
            best_bic = bic
            best_d = d

    # 2. 最优延迟下辨识
    reg = _ipdt_regress(u_train, y_train, best_d)
    if reg is None:
        logger.debug("P2-008 IPDT 辨识失败：差分回归数据不足")
        return None
    b1, res_var, r2_train, n_samples = reg
    if abs(b1) < 1e-12:
        logger.debug("P2-008 IPDT 辨识失败：b1≈0，无积分关系")
        return None
    K = b1 / ts
    theta = best_d * ts
    params = ModelParams(model_type=ModelType.IPDT, K=K, theta=theta)

    # 3. 验证集自由仿真 R²（v1.2：val 段近稳态防护同主路径）
    y_val_pred, r2_val = _ipdt_free_run(u_val, y_val, b1, best_d)
    val_stationary = float(np.ptp(y_val)) < _VAL_STATIONARY_MIN_PTP
    r2_eval = r2_train if val_stationary else r2_val
    residuals_val_arr = y_val - y_val_pred
    y_val_range = float(np.ptp(y_val))
    if len(residuals_val_arr):
        rmse_val = float(np.sqrt(np.mean(residuals_val_arr**2)))
    else:
        rmse_val = 0.0
    nrmse_val = rmse_val / y_val_range if y_val_range > 1e-12 else 0.0

    # 4. 残差检验：IPDT 方程误差 = dy - b1·u(k-d)，检验与输入独立性
    dy_train = np.diff(y_train)
    if best_d <= 0:
        eq_err = dy_train - b1 * u_train[1 : 1 + len(dy_train)]
    else:
        eq_err = dy_train[best_d - 1 :] - b1 * u_train[: len(dy_train) - best_d + 1]
    exceed_ratio = _residual_input_exceed_ratio(eq_err, u_train)
    residual_white = exceed_ratio <= _XCORR_EXCEED_TOLERANCE
    test_note = f"xcorr_exceed={exceed_ratio:.3f}"

    # 5. 物理可行性（IPDT 无 NMP 零点概念，仅检负增益）
    feasibility = check_physical_feasibility(params, [b1], ts)
    physical_flag = "" if feasibility.passed else f", {feasibility.reason_code}"

    # 6. 可信度评估（v1.2：val 段近稳态回退 R²_train 并封顶 C）
    confidence = _assess_confidence(exc, r2_eval, residual_white, exc_score)
    if val_stationary:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
        physical_flag += ", VAL_STATIONARY"
    if theta_source == ThetaSource.HEURISTIC_2TS:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
    if not feasibility.passed:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
        physical_flag = f", {feasibility.reason_code}(K={K:.4g})"
    # P2-005：相干辅助门禁
    low_coherence = mean_coherence is not None and mean_coherence < _LOW_COHERENCE_THRESHOLD
    if low_coherence:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)

    # 7. AIC/BIC（k=1 参数）
    aic_val = compute_aic(n_samples, res_var, 1)
    bic_val = compute_bic(n_samples, res_var, 1)

    # 8. 证据输出
    reason_codes: list[str] = []
    if val_stationary:
        reason_codes.append("VAL_STATIONARY")
    if not feasibility.passed:
        reason_codes.append(feasibility.reason_code)
    if low_coherence:
        reason_codes.append("LOW_COHERENCE")
    if theta_source == ThetaSource.HEURISTIC_2TS:
        reason_codes.append("HEURISTIC_2TS")
    evidence = ModelEvidence(
        n_train=n_train,
        n_val=n_val,
        n_test=n_test,
        r2_val=round(r2_val, 4),
        r2_train=round(r2_train, 4),
        nrmse_val=round(nrmse_val, 4),
        residual_test_note=test_note,
        mean_coherence=mean_coherence,
        algorithm_version=ALGORITHM_VERSION,
        data_hash=data_hash,
        theta_source=theta_source.value,
        delay_search_trace=delay_search_trace,
        reason_codes=reason_codes,
        y_val_observed=[round(float(v), 6) for v in y_val],
        y_val_predicted=[round(float(v), 6) for v in y_val_pred],
        residuals_val=[round(float(v), 6) for v in residuals_val_arr],
        cleaning_stats=_evidence_cleaning_stats(cleaning_stats),
        physical_feasibility=_feasibility_dict(feasibility),
    )

    return CandidateModel(
        params=params,
        fitting_score=round(r2_eval * 100, 2),  # v1.2：val 稳态回退口径
        confidence=confidence,
        identify_method=IdentifyMethod.HISTORICAL_ARX,
        residual_test_passed=residual_white,
        excitation_score=exc_score,
        reason=(
            f"R²_val={r2_val:.3f}, R²_train={r2_train:.3f},"
            f" {test_note}, AIC={aic_val:.1f}, BIC={bic_val:.1f},"
            f" K={K:.4g}, θ={theta:.2g}s{physical_flag}"
        ),
        aic=round(aic_val, 2),
        bic=round(bic_val, 2),
        evidence=evidence,
    )


def _residual_input_exceed_ratio(
    residuals: np.ndarray,
    u: np.ndarray,
    max_lag: int = _XCORR_MAX_LAG,
) -> float:
    """残差-输入互相关越界率（P1-4，ARX/IV 模型充分性检验）.

    归一化互相关 ρ_k = Σ_t ε(t)·u(t−k) / (‖ε‖·‖u‖)，k = 0..max_lag；
    白噪声假设下 95% 置信界 ±1.96/√N。返回越界 lag 占比：
    残差仍含未建模输入动态（如有偏 ARX）时低阶 lag 大面积越界 → 占比趋 1；
    模型充分时仅随机越界 → 占比 ≈ 5%。

    Args:
        residuals: 残差序列（方程误差）
        u: 输入序列（已去均值）
        max_lag: 最大互相关滞后

    Returns:
        越界 lag 占比 0~1（数据不足或退化时返回 0.0，不拒绝）
    """
    n = len(residuals)
    if n < max_lag + 10 or len(u) < n:
        return 0.0
    # 残差第 i 点对应原序列索引 max_lag+i（回归起点之后），与 u 尾部对齐
    u = u[-n:]
    r = residuals - float(np.mean(residuals))
    uu = u - float(np.mean(u))
    denom = math.sqrt(float(np.sum(r * r) * np.sum(uu * uu)))
    if denom < 1e-12:
        return 0.0
    bound = 1.96 / math.sqrt(n)
    exceed = 0
    for k in range(max_lag + 1):
        cov = float(np.dot(r[k:], uu[: n - k])) if k else float(np.dot(r, uu))
        if abs(cov / denom) > bound:
            exceed += 1
    return exceed / (max_lag + 1)


def _equation_error_to_prediction_error(
    equation_error: np.ndarray,
    c_coeffs: list[float],
) -> np.ndarray:
    """方程误差 e = A·y − B·u 经 1/C 反滤波得一步预测误差 ε（ARMAX：e = C·ε）.

    递推：ε(t) = e(t) − Σ_j c_j·ε(t−j)，初始 ε(t<0) = 0。

    Args:
        equation_error: 方程误差序列 e(t) = A·y − B·u
        c_coeffs: ARMAX C 多项式系数 [c1, c2, ...]（C = 1 + c1·z⁻¹ + ...）

    Returns:
        一步预测误差序列 ε(t)
    """
    eps = np.zeros_like(equation_error)
    for i in range(len(equation_error)):
        val = equation_error[i]
        for j, cj in enumerate(c_coeffs):
            if i - 1 - j >= 0:
                val -= cj * eps[i - 1 - j]
        eps[i] = val
    return eps


def _assess_confidence(
    exc,
    r2: float,
    residual_white: bool,
    exc_score: float,
) -> AlgorithmConfidenceLevel:
    """可信度综合评估."""
    if not exc.is_sufficient:
        return AlgorithmConfidenceLevel.INCONCLUSIVE
    if r2 >= _R2_A and residual_white and exc_score >= 60:
        return AlgorithmConfidenceLevel.A
    if r2 >= _R2_B and residual_white:
        return AlgorithmConfidenceLevel.B
    if r2 >= _R2_C:
        return AlgorithmConfidenceLevel.C
    if r2 >= _R2_D:
        return AlgorithmConfidenceLevel.D
    return AlgorithmConfidenceLevel.E


def _candidate_better(a: CandidateModel, b: CandidateModel) -> bool:
    """a 是否优于 b（可信度优先，其次拟合度）."""
    return _candidate_sort_key(a) > _candidate_sort_key(b)


def _select_with_occam(results: list[CandidateModel]) -> CandidateModel:
    """P2-006 Occam 削减：在 sort_key 选优基础上对 SOPDT 升级施加门禁.

    规则（对齐 order_selection.py 文档）：SOPDT 优于 FOPDT 当且仅当
    R²_val 相对提升 > 5% 且 BIC 下降。否则保留更简单的 FOPDT。

    仅当 sort_key 选出的最优为 SOPDT 且存在 FOPDT 候选时触发；
    其余情况（FOPDT 最优 / 无 FOPDT 候选 / BIC 缺失）直接返回 sort_key 最优。

    Args:
        results: 各 model_type 的最优候选列表

    Returns:
        Occam 削减后的最优候选
    """
    best = max(results, key=_candidate_sort_key)
    if best.params.model_type != ModelType.SOPDT:
        return best
    fopdt_candidates = [c for c in results if c.params.model_type == ModelType.FOPDT]
    if not fopdt_candidates:
        return best
    fopdt = max(fopdt_candidates, key=_candidate_sort_key)
    # SOPDT 须显著更优：R²_val 相对提升 > 5% 且 BIC 下降
    r2_gain = (best.fitting_score - fopdt.fitting_score) / max(fopdt.fitting_score, 1.0)
    bic_lower = best.bic is not None and fopdt.bic is not None and best.bic < fopdt.bic
    if r2_gain > _OCCAM_R2_RELATIVE_GAIN and bic_lower:
        return best
    # Occam：SOPDT 未显著优于 FOPDT，保留简单模型
    logger.debug(
        "P2-006 Occam 削减：SOPDT R²_gain=%.1f%% BIC=%s vs FOPDT BIC=%s → 选 FOPDT",
        r2_gain * 100,
        best.bic,
        fopdt.bic,
    )
    return fopdt


def _candidate_sort_key(c: CandidateModel) -> tuple:
    """排序键：可信度等级 > 拟合度 > 残差检验."""
    level_order = {
        AlgorithmConfidenceLevel.A: 5,
        AlgorithmConfidenceLevel.B: 4,
        AlgorithmConfidenceLevel.C: 3,
        AlgorithmConfidenceLevel.D: 2,
        AlgorithmConfidenceLevel.E: 1,
        AlgorithmConfidenceLevel.INCONCLUSIVE: 0,
    }
    return (level_order.get(c.confidence, 0), c.fitting_score, c.residual_test_passed)


def _cap_confidence(
    confidence: AlgorithmConfidenceLevel,
    maximum: AlgorithmConfidenceLevel,
) -> AlgorithmConfidenceLevel:
    """将可信度限制在 maximum，不提升原有低可信度结果."""
    level_order = {
        AlgorithmConfidenceLevel.A: 5,
        AlgorithmConfidenceLevel.B: 4,
        AlgorithmConfidenceLevel.C: 3,
        AlgorithmConfidenceLevel.D: 2,
        AlgorithmConfidenceLevel.E: 1,
        AlgorithmConfidenceLevel.INCONCLUSIVE: 0,
    }
    if level_order[confidence] > level_order[maximum]:
        return maximum
    return confidence
