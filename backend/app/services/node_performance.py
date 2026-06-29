"""Node-level performance evaluation service (GB/T 44693.2-2024 §6.4).

按 plant_node 递归收集下属回路，以 score_weight 加权聚合回路级快照，
支持企业级/装置级/单元级 KPI 持久化与查询。

核心功能：
- 递归收集节点下属回路（PostgreSQL CTE）
- 加权聚合回路级快照（score_weight 加权均值）
- 节点级快照读写（幂等：相同 plant_node_id + ts_start 不重复写入）
- 查询服务（最新快照、历史趋势、节点间排名、全厂总览）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Integer, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.loop import LoopLedger
from app.models.loop_config import LoopLevelWeight
from app.models.metric import KpiSnapshotHourly
from app.models.node_kpi import (
    KpiNodeSnapshotDaily,
    KpiNodeSnapshotHourly,
    KpiNodeSnapshotMonthly,
)
from app.models.plant_node import PlantNode
from app.services.performance import ALGORITHM_VERSION, KPI_NAME_MAP, _score_to_status

logger = logging.getLogger(__name__)

# 参与加权聚合的 KPI 字段（与回路级快照对齐）
KPI_FIELDS = (
    "good_value_rate",
    "auto_mode_rate",
    "effective_auto_rate",
    "steady_rate",
    "accuracy_rate",
    "fast_response_rate",
    "oscillation_rate",
    "saturation_rate",
    "score",
)


# ---------------------------------------------------------------------------
# 递归收集节点下属回路
# ---------------------------------------------------------------------------


async def collect_descendant_loop_ids(
    db: AsyncSession,
    plant_node_id: str,
) -> list[str]:
    """递归收集指定节点及其所有子孙节点下挂载的回路 ID。

    使用 PostgreSQL 递归 CTE 遍历 plant_node 树，再 JOIN loop_ledger.unit_id。

    Returns:
        回路 ID 列表（仅 is_active=True 的回路）
    """
    # 递归 CTE：从 plant_node_id 出发，向下遍历所有子节点
    cte_sql = text("""
        WITH RECURSIVE node_tree AS (
            SELECT id FROM plant_node WHERE id = :node_id
            UNION ALL
            SELECT child.id FROM plant_node child
            JOIN node_tree ON child.parent_id = node_tree.id
        )
        SELECT l.id AS loop_id
        FROM loop_ledger l
        WHERE l.unit_id IN (SELECT id FROM node_tree)
          AND l.is_active = TRUE
    """)
    result = await db.execute(cte_sql, {"node_id": plant_node_id})
    return [str(row.loop_id) for row in result.all()]


# ---------------------------------------------------------------------------
# 实时自控率查询
# ---------------------------------------------------------------------------


async def query_realtime_auto_rate(
    db: AsyncSession,
    loop_ids: list[str],
) -> dict | None:
    """查询当前时刻处于自动模式的回路占比（实时自控率）。

    从 TDengine 查询每个回路的最新 MODE 值，
    根据该回路的投用定义（loop_mode_mapping）判断是否算自动模式。
    无投用定义的回路回退到默认 {1, 2, 3}（向后兼容）。

    Args:
        db: 异步数据库会话
        loop_ids: 回路 ID 列表

    Returns:
        实时自控率统计结果 dict：
        - rate: 自控率百分比（Decimal）
        - auto_count: 自动模式回路数
        - manual_count: 手动模式回路数
        - total_count: 有效回路总数
        - read_at: 统计时间（ISO 字符串）
        TDengine 不可用或无数据时返回 None
    """
    if not loop_ids:
        return None

    from app.core.tdengine import query_trend_data
    from app.models.loop import LoopTagMapping
    from app.models.loop_config import LoopModeMapping
    from app.models.tag import TagRegistry

    # --- 1. 批量查询投用定义，构建 {loop_id: set(auto_mode_values)} ---
    mm_result = await db.execute(
        select(LoopModeMapping.loop_id, LoopModeMapping.mode_value).where(
            LoopModeMapping.loop_id.in_(loop_ids),
            LoopModeMapping.is_auto.is_(True),
        )
    )
    auto_mode_map: dict[str, set[int]] = {}
    for row in mm_result.all():
        auto_mode_map.setdefault(row.loop_id, set()).add(row.mode_value)

    # --- 2. 查询每个回路的 MODE tag 映射 ---
    result = await db.execute(
        select(LoopTagMapping.loop_id, TagRegistry.tag_name)
        .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
        .where(
            LoopTagMapping.loop_id.in_(loop_ids),
            LoopTagMapping.tag_role == "MODE",
        )
    )
    rows = result.all()

    if not rows:
        logger.debug("[实时自控率] 无 MODE tag 映射，跳过")
        return None

    # --- 3. 查询时间窗：最近 5 分钟 ---
    now = datetime.now(UTC)
    end_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_time = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- 4. 并发查询所有回路的最新 MODE 值 ---
    async def _get_latest_mode(tag_name: str) -> int | None:
        try:
            data = await query_trend_data(tag_name, start_time, end_time)
            if data:
                return int(data[-1]["value"])
        except Exception as exc:  # noqa: BLE001
            logger.debug("[实时自控率] 查询 %s 失败: %s", tag_name, exc)
        return None

    tasks = [_get_latest_mode(row.tag_name) for row in rows]
    mode_values = await asyncio.gather(*tasks)

    # --- 5. 按回路投用定义判断是否算自动 ---
    DEFAULT_AUTO_MODES = {1, 2, 3}  # 向后兼容默认值
    auto_count = 0
    valid_count = 0

    for row, mode_val in zip(rows, mode_values, strict=False):
        if mode_val is None:
            continue
        valid_count += 1
        # 取该回路的自动 MODE 集合，无配置时回退到默认
        auto_modes = auto_mode_map.get(row.loop_id, DEFAULT_AUTO_MODES)
        if mode_val in auto_modes:
            auto_count += 1

    if valid_count == 0:
        logger.debug("[实时自控率] TDengine 无可用 MODE 数据")
        return None

    rate = round(auto_count / valid_count * 100, 2)
    logger.debug(
        "[实时自控率] 有效回路=%d, 自动模式=%d, 实时自控率=%.2f%%",
        valid_count,
        auto_count,
        rate,
    )
    return {
        "rate": Decimal(str(rate)),
        "auto_count": auto_count,
        "manual_count": valid_count - auto_count,
        "total_count": valid_count,
        "read_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# 节点级快照聚合与持久化
# ---------------------------------------------------------------------------


async def aggregate_node_snapshot(
    db: AsyncSession,
    plant_node_id: str,
    ts_start: datetime,
    ts_end: datetime,
) -> dict | None:
    """聚合指定节点在时间窗内的回路级快照，生成节点级快照。

    聚合规则（对齐 GB/T 44693.2-2024 §6.4 + 附表2）：
    - 递归收集节点下属所有 active 回路
    - 取每个回路在该时间窗内最新一条 SUCCESS 快照
    - 按回路级别加权平均（level=1→3.0, level=2→2.0, level=3→1.0）
    - 投自动回路占比 = auto_mode_rate > 0 的回路数 / 总回路数

    Returns:
        节点级快照字典，若无数据返回 None
    """
    loop_ids = await collect_descendant_loop_ids(db, plant_node_id)
    if not loop_ids:
        logger.debug("[节点级聚合] plant_node_id=%s 无下属回路", plant_node_id)
        return None

    # 子查询：每个回路在时间窗内最新一条 SUCCESS 快照
    # 使用 DISTINCT ON (loop_id) 取每组最新
    subq = (
        select(KpiSnapshotHourly)
        .where(
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= ts_start,
            KpiSnapshotHourly.ts_start <= ts_end,
            KpiSnapshotHourly.status == "SUCCESS",
        )
        .distinct(KpiSnapshotHourly.loop_id)
        .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_start.desc())
    ).subquery()

    # 外层 JOIN loop_ledger + loop_level_weight，按回路级别加权聚合（v2，对齐附表2）
    # level=NULL 时 OUTER JOIN 不匹配，COALESCE 到 1.0（等同 level=3）
    weight_col = func.coalesce(LoopLevelWeight.weight, Decimal("1.0")).label("w")
    weight_sum_col = func.nullif(func.sum(weight_col), 0).label("weight_sum")

    # 加权聚合列（必须引用子查询 subq.c，而非原表 KpiSnapshotHourly，避免笛卡尔积）
    weighted_cols = []
    for f in KPI_FIELDS:
        col = getattr(subq.c, f)
        weighted_cols.append((func.sum(col * weight_col) / weight_sum_col).label(f))

    # 投自动回路占比
    auto_loop_count = func.sum(
        func.coalesce(
            func.cast(subq.c.auto_mode_rate > 0, Integer),
            0,
        )
    ).label("auto_loop_count")
    total_count = func.count().label("cnt")

    stmt = select(total_count, auto_loop_count, weight_sum_col, *weighted_cols).select_from(
        subq.join(LoopLedger, subq.c.loop_id == LoopLedger.id).outerjoin(
            LoopLevelWeight, LoopLedger.level == LoopLevelWeight.level
        )
    )
    result = await db.execute(stmt)
    row = result.one()

    if row.cnt == 0:
        logger.debug(
            "[节点级聚合] plant_node_id=%s, 时间窗 %s~%s 无 SUCCESS 快照",
            plant_node_id,
            ts_start,
            ts_end,
        )
        return None

    weight_sum_val = float(row.weight_sum) if row.weight_sum is not None else 0.0
    if weight_sum_val == 0:
        logger.warning(
            "[节点级聚合] plant_node_id=%s, SUM(weight)=0，无法计算加权平均",
            plant_node_id,
        )
        return None

    def avg_value(field: str) -> Decimal | None:
        val = getattr(row, field)
        if val is None:
            return None
        return Decimal(str(val)).quantize(Decimal("0.01"))

    score_avg = avg_value("score")
    auto_loop_count_val = int(row.auto_loop_count or 0)
    auto_loop_ratio = round(auto_loop_count_val / int(row.cnt) * 100, 2)

    status = _score_to_status(score_avg)

    # 查询实时自控率（TDengine 不可用时返回 None，不影响聚合流程）
    _realtime_result = await query_realtime_auto_rate(db, loop_ids)
    realtime_auto_rate = _realtime_result["rate"] if _realtime_result else None

    logger.info(
        "[节点级聚合] plant_node_id=%s, 回路数=%d, 投自动回路数=%d, "
        "投自动占比=%.2f%%, 实时自控率=%s, 加权综合评分=%s, 定级=%s",
        plant_node_id,
        row.cnt,
        auto_loop_count_val,
        auto_loop_ratio,
        realtime_auto_rate,
        score_avg,
        status,
    )

    return {
        "plant_node_id": plant_node_id,
        "ts_start": ts_start,
        "ts_end": ts_end,
        "score": score_avg,
        "good_value_rate": avg_value("good_value_rate"),
        "auto_mode_rate": avg_value("auto_mode_rate"),
        "effective_auto_rate": avg_value("effective_auto_rate"),
        "steady_rate": avg_value("steady_rate"),
        "accuracy_rate": avg_value("accuracy_rate"),
        "fast_response_rate": avg_value("fast_response_rate"),
        "oscillation_rate": avg_value("oscillation_rate"),
        "saturation_rate": avg_value("saturation_rate"),
        "auto_loop_ratio": Decimal(str(auto_loop_ratio)).quantize(Decimal("0.01")),
        "realtime_auto_rate": realtime_auto_rate,
        "loop_count": int(row.cnt),
        "status": status,
        "algorithm_version": ALGORITHM_VERSION,
    }


async def save_node_snapshot(db: AsyncSession, snap_data: dict) -> dict:
    """保存节点级快照（幂等：相同 plant_node_id + ts_start 覆盖更新）。

    Args:
        db: 异步数据库会话
        snap_data: aggregate_node_snapshot 返回的快照字典

    Returns:
        保存后的快照字典
    """
    plant_node_id = snap_data["plant_node_id"]
    ts_start = snap_data["ts_start"]

    # 查询是否已存在（幂等）
    existing_result = await db.execute(
        select(KpiNodeSnapshotHourly).where(
            KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
            KpiNodeSnapshotHourly.ts_start == ts_start,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        # 覆盖更新
        for key, val in snap_data.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        existing.created_at = datetime.now(UTC).replace(tzinfo=None)
        await db.flush()
        logger.debug("[节点级快照] 覆盖更新 plant_node_id=%s, ts_start=%s", plant_node_id, ts_start)
    else:
        # 新增
        snap = KpiNodeSnapshotHourly(
            id=str(uuid4()),
            **snap_data,
        )
        db.add(snap)
        await db.flush()
        logger.debug("[节点级快照] 新增 plant_node_id=%s, ts_start=%s", plant_node_id, ts_start)

    return snap_data


async def calculate_and_save_node_snapshot(
    db: AsyncSession,
    plant_node_id: str,
    ts_start: datetime,
    ts_end: datetime,
) -> dict | None:
    """聚合并保存节点级快照（一步完成）。"""
    # 剥离 tzinfo，对齐 KpiNodeSnapshotHourly 表的 TIMESTAMP WITHOUT TIME ZONE 列
    ts_start = ts_start.replace(tzinfo=None) if ts_start.tzinfo else ts_start
    ts_end = ts_end.replace(tzinfo=None) if ts_end.tzinfo else ts_end
    snap_data = await aggregate_node_snapshot(db, plant_node_id, ts_start, ts_end)
    if snap_data is None:
        return None
    return await save_node_snapshot(db, snap_data)


# ---------------------------------------------------------------------------
# 查询服务
# ---------------------------------------------------------------------------


def _snapshot_to_dict(snap: KpiNodeSnapshotHourly, node_name: str | None = None) -> dict:
    """快照对象转字典。"""

    def to_float(v):
        return float(v) if v is not None else None

    return {
        "plantNodeId": str(snap.plant_node_id),
        "plantNodeName": node_name,
        "tsStart": snap.ts_start.isoformat() if snap.ts_start else None,
        "tsEnd": snap.ts_end.isoformat() if snap.ts_end else None,
        "score": to_float(snap.score),
        "goodValueRate": to_float(snap.good_value_rate),
        "autoModeRate": to_float(snap.auto_mode_rate),
        "effectiveAutoRate": to_float(snap.effective_auto_rate),
        "steadyRate": to_float(snap.steady_rate),
        "accuracyRate": to_float(snap.accuracy_rate),
        "fastResponseRate": to_float(snap.fast_response_rate),
        "oscillationRate": to_float(snap.oscillation_rate),
        "saturationRate": to_float(snap.saturation_rate),
        "autoLoopRatio": to_float(snap.auto_loop_ratio),
        "realtimeAutoRate": to_float(snap.realtime_auto_rate),
        "loopCount": snap.loop_count,
        "status": snap.status,
        "algorithmVersion": snap.algorithm_version,
    }


async def get_node_latest_snapshot(
    db: AsyncSession,
    plant_node_id: str,
) -> dict | None:
    """获取节点最新一条快照。"""
    result = await db.execute(
        select(KpiNodeSnapshotHourly)
        .where(KpiNodeSnapshotHourly.plant_node_id == plant_node_id)
        .order_by(KpiNodeSnapshotHourly.ts_start.desc())
        .limit(1)
    )
    snap = result.scalar_one_or_none()
    if snap is None:
        return None

    # 查节点名
    node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
    node = node_result.scalar_one_or_none()
    node_name = node.name if node else None

    return _snapshot_to_dict(snap, node_name)


async def get_node_trend(
    db: AsyncSession,
    plant_node_id: str,
    start: datetime,
    end: datetime,
) -> dict:
    """获取节点历史趋势。"""
    result = await db.execute(
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
        )
        .order_by(KpiNodeSnapshotHourly.ts_start.asc())
    )
    snaps = result.scalars().all()

    # 查节点名
    node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
    node = node_result.scalar_one_or_none()
    node_name = node.name if node else None

    timestamps = [s.ts_start.isoformat() for s in snaps]

    def series(field: str, name: str) -> dict:
        return {
            "metricKey": field,
            "metricName": name,
            "values": [
                float(getattr(s, field)) if getattr(s, field) is not None else None for s in snaps
            ],
        }

    return {
        "plantNodeId": plant_node_id,
        "plantNodeName": node_name,
        "timestamps": timestamps,
        "series": [
            series("score", KPI_NAME_MAP.get("composite_score", "综合评分")),
            series("auto_loop_ratio", KPI_NAME_MAP.get("auto_loop_ratio", "投自动回路占比")),
            series("realtime_auto_rate", "实时自控率"),
            series("steady_rate", KPI_NAME_MAP.get("steady_rate", "平稳率")),
            series("effective_auto_rate", KPI_NAME_MAP.get("effective_auto_rate", "有效自控率")),
        ],
    }


async def get_node_ranking(
    db: AsyncSession,
    start: datetime,
    end: datetime,
    node_type: str | None = None,
    sort_by: str = "score",
    sort_order: str = "desc",
    limit: int = 50,
) -> list[dict]:
    """获取节点间性能排名。

    Args:
        node_type: 筛选节点类型（FACTORY/UNIT/EQUIPMENT），None 表示所有启用节点
        sort_by: 排序字段 score/steady_rate/auto_loop_ratio
        sort_order: asc/desc（默认 desc，分数最高的在前）
    """
    # 子查询：每个节点在时间窗内最新一条快照
    base = (
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
        )
        .distinct(KpiNodeSnapshotHourly.plant_node_id)
        .order_by(KpiNodeSnapshotHourly.plant_node_id, KpiNodeSnapshotHourly.ts_start.desc())
    ).subquery()

    # JOIN plant_node 过滤类型
    stmt = select(base, PlantNode.name, PlantNode.type).join(
        PlantNode, base.c.plant_node_id == PlantNode.id
    )

    if node_type:
        stmt = stmt.where(PlantNode.type == node_type)

    # 排序
    sort_field_map = {
        "score": base.c.score,
        "steady_rate": base.c.steady_rate,
        "auto_loop_ratio": base.c.auto_loop_ratio,
        "effective_auto_rate": base.c.effective_auto_rate,
        "realtime_auto_rate": base.c.realtime_auto_rate,
    }
    sort_col = sort_field_map.get(sort_by, base.c.score)
    if sort_order.lower() == "asc":
        stmt = stmt.order_by(sort_col.asc().nulls_last())
    else:
        stmt = stmt.order_by(sort_col.desc().nulls_last())

    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for idx, row in enumerate(rows, start=1):

        def to_float(v):
            return float(v) if v is not None else None

        items.append(
            {
                "rank": idx,
                "plantNodeId": str(row.plant_node_id),
                "plantNodeName": row.name,
                "plantNodeType": row.type,
                "tsStart": row.ts_start.isoformat() if row.ts_start else None,
                "score": to_float(row.score),
                "goodValueRate": to_float(row.good_value_rate),
                "autoModeRate": to_float(row.auto_mode_rate),
                "effectiveAutoRate": to_float(row.effective_auto_rate),
                "steadyRate": to_float(row.steady_rate),
                "accuracyRate": to_float(row.accuracy_rate),
                "fastResponseRate": to_float(row.fast_response_rate),
                "oscillationRate": to_float(row.oscillation_rate),
                "saturationRate": to_float(row.saturation_rate),
                "autoLoopRatio": to_float(row.auto_loop_ratio),
                "realtimeAutoRate": to_float(row.realtime_auto_rate),
                "loopCount": row.loop_count,
                "status": row.status,
                "algorithmVersion": row.algorithm_version,
            }
        )
    return items


async def get_nodes_overview(
    db: AsyncSession,
    start: datetime,
    end: datetime,
) -> dict:
    """全厂总览：所有启用 KPI 评估的节点最新快照汇总。"""
    # 查询所有 is_kpi_enabled 的节点
    node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
    enabled_nodes = node_result.scalars().all()

    if not enabled_nodes:
        return {
            "totalNodes": 0,
            "nodes": [],
            "statusDistribution": {},
        }

    node_ids = [str(n.id) for n in enabled_nodes]
    node_name_map = {str(n.id): n.name for n in enabled_nodes}
    node_type_map = {str(n.id): n.type for n in enabled_nodes}

    # 子查询：每个节点最新快照
    base = (
        select(KpiNodeSnapshotHourly)
        .where(
            KpiNodeSnapshotHourly.ts_start >= start,
            KpiNodeSnapshotHourly.ts_start <= end,
            KpiNodeSnapshotHourly.plant_node_id.in_(node_ids),
        )
        .distinct(KpiNodeSnapshotHourly.plant_node_id)
        .order_by(KpiNodeSnapshotHourly.plant_node_id, KpiNodeSnapshotHourly.ts_start.desc())
    ).subquery()

    stmt = select(base)
    result = await db.execute(stmt)
    rows = result.all()

    nodes = []
    status_dist: dict[str, int] = {}
    for row in rows:
        nid = str(row.plant_node_id)

        def to_float(v):
            return float(v) if v is not None else None

        status = row.status
        status_dist[status] = status_dist.get(status, 0) + 1

        nodes.append(
            {
                "plantNodeId": nid,
                "plantNodeName": node_name_map.get(nid),
                "plantNodeType": node_type_map.get(nid),
                "score": to_float(row.score),
                "autoLoopRatio": to_float(row.auto_loop_ratio),
                "realtimeAutoRate": to_float(row.realtime_auto_rate),
                "steadyRate": to_float(row.steady_rate),
                "effectiveAutoRate": to_float(row.effective_auto_rate),
                "loopCount": row.loop_count,
                "status": status,
                "tsStart": row.ts_start.isoformat() if row.ts_start else None,
            }
        )

    # 按评分降序
    nodes.sort(key=lambda x: -(x["score"] or 0))

    return {
        "totalNodes": len(enabled_nodes),
        "nodesWithSnapshot": len(nodes),
        "nodes": nodes,
        "statusDistribution": status_dist,
    }


# ---------------------------------------------------------------------------
# 多维度监控查询（hour / day / month）
# ---------------------------------------------------------------------------


def _monitor_snapshot_to_dict(snap, dimension: str, node_name: str | None = None) -> dict:
    """监控快照对象转字典（兼容 hour/day/month 三种维度）。"""

    def to_float(v):
        return float(v) if v is not None else None

    # 时间字段：hour 用 ts_start/ts_end，day 用 stat_date，month 用 stat_month
    if dimension == "hour":
        time_label = snap.ts_start.isoformat() if snap.ts_start else None
        time_end = snap.ts_end.isoformat() if snap.ts_end else None
    elif dimension == "day":
        time_label = snap.stat_date.isoformat() if snap.stat_date else None
        time_end = None
    else:  # month
        time_label = snap.stat_month.isoformat() if snap.stat_month else None
        time_end = None

    return {
        "plantNodeId": str(snap.plant_node_id),
        "plantNodeName": node_name,
        "dimension": dimension,
        "tsStart": time_label,
        "tsEnd": time_end,
        "score": to_float(snap.score),
        "goodValueRate": to_float(snap.good_value_rate),
        "autoModeRate": to_float(snap.auto_mode_rate),
        "effectiveAutoRate": to_float(snap.effective_auto_rate),
        "steadyRate": to_float(snap.steady_rate),
        "accuracyRate": to_float(snap.accuracy_rate),
        "fastResponseRate": to_float(snap.fast_response_rate),
        "oscillationRate": to_float(snap.oscillation_rate),
        "saturationRate": to_float(snap.saturation_rate),
        "autoLoopRatio": to_float(snap.auto_loop_ratio),
        "realtimeAutoRate": to_float(snap.realtime_auto_rate),
        "loopCount": snap.loop_count,
        "status": snap.status,
        "algorithmVersion": snap.algorithm_version,
    }


async def get_node_monitor_data(
    db: AsyncSession,
    plant_node_id: str,
    dimension: str,
    start: datetime,
    end: datetime,
) -> dict:
    """获取节点多维度监控数据（hour/day/month）。

    Args:
        db: 异步数据库会话
        plant_node_id: 工厂节点 ID
        dimension: 维度 hour/day/month
        start: 起始时间（datetime，day/month 维度会取 .date()）
        end: 结束时间（datetime，day/month 维度会取 .date()）

    Returns:
        {plantNodeId, plantNodeName, dimension, start, end, snapshots: [...]}
    """
    # 查节点名
    node_result = await db.execute(select(PlantNode).where(PlantNode.id == plant_node_id))
    node = node_result.scalar_one_or_none()
    node_name = node.name if node else None

    if dimension == "hour":
        stmt = (
            select(KpiNodeSnapshotHourly)
            .where(
                KpiNodeSnapshotHourly.plant_node_id == plant_node_id,
                KpiNodeSnapshotHourly.ts_start >= start,
                KpiNodeSnapshotHourly.ts_start <= end,
            )
            .order_by(KpiNodeSnapshotHourly.ts_start.asc())
        )
    elif dimension == "day":
        start_date = start.date() if isinstance(start, datetime) else start
        end_date = end.date() if isinstance(end, datetime) else end
        stmt = (
            select(KpiNodeSnapshotDaily)
            .where(
                KpiNodeSnapshotDaily.plant_node_id == plant_node_id,
                KpiNodeSnapshotDaily.stat_date >= start_date,
                KpiNodeSnapshotDaily.stat_date <= end_date,
            )
            .order_by(KpiNodeSnapshotDaily.stat_date.asc())
        )
    elif dimension == "month":
        start_month = (
            start.date().replace(day=1) if isinstance(start, datetime) else start.replace(day=1)
        )
        end_month = end.date().replace(day=1) if isinstance(end, datetime) else end.replace(day=1)
        stmt = (
            select(KpiNodeSnapshotMonthly)
            .where(
                KpiNodeSnapshotMonthly.plant_node_id == plant_node_id,
                KpiNodeSnapshotMonthly.stat_month >= start_month,
                KpiNodeSnapshotMonthly.stat_month <= end_month,
            )
            .order_by(KpiNodeSnapshotMonthly.stat_month.asc())
        )
    else:
        raise ValueError(f"不支持的维度: {dimension}，可选值: hour/day/month")

    result = await db.execute(stmt)
    snaps = result.scalars().all()

    snapshots = [_monitor_snapshot_to_dict(s, dimension, node_name) for s in snaps]

    return {
        "plantNodeId": plant_node_id,
        "plantNodeName": node_name,
        "dimension": dimension,
        "start": start.isoformat() if isinstance(start, datetime) else str(start),
        "end": end.isoformat() if isinstance(end, datetime) else str(end),
        "snapshots": snapshots,
    }


__all__ = [
    "aggregate_node_snapshot",
    "calculate_and_save_node_snapshot",
    "collect_descendant_loop_ids",
    "get_node_latest_snapshot",
    "get_node_monitor_data",
    "get_node_ranking",
    "get_node_trend",
    "get_nodes_overview",
    "query_realtime_auto_rate",
    "save_node_snapshot",
]
