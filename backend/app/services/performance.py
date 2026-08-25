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

from sqlalchemy import Integer, case, func, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.exceptions import BizError
from app.core.modules import is_module_enabled
from app.core.redis import redis_client
from app.models.audit import SysAuditLog
from app.models.engine import EngineRule
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly, MetricConfig
from app.models.node_kpi import KpiNodeSnapshotHourly
from app.models.plant_node import PlantNode
from app.models.sys_config import SysConfig
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
    "fast_rate",
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
    "fast_rate": "快速率",
    "oscillation_rate": "振荡率",
    "saturation_rate": "饱和率",
    "instrument_fault_rate": "仪表故障率",
    "composite_score": "综合评分",
    "auto_loop_ratio": "投自动回路占比",
}

# 时间窗映射（基于"今天"为基准）
TIME_WINDOWS: dict[str, timedelta] = {
    "today": timedelta(days=1),
    "yesterday": timedelta(days=1),
    "last_8_hours": timedelta(hours=8),
    "last_24_hours": timedelta(hours=24),
    "last_72_hours": timedelta(hours=72),
    "last_168_hours": timedelta(hours=168),
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

    # EVAL_CALC_CYCLE 变更后通知 Beat 进程即时重载调度（无需重启）
    if rule.rule_code == "EVAL_CALC_CYCLE":
        try:
            await redis_client.publish(
                "clpm:beat:reload",
                json.dumps(
                    {"rule_code": rule.rule_code, "updated_by": operator},
                    ensure_ascii=False,
                ),
            )
            logger.info("已通知 Beat 重载调度配置 (rule=%s)", rule.rule_code)
        except Exception as exc:  # noqa: BLE001
            logger.warning("通知 Beat 重载失败（调度将在下次 Beat 重启时生效）: %s", exc)

    return after


# ---------------------------------------------------------------------------
# S3-METRIC-004: 全局看板
# ---------------------------------------------------------------------------


async def get_board(
    db: AsyncSession,
    plant_node_id: str | None = None,
    time_window: str = "today",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict:
    """全局看板数据。

    Redis 缓存 5 分钟。``time_window="custom"`` 时需提供 ``start_time``/``end_time``。
    """
    cache_key = DASHBOARD_CACHE_KEY_TEMPLATE.format(
        plant_node_id=plant_node_id or "all",
        time_window=(
            f"custom:{start_time.isoformat()}~{end_time.isoformat()}"
            if time_window == "custom" and start_time and end_time
            else time_window
        ),
    )
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取看板缓存失败: %s", exc)

    # 计算 time_window 对应的时间范围
    if time_window == "custom" and start_time is not None and end_time is not None:
        start, now = start_time, end_time
    else:
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

    # P2 IA优化：L0~L4 适用性分布（从窗口内每回路最新快照 fitness_level 汇总）
    fitness_distribution: dict[str, int] = {
        "L0": 0,
        "L1": 0,
        "L2": 0,
        "L3": 0,
        "L4": 0,
    }
    try:
        from app.models.metric import KpiSnapshotHourly as _KpiHourly

        subq_latest = (
            select(_KpiHourly.loop_id, _KpiHourly.fitness_level)
            .distinct(_KpiHourly.loop_id)
            .order_by(_KpiHourly.loop_id, _KpiHourly.ts_start.desc())
        )
        if plant_node_id:
            from app.services.node_performance import collect_descendant_loop_ids

            scope_ids = await collect_descendant_loop_ids(db, plant_node_id)
            if scope_ids:
                subq_latest = subq_latest.where(_KpiHourly.loop_id.in_(scope_ids))
        subq_latest = subq_latest.where(
            _KpiHourly.ts_start >= start,
            _KpiHourly.ts_start <= now,
        )
        subquery = subq_latest.subquery()
        stmt_lvl = (
            select(subquery.c.fitness_level, func.count())
            .select_from(subquery)
            .group_by(subquery.c.fitness_level)
        )
        rows = (await db.execute(stmt_lvl)).all()
        for lvl, cnt in rows:
            if lvl in fitness_distribution:
                fitness_distribution[lvl] = int(cnt or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_board fitness 分布计算失败，已忽略: %s", exc)

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
        "fitnessDistribution": fitness_distribution,
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
        "fast_rate",
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
    # Phase 1 新增：仪表故障率卡片（辅助诊断指标，越低越好）
    fault_avg = weighted_avg("instrument_fault_rate")
    # 诊断日志：逐节点打印仪表故障率，便于排查数据为空问题
    ifr_node_details = [
        {
            "node_id": str(s.plant_node_id),
            "instrument_fault_rate": _to_float(s.instrument_fault_rate),
            "loop_count": s.loop_count,
        }
        for s in snaps
    ]
    ifr_node_non_null = [d for d in ifr_node_details if d["instrument_fault_rate"] is not None]
    logger.info(
        "[多节点聚合-仪表故障率] 节点数=%d, 有值=%d, 无值=%d, 加权均值=%s, 详情=%s",
        len(snaps),
        len(ifr_node_non_null),
        len(ifr_node_details) - len(ifr_node_non_null),
        fault_avg,
        ifr_node_details,
    )
    cards.append(
        {
            "metricKey": "instrument_fault_rate",
            "metricName": KPI_NAME_MAP["instrument_fault_rate"],
            "value": fault_avg,
            "unit": "%",
            "status": _kpi_status(
                "instrument_fault_rate",
                fault_avg,
                _default_threshold("instrument_fault_rate"),
            ),
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
        "fast_rate": weighted_avg("fast_rate"),
        "oscillation_rate": weighted_avg("oscillation_rate"),
        "saturation_rate": weighted_avg("saturation_rate"),
        "instrument_fault_rate": weighted_avg("instrument_fault_rate"),
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
        "fast_rate": None,
        "oscillation_rate": None,
        "saturation_rate": None,
        "instrument_fault_rate": None,
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
    offset: int = 0,
    sort_by: str = "score",
    sort_order: str = "asc",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[dict]:
    """低效回路排行。

    Args:
        limit: 返回条数（最多 100）
        offset: 偏移量（配合 limit 实现分页拉全量）
        sort_by: 排序字段 score/steady_rate/good_value_rate
        sort_order: asc/desc（默认 asc，分数最低的在前）
        start_time/end_time: ``time_window="custom"`` 时的自定义窗口
    """
    if time_window == "custom" and start_time is not None and end_time is not None:
        start, now = start_time, end_time
    else:
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
    # 指标分析页（M1）：扩展 accuracy_rate/auto_mode_rate/effective_auto_rate，
    # 支持单指标横切排行（默认回退 score）
    sort_field_map = {
        "score": "score",
        "steady_rate": "steady_rate",
        "good_value_rate": "good_value_rate",
        "fast_rate": "fast_rate",
        "accuracy_rate": "accuracy_rate",
        "auto_mode_rate": "auto_mode_rate",
        "effective_auto_rate": "effective_auto_rate",
    }
    sort_field_name = sort_field_map.get(sort_by, "score")

    # 递归收集节点下属回路 ID（支持 FACTORY/AREA → UNIT 层级）
    descendant_loop_ids: list[str] | None = None
    if plant_node_id:
        from app.services.node_performance import collect_descendant_loop_ids

        descendant_loop_ids = await collect_descendant_loop_ids(db, plant_node_id)

    # 子查询：每个回路最新一条 SUCCESS 快照（PostgreSQL DISTINCT ON）
    base = (
        select(KpiSnapshotHourly)
        .distinct(KpiSnapshotHourly.loop_id)
        .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_start.desc())
    )
    base = _apply_snapshot_filters(
        base,
        plant_node_id=None,
        start=start,
        end=now,
        status_filter="SUCCESS",
        loop_ids=descendant_loop_ids,
    )
    subquery = base.subquery()
    snapshot_alias = aliased(KpiSnapshotHourly, subquery)

    # 外层查询：按排序字段排序（NULLS LAST）并截断
    sort_column = getattr(snapshot_alias, sort_field_name)
    if sort_order.lower() == "desc":
        order_expr = sort_column.desc().nulls_last()
    else:
        order_expr = sort_column.asc().nulls_last()

    stmt = select(snapshot_alias).order_by(order_expr).limit(limit).offset(offset)
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

    # 查询预诊断（从 action_tracker 取最新开放态诊断标签）
    # D1/D2 整改：仅取 PENDING/IN_PROGRESS 的 tracker，避免已闭环的历史记录
    # 干扰预诊断展示；同一回路可能有多个标签的 tracker，取最新一条
    # 模块热插拔：诊断模块禁用时跳过 tracker 查询
    diagnosis_map: dict[str, str] = {}
    action_status_map: dict[str, str] = {}
    if loop_ids and is_module_enabled("diagnosis"):
        t_result = await db.execute(
            select(ActionTracker)
            .where(ActionTracker.loop_id.in_(loop_ids))
            .where(ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"]))
            .order_by(ActionTracker.created_at.desc().nulls_last())
        )
        for tracker in t_result.scalars().all():
            lid = str(tracker.loop_id) if tracker.loop_id else ""
            if lid and tracker.diagnosis_label and lid not in diagnosis_map:
                diagnosis_map[lid] = tracker.diagnosis_label
            if lid and tracker.action_status and lid not in action_status_map:
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
                "loopName": loop.description,
                "unitName": unit_map.get(str(loop.unit_id)) if loop.unit_id else None,
                "score": _to_float(snap.score),
                "goodValueRate": _to_float(snap.good_value_rate),
                "autoModeRate": _to_float(snap.auto_mode_rate),
                "effectiveAutoRate": _to_float(snap.effective_auto_rate),
                "steadyRate": _to_float(snap.steady_rate),
                "accuracyRate": _to_float(snap.accuracy_rate),
                "fastRate": _to_float(snap.fast_rate),
                "oscillationRate": _to_float(snap.oscillation_rate),
                "saturationRate": _to_float(snap.saturation_rate),
                "instrumentFaultRate": _to_float(snap.instrument_fault_rate),
                "status": _score_to_status(snap.score),
                "algorithmVersion": ALGORITHM_VERSION,
                "preDiagnosis": diagnosis_map.get(loop_id),
                "actionStatus": action_status_map.get(loop_id, "PENDING"),
                "includeInEvaluation": (loop.include_in_evaluation if loop is not None else None),
                "validRate": _to_float(snap.valid_rate),
                "samplingFreq": snap.sampling_freq,
                "qualityPolicy": snap.quality_policy,
                "confidenceLevel": snap.confidence_level,
                # P2 IA优化：适用性评估字段（从快照直接取）
                "fitnessLevel": snap.fitness_level,
                "fitnessTags": (
                    list(snap.fitness_tags)
                    if isinstance(snap.fitness_tags, list)
                    else (None if snap.fitness_tags is None else list(snap.fitness_tags))
                ),
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
    loop_ids: list[str] | None = None,
):
    """为快照查询添加时间/状态/装置过滤条件。

    若提供 ``loop_ids``，直接按回路 ID 列表过滤（支持递归子节点）。
    否则若提供 ``plant_node_id``，通过 join loop_ledger 按 unit_id 过滤（仅直接子节点）。
    """
    if start is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start >= start)
    if end is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start <= end)
    if status_filter:
        stmt = stmt.where(KpiSnapshotHourly.status == status_filter)
    if loop_ids is not None:
        if loop_ids:
            stmt = stmt.where(KpiSnapshotHourly.loop_id.in_(loop_ids))
        else:
            stmt = stmt.where(False)
    elif plant_node_id:
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
    # oscillation_rate / saturation_rate / instrument_fault_rate 是越低越好
    if metric_code in ("oscillation_rate", "saturation_rate", "instrument_fault_rate"):
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
    """聚合 KPI 卡片（6 大 KPI + 综合评分 + 仪表故障率 = 9 张卡片）— SQL 聚合。"""
    fields = (*KPI_METRIC_CODES, "score", "instrument_fault_rate")
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
    # Phase 1 新增：仪表故障率卡片（辅助诊断指标，越低越好）
    fault_avg = _to_float(row.instrument_fault_rate)
    cards.append(
        {
            "metricKey": "instrument_fault_rate",
            "metricName": KPI_NAME_MAP["instrument_fault_rate"],
            "value": round(fault_avg, 2) if fault_avg is not None else None,
            "unit": "%",
            "status": _kpi_status(
                "instrument_fault_rate",
                fault_avg,
                _default_threshold("instrument_fault_rate"),
            ),
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
    # Phase 1 新增：仪表故障率空卡片
    cards.append(
        {
            "metricKey": "instrument_fault_rate",
            "metricName": KPI_NAME_MAP["instrument_fault_rate"],
            "value": None,
            "unit": "%",
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
        "fast_rate": {"min": 0, "max": 100, "alert": 80},
        "oscillation_rate": {"min": 0, "max": 100, "alert": 20},
        "saturation_rate": {"min": 0, "max": 100, "alert": 15},
        "instrument_fault_rate": {"min": 0, "max": 100, "alert": 10},
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
    fields = (*KPI_METRIC_CODES, "score", "instrument_fault_rate")
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
        "fast_rate": None,
        "oscillation_rate": None,
        "saturation_rate": None,
        "instrument_fault_rate": None,
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

    # 仪表故障率诊断日志
    ifr_avg = avg_value("instrument_fault_rate")
    logger.info(
        "[装置级聚合-仪表故障率] plant_node_id=%s, 回路数=%d, "
        "SQL加权仪表故障率=%s, 加权综合评分=%s",
        plant_node_id,
        row.cnt,
        ifr_avg,
        score_avg,
    )

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
        "fast_rate": avg_value("fast_rate"),
        "oscillation_rate": avg_value("oscillation_rate"),
        "saturation_rate": avg_value("saturation_rate"),
        "instrument_fault_rate": ifr_avg,
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
        "fast_rate": KpiSnapshotHourly.fast_rate,
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
    """聚合坏演员分布（从 action_tracker 取开放态诊断标签）。

    D1/D2 整改：仅统计 PENDING/IN_PROGRESS 的 tracker，避免已闭环的历史
    记录膨胀坏演员计数。
    """
    loop_ids = [str(s.loop_id) for s in snapshots if s.loop_id]
    if not loop_ids:
        return []

    # 查询 action_tracker 中开放态诊断标签
    # D1 整改：补 created_at DESC 排序，确保同一回路多条记录时取到最新一条
    # （与 L717 回路排行榜预诊断标签查询口径一致）
    # 模块热插拔：诊断模块禁用时跳过 tracker 查询，返回空分布
    unique_loop_ids = list(set(loop_ids))
    if not is_module_enabled("diagnosis"):
        return []
    t_result = await db.execute(
        select(ActionTracker)
        .where(ActionTracker.loop_id.in_(unique_loop_ids))
        .where(ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"]))
        .order_by(ActionTracker.created_at.desc().nulls_last())
    )
    label_count: dict[str, int] = {}
    for tracker in t_result.scalars().all():
        if tracker.diagnosis_label:
            label_count[tracker.diagnosis_label] = label_count.get(tracker.diagnosis_label, 0) + 1

    items = [{"label": label, "count": count} for label, count in label_count.items()]
    items.sort(key=lambda x: -x["count"])
    return items


# ---------------------------------------------------------------------------
# 回路小时指标快照列表
# ---------------------------------------------------------------------------

# 性能等级名称（按 level 1-5 顺序，对齐 GB/T 44693.2-2024 §6.3）
GRADE_NAMES = ("EXCELLENT", "GOOD", "FAIR", "WARNING", "POOR")


async def _load_grading_thresholds(db: AsyncSession) -> list[dict]:
    """加载当前生效的 5 级定级阈值（按 level 升序）。

    读取 sys_config['grading_thresholds.current']（写入口径见 grading_config 端点）；
    未配置或解析失败时回退国标默认阈值（90/80/60/40）。
    """
    from app.api.v1.endpoints.grading_config import (
        _KEY_CURRENT,
        DEFAULT_GRADING_THRESHOLDS,
    )

    result = await db.execute(select(SysConfig).where(SysConfig.key == _KEY_CURRENT))
    cfg = result.scalar_one_or_none()
    if cfg and cfg.value:
        try:
            thresholds = json.loads(cfg.value).get("thresholds")
            if isinstance(thresholds, list) and thresholds:
                return sorted(thresholds, key=lambda t: t["level"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("定级阈值解析失败，回退国标默认: %s", exc)
    return list(DEFAULT_GRADING_THRESHOLDS)


def _score_grade_condition(score_col, threshold: dict):
    """单个等级的 score 区间条件.

    区间连续：level 1 取 [minScore, +∞)，其余取 [minScore, maxScore)。
    与前端 getGrade 语义一致：前端按 level 顺序首个命中返回，
    等价于 level 1 下界闭区间、其余等级左闭右开。
    """
    cond = score_col >= threshold["minScore"]
    if threshold["level"] != 1:
        cond = cond & (score_col < threshold["maxScore"])
    return cond


def _grade_case(score_col, thresholds: list[dict]):
    """构建 score → 等级名 的 SQL CASE 表达式（NULL/未命中 → INCONCLUSIVE）。"""
    branches = [(_score_grade_condition(score_col, t), t["name"]) for t in thresholds]
    return case(*branches, else_="INCONCLUSIVE")


def _grade_filter_condition(grade: str, thresholds: list[dict]):
    """构建等级筛选 WHERE 条件。

    grade ∈ EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE（大小写不敏感）；
    INCONCLUSIVE 对应 score IS NULL。未知等级返回 None。
    """
    g = grade.strip().upper()
    if g == "INCONCLUSIVE":
        return KpiSnapshotHourly.score.is_(None)
    for t in thresholds:
        if t["name"] == g:
            return _score_grade_condition(KpiSnapshotHourly.score, t)
    return None


async def _build_snapshot_conditions(
    db: AsyncSession,
    *,
    loop_ids: list[str] | None = None,
    plant_node_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
    confidence_level: str | None = None,
    loop_tag_name: str | None = None,
) -> tuple[list, bool]:
    """构建快照查询的基础 WHERE 条件（快照列表与等级分布共用，保证口径一致）.

    注意：性能等级（grade）筛选不在此处——等级由"每回路最新一条快照"的
    score 派生，必须在 latestOnly 窗口取数之后应用（见 list_loop_snapshots）。

    Returns:
        (conditions, need_loop_join) — need_loop_join 表示子查询是否需要
        join loop_ledger（装置过滤或回路编号模糊搜索时）
    """
    # 默认时间范围：近 30 天（对齐项目 30 天时间窗口约定）
    # 注意：数据库 ts_start 字段为无时区类型，必须使用 naive datetime，
    # 否则 asyncpg 会抛 "can't subtract offset-naive and offset-aware datetimes"
    if start is None:
        start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=30)
    if end is None:
        end = datetime.now(UTC).replace(tzinfo=None)

    # 递归展开 plant_node_ids（包含子节点）
    expanded_node_ids: list[str] | None = None
    if plant_node_ids:
        from app.services.loop import _get_descendant_node_ids

        expanded_node_ids = list(plant_node_ids)
        for nid in plant_node_ids:
            descendants = await _get_descendant_node_ids(db, nid)
            expanded_node_ids.extend(descendants)

    conditions: list = [
        KpiSnapshotHourly.ts_start >= start,
        KpiSnapshotHourly.ts_start <= end,
    ]
    if loop_ids:
        conditions.append(KpiSnapshotHourly.loop_id.in_(loop_ids))
    if expanded_node_ids:
        conditions.append(LoopLedger.unit_id.in_(expanded_node_ids))
    if status_filter:
        conditions.append(KpiSnapshotHourly.status == status_filter)
    if confidence_level:
        conditions.append(KpiSnapshotHourly.confidence_level == confidence_level)
    if loop_tag_name:
        conditions.append(LoopLedger.tag_name.ilike(f"%{loop_tag_name}%"))

    need_loop_join = bool(expanded_node_ids or loop_tag_name)
    return conditions, need_loop_join


async def _build_grade_condition(db: AsyncSession, grade: str):
    """构建性能等级筛选条件（基于当前生效的定级阈值）.

    Returns:
        SQLAlchemy 条件表达式（作用于 KpiSnapshotHourly.score）

    Raises:
        BizError: ERR_INVALID_GRADE（grade 非合法等级名）
    """
    thresholds = await _load_grading_thresholds(db)
    grade_cond = _grade_filter_condition(grade, thresholds)
    if grade_cond is None:
        raise BizError(
            code="ERR_INVALID_GRADE",
            message=(
                f"无效的性能等级: {grade}（可选：EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE）"
            ),
            status_code=400,
        )
    return grade_cond


async def get_grade_distribution(
    db: AsyncSession,
    loop_ids: list[str] | None = None,
    plant_node_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
    confidence_level: str | None = None,
    loop_tag_name: str | None = None,
) -> dict:
    """各性能等级的回路数分布（SQL 聚合，每回路取最新一条快照）.

    与快照列表 latestOnly=True 同口径：同一组筛选条件下，
    各等级计数之和（含 INCONCLUSIVE）== 列表 total。
    等级判定使用 sys_config 当前生效的定级阈值（与前端等级卡片一致），
    score 为 NULL 计入 INCONCLUSIVE。

    Returns:
        {"EXCELLENT": n, "GOOD": n, "FAIR": n, "WARNING": n, "POOR": n,
         "INCONCLUSIVE": n, "total": n,
         "fitnessDistribution": {"L0": n, ..., "L4": n, "total": n}}
    """
    conditions, need_loop_join = await _build_snapshot_conditions(
        db,
        loop_ids=loop_ids,
        plant_node_ids=plant_node_ids,
        start=start,
        end=end,
        status_filter=status_filter,
        confidence_level=confidence_level,
        loop_tag_name=loop_tag_name,
    )
    thresholds = await _load_grading_thresholds(db)

    # 子查询：每个回路最新一条快照（优先非 INCONCLUSIVE，口径同快照列表 latestOnly）
    rn_col = (
        func.row_number()
        .over(
            partition_by=KpiSnapshotHourly.loop_id,
            order_by=[
                case((KpiSnapshotHourly.status != "INCONCLUSIVE", 0), else_=1).asc(),
                KpiSnapshotHourly.ts_start.desc(),
            ],
        )
        .label("rn")
    )
    subq_stmt = select(
        KpiSnapshotHourly.id.label("snap_id"),
        KpiSnapshotHourly.score.label("score"),
        KpiSnapshotHourly.fitness_level.label("fitness_level"),
        rn_col,
    )
    if need_loop_join:
        subq_stmt = subq_stmt.outerjoin(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
    latest_subq = subq_stmt.where(*conditions).subquery()

    grade_col = _grade_case(latest_subq.c.score, thresholds).label("grade")
    stmt = (
        select(grade_col, func.count().label("cnt"))
        .where(latest_subq.c.rn == 1)
        .group_by(grade_col)
    )
    result = await db.execute(stmt)

    distribution: dict[str, int] = dict.fromkeys(GRADE_NAMES, 0)
    distribution["INCONCLUSIVE"] = 0
    total = 0
    for row in result.all():
        # 防御：自定义阈值配置了非国标等级名时并入 INCONCLUSIVE
        key = row.grade if row.grade in distribution else "INCONCLUSIVE"
        distribution[key] += row.cnt
        total += row.cnt
    distribution["total"] = total

    # 适用性分层分布（L0~L4，P2 IA优化；同一"每回路最新快照"口径，
    # 未分层快照不计入各等级，total 为全量回路数）
    fitness_stmt = (
        select(latest_subq.c.fitness_level, func.count().label("cnt"))
        .where(latest_subq.c.rn == 1)
        .group_by(latest_subq.c.fitness_level)
    )
    fitness_rows = (await db.execute(fitness_stmt)).all()
    fitness_distribution: dict[str, int] = dict.fromkeys(("L0", "L1", "L2", "L3", "L4"), 0)
    for row in fitness_rows:
        if row.fitness_level in fitness_distribution:
            fitness_distribution[row.fitness_level] += row.cnt
    fitness_distribution["total"] = total
    distribution["fitnessDistribution"] = fitness_distribution
    return distribution


# loops/snapshots 服务端排序白名单（防 SQL 注入：只允许映射内列，非法值回退默认 ts_start DESC）。
# 指标分析页 M3 联动（2026-08-25）：在 score 基础上扩展 6 个 KPI 列，
# 供回路性能页排序下拉与横切分析场景使用（口径与 get_ranking sort_field_map 对齐）
SNAPSHOT_SORT_COLUMNS = {
    "score": KpiSnapshotHourly.score,
    "accuracy_rate": KpiSnapshotHourly.accuracy_rate,
    "auto_mode_rate": KpiSnapshotHourly.auto_mode_rate,
    "effective_auto_rate": KpiSnapshotHourly.effective_auto_rate,
    "fast_rate": KpiSnapshotHourly.fast_rate,
    "steady_rate": KpiSnapshotHourly.steady_rate,
    "good_value_rate": KpiSnapshotHourly.good_value_rate,
}


async def list_loop_snapshots(
    db: AsyncSession,
    loop_ids: list[str] | None = None,
    plant_node_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
    confidence_level: str | None = None,
    loop_tag_name: str | None = None,
    grade: str | None = None,
    latest_only: bool = True,
    page: int = 1,
    page_size: int = 20,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> tuple[list[tuple[KpiSnapshotHourly, str | None]], int]:
    """查询回路小时指标快照列表（含回路名，分页）.

    Args:
        db: 异步 DB 会话
        loop_ids: 回路 ID 列表过滤；None=不按回路过滤
        plant_node_ids: 装置 ID 列表过滤（LoopLedger.unit_id）；None=不按装置过滤
        start: 起始时间（按 ts_start 过滤）；None=默认近 30 天
        end: 结束时间（按 ts_start 过滤）；None=当前时间
        status_filter: 状态过滤（SUCCESS/INCONCLUSIVE/PARTIAL）
        confidence_level: 可信度等级过滤（A/B/C/D/E）
        loop_tag_name: 回路编号模糊匹配（ILIKE %keyword%）
        grade: 性能等级筛选（EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE），
            按当前定级阈值在服务端过滤；latest_only=True 时按"每回路最新一条"
            快照的 score 判定（与 /grade-distribution 及前端等级卡片口径一致）；
            None=不按等级筛选（向后兼容）
        latest_only: True=每个回路只返回最新一条评估记录（默认）；
            False=返回所有快照（用于历史趋势/诊断历史）
        page: 页码（1-based）
        page_size: 每页条数
        sort_by: 排序字段；None=按时间窗起始 DESC。白名单（M3 扩展，见
            SNAPSHOT_SORT_COLUMNS）：score/accuracy_rate/auto_mode_rate/
            effective_auto_rate/fast_rate/steady_rate/good_value_rate
            （排序时 NULL 恒置末位，次排序 ts_start DESC）
        sort_order: 排序方向（"asc"/"desc"，默认 desc）

    Returns:
        (rows, total) — rows 为 [(KpiSnapshotHourly, tag_name), ...]，
        total 为符合条件的总记录数

    Raises:
        BizError: ERR_INVALID_GRADE（grade 非合法等级名）
    """
    base_conditions, need_loop_join = await _build_snapshot_conditions(
        db,
        loop_ids=loop_ids,
        plant_node_ids=plant_node_ids,
        start=start,
        end=end,
        status_filter=status_filter,
        confidence_level=confidence_level,
        loop_tag_name=loop_tag_name,
    )

    # 等级筛选条件（grade 由最新快照的 score 派生，需在窗口取数后应用）
    grade_cond = None
    if grade:
        grade_cond = await _build_grade_condition(db, grade)

    if latest_only:
        # 使用窗口函数取每个回路最新一条评估记录
        # 优先返回非 INCONCLUSIVE（有实际评估结果）的记录；
        # 如果某回路只有 INCONCLUSIVE 记录，则返回最新的 INCONCLUSIVE。
        # 实现：按 loop_id 分组，先按 status 优先级排序（SUCCESS/PARTIAL 优先于 INCONCLUSIVE），
        # 再按 ts_start DESC，取 rn=1。
        rn_col = (
            func.row_number()
            .over(
                partition_by=KpiSnapshotHourly.loop_id,
                order_by=[
                    # CASE: status != 'INCONCLUSIVE' 排在前面
                    case((KpiSnapshotHourly.status != "INCONCLUSIVE", 0), else_=1).asc(),
                    KpiSnapshotHourly.ts_start.desc(),
                ],
            )
            .label("rn")
        )
        subq_stmt = select(KpiSnapshotHourly.id.label("snap_id"), rn_col)
        if need_loop_join:
            subq_stmt = subq_stmt.outerjoin(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
        subq_stmt = subq_stmt.where(*base_conditions)
        latest_subq = subq_stmt.subquery()

        # 主查询：只取 rn=1 的 snapshot
        # 可选排序：白名单列 / ts_start（默认 ts_start DESC）；指标列排序时 NULL 恒置末位
        sort_col = SNAPSHOT_SORT_COLUMNS.get(sort_by or "")
        if sort_col is not None:
            col_order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
            latest_order = [nulls_last(col_order), KpiSnapshotHourly.ts_start.desc()]
        else:
            latest_order = [KpiSnapshotHourly.ts_start.desc()]
        stmt = (
            select(KpiSnapshotHourly, LoopLedger.tag_name)
            .outerjoin(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .join(latest_subq, KpiSnapshotHourly.id == latest_subq.c.snap_id)
            .where(latest_subq.c.rn == 1)
        )
        # 等级筛选作用于"每回路最新一条"的 score（与前端等级卡片口径一致）：
        # 先窗口取最新，再按等级过滤，total 与 /grade-distribution 对应桶一致
        if grade_cond is not None:
            stmt = stmt.where(grade_cond)
        stmt = stmt.order_by(*latest_order)

        if grade_cond is not None:
            # count：对筛选后的最新快照集合计数（口径与列表一致）
            count_stmt = select(func.count()).select_from(
                stmt.with_only_columns(KpiSnapshotHourly.id).order_by(None).subquery()
            )
        else:
            # count：每个回路算一条，按 distinct loop_id 计数
            count_stmt = (
                select(func.count(func.distinct(KpiSnapshotHourly.loop_id)))
                .outerjoin(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
                .where(*base_conditions)
            )
    else:
        # 全量时间序列（历史趋势/诊断历史用）：等级筛选按行应用
        if grade_cond is not None:
            base_conditions.append(grade_cond)
        # 可选排序：白名单列 / ts_start（默认 ts_start DESC）；指标列排序时 NULL 恒置末位
        sort_col = SNAPSHOT_SORT_COLUMNS.get(sort_by or "")
        if sort_col is not None:
            col_order = sort_col.asc() if sort_order == "asc" else sort_col.desc()
            order_clause = [nulls_last(col_order), KpiSnapshotHourly.ts_start.desc()]
        else:
            order_clause = [KpiSnapshotHourly.ts_start.desc()]
        stmt = (
            select(KpiSnapshotHourly, LoopLedger.tag_name)
            .outerjoin(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(*base_conditions)
            .order_by(*order_clause)
        )
        count_stmt = (
            select(func.count())
            .select_from(KpiSnapshotHourly)
            .outerjoin(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(*base_conditions)
        )

    # 分页
    if page > 0 and page_size > 0:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    # 执行查询
    list_result = await db.execute(stmt)
    rows = list(list_result.all())

    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    return rows, total


__all__ = [
    "ALGORITHM_VERSION",
    "DASHBOARD_CACHE_TTL",
    "GRADE_NAMES",
    "KPI_METRIC_CODES",
    "KPI_NAME_MAP",
    "SNAPSHOT_SORT_COLUMNS",
    "export_analytics_csv",
    "get_analytics",
    "get_board",
    "get_grade_distribution",
    "get_ranking",
    "list_engine_rules",
    "list_loop_snapshots",
    "list_metric_configs",
    "update_engine_rule",
    "update_metric_config",
]
