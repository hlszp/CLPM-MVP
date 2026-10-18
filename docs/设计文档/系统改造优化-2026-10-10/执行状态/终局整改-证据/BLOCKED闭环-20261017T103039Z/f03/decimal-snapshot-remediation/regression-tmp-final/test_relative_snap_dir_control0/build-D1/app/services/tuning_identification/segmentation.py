"""片段切分（算法栈层 0：辨识前事件切片）.

基于同轴后的 PV/OP/SP/MODE 时序，按 MODE 切换、数据缺口、OP 饱和等
事件边界切分为可辨识片段，防止人工/饱和/切换/启停片段污染模型。

设计依据：v6.2 方案 §10（可信辨识目标流水线）、P1-02/P1-007。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Final

import numpy as np

from app.constants.mode import AUTO_MODES, MODE_LABELS_EN

logger = logging.getLogger(__name__)

_MIN_SEGMENT_POINTS: Final[int] = 50  # 最小可辨识片段点数（P-12 物理公式的下限档）
_OP_SATURATION_MARGIN: Final[float] = 0.01  # OP 距上下限 1% 内视为饱和
_OP_SATURATION_RATIO: Final[float] = 0.80  # 80% 以上点饱和则整段排除
_LOW_VALID_RATIO: Final[float] = 0.50  # 有效样本比例低于 50% 排除

# P3-06b（P-12，oracle 冻结草案 §3 冻结值一字不差）：
# 合格连续段最小点数物理化——
#     min_pts = max(50 点, min(600 点, 2×(θ_max + 5×τ_预期) / ts))
# θ_max 为 R1（P-02）公式结果（max(300s, 5×粗估 τ+θ) ≤ 数据时长/4；无粗估
# 估时按 P-02 下限 300s 与时长/4 取小），τ_预期 为非参粗估 τ+θ（无粗估 → 0）。
# 同一物理数据不同 ts 下段资格等价（C14-ID2）；50 点下限与 600 点上限均冻结。
SEG_MIN_POINTS_FLOOR: Final[int] = 50  # P-12：max(50, ·) 下限
SEG_MIN_POINTS_CAP: Final[int] = 600  # P-12：min(600, ·) 上限
SEG_PHYS_TAU_FACTOR: Final[float] = 5.0  # P-12：5 × τ_预期
SEG_PHYS_MIN_SECONDS: Final[float] = 300.0  # P-02 下限（无粗估时的 θ_max）
SEG_PHYS_DURATION_QUARTER: Final[float] = 4.0  # P-02：θ_max ≤ 数据时长 / 4


@dataclass
class SegmentSpec:
    """切分后的片段规格（含排除原因，供 pipeline/preview 使用）.

    与 ``SegmentInfo`` 的区别：``SegmentInfo`` 含激励检测结果（辨识后填充），
    ``SegmentSpec`` 仅描述切分边界与排除原因（辨识前生成），用于 preview API
    和 pipeline 的片段筛选。

    P3-06b（P-14，加性字段、缺省不破坏既有构造/测试）：扩展对齐 P2-03
    段与事件目录层——segmentId/tsStart/tsEnd/mode/regimeId/pidVersion/
    sampleInterval；regimeId/pidVersion 恒 None（工况/PID 版本未知显式标记，
    不自动伪填工况身份，07§3.2 第 2 层）。tsStart/tsEnd 为物理秒（相对
    输入窗起点，绝对时间由快照层 window+ts_rel 承载）。
    """

    start_idx: int
    end_idx: int  # exclusive
    mode_label: str  # 主导 MODE 英文标签（"AUTO"/"MANUAL"/"UNKNOWN"）
    is_auto: bool  # 主导 MODE 是否计入自控率（AUTO/CAS/REMOTE/APC）
    exclusion_reason: str | None  # 排除原因（None=可辨识）
    valid_sample_ratio: float  # 有效样本（非 NaN）比例 0~1
    point_count: int
    # P-14 加性字段（P3-06b；None=未提供 ts 上下文，preview 旧路径不填）
    segment_id: str | None = None  # 目录内稳定标识（"SEG-001" 按出现序）
    ts_start: float | None = None  # 段起点物理秒（相对输入窗）
    ts_end: float | None = None  # 段终点物理秒（相对输入窗，exclusive）
    sample_interval: float | None = None  # 采样周期（秒）
    regime_id: str | None = None  # 工况组 ID（未知显式 None，不伪填）
    pid_version: str | None = None  # PID 参数版本（未知显式 None）
    qualify_tier: int | None = None  # P-12 资格档（1=物理首选档 2=50 点下限档；None=排除段）


def physical_min_segment_points(
    ts: float,
    total_points: int,
    theta_max_seconds: float | None = None,
    tau_expected_seconds: float | None = None,
) -> int:
    """P-12（冻结值）：合格连续段最小点数——物理时长折算.

    min_pts = max(50 点, min(600 点, ceil(2×(θ_max + 5×τ_预期)/ts)))

    - θ_max：显式给出时用之；否则按 R1（P-02）公式的可确定部分——
      min(300s, 数据时长/4)（无非参粗估时 5×粗估项不可得，按 0 处理，
      粗估接入由 pipeline 侧传入显式值）；
    - τ_预期：非参粗估 τ+θ（None → 0）；
    - 50 点下限与 600 点上限为冻结值（P-12 字面）。
    """
    if not math.isfinite(ts) or ts <= 0 or total_points <= 0:
        return _MIN_SEGMENT_POINTS
    duration_s = total_points * ts
    if theta_max_seconds is None:
        theta_max_seconds = min(SEG_PHYS_MIN_SECONDS, duration_s / SEG_PHYS_DURATION_QUARTER)
    tau_expected_seconds = tau_expected_seconds or 0.0
    physical_secs = 2.0 * (theta_max_seconds + SEG_PHYS_TAU_FACTOR * tau_expected_seconds)
    physical_pts = math.ceil(physical_secs / ts)
    return max(SEG_MIN_POINTS_FLOOR, min(SEG_MIN_POINTS_CAP, physical_pts))


def segment_signals(
    pv: list[float],
    op: list[float],
    mode: list[int] | None = None,
    op_min: float | None = None,
    op_max: float | None = None,
    min_segment_points: int = _MIN_SEGMENT_POINTS,
    ts: float | None = None,
    theta_max_seconds: float | None = None,
    tau_expected_seconds: float | None = None,
) -> list[SegmentSpec]:
    """按 MODE/缺口/饱和事件边界切分时序.

    切分边界：
    1. MODE 切换（AUTO↔MANUAL 等）
    2. PV/OP 数据缺口（NaN/inf）
    3. OP 饱和段（OP 长时间贴近上下限，仅当 op_min/op_max 提供时检测）

    排除原因（exclusion_reason）：
    - ``MANUAL_MODE``：手动模式片段（人工干预，不反映对象动态）
    - ``DATA_GAP``：片段内有效样本比例过低（< 50%）
    - ``OP_SATURATION``：OP 饱和段（阀门全开/全关，无调节动态）
    - ``TOO_SHORT``：片段点数不足（< min_segment_points）
    - ``None``：可辨识片段

    P3-06b（P-12/P-14）：提供 ``ts`` 时最小点数按物理公式折算（忽略
    ``min_segment_points`` 显式值），段规格填充 P-14 目录字段（segmentId/
    tsStart/tsEnd/sampleInterval/regimeId/pidVersion=显式 None）。tier 归档
    由调用方（pipeline 段目录构建器）处理——本函数仅按单一门槛判定。

    Args:
        pv: PV 时序（同轴后）
        op: OP 时序（同轴后）
        mode: MODE 时序（同轴后，可选；None 时跳过 MODE 切分，假设全 AUTO）
        op_min: OP 下限（量程），用于饱和检测；None 跳过饱和检测
        op_max: OP 上限（量程），用于饱和检测；None 跳过饱和检测
        min_segment_points: 最小可辨识片段点数（ts 未提供时生效）
        ts: 采样周期秒（P-12 物理化；None=旧固定点数口径，preview 兼容）
        theta_max_seconds: θ_max 物理秒（P-12；None 按可确定部分推）
        tau_expected_seconds: τ_预期 物理秒（P-12；None → 0）

    Returns:
        SegmentSpec 列表（按时间顺序，含被排除片段）
    """
    n = len(pv)
    if n == 0 or len(op) != n:
        return []

    pv_arr = np.array(pv, dtype=float)
    op_arr = np.array(op, dtype=float)
    mode_arr = np.array(mode, dtype=float) if mode else None

    effective_min = (
        physical_min_segment_points(
            ts, n, theta_max_seconds=theta_max_seconds, tau_expected_seconds=tau_expected_seconds
        )
        if ts is not None
        else min_segment_points
    )

    # ── 1. 计算切分边界 ──
    cut_points = _detect_cut_points(pv_arr, op_arr, mode_arr)

    # ── 2. 按边界切分并标注 ──
    segments: list[SegmentSpec] = []
    for seg_no, (start, end) in enumerate(cut_points, start=1):
        if end <= start:
            continue
        spec = _build_segment_spec(
            pv_arr[start:end],
            op_arr[start:end],
            mode_arr[start:end] if mode_arr is not None else None,
            start,
            end,
            op_min,
            op_max,
            effective_min,
        )
        if ts is not None:
            spec.segment_id = f"SEG-{seg_no:03d}"
            spec.ts_start = round(float(start) * ts, 4)
            spec.ts_end = round(float(end) * ts, 4)
            spec.sample_interval = float(ts)
            spec.regime_id = None  # 工况未知显式 None（07§3.2：不自动伪填）
            spec.pid_version = None  # PID 版本未知显式 None
            spec.qualify_tier = 1 if spec.exclusion_reason is None else None
        segments.append(spec)

    return segments


def _detect_cut_points(
    pv: np.ndarray,
    op: np.ndarray,
    mode: np.ndarray | None,
) -> list[tuple[int, int]]:
    """检测切分边界点，返回 [(start, end), ...] 区间列表.

    边界来源：
    - 序列首尾（0, n）
    - MODE 变化点
    - PV/OP NaN/inf 缺口的起止
    """
    n = len(pv)
    cuts: set[int] = {0, n}

    # MODE 变化点
    if mode is not None and len(mode) == n:
        valid_mode = np.isfinite(mode)
        # 将 NaN mode 标记为 -1（与任何合法 mode 不同，触发切分）
        mode_int = np.where(valid_mode, np.round(mode).astype(int), -1)
        changes = np.where(np.diff(mode_int) != 0)[0] + 1
        cuts.update(changes.tolist())

    # PV/OP 缺口（NaN/inf）
    finite_mask = np.isfinite(pv) & np.isfinite(op)
    if not finite_mask.all():
        # 首尾补 True（视为有效），便于检测边界转换
        padded = np.pad(finite_mask, (1, 1), constant_values=True)
        # 缺口开始：当前位置 invalid 且前一位置 valid（valid→invalid 转换）
        gap_starts = np.where((~padded[1:-1]) & padded[:-2])[0]
        # 缺口结束：当前位置 valid 且前一位置 invalid（invalid→valid 转换）
        # 该 index 即缺口后首个有效点，作为新段起点
        gap_ends = np.where(padded[1:-1] & (~padded[:-2]))[0]
        cuts.update(gap_starts.tolist())
        cuts.update(gap_ends.tolist())

    sorted_cuts = sorted(cuts)
    return [(sorted_cuts[i], sorted_cuts[i + 1]) for i in range(len(sorted_cuts) - 1)]


def _build_segment_spec(
    pv_seg: np.ndarray,
    op_seg: np.ndarray,
    mode_seg: np.ndarray | None,
    start: int,
    end: int,
    op_min: float | None,
    op_max: float | None,
    min_pts: int,
) -> SegmentSpec:
    """为单个片段构建 SegmentSpec（含主导 mode、排除原因、有效样本比例）."""
    n = len(pv_seg)
    finite_mask = np.isfinite(pv_seg) & np.isfinite(op_seg)
    valid_count = int(finite_mask.sum())
    valid_ratio = valid_count / n if n > 0 else 0.0

    # 主导 MODE（众数）
    if mode_seg is not None and len(mode_seg) > 0:
        valid_modes = mode_seg[np.isfinite(mode_seg)]
        if len(valid_modes) > 0:
            vals, counts = np.unique(np.round(valid_modes).astype(int), return_counts=True)
            dominant_mode = int(vals[np.argmax(counts)])
            is_auto = dominant_mode in AUTO_MODES
            mode_label = MODE_LABELS_EN.get(dominant_mode, "UNKNOWN")
        else:
            dominant_mode = -1
            is_auto = False
            mode_label = "UNKNOWN"
    else:
        # 无 MODE 信息：假设 AUTO（兼容旧路径，不因缺 MODE 而排除）
        dominant_mode = -1
        is_auto = True
        mode_label = "UNKNOWN"

    # 排除原因判定（优先级：MANUAL > DATA_GAP > SATURATION > TOO_SHORT）
    exclusion_reason: str | None = None
    if not is_auto and dominant_mode == 0:
        exclusion_reason = "MANUAL_MODE"
    elif valid_ratio < _LOW_VALID_RATIO:
        exclusion_reason = "DATA_GAP"
    elif op_min is not None and op_max is not None and op_max > op_min:
        if _is_op_saturated(op_seg, op_min, op_max):
            exclusion_reason = "OP_SATURATION"
    elif n < min_pts:
        exclusion_reason = "TOO_SHORT"

    # 太短检查放在最后（即使有其他原因，太短也标注太短？不：保持首个原因）
    if exclusion_reason is None and n < min_pts:
        exclusion_reason = "TOO_SHORT"

    return SegmentSpec(
        start_idx=start,
        end_idx=end,
        mode_label=mode_label,
        is_auto=is_auto,
        exclusion_reason=exclusion_reason,
        valid_sample_ratio=round(valid_ratio, 4),
        point_count=n,
    )


def _is_op_saturated(op: np.ndarray, op_min: float, op_max: float) -> bool:
    """检测 OP 是否长时间贴近上下限（阀门饱和）.

    饱和定义：超过 _OP_SATURATION_RATIO 的点落在 [op_min, op_min+margin] 或
    [op_max-margin, op_max] 区间内。饱和段无调节动态，不适合辨识。
    """
    finite_op = op[np.isfinite(op)]
    if len(finite_op) == 0:
        return False
    margin = (op_max - op_min) * _OP_SATURATION_MARGIN
    sat_low = np.sum(finite_op <= op_min + margin)
    sat_high = np.sum(finite_op >= op_max - margin)
    return (sat_low + sat_high) / len(finite_op) > _OP_SATURATION_RATIO


def select_best_segment(segments: list[SegmentSpec]) -> SegmentSpec | None:
    """从片段列表中选择最佳可辨识片段.

    选择策略：
    1. 仅考虑 exclusion_reason is None 的可辨识片段
    2. 优先点数最多（数据量最大）的片段
    3. 点数相同取 valid_sample_ratio 最高

    Args:
        segments: segment_signals 输出

    Returns:
        最佳 SegmentSpec，或 None（无可辨识片段）
    """
    candidates = [s for s in segments if s.exclusion_reason is None]
    if not candidates:
        return None
    return max(candidates, key=lambda s: (s.point_count, s.valid_sample_ratio))


__all__ = [
    "SEG_MIN_POINTS_CAP",
    "SEG_MIN_POINTS_FLOOR",
    "SegmentSpec",
    "physical_min_segment_points",
    "segment_signals",
    "select_best_segment",
]
