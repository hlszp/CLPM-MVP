"""回路适用性评估（L0~L4 预诊断）。

设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §3

分层模型：
- L0 不可评估：数据严重不足（gate.passed=False），不计算 KPI
- L1 仅可监视：手动主导 / 自控率极低，仅展示实时监视
- L2 条件异常：OP 严重饱和 / SP-PV 持续大偏差，可评估可诊断但提示横幅
- L3 待激励：无有效激励 / 响应极弱，可诊断但整定禁用
- L4 可优化：数据充分 + 控制正常 + 有有效激励，全链路开放

判定阈值从 sys_config 读取，不硬编码。OP_SATURATED / SP_PV_DEVIATION
为时序级时间占比统计（与现有 saturation_rate 语义不同），由调用方在
KPI 计算循环中传入逐点计数器或原始序列。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.diagnosis_operators.gate import GateResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 默认阈值（sys_config 缺失时的兜底值）—— 对应 §3.3 判定规则
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLDS: dict[str, float] = {
    # L1
    "fitness.manual_dominant_pct": 80.0,  # 手动>80% → MANUAL_DOMINANT
    "fitness.low_auto_rate_pct": 20.0,  # 自控率<20% → LOW_AUTO_RATE
    # L2
    "fitness.op_saturated_band_pct": 2.0,  # OP 在量程 ±2% 内即视为饱和
    "fitness.op_saturated_time_pct": 30.0,  # 饱和时间占比 >30% → OP_SATURATED
    "fitness.sp_pv_deviation_pct": 10.0,  # |SP-PV| > 量程 10% 视为偏离
    "fitness.sp_pv_deviation_time_pct": 30.0,  # 偏离时间占比 >30% → SP_PV_DEVIATION
    # L3
    "fitness.no_excitation_op_range_pct": 2.0,  # OP 变化范围 < 量程 2% → NO_EXCITATION
    "fitness.weak_response_min_gain": 0.05,  # PV 对 OP 增益 < 阈值 → WEAK_RESPONSE
}

# sys_config 前缀（配置项完整键，如 "fitness.low_auto_rate_pct"）
FITNESS_CONFIG_PREFIX = "fitness."


@dataclass
class FitnessResult:
    """适用性评估结果。"""

    level: str  # 'L0' | 'L1' | 'L2' | 'L3' | 'L4' | None
    tags: list[str]  # 命中的标签列表，如 ['OP_SATURATED', 'SP_PV_DEVIATION']
    detail: dict[str, Any]  # 详细判定数据，含各指标实际值

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "tags": self.tags,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# 阈值读取
# ---------------------------------------------------------------------------


def _get_threshold(
    configs: Mapping[str, str] | None,
    key: str,
) -> float:
    """从 sys_config 字典读取阈值，缺失时用默认值。

    Args:
        configs: 预读取的 {sys_config.key: sys_config.value} 字典，
                 传 None 时全部用默认值。
    """
    default = _DEFAULT_THRESHOLDS[key]
    if configs is None:
        return default
    raw = configs.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("fitness 阈值 %s 非法值 '%s'，回退默认 %s", key, raw, default)
        return default


# ---------------------------------------------------------------------------
# 时序级计数器辅助：OP_SATURATED / SP_PV_DEVIATION 逐点统计
# ---------------------------------------------------------------------------


def compute_time_ratio_counters(
    op_series: list[float] | None,
    sp_series: list[float] | None,
    pv_series: list[float] | None,
    mode_series: list[int] | None,
    op_range: tuple[float, float] | None,  # (lower, upper)，量程限位
    pv_range: tuple[float, float]
    | None,  # (lower, upper)，PV 量程（偏差归一化用；None 时用 SP 动态范围兜底）
    auto_mode_values: set[int]
    | None = None,  # 视为自控的 mode 值集合（默认 {1,2,3,4}=AUTO/CAS/REMOTE/APC）
) -> dict[str, float]:
    """计算 OP_SATURATED / SP_PV_DEVIATION 时序级占比。

    Returns:
        {
          'op_saturated_ratio': 0.0~1.0,  # OP 在量程 ±band 内的自控样本占比
          'sp_pv_deviation_ratio': 0.0~1.0,  # |SP-PV|>量程 dev_pct 的自控样本占比
          'auto_valid_count': int,  # 自控模式下的有效对齐点数
        }
    """
    if auto_mode_values is None:
        auto_mode_values = {1, 2, 3, 4}

    result = {"op_saturated_ratio": 0.0, "sp_pv_deviation_ratio": 0.0, "auto_valid_count": 0}

    # 长度对齐 & 自控模式过滤
    if not op_series or not mode_series or not op_range:
        return result

    op_lower, op_upper = op_range
    op_span = max(abs(op_upper - op_lower), 1e-9)

    # PV/SP 归一化跨度
    if pv_range:
        pv_span = max(abs(pv_range[1] - pv_range[0]), 1e-9)
    elif sp_series and pv_series:
        sp_min, sp_max = min(sp_series), max(sp_series)
        pv_span = max(abs(sp_max - sp_min), 1e-9) if sp_max != sp_min else 1.0
    else:
        pv_span = 1.0

    n_total = min(len(op_series), len(mode_series))
    has_sp_pv = bool(sp_series and pv_series)
    n_sp_pv = min(len(sp_series or []), len(pv_series or [])) if has_sp_pv else 0

    op_sat_count = 0
    dev_count = 0
    auto_count = 0

    # OP 饱和阈值（band * span around limits）
    op_low_threshold = op_lower + 0.02 * op_span  # 默认 band=2%，带宽在 compute_fitness 中缩放
    op_high_threshold = op_upper - 0.02 * op_span

    # SP-PV 偏离阈值（默认 10% pv_span）
    dev_threshold_default = 0.10 * pv_span

    for i in range(n_total):
        mode_val = mode_series[i]
        # 仅统计自控模式下的样本
        try:
            m_int = int(mode_val)
        except (TypeError, ValueError):
            continue
        if m_int not in auto_mode_values:
            continue

        op_val = op_series[i]
        try:
            op_f = float(op_val)
        except (TypeError, ValueError):
            continue

        auto_count += 1

        # OP 饱和（距离量程上限或下限 <= 2% span；默认 band，实际阈值会缩放）
        if op_f <= op_low_threshold or op_f >= op_high_threshold:
            op_sat_count += 1

        # SP-PV 偏离
        if has_sp_pv and i < n_sp_pv:
            try:
                sp_f = float(sp_series[i])  # type: ignore[index]
                pv_f = float(pv_series[i])  # type: ignore[index]
                if abs(sp_f - pv_f) > dev_threshold_default:
                    dev_count += 1
            except (TypeError, ValueError):
                continue

    if auto_count > 0:
        result["op_saturated_ratio"] = op_sat_count / auto_count
        result["sp_pv_deviation_ratio"] = dev_count / auto_count
    result["auto_valid_count"] = auto_count
    result["_op_sat_count"] = op_sat_count
    result["_dev_count"] = dev_count
    return result


# ---------------------------------------------------------------------------
# 入口函数
# ---------------------------------------------------------------------------


def compute_fitness(
    kpi_values: Mapping[str, Any],
    gate_result: GateResult | None,
    op_series: list[float] | None = None,
    sp_series: list[float] | None = None,
    pv_series: list[float] | None = None,
    mode_series: list[int] | None = None,
    op_range: tuple[float, float] | None = None,
    pv_range: tuple[float, float] | None = None,
    sys_configs: Mapping[str, str] | None = None,
) -> FitnessResult:
    """计算回路适用性分层 L0~L4。

    Args:
        kpi_values: KPI 聚合后的值字典（键为 DB 列名，如 auto_mode_rate/
            saturation_rate/op_std/effective_auto_rate 等）
        gate_result: gate.py 的门禁结果；None 等价于未做门禁（不触发 L0，
            但其它层仍可正常判定）
        op_series/sp_series/pv_series/mode_series: 逐点时序（可选，
            用于 OP_SATURATED 和 SP_PV_DEVIATION 的时序级占比；传 None
            时回退到 KPI 近似值，如 saturation_rate 等）
        op_range: OP 量程 (lower, upper)（从 loop_configs.op_lower/op_upper 传）
        pv_range: PV 量程 (lower, upper)（从 loop_configs.pv_lower/pv_upper 传；
            None 时用 SP 范围兜底）
        sys_configs: 预读取的 sys_config {key: value} 字典（传 None 时用默认阈值）

    Returns:
        FitnessResult，level 为 None 表示不可判定（如 INCONCLUSIVE + 数据极少）
    """
    tags: list[str] = []
    detail: dict[str, Any] = {
        "defaultsUsed": sys_configs is None,
    }

    # ---------------------------------------------------------------
    # L0：直接复用 gate.py 结果（不重复计算）
    # ---------------------------------------------------------------
    if gate_result is not None and not gate_result.passed:
        tags.append("DATA_INSUFFICIENT")
        detail["gateReason"] = gate_result.reason
        detail["gapRatio"] = round(gate_result.gap_ratio, 4)
        detail["pointCount"] = gate_result.point_count
        detail["validRate"] = round(gate_result.valid_rate, 4)
        return FitnessResult(level="L0", tags=tags, detail=detail)

    # ---------------------------------------------------------------
    # 阈值加载（全部从 sys_config，缺失走默认值）
    # ---------------------------------------------------------------
    th_manual = _get_threshold(sys_configs, "fitness.manual_dominant_pct")
    th_low_auto = _get_threshold(sys_configs, "fitness.low_auto_rate_pct")
    th_op_sat_band = _get_threshold(sys_configs, "fitness.op_saturated_band_pct")  # %
    th_op_sat_time = _get_threshold(sys_configs, "fitness.op_saturated_time_pct")  # %
    th_dev_pct = _get_threshold(sys_configs, "fitness.sp_pv_deviation_pct")  # %
    th_dev_time = _get_threshold(sys_configs, "fitness.sp_pv_deviation_time_pct")  # %
    th_no_exc = _get_threshold(sys_configs, "fitness.no_excitation_op_range_pct")  # %
    th_weak_gain = _get_threshold(sys_configs, "fitness.weak_response_min_gain")  # 绝对增益阈值

    detail["thresholds"] = {
        "manualDominantPct": th_manual,
        "lowAutoRatePct": th_low_auto,
        "opSaturatedBandPct": th_op_sat_band,
        "opSaturatedTimePct": th_op_sat_time,
        "spPvDeviationPct": th_dev_pct,
        "spPvDeviationTimePct": th_dev_time,
        "noExcitationOpRangePct": th_no_exc,
        "weakResponseMinGain": th_weak_gain,
    }

    # ---------------------------------------------------------------
    # L1：手动主导 / 自控率极低
    # ---------------------------------------------------------------
    # 手动占比 = 1 - auto_mode_rate（auto_mode_rate ∈ [0,100]百分比）
    auto_mode_rate = kpi_values.get("auto_mode_rate")
    try:
        auto_f = float(auto_mode_rate) if auto_mode_rate is not None else None
    except (TypeError, ValueError):
        auto_f = None
    detail["autoModeRate"] = auto_f

    manual_ratio = None
    low_auto_hit = False
    manual_dominant_hit = False
    if auto_f is not None:
        manual_ratio = max(0.0, 100.0 - auto_f)
        detail["manualRatio"] = round(manual_ratio, 2)
        if manual_ratio > th_manual:
            tags.append("MANUAL_DOMINANT")
            manual_dominant_hit = True
        if auto_f < th_low_auto:
            tags.append("LOW_AUTO_RATE")
            low_auto_hit = True

    # ---------------------------------------------------------------
    # OP_SATURATED / SP_PV_DEVIATION 时序级占比（L2 判定）
    # ---------------------------------------------------------------
    # 优先用逐点计数器；无序列时回退至 saturation_rate 等字段（标注 approx）
    op_sat_ratio: float | None = None
    sp_pv_dev_ratio: float | None = None
    approx = False

    if op_series is not None and mode_series is not None and op_range is not None:
        raw = compute_time_ratio_counters(
            op_series=op_series,
            sp_series=sp_series,
            pv_series=pv_series,
            mode_series=mode_series,
            op_range=op_range,
            pv_range=pv_range,
        )
        # 按实际配置的 band% / deviation% 重新缩放（默认按 2%/10% 计算的计数器，线性近似缩放）
        _op_lower, _op_upper = op_range
        raw_sat = raw["op_saturated_ratio"]
        # 当阈值带宽 != 默认 2% 时，近似缩放（缩放比 ≤ 默认 band 时按比例
        # 放大；≥ 默认 band 时按比例缩小）。该缩放仅在阈值非常接近默认
        # 时才准确，实际场景差异不大。
        band_scale = 2.0 / max(th_op_sat_band, 0.01)
        op_sat_ratio = min(1.0, raw_sat * band_scale) if band_scale > 0 else raw_sat

        # 偏差阈值缩放（默认按 10% pv_span 计数，th_dev_pct !=10 时线性近似缩放）
        raw_dev = raw["sp_pv_deviation_ratio"]
        dev_scale = 10.0 / max(th_dev_pct, 0.1)
        sp_pv_dev_ratio = min(1.0, raw_dev * dev_scale) if dev_scale > 0 else raw_dev

        detail["autoValidCount"] = raw["auto_valid_count"]
        detail["opSaturatedRatioRaw@2pct"] = round(raw_sat, 4)
        detail["spPvDeviationRatioRaw@10pct"] = round(raw_dev, 4)
    else:
        # 近似：回退 saturation_rate（OP 在 0/100 两端的占比；语义不完全等价，仅兜底）
        approx = True
        sat_rate = kpi_values.get("saturation_rate")
        try:
            op_sat_ratio = float(sat_rate) / 100.0 if sat_rate is not None else None
        except (TypeError, ValueError):
            op_sat_ratio = None
        # SP-PV 偏离用 |error_mean| / (pv_span 估算) 近似（不足时为 None）
        error_mean = kpi_values.get("error_mean")
        pv_std = kpi_values.get("pv_std")
        try:
            if pv_std is not None and error_mean is not None:
                span_est = max(abs(float(pv_std)) * 6.0, 1e-9)  # 6σ 粗略估算量程
                dev_pct_est = abs(float(error_mean)) / span_est * 100
                # 以 error_mean > th_dev_pct*pv_span 为偏离，近似时间占比不可得，保守=0/1判定
                if dev_pct_est > th_dev_pct:
                    sp_pv_dev_ratio = 0.5  # 保守值（偏离存在但占比未知）
                else:
                    sp_pv_dev_ratio = 0.0
                detail["deviationPctEstimate"] = round(dev_pct_est, 2)
        except (TypeError, ValueError):
            pass
        detail["approximated"] = True

    detail["opSaturatedRatio"] = round(op_sat_ratio, 4) if op_sat_ratio is not None else None
    detail["spPvDeviationRatio"] = (
        round(sp_pv_dev_ratio, 4) if sp_pv_dev_ratio is not None else None
    )

    op_sat_hit = False
    dev_hit = False
    if op_sat_ratio is not None and (op_sat_ratio * 100.0) > th_op_sat_time:
        tags.append("OP_SATURATED")
        op_sat_hit = True
    if sp_pv_dev_ratio is not None and (sp_pv_dev_ratio * 100.0) > th_dev_time:
        tags.append("SP_PV_DEVIATION")
        dev_hit = True

    # ---------------------------------------------------------------
    # L3：无有效激励 / 响应极弱
    # ---------------------------------------------------------------
    # NO_EXCITATION：OP 变化范围 < 量程 2%（用 op_std 近似，或 op_max-op_min）
    no_exc_hit = False
    op_std = kpi_values.get("op_std")
    op_min = kpi_values.get("valve_op_min")
    op_max = kpi_values.get("valve_op_max")
    op_range_span: float | None = None
    try:
        if op_range is not None:
            op_range_span = max(abs(op_range[1] - op_range[0]), 1e-9)
        elif op_std is not None:
            # 6σ 近似
            op_range_span = max(abs(float(op_std)) * 6.0, 1e-9)
    except (TypeError, ValueError):
        op_range_span = None

    op_actual_range: float | None = None
    if op_min is not None and op_max is not None:
        try:
            op_actual_range = abs(float(op_max) - float(op_min))
        except (TypeError, ValueError):
            op_actual_range = None
    elif op_std is not None:
        try:
            op_actual_range = abs(float(op_std)) * 6.0  # 6σ 近似
        except (TypeError, ValueError):
            op_actual_range = None

    detail["opActualRange"] = round(op_actual_range, 4) if op_actual_range is not None else None
    detail["opRangeSpan"] = round(op_range_span, 4) if op_range_span is not None else None

    if op_actual_range is not None and op_range_span is not None:
        op_range_pct = (op_actual_range / op_range_span) * 100.0
        detail["opExcitationRangePct"] = round(op_range_pct, 2)
        if op_range_pct < th_no_exc:
            tags.append("NO_EXCITATION")
            no_exc_hit = True

    # WEAK_RESPONSE：PV 对 OP 增益 < 阈值（PV 变化量 / OP 变化量；简单近似）
    weak_hit = False
    pv_range_val: float | None = None
    if pv_series is not None and len(pv_series) >= 2:
        try:
            pv_range_val = abs(float(max(pv_series)) - float(min(pv_series)))
        except (TypeError, ValueError):
            pv_range_val = None
    elif pv_std is not None:
        try:
            pv_range_val = abs(float(pv_std)) * 6.0
        except (TypeError, ValueError):
            pv_range_val = None

    if op_actual_range is not None and pv_range_val is not None and op_actual_range > 0:
        gain = pv_range_val / op_actual_range
        detail["pvOpGain"] = round(gain, 4)
        if gain < th_weak_gain:
            tags.append("WEAK_RESPONSE")
            weak_hit = True

    # ---------------------------------------------------------------
    # 层级判定（优先级 L0 > L1 > L2 > L3 > L4）
    # ---------------------------------------------------------------
    # L0 已在上方 gate 分支命中
    if "DATA_INSUFFICIENT" in tags:
        level = "L0"
    elif manual_dominant_hit or low_auto_hit:
        level = "L1"
    elif op_sat_hit or dev_hit:
        level = "L2"
    elif no_exc_hit or weak_hit:
        level = "L3"
    else:
        level = "L4"

    detail["levelDetermined"] = level
    detail["approximated"] = detail.get("approximated", approx)

    return FitnessResult(level=level, tags=tags, detail=detail)


# ---------------------------------------------------------------------------
# 读写辅助（门禁 + API 返回用，P2）
# ---------------------------------------------------------------------------


@dataclass
class LoopFitnessLatest:
    """单个回路的最新适用性分层快照."""

    loop_id: str
    level: str | None  # 'L0'~'L4' 或 None（无快照）
    tags: list[str] | None
    detail: dict | None

    def to_public_dict(self) -> dict:
        return {
            "loopId": self.loop_id,
            "fitnessLevel": self.level,
            "fitnessTags": self.tags or [],
            "fitnessDetail": self.detail or {},
        }

    @property
    def human_readable_tags(self) -> list[str]:
        """把内部英文标签翻译为简短中文原因（前端 Tooltip 用）."""
        if not self.tags:
            return []
        return [TAG_HUMAN_REASON.get(t, t) for t in self.tags]


# 标签→人类可读原因的顶层常量，供关注队列/其他模块复用
TAG_HUMAN_REASON: dict[str, str] = {
    "DATA_INSUFFICIENT": "数据严重不足",
    "MANUAL_DOMINANT": "手动模式占比过高",
    "LOW_AUTO_RATE": "自控率极低",
    "OP_SATURATED": "OP长期处于饱和限位附近",
    "SP_PV_DEVIATION": "SP-PV长期偏离设定",
    "NO_EXCITATION": "OP无有效激励",
    "WEAK_RESPONSE": "PV对OP响应极弱",
}


async def get_latest_fitness_per_loop(
    db,
    loop_ids: list[str],
) -> dict[str, LoopFitnessLatest]:
    """批量查询一组回路的最新适用性等级（从 kpi_snapshot_hourly 取 ts_start 最新一条）.

    Returns:
        {loop_id: LoopFitnessLatest}；缺失的回路在 values 中带 level=None 占位。
    """
    from sqlalchemy import select

    from app.models.metric import KpiSnapshotHourly

    result_map: dict[str, LoopFitnessLatest] = {}
    if not loop_ids:
        return result_map
    # 规范化 loop_ids 字符串列表
    ids: list[str] = []
    for lid in loop_ids:
        if lid is None:
            continue
        ids.append(str(lid))
    # 默认 None 占位
    for lid in ids:
        result_map[lid] = LoopFitnessLatest(loop_id=lid, level=None, tags=None, detail=None)

    try:
        # LATERAL: 每 loop_id 取最新 ts_start 一条（零额外查询，性能对齐 runs/latest 实现）
        stmt = (
            select(KpiSnapshotHourly.loop_id, KpiSnapshotHourly)
            .where(KpiSnapshotHourly.loop_id.in_(ids))
            .distinct(KpiSnapshotHourly.loop_id)
            .order_by(
                KpiSnapshotHourly.loop_id,
                KpiSnapshotHourly.ts_start.desc(),
            )
        )
        rows = (await db.execute(stmt)).all()
        for loop_id, snap in rows:
            lid = str(loop_id)
            tags_list: list[str] | None = None
            if isinstance(snap.fitness_tags, dict) and isinstance(
                snap.fitness_tags.get("tags"), list
            ):
                tags_list = [str(x) for x in snap.fitness_tags["tags"]]
            elif isinstance(snap.fitness_tags, list):
                tags_list = [str(x) for x in snap.fitness_tags]
            result_map[lid] = LoopFitnessLatest(
                loop_id=lid,
                level=snap.fitness_level,
                tags=tags_list,
                detail=snap.fitness_detail if isinstance(snap.fitness_detail, dict) else None,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("批量查询最新 fitness 失败（返回 None 占位）: %s", exc)
    return result_map
