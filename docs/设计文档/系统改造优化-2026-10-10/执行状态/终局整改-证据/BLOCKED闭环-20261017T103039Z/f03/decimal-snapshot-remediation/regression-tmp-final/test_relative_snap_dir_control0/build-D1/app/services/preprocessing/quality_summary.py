"""QualitySummary 生成模块.

计算数据块的质量摘要，包括有效数据率、无效率、缺失率等。

v6.2 变更（可信度统一 Phase 1）：质量摘要的 ``valid_rate``（全 tag 交集）
仅用于**审计展示与数据血缘**，不参与可信度判定。可信度判定改由
**回路级 valid_rate**（核心 tag pv/sp/op/mode 交集 / point_count）负责，
由 :meth:`DataQualityAssessor.compute_loop_valid_rate` 统一计算，
KPI/诊断/整定三链路共享同一口径。详见可信度统一改进方案 §4.3。

设计依据：算法说明 §3.4.2 步骤⑧, §3.7.2
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.contracts.data_types import QualityStatus, QualitySummary
from app.services.preprocessing.quality_code import map_quality_code

logger = logging.getLogger(__name__)


def compute_quality_summary(
    validity: dict[str, list[bool]],
    timestamps: list[datetime],
    point_count: int,
    quality_codes: list[int] | None = None,
    expected_interval_s: float = 1.0,
    unknown_slot_count: int | None = None,
    window_seconds: float | None = None,
    unknown_seconds: float | None = None,
) -> QualitySummary:
    """生成数据质量摘要（算法说明 §3.4.2 步骤⑧）.

    计算 valid_rate / bad_rate / missing_rate。

    .. note::
        此处的 valid_rate 是**所有 tag 有效性的交集**，仅用于审计展示
        和数据血缘（DataLineage）。**不参与可信度判定**。

        可信度判定由**回路级 valid_rate** 负责（可信度统一 Phase 1）：
        由 :meth:`DataQualityAssessor.compute_loop_valid_rate` 计算核心 tag
        （pv/sp/op/mode）交集 / point_count，KPI/诊断/整定三链路共享。
        详见可信度统一改进方案 §4.3。

    Args:
        validity: 有效性标记字典，key 为 ``{tag}_valid``
        timestamps: 时间戳序列
        point_count: 数据点数
        quality_codes: PV 质量码数组（仅 QUALITY_HF 时传入，用于好值率）
        expected_interval_s: 期望采样间隔（秒），用于缺失检测
        window_seconds: 请求窗口秒数（P3-08b 半开 [start,end) 口径；
            None=未记录。R/Auto/Sat 分母=完整请求窗口秒数，本字段使
            质量摘要与比率类指标分母可对账）
        unknown_seconds: 无依据区间秒数（六因+窗口未覆盖；不补时不缩分母；
            None=未记录）

    Returns:
        QualitySummary 质量摘要（审计用）

    设计依据：算法说明 §3.4.2 步骤⑧, §3.7.2；P3-08b 契约 §2.1 比率口径
    """
    total = point_count
    if total == 0:
        return QualitySummary(window_seconds=window_seconds, unknown_seconds=unknown_seconds)

    # 取所有信号 valid 的交集作为"该时间戳是否有效"（审计用）
    # 注意：此交集仅用于审计展示和数据血缘，不参与可信度判定。
    # 可信度判定使用回路级 valid_rate（核心 tag pv/sp/op/mode 交集 / point_count），
    # 由 DataQualityAssessor.compute_loop_valid_rate 统一计算，
    # 避免无关 tag（如 PID_P/PID_I/PID_D）拉低有效数据率。
    all_valid = [True] * total
    for tag_validity in validity.values():
        for i, v in enumerate(tag_validity):
            if i < total:
                all_valid[i] = all_valid[i] and v

    valid_count = sum(1 for v in all_valid if v)
    bad_count = total - valid_count

    # 缺失检测（AD03/I05 §4.2）：point 网格行数恒满，缺失**不能**按
    # "期望-实际行数"推断（恒 0 会把未知占位洗白）——point 路径由调用方
    # 传入上下文统计的未知槽数（覆盖依据）；legacy 保留原行数差口径
    if unknown_slot_count is not None:
        missing_count = max(0, min(int(unknown_slot_count), total))
        expected_count = total
    else:
        expected_count = _compute_expected_count(timestamps, expected_interval_s)
        missing_count = max(0, expected_count - total)

    valid_rate = valid_count / total if total else 0.0
    bad_rate = bad_count / total if total else 0.0
    # CAL-08 修复（P1-01）：分母判据用 expected_count > 0 而非 > total——
    # point 网格分支 expected_count==total 且 missing_count 可 >0（未知槽
    # 占位），原判据使 missing_rate 恒 0，出现 count>0 而 rate=0 的口径
    # 矛盾（100 网格 20 UNKNOWN → count=20 且 rate=0.2）。legacy 分支
    # expected≥total 恒成立：expected==total 时 missing_count=0，rate=0
    # 不变；expected>total 时数值不变。
    missing_rate = missing_count / expected_count if expected_count > 0 else 0.0

    # 好值率（仅当有质量码时计算）
    good_value_rate: float | None = None
    if quality_codes is not None and len(quality_codes) > 0:
        good_count = sum(1 for qc in quality_codes if map_quality_code(qc) == QualityStatus.GOOD)
        good_value_rate = good_count / len(quality_codes)

    # PV 数值可用比例（P3-10a，10§3.4 Good 行：QC Good 与 usable 分开，
    # C04-EA3）：``pv_valid`` 口径——越限/坏质量等预处理数值资格判定的交。
    # good_value_rate 反映**来源质量码**（缺质量码=None 不可判定，不用本值
    # 冒充）；usable_rate 反映**数值可用性**（含越限，QC Good 但 PV 越界的
    # 点 usable=False）。无 pv_valid 标记时 None（未记录，不猜测）。
    pv_valid = validity.get("pv_valid")
    usable_rate: float | None = None
    if pv_valid and total > 0:
        usable_count = sum(1 for v in pv_valid[:total] if v)
        usable_rate = usable_count / total

    summary = QualitySummary(
        total_count=total,
        valid_count=valid_count,
        bad_count=bad_count,
        missing_count=missing_count,
        valid_rate=round(valid_rate, 4),
        bad_rate=round(bad_rate, 4),
        missing_rate=round(missing_rate, 4),
        good_value_rate=round(good_value_rate, 4) if good_value_rate is not None else None,
        usable_rate=round(usable_rate, 4) if usable_rate is not None else None,
        window_seconds=window_seconds,
        unknown_seconds=unknown_seconds,
    )

    logger.debug(
        "QualitySummary: total=%d, valid=%d, bad=%d, missing=%d, "
        "valid_rate=%.4f, good_value_rate=%s",
        summary.total_count,
        summary.valid_count,
        summary.bad_count,
        summary.missing_count,
        summary.valid_rate,
        summary.good_value_rate,
    )
    return summary


def _compute_expected_count(timestamps: list[datetime], expected_interval_s: float) -> int:
    """根据时间跨度计算期望点数.

    Args:
        timestamps: 时间戳序列
        expected_interval_s: 期望采样间隔（秒）

    Returns:
        期望点数（至少为 len(timestamps)）
    """
    if len(timestamps) < 2 or expected_interval_s <= 0:
        return len(timestamps)
    duration = (timestamps[-1] - timestamps[0]).total_seconds()
    expected = int(duration / expected_interval_s) + 1
    return max(expected, len(timestamps))


def compute_consecutive_segments(
    all_valid: list[bool],
    min_consecutive_points: int,
) -> list[tuple[int, int]]:
    """计算连续有效段（算法说明 §3.4.2 步骤⑥）。

    标记连续 valid=True 的段，当缺口（valid=False）出现时切断。
    长度不足 min_consecutive_points 的段被丢弃。

    Args:
        all_valid: 每个时间戳是否全有效（所有 tag valid 的交集）
        min_consecutive_points: 连续有效最短段点数（算法说明 §3.4.4）

    Returns:
        连续有效段索引列表 ``[(start_idx, end_idx), ...]``（闭区间）
    """
    segments: list[tuple[int, int]] = []
    n = len(all_valid)
    i = 0
    while i < n:
        if all_valid[i]:
            start = i
            while i < n and all_valid[i]:
                i += 1
            end = i - 1
            if (end - start + 1) >= min_consecutive_points:
                segments.append((start, end))
        else:
            i += 1
    return segments


def split_segments_at_boundaries(
    segments: list[tuple[int, int]],
    timestamps: list[datetime],
    boundary_times: list[datetime],
) -> list[tuple[int, int]]:
    """按分段边界把连续段一分为二（P3-08b 跨 gap 禁拼接，契约 §2.3）.

    缺口、改绑、量程/单位解释切换、被剔除暂态均形成动态片段边界；边界
    不等于坏质量（不得用伪造坏点表示），两侧各自保留为独立段——禁止
    删除边界后拼成连续信号。边界 b 生效于"最后一个 ts < b 的槽之后"：
    即 ts[i-1] < b <= ts[i] 时在 i-1/i 之间切开。

    Args:
        segments: 连续段列表（闭区间索引对，升序）
        timestamps: 槽时间戳（与索引对应）
        boundary_times: 边界生效时刻（改绑/解释切换/布局切换）

    Returns:
        切分后的段列表（保持升序；不丢弃任何一侧）
    """
    if not segments or not boundary_times:
        return segments

    def _naive_utc(dt: datetime) -> datetime:
        """naive/aware 混合比较归一（aware → naive UTC；naive 视为 UTC）。"""
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(UTC).replace(tzinfo=None)

    try:
        boundaries = sorted(_naive_utc(b) for b in boundary_times)
        ts_naive = [_naive_utc(t) for t in timestamps]
    except TypeError:
        return segments
    out: list[tuple[int, int]] = []
    for start, end in segments:
        cut_points: list[int] = []
        for i in range(start + 1, end + 1):
            if i >= len(ts_naive):
                break
            prev_ts = ts_naive[i - 1]
            cur_ts = ts_naive[i]
            if any(prev_ts < b <= cur_ts for b in boundaries):
                cut_points.append(i)
        if not cut_points:
            out.append((start, end))
            continue
        prev = start
        for cp in cut_points + [end + 1]:
            seg_end = cp - 1
            if seg_end >= prev:
                out.append((prev, seg_end))
            prev = cp
    return out


# ---------------------------------------------------------------------------
# R14 稀疏数据准入（2026-09-06）：实际采样间隔与时间覆盖率
# ---------------------------------------------------------------------------


def compute_median_interval(timestamps: list[datetime]) -> float | None:
    """相邻时间戳的中位间隔（秒）。

    作为"实际采样间隔"的稳健估计（中位数不受个别缺口/重复影响），
    供 ``DataBlock.sampling_freq`` 标签与 ARMA 等间隔准入使用。

    热路径口径：仅做 datetime 排序 + 减法 + ``timedelta.total_seconds()``，
    不对 naive datetime 逐点调 ``.timestamp()``（AGENTS.md 红线）。

    Args:
        timestamps: 时间戳序列（无需预排序）

    Returns:
        中位间隔（秒）；点数 < 2 或元素不可比较时返回 None
    """
    if len(timestamps) < 2:
        return None
    try:
        ts_sorted = sorted(timestamps)
        deltas = sorted(
            (b - a).total_seconds() for a, b in zip(ts_sorted, ts_sorted[1:], strict=False)
        )
    except TypeError:
        return None
    if not deltas:
        return None
    n = len(deltas)
    mid = n // 2
    if n % 2 == 1:
        return float(deltas[mid])
    return (deltas[mid - 1] + deltas[mid]) / 2.0


def compute_time_coverage(
    timestamps: list[datetime],
    expected_interval_s: float,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> float:
    """时间覆盖率 = 去重后实际点数 / 窗口期望点数（R14 可信度准入输入）。

    期望点数 = 窗口时长 / ``expected_interval_s`` + 1（含首尾）。
    ``expected_interval_s`` 必须是**契约采样间隔**（如 FLOW=1s），不能用
    实际中位间隔——否则稀疏 COV 数据（120 点/30s/1h）会被洗白成
    coverage=100%，失去缺口检出能力。

    窗口来源：
    - 优先用查询窗口 ``[window_start, window_end]``（可感知窗口头部/尾部
      整段缺失，如 TD 只返回了 1 小时窗的后 30 分钟）；
    - 缺失时退化为首尾时间戳跨度（无法感知头尾截断，由 kpi_calc 门禁的
      窗口口径兜底）。

    Args:
        timestamps: 时间戳序列
        expected_interval_s: 契约期望采样间隔（秒），≤0 按 1s
        window_start / window_end: 查询窗口边界（None 时用数据跨度）

    Returns:
        覆盖率 ∈ [0, 1]；无时间戳返回 0
    """
    if not timestamps:
        return 0.0
    if expected_interval_s <= 0:
        expected_interval_s = 1.0

    if window_start is not None and window_end is not None and window_end > window_start:
        duration_s = (window_end - window_start).total_seconds()
        expected = duration_s / expected_interval_s + 1.0
    else:
        try:
            ts_sorted = sorted(timestamps)
            duration_s = (ts_sorted[-1] - ts_sorted[0]).total_seconds()
        except TypeError:
            return 0.0
        expected = (
            duration_s / expected_interval_s + 1.0 if duration_s > 0 else float(len(timestamps))
        )

    if expected <= 0:
        return 0.0
    # 去重后点数（重复 ts 不重复计数）
    unique_count = len(set(timestamps))
    return max(0.0, min(1.0, unique_count / expected))
