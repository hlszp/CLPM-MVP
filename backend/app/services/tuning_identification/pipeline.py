"""算法栈编排（层 1→6 串联）.

入口函数 identify_from_history：接收 OP/PV/SP/MODE 时序，
执行激励检测→非参数粗估→参数化辨识→阶次选择→离散转换→可信度评估，
返回 IdentificationResult。
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Final

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
from app.services.tuning_identification.segmentation import (
    SEG_MIN_POINTS_FLOOR as _SEG_MIN_FLOOR,
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

# 算法版本单一真相源在 types.IdentificationResult
# （v1.5=2026-10-12 P3-06a 正确性子卡：R1 时滞物理边界/R6 粗增益校验降级/
# R7 IPDT 漂移项/R4+R5 final-test 三段分数与终验四态/R17 估计身份证据；
# v1.6=2026-10-12 P3-06b 片段与时间尺度子卡：R2 合格连续段全保留（废弃
# 大缺口取最长段）/P-12 段最小点数物理化/P-14 段目录字段/P-16 信息量通道/
# P-17 自然事件锚点窗/P-19 采样分辨率标记/O-A6 时滞边界命中出口/
# B-05~B-08 搜索预算与四类审计落字段）
ALGORITHM_VERSION = "TUNE_IDENT_v1.6"

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

# ── P3-06a（2026-10-12，oracle 冻结草案 §2 裁决值，一字不差）──
# R1（P-02）时滞物理搜索上界：θ_max = max(300s, 5×非参粗估 τ+θ)，且 ≤ 数据时长 1/4
_DELAY_PHYS_MIN_SECONDS = 300.0  # P-02 下限 300s
_DELAY_PHYS_TAU_FACTOR = 5.0  # P-02：5 × 粗估(τ+θ)
_DELAY_PHYS_DURATION_QUARTER = 4.0  # P-02：上界 ≤ 数据时长 / 4
# B-01 时滞候选数上限：物理秒范围换算后候选数 ≤ 241（含 d=0）；
# 超过则按对数网格稀疏化并在 trace/审计记录稀疏化事实
_DELAY_CANDIDATE_CAP = 241
# R5（P-11）终验状态枚举（四态，冻结值）
FINAL_TEST_PASSED = "PASSED"
FINAL_TEST_FAILED = "FAILED"
FINAL_TEST_NO_DYNAMICS = "NO_DYNAMICS"
FINAL_TEST_NOT_RUN = "NOT_RUN"
# 终验 PASSED 判据：final-test 自由仿真 R² ≥ _R2_D（与可信度 D 级同阈；
# 有动态但解释力不及 D 级 = 终验失败，可信度封顶 C 不晋升）
_FINAL_TEST_MIN_R2 = 0.50
# R6（P-06）OP 近白判定：去均值自相关 |ρ_k| ≤ 此值（k=1..max_lag）视为近白
# ——相关分析"互相关 ∝ 脉冲响应"仅对白输入成立（nonparametric.py 自注），
# 近白时 K 粗估有效，粗增益校验作为封顶信号；有色（闭环）时降级记录不封顶
_OP_NEAR_WHITE_MAX_ABS_RHO = 0.3
_OP_NEAR_WHITE_MAX_LAG = 10

# ── P3-06b（2026-10-12，oracle 冻结草案 §3 冻结值，一字不差）──
# P-16 信息量通道（幅度通道保留 v1.4 判据，两通道并联入候选评分；具体
# 公式草案留白待定——本实现取候选三选之首"累计运动量 Σ|ΔOP|"，见交接
# 裁量登记）：累计运动量 = Σ|ΔOP| / OP 量程（0~100 归一化域，量程倍数）；
# 低于门槛的窗按信息量通道拒绝（保持/慢变化输入不进候选池，O-B2）。
_WINDOW_INFO_MIN_MOVEMENT = 0.02  # 信息量门槛：累计运动 ≥ 2% 量程（保持输入≈0 恒拒）
_WINDOW_INFO_NORM_FULL = 4.0  # 信息量归一饱和点（4 倍量程累计运动 = 评分满分）
_WINDOW_INFO_WEIGHT = 5.0  # 信息量通道评分权重（并联加成，量级≈幅度通道贡献）
# P-17 自然事件锚点窗：SP 阶跃事件检测（前/后局部均值差 > 阈值；归一化域
# 1% 量程级，容忍斜坡保持型阶跃）与 MODE 变更事件；事件窗并入统一候选池。
_EVENT_SP_STEP_MIN = 1.0  # SP 事件检测门：前后 flank 均值差（归一化域单位）
_EVENT_FLANK_POINTS = 60  # SP 事件前后 flank 局部均值窗（点）
_EVENT_WINDOW_MIN_POINTS = 600  # 事件窗最短点数（沿用 v1.4 窗口径 _AUTOWIN_MIN_POINTS）
# B-07 冻结窗长档 {120,60,30}min——单窗时长上限 120min（整段候选与事件窗
# 均受此上限约束）
_WINDOW_MAX_MINUTES = 120.0
_WINDOW_MINUTES_LADDER: Final[tuple[float, ...]] = (120.0, 60.0, 30.0)
# B-05 候选窗总数上限（滑窗网格 + 事件窗 + 整段候选合并去重后；超限裁剪
# 记录进预算终态字段）；B-06 互不重叠入选窗数 k=3（v1.4 既有值保持）。
_WINDOW_POOL_CAP = 64
_MULTI_WINDOW_MAX_PICK = 3


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


def _interp_small_gaps(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    max_interp_gap: int = _MAX_INTERP_GAP,
) -> tuple[int, list[tuple[int, int]]]:
    """P2-019 小缺口线性插值（原地修改；P3-06b R2 从 _clean_nan_segments 抽取）.

    OP/PV 任一为 NaN/Inf 视为坏点；SP 的坏点同步纳入。小缺口（连续坏点
    < max_interp_gap 且两端有有效值）线性插值填补；其余（大缺口/端点坏段）
    原样保留 NaN 并返回其区间列表——**不做任何截断或拼接**（R2：全部合格
    连续段保留，段切分由段目录构建器按大缺口边界处理）。

    Returns:
        (interpolated_points, large_gaps)：插值点数与大缺口 [(start, end)]
        区间列表（闭区间，坏点段）。原始数据无坏点时为 (0, [])。
    """
    n = len(u)
    bad = ~np.isfinite(u) | ~np.isfinite(y)
    # OP/PV 无坏点即原样返回（legacy 语义：SP 独有 NaN 不触发清洗——
    # 该场景 CLIVC 因激励检测不过而不启用，与原 _clean_nan_segments 一致）
    if int(bad.sum()) == 0:
        return 0, []
    if sp is not None:
        bad = bad | ~np.isfinite(sp)

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
    return interpolated, large_gaps


def _clean_nan_segments(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    max_interp_gap: int = _MAX_INTERP_GAP,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, dict | None]:
    """P2-019 坏点清洗（legacy 单序列口径）：小缺口插值 + 大缺口取最长段.

    P3-06b（R2）：正式管线已改为段目录全保留（见 _build_segment_catalog），
    本函数保留 legacy 截断口径供既有单元测试与工具路径使用，行为不变。

    策略：
    1. 小缺口（连续坏点 < max_interp_gap 且两端有有效值）：线性插值填补；
    2. 大缺口（连续坏点 ≥ max_interp_gap 或在端点）：按大缺口切分，取最长连续有效段；
    3. 清洗后点数 < 1 时返回 None（由调用方再判 < 50）。

    Returns:
        (u_clean, y_clean, sp_clean, stats)
        stats: {original_points, interpolated_points, dropped_points,
                valid_points, valid_rate, n_large_gaps}
        若原始数据无坏点，stats 的 interpolated/dropped 均为 0。
        sp_clean 为 None 当且仅当输入 sp 为 None。
    """
    n = len(u)
    interpolated, large_gaps = _interp_small_gaps(u, y, sp, max_interp_gap)
    if not large_gaps and interpolated == 0:
        stats = {
            "original_points": n,
            "interpolated_points": 0,
            "dropped_points": 0,
            "valid_points": n,
            "valid_rate": 1.0,
            "n_large_gaps": 0,
        }
        return u, y, sp, stats

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
# P3-06b（2026-10-12 片段与时间尺度子卡，oracle 冻结草案 §3 冻结值）：
# 段目录全保留（R2/P-13/P-14）/ 信息量通道（P-16）/ 自然事件锚点窗（P-17）
# / 物理时间与抗混叠（P-12/P-19）/ 搜索预算与四类审计（B-05~B-08/B-04）。
# ---------------------------------------------------------------------------


def _rough_tau_theta_for_catalog(
    u: np.ndarray,
    y: np.ndarray,
    ts: float,
) -> float | None:
    """段目录用非参粗估 τ+θ（P-12 的 τ_预期/θ_max 输入；仅用于门槛折算）.

    在最长有限连续段上做相关分析（缺口段不参与——不跨 gap 造统计）。
    失败/不可得返回 None（P-12 按可确定部分折算）。
    """
    finite = np.isfinite(u) & np.isfinite(y)
    segments = _find_contiguous_segments(finite)
    if not segments:
        return None
    best_start, best_end = max(segments, key=lambda s: s[1] - s[0])
    if best_end - best_start + 1 < 50:
        return None
    try:
        nonparam = correlation_analysis(
            u[best_start : best_end + 1], y[best_start : best_end + 1], ts
        )
        est = nonparam.time_constant_estimate
        return float(est) if est is not None and math.isfinite(est) and est > 0 else None
    except Exception:
        return None


def _segment_theta_max_seconds(
    total_points: int,
    ts: float,
    rough_tau_theta: float | None,
) -> float:
    """P-12 的 θ_max（R1/P-02 公式在段目录时点的可确定部分）.

    max(300s, 5×粗估 τ+θ)，且 ≤ 数据时长/4；粗估不可得时取 300s 下限
    与时长/4 的较小者（P-02 公式逐字）。
    """
    duration_s = total_points * ts
    rough = 5.0 * max(0.0, rough_tau_theta or 0.0)
    return min(max(_DELAY_PHYS_MIN_SECONDS, rough), duration_s / _DELAY_PHYS_DURATION_QUARTER)


def _window_info_metric(u_slice: np.ndarray) -> float:
    """P-16 信息量通道：累计运动量 Σ|ΔOP| / OP 量程（量程倍数，无量纲）.

    保持输入（OP 恒定）≈0；白输入随窗长线性增长；自然单阶跃=单次阶跃幅。
    OP 量程取归一化域 100（V62-P1-009：生产链 OP 已归一化 0~100，算法栈
    不假设输入量纲时由包装层传入——本度量与 check_excitation 的 op_span
    同口径）。
    """
    if len(u_slice) < 2:
        return 0.0
    return float(np.sum(np.abs(np.diff(u_slice)))) / _OP_SPAN_NORMALIZED


def _window_direction_changes(u_slice: np.ndarray) -> int:
    """P-16 信息量通道·方向丰富度：死区过滤后的 OP 方向变化数.

    与 excitation._OP_DIRECTION_DEADBAND_RATIO 同口径（死区=1%×窗内量程）
    ——纯单调输入（慢斜坡/单次保持阶跃）无方向变化，频谱不丰富，模型
    参数（尤其 τ）不可分辨（O-B2"慢变化按信息量通道正确拒绝或降级"）。
    """
    if len(u_slice) < 3:
        return 0
    du = np.diff(u_slice)
    u_range = float(np.max(u_slice) - np.min(u_slice))
    deadband = max(1e-9, 0.01 * u_range)
    du_sig = du[np.abs(du) > deadband]
    if len(du_sig) <= 1:
        return 0
    return int(np.sum(np.diff(np.sign(du_sig)) != 0))


def _window_info_gate(u_slice: np.ndarray) -> tuple[bool, dict]:
    """P-16 信息量通道门槛（并联于幅度通道）：运动量 + 方向丰富度 + 训练段信息.

    拒绝条件（任一）：
    - 累计运动量 < _WINDOW_INFO_MIN_MOVEMENT（保持输入）；
    - 方向变化数 = 0（纯单调：慢斜坡/单保持阶跃——无频谱丰富度）；
    - 训练段（前 60%，与 60/20/20 分割一致）累计运动量不足——动态全部
      落在验证/终验段的窗无训练信息（回归矩阵退化，"回归条件数"信息量
      候选的工程等价判据；O-B3 教训：train 死水窗上 τ≈0 退化候选凭
      瞬跳拟合验证段胜出）。
    返回 (passed, {movement, directionChanges, trainMovement})。
    """
    movement = _window_info_metric(u_slice)
    changes = _window_direction_changes(u_slice)
    n = len(u_slice)
    train_movement = _window_info_metric(u_slice[: max(1, int(n * 0.6))])
    passed = (
        movement >= _WINDOW_INFO_MIN_MOVEMENT
        and changes >= 1
        and train_movement >= _WINDOW_INFO_MIN_MOVEMENT
    )
    return passed, {
        "movement": round(movement, 6),
        "directionChanges": int(changes),
        "trainMovement": round(train_movement, 6),
    }


def _info_score_addon(info: float) -> float:
    """信息量通道评分加成（P-16"两通道并联入候选评分"）.

    归一 [0,1]（4 倍量程累计运动饱和）× 权重 5（与幅度通道 ptp 值同量级，
    不喧宾夺主）。保持输入 info≈0 → 加成≈0（另有硬门拒绝）。
    """
    return _WINDOW_INFO_WEIGHT * min(1.0, max(0.0, info) / _WINDOW_INFO_NORM_FULL)


def _detect_natural_events(
    sp: np.ndarray | None,
    mode: list[int] | None,
    n: int,
) -> list[dict]:
    """P-17 自然事件检测：SP 阶跃（含斜坡保持型）与 MODE 变更.

    - SP 阶跃：位置 k 的前/后 flank 局部均值差 |mean(sp[k+1:k+F]) −
      mean(sp[k−F:k])| > _EVENT_SP_STEP_MIN（归一化域 1% 量程级）。
      flank 均值比较天然容忍斜坡保持型变更（斜坡期间两侧均值差单调
      上升越阈，取越阈段中点为锚点），避免逐点阈值对慢斜坡失明；
    - MODE 变更：mode 序列值变化点。

    Returns:
        [{eventId, index, type∈{SP_STEP, MODE_CHANGE}, stepSize}] 按位置序。
        SP 缺失/全 NaN 时仅检测 MODE 事件。
    """
    events: list[dict] = []
    ev_no = 0

    def _add(index: int, ev_type: str, step: float) -> None:
        nonlocal ev_no
        ev_no += 1
        events.append(
            {
                "eventId": f"EVT-{ev_no:03d}",
                "index": int(index),
                "type": ev_type,
                "stepSize": round(float(step), 4),
            }
        )

    if sp is not None and len(sp) == n:
        s = np.where(np.isfinite(sp), sp, np.nan)
        if bool(np.isfinite(s).any()):
            f = min(_EVENT_FLANK_POINTS, max(3, n // 4))
            # 前/后 flank 滑动均值（cumsum 向量化；NaN 传播为 NaN 均值→跳过）
            cs = np.nancumsum(s)

            def _mean(rng_lo: int, rng_hi: int) -> float:
                lo, hi = max(0, rng_lo), min(n, rng_hi)
                if hi <= lo:
                    return float("nan")
                prev = cs[lo - 1] if lo > 0 else 0.0
                return float((cs[hi - 1] - prev) / (hi - lo))

            diffs = np.full(n, np.nan)
            for k in range(f, n - f):
                m_post = _mean(k + 1, k + 1 + f)
                m_pre = _mean(k - f, k)
                if math.isfinite(m_post) and math.isfinite(m_pre):
                    diffs[k] = m_post - m_pre
            above = np.isfinite(diffs) & (np.abs(diffs) > _EVENT_SP_STEP_MIN)
            # 越阈连续段取 |diff| 最大处为锚点（斜坡保持型：峰值≈斜坡末端）
            k = 0
            while k < n:
                if above[k]:
                    run_end = k
                    while run_end + 1 < n and above[run_end + 1]:
                        run_end += 1
                    peak = k + int(np.argmax(np.abs(diffs[k : run_end + 1])))
                    _add(peak, "SP_STEP", diffs[peak])
                    k = run_end + 1
                else:
                    k += 1

    if mode is not None and len(mode) == n:
        m = np.array(mode, dtype=float)
        valid = np.isfinite(m)
        m_int = np.where(valid, np.round(m).astype(int), -1)
        changes = np.where(np.diff(m_int) != 0)[0] + 1
        for c in changes:
            _add(int(c), "MODE_CHANGE", float(m_int[c] - m_int[c - 1]))
    return events


def _event_window_bounds(
    event_index: int,
    seg_start: int,
    seg_end: int,
    ts: float,
) -> tuple[int, int] | None:
    """P-17/B-07 事件窗边界：段内最大适用窗长，事件位 25%/75% 分位锚定.

    窗长取 {120,60,30}min 中段内放得下的最大档（B-07：事件窗按事件边界，
    窗长仍取自适应尺度）；前置 25%、后置 75%（阶跃响应主体在事件后），
    裁剪到段边界；窗长 < _EVENT_WINDOW_MIN_POINTS 点返回 None（不硬凑）。
    """
    dur_minutes = (seg_end - seg_start) * ts / 60.0
    win_minutes = [m for m in _WINDOW_MINUTES_LADDER if m <= dur_minutes]
    if not win_minutes:
        return None
    w = int((win_minutes[0] * 60.0) / ts)  # 最大适用档
    if w < _EVENT_WINDOW_MIN_POINTS:
        return None
    pre = int(w * 0.25)
    start = max(seg_start, event_index - pre)
    end = min(seg_end, start + w)
    start = max(seg_start, end - w)
    if end - start < _EVENT_WINDOW_MIN_POINTS:
        return None
    return start, end


def _scan_windows_in_segment(
    u: np.ndarray,
    y: np.ndarray,
    ts: float,
    seg_start: int,
    seg_end: int,
    segment_id: str,
) -> tuple[list[dict], list[dict]]:
    """P-16/P-17 段内有界窗扫描：幅度通道（v1.4 判据保留）+ 信息量通道.

    启动条件（v1.4 不变，作用于**段**的验证位）：段 val 位（60%~80%）
    PV ptp ≥ 1.5% 量程（段验证段有动态）→ 无需挖子窗（整段候选由
    _collect_window_pool 生成）。

    每窗条件（幅度通道，v1.4 冻结判据）：窗 PV ptp ≥ 3%、窗 val 位
    ptp ≥ 1.5%、≥600 点；窗长 {120,60,30}min 自适应、步长 W/6；
    信息量通道（P-16 新增）：窗累计运动量 < _WINDOW_INFO_MIN_MOVEMENT
    拒绝（保持/慢变化输入不进候选池）。
    评分 = val 位 ptp + 0.5×整体 ptp（幅度，v1.4）+ 信息量加成（P-16 并联）。

    窗只从段内 [seg_start, seg_end) 切——跨 gap 造动态结构性不可能
    （契约红线：不跨 gap 拼接）。

    Returns:
        (candidates, rejected)：candidates=[{start,end,score,valPtp,segPtp,
        info,source,segmentId}]；rejected=[{start,end,reason}]（信息量拒绝
        的窗，进搜索审计 rejected 清单）。
    """
    n = seg_end - seg_start
    candidates: list[dict] = []
    rejected: list[dict] = []
    val_lo = seg_start + int(n * 0.6)
    val_hi = seg_start + int(n * 0.8)
    if float(np.ptp(y[val_lo:val_hi])) >= _VAL_STATIONARY_MIN_PTP:
        return candidates, rejected  # 段验证位有动态：无需挖子窗（整段候选覆盖）

    dur_minutes = n * ts / 60.0
    win_minutes = [m for m in _WINDOW_MINUTES_LADDER if m <= dur_minutes / 2.0]
    for wm in win_minutes:
        w = int(wm * 60.0 / ts)
        if w < _AUTOWIN_MIN_POINTS:
            continue
        step = max(1, w // 6)
        for start in range(seg_start, seg_end - w + 1, step):
            seg = y[start : start + w]
            seg_ptp = float(np.ptp(seg))
            if seg_ptp < _AUTOWIN_MIN_FULL_PTP:
                continue
            v_lo = start + int(w * 0.6)
            v_hi = start + int(w * 0.8)
            v_ptp = float(np.ptp(y[v_lo:v_hi]))
            if v_ptp < _VAL_STATIONARY_MIN_PTP:
                continue
            info_passed, info_metrics = _window_info_gate(u[start : start + w])
            if not info_passed:
                rejected.append(
                    {
                        "start": int(start),
                        "end": int(start + w),
                        "reason": "INSUFFICIENT_INFO",
                        "info": info_metrics["movement"],
                        "directionChanges": info_metrics["directionChanges"],
                        "segmentId": segment_id,
                    }
                )
                continue
            candidates.append(
                {
                    "start": int(start),
                    "end": int(start + w),
                    "score": v_ptp + 0.5 * seg_ptp + _info_score_addon(info_metrics["movement"]),
                    "valPtp": round(v_ptp, 4),
                    "segPtp": round(seg_ptp, 4),
                    "info": info_metrics["movement"],
                    "source": "SLIDING_SCAN",
                    "segmentId": segment_id,
                    "eventId": None,
                }
            )
    return candidates, rejected


def _segment_whole_candidate(
    u: np.ndarray,
    y: np.ndarray,
    seg_start: int,
    seg_end: int,
    ts: float,
    segment_id: str,
) -> tuple[dict | None, dict | None]:
    """整段候选（R2：合格连续段本身进候选池；段验证位有动态时）.

    段作为天然边界的窗；窗长受 B-07 冻结上限约束——单窗时长上限
    {120,60,30}min 的最大档 120min，超过 120min 的段不产生整段候选
    （长段的动态密度被平坦段稀释——单事件长稳态段整窗回归会把极点
    拉向原点、时滞被 θ–τ 折衷吸收；长段由滑窗/事件窗覆盖）。
    沿用 v1.4 窗口径 ≥600 点、PV ptp ≥3%、val 位 ≥1.5%（幅度通道）+
    信息量通道门槛与评分。验证位死水的段返回 (None, None)（滑窗扫描
    负责从中挖动态子窗）；幅度过关而信息量不足的段返回 (None, 拒绝
    记录)——信息量拒绝显式入审计 rejected 清单（不静默）。
    """
    n = seg_end - seg_start
    if n < _AUTOWIN_MIN_POINTS:
        return None, None
    if n * ts / 60.0 > _WINDOW_MAX_MINUTES:
        return None, None
    seg = y[seg_start:seg_end]
    seg_ptp = float(np.ptp(seg))
    if seg_ptp < _AUTOWIN_MIN_FULL_PTP:
        return None, None
    v_lo = seg_start + int(n * 0.6)
    v_hi = seg_start + int(n * 0.8)
    v_ptp = float(np.ptp(y[v_lo:v_hi]))
    if v_ptp < _VAL_STATIONARY_MIN_PTP:
        return None, None
    info_passed, info_metrics = _window_info_gate(u[seg_start:seg_end])
    if not info_passed:
        return None, {
            "start": int(seg_start),
            "end": int(seg_end),
            "reason": "INSUFFICIENT_INFO",
            "info": info_metrics["movement"],
            "directionChanges": info_metrics["directionChanges"],
            "segmentId": segment_id,
        }
    return {
        "start": int(seg_start),
        "end": int(seg_end),
        "score": v_ptp + 0.5 * seg_ptp + _info_score_addon(info_metrics["movement"]),
        "valPtp": round(v_ptp, 4),
        "segPtp": round(seg_ptp, 4),
        "info": info_metrics["movement"],
        "source": "SEGMENT_WHOLE",
        "segmentId": segment_id,
        "eventId": None,
    }, None


def _build_segment_catalog(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    mode: list[int] | None,
    op_limits: tuple[float, float] | None,
    ts: float,
) -> tuple[list[dict], list[dict]]:
    """R2/P-13/P-14：段目录构建——全部合格连续段保留进候选池.

    输入为**小缺口已插值**的序列（大缺口/端点坏段仍为 NaN）——
    segment_signals 在 NaN 边界切分，段为纯有限段（跨 gap 拼接结构性
    不可能）。P-12 物理最小点数为首选档（tier1）；点数 ≥ P-12 下限 50
    但低于物理公式的段按 tier2 降级保留（P-12 字面 max(50, ·) 的下限档；
    保证 C12b-ID"激励段不被丢弃"——见交接裁量登记）。

    Returns:
        (catalog, qualified)：catalog=全部段记录（含排除原因，供审计与
        manifest 第 2 层）；qualified=合格段记录（tier1/tier2，含激励分
        与信息量评分，供主选段与窗池消费）。
    """
    from app.services.tuning_identification.segmentation import segment_signals

    op_min, op_max = op_limits if op_limits is not None else (None, None)
    rough = _rough_tau_theta_for_catalog(u, y, ts)
    theta_max_s = _segment_theta_max_seconds(len(y), ts, rough)
    specs = segment_signals(
        y.tolist(),
        u.tolist(),
        mode=mode,
        op_min=op_min,
        op_max=op_max,
        ts=ts,
        theta_max_seconds=theta_max_s,
        tau_expected_seconds=rough,
    )
    catalog: list[dict] = []
    qualified: list[dict] = []
    for spec in specs:
        # tier2 重分类：仅因 TOO_SHORT 被排除、点数 ≥ P-12 下限 50 的段
        # 降级保留（物理首选档不满足，但未低于 P-12 的绝对下限）
        tier = spec.qualify_tier
        exclusion = spec.exclusion_reason
        if exclusion == "TOO_SHORT" and spec.point_count >= _SEG_MIN_FLOOR:
            tier = 2
            exclusion = None
        record = {
            "segmentId": spec.segment_id or f"SEG-{len(catalog) + 1:03d}",
            "indexStart": int(spec.start_idx),
            "indexEnd": int(spec.end_idx),
            "tsStart": spec.ts_start,
            "tsEnd": spec.ts_end,
            "durationSeconds": (
                round(spec.ts_end - spec.ts_start, 4)
                if spec.ts_start is not None and spec.ts_end is not None
                else None
            ),
            "pointCount": int(spec.point_count),
            "validSampleRatio": spec.valid_sample_ratio,
            "mode": spec.mode_label,  # 工况未知显式 "UNKNOWN"（07§3.2 第 2 层）
            "regimeId": spec.regime_id,  # None=未知，不伪填
            "pidVersion": spec.pid_version,  # None=未知，不伪填
            "sampleInterval": spec.sample_interval,
            "exclusionReason": exclusion,
            "qualifyTier": tier,
        }
        if exclusion is None:
            su = u[spec.start_idx : spec.end_idx]
            sy = y[spec.start_idx : spec.end_idx]
            try:
                exc = check_excitation(su, sy, 1, op_span=_OP_SPAN_NORMALIZED)
                exc_score = excitation_score(exc.condition_number, exc.significant_changes)
                exc_sufficient = bool(exc.is_sufficient)
            except Exception:
                exc_score = 0.0
                exc_sufficient = False
            record["excitationScore"] = round(float(exc_score), 2)
            record["excitationSufficient"] = exc_sufficient
            record["infoMovement"] = round(_window_info_metric(su), 6)
            qualified.append(record)
        catalog.append(record)
    return catalog, qualified


def _collect_window_pool(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    mode: list[int] | None,
    catalog: list[dict],
    qualified: list[dict],
    ts: float,
) -> dict:
    """P-16/P-17/B-05~B-07：统一候选窗池（滑窗 + 事件窗 + 整段候选）.

    对每个合格段（tier1/tier2）：段验证位死水 → 滑窗扫描；段验证位有
    动态 → 整段候选；段内自然事件（SP 阶跃/MODE 变更）→ 事件锚点窗。
    合并去重（同 (start,end) 取高分并记录来源），按 B-05 cap=64 截断
    （超限窗进 rejected 清单，预算终态记录截断事实）。

    Returns:
        搜索审计记录（B-04 四类清单）：{ran, scanGrid, windowsChecked,
        windowsSelected, windowsRejected, notSearched, budgetTermination,
        selectedTuple}（selectedTuple=评分降序的 (score,start,end,v,s)
        元组列表，供 _collect_disjoint_windows 选 k=3 互不重叠窗）。
    """
    events = _detect_natural_events(sp, mode, len(y))
    pool: dict[tuple[int, int], dict] = {}
    rejected: list[dict] = []
    not_searched: list[dict] = []

    for rec in catalog:
        reason = rec.get("exclusionReason")
        if reason is not None:
            not_searched.append(
                {
                    "start": rec["indexStart"],
                    "end": rec["indexEnd"],
                    "reason": f"SEGMENT_EXCLUDED:{reason}",
                    "segmentId": rec["segmentId"],
                }
            )

    for q in qualified:
        seg_start, seg_end = q["indexStart"], q["indexEnd"]
        seg_id = q["segmentId"]

        def _put(candidate: dict) -> None:
            key = (candidate["start"], candidate["end"])
            existing = pool.get(key)
            if existing is None or candidate["score"] > existing["score"]:
                if existing is not None:
                    candidate = {
                        **candidate,
                        "mergedSources": [existing["source"], candidate["source"]],
                    }
                pool[key] = candidate

        scan_candidates, scan_rejected = _scan_windows_in_segment(
            u, y, ts, seg_start, seg_end, seg_id
        )
        rejected.extend(scan_rejected)
        for cand in scan_candidates:
            _put(cand)
        whole, whole_rejected = _segment_whole_candidate(u, y, seg_start, seg_end, ts, seg_id)
        if whole is not None:
            _put(whole)
        elif whole_rejected is not None:
            rejected.append(whole_rejected)
        for ev in events:
            if not (seg_start <= ev["index"] < seg_end):
                continue
            bounds = _event_window_bounds(ev["index"], seg_start, seg_end, ts)
            if bounds is None:
                # 段太短放不下最小事件窗：显式记录未搜索原因（缺信息有具体原因）
                not_searched.append(
                    {
                        "start": int(seg_start),
                        "end": int(seg_end),
                        "reason": f"EVENT_WINDOW_TOO_SHORT:{ev['eventId']}",
                        "segmentId": seg_id,
                    }
                )
                continue
            e_start, e_end = bounds
            seg_ptp = float(np.ptp(y[e_start:e_end]))
            if seg_ptp < _AUTOWIN_MIN_FULL_PTP:
                continue
            info_passed, info_metrics = _window_info_gate(u[e_start:e_end])
            if not info_passed:
                rejected.append(
                    {
                        "start": int(e_start),
                        "end": int(e_end),
                        "reason": "INSUFFICIENT_INFO",
                        "info": info_metrics["movement"],
                        "directionChanges": info_metrics["directionChanges"],
                        "segmentId": seg_id,
                        "eventId": ev["eventId"],
                    }
                )
                continue
            n_w = e_end - e_start
            v_lo = e_start + int(n_w * 0.6)
            v_hi = e_start + int(n_w * 0.8)
            v_ptp = float(np.ptp(y[v_lo:v_hi]))
            _put(
                {
                    "start": int(e_start),
                    "end": int(e_end),
                    "score": v_ptp + 0.5 * seg_ptp + _info_score_addon(info_metrics["movement"]),
                    "valPtp": round(v_ptp, 4),
                    "segPtp": round(seg_ptp, 4),
                    "info": info_metrics["movement"],
                    "source": "NATURAL_EVENT",
                    "segmentId": seg_id,
                    "eventId": ev["eventId"],
                }
            )

    # 评分降序；B-05 候选窗总数上限 64（去重后），超限裁剪并记录预算终态
    ranked = sorted(pool.values(), key=lambda c: c["score"], reverse=True)
    budget_record: dict | None = None
    if len(ranked) > _WINDOW_POOL_CAP:
        for dropped_win in ranked[_WINDOW_POOL_CAP:]:
            rejected.append(
                {
                    "start": dropped_win["start"],
                    "end": dropped_win["end"],
                    "reason": f"BUDGET_CAP_{_WINDOW_POOL_CAP}",
                    "score": round(dropped_win["score"], 4),
                    "segmentId": dropped_win.get("segmentId"),
                }
            )
        budget_record = {
            "dimension": "WINDOW_POOL",
            "cap": _WINDOW_POOL_CAP,
            "truncated": len(ranked) - _WINDOW_POOL_CAP,
            "note": "B-05 候选窗总数上限截断（评分降序保留前 cap 个）",
        }
        ranked = ranked[:_WINDOW_POOL_CAP]

    return {
        "ran": True,
        "scanGrid": {
            "windowMinutes": list(_WINDOW_MINUTES_LADDER),
            "stepRatio": 6,
            "minWindowPoints": _AUTOWIN_MIN_POINTS,
            "infoMinMovement": _WINDOW_INFO_MIN_MOVEMENT,
        },
        "events": events,
        "windowsChecked": [
            {k: v for k, v in c.items() if k != "mergedSources"} | {"score": round(c["score"], 4)}
            for c in ranked
        ],
        "windowsRejected": rejected,
        "notSearched": not_searched,
        "budgetTermination": budget_record,
        "selectedTuple": [
            (c["score"], c["start"], c["end"], c["valPtp"], c["segPtp"]) for c in ranked
        ],
    }


def _auto_select_segment(
    u: np.ndarray,
    y: np.ndarray,
    sp: np.ndarray | None,
    mode: list[int] | None,
    op_limits: tuple[float, float] | None = None,
    ts: float = 1.0,
    catalog: list[dict] | None = None,
    qualified: list[dict] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, str | None]:
    """自动选段：按 MODE/缺口/饱和切分，激励评分选最优可辨识片段.

    P3-06b（R2）：优先消费段目录（_build_segment_catalog 产物——全部合格
    连续段池，tier1 物理首选档 + tier2 P-12 下限档）；无目录时按旧口径
    现场切分（ts 用于 P-12 物理最小点数折算，缺省 1.0 兼容既有直调）。

    分级回退（与 G24 裁决兼容：手动段可辨识，仅降权，不作硬门控）：
    1. 可辨识片段（exclusion_reason=None）中激励分最高者（并列取点数多）；
    2. 无 → 手动模式片段（MANUAL_MODE）中激励分最高者；
    3. 无 → 回退整窗（不因选段失败而拒绝辨识）。

    mode=None 时 segment_signals 不切分（假设全 AUTO），等价整窗。
    op_limits=(min, max)：OP 工程量程（饱和检测用）；None 跳过饱和检测
    ——算法栈不假设输入量纲（生产链 OP 已归一化 0~100，由包装层显式传入）。
    """
    from app.services.tuning_identification.segmentation import segment_signals

    if catalog is not None and qualified is not None:
        catalog_pool = qualified
        manual_pool = [r for r in catalog if r.get("exclusionReason") == "MANUAL_MODE"]
        n_specs = len(catalog)
    else:
        op_min, op_max = op_limits if op_limits is not None else (None, None)
        try:
            specs = segment_signals(
                y.tolist(),
                u.tolist(),
                mode=mode,
                op_min=op_min,
                op_max=op_max,
                ts=ts,
            )
        except Exception:
            logger.debug("自动选段切分失败，回退整窗", exc_info=True)
            return u, y, sp, None
        catalog_pool = []
        manual_pool = []
        for s in specs:
            entry = {
                "indexStart": s.start_idx,
                "indexEnd": s.end_idx,
                "pointCount": s.point_count,
                "mode": s.mode_label,
                "exclusionReason": s.exclusion_reason,
                "qualifyTier": s.qualify_tier,
            }
            if s.exclusion_reason is None or (
                s.exclusion_reason == "TOO_SHORT" and s.point_count >= _SEG_MIN_FLOOR
            ):
                if s.exclusion_reason == "TOO_SHORT":
                    entry["qualifyTier"] = 2
                entry["exclusionReason"] = None
                catalog_pool.append(entry)
            elif s.exclusion_reason == "MANUAL_MODE":
                manual_pool.append(entry)
        n_specs = len(specs)

    # 激励评分选优（d=1 粗滞后，同预览端点口径；正式延迟由参数化阶段搜索）。
    # P3-06b（P-18/G24 + O-B2"动态手动段实际进入分析"）：激励充分性参与分级
    # ——可辨识段中存在激励充分段时按分择优（现行）；可辨识段全部激励不足
    # 而手动段有激励充分者 → 手动段降级使用（安静 AUTO 不再阻塞 G24 回退，
    # 07§4 完成判据"动态手动段实际进入分析"）；两者皆无 → 维持现行择优，
    # 由下游激励检测显式拒绝（缺信息有具体原因）。
    def _score_entries(entries: list[dict]) -> list[tuple[float, int, int, dict, bool]]:
        scored_entries: list[tuple[float, int, int, dict, bool]] = []
        for i, s in enumerate(entries):
            seg_u = u[s["indexStart"] : s["indexEnd"]]
            seg_y = y[s["indexStart"] : s["indexEnd"]]
            sufficient = bool(s.get("excitationSufficient"))
            if not sufficient and len(seg_u) >= 10:
                try:
                    exc = check_excitation(seg_u, seg_y, 1, op_span=_OP_SPAN_NORMALIZED)
                    score = excitation_score(exc.condition_number, exc.significant_changes)
                    sufficient = bool(exc.is_sufficient)
                except Exception:
                    score = 0.0
            else:
                score = float(s.get("excitationScore", 0.0) or 0.0)
            scored_entries.append((score, int(s["pointCount"]), i, s, sufficient))
        return scored_entries

    scored = _score_entries(catalog_pool) if catalog_pool else []
    scored_manual = _score_entries(manual_pool) if manual_pool else []
    has_sufficient = any(e[4] for e in scored)
    if scored and has_sufficient:
        pool, use_manual = catalog_pool, False
        best_score, _, best_i, best, _suff = max(
            (e for e in scored if e[4]), key=lambda e: (e[0], e[1], -e[2])
        )
    elif scored_manual and any(e[4] for e in scored_manual):
        pool, use_manual = manual_pool, True
        best_score, _, best_i, best, _suff = max(
            (e for e in scored_manual if e[4]), key=lambda e: (e[0], e[1], -e[2])
        )
    else:
        pool = catalog_pool or manual_pool
        use_manual = not catalog_pool and bool(manual_pool)
        if not pool:
            return u, y, sp, "auto-segment: 无可辨识片段，回退整窗"
        best_score, _, best_i, best, _suff = max(scored or scored_manual)

    # 单段且为可辨识段 = 整窗，无选段事实（手动降级路径即使单段也标注）
    if (
        catalog_pool
        and not use_manual
        and n_specs == 1
        and best["indexStart"] == 0
        and best["indexEnd"] == len(u)
    ):
        return u, y, sp, None
    su = u[best["indexStart"] : best["indexEnd"]]
    sy = y[best["indexStart"] : best["indexEnd"]]
    ssp = sp[best["indexStart"] : best["indexEnd"]] if sp is not None else None
    degrade = "" if not use_manual else "（手动段降级使用，G24）"
    if use_manual and scored and not has_sufficient:
        degrade += "（可辨识段全部激励不足，降级取激励充分手动段）"
    tier_note = ""
    if best.get("qualifyTier") == 2:
        tier_note = "（P-12 物理最小点数不满足，按 50 点下限档降级保留）"
    note = (
        f"auto-segment: {best_i + 1}/{len(pool)} [{best['indexStart']}:{best['indexEnd']}] "
        f"{best.get('mode', 'UNKNOWN')} 激励分{best_score:.0f}{degrade}{tier_note}"
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
    win_minutes = [m for m in _WINDOW_MINUTES_LADDER if m <= dur_minutes / 2.0]
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
    """仅在有清洗时返回统计（无坏点时 None，避免证据冗余）.

    P3-06b：R2 后 dropped 恒 0；大缺口存在（n_large_gaps>0）也视为有清洗
    事实（缺口保留不拼接，但证据需可查）。
    """
    if (
        cleaning_stats.get("interpolated_points", 0) > 0
        or cleaning_stats.get("dropped_points", 0) > 0
        or cleaning_stats.get("n_large_gaps", 0) > 0
    ):
        return cleaning_stats
    return None


def _public_window_search(audit: dict | None) -> dict[str, Any] | None:
    """B-04 搜索审计的公开形态（剥离内部 selectedTuple 元组；None=未运行）."""
    if audit is None:
        return None
    return {k: v for k, v in audit.items() if k != "selectedTuple"}


def _build_preprocessing_record(
    *,
    cleaning_stats: dict,
    seg_note: str | None,
    win_note: str | None,
    detrend_note: str | None,
    lowpass_meta: dict | None,
    mode_provided: bool,
    op_limits: tuple[float, float] | None,
    segment_catalog: list[dict] | None = None,
    window_search: dict | None = None,
) -> dict[str, Any]:
    """P2-03（R2 §三 DEC-10 补表述）：辨识预处理链结构化记录.

    把原先只拼进 reason 字符串的三件套（自动选段 / 动态感知子窗 / 去趋势+
    低通滤波）开关与实际生效参数提升为结构化字段；reason 字符串与既有
    evidence 字段（data_hash 等）保持不变——evidence.data_hash 仍按预处理
    后数据取，原始输入与预处理参数由不可变输入包 manifest.preprocess 承载
    （app/services/calc_snapshot.py::save_identification_snapshot）。

    字段口径（调用侧不可得时为显式 None，不伪补）：
    - ``cleaning``：坏点清洗统计（P2-019；R2 后 dropped 恒 0——段选择审计
      由 segmentCatalog 承载）；
    - ``autoSegment``：MODE/缺口/饱和切分（applied=note 非空）；
    - ``autoWindow``：动态感知子窗（applied=note 非空）；
    - ``detrend``：去趋势（enabled=全局开关，applied=实际生效）；
    - ``lowpass``：低通滤波（applied=False 或含 appliedWn/appliedFcHz）；
    - ``multiWindow``：v1.4 多窗编排窗位（外层编排器回填，含被检/入选/
      胜出窗位与各窗拟合度）；
    - ``segmentCatalog``：P3-06b（R2/P-14）段目录——全部连续段记录（含
      排除原因与 qualifyTier），manifest 第 2 层（段与事件目录）消费；
    - ``windowSearch``：P3-06b（B-04/B-05）统一窗池搜索审计——被检/选中/
      拒绝/未搜索四类清单 + 预算终态；多窗择优未触发时 selected 为空但
      审计仍在（外层调用必产，_internal_call 窗切片为 None=未运行）。
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
        # P3-06b 加性字段（None=目录未构建/内层窗切片未运行，显式不伪补）
        "segmentCatalog": segment_catalog,
        "windowSearch": _public_window_search(window_search),
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
    delay_search_max_seconds: float | None = None,
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

    v1.6（P3-06b 片段与时间尺度）在 v1.4 之上升级（不另起第二编排）：
    - R2：清洗不再"大缺口取最长段"——全部合格连续段进段目录与候选池
      （P-12 物理最小点数 tier1 + 50 点下限 tier2），选择审计落
      preprocessing.segmentCatalog；主选段消费目录池（G24 手动回退不变）；
    - 统一候选窗池：段内有界滑窗（幅度通道 v1.4 判据保留）+ 信息量通道
      （P-16）+ 自然事件锚点窗（P-17，SP 阶跃含斜坡保持/MODE 变更）+
      整段候选，B-05 cap 64 去重、B-06 k=3 互不重叠，≥2 个才逐窗择优；
      搜索审计（被检/选中/拒/未搜索 + 预算终态）落 preprocessing.windowSearch；
    - 窗只从段内切——**不跨 gap 造动态**（契约红线，结构性不可能）。

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
        op_limits: OP 上下限（可选；自动选段的饱和切分依据）
        delay_search_max_seconds: 时滞搜索显式物理秒上界（P3-03 B1 R1 载体，
            P3-06a 起与 R1 冻结公式 max(300s, 5×非参粗估 τ+θ)≤数据时长/4
            的结果取 min——调用方仅能进一步收窄搜索窗。非法值显式拒绝。
            仅当 ``theta_estimate`` 未显式给出（全域搜索分支）时生效；
            生效事实记入 ``preprocessing.delaySearchBound``（含公式事实、
            稀疏化与候选数，B-01 搜索审计）。

    Returns:
        IdentificationResult
    """
    # R1 载体入参校验（物理秒，正有限值；非法显式拒绝不静默忽略）
    if delay_search_max_seconds is not None and (
        not math.isfinite(delay_search_max_seconds) or delay_search_max_seconds <= 0
    ):
        return IdentificationResult(
            success=False,
            reason="delay_search_max_seconds 必须为有限正数（物理秒，R1 时滞物理边界载体）",
            theta_source=ThetaSource.HEURISTIC_2TS
            if theta_estimate is None
            else ThetaSource.EXPLICIT,
        )
    # v1.4→v1.6 多窗编排（P3-06b 升级，仅外层调用；递归窗辨识跳过防无限递归）：
    # R2 段目录全保留 + P-16 信息量通道 + P-17 自然事件锚点 → 统一候选窗池
    # （B-05 cap 64 去重），≥2 个互不重叠动态窗（B-06 k=3）才逐窗辨识择优
    # （v1.4 条件保持）。窗只从段内切——不跨 gap 造动态结构性不可能。
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

    # P2-019 + P3-06b（R2）：坏点清洗——小缺口线性插值（原地），大缺口
    # **不截断不拼接**（废弃"取最长段"静默丢段）：全部连续段进段目录，
    # 选择审计见 preprocessing.segmentCatalog/windowSearch。替代旧
    # np.isfinite 硬拒绝的初衷不变（清洗后继续辨识，证据记录统计）。
    interpolated_pts, large_gaps = _interp_small_gaps(u_raw, y_raw, sp_raw)
    valid_points = int((np.isfinite(u_raw) & np.isfinite(y_raw)).sum())
    cleaning_stats: dict = {
        "original_points": len(u_raw),
        "interpolated_points": interpolated_pts,
        "dropped_points": 0,  # R2：不再截断（段目录全保留；"剔除"语义由排除原因承载）
        "valid_points": valid_points,
        "valid_rate": valid_points / len(u_raw) if len(u_raw) else 0.0,
        "n_large_gaps": len(large_gaps),
    }
    if valid_points < 50:
        return IdentificationResult(
            success=False,
            reason=(
                f"清洗后有效数据不足（{valid_points} 点，需 ≥ 50；"
                f"原始 {cleaning_stats['original_points']} 点，"
                f"插值 {interpolated_pts} 点，"
                f"大缺口 {len(large_gaps)} 处）"
            ),
            theta_source=theta_source,
        )

    # P3-06b（R2/P-14）：段目录构建（全部合格连续段 + 排除原因；P-12 物理最小
    # 点数 tier1 + 50 点下限 tier2）。目录供主选段与统一窗池共同消费。
    segment_catalog: list[dict] | None = None
    segment_qualified: list[dict] = []
    if not _internal_call:
        try:
            segment_catalog, segment_qualified = _build_segment_catalog(
                u_raw, y_raw, sp_raw, mode, op_limits, ts
            )
        except Exception:
            logger.debug("段目录构建失败，回退 legacy 选段口径", exc_info=True)
            segment_catalog = None

    # P3-06b 统一候选窗池 + v1.4 多窗择优（仅外层调用）
    window_search_audit: dict | None = None
    if not _internal_call and segment_catalog is not None:
        try:
            window_search_audit = _collect_window_pool(
                u_raw, y_raw, sp_raw, mode, segment_catalog, segment_qualified, ts
            )
            _wins = _collect_disjoint_windows(
                window_search_audit["selectedTuple"], k=_MULTI_WINDOW_MAX_PICK
            )
            window_search_audit["windowsSelected"] = [
                {"start": int(w[1]), "end": int(w[2]), "score": round(float(w[0]), 4)}
                for w in _wins
            ]
        except Exception:
            logger.debug("统一窗池构建失败，落回整窗流程", exc_info=True)
            window_search_audit = None
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
            for _s, _e in ((int(w[1]), int(w[2])) for w in _wins):
                _r = identify_from_history(
                    op=u_raw[_s:_e].tolist(),
                    pv=y_raw[_s:_e].tolist(),
                    sp=sp_raw[_s:_e].tolist() if sp_raw is not None else None,
                    ts=ts,
                    theta_estimate=theta_estimate,
                    candidate_models=candidate_models,
                    mode=mode[_s:_e] if mode is not None else None,
                    op_limits=op_limits,
                    delay_search_max_seconds=delay_search_max_seconds,
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
                # P2-03（DEC-10）+ P3-06b（B-04/B-05/B-08）：多窗编排窗位结构化
                # ——被检/入选/胜出窗位、各窗拟合度、预算终态与搜索审计
                # （reason 字符串保持不变）
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
                    # B-08：墙钟预算依赖 B-03（P3-04/P5-02 承接，未执行显式 null
                    # 不伪补）；B-05 候选窗数预算终态由 windowSearch.budgetTermination
                    # 承载（实际生效的预算维度）
                    "budgetTermination": None,
                }
                best_r.preprocessing["segmentCatalog"] = segment_catalog
                best_r.preprocessing["windowSearch"] = _public_window_search(window_search_audit)
                return best_r
            # 全窗失败 → 落回整窗既有流程（下方）

    # P2-020（2026-10-04 预处理增强）：自动选段 → 去趋势 → 低通滤波。
    # 位于 SP 激励判定之前（切出的最优段决定 CLIVC 是否启用更准确）；
    # 三项均可独立跳过，标注拼入 reason 透明化（不静默改变辨识输入）。
    # 激励检测（层 1）使用滤波前副本：滤波会抹平 OP 微小方向变化，
    # 在滤波后数据上检测会系统性偏严（闭环 OP 本就渐进少变号）。
    # P2-03（R2 §三 DEC-10）：同步产出结构化预处理参数（steps 开关/参数/
    # applied 事实），reason 字符串保持不变；data_hash 仍按预处理后数据取。
    preprocess_notes: list[str] = []
    # P2-020 + P3-06b（R2）：主选段——优先消费段目录（全部合格连续段池，
    # tier1 物理首选 + tier2 50 点下限降级），G24 手动回退链不变；目录不可
    # 得时按 legacy 口径现场切分（ts 参与 P-12 物理最小点数折算）。
    u_raw, y_raw, sp_raw, seg_note = _auto_select_segment(
        u_raw,
        y_raw,
        sp_raw,
        mode,
        op_limits,
        ts=ts,
        catalog=segment_catalog,
        qualified=segment_qualified if segment_catalog is not None else None,
    )
    if seg_note:
        preprocess_notes.append(seg_note)
    # R2 红线出口：无任何合格段且存在大缺口——不回退整窗（整窗含 NaN 缺口，
    # 不跨 gap 拼接是契约红线；拼接造动态不可接受），显式拒绝并给出具体原因。
    if seg_note and "回退整窗" in seg_note and large_gaps:
        _seg_pts = [
            e - s + 1 for s, e in _find_contiguous_segments(np.isfinite(u_raw) & np.isfinite(y_raw))
        ]
        return IdentificationResult(
            success=False,
            reason=(
                "无可辨识连续段：所有大缺口切分段均不满足最小点数"
                f"（最长 {_seg_pts and max(_seg_pts) or 0} 点 < 50；"
                f"段目录 {len(segment_catalog or [])} 段全部排除，"
                "R2 契约禁止跨 gap 拼接，缺信息显式拒绝）"
            ),
            theta_source=theta_source,
        )
    # v1.3/v1.6 动态感知子窗（大窗口内定位阶跃响应段；阶跃稀少是生产常态）。
    # P3-06b：外层调用且统一窗池有合格候选时消费**池内最优候选**（幅度 +
    # 信息量双通道已过滤、段内有界），不再按幅度单通道重扫；池空/内层调用
    # 维持 v1.3 _auto_select_window 既有行为。池窗为全序列绝对索引，此处
    # 数组已按主选段切片——从 seg_note 解析选中段界换算相对索引（note 格式
    # 由本模块 _auto_select_segment 控制）。
    _pool_best = (window_search_audit or {}).get("selectedTuple") or []
    if not _internal_call and _pool_best:
        import re as _re

        _m = _re.search(r"\[(\d+):(\d+)\]", seg_note or "")
        _seg_lo, _seg_hi = (int(_m.group(1)), int(_m.group(2))) if _m else (0, len(u_raw))
        _seg_cands = [w for w in _pool_best if _seg_lo <= w[1] and w[2] <= _seg_hi]
        if _seg_cands:
            _pb_score, _pb_s, _pb_e, _pb_v, _pb_seg = _seg_cands[0]
            _rs, _re_ = _pb_s - _seg_lo, _pb_e - _seg_lo
            if (_rs, _re_) != (0, len(u_raw)):
                u_raw = u_raw[_rs:_re_]
                y_raw = y_raw[_rs:_re_]
                sp_raw = sp_raw[_rs:_re_] if sp_raw is not None else None
                win_note = (
                    f"auto-window(pool): [{_pb_s}:{_pb_e}] "
                    f"W={round((_pb_e - _pb_s) * ts / 60)}min PV跨度 {_pb_seg:.1f}%"
                    f"·验证位跨度 {_pb_v:.1f}%（统一窗池最优，信息量通道已过滤）"
                )
            else:
                win_note = None
        else:
            u_raw, y_raw, sp_raw, win_note = _auto_select_window(u_raw, y_raw, sp_raw, ts)
    else:
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
        segment_catalog=segment_catalog,
        window_search=window_search_audit,
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

    # ── 层 2：非参数粗估（用于初值和交叉校验）──
    # P3-06a（R1）：提前至延迟边界计算之前——θ_max 公式消费非参粗估 τ+θ；
    # P3-06a（R6）：OP 近白判定在此产出（粗增益校验的封顶资格条件）。
    # P2-005：Welch 相干仅作辅助门禁，不宣称闭环对象频响无偏——
    # 闭环下输入与扰动相关，谱估计 Ĝ=S_uy/S_uu 有偏；相干低只作可信度辅助信号。
    K_rough: float | None = None
    tau_theta_rough: float | None = None
    try:
        nonparam = correlation_analysis(u, y, ts)
        K_rough = nonparam.gain_estimate
        tau_theta_rough = nonparam.time_constant_estimate
        logger.debug("非参数粗估 K=%s, tau+theta=%s", K_rough, nonparam.time_constant_estimate)
    except Exception:
        logger.debug("非参数粗估失败，跳过", exc_info=True)
    # P2-005：Welch 相干辅助门禁（低相干 = 弱线性 / 低信噪比，封顶可信度）
    mean_coherence: float | None = None
    try:
        spectrum = welch_spectral_analysis(u, y, ts)
        coh = spectrum.get("coherence")
        if coh is not None and len(coh) > 0:
            mean_coherence = float(np.mean(coh))
    except Exception:
        logger.debug("Welch 谱分析失败，跳过相干辅助门禁", exc_info=True)

    # P2-001：延迟候选搜索范围
    # 用户给 theta 时在 d_explicit ± 3 邻域精搜；未给时全域搜索 0..d_max。
    #
    # P3-06a（R1 冻结值接入，oracle 草案 §2.1 P-01/P-02/P-03 + B-01）：
    # 全域搜索分支的搜索上界按**物理秒**公式计算——
    #     θ_max = max(300s, 5 × 非参粗估 τ+θ)，且 θ_max ≤ 数据时长 / 4
    # 再按当前 ts 换算候选样本数（P-03：保持逐 ts 一格，候选数超 241 时
    # 对数网格稀疏化并在审计记录）。三个生效方向：
    #   - max 放开：θ_max 物理秒换算可超过原隐含 60 样本边界（C14-ID2
    #     θ=120s @ ts=1s 场景；旧 min(n/4, 60) 在 ts=1s 时只能搜到 60s）；
    #   - min 收紧：θ_max / ts < 60 样本时（如 ts=10s → 300s/10=30 样本）
    #     搜索窗收紧，同一物理过程的 θ 搜索范围不随采样周期漂移；
    #   - 显式覆盖：delay_search_max_seconds（P3-03 B1 R1 载体）给定时
    #     作为额外上限与公式结果取 min（调用方显式收紧，仅能收窄）。
    n_u = len(u)
    delay_bound_record: dict[str, Any] | None = None
    if theta_estimate is not None:
        d_search_max = min(d_explicit + 3, max(0, n_u // 4))
        theta_source = ThetaSource.EXPLICIT
        delay_candidates = list(range(d_search_max + 1))
        delay_grid_sparse = False
    else:
        # R1（P-02）：θ_max = max(300s, 5×粗估 τ+θ)，≤ 数据时长 1/4
        data_duration_seconds = n_u * ts
        rough_seconds = tau_theta_rough if tau_theta_rough is not None else 0.0
        theta_max_formula = max(
            _DELAY_PHYS_MIN_SECONDS, _DELAY_PHYS_TAU_FACTOR * max(0.0, rough_seconds)
        )
        theta_max_formula = min(
            theta_max_formula, data_duration_seconds / _DELAY_PHYS_DURATION_QUARTER
        )
        theta_max_effective = theta_max_formula
        explicit_cap_samples: int | None = None
        if delay_search_max_seconds is not None:
            # B1 载体：显式物理秒上限（仅收窄；语义沿用 B1：与公式结果取 min）
            explicit_cap_samples = max(0, int(round(delay_search_max_seconds / ts)))
            theta_max_effective = min(theta_max_effective, delay_search_max_seconds)
        d_search_max = min(max(0, n_u // 4), max(0, int(theta_max_effective / ts)))
        # B-01 双域候选网格：小延迟域全密度（≤ min(d_max,60)，v1.4 原窗）+
        # 大延迟域对数稀疏（R1 放开覆盖；总候选 ≤ 241 = 61 + ≤180）
        _small_cap = min(d_search_max, 60)
        _large_grid, _ = (
            _delay_candidate_grid(d_search_max, cap=_DELAY_CANDIDATE_CAP - 61)
            if d_search_max > 60
            else ([], False)
        )
        delay_candidates = list(range(_small_cap + 1)) + [d for d in _large_grid if d > _small_cap]
        delay_grid_sparse = d_search_max > 60
        delay_bound_record = {
            # B1 兼容键（P3-03 B1 测试契约）：生效物理秒上界与换算样本数
            "maxSeconds": round(float(theta_max_effective), 4),
            "maxSamples": int(d_search_max),
            "physicalCapSamples": explicit_cap_samples,
            "applied": True,
            # R1 扩展键（P3-06a）：公式事实与审计
            "mode": "R1_FORMULA" if explicit_cap_samples is None else "R1_FORMULA_EXPLICIT_CAP",
            "maxSecondsFormula": round(float(theta_max_formula), 4),
            "roughTauPlusThetaSeconds": (
                round(float(tau_theta_rough), 4) if tau_theta_rough is not None else None
            ),
            "dataDurationSeconds": round(float(data_duration_seconds), 4),
            "formula": "max(300s, 5*roughTauPlusTheta) <= duration/4 (R1 冻结值)",
        }
        if preprocess_structured is not None:
            delay_bound_record["sparse"] = delay_grid_sparse
            delay_bound_record["candidateCount"] = len(delay_candidates)
        theta_source = ThetaSource.SEARCHED
    if delay_bound_record is not None and preprocess_structured is not None:
        preprocess_structured["delaySearchBound"] = delay_bound_record

    # 激励检测用 d_explicit（粗略估计，搜索在参数化阶段做）
    d = d_explicit

    # P2-002：时间顺序留出集分割（不随机打乱，保留时序自相关）
    # 60% train / 20% val / 20% test；数据不足时退化为 70/30 train/val（无 test）
    # P3-06a（R4/R5，P-10/P-11）：final-test 段（尾部 20%）开始被消费——
    # 独立自由仿真 R² + 四态终验状态；独立动态事件终验归 06c（P-24）。
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
    u_test, y_test = u[n_train + n_val :], y[n_train + n_val :]
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

    # P3-06a（R6，P-06 方向 A）：粗增益校验的封顶资格判定.
    # 相关分析"互相关 ∝ 脉冲响应"仅对白输入成立；封顶仅在 OP 近白/外生
    # （校验本身有效）时生效。降级判据（二者同时成立）：
    #   1. OP 有色（非近白，_u_near_white 判定）；
    #   2. 闭环跟踪证据——SP 有显著变化（OP 为 PID 输出、对负载/噪声内生）。
    # 恒 SP 闭环 / 串级 / 跟踪型 SP 的细粒度外生性检测归 06c（M2/P-21 裁决），
    # 当前对未检出 SP 激励的有色 OP 保守维持封顶（现行行为不回退）。
    u_near_white = _u_near_white(u)
    np_record_only = has_sp_excitation and not u_near_white

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
                u_test=u_test,
                y_test=y_test,
                d_search_max=d_search_max,
                delay_candidates=delay_candidates,
                ts=ts,
                theta_source=theta_source,
                exc=exc,
                exc_score=exc_score,
                K_rough=K_rough,
                np_record_only=np_record_only,
                u_near_white=u_near_white,
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

        # P2-001：延迟候选搜索 — 对候选 d 网格跑 ARX，用 BIC 选最优 d
        # P2-002：搜索用训练集（不泄漏留出集信息）
        # P3-06a（B-01）：候选网格由物理秒边界换算（≤241，超限对数稀疏化）
        #
        # 已知局限（P2-017 标注集验证暴露）：SOPDT（na=2）的延迟/极点存在退化——
        # ARX na=2 可用极快极点补偿过大 d，且慢过阻尼过程 y[k-1]≈y[k-2] 使回归
        # 矩阵病态，T2 崩塌、a2 失去物理意义。彻底修复需 SRIVC（连续时间工具变量），
        # 属后续工作；当前 SOPDT 候选仍输出供审计，但 T1/T2 精度不作门禁。
        best_d, delay_search_trace = _search_delay(
            u_train, y_train, na=na, nb=nb, d_max=d_search_max, candidates=delay_candidates
        )
        d_model = best_d
        # P3-06a（B-01）边界命中标记：best_d 落在网格端点 = 搜索边界命中
        # （θ 可能被边界截断，审计可见；07§3.2"物理时滞范围与边界命中"）
        delay_boundary_hit = None
        if delay_candidates:
            if best_d == delay_candidates[-1]:
                delay_boundary_hit = "UPPER"
            elif best_d == delay_candidates[0]:
                delay_boundary_hit = "LOWER"
        delay_search_info = {
            "boundaryHit": delay_boundary_hit,
            "maxD": int(d_search_max),
            "candidateCount": len(delay_candidates),
            "sparse": delay_grid_sparse,
            # P3-06a（B-01）两段式选择：段 2 在段 1 胜者邻域 ±max(3,10%·d₁)
            # 全密度精搜（局部公共窗）；trace 仅含段 1 全域记录
            "twoStage": True,
            "stage2HalfWidth": max(3, math.ceil(0.1 * best_d)),
        }
        logger.debug(
            "P2-001 %s 延迟搜索: d_max=%d → best_d=%d, trace=%s",
            model_type,
            d_search_max,
            d_model,
            delay_search_trace[:5],
        )

        # P2-009：保留所有成功候选（透明化算法对比，CLIVC/ARX/ARMAX 并列可审计）
        model_candidates: list[CandidateModel] = []

        def _fit_one(algo_key: str, d_try: int, *, na: int = na, nb: int = nb):
            """按算法拟合指定 d；返回估计结果或 None（方法不适用）."""
            if algo_key == "arx":
                return identify_arx(u_train, y_train, d_try, na=na, nb=nb)
            if algo_key == "armax":
                return identify_armax(u_train, y_train, d_try, na=na, nb=nb, nc=1)
            if algo_key == "clivc":
                if sp_train is None:
                    return None
                return identify_clivc4(u_train, y_train, sp_train, d_try, na=na, nb=nb)
            return None

        def _convert_one(res_arg, *, model_type_arg=model_type):
            """离散→连续转换；失败抛原异常（由调用方处置）."""
            if model_type_arg == ModelType.FOPDT:
                return arx_to_fopdt(res_arg.a_coeffs[0], res_arg.b_coeffs[0], res_arg.d, ts)
            if len(res_arg.a_coeffs) < 2:
                raise ValueError("A 多项式阶次不足（SOPDT 需 na≥2）")
            return arx_to_sopdt(
                res_arg.a_coeffs[0], res_arg.a_coeffs[1], res_arg.b_coeffs[0], res_arg.d, ts
            )

        # P3-06a：两遍拟合——第一遍在 BIC 胜者 d_model 上跑全部方法；仅当
        # 该结构**全灭**（所有方法拟合/转换失败）时，第二遍在邻域 d±1..3
        # 做结构拯救（最后手段，取首个可转换的方法-邻域组合）。
        # 背景（loop-16 教训）：BIC 的 d 由 ARX 方程误差决定，噪声下 ARX
        # na=2 极点常退化（复/负）致转换失败，而 ARMAX/CLIVC 在邻近 d 可
        # 转换——结构不应因单一 d 的转换运气全灭。拯救限定为全灭兜底而非
        # 逐方法机会重试：中质量拯救候选会污染 Occam 选择（trend+noise
        # 场景教训）。实际使用的 d 记入 estimatorProfile.delaySamples
        # 与 delayAdjustedFrom（不静默偏离搜索胜者）。
        method_fits: list[tuple[object, str, object, object, int | None]] = []
        for method, algo_key in identification_runs:
            try:
                res_try = _fit_one(algo_key, d_model)
                if res_try is None:
                    continue
                params_try = _convert_one(res_try)
            except Exception as fit_err:
                logger.debug("%s @d=%d 拟合/转换失败：%s", method, d_model, fit_err)
                continue
            method_fits.append((method, algo_key, res_try, params_try, None))
        if not method_fits:
            for d_try in (
                d_model + 1,
                d_model - 1,
                d_model + 2,
                d_model - 2,
                d_model + 3,
                d_model - 3,
            ):
                if d_try < 0 or d_try > d_search_max:
                    continue
                for method, algo_key in identification_runs:
                    try:
                        res_try = _fit_one(algo_key, d_try)
                        if res_try is None:
                            continue
                        params_try = _convert_one(res_try)
                    except Exception as fit_err:
                        logger.debug("拯救 %s @d=%d 拟合/转换失败：%s", method, d_try, fit_err)
                        continue
                    method_fits.append((method, algo_key, res_try, params_try, d_model))
                    break
                if method_fits:
                    break

        for method, _algo_key, res, params, d_adjusted_from in method_fits:
            method_used = method

            # P2-012：物理可行性门禁 —— 负增益/NMP 零点不拒绝但封顶可信度并标记
            feasibility = check_physical_feasibility(params, res.b_coeffs, ts)
            physical_flag = "" if feasibility.passed else f", {feasibility.reason_code}"

            # P2-004：非参数一致性检查（符号 + 量级）—— 与独立的相关分析粗估交叉校验
            # P3-06a（R6，P-06 方向 A）：封顶资格判定——粗增益校验仅在
            # OP 近白/外生（校验有效）时作为封顶信号；OP 有色（闭环跟踪）
            # 时降级为记录性证据不封顶（相关分析对有色输入有偏，校验本身
            # 无效，准确保持输入模型不得因无效粗估误降——O-A2）。
            np_passed, np_reason = _check_nonparam_consistency(params.K, K_rough)
            np_check_mode = "RECORD_ONLY" if np_record_only else "CAP"
            np_check_basis = (
                "COLORED_OP_CLOSED_LOOP"
                if np_record_only
                else ("NEAR_WHITE" if u_near_white else "EXOGENOUS_ASSUMED")
            )
            if not np_passed:
                physical_flag += (
                    f", {np_reason}"
                    if not np_record_only
                    else (f", {np_reason}[RECORD_ONLY:COLORED_OP]")
                )
            nonparam_check_record = {
                "roughGain": round(float(K_rough), 6) if K_rough is not None else None,
                "paramGain": round(float(params.K), 6),
                "verdict": np_reason
                if np_reason
                else ("UNAVAILABLE" if K_rough is None else "PASS"),
                "mode": np_check_mode,
                "basis": np_check_basis,
            }

            # P2-002：验证集自由仿真 R²（留出集泛化能力，替代训练集方程误差 R²）
            r2_train = max(0.0, min(1.0, res.r_squared))
            d_used = res.d  # 实际拟合 d（邻域重试后可能与 d_model 不同）
            y_val_pred, r2_val = _free_run_simulation(
                u_val, y_val, res.a_coeffs, res.b_coeffs, d_used
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

            # P3-06a（R4/R5，P-10/P-11）：final-test 终验段独立评估（首次消费
            # 尾部 20% 段——此前 n_test 已算但从不参与任何指标，M3）。
            # 四态：PASSED（有动态且自由仿真 R² ≥ 0.5）/ FAILED（有动态但
            # 解释力不足——终验失败不晋升，可信度封顶 C）/ NO_DYNAMICS
            # （段近稳态，ptp 门限沿用 _VAL_STATIONARY_MIN_PTP——不伪称
            # 通过，仅记录）/ NOT_RUN（短数据 70/30 分割无终验段）。
            # 终验分数不参与选模（选模仍按可信度+拟合度；终验只降不升）。
            if n_test > 0:
                _, r2_final_test = _free_run_simulation(
                    u_test, y_test, res.a_coeffs, res.b_coeffs, d_used
                )
                test_ptp = float(np.ptp(y_test))
                if test_ptp < _VAL_STATIONARY_MIN_PTP:
                    final_status = FINAL_TEST_NO_DYNAMICS
                elif r2_final_test >= _FINAL_TEST_MIN_R2:
                    final_status = FINAL_TEST_PASSED
                else:
                    final_status = FINAL_TEST_FAILED
                final_test_record = {
                    "status": final_status,
                    "r2": round(float(r2_final_test), 4),
                    "n": int(n_test),
                    "ptpRange": round(test_ptp, 4),
                }
            else:
                r2_final_test = None
                final_status = FINAL_TEST_NOT_RUN
                final_test_record = None

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
            max_lag_na = max(na, nb + d_used)
            rows = len(y_train) - max_lag_na
            residuals = np.zeros(max(rows, 0))
            if rows > 0:
                for i in range(rows):
                    idx = max_lag_na + i
                    val = y_train[idx]
                    for j in range(na):
                        val += res.a_coeffs[j] * y_train[idx - 1 - j]
                    for j in range(nb):
                        val -= res.b_coeffs[j] * u_train[idx - d_used - j]
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
            # P3-06a（R6）：仅 OP 近白/外生（mode=CAP）时封顶；有色 OP 闭环
            # 下 K 粗估有偏、校验无效，降级记录不封顶（不误降准确模型）
            if not np_passed and not np_record_only:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
            # P2-005：Welch 相干辅助门禁（低相干 = 弱线性/低信噪比，仅辅助信号不拒绝）
            low_coherence = mean_coherence is not None and mean_coherence < _LOW_COHERENCE_THRESHOLD
            if low_coherence:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
            # P3-06a（R4/R5）：终验失败不晋升——终验段有动态而模型解释力不足，
            # 可信度封顶 C（C19-ID2"训练好、终验差的模型不得晋升"）
            if final_status == FINAL_TEST_FAILED:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
                physical_flag += ", FINAL_TEST_FAILED"
            # P3-06b（O-A6 完整出口）：全域搜索胜者命中搜索上界（UPPER）= θ 可能
            # 被边界截断、信息不足以确定 θ——显式原因码 + 可信度封顶 C
            # （θ̂=边界值不得冒充已确定的 θ；仅全域搜索分支，显式 θ 分支
            # 的 ±3 邻域端点命中不构成信息不足证据）
            delay_boundary_capped = (
                delay_boundary_hit == "UPPER" and theta_source == ThetaSource.SEARCHED
            )
            if delay_boundary_capped:
                confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
                physical_flag += ", DELAY_BOUNDARY_UPPER"

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
                # P3-06a（R6）：降级模式下不输出封顶语义码（误判不再作为可信度
                # 信号），改记录降级事实；原始 verdict 由 nonparamCheck 结构化承载
                reason_codes.append(np_reason if not np_record_only else "NP_CHECK_RECORD_ONLY")
            if low_coherence:
                reason_codes.append("LOW_COHERENCE")
            if theta_source == ThetaSource.HEURISTIC_2TS:
                reason_codes.append("HEURISTIC_2TS")
            if val_stationary:
                reason_codes.append("VAL_STATIONARY")
            if final_status == FINAL_TEST_FAILED:
                reason_codes.append("FINAL_TEST_FAILED")
            elif final_status == FINAL_TEST_NO_DYNAMICS:
                reason_codes.append("FINAL_TEST_NO_DYNAMICS")
            # P3-06b（O-A6）：θ 边界命中信息不足显式标记（封顶见上）
            if delay_boundary_capped:
                reason_codes.append("DELAY_BOUNDARY_UPPER_HIT")
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
                # P3-06a 加性证据（R4~R7/R17/B-01）
                final_test=final_test_record,
                nonparam_check=nonparam_check_record,
                ipdt_drift=None,
                estimator_profile={
                    "profileVersion": "TUNE_IDENT_PROFILE_v1",
                    "structure": model_type.value,
                    "estimator": method_used.value,
                    "orders": {"na": na, "nb": nb, "nc": len(c_coeffs) if c_coeffs else 0},
                    "delaySamples": int(res.d),
                    # 转换失败邻域重试的实际来源（None=搜索胜者未调整）
                    "delayAdjustedFrom": d_adjusted_from,
                },
                delay_search_info=delay_search_info,
            )

            # P3-06a（R4/R5）：终验分数入 reason 字符串（三段分数透明化）
            _final_note = (
                f", R²_final={r2_final_test:.3f}({final_status})"
                if r2_final_test is not None
                else ""
            )
            candidate = CandidateModel(
                params=params,
                fitting_score=round(r2_eval * 100, 2),  # v1.2：val 稳态回退口径
                confidence=confidence,
                identify_method=method_used,
                residual_test_passed=residual_white,
                excitation_score=exc_score,
                reason=(
                    f"R²_val={r2_val:.3f}, R²_train={r2_train:.3f}{_final_note},"
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

    # P3-06b（P-19 物理时间与抗混叠）：胜出模型的连续时间常数 < 采样周期 →
    # 动态在单采样内已收敛，采样分辨率不足以分辨（C14-ID2 采样不足场景）——
    # 显式标记"不可判断该时间常数"而非硬出高可信值（原因码 + 封顶 C；
    # 多采样率比较前提不成立显式不可比较，07§4 06b）。
    _best_tau = best.params.tau if best.params.model_type == ModelType.FOPDT else None
    if best.params.model_type == ModelType.SOPDT:
        _t1, _t2 = best.params.T1, best.params.T2
        _taus = [t for t in (_t1, _t2) if t and t > 0]
        _best_tau = min(_taus) if _taus else None
    if _best_tau is not None and 0.0 < _best_tau < ts:
        best.confidence = _cap_confidence(best.confidence, AlgorithmConfidenceLevel.C)
        _below_res_note = (
            f", DYNAMICS_BELOW_RESOLUTION(τ={_best_tau:.3g}s < ts={ts:g}s，"
            "采样分辨率不足以分辨该时间常数)"
        )
        best.reason = (best.reason or "") + _below_res_note
        if best.evidence is not None:
            best.evidence.reason_codes.append("DYNAMICS_BELOW_RESOLUTION")

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


def _u_near_white(
    u: np.ndarray,
    max_lag: int = _OP_NEAR_WHITE_MAX_LAG,
    threshold: float = _OP_NEAR_WHITE_MAX_ABS_RHO,
) -> bool:
    """R6（P-06 方向 A）：OP 近白判定.

    相关分析"互相关 ∝ 脉冲响应"仅对白输入成立（nonparametric.py 自注）；
    OP 近白时 K 粗估有效，粗增益交叉校验才可作为封顶信号。判定：去均值
    自相关 |ρ_k| ≤ threshold（k=1..max_lag）。白噪声 ρ_k ≈ ±1.96/√N，
    阈值 0.3 对白输入恒成立；闭环 PI 的 OP 平滑（ρ1 > 0.9）与 PRBS 保持
    型输入（ρ1 ≈ 0.9）均判非白——非白本身不触发降级，须再叠加闭环证据
    （SP 激励）才降级（见 identify_from_history 的 R6 组合判定）。
    """
    n = len(u)
    if n < 4 * max_lag:
        return False  # 样本不足无法可靠估计自相关，保守判非白
    x = u - float(np.mean(u))
    var = float(np.dot(x, x)) / n
    if var < 1e-12:
        return False
    for k in range(1, min(max_lag, n // 4) + 1):
        rho = float(np.dot(x[k:], x[:-k])) / (n * var)
        if abs(rho) > threshold:
            return False
    return True


def _delay_candidate_grid(d_max: int, cap: int | None = None) -> tuple[list[int], bool]:
    """B-01：时滞候选网格（候选数 ≤ 241，超出按对数网格稀疏化）.

    - d_max + 1 ≤ cap：全密度 0..d_max（现行行为）；
    - 超限：{0} ∪ {round(d_max^(i/m)) | i=1..m}（m = cap−1）——对数
      均匀网格，保证任意 d 处相邻候选间距 ≈ d·ln(d_max)/m（d=120、
      d_max=800 时间距 ≈ 0.86 样本），θ 恢复精度不受稀疏化破坏；稀疏化
      事实记入 delaySearchBound 供搜索审计（07§3.2 未搜索范围可见）。
    - cap 缺省 241（B-01 冻结值）；双域搜索的大延迟域传更小 cap 以保证
      总预算（小延迟域 61 + 大延迟域 ≤180 = ≤241）。
    """
    budget = cap if cap is not None else _DELAY_CANDIDATE_CAP
    if d_max < 0:
        return [0], False
    if d_max + 1 <= budget:
        return list(range(d_max + 1)), False
    m = budget - 1
    exponents = (np.arange(1, m + 1, dtype=float) / m) * math.log(d_max)
    grid = {0} | {int(round(math.exp(v))) for v in exponents}
    grid.add(d_max)
    candidates = sorted(g for g in grid if 0 <= g <= d_max)
    return candidates, True


def _arx_delay_candidate_convertible(a_coeffs: list[float]) -> bool:
    """P3-06a（B-01 配套）：延迟候选的 ARX 拟合是否可转换为该结构的连续模型.

    FOPDT（na=1）：a1 ∈ (−1, 0)（极点 −a1 ∈ (0,1)，稳定且可取对数）；
    SOPDT（na=2）：两个离散极点均为正实且 < 1（arx_to_sopdt 的接受域）。

    不可转换的候选（如闭环数据上大延迟+退化积分极点 a1≈−1 的拟合）永远
    产不出该结构的物理模型，其 BIC 对结构选择无意义——从最优 d 选择中
    排除（trace 仍记录全量 BIC 供审计）。
    """
    if not a_coeffs:
        return True
    if len(a_coeffs) == 1:
        a1 = float(a_coeffs[0])
        return -1.0 < a1 < 0.0
    try:
        roots = np.roots([1.0, *a_coeffs])
    except (np.linalg.LinAlgError, ValueError):
        return False
    return bool(
        np.all(np.abs(roots.imag) < 1e-12) and np.all(roots.real > 0.0) and np.all(roots.real < 1.0)
    )


def _search_delay(
    u: np.ndarray,
    y: np.ndarray,
    na: int,
    nb: int,
    d_max: int,
    candidates: list[int] | None = None,
) -> tuple[int, list[tuple[int, float]]]:
    """延迟候选搜索（P2-001 + P3-06a B-01/R1）— 双域 BIC + 自由仿真仲裁.

    BIC = n·ln(σ²) + k·ln(n)；BIC 越小越好（惩罚复杂度）。

    S3 修复（G27#1）：BIC 必须在**同一样本窗**上比较——各候选在公共窗
    [t0, N) 上重算残差方差，以同一 n 计罚。

    P3-06a 双域结构（R1 宽搜索域暴露的选择病理修复）：
    R1 把搜索上界放宽到物理秒公式（≥300s，ts=1s 时 ≥300 样本），公共窗
    被迫取 t0 = d_max+nb——闭环 SP 阶梯数据上该窗把携带判别动态的早期段
    排除在评估外，"边际稳定积分极点 + 大延迟"的 2 参数垃圾拟合可在平台
    段上以微弱 BIC 优势劫持选择（实测 loop-06：正确 d=8 在窄窗上胜出 374
    BIC 单位，在宽窗上反被 d≈160 垃圾区反超）。处置（不违背 G27#1 同窗
    比较原则、不收窄 R1 覆盖）：

    - **小延迟域 S**：d ∈ [0, min(d_max, 60)] 全密度，公共窗 t0S=61 级
      （=v1.4 原搜索窗，判别力完整）；过滤不可转换候选（_arx_delay_
      candidate_convertible）+ BIC 平局容差（0.5·ln(n)）内取最小 d 的
      Occam 裁决；
    - **大延迟域 L**：d ∈ (60, d_max] 对数稀疏网格（B-01 ≤241），公共窗
      t0L=d_max+nb；同过滤；胜者邻域 ±max(3, 10%·d) 全密度精搜（局部窗）；
    - **跨域仲裁**：S/L 胜者在宽公共窗 [t0L, N) 上比较**自由仿真 SSE**
      （方程误差可被平台段利用，自由仿真误差累积不可欺骗）——SSE 小者
      胜，相对差 <1% 取较小 d（Occam）。

    Args:
        u, y: 输入/输出信号（已去均值，训练段）
        na, nb: A/B 多项式阶次
        d_max: 最大延迟候选（采样数）
        candidates: 显式候选网格（缺省 = 双域自动构造）

    Returns:
        (best_d, search_trace) — trace 为逐候选 (d, bic) 审计记录
        （S/L 两域公共窗不同，数值不可跨域直接比较——见 delaySearchInfo）
    """
    n_total = len(y)
    k = na + nb

    def _fit(d: int) -> tuple[list[float], list[float]] | None:
        try:
            res = identify_arx(u, y, d, na=na, nb=nb)
        except Exception:
            return None
        return res.a_coeffs, res.b_coeffs

    def _bic_on_window(a: list[float], b: list[float], d: int, t0: int) -> float:
        sigma2 = _residual_var_on_common_window(u, y, a, b, d, t0)
        n_common = max(1, n_total - t0)
        return n_common * math.log(max(sigma2, 1e-12)) + k * math.log(n_common)

    def _pick_occam(pool: list[tuple[float, int]], t0: int) -> int:
        """BIC 平局容差（0.5·ln(n)）内取最小 d（Occam：死时间小=模型简单）."""
        min_bic = min(b for b, _ in pool)
        margin = 0.5 * math.log(max(n_total - t0, 2))
        return min(d for b, d in pool if b <= min_bic + margin)

    # 候选网格（显式给出时按其构造 S/L；缺省双域自动）
    d_small_cap = min(d_max, 60)
    if candidates is None:
        s_candidates = list(range(d_small_cap + 1))
        l_candidates, _ = _delay_candidate_grid(d_max) if d_max > 60 else ([], False)
        l_candidates = [d for d in l_candidates if d > d_small_cap]
    else:
        s_candidates = [d for d in candidates if d <= d_small_cap]
        l_candidates = [d for d in candidates if d > d_small_cap]

    search_trace: list[tuple[int, float]] = []

    # ── 域 S：小延迟全密度（v1.4 原窗与原选择语义 + Occam 平局裁决）──
    # 注：S 域不做可转换性过滤——开环噪声数据上真 d 的 ARX na=2 拟合极点
    # 可因噪声偏差变复/负（ARMAX 在同 d 仍可转换），按 ARX 极点过滤会把
    # 真 d 整体排除（loop-16 教训）。垃圾积分型拟合（a1≈−1）聚居大延迟
    # 区，由 L 域过滤 + 跨域自由仿真仲裁拦截。
    t0_s = max(na, (max(s_candidates) if s_candidates else 0) + nb)
    s_finite: list[tuple[float, int]] = []
    s_fits: dict[int, tuple[list[float], list[float]]] = {}
    for d in s_candidates:
        fit = _fit(d)
        if fit is None:
            search_trace.append((d, float("inf")))
            continue
        bic = _bic_on_window(fit[0], fit[1], d, t0_s)
        search_trace.append((d, round(bic, 2)))
        s_finite.append((bic, d))
        s_fits[d] = fit
    d_s = _pick_occam(s_finite, t0_s) if s_finite else None

    # ── 域 L：大延迟稀疏网格 + 局部精搜 ──
    t0_l = max(na, d_max + nb)
    l_finite: list[tuple[float, int]] = []
    l_convertible: list[tuple[float, int]] = []
    l_fits: dict[int, tuple[list[float], list[float]]] = {}
    for d in l_candidates:
        fit = _fit(d)
        if fit is None:
            search_trace.append((d, float("inf")))
            continue
        bic = _bic_on_window(fit[0], fit[1], d, t0_l)
        search_trace.append((d, round(bic, 2)))
        l_finite.append((bic, d))
        l_fits[d] = fit
        if _arx_delay_candidate_convertible(fit[0]):
            l_convertible.append((bic, d))
    d_l: int | None = None
    if l_convertible or l_finite:
        d_l = _pick_occam(l_convertible or l_finite, t0_l)
        # 局部精搜（局部窗恢复判别力）
        w = max(3, math.ceil(0.1 * d_l))
        lo, hi = max(d_small_cap + 1, d_l - w), min(d_max, d_l + w)
        pool2: list[tuple[float, int]] = []
        t0_local = max(na, hi + nb)
        for d in range(lo, hi + 1):
            fit = _fit(d)
            if fit is None:
                continue
            bic = _bic_on_window(fit[0], fit[1], d, t0_local)
            if _arx_delay_candidate_convertible(fit[0]):
                pool2.append((bic, d))
                l_fits[d] = fit
        if pool2:
            d_l = _pick_occam(pool2, t0_local)

    # ── 跨域仲裁 ──
    if d_s is None and d_l is None:
        return (candidates[0] if candidates else 0), search_trace
    if d_s is None:
        return d_l, search_trace
    if d_l is None:
        return d_s, search_trace
    t0_arb = t0_l
    y_pred_s, _ = _free_run_simulation(u, y, s_fits[d_s][0], s_fits[d_s][1], d_s)
    y_pred_l, _ = _free_run_simulation(u, y, l_fits[d_l][0], l_fits[d_l][1], d_l)
    sse_s = float(np.sum((y[t0_arb:] - y_pred_s[t0_arb:]) ** 2))
    sse_l = float(np.sum((y[t0_arb:] - y_pred_l[t0_arb:]) ** 2))
    if sse_l < sse_s * 0.99:
        return d_l, search_trace
    return d_s, search_trace


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
    ts: float = 1.0,
) -> tuple[float, float, float, float, int] | None:
    """P2-008 + P3-06a（R7，P-07 方向 A）：IPDT 差分回归含显式漂移项.

    积分过程 G(s)=K·e^(-θs)/s 离散化（ZOH 近似）：
        y(k) - y(k-1) = K·ts·u(k-d) + c0 + c1·(k·ts)
    差分去积分器后做最小二乘（数据已去均值）。P3-06a 起回归显式含漂移项
    ——恒值漂移（OP 持续偏置/负载阶跃后的恒速率爬升）吸收进截距 c0，
    加速度型漂移（负载斜坡的局部线性化）吸收进斜坡项 c1；漂移项吸收并
    记录（不静默），K=b1/ts 不再被平衡/负载漂移污染（C19-ID1/O-A4）。

    Args:
        u: 输入（已去均值）
        y: 输出（已去均值）
        d: 延迟（采样数）
        ts: 采样周期（秒）

    Returns:
        (b1, c0, c1, residual_var, r2, n_samples) 或 None（数据不足）
        b1 = K·ts；c0 = 每样本恒值漂移（PV 单位/样本）；c1 = 每样本线性
        漂移斜率（PV 单位/样本²，时间基 t=k·ts 秒）
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
    # R7：显式漂移项（截距 + 局部线性斜坡；时间基 t = i·ts 秒）
    t_local = np.arange(m, dtype=float) * ts
    Phi = np.column_stack([u_indep, np.ones(m), t_local])
    theta, _, _, _ = np.linalg.lstsq(Phi, y_dep, rcond=None)
    b1, c0, c1 = (float(theta[0]), float(theta[1]), float(theta[2]))
    residuals = y_dep - Phi @ theta
    res_var = float(np.var(residuals)) if m > 1 else 0.0
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((y_dep - np.mean(y_dep)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
    return b1, c0, c1, res_var, max(0.0, min(1.0, r2)), m


def _ipdt_free_run(
    u: np.ndarray,
    y: np.ndarray,
    b1: float,
    d: int,
    ts: float = 1.0,
    c0: float = 0.0,
    c1: float = 0.0,
) -> tuple[np.ndarray, float]:
    """P2-008 + P3-06a（R7）：IPDT 验证集自由仿真含漂移项.

    y_pred(k) = y_pred(k-1) + b1·u(k-d) + c0 + c1·(k·ts)——自由仿真消费
    与回归一致的完整增量模型（含漂移项），否则漂移被二次注入预测误差。

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
        drift = c0 + c1 * (k * ts)
        if idx_u >= 0:
            y_pred[k] = y_pred[k - 1] + b1 * u[idx_u] + drift
        else:
            y_pred[k] = y_pred[k - 1] + drift
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
    u_test: np.ndarray,
    y_test: np.ndarray,
    d_search_max: int,
    delay_candidates: list[int],
    ts: float,
    theta_source: ThetaSource,
    exc,
    exc_score: float,
    K_rough: float | None,
    np_record_only: bool,
    u_near_white: bool,
    mean_coherence: float | None,
    n_train: int,
    n_val: int,
    n_test: int,
    data_hash: str,
    cleaning_stats: dict,
) -> CandidateModel | None:
    """P2-008 + P3-06a：IPDT 历史辨识 G(s) = K·exp(-theta·s)/s.

    积分过程（如液位、累积量）PV 不回归稳态而是持续斜坡变化。
    差分去积分器后线性回归辨识 K 与 theta，复用 BIC 延迟搜索与留出集自由仿真。

    P3-06a 升级（oracle 冻结草案 §2）：
    - R7（P-07 方向 A）：差分回归显式漂移项（截距 c0 + 斜坡 c1）吸收
      OP 偏置/负载斜坡并记录入证据（ipdtDrift）；
    - R4/R5（P-10/P-11）：final-test 尾段独立自由仿真 + 四态终验；
    - R6（P-06）：粗增益校验按封顶资格判定（同主路径语义）；
    - B-01：延迟搜索消费物理秒边界换算的候选网格（k=3 参数计 BIC）。

    流程：
    1. 差分 y_train → dy，对候选网格各 d 回归 dy=b1·u(k-d)+c0+c1·t，BIC 选最优 d
    2. K = b1/ts，theta = d·ts；漂移项换算物理速率并记录
    3. 验证集/终验段自由仿真 R²（积分累积误差；自由仿真含漂移项）
    4. 物理可行性（负增益）/相干辅助门禁
    """
    # 1. 延迟搜索（BIC 准则；k=3 参数 = b1 + 漂移截距 c0 + 斜坡 c1）
    # P3-06a（B-01 同款两段式）：段 1 候选网格全域比较，b1≈0/非有限的候选
    # （无积分关系，不可转换）不参与选择；段 2 胜者邻域全密度精搜
    best_d = delay_candidates[0] if delay_candidates else 0
    delay_search_trace: list[tuple[int, float]] = []
    finite: list[tuple[float, int]] = []
    convertible: list[tuple[float, int]] = []
    for d in delay_candidates:
        if d < 0 or d > d_search_max:
            continue
        reg = _ipdt_regress(u_train, y_train, d, ts=ts)
        if reg is None:
            delay_search_trace.append((d, float("inf")))
            continue
        b1_d, _c0_d, _c1_d, res_var_d, _, n_d = reg
        bic = n_d * math.log(max(res_var_d, 1e-12)) + 3 * math.log(max(n_d, 2))
        bic_rounded = round(bic, 2)
        delay_search_trace.append((d, bic_rounded))
        finite.append((bic, d))
        if math.isfinite(b1_d) and abs(b1_d) > 1e-12:
            convertible.append((bic, d))
    if finite:
        pool1 = convertible or finite

        def _pick_occam_ipdt(pool: list[tuple[float, int]]) -> int:
            # BIC 平局容差内取最小 d（同 _search_delay 的 Occam 平局裁决）
            min_bic = min(b for b, _ in pool)
            n_ref = max(len(y_train) - max(1, d_search_max), 2)
            margin = 0.5 * math.log(n_ref)
            return min(d for b, d in pool if b <= min_bic + margin)

        d1 = _pick_occam_ipdt(pool1)
        w = max(3, math.ceil(0.1 * d1))
        stage2 = list(range(max(0, d1 - w), min(d_search_max, d1 + w) + 1))
        pool2: list[tuple[float, int]] = []
        for d in stage2:
            reg = _ipdt_regress(u_train, y_train, d, ts=ts)
            if reg is None:
                continue
            b1_d, _c0_d, _c1_d, res_var_d, _, n_d = reg
            if math.isfinite(b1_d) and abs(b1_d) > 1e-12:
                pool2.append((n_d * math.log(max(res_var_d, 1e-12)) + 3 * math.log(max(n_d, 2)), d))
        if pool2:
            best_d = _pick_occam_ipdt(pool2)
        else:
            best_d = d1

    # 2. 最优延迟下辨识（R7：漂移项吸收并记录）
    reg = _ipdt_regress(u_train, y_train, best_d, ts=ts)
    if reg is None:
        logger.debug("P2-008 IPDT 辨识失败：差分回归数据不足")
        return None
    b1, c0, c1, res_var, r2_train, n_samples = reg
    if abs(b1) < 1e-12:
        logger.debug("P2-008 IPDT 辨识失败：b1≈0，无积分关系")
        return None
    K = b1 / ts
    theta = best_d * ts
    params = ModelParams(model_type=ModelType.IPDT, K=K, theta=theta)
    ipdt_drift_record = {
        "absorbed": True,
        "sampleOffset": round(float(c0), 8),
        "rampPerSample": round(float(c1), 10),
        "pvRatePerSecond": round(float(c0) / ts, 8),
        "note": (
            "R7 显式漂移项（差分回归截距+斜坡）吸收并记录；"
            "pvRatePerSecond 为恒值漂移速率（PV 单位/秒）"
        ),
    }

    # 3. 验证集自由仿真 R²（v1.2：val 段近稳态防护同主路径；R7：自由仿真含漂移项）
    y_val_pred, r2_val = _ipdt_free_run(u_val, y_val, b1, best_d, ts=ts, c0=c0, c1=c1)
    val_stationary = float(np.ptp(y_val)) < _VAL_STATIONARY_MIN_PTP
    r2_eval = r2_train if val_stationary else r2_val
    residuals_val_arr = y_val - y_val_pred
    y_val_range = float(np.ptp(y_val))
    if len(residuals_val_arr):
        rmse_val = float(np.sqrt(np.mean(residuals_val_arr**2)))
    else:
        rmse_val = 0.0
    nrmse_val = rmse_val / y_val_range if y_val_range > 1e-12 else 0.0

    # P3-06a（R4/R5）：final-test 尾段独立终验（四态，同主路径语义）
    if n_test > 0:
        _, r2_final_test = _ipdt_free_run(u_test, y_test, b1, best_d, ts=ts, c0=c0, c1=c1)
        test_ptp = float(np.ptp(y_test))
        if test_ptp < _VAL_STATIONARY_MIN_PTP:
            final_status = FINAL_TEST_NO_DYNAMICS
        elif r2_final_test >= _FINAL_TEST_MIN_R2:
            final_status = FINAL_TEST_PASSED
        else:
            final_status = FINAL_TEST_FAILED
        final_test_record = {
            "status": final_status,
            "r2": round(float(r2_final_test), 4),
            "n": int(n_test),
            "ptpRange": round(test_ptp, 4),
        }
    else:
        r2_final_test = None
        final_status = FINAL_TEST_NOT_RUN
        final_test_record = None

    # 4. 残差检验：IPDT 方程误差 = dy - (b1·u(k-d) + c0 + c1·t)，检验与输入独立性
    # R7：残差已扣除漂移项（旧实现漂移残留残差使互相关检验系统性失败）
    dy_train = np.diff(y_train)
    if best_d <= 0:
        eq_err = (
            dy_train
            - b1 * u_train[1 : 1 + len(dy_train)]
            - c0
            - c1 * (np.arange(len(dy_train), dtype=float) * ts)
        )
    else:
        n_rows = len(dy_train) - best_d + 1
        eq_err = (
            dy_train[best_d - 1 :]
            - b1 * u_train[:n_rows]
            - c0
            - c1 * (np.arange(n_rows, dtype=float) * ts)
        )
    exceed_ratio = _residual_input_exceed_ratio(eq_err, u_train)
    residual_white = exceed_ratio <= _XCORR_EXCEED_TOLERANCE
    test_note = f"xcorr_exceed={exceed_ratio:.3f}"

    # 5. 物理可行性（IPDT 无 NMP 零点概念，仅检负增益）
    feasibility = check_physical_feasibility(params, [b1], ts)
    physical_flag = "" if feasibility.passed else f", {feasibility.reason_code}"

    # 5b. P3-06a（R6）：粗增益校验按封顶资格判定（同主路径语义）
    np_passed, np_reason = _check_nonparam_consistency(K, K_rough)
    nonparam_check_record = {
        "roughGain": round(float(K_rough), 6) if K_rough is not None else None,
        "paramGain": round(float(K), 6),
        "verdict": np_reason if np_reason else ("UNAVAILABLE" if K_rough is None else "PASS"),
        "mode": "RECORD_ONLY" if np_record_only else "CAP",
        "basis": (
            "COLORED_OP_CLOSED_LOOP"
            if np_record_only
            else ("NEAR_WHITE" if u_near_white else "EXOGENOUS_ASSUMED")
        ),
    }
    if not np_passed:
        physical_flag += (
            f", {np_reason}" if not np_record_only else f", {np_reason}[RECORD_ONLY:COLORED_OP]"
        )

    # 6. 可信度评估（v1.2：val 段近稳态回退 R²_train 并封顶 C）
    delay_boundary_hit = None
    if delay_candidates:
        if best_d == delay_candidates[-1]:
            delay_boundary_hit = "UPPER"
        elif best_d == delay_candidates[0]:
            delay_boundary_hit = "LOWER"
    confidence = _assess_confidence(exc, r2_eval, residual_white, exc_score)
    if val_stationary:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
        physical_flag += ", VAL_STATIONARY"
    if theta_source == ThetaSource.HEURISTIC_2TS:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
    if not feasibility.passed:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
        physical_flag = f", {feasibility.reason_code}(K={K:.4g})"
    # R6：粗增益校验仅在 OP 近白/外生（mode=CAP）时封顶
    if not np_passed and not np_record_only:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
    # P2-005：相干辅助门禁
    low_coherence = mean_coherence is not None and mean_coherence < _LOW_COHERENCE_THRESHOLD
    if low_coherence:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
    # R4/R5：终验失败不晋升
    if final_status == FINAL_TEST_FAILED:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
        physical_flag += ", FINAL_TEST_FAILED"
    # P3-06b（O-A6 完整出口，同主路径语义）：全域搜索胜者命中上界 →
    # θ 信息不足显式标记 + 可信度封顶 C（θ̂ 不得冒充已确定）
    delay_boundary_capped = delay_boundary_hit == "UPPER" and theta_source == ThetaSource.SEARCHED
    if delay_boundary_capped:
        confidence = _cap_confidence(confidence, AlgorithmConfidenceLevel.C)
        physical_flag += ", DELAY_BOUNDARY_UPPER"

    # 7. AIC/BIC（k=3 参数：b1 + 漂移截距 + 斜坡）
    aic_val = compute_aic(n_samples, res_var, 3)
    bic_val = compute_bic(n_samples, res_var, 3)

    # 8. 证据输出
    reason_codes: list[str] = []
    if val_stationary:
        reason_codes.append("VAL_STATIONARY")
    if not feasibility.passed:
        reason_codes.append(feasibility.reason_code)
    if not np_passed:
        reason_codes.append(np_reason if not np_record_only else "NP_CHECK_RECORD_ONLY")
    if low_coherence:
        reason_codes.append("LOW_COHERENCE")
    if theta_source == ThetaSource.HEURISTIC_2TS:
        reason_codes.append("HEURISTIC_2TS")
    if final_status == FINAL_TEST_FAILED:
        reason_codes.append("FINAL_TEST_FAILED")
    elif final_status == FINAL_TEST_NO_DYNAMICS:
        reason_codes.append("FINAL_TEST_NO_DYNAMICS")
    # P3-06b（O-A6）：θ 边界命中信息不足显式标记（封顶与 delay_boundary_hit
    # 计算均在第 6 步，此处补充 reason code）
    if delay_boundary_capped:
        reason_codes.append("DELAY_BOUNDARY_UPPER_HIT")
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
        # P3-06a 加性证据
        final_test=final_test_record,
        nonparam_check=nonparam_check_record,
        ipdt_drift=ipdt_drift_record,
        estimator_profile={
            "profileVersion": "TUNE_IDENT_PROFILE_v1",
            "structure": ModelType.IPDT.value,
            "estimator": "IPDT_DIFF_LSQ_DRIFT",
            "orders": {"na": 0, "nb": 1, "nc": 0, "drift": 2},
            "delaySamples": int(best_d),
        },
        delay_search_info={
            "boundaryHit": delay_boundary_hit,
            "maxD": int(d_search_max),
            "candidateCount": len(delay_candidates),
        },
    )

    _final_note = (
        f", R²_final={r2_final_test:.3f}({final_status})" if r2_final_test is not None else ""
    )
    return CandidateModel(
        params=params,
        fitting_score=round(r2_eval * 100, 2),  # v1.2：val 稳态回退口径
        confidence=confidence,
        identify_method=IdentifyMethod.HISTORICAL_ARX,
        residual_test_passed=residual_white,
        excitation_score=exc_score,
        reason=(
            f"R²_val={r2_val:.3f}, R²_train={r2_train:.3f}{_final_note},"
            f" {test_note}, AIC={aic_val:.1f}, BIC={bic_val:.1f},"
            f" K={K:.4g}, θ={theta:.2g}s, 漂移={ipdt_drift_record['pvRatePerSecond']:.3g}/s"
            f"{physical_flag}"
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
