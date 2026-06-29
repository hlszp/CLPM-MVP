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
from app.models.diagnosis import DiagnosisConfig, DiagnosisResult
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

    # 查询该回路的所有诊断结果（按置信度降序）
    diag_result = await db.execute(
        select(DiagnosisResult)
        .where(DiagnosisResult.loop_id == loop_id)
        .order_by(DiagnosisResult.diagnosed_at.desc())
    )
    diag_records = list(diag_result.scalars().all())

    if not diag_records:
        raise BizError(
            code="ERR_DIAG_RESULT_NOT_FOUND",
            message="该回路暂无诊断结果",
            status_code=404,
        )

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

    # 构建 diagnosisLabels 数组
    diagnosis_labels: list[dict] = []
    feature_values: dict[str, Any] = {}
    for record in diag_records:
        label = record.diag_label or "MANUAL_REVIEW"
        label_name = DIAG_LABEL_NAMES.get(label, label)
        confidence = _confidence_to_float(record.confidence)
        evidence = record.evidence_chain or {}
        # 提取特征值
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


__all__ = [
    "CLOSE_DURATION_BUCKETS",
    "DIAG_ALGORITHM_VERSION",
    "DIAG_LABEL_NAMES",
    "get_diagnosis_analytics",
    "get_diagnosis_detail",
    "list_diagnosis",
    "list_diagnosis_configs",
    "update_diagnosis_config",
]
