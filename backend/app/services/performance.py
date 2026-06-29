"""Performance evaluation service (IDS v3.2 §2.3 — S3-METRIC-001~006).

业务逻辑：
- 指标配置 CRUD（含权重总和校验、Redis 缓存失效、审计日志）
- 引擎规则 CRUD（含审计日志）
- 全局看板聚合（Redis 缓存 5 分钟）
- 低效回路排行
- 统计报表 + CSV 导出
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.exceptions import BizError
from app.core.redis import redis_client
from app.models.audit import SysAuditLog
from app.models.engine import EngineRule
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly, MetricConfig
from app.models.node_kpi import KpiNodeSnapshotHourly
from app.models.plant_node import PlantNode
from app.models.tracker import ActionTracker
from app.schemas.performance import WeightSumValidator

logger = logging.getLogger(__name__)

# 算法版本号
ALGORITHM_VERSION = "KPI_CALC_v1.0"

# Redis 缓存键
METRIC_CONFIG_CACHE_KEY = "clpm:metric_config"
DASHBOARD_CACHE_KEY_TEMPLATE = "clpm:dashboard:{plant_node_id}:{time_window}"
DASHBOARD_CACHE_TTL = 300  # 5 分钟

# 7 大 KPI metric_code 列表（固定顺序，对齐 GB/T 44693.2-2024）
KPI_METRIC_CODES = (
    "good_value_rate",
    "auto_mode_rate",
    "effective_auto_rate",
    "steady_rate",
    "accuracy_rate",
    "fast_response_rate",
    "oscillation_rate",
    "saturation_rate",
)

# KPI 中文名映射
KPI_NAME_MAP = {
    "good_value_rate": "好值率",
    "auto_mode_rate": "自控率",
    "effective_auto_rate": "有效自控率",
    "steady_rate": "平稳率",
    "accuracy_rate": "准确率",
    "fast_response_rate": "快速率",
    "oscillation_rate": "振荡率",
    "saturation_rate": "饱和率",
    "composite_score": "综合评分",
    "auto_loop_ratio": "投自动回路占比",
}

# 时间窗映射（基于"今天"为基准）
TIME_WINDOWS: dict[str, timedelta] = {
    "today": timedelta(days=1),
    "yesterday": timedelta(days=1),
    "last_7_days": timedelta(days=7),
    "last_30_days": timedelta(days=30),
}


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


async def _invalidate_metric_config_cache() -> None:
    """失效指标配置 Redis 缓存。"""
    try:
        await redis_client.delete(METRIC_CONFIG_CACHE_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("失效指标配置缓存失败: %s", exc)


async def _invalidate_dashboard_cache(plant_node_id: str | None, time_window: str) -> None:
    """失效看板 Redis 缓存。"""
    key = DASHBOARD_CACHE_KEY_TEMPLATE.format(
        plant_node_id=plant_node_id or "all",
        time_window=time_window,
    )
    try:
        await redis_client.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("失效看板缓存失败: %s", exc)


# ---------------------------------------------------------------------------
# S3-METRIC-001: 指标配置 CRUD
# ---------------------------------------------------------------------------


async def list_metric_configs(db: AsyncSession) -> list[dict]:
    """获取 6 大 KPI 指标配置列表。"""
    result = await db.execute(select(MetricConfig).order_by(MetricConfig.metric_code.asc()))
    configs = result.scalars().all()
    return [_metric_config_to_dict(c) for c in configs]


async def update_metric_config(
    db: AsyncSession,
    metric_id: str,
    operator: str,
    *,
    metric_name: str | None = None,
    formula: str | None = None,
    weight: Decimal | None = None,
    threshold: dict | None = None,
    control_type: str | None = None,
    is_enabled: bool | None = None,
) -> dict:
    """更新指标配置。

    校验：
    - 指标必须存在（ERR_METRIC_NOT_FOUND）
    - weight 变更后，6 大 KPI 启用指标权重总和必须为 100（ERR_METRIC_WEIGHT_SUM）

    Raises:
        BizError: ERR_METRIC_NOT_FOUND / ERR_METRIC_WEIGHT_SUM
    """
    result = await db.execute(select(MetricConfig).where(MetricConfig.id == metric_id))
    config = result.scalar_one_or_none()
    if config is None:
        raise BizError(
            code="ERR_METRIC_NOT_FOUND",
            message="指标配置不存在",
            status_code=404,
        )

    before = _metric_config_to_dict(config)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if metric_name is not None:
        config.metric_name = metric_name
    if formula is not None:
        config.formula = formula
    if weight is not None:
        config.weight = weight
    if threshold is not None:
        config.threshold = threshold
    if control_type is not None:
        config.control_type = control_type
    if is_enabled is not None:
        config.is_enabled = is_enabled

    config.updated_by = operator
    config.updated_at = datetime.now(UTC).replace(tzinfo=None)
    config.version = (config.version or 1) + 1

    # 权重总和校验：仅校验启用状态的 6 大 KPI
    all_result = await db.execute(select(MetricConfig))
    all_configs = all_result.scalars().all()
    enabled_weights = [
        c.weight
        for c in all_configs
        if c.is_enabled and c.weight is not None and c.metric_code in KPI_METRIC_CODES
    ]
    if enabled_weights:
        WeightSumValidator.validate(enabled_weights)

    after = _metric_config_to_dict(config)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="METRIC_CONFIG_UPDATE",
        target_type="metric_config",
        target_id=str(config.id),
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    # 失效缓存
    await _invalidate_metric_config_cache()

    return after


# ---------------------------------------------------------------------------
# S3-METRIC-002: 引擎规则 CRUD
# ---------------------------------------------------------------------------


async def list_engine_rules(db: AsyncSession) -> list[dict]:
    """获取引擎规则列表。"""
    result = await db.execute(select(EngineRule).order_by(EngineRule.rule_code.asc()))
    rules = result.scalars().all()
    return [_engine_rule_to_dict(r) for r in rules]


async def update_engine_rule(
    db: AsyncSession,
    rule_id: str,
    operator: str,
    *,
    rule_name: str | None = None,
    params: dict | None = None,
    is_enabled: bool | None = None,
) -> dict:
    """更新引擎规则。

    Raises:
        BizError: ERR_RULE_NOT_FOUND
    """
    result = await db.execute(select(EngineRule).where(EngineRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise BizError(
            code="ERR_RULE_NOT_FOUND",
            message="引擎规则不存在",
            status_code=404,
        )

    before = _engine_rule_to_dict(rule)
    before_json = json.dumps(before, ensure_ascii=False, default=str)

    if rule_name is not None:
        rule.rule_name = rule_name
    if params is not None:
        rule.params = params
    if is_enabled is not None:
        rule.is_enabled = is_enabled

    rule.updated_by = operator
    rule.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after = _engine_rule_to_dict(rule)
    after_json = json.dumps(after, ensure_ascii=False, default=str)

    await _write_audit(
        db=db,
        operator=operator,
        operation_type="ENGINE_RULE_UPDATE",
        target_type="engine_rule",
        target_id=str(rule.id),
        before_value=before_json,
        after_value=after_json,
    )
    await db.commit()

    return after


# ---------------------------------------------------------------------------
# S3-METRIC-004: 全局看板
# ---------------------------------------------------------------------------


async def get_board(
    db: AsyncSession,
    plant_node_id: str | None = None,
    time_window: str = "today",
) -> dict:
    """全局看板数据。

    Redis 缓存 5 分钟。
    """
    cache_key = DASHBOARD_CACHE_KEY_TEMPLATE.format(
        plant_node_id=plant_node_id or "all",
        time_window=time_window,
    )
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取看板缓存失败: %s", exc)

    # 计算 time_window 对应的时间范围
    now = datetime.now(UTC).replace(tzinfo=None)
    if time_window == "today":
        start = now - timedelta(hours=24)
    elif time_window == "yesterday":
        start = now - timedelta(days=2)
        now = now - timedelta(days=1)
    else:
        delta = TIME_WINDOWS.get(time_window, timedelta(days=1))
        start = now - delta

    # 获取装置名称
    plant_node_name = "全厂"
    if plant_node_id:
        node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
        node = node_result.scalar_one_or_none()
        if node:
            plant_node_name = node.name

    # --- 统一数据通路：从节点级快照表读取（与 /nodes/* API 一致） ---
    kpi_cards, kpi_summary = await _aggregate_node_board(db, plant_node_id, start, now)

    # 平稳率趋势（从节点级快照按小时聚合）
    steady_trend = await _aggregate_node_steady_trend(db, plant_node_id, start, now)

    # 部分数据警告（从节点级快照状态计数）
    node_count_stmt = select(KpiNodeSnapshotHourly.status, func.count().label("cnt"))
    if plant_node_id:
        node_count_stmt = node_count_stmt.where(
            KpiNodeSnapshotHourly.plant_node_id == plant_node_id
        )
    else:
        # 全厂：仅统计 is_kpi_enabled 的节点
        enabled_ids = select(PlantNode.id).where(PlantNode.is_kpi_enabled.is_(True))
        node_count_stmt = node_count_stmt.where(
            KpiNodeSnapshotHourly.plant_node_id.in_(enabled_ids)
        )
    node_count_stmt = node_count_stmt.where(
        KpiNodeSnapshotHourly.ts_start >= start,
        KpiNodeSnapshotHourly.ts_start <= now,
    ).group_by(KpiNodeSnapshotHourly.status)
    count_result = await db.execute(node_count_stmt)
    status_counts = {r.status: r.cnt for r in count_result.all()}
    inconclusive_count = status_counts.get("INCONCLUSIVE", 0)
    partial_warning = {
        "active": inconclusive_count > 0,
        "inconclusiveCount": inconclusive_count,
        "partialCount": 0,
        "message": (f"存在 {inconclusive_count} 个不确定结果" if inconclusive_count > 0 else None),
    }

    data = {
        "filterScope": {
            "plantNodeId": plant_node_id,
            "plantNodeName": plant_node_name,
            "timeWindow": time_window,
        },
        "kpiCards": kpi_cards,
        "kpiSummary": kpi_summary,
        "steadyRateTrend": steady_trend,
        "partialWarning": partial_warning,
    }

    # 写入缓存
    try:
        await redis_client.setex(cache_key, DASHBOARD_CACHE_TTL, json.dumps(data, default=str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入看板缓存失败: %s", exc)

    return data


# ---------------------------------------------------------------------------
# 看板辅助函数（统一从节点级快照表读取）
# ---------------------------------------------------------------------------


def _node_kpi_fields() -> tuple[str, ...]:
    """节点级快照中的 KPI 字段列表。"""
    return (
        "good_value_rate",
        "auto_mode_rate",
        "effective_auto_rate",
        "steady_rate",
        "accuracy_rate",
        "fast_response_rate",
        "oscillation_rate",
        "saturation_rate",
    )


async def _aggregate_node_board(
    db: AsyncSession,
    plant_node_id: str | None,
    start: datetime,
    end: datetime,
) -> tuple[list[dict], dict]:
    """从节点级快照表聚合看板 KPI 卡片和汇总。

    统一数据通路：与 /performance/nodes/* API 读取同一张 kpi_node_snapshot_hourly 表。

    - 指定 plant_node_id：取该节点最新快照
    - 未指定：取所有 is_kpi_enabled 节点的最新快照，按 loop_count 加权平均
    """
    # 构建基础查询：取每个节点在时间窗内最新一条快照
    base = (
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
        )
        .distinct(KpiNodeSnapshotHourly.plant_node_id)
        .order_by(
            KpiNodeSnapshotHourly.plant_node_id,
            KpiNodeSnapshotHourly.ts_start.desc(),
        )
    )

    if plant_node_id:
        base = base.where(KpiNodeSnapshotHourly.plant_node_id == plant_node_id)
    else:
        # 全厂：仅 is_kpi_enabled 的节点
        enabled_ids = select(PlantNode.id).where(PlantNode.is_kpi_enabled.is_(True))
        base = base.where(KpiNodeSnapshotHourly.plant_node_id.in_(enabled_ids))

    result = await db.execute(base)
    snaps = result.scalars().all()

    if not snaps:
        return _empty_kpi_cards(), _empty_kpi_summary()

    # 聚合 KPI 值（多节点时按 loop_count 加权平均）
    _node_kpi_fields()
    total_weight = sum(s.loop_count or 1 for s in snaps)

    def weighted_avg(field: str) -> float | None:
        numerator = sum(
            (getattr(s, field) or 0) * (s.loop_count or 1)
            for s in snaps
            if getattr(s, field) is not None
        )
        denominator = sum((s.loop_count or 1) for s in snaps if getattr(s, field) is not None)
        if denominator == 0:
            return None
        return round(float(numerator) / denominator, 2)

    # 构建 KPI 卡片
    cards: list[dict] = []
    for code in KPI_METRIC_CODES:
        val = weighted_avg(code)
        cards.append(
            {
                "metricKey": code,
                "metricName": KPI_NAME_MAP.get(code, code),
                "value": val,
                "unit": "%",
                "status": _kpi_status(code, val, _default_threshold(code)),
                "algorithmVersion": ALGORITHM_VERSION,
            }
        )

    # 综合评分卡片
    score_avg = weighted_avg("score")
    cards.append(
        {
            "metricKey": "composite_score",
            "metricName": KPI_NAME_MAP["composite_score"],
            "value": score_avg,
            "unit": "",
            "status": _score_to_status(score_avg),
            "algorithmVersion": ALGORITHM_VERSION,
        }
    )

    # 构建 KPI 汇总
    auto_loop_ratio = weighted_avg("auto_loop_ratio")
    realtime_auto_rate = weighted_avg("realtime_auto_rate")
    summary = {
        "good_value_rate": weighted_avg("good_value_rate"),
        "auto_mode_rate": weighted_avg("auto_mode_rate"),
        "effective_auto_rate": weighted_avg("effective_auto_rate"),
        "steady_rate": weighted_avg("steady_rate"),
        "accuracy_rate": weighted_avg("accuracy_rate"),
        "fast_response_rate": weighted_avg("fast_response_rate"),
        "oscillation_rate": weighted_avg("oscillation_rate"),
        "saturation_rate": weighted_avg("saturation_rate"),
        "composite_score": score_avg,
        "auto_loop_ratio": auto_loop_ratio,
        "realtime_auto_rate": realtime_auto_rate,
        "loop_count": total_weight,
        "node_count": len(snaps),
        "status": _score_to_status(score_avg),
        "algorithm_version": ALGORITHM_VERSION,
    }

    return cards, summary


def _empty_kpi_summary() -> dict:
    """空 KPI 汇总。"""
    return {
        "good_value_rate": None,
        "auto_mode_rate": None,
        "effective_auto_rate": None,
        "steady_rate": None,
        "accuracy_rate": None,
        "fast_response_rate": None,
        "oscillation_rate": None,
        "saturation_rate": None,
        "composite_score": None,
        "auto_loop_ratio": None,
        "realtime_auto_rate": None,
        "loop_count": 0,
        "node_count": 0,
        "status": "INCONCLUSIVE",
        "algorithm_version": ALGORITHM_VERSION,
    }


async def _aggregate_node_steady_trend(
    db: AsyncSession,
    plant_node_id: str | None,
    start: datetime,
    end: datetime,
) -> dict:
    """从节点级快照表聚合平稳率趋势（按小时分组）。"""
    hour_col = func.date_trunc("hour", KpiNodeSnapshotHourly.ts_start).label("hour")
    avg_col = func.avg(KpiNodeSnapshotHourly.steady_rate).label("avg_steady")

    stmt = (
        select(hour_col, avg_col)
        .where(
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
        )
        .group_by(hour_col)
        .order_by(hour_col.asc())
    )

    if plant_node_id:
        stmt = stmt.where(KpiNodeSnapshotHourly.plant_node_id == plant_node_id)
    else:
        enabled_ids = select(PlantNode.id).where(PlantNode.is_kpi_enabled.is_(True))
        stmt = stmt.where(KpiNodeSnapshotHourly.plant_node_id.in_(enabled_ids))

    result = await db.execute(stmt)
    rows = result.all()
    timestamps = [r.hour.strftime("%Y-%m-%dT%H:00:00") for r in rows]
    values = [round(float(r.avg_steady), 2) if r.avg_steady is not None else None for r in rows]
    return {"timestamps": timestamps, "values": values}


# ---------------------------------------------------------------------------
# S3-METRIC-005: 低效回路排行
# ---------------------------------------------------------------------------


async def get_ranking(
    db: AsyncSession,
    plant_node_id: str | None = None,
    time_window: str = "today",
    limit: int = 20,
    sort_by: str = "score",
    sort_order: str = "asc",
) -> list[dict]:
    """低效回路排行。

    Args:
        sort_by: 排序字段 score/steady_rate/good_value_rate
        sort_order: asc/desc（默认 asc，分数最低的在前）
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    if time_window == "today":
        start = now - timedelta(hours=24)
    elif time_window == "yesterday":
        start = now - timedelta(days=2)
        now = now - timedelta(days=1)
    else:
        delta = TIME_WINDOWS.get(time_window, timedelta(days=1))
        start = now - delta

    # 排序字段白名单（防止 SQL 注入：不直接拼接用户输入到 SQL）
    sort_field_map = {
        "score": "score",
        "steady_rate": "steady_rate",
        "good_value_rate": "good_value_rate",
        "fast_response_rate": "fast_response_rate",
    }
    sort_field_name = sort_field_map.get(sort_by, "score")

    # 子查询：每个回路最新一条 SUCCESS 快照（PostgreSQL DISTINCT ON）
    base = (
        select(KpiSnapshotHourly)
        .distinct(KpiSnapshotHourly.loop_id)
        .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_start.desc())
    )
    base = _apply_snapshot_filters(
        base,
        plant_node_id=plant_node_id,
        start=start,
        end=now,
        status_filter="SUCCESS",
    )
    subquery = base.subquery()
    snapshot_alias = aliased(KpiSnapshotHourly, subquery)

    # 外层查询：按排序字段排序（NULLS LAST）并截断
    sort_column = getattr(snapshot_alias, sort_field_name)
    if sort_order.lower() == "desc":
        order_expr = sort_column.desc().nulls_last()
    else:
        order_expr = sort_column.asc().nulls_last()

    stmt = select(snapshot_alias).order_by(order_expr).limit(limit)
    result = await db.execute(stmt)
    snapshots = result.scalars().all()

    # 收集 loop_id
    loop_ids = [str(s.loop_id) for s in snapshots if s.loop_id]

    # 查询回路基础信息
    loop_map: dict[str, LoopLedger] = {}
    unit_map: dict[str, str] = {}
    if loop_ids:
        l_result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
        for loop in l_result.scalars().all():
            loop_map[str(loop.id)] = loop

        # 批量查 unit_name
        unit_ids = [str(loop.unit_id) for loop in loop_map.values() if loop.unit_id]
        if unit_ids:
            u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
            for node in u_result.scalars().all():
                unit_map[str(node.id)] = node.name

    # 查询预诊断（从 action_tracker 取最新诊断标签）
    diagnosis_map: dict[str, str] = {}
    action_status_map: dict[str, str] = {}
    if loop_ids:
        t_result = await db.execute(
            select(ActionTracker).where(ActionTracker.loop_id.in_(loop_ids))
        )
        for tracker in t_result.scalars().all():
            lid = str(tracker.loop_id) if tracker.loop_id else ""
            if lid and tracker.diagnosis_label:
                diagnosis_map[lid] = tracker.diagnosis_label
            if lid and tracker.action_status:
                action_status_map[lid] = tracker.action_status

    # 构建排行项（SQL 已完成去重、排序、截断）
    items: list[dict] = []
    for snap in snapshots:
        loop_id = str(snap.loop_id) if snap.loop_id else ""
        if not loop_id:
            continue
        loop = loop_map.get(loop_id)
        if not loop:
            continue
        items.append(
            {
                "loopId": loop_id,
                "tagName": loop.tag_name,
                "unitName": unit_map.get(str(loop.unit_id)) if loop.unit_id else None,
                "compositeScore": _to_float(snap.score),
                "goodValueRate": _to_float(snap.good_value_rate),
                "autoModeRate": _to_float(snap.auto_mode_rate),
                "effectiveAutoRate": _to_float(snap.effective_auto_rate),
                "steadyRate": _to_float(snap.steady_rate),
                "accuracyRate": _to_float(snap.accuracy_rate),
                "fastResponseRate": _to_float(snap.fast_response_rate),
                "oscillationRate": _to_float(snap.oscillation_rate),
                "saturationRate": _to_float(snap.saturation_rate),
                "status": _score_to_status(snap.score),
                "algorithmVersion": ALGORITHM_VERSION,
                "preDiagnosis": diagnosis_map.get(loop_id),
                "actionStatus": action_status_map.get(loop_id, "PENDING"),
            }
        )

    # 打排名
    for idx, item in enumerate(items, start=1):
        item["rank"] = idx

    return items


# ---------------------------------------------------------------------------
# S3-METRIC-006: 统计报表
# ---------------------------------------------------------------------------


async def get_analytics(
    db: AsyncSession,
    start_time: str,
    end_time: str,
    plant_node_id: str | None = None,
    metric_key: str = "score",
    granularity: str = "day",
) -> dict:
    """统计报表数据。"""
    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except ValueError:
        start_dt = datetime.fromisoformat(start_time)
    try:
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError:
        end_dt = datetime.fromisoformat(end_time)

    snapshots = await _query_snapshots(
        db=db,
        plant_node_id=plant_node_id,
        start=start_dt,
        end=end_dt,
    )

    # KPI 趋势（SQL 聚合）
    kpi_trend = await _aggregate_kpi_trend(
        db=db,
        plant_node_id=plant_node_id,
        start=start_dt,
        end=end_dt,
        metric_key=metric_key,
        granularity=granularity,
    )

    # 单元排名
    unit_ranking = await _aggregate_unit_ranking(db, snapshots)

    # 坏演员分布
    bad_actor_distribution = await _aggregate_bad_actor_distribution(db, snapshots)

    return {
        "filterScope": {
            "startTime": start_time,
            "endTime": end_time,
            "plantNodeId": plant_node_id,
            "metricKey": metric_key,
            "granularity": granularity,
        },
        "kpiTrend": kpi_trend,
        "unitRanking": unit_ranking,
        "badActorDistribution": bad_actor_distribution,
    }


async def export_analytics_csv(
    db: AsyncSession,
    start_time: str,
    end_time: str,
    plant_node_id: str | None = None,
    metric_key: str = "score",
    granularity: str = "day",
) -> str:
    """导出统计报表为 CSV 字符串。"""
    analytics = await get_analytics(
        db=db,
        start_time=start_time,
        end_time=end_time,
        plant_node_id=plant_node_id,
        metric_key=metric_key,
        granularity=granularity,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "key", "value"])

    # 写入筛选范围
    fs = analytics["filterScope"]
    for k, v in fs.items():
        writer.writerow(["filterScope", k, v])

    # 写入 KPI 趋势
    trend = analytics["kpiTrend"]
    timestamps = trend.get("timestamps", [])
    for series in trend.get("series", []):
        for i, value in enumerate(series.get("values", [])):
            ts = timestamps[i] if i < len(timestamps) else ""
            writer.writerow(
                [
                    "kpiTrend",
                    f"{series['metricKey']}@{ts}",
                    value if value is not None else "",
                ]
            )

    # 写入单元排名
    for item in analytics["unitRanking"]:
        writer.writerow(
            [
                "unitRanking",
                item["unitName"] or item["unitId"],
                item["score"] if item["score"] is not None else "",
            ]
        )

    # 写入坏演员分布
    for item in analytics["badActorDistribution"]:
        writer.writerow(["badActorDistribution", item["label"], item["count"]])

    return output.getvalue()


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _metric_config_to_dict(c: MetricConfig) -> dict:
    return {
        "metricId": str(c.id),
        "metricCode": c.metric_code,
        "metricName": c.metric_name,
        "formula": c.formula,
        "weight": float(c.weight) if c.weight is not None else None,
        "threshold": c.threshold,
        "controlType": c.control_type,
        "isEnabled": bool(c.is_enabled) if c.is_enabled is not None else True,
        "updatedBy": c.updated_by,
        "updatedAt": c.updated_at.isoformat() if c.updated_at else None,
        "version": c.version or 1,
    }


def _engine_rule_to_dict(r: EngineRule) -> dict:
    return {
        "ruleId": str(r.id),
        "ruleCode": r.rule_code,
        "ruleName": r.rule_name,
        "ruleType": r.rule_type,
        "params": r.params,
        "isEnabled": bool(r.is_enabled) if r.is_enabled is not None else True,
        "updatedBy": r.updated_by,
        "updatedAt": r.updated_at.isoformat() if r.updated_at else None,
    }


def _apply_snapshot_filters(
    stmt,
    plant_node_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
):
    """为快照查询添加时间/状态/装置过滤条件。"""
    if start is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start >= start)
    if end is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start <= end)
    if status_filter:
        stmt = stmt.where(KpiSnapshotHourly.status == status_filter)
    if plant_node_id:
        # 通过 join loop_ledger 过滤
        stmt = stmt.join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id).where(
            LoopLedger.unit_id == plant_node_id
        )
    return stmt


async def _query_snapshots(
    db: AsyncSession,
    plant_node_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
) -> list[KpiSnapshotHourly]:
    """查询快照数据，可选按装置/时间/状态过滤。"""
    stmt = _apply_snapshot_filters(
        select(KpiSnapshotHourly),
        plant_node_id=plant_node_id,
        start=start,
        end=end,
        status_filter=status_filter,
    ).order_by(KpiSnapshotHourly.ts_start.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _score_to_status(score: Decimal | float | None) -> str:
    """综合评分 → 5 级状态枚举（对齐 GB/T 44693.2-2024 §6.3 性能分级）。

    5 级划分：
        - EXCELLENT (优):  score >= 90
        - GOOD (良):       80 <= score < 90
        - FAIR (中):       70 <= score < 80
        - WARNING (差):    60 <= score < 70
        - POOR (劣):       score < 60
    """
    if score is None:
        return "INCONCLUSIVE"
    s = float(score)
    if s >= 90:
        return "EXCELLENT"
    if s >= 80:
        return "GOOD"
    if s >= 70:
        return "FAIR"
    if s >= 60:
        return "WARNING"
    return "POOR"


def _to_float(value: Decimal | float | None) -> float | None:
    """Decimal/float → float，None 透传。"""
    if value is None:
        return None
    return float(value)


def _kpi_status(metric_code: str, value: float | None, threshold: dict | None) -> str:
    """根据阈值判断 KPI 状态。"""
    if value is None:
        return "INCONCLUSIVE"
    if not threshold:
        return "GOOD" if value > 0 else "POOR"
    alert = threshold.get("alert")
    if alert is None:
        return "GOOD"
    # oscillation_rate / saturation_rate 是越低越好
    if metric_code in ("oscillation_rate", "saturation_rate"):
        if value <= alert:
            return "GOOD"
        if value <= alert * 1.5:
            return "WARNING"
        return "POOR"
    # 其他指标越高越好
    if value >= alert:
        return "GOOD"
    if value >= alert * 0.9:
        return "WARNING"
    return "POOR"


async def _aggregate_kpi_cards(
    db: AsyncSession,
    plant_node_id: str | None,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """聚合 KPI 卡片（6 大 KPI + 综合评分 = 7 张卡片）— SQL 聚合。"""
    fields = (*KPI_METRIC_CODES, "score")
    avg_cols = [func.avg(getattr(KpiSnapshotHourly, f)).label(f) for f in fields]
    stmt = _apply_snapshot_filters(
        select(func.count().label("cnt"), *avg_cols),
        plant_node_id=plant_node_id,
        start=start,
        end=end,
        status_filter="SUCCESS",
    )
    result = await db.execute(stmt)
    row = result.one()

    if row.cnt == 0:
        return _empty_kpi_cards()

    cards: list[dict] = []
    for code in KPI_METRIC_CODES:
        val = _to_float(getattr(row, code))
        cards.append(
            {
                "metricKey": code,
                "metricName": KPI_NAME_MAP.get(code, code),
                "value": round(val, 2) if val is not None else None,
                "unit": "%",
                "status": _kpi_status(code, val, _default_threshold(code)),
                "algorithmVersion": ALGORITHM_VERSION,
            }
        )

    # 综合评分卡片
    score_avg = _to_float(row.score)
    cards.append(
        {
            "metricKey": "composite_score",
            "metricName": KPI_NAME_MAP["composite_score"],
            "value": round(score_avg, 2) if score_avg is not None else None,
            "unit": "",
            "status": _score_to_status(score_avg),
            "algorithmVersion": ALGORITHM_VERSION,
        }
    )
    return cards


def _empty_kpi_cards() -> list[dict]:
    """空 KPI 卡片列表。"""
    cards: list[dict] = []
    for code in KPI_METRIC_CODES:
        cards.append(
            {
                "metricKey": code,
                "metricName": KPI_NAME_MAP.get(code, code),
                "value": None,
                "unit": "%",
                "status": "INCONCLUSIVE",
                "algorithmVersion": ALGORITHM_VERSION,
            }
        )
    cards.append(
        {
            "metricKey": "composite_score",
            "metricName": KPI_NAME_MAP["composite_score"],
            "value": None,
            "unit": "",
            "status": "INCONCLUSIVE",
            "algorithmVersion": ALGORITHM_VERSION,
        }
    )
    return cards


def _default_threshold(metric_code: str) -> dict:
    """默认阈值。"""
    defaults = {
        "good_value_rate": {"min": 0, "max": 100, "alert": 80},
        "auto_mode_rate": {"min": 0, "max": 100, "alert": 90},
        "effective_auto_rate": {"min": 0, "max": 100, "alert": 90},
        "steady_rate": {"min": 0, "max": 100, "alert": 85},
        "accuracy_rate": {"min": 0, "max": 100, "alert": 80},
        "fast_response_rate": {"min": 0, "max": 100, "alert": 80},
        "oscillation_rate": {"min": 0, "max": 100, "alert": 20},
        "saturation_rate": {"min": 0, "max": 100, "alert": 15},
    }
    return defaults.get(metric_code, {})


async def _aggregate_kpi_summary(
    db: AsyncSession,
    plant_node_id: str | None,
    start: datetime,
    end: datetime,
) -> dict:
    """聚合 KPI 汇总 — 按回路重要度加权聚合（对齐 GB/T 44693.2-2024 §6.2）。

    P0#12: 装置级加权聚合 — 使用 LoopLedger.score_weight 作为权重，
           加权平均公式: weighted_avg = Σ(value_i × w_i) / Σ(w_i)
           权重为 NULL 时按 1.0 处理（等权）。

    P0#11: 投自动回路占比 — 统计 auto_mode_rate > 0 的回路数占比，
           反映装置内有多少回路实际投入自动控制。
    """
    fields = (*KPI_METRIC_CODES, "score")
    # 使用 COALESCE 处理 NULL 权重（默认 1.0），确保不丢失数据点
    weight_col = func.coalesce(LoopLedger.score_weight, Decimal("1.0")).label("w")
    # SUM(weight) 可能因所有权重显式为 0 而等于 0，使用 NULLIF 避免 SQL 除零
    weight_sum_col = func.nullif(func.sum(weight_col), 0).label("weight_sum")

    # 加权聚合：Σ(value × w) / Σ(w)
    # NULLIF 确保 SUM(w)=0 时返回 NULL 而非抛除零错误
    weighted_cols = []
    for f in fields:
        col = getattr(KpiSnapshotHourly, f)
        # SUM(value * w) / NULLIF(SUM(w), 0)
        weighted_cols.append((func.sum(col * weight_col) / weight_sum_col).label(f))

    # 投自动回路占比：COUNT(auto_mode_rate > 0) / COUNT(*)
    auto_loop_count = func.sum(
        func.coalesce(
            func.cast(
                KpiSnapshotHourly.auto_mode_rate > 0,
                Integer,
            ),
            0,
        )
    ).label("auto_loop_count")
    total_count = func.count().label("cnt")

    stmt = _apply_snapshot_filters(
        select(total_count, auto_loop_count, weight_sum_col, *weighted_cols),
        plant_node_id=plant_node_id,
        start=start,
        end=end,
        status_filter="SUCCESS",
    )
    result = await db.execute(stmt)
    row = result.one()

    empty = {
        "good_value_rate": None,
        "auto_mode_rate": None,
        "effective_auto_rate": None,
        "steady_rate": None,
        "accuracy_rate": None,
        "fast_response_rate": None,
        "oscillation_rate": None,
        "saturation_rate": None,
        "composite_score": None,
        "auto_loop_ratio": None,
        "status": "INCONCLUSIVE",
        "algorithm_version": ALGORITHM_VERSION,
    }
    if row.cnt == 0:
        return empty

    # 除零保护：SUM(weight)=0（所有权重显式为 0）时返回空结果
    weight_sum_val = _to_float(row.weight_sum)
    if weight_sum_val is None or weight_sum_val == 0:
        logger.warning(
            "[装置级聚合] plant_node_id=%s, SUM(weight)=%s（为 0 或 NULL），"
            "无法计算加权平均，返回 INCONCLUSIVE",
            plant_node_id,
            weight_sum_val,
        )
        return empty

    def avg_value(field: str) -> float | None:
        val = _to_float(getattr(row, field))
        return round(val, 2) if val is not None else None

    score_avg = avg_value("score")
    # 投自动回路占比 = auto_loop_count / total_count × 100
    auto_loop_count_val = _to_float(row.auto_loop_count) or 0.0
    auto_loop_ratio = round(auto_loop_count_val / float(row.cnt) * 100, 2)

    logger.debug(
        "[装置级聚合] plant_node_id=%s, 回路数=%d, 投自动回路数=%.0f, "
        "投自动回路占比=%.2f%%, SUM(weight)=%.4f, 加权综合评分=%s",
        plant_node_id,
        row.cnt,
        auto_loop_count_val,
        auto_loop_ratio,
        weight_sum_val,
        score_avg,
    )

    return {
        "good_value_rate": avg_value("good_value_rate"),
        "auto_mode_rate": avg_value("auto_mode_rate"),
        "effective_auto_rate": avg_value("effective_auto_rate"),
        "steady_rate": avg_value("steady_rate"),
        "accuracy_rate": avg_value("accuracy_rate"),
        "fast_response_rate": avg_value("fast_response_rate"),
        "oscillation_rate": avg_value("oscillation_rate"),
        "saturation_rate": avg_value("saturation_rate"),
        "composite_score": score_avg,
        "auto_loop_ratio": auto_loop_ratio,
        "status": _score_to_status(score_avg),
        "algorithm_version": ALGORITHM_VERSION,
    }


async def _aggregate_steady_trend(
    db: AsyncSession,
    plant_node_id: str | None,
    start: datetime,
    end: datetime,
) -> dict:
    """聚合平稳率趋势（按小时聚合）— SQL date_trunc + GROUP BY + AVG。"""
    hour_col = func.date_trunc("hour", KpiSnapshotHourly.ts_start).label("hour")
    avg_col = func.avg(KpiSnapshotHourly.steady_rate).label("avg_steady")
    stmt = (
        _apply_snapshot_filters(
            select(hour_col, avg_col),
            plant_node_id=plant_node_id,
            start=start,
            end=end,
        )
        .group_by(hour_col)
        .order_by(hour_col.asc())
    )
    result = await db.execute(stmt)
    rows = result.all()
    timestamps = [r.hour.strftime("%Y-%m-%dT%H:00:00") for r in rows]
    values = [round(float(r.avg_steady), 2) if r.avg_steady is not None else None for r in rows]
    return {"timestamps": timestamps, "values": values}


async def _aggregate_kpi_trend(
    db: AsyncSession,
    plant_node_id: str | None,
    start: datetime,
    end: datetime,
    metric_key: str,
    granularity: str,
) -> dict:
    """聚合 KPI 趋势（按粒度分组）— SQL date_trunc + GROUP BY + AVG。"""
    field_map = {
        "score": KpiSnapshotHourly.score,
        "good_value_rate": KpiSnapshotHourly.good_value_rate,
        "auto_mode_rate": KpiSnapshotHourly.auto_mode_rate,
        "effective_auto_rate": KpiSnapshotHourly.effective_auto_rate,
        "steady_rate": KpiSnapshotHourly.steady_rate,
        "accuracy_rate": KpiSnapshotHourly.accuracy_rate,
        "fast_response_rate": KpiSnapshotHourly.fast_response_rate,
        "oscillation_rate": KpiSnapshotHourly.oscillation_rate,
        "saturation_rate": KpiSnapshotHourly.saturation_rate,
    }
    column = field_map.get(metric_key, KpiSnapshotHourly.score)
    metric_name = KPI_NAME_MAP.get(metric_key, metric_key)

    # 粒度白名单校验（date_trunc 第一参数为绑定参数，安全）
    if granularity not in ("hour", "day", "week", "month"):
        granularity = "day"

    bucket_col = func.date_trunc(granularity, KpiSnapshotHourly.ts_start).label("bucket")
    avg_col = func.avg(column).label("avg_value")
    stmt = (
        _apply_snapshot_filters(
            select(bucket_col, avg_col),
            plant_node_id=plant_node_id,
            start=start,
            end=end,
        )
        .group_by(bucket_col)
        .order_by(bucket_col.asc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    def format_bucket(dt: datetime) -> str:
        if granularity == "hour":
            return dt.strftime("%Y-%m-%dT%H:00:00")
        if granularity == "month":
            return dt.strftime("%Y-%m")
        return dt.strftime("%Y-%m-%d")

    timestamps = [format_bucket(r.bucket) for r in rows]
    values = [round(float(r.avg_value), 2) if r.avg_value is not None else None for r in rows]
    return {
        "timestamps": timestamps,
        "series": [
            {
                "metricKey": metric_key,
                "metricName": metric_name,
                "values": values,
            }
        ],
    }


async def _aggregate_unit_ranking(
    db: AsyncSession,
    snapshots: list[KpiSnapshotHourly],
) -> list[dict]:
    """聚合单元排名。"""
    if not snapshots:
        return []

    # 按 loop_id 取最新快照
    loop_latest: dict[str, KpiSnapshotHourly] = {}
    for snap in snapshots:
        lid = str(snap.loop_id) if snap.loop_id else ""
        if not lid:
            continue
        if lid not in loop_latest or snap.ts_start > loop_latest[lid].ts_start:
            loop_latest[lid] = snap

    loop_ids = list(loop_latest.keys())
    if not loop_ids:
        return []

    # 查询回路 → 单元
    loop_unit: dict[str, str] = {}
    l_result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
    for loop in l_result.scalars().all():
        if loop.unit_id:
            loop_unit[str(loop.id)] = str(loop.unit_id)

    unit_ids = list(set(loop_unit.values()))
    unit_name_map: dict[str, str] = {}
    if unit_ids:
        u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
        for node in u_result.scalars().all():
            unit_name_map[str(node.id)] = node.name

    # 按单元聚合
    unit_scores: dict[str, list[float]] = {}
    for lid, snap in loop_latest.items():
        uid = loop_unit.get(lid)
        if not uid:
            continue
        if snap.score is None:
            continue
        unit_scores.setdefault(uid, []).append(float(snap.score))

    items = [
        {
            "unitId": uid,
            "unitName": unit_name_map.get(uid),
            "score": round(sum(scores) / len(scores), 2) if scores else None,
            "loopCount": len(scores),
        }
        for uid, scores in unit_scores.items()
    ]
    items.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0)))
    return items


async def _aggregate_bad_actor_distribution(
    db: AsyncSession,
    snapshots: list[KpiSnapshotHourly],
) -> list[dict]:
    """聚合坏演员分布（从 action_tracker 取诊断标签）。"""
    loop_ids = [str(s.loop_id) for s in snapshots if s.loop_id]
    if not loop_ids:
        return []

    # 查询 action_tracker 中诊断标签
    unique_loop_ids = list(set(loop_ids))
    t_result = await db.execute(
        select(ActionTracker).where(ActionTracker.loop_id.in_(unique_loop_ids))
    )
    label_count: dict[str, int] = {}
    for tracker in t_result.scalars().all():
        if tracker.diagnosis_label:
            label_count[tracker.diagnosis_label] = label_count.get(tracker.diagnosis_label, 0) + 1

    items = [{"label": label, "count": count} for label, count in label_count.items()]
    items.sort(key=lambda x: -x["count"])
    return items


__all__ = [
    "ALGORITHM_VERSION",
    "DASHBOARD_CACHE_TTL",
    "KPI_METRIC_CODES",
    "KPI_NAME_MAP",
    "export_analytics_csv",
    "get_analytics",
    "get_board",
    "get_ranking",
    "list_engine_rules",
    "list_metric_configs",
    "update_engine_rule",
    "update_metric_config",
]
