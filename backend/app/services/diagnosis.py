"""Diagnosis center service (IDS v3.2 §2.4 — S4-DIAG-001/003/006).

业务逻辑：
- 诊断指标配置 CRUD（含审计日志）
- 诊断列表（分页 + 筛选，含 action_tracker 状态）
- 诊断详情（含 8 类标签 + 证据链 + 特征值）
- 诊断统计报表（标签分布 / 效率趋势 / 闭环时长分布）
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisConfig, DiagnosisResult, DiagnosisTask
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.models.plant_node import PlantNode
from app.models.tracker import ActionTracker

logger = logging.getLogger(__name__)

# 算法版本号
DIAG_ALGORITHM_VERSION = "DIAG_ENGINE_v1.0"

# 8 类诊断标签中文名映射
DIAG_LABEL_NAMES: dict[str, str] = {
    "OSCILLATION": "振荡",
    "VALVE_STICTION": "阀门粘滞",
    "OVERAGGRESSIVE": "参数过激",
    "OVERCONSERVATIVE": "参数过保守",
    "EXTERNAL_DISTURBANCE": "外扰频繁",
    "QUALITY_ABNORMAL": "PV 质量异常",
    "OUTPUT_SATURATION": "输出饱和",
    "MANUAL_REVIEW": "人工复核",
}

# 闭环时长分档（小时）
CLOSE_DURATION_BUCKETS = [
    ("0-24h", 0, 24),
    ("24-72h", 24, 72),
    ("72h+", 72, None),
]


# ---------------------------------------------------------------------------
# 审计日志辅助
# ---------------------------------------------------------------------------


async def _write_audit(
    db: AsyncSession,
    operator: str,
    operation_type: str,
    target_type: str,
    target_id: str,
    before_value: str | None = None,
    after_value: str | None = None,
) -> None:
    """写入审计日志。"""
    log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        before_value=before_value,
        after_value=after_value,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)


# ---------------------------------------------------------------------------
# S4-DIAG-001: 诊断指标配置 CRUD
# ---------------------------------------------------------------------------


async def list_diagnosis_configs(db: AsyncSession) -> list[dict]:
    """获取诊断指标配置列表。"""
    result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.diag_code.asc()))
    configs = result.scalars().all()
    return [_config_to_dict(c) for c in configs]


async def update_diagnosis_config(
    db: AsyncSession,
    diag_id: str,
    operator: str,
    *,
    diag_name: str | None = None,
    algorithm_type: str | None = None,
    calc_method: str | None = None,
    params: dict | None = None,
    threshold: dict | None = None,
    is_enabled: bool | None = None,
) -> dict:
    """更新诊断指标配置。

    Raises:
        BizError: ERR_DIAG_CONFIG_NOT_FOUND
    """
    result = await db.execute(select(DiagnosisConfig).where(DiagnosisConfig.id == diag_id))
    config = result.scalar_one_or_none()
    if config is None:
        raise BizError(
            code="ERR_DIAG_CONFIG_NOT_FOUND",
            message="诊断指标配置不存在",
            status_code=404,
        )

    before = _config_to_dict(config)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if diag_name is not None:
        config.diag_name = diag_name
    if algorithm_type is not None:
        config.algorithm_type = algorithm_type
    if calc_method is not None:
        config.calc_method = calc_method
    if params is not None:
        config.params = params
    if threshold is not None:
        config.threshold = threshold
    if is_enabled is not None:
        config.is_enabled = is_enabled

    config.updated_by = operator
    config.updated_at = datetime.now(UTC).replace(tzinfo=None)
    config.version = (config.version or 1) + 1

    after = _config_to_dict(config)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DIAG_CONFIG_UPDATE",
        target_type="diagnosis_config",
        target_id=str(config.id),
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return after


# ---------------------------------------------------------------------------
# S4-DIAG-003: 诊断列表与详情
# ---------------------------------------------------------------------------


async def list_diagnosis(
    db: AsyncSession,
    *,
    plant_node_id: str | None = None,
    diagnosis_label: str | None = None,
    action_status: str | None = None,
    time_window: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """诊断列表（分页 + 筛选）。

    Returns:
        {items, total, page, pageSize}
    """
    # 构建基础查询：diagnosis_result JOIN loop_ledger LEFT JOIN action_tracker
    # 取每个 loop_id 最新的一条诊断结果
    conditions: list[Any] = []
    if plant_node_id:
        conditions.append(LoopLedger.unit_id == plant_node_id)
    if diagnosis_label:
        conditions.append(DiagnosisResult.diag_label == diagnosis_label)
    if action_status:
        conditions.append(ActionTracker.action_status == action_status)

    # 时间窗筛选
    time_cond = _build_time_window_condition(time_window)
    if time_cond is not None:
        conditions.append(time_cond)

    # 子查询：每个 loop_id 的最新 diagnosed_at
    latest_sub = (
        select(
            DiagnosisResult.loop_id,
            func.max(DiagnosisResult.diagnosed_at).label("max_diagnosed_at"),
        )
        .where(DiagnosisResult.loop_id.is_not(None))
        .group_by(DiagnosisResult.loop_id)
        .subquery()
    )

    # 主查询
    base_stmt = (
        select(DiagnosisResult, LoopLedger, ActionTracker)
        .join(
            latest_sub,
            and_(
                DiagnosisResult.loop_id == latest_sub.c.loop_id,
                DiagnosisResult.diagnosed_at == latest_sub.c.max_diagnosed_at,
            ),
        )
        .join(LoopLedger, DiagnosisResult.loop_id == LoopLedger.id, isouter=True)
        .outerjoin(ActionTracker, ActionTracker.loop_id == LoopLedger.id)
    )

    for cond in conditions:
        base_stmt = base_stmt.where(cond)

    # 计数
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # 分页
    stmt = (
        base_stmt.order_by(DiagnosisResult.diagnosed_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # 批量查 unit_name
    unit_ids = [str(row[1].unit_id) for row in rows if row[1] and row[1].unit_id]
    unit_map: dict[str, str] = {}
    if unit_ids:
        u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
        for node in u_result.scalars().all():
            unit_map[str(node.id)] = node.name

    # 取每个 loop 的最新综合评分（从 kpi_snapshot_hourly）
    loop_ids = [str(row[1].id) for row in rows if row[1]]
    score_map: dict[str, Decimal | None] = {}
    if loop_ids:
        # 取每个 loop 的最新快照评分
        score_sub = (
            select(
                KpiSnapshotHourly.loop_id,
                KpiSnapshotHourly.score,
                func.row_number()
                .over(
                    partition_by=KpiSnapshotHourly.loop_id,
                    order_by=KpiSnapshotHourly.ts_start.desc(),
                )
                .label("rn"),
            )
            .where(KpiSnapshotHourly.loop_id.in_(loop_ids))
            .subquery()
        )
        s_result = await db.execute(
            select(score_sub.c.loop_id, score_sub.c.score).where(score_sub.c.rn == 1)
        )
        for lid, score in s_result.all():
            score_map[str(lid)] = score

    items: list[dict] = []
    for diag_result, loop, tracker in rows:
        loop_id = str(diag_result.loop_id) if diag_result.loop_id else ""
        tag_name = loop.tag_name if loop else None
        unit_name = unit_map.get(str(loop.unit_id)) if loop and loop.unit_id else None
        diag_label = diag_result.diag_label or "MANUAL_REVIEW"
        label_name = DIAG_LABEL_NAMES.get(diag_label, diag_label)
        confidence = _confidence_to_float(diag_result.confidence)
        # fused_confidence 从 evidence_chain 中读取
        fused_confidence = None
        if diag_result.evidence_chain and isinstance(diag_result.evidence_chain, dict):
            fused_confidence = diag_result.evidence_chain.get("fused_confidence")
        composite_score = _to_float(score_map.get(loop_id))
        action_status_val = tracker.action_status if tracker else "PENDING"
        algorithm_version = diag_result.algorithm_version

        items.append(
            {
                "loopId": loop_id,
                "tagName": tag_name,
                "unitName": unit_name,
                "compositeScore": composite_score,
                "diagnosisLabel": diag_label,
                "labelName": label_name,
                "confidence": confidence,
                "fusedConfidence": fused_confidence,
                "algorithm": algorithm_version,
                "actionStatus": action_status_val,
                "diagnosedAt": diag_result.diagnosed_at.isoformat()
                if diag_result.diagnosed_at
                else None,
                "algorithmVersion": algorithm_version,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_diagnosis_detail(db: AsyncSession, loop_id: str) -> dict:
    """诊断详情（含 8 类标签数组 + 证据链 + 特征值）。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_DIAG_RESULT_NOT_FOUND
    """
    # 查询回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 查询该回路最新一次诊断任务的所有结果
    latest_diag = await db.execute(
        select(DiagnosisResult)
        .where(DiagnosisResult.loop_id == loop_id)
        .order_by(DiagnosisResult.diagnosed_at.desc())
        .limit(1)
    )
    latest_record = latest_diag.scalar_one_or_none()
    
    if not latest_record:
        raise BizError(
            code="ERR_DIAG_RESULT_NOT_FOUND",
            message="该回路暂无诊断结果",
            status_code=404,
        )
    
    # 获取最新诊断的 task_id（如果有），取该任务的所有诊断结果
    latest_task_id = latest_record.task_id
    if latest_task_id:
        diag_result = await db.execute(
            select(DiagnosisResult)
            .where(DiagnosisResult.loop_id == loop_id, DiagnosisResult.task_id == latest_task_id)
            .order_by(DiagnosisResult.diagnosed_at.desc())
        )
        diag_records = list(diag_result.scalars().all())
    else:
        diag_records = [latest_record]

    # 取最新综合评分
    score_result = await db.execute(
        select(KpiSnapshotHourly)
        .where(KpiSnapshotHourly.loop_id == loop_id)
        .order_by(KpiSnapshotHourly.ts_start.desc())
        .limit(1)
    )
    latest_snapshot = score_result.scalar_one_or_none()
    composite_score = _to_float(latest_snapshot.score) if latest_snapshot else None

    # 取最新一条诊断作为主诊断
    primary = diag_records[0]
    primary_evidence = primary.evidence_chain or {}
    fused_confidence = (
        primary_evidence.get("fused_confidence") if isinstance(primary_evidence, dict) else None
    )

    # 构建 diagnosisLabels 数组（只取最新一次诊断任务的标签）
    diagnosis_labels: list[dict] = []
    feature_values: dict[str, Any] = {}
    for record in diag_records:
        label = record.diag_label or "MANUAL_REVIEW"
        label_name = DIAG_LABEL_NAMES.get(label, label)
        confidence = _confidence_to_float(record.confidence)
        evidence = record.evidence_chain or {}
        if record.feature_values and isinstance(record.feature_values, dict):
            feature_values.update(record.feature_values)

        diagnosis_labels.append(
            {
                "label": label,
                "labelName": label_name,
                "confidence": confidence,
                "evidence": evidence,
                "algorithm": record.algorithm_version,
            }
        )

    # 构建证据链
    start_time, end_time = _build_waveform_time_window(primary.diagnosed_at)
    waveform_url = (
        f"/api/v1/timeseries/{loop_id}/waveform?startTime={start_time}&endTime={end_time}"
    )
    scatter_plot = (
        primary_evidence.get("scatter_plot") if isinstance(primary_evidence, dict) else None
    )
    reasoning = primary_evidence.get("reasoning") if isinstance(primary_evidence, dict) else None

    evidence_chain = {
        "waveformUrl": waveform_url,
        "scatterPlot": scatter_plot,
        "reasoning": reasoning,
    }

    return {
        "loopId": loop_id,
        "tagName": loop.tag_name,
        "compositeScore": composite_score,
        "diagnosisLabels": diagnosis_labels,
        "fusedConfidence": fused_confidence,
        "featureValues": feature_values,
        "evidenceChain": evidence_chain,
        "algorithmVersion": primary.algorithm_version,
        "diagnosedAt": primary.diagnosed_at.isoformat() if primary.diagnosed_at else None,
    }


async def get_diagnosis_visualization(db: AsyncSession, loop_id: str) -> dict:
    """诊断可视化数据（包含 8 类算法的完整可视化数组）。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_DIAG_RESULT_NOT_FOUND
    """
    detail = await get_diagnosis_detail(db=db, loop_id=loop_id)
    feature_values = detail.get("featureValues", {})

    visualization_data = {
        "loopId": detail["loopId"],
        "tagName": detail["tagName"],
        "compositeScore": detail.get("compositeScore"),
        "fusedConfidence": detail.get("fusedConfidence"),
        "diagnosedAt": detail.get("diagnosedAt"),
        "diagnosisLabels": detail.get("diagnosisLabels", []),
        "spectrum": {
            "frequencies": feature_values.get("fft_frequencies", []),
            "amplitudes": feature_values.get("fft_amplitudes", []),
            "peakFrequency": feature_values.get("oscillation_frequency", 0.0),
            "peakAmplitude": feature_values.get("oscillation_amplitude", 0.0),
            "oscillationIndex": feature_values.get("oscillation_index", 0.0),
        },
        "stepResponse": {
            "timestamps": feature_values.get("step_timestamps", []),
            "pvResponse": feature_values.get("step_pv_response", []),
            "spValues": feature_values.get("step_sp_values", []),
            "stepIndices": feature_values.get("step_indices", []),
            "overshoot": feature_values.get("overshoot", 0.0),
            "decayRatio": feature_values.get("decay_ratio", 0.0),
            "steadyStateError": feature_values.get("steady_state_error", 0.0),
        },
        "cusumAnalysis": {
            "timestamps": feature_values.get("cusum_timestamps", []),
            "cusumPos": feature_values.get("cusum_pos", []),
            "cusumNeg": feature_values.get("cusum_neg", []),
            "shiftPoints": feature_values.get("cusum_shift_points", []),
            "threshold": feature_values.get("cusum_threshold", 0.0),
            "shiftCount": feature_values.get("shift_count", 0),
            "maxCusum": feature_values.get("max_cusum", 0.0),
        },
        "scatterPlot": {
        "x": feature_values.get("scatter_plot_x", []),
        "y": feature_values.get("scatter_plot_y", []),
        "fittingScore": feature_values.get("fitting_score", 0.0),
        "stictionIndex": feature_values.get("stiction_index", 0.0),
    },
        "qualityTimeline": {
            "badRate": feature_values.get("bad_quality_rate", 0.0),
            "totalPoints": feature_values.get("total_points", 0),
            "badPoints": feature_values.get("bad_points", 0),
            "qualityPattern": feature_values.get("quality_pattern", "NORMAL"),
        },
        "saturationAnalysis": {
            "saturationRate": feature_values.get("saturation_rate", 0.0),
            "highSaturationCount": feature_values.get("high_saturation_count", 0),
            "lowSaturationCount": feature_values.get("low_saturation_count", 0),
        },
        "slowResponse": {
            "timeConstant": feature_values.get("time_constant", 0.0),
            "expectedTimeConstant": feature_values.get("expected_time_constant", 0.0),
            "ratio": feature_values.get("ratio", 0.0),
        },
        "choudhury": {
            "ngi": feature_values.get("ngi", 0.0),
            "nli": feature_values.get("nli", 0.0),
            "stictionIndex": feature_values.get("choudhury_stiction_index", 0.0),
        },
        "iaeAnalysis": {
            "similarity": feature_values.get("iae_similarity", 0.0),
            "zeroCrossingCount": feature_values.get("iae_zero_crossing_count", 0),
            "meanPeriod": feature_values.get("iae_mean_period", 0.0),
        },
        "kano": {
            "stictionRatio": feature_values.get("kano_stiction_ratio", 0.0),
            "correlation": feature_values.get("pv_op_correlation", 0.0),
            "stdRatio": feature_values.get("std_ratio", 0.0),
        },
    }

    evidence_chain = detail.get("evidenceChain", {})
    scatter_plot = evidence_chain.get("scatterPlot")
    if scatter_plot and isinstance(scatter_plot, dict):
        visualization_data["scatterPlot"]["x"] = scatter_plot.get("x", [])
        visualization_data["scatterPlot"]["y"] = scatter_plot.get("y", [])

    return visualization_data


# ---------------------------------------------------------------------------
# S4-DIAG-006: 诊断统计报表
# ---------------------------------------------------------------------------


async def get_diagnosis_analytics(
    db: AsyncSession,
    *,
    start_time: str,
    end_time: str,
    plant_node_id: str | None = None,
    diagnosis_label: str | None = None,
    action_status: str | None = None,
    granularity: str = "day",
) -> dict:
    """诊断统计报表数据。"""
    start_dt = _parse_iso_datetime(start_time)
    end_dt = _parse_iso_datetime(end_time)

    # 查询时间窗内的诊断结果
    diag_stmt = (
        select(DiagnosisResult, LoopLedger, ActionTracker)
        .join(LoopLedger, DiagnosisResult.loop_id == LoopLedger.id, isouter=True)
        .outerjoin(ActionTracker, ActionTracker.loop_id == LoopLedger.id)
        .where(DiagnosisResult.diagnosed_at >= start_dt)
        .where(DiagnosisResult.diagnosed_at <= end_dt)
    )
    if plant_node_id:
        diag_stmt = diag_stmt.where(LoopLedger.unit_id == plant_node_id)
    if diagnosis_label:
        diag_stmt = diag_stmt.where(DiagnosisResult.diag_label == diagnosis_label)
    if action_status:
        diag_stmt = diag_stmt.where(ActionTracker.action_status == action_status)

    diag_result = await db.execute(diag_stmt)
    diag_rows = diag_result.all()

    # 标签分布
    label_distribution = _aggregate_label_distribution(diag_rows)

    # 效率趋势（按粒度聚合）
    efficiency_trend = _aggregate_efficiency_trend(diag_rows, granularity, start_dt, end_dt)

    # 闭环时长分布
    close_duration_distribution = _aggregate_close_duration_distribution(diag_rows)

    return {
        "filterScope": {
            "startTime": start_time,
            "endTime": end_time,
            "plantNodeId": plant_node_id,
            "diagnosisLabel": diagnosis_label,
            "actionStatus": action_status,
            "granularity": granularity,
        },
        "labelDistribution": label_distribution,
        "efficiencyTrend": efficiency_trend,
        "closeDurationDistribution": close_duration_distribution,
    }


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _config_to_dict(c: DiagnosisConfig) -> dict:
    return {
        "diagId": str(c.id),
        "diagCode": c.diag_code,
        "diagName": c.diag_name,
        "algorithmType": c.algorithm_type,
        "calcMethod": c.calc_method,
        "params": c.params,
        "threshold": c.threshold,
        "isEnabled": bool(c.is_enabled) if c.is_enabled is not None else True,
        "updatedBy": c.updated_by,
        "updatedAt": c.updated_at.isoformat() if c.updated_at else None,
        "version": c.version or 1,
    }


def _confidence_to_float(value: Decimal | None) -> float:
    """置信度 Decimal (0-100) → float (0-1)。"""
    if value is None:
        return 0.0
    return float(value) / 100.0


def _to_float(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _parse_iso_datetime(s: str) -> datetime:
    """解析 ISO 8601 时间字符串，返回 naive datetime。

    diagnosis_result.diagnosed_at 列为 TIMESTAMP WITHOUT TIME ZONE，
    asyncpg 不允许 tz-aware datetime 传入 naive 列，因此统一剥离 tzinfo。
    """
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _build_time_window_condition(time_window: str | None):
    """根据 timeWindow 参数构建时间筛选条件。

    timeWindow 格式：last_24_hours / last_7_days / last_30_days
    """
    if not time_window:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    delta_map = {
        "last_24_hours": timedelta(hours=24),
        "last_7_days": timedelta(days=7),
        "last_30_days": timedelta(days=30),
    }
    delta = delta_map.get(time_window)
    if delta is None:
        return None
    start = now - delta
    return DiagnosisResult.diagnosed_at >= start


def _build_waveform_time_window(diagnosed_at: datetime) -> tuple[str, str]:
    """构建波形 URL 的时间窗（诊断前后各 12 小时）。"""
    if diagnosed_at.tzinfo is None:
        diagnosed_at = diagnosed_at.replace(tzinfo=UTC)
    start = diagnosed_at - timedelta(hours=12)
    end = diagnosed_at + timedelta(hours=12)
    return start.isoformat(), end.isoformat()


def _aggregate_label_distribution(rows: list) -> list[dict]:
    """聚合标签分布。"""
    counts: dict[str, int] = {}
    for diag_result, _loop, _tracker in rows:
        label = diag_result.diag_label or "MANUAL_REVIEW"
        counts[label] = counts.get(label, 0) + 1
    items = [
        {
            "label": label,
            "labelName": DIAG_LABEL_NAMES.get(label, label),
            "count": count,
        }
        for label, count in counts.items()
    ]
    items.sort(key=lambda x: -x["count"])
    return items


def _aggregate_efficiency_trend(
    rows: list,
    granularity: str,
    start: datetime,
    end: datetime,
) -> dict:
    """聚合效率趋势（按粒度分桶）。"""

    # 按粒度分桶
    def bucket_key(ts: datetime) -> str:
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        if granularity == "hour":
            return ts.strftime("%Y-%m-%dT%H:00:00")
        if granularity == "week":
            monday = ts - timedelta(days=ts.weekday())
            return monday.strftime("%Y-%m-%d")
        if granularity == "month":
            return ts.strftime("%Y-%m")
        return ts.strftime("%Y-%m-%d")

    # 收集每个桶的 resolved 数量和闭环时长
    buckets: dict[str, dict] = {}
    for diag_result, _loop, tracker in rows:
        ts = diag_result.diagnosed_at
        if ts is None:
            continue
        key = bucket_key(ts)
        bucket = buckets.setdefault(key, {"resolved": 0, "durations": []})
        if tracker and tracker.action_status == "IMPLEMENTED":
            bucket["resolved"] += 1
            # 计算闭环时长
            if tracker.updated_at and ts:
                duration = (tracker.updated_at - ts).total_seconds() / 3600.0
                if duration >= 0:
                    bucket["durations"].append(duration)

    timestamps = sorted(buckets.keys())
    resolved_count = [buckets[k]["resolved"] for k in timestamps]
    avg_durations = []
    for k in timestamps:
        durations = buckets[k]["durations"]
        if durations:
            avg_durations.append(round(sum(durations) / len(durations), 2))
        else:
            avg_durations.append(None)

    return {
        "timestamps": timestamps,
        "resolvedCount": resolved_count,
        "avgCloseDurationHours": avg_durations,
    }


def _aggregate_close_duration_distribution(rows: list) -> list[dict]:
    """聚合闭环时长分布。"""
    counts = {bucket[0]: 0 for bucket in CLOSE_DURATION_BUCKETS}
    for diag_result, _loop, tracker in rows:
        if not tracker or tracker.action_status != "IMPLEMENTED":
            continue
        if not tracker.updated_at or not diag_result.diagnosed_at:
            continue
        duration_hours = (tracker.updated_at - diag_result.diagnosed_at).total_seconds() / 3600.0
        if duration_hours < 0:
            continue
        for label, lo, hi in CLOSE_DURATION_BUCKETS:
            if hi is None:
                if duration_hours >= lo:
                    counts[label] += 1
                    break
            elif lo <= duration_hours < hi:
                counts[label] += 1
                break
    return [{"range": label, "count": counts[label]} for label, _, _ in CLOSE_DURATION_BUCKETS]


# ---------------------------------------------------------------------------
# 诊断任务管理 (PRD §5.6 诊断中心 — 诊断任务子模块)
# ---------------------------------------------------------------------------


# 可取消的任务状态
_CANCELLABLE_STATUSES = ("PENDING", "RUNNING")


async def trigger_diagnosis(
    db: AsyncSession,
    *,
    loop_ids: list[str],
    start_time: str | None = None,
    end_time: str | None = None,
    operator: str = "system",
) -> dict:
    """触发诊断任务（手动，支持批量）。

    为每个回路创建一条 DiagnosisTask 记录（trigger_type='manual'），
    并通过 Celery 异步执行诊断。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表（至少 1 个）
        start_time: 时间窗起始（ISO 8601，可选，默认最近 1 小时）
        end_time: 时间窗结束（ISO 8601，可选，默认当前时间）
        operator: 触发人用户名

    Returns:
        {tasks: [{taskId, loopId, status}, ...]}

    Raises:
        BizError: ERR_VALIDATION — loop_ids 为空
    """
    if not loop_ids:
        raise BizError(
            code="ERR_VALIDATION",
            message="回路 ID 列表不能为空",
            status_code=422,
        )

    # 延迟导入避免 Celery worker 循环依赖
    from app.tasks.diagnosis_engine import run_loop_diagnosis

    # 解析时间范围（naive datetime 入库）
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    if start_time:
        ts_start_dt = _parse_iso_datetime(start_time)
    else:
        ts_start_dt = now_naive - timedelta(hours=1)
    if end_time:
        ts_end_dt = _parse_iso_datetime(end_time)
    else:
        ts_end_dt = now_naive

    tasks_list: list[dict] = []
    for lid in loop_ids:
        task_id = str(uuid4())
        task = DiagnosisTask(
            id=task_id,
            loop_id=lid,
            trigger_type="manual",
            triggered_by=operator,
            status="PENDING",
            time_range_start=ts_start_dt,
            time_range_end=ts_end_dt,
        )
        db.add(task)
        tasks_list.append({"taskId": task_id, "loopId": lid, "status": "PENDING"})

    await db.commit()

    # 提交 Celery 异步任务（每回路一个）
    for item in tasks_list:
        run_loop_diagnosis.delay(
            item["loopId"],
            task_id=item["taskId"],
            time_range_start=ts_start_dt.isoformat(),
            time_range_end=ts_end_dt.isoformat(),
        )

    logger.info(
        "诊断任务已触发: %d 个回路, operator=%s, range=%s~%s",
        len(loop_ids),
        operator,
        ts_start_dt.isoformat(),
        ts_end_dt.isoformat(),
    )

    return {"tasks": tasks_list}


async def list_diagnosis_tasks(
    db: AsyncSession,
    *,
    status: str | None = None,
    trigger_type: str | None = None,
    loop_id: str | None = None,
    plant_node_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """诊断任务列表（仅未归档，分页 + 筛选）。

    Returns:
        {items, total, page, pageSize}
    """
    conditions: list[Any] = [DiagnosisTask.is_archived.is_(False)]
    if status:
        conditions.append(DiagnosisTask.status == status)
    if trigger_type:
        conditions.append(DiagnosisTask.trigger_type == trigger_type)
    if loop_id:
        conditions.append(DiagnosisTask.loop_id == loop_id)

    base_stmt = select(DiagnosisTask)
    if plant_node_id:
        base_stmt = base_stmt.join(
            LoopLedger, DiagnosisTask.loop_id == LoopLedger.id
        ).where(LoopLedger.unit_id == plant_node_id)
    for cond in conditions:
        base_stmt = base_stmt.where(cond)

    # 计数
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页查询（按触发时间倒序，同时间按 ID 倒序）
    stmt = (
        base_stmt.order_by(
            DiagnosisTask.triggered_at.desc(),
            DiagnosisTask.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    tasks = list(result.scalars().all())

    # 批量查询回路信息和最新评分（含关键 KPI 指标）
    loop_ids_list = [str(t.loop_id) for t in tasks if t.loop_id]
    loop_map: dict[str, LoopLedger] = {}
    unit_map: dict[str, str] = {}
    score_map: dict[str, dict] = {}
    if loop_ids_list:
        # 查询回路
        l_result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids_list)))
        for loop in l_result.scalars().all():
            loop_map[str(loop.id)] = loop
        # 查询装置名称
        unit_ids = [str(l.unit_id) for l in loop_map.values() if l.unit_id]
        if unit_ids:
            u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
            for node in u_result.scalars().all():
                unit_map[str(node.id)] = node.name
        # 查询最新评分 + 关键 KPI 指标
        score_sub = (
            select(
                KpiSnapshotHourly.loop_id,
                KpiSnapshotHourly.score,
                KpiSnapshotHourly.accuracy_rate,
                KpiSnapshotHourly.fast_rate,
                KpiSnapshotHourly.steady_rate,
                KpiSnapshotHourly.effective_auto_rate,
                func.row_number()
                .over(
                    partition_by=KpiSnapshotHourly.loop_id,
                    order_by=KpiSnapshotHourly.ts_start.desc(),
                )
                .label("rn"),
            )
            .where(KpiSnapshotHourly.loop_id.in_(loop_ids_list))
            .subquery()
        )
        s_result = await db.execute(
            select(
                score_sub.c.loop_id,
                score_sub.c.score,
                score_sub.c.accuracy_rate,
                score_sub.c.fast_rate,
                score_sub.c.steady_rate,
                score_sub.c.effective_auto_rate,
            ).where(score_sub.c.rn == 1)
        )
        for lid, score, acc_rate, fast_rate, steady_rate, auto_rate in s_result.all():
            score_map[str(lid)] = {
                "score": score,
                "accuracy_rate": acc_rate,
                "fast_rate": fast_rate,
                "steady_rate": steady_rate,
                "effective_auto_rate": auto_rate,
            }

    # 批量查询诊断结果标签
    task_ids_list = [str(t.id) for t in tasks]
    labels_map: dict[str, list[str]] = {}
    if task_ids_list:
        r_result = await db.execute(
            select(DiagnosisResult.task_id, DiagnosisResult.diag_label)
            .where(DiagnosisResult.task_id.in_(task_ids_list))
            .distinct()
        )
        for tid, label in r_result.all():
            tid_str = str(tid) if tid else ""
            if tid_str not in labels_map:
                labels_map[tid_str] = []
            if label and label not in labels_map[tid_str]:
                labels_map[tid_str].append(label)

    items: list[dict] = []
    for task in tasks:
        item = _task_to_dict(task, loop_map, unit_map, score_map)
        item["diagLabels"] = labels_map.get(str(task.id), [])
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_diagnosis_task_detail(db: AsyncSession, task_id: str) -> dict:
    """获取诊断任务详情（含关联的诊断结果）。

    Raises:
        BizError: ERR_DIAG_TASK_NOT_FOUND
    """
    result = await db.execute(select(DiagnosisTask).where(DiagnosisTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_FOUND",
            message="诊断任务不存在",
            status_code=404,
        )

    # 查询回路信息
    loop_map: dict[str, LoopLedger] = {}
    unit_map: dict[str, str] = {}
    if task.loop_id:
        l_result = await db.execute(
            select(LoopLedger).where(LoopLedger.id == str(task.loop_id))
        )
        loop = l_result.scalar_one_or_none()
        if loop:
            loop_map[str(loop.id)] = loop
            if loop.unit_id:
                u_result = await db.execute(
                    select(PlantNode).where(PlantNode.id == str(loop.unit_id))
                )
                node = u_result.scalar_one_or_none()
                if node:
                    unit_map[str(node.id)] = node.name

    # 查询关联的诊断结果
    diag_result = await db.execute(
        select(DiagnosisResult)
        .where(DiagnosisResult.task_id == task_id)
        .order_by(DiagnosisResult.diagnosed_at.desc())
    )
    diag_records = list(diag_result.scalars().all())

    results_list: list[dict] = []
    for record in diag_records:
        label = record.diag_label or "MANUAL_REVIEW"
        results_list.append(
            {
                "id": str(record.id),
                "label": label,
                "labelName": DIAG_LABEL_NAMES.get(label, label),
                "confidence": _confidence_to_float(record.confidence),
                "featureValues": record.feature_values or {},
                "evidenceChain": record.evidence_chain or {},
                "algorithmVersion": record.algorithm_version,
                "diagnosedAt": record.diagnosed_at.isoformat()
                if record.diagnosed_at
                else None,
            }
        )

    # 构建详情字典
    score_map: dict[str, dict] = {}
    if task.loop_id:
        snap_result = await db.execute(
            select(KpiSnapshotHourly)
            .where(KpiSnapshotHourly.loop_id == str(task.loop_id))
            .order_by(KpiSnapshotHourly.ts_start.desc())
            .limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        if snap:
            score_map[str(task.loop_id)] = {
                "score": snap.score,
                "accuracy_rate": snap.accuracy_rate,
                "fast_rate": snap.fast_rate,
                "steady_rate": snap.steady_rate,
                "effective_auto_rate": snap.effective_auto_rate,
            }

    base = _task_to_dict(task, loop_map, unit_map, score_map)
    base.update(
        {
            "errorMessage": task.error_message,
            "results": results_list,
        }
    )
    return base


async def run_diagnosis_task(
    db: AsyncSession,
    task_id: str,
) -> dict:
    """对已有诊断任务执行诊断（不创建新任务）。

    读取该任务的 loop_id 和时间范围，重置状态为 PENDING 后提交 Celery 异步执行。
    适用于行级"诊断"按钮：对当前任务重新执行诊断。

    Raises:
        BizError: ERR_DIAG_TASK_NOT_FOUND
    """
    result = await db.execute(select(DiagnosisTask).where(DiagnosisTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_FOUND",
            message="诊断任务不存在",
            status_code=404,
        )

    # 重置任务状态为 PENDING，清除之前的错误信息和完成时间
    task.status = "PENDING"
    task.error_message = None
    task.completed_at = None
    await db.commit()

    # 提交 Celery 异步任务（复用已有 task_id 和时间范围）
    from app.tasks.diagnosis_engine import run_loop_diagnosis

    ts_start = task.time_range_start.isoformat() if task.time_range_start else None
    ts_end = task.time_range_end.isoformat() if task.time_range_end else None
    run_loop_diagnosis.delay(
        str(task.loop_id),
        task_id=str(task.id),
        time_range_start=ts_start,
        time_range_end=ts_end,
    )

    logger.info(
        "诊断任务 %s 已重新执行, loop_id=%s, range=%s~%s",
        task_id,
        task.loop_id,
        ts_start,
        ts_end,
    )

    return {"taskId": task_id, "status": "PENDING"}


async def archive_diagnosis_task(
    db: AsyncSession,
    task_id: str,
    operator: str = "system",
) -> dict:
    """归档诊断任务（仅 SUCCESS/FAILED/CANCELLED 可归档）。

    Raises:
        BizError: ERR_DIAG_TASK_NOT_FOUND / ERR_DIAG_TASK_NOT_ARCHIVABLE
    """
    result = await db.execute(select(DiagnosisTask).where(DiagnosisTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_FOUND",
            message="诊断任务不存在",
            status_code=404,
        )

    if task.is_archived:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_ARCHIVABLE",
            message="任务已归档，无需重复操作",
            status_code=400,
        )

    if task.status in _CANCELLABLE_STATUSES:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_ARCHIVABLE",
            message=f"任务状态为 {task.status}，仅终态（SUCCESS/FAILED/CANCELLED）可归档",
            status_code=400,
        )

    before_snapshot = json.dumps(
        {"id": str(task.id), "isArchived": task.is_archived}, ensure_ascii=False
    )

    task.is_archived = True
    task.archived_at = datetime.now(UTC).replace(tzinfo=None)
    task.archived_by = operator

    after_snapshot = json.dumps(
        {
            "id": str(task.id),
            "isArchived": task.is_archived,
            "archivedAt": task.archived_at.isoformat() if task.archived_at else None,
            "archivedBy": task.archived_by,
        },
        ensure_ascii=False,
        default=str,
    )

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DIAG_TASK_ARCHIVE",
        target_type="diagnosis_task",
        target_id=str(task.id),
        before_value=before_snapshot,
        after_value=after_snapshot,
    )
    await db.commit()

    logger.info("诊断任务 %s 已归档, operator=%s", task_id, operator)
    return {"taskId": task_id, "isArchived": True}


async def cancel_diagnosis_task(
    db: AsyncSession,
    task_id: str,
    operator: str = "system",
) -> dict:
    """取消诊断任务（仅 PENDING/RUNNING 可取消）。

    Raises:
        BizError: ERR_DIAG_TASK_NOT_FOUND / ERR_DIAG_TASK_NOT_CANCELLABLE
    """
    result = await db.execute(select(DiagnosisTask).where(DiagnosisTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_FOUND",
            message="诊断任务不存在",
            status_code=404,
        )

    if task.status not in _CANCELLABLE_STATUSES:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_CANCELLABLE",
            message=f"任务状态为 {task.status}，仅 PENDING/RUNNING 可取消",
            status_code=400,
        )

    before_snapshot = json.dumps(
        {"id": str(task.id), "status": task.status}, ensure_ascii=False
    )

    task.status = "CANCELLED"
    task.completed_at = datetime.now(UTC).replace(tzinfo=None)

    after_snapshot = json.dumps(
        {
            "id": str(task.id),
            "status": task.status,
            "completedAt": task.completed_at.isoformat() if task.completed_at else None,
        },
        ensure_ascii=False,
        default=str,
    )

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DIAG_TASK_CANCEL",
        target_type="diagnosis_task",
        target_id=str(task.id),
        before_value=before_snapshot,
        after_value=after_snapshot,
    )
    await db.commit()

    logger.info("诊断任务 %s 已取消, operator=%s", task_id, operator)
    return {"taskId": task_id, "status": "CANCELLED"}


async def delete_diagnosis_task(
    db: AsyncSession,
    task_id: str,
    operator: str = "system",
) -> dict:
    """物理删除诊断任务（测试期间放开所有限制，任意状态均可删除）。

    Raises:
        BizError: ERR_DIAG_TASK_NOT_FOUND
    """
    result = await db.execute(select(DiagnosisTask).where(DiagnosisTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise BizError(
            code="ERR_DIAG_TASK_NOT_FOUND",
            message="诊断任务不存在",
            status_code=404,
        )

    before_snapshot = json.dumps(
        {"id": str(task.id), "status": task.status, "loopId": str(task.loop_id)},
        ensure_ascii=False,
    )

    await db.delete(task)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="DIAG_TASK_DELETE",
        target_type="diagnosis_task",
        target_id=str(task.id),
        before_value=before_snapshot,
        after_value=None,
    )
    await db.commit()

    logger.info("诊断任务 %s 已删除, operator=%s", task_id, operator)
    return {"taskId": task_id, "deleted": True}


async def list_diagnosis_records(
    db: AsyncSession,
    *,
    status: str | None = None,
    trigger_type: str | None = None,
    loop_id: str | None = None,
    plant_node_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """诊断记录列表（仅已归档，分页 + 筛选）。

    Returns:
        {items, total, page, pageSize}
    """
    conditions: list[Any] = [DiagnosisTask.is_archived.is_(True)]
    if status:
        conditions.append(DiagnosisTask.status == status)
    if trigger_type:
        conditions.append(DiagnosisTask.trigger_type == trigger_type)
    if loop_id:
        conditions.append(DiagnosisTask.loop_id == loop_id)

    base_stmt = select(DiagnosisTask)
    if plant_node_id:
        base_stmt = base_stmt.join(
            LoopLedger, DiagnosisTask.loop_id == LoopLedger.id
        ).where(LoopLedger.unit_id == plant_node_id)
    for cond in conditions:
        base_stmt = base_stmt.where(cond)

    # 计数
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页查询（按归档时间倒序）
    stmt = (
        base_stmt.order_by(DiagnosisTask.archived_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    tasks = list(result.scalars().all())

    # 批量查询回路信息和最新评分（含关键 KPI 指标）
    loop_ids_list = [str(t.loop_id) for t in tasks if t.loop_id]
    loop_map: dict[str, LoopLedger] = {}
    unit_map: dict[str, str] = {}
    score_map: dict[str, dict] = {}
    if loop_ids_list:
        l_result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids_list)))
        for loop in l_result.scalars().all():
            loop_map[str(loop.id)] = loop
        unit_ids = [str(l.unit_id) for l in loop_map.values() if l.unit_id]
        if unit_ids:
            u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
            for node in u_result.scalars().all():
                unit_map[str(node.id)] = node.name
        score_sub = (
            select(
                KpiSnapshotHourly.loop_id,
                KpiSnapshotHourly.score,
                KpiSnapshotHourly.accuracy_rate,
                KpiSnapshotHourly.fast_rate,
                KpiSnapshotHourly.steady_rate,
                KpiSnapshotHourly.effective_auto_rate,
                func.row_number()
                .over(
                    partition_by=KpiSnapshotHourly.loop_id,
                    order_by=KpiSnapshotHourly.ts_start.desc(),
                )
                .label("rn"),
            )
            .where(KpiSnapshotHourly.loop_id.in_(loop_ids_list))
            .subquery()
        )
        s_result = await db.execute(
            select(
                score_sub.c.loop_id,
                score_sub.c.score,
                score_sub.c.accuracy_rate,
                score_sub.c.fast_rate,
                score_sub.c.steady_rate,
                score_sub.c.effective_auto_rate,
            ).where(score_sub.c.rn == 1)
        )
        for lid, score, acc_rate, fast_rate, steady_rate, auto_rate in s_result.all():
            score_map[str(lid)] = {
                "score": score,
                "accuracy_rate": acc_rate,
                "fast_rate": fast_rate,
                "steady_rate": steady_rate,
                "effective_auto_rate": auto_rate,
            }

    # 批量查询诊断结果标签
    task_ids_list = [str(t.id) for t in tasks]
    labels_map: dict[str, list[str]] = {}
    if task_ids_list:
        r_result = await db.execute(
            select(DiagnosisResult.task_id, DiagnosisResult.diag_label)
            .where(DiagnosisResult.task_id.in_(task_ids_list))
            .distinct()
        )
        for tid, label in r_result.all():
            tid_str = str(tid) if tid else ""
            if tid_str not in labels_map:
                labels_map[tid_str] = []
            if label and label not in labels_map[tid_str]:
                labels_map[tid_str].append(label)

    items: list[dict] = []
    for task in tasks:
        item = _task_to_dict(task, loop_map, unit_map, score_map)
        item["diagLabels"] = labels_map.get(str(task.id), [])
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


def _task_to_dict(
    task: DiagnosisTask,
    loop_map: dict[str, LoopLedger] | None = None,
    unit_map: dict[str, str] | None = None,
    score_map: dict[str, dict] | None = None,
) -> dict:
    """将 DiagnosisTask ORM 模型转换为响应字典。

    Args:
        task: 诊断任务 ORM 对象
        loop_map: 回路 ID → LoopLedger 映射（可选，用于补充回路信息）
        unit_map: 装置 ID → 名称映射（可选，用于补充装置名称）
        score_map: 回路 ID → 最新 KPI 指标字典映射（可选，含 score/accuracy_rate/fast_rate/steady_rate/effective_auto_rate）
    """
    loop_map = loop_map or {}
    unit_map = unit_map or {}
    score_map = score_map or {}

    loop_id = str(task.loop_id) if task.loop_id else ""
    loop = loop_map.get(loop_id)
    tag_name = loop.tag_name if loop else None
    loop_name = loop.description if loop else None
    unit_name = unit_map.get(str(loop.unit_id)) if loop and loop.unit_id else None
    kpi = score_map.get(loop_id, {})
    composite_score = _to_float(kpi.get("score"))
    accuracy_score = _to_float(kpi.get("accuracy_rate"))
    fast_score = _to_float(kpi.get("fast_rate"))
    steady_score = _to_float(kpi.get("steady_rate"))
    effective_auto_rate = _to_float(kpi.get("effective_auto_rate"))

    return {
        "taskId": str(task.id),
        "loopId": loop_id,
        "tagName": tag_name,
        "loopName": loop_name,
        "unitName": unit_name,
        "compositeScore": composite_score,
        "accuracyScore": accuracy_score,
        "fastScore": fast_score,
        "steadyScore": steady_score,
        "effectiveAutoRate": effective_auto_rate,
        "status": task.status,
        "triggerType": task.trigger_type,
        "triggeredBy": task.triggered_by,
        "triggeredAt": task.triggered_at.isoformat() if task.triggered_at else None,
        "completedAt": task.completed_at.isoformat() if task.completed_at else None,
        "timeRangeStart": task.time_range_start.isoformat()
        if task.time_range_start
        else None,
        "timeRangeEnd": task.time_range_end.isoformat() if task.time_range_end else None,
        "isArchived": bool(task.is_archived),
        "errorMessage": task.error_message,
    }


__all__ = [
    "CLOSE_DURATION_BUCKETS",
    "DIAG_ALGORITHM_VERSION",
    "DIAG_LABEL_NAMES",
    "archive_diagnosis_task",
    "cancel_diagnosis_task",
    "get_diagnosis_analytics",
    "get_diagnosis_detail",
    "get_diagnosis_task_detail",
    "list_diagnosis",
    "list_diagnosis_configs",
    "list_diagnosis_records",
    "list_diagnosis_tasks",
    "run_diagnosis_task",
    "trigger_diagnosis",
    "update_diagnosis_config",
]
