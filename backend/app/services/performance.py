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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.redis import redis_client
from app.models.audit import SysAuditLog
from app.models.engine import EngineRule
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly, MetricConfig
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

# 6 大 KPI metric_code 列表（固定顺序）
KPI_METRIC_CODES = (
    "good_value_rate",
    "auto_mode_rate",
    "steady_rate",
    "accuracy_rate",
    "oscillation_rate",
    "saturation_rate",
)

# KPI 中文名映射
KPI_NAME_MAP = {
    "good_value_rate": "好值率",
    "auto_mode_rate": "自控率",
    "steady_rate": "平稳率",
    "accuracy_rate": "准确率",
    "oscillation_rate": "振荡率",
    "saturation_rate": "饱和率",
    "composite_score": "综合评分",
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
        operated_at=datetime.utcnow(),
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
    result = await db.execute(
        select(MetricConfig).order_by(MetricConfig.metric_code.asc())
    )
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
    config.updated_at = datetime.utcnow()
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
    rule.updated_at = datetime.utcnow()

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
    now = datetime.now(UTC)
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

    # 查询时间窗内的快照数据
    snapshots = await _query_snapshots(
        db=db,
        plant_node_id=plant_node_id,
        start=start,
        end=now,
    )

    # 聚合 KPI 卡片
    kpi_cards = _aggregate_kpi_cards(snapshots)
    kpi_summary = _aggregate_kpi_summary(snapshots)

    # 平稳率趋势（按小时聚合）
    steady_trend = _aggregate_steady_trend(snapshots)

    # 部分数据警告
    inconclusive_count = sum(1 for s in snapshots if s.status == "INCONCLUSIVE")
    partial_count = sum(1 for s in snapshots if s.status == "PARTIAL")
    partial_warning = {
        "active": inconclusive_count > 0 or partial_count > 0,
        "inconclusiveCount": inconclusive_count,
        "partialCount": partial_count,
        "message": (
            f"存在 {inconclusive_count} 个不确定结果、{partial_count} 个部分结果"
            if inconclusive_count > 0 or partial_count > 0
            else None
        ),
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
    now = datetime.now(UTC)
    if time_window == "today":
        start = now - timedelta(hours=24)
    elif time_window == "yesterday":
        start = now - timedelta(days=2)
        now = now - timedelta(days=1)
    else:
        delta = TIME_WINDOWS.get(time_window, timedelta(days=1))
        start = now - delta

    # 查询快照（仅 SUCCESS 状态，INCONCLUSIVE 不参与排行）
    snapshots = await _query_snapshots(
        db=db,
        plant_node_id=plant_node_id,
        start=start,
        end=now,
        status_filter="SUCCESS",
    )

    # 按回路聚合（取最新一条快照）
    loop_latest: dict[str, KpiSnapshotHourly] = {}
    for snap in snapshots:
        loop_id = str(snap.loop_id) if snap.loop_id else ""
        if not loop_id:
            continue
        if loop_id not in loop_latest or snap.ts_start > loop_latest[loop_id].ts_start:
            loop_latest[loop_id] = snap

    # 查询回路基础信息
    loop_ids = list(loop_latest.keys())
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

    # 构建排行项
    items: list[dict] = []
    for loop_id, snap in loop_latest.items():
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
                "steadyRate": _to_float(snap.steady_rate),
                "accuracyRate": _to_float(snap.accuracy_rate),
                "oscillationRate": _to_float(snap.oscillation_rate),
                "saturationRate": _to_float(snap.saturation_rate),
                "status": _score_to_status(snap.score),
                "algorithmVersion": ALGORITHM_VERSION,
                "preDiagnosis": diagnosis_map.get(loop_id),
                "actionStatus": action_status_map.get(loop_id, "PENDING"),
            }
        )

    # 排序
    sort_field_map = {
        "score": "compositeScore",
        "steady_rate": "steadyRate",
        "good_value_rate": "goodValueRate",
    }
    sort_field = sort_field_map.get(sort_by, "compositeScore")
    reverse = sort_order.lower() == "desc"
    items.sort(
        key=lambda x: (x.get(sort_field) is None, x.get(sort_field) or 0),
        reverse=reverse,
    )

    # 截断并打排名
    items = items[:limit]
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

    # KPI 趋势
    kpi_trend = _aggregate_kpi_trend(
        snapshots=snapshots,
        metric_key=metric_key,
        granularity=granularity,
        start=start_dt,
        end=end_dt,
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


async def _query_snapshots(
    db: AsyncSession,
    plant_node_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
) -> list[KpiSnapshotHourly]:
    """查询快照数据，可选按装置/时间/状态过滤。"""
    stmt = select(KpiSnapshotHourly)
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
    stmt = stmt.order_by(KpiSnapshotHourly.ts_start.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _score_to_status(score: Decimal | float | None) -> str:
    """综合评分 → 状态枚举。"""
    if score is None:
        return "INCONCLUSIVE"
    s = float(score)
    if s >= 80:
        return "GOOD"
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


def _aggregate_kpi_cards(snapshots: list[KpiSnapshotHourly]) -> list[dict]:
    """聚合 KPI 卡片（6 大 KPI + 综合评分 = 7 张卡片）。"""
    if not snapshots:
        return _empty_kpi_cards()

    # 仅 SUCCESS 状态参与聚合
    valid = [s for s in snapshots if s.status == "SUCCESS"]
    if not valid:
        return _empty_kpi_cards()

    def avg(field: str) -> float | None:
        vals = [getattr(s, field) for s in valid if getattr(s, field) is not None]
        if not vals:
            return None
        return float(sum(vals) / len(vals))

    cards: list[dict] = []
    for code in KPI_METRIC_CODES:
        val = avg(code)
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
    score_avg = avg("score")
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
        "steady_rate": {"min": 0, "max": 100, "alert": 85},
        "accuracy_rate": {"min": 0, "max": 100, "alert": 80},
        "oscillation_rate": {"min": 0, "max": 100, "alert": 20},
        "saturation_rate": {"min": 0, "max": 100, "alert": 15},
    }
    return defaults.get(metric_code, {})


def _aggregate_kpi_summary(snapshots: list[KpiSnapshotHourly]) -> dict:
    """聚合 KPI 汇总。"""
    valid = [s for s in snapshots if s.status == "SUCCESS"]
    if not valid:
        return {
            "good_value_rate": None,
            "auto_mode_rate": None,
            "steady_rate": None,
            "accuracy_rate": None,
            "oscillation_rate": None,
            "saturation_rate": None,
            "composite_score": None,
            "status": "INCONCLUSIVE",
            "algorithm_version": ALGORITHM_VERSION,
        }

    def avg(field: str) -> float | None:
        vals = [getattr(s, field) for s in valid if getattr(s, field) is not None]
        if not vals:
            return None
        return round(float(sum(vals) / len(vals)), 2)

    score_avg = avg("score")
    return {
        "good_value_rate": avg("good_value_rate"),
        "auto_mode_rate": avg("auto_mode_rate"),
        "steady_rate": avg("steady_rate"),
        "accuracy_rate": avg("accuracy_rate"),
        "oscillation_rate": avg("oscillation_rate"),
        "saturation_rate": avg("saturation_rate"),
        "composite_score": score_avg,
        "status": _score_to_status(score_avg),
        "algorithm_version": ALGORITHM_VERSION,
    }


def _aggregate_steady_trend(snapshots: list[KpiSnapshotHourly]) -> dict:
    """聚合平稳率趋势（按小时聚合）。"""
    if not snapshots:
        return {"timestamps": [], "values": []}

    # 按小时分组
    hourly: dict[str, list[float]] = {}
    for s in snapshots:
        if s.steady_rate is None:
            continue
        hour_key = s.ts_start.strftime("%Y-%m-%dT%H:00:00")
        hourly.setdefault(hour_key, []).append(float(s.steady_rate))

    timestamps = sorted(hourly.keys())
    values = [round(sum(hourly[k]) / len(hourly[k]), 2) for k in timestamps]
    return {"timestamps": timestamps, "values": values}


def _aggregate_kpi_trend(
    snapshots: list[KpiSnapshotHourly],
    metric_key: str,
    granularity: str,
    start: datetime,
    end: datetime,
) -> dict:
    """聚合 KPI 趋势（按粒度分组）。"""
    if not snapshots:
        return {"timestamps": [], "series": []}

    field_map = {
        "score": "score",
        "good_value_rate": "good_value_rate",
        "auto_mode_rate": "auto_mode_rate",
        "steady_rate": "steady_rate",
        "accuracy_rate": "accuracy_rate",
        "oscillation_rate": "oscillation_rate",
        "saturation_rate": "saturation_rate",
    }
    field = field_map.get(metric_key, "score")
    metric_name = KPI_NAME_MAP.get(metric_key, metric_key)

    def bucket_key(ts: datetime) -> str:
        if granularity == "hour":
            return ts.strftime("%Y-%m-%dT%H:00:00")
        if granularity == "day":
            return ts.strftime("%Y-%m-%d")
        if granularity == "week":
            # ISO 周一为起始
            monday = ts - timedelta(days=ts.weekday())
            return monday.strftime("%Y-%m-%d")
        if granularity == "month":
            return ts.strftime("%Y-%m")
        return ts.strftime("%Y-%m-%d")

    grouped: dict[str, list[float]] = {}
    for s in snapshots:
        val = getattr(s, field, None)
        if val is None:
            continue
        key = bucket_key(s.ts_start)
        grouped.setdefault(key, []).append(float(val))

    timestamps = sorted(grouped.keys())
    values = [round(sum(grouped[k]) / len(grouped[k]), 2) for k in timestamps]
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
            label_count[tracker.diagnosis_label] = (
                label_count.get(tracker.diagnosis_label, 0) + 1
            )

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
