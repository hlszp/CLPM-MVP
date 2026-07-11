"""Dashboard aggregation endpoints (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

路由清单：
- GET /api/v1/dashboard/overview      — 工作台聚合数据（6 大 KPI + 低效回路 Top 10 + 趋势 + 异常）
- GET /api/v1/dashboard/board         — 装置级三大 KPI 看板（综合性能/平均自控率/稳定率）
- GET /api/v1/dashboard/auto-rate-rt  — 实时自控率（每分钟刷新，来自 TDengine 最新 MODE 值）
"""

from __future__ import annotations

import logging
from datetime import UTC

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.models.plant_node import PlantNode
from app.models.sys_user import SysUser
from app.models.unit_kpi_summary import UnitKpiSummary
from app.schemas.common import ApiResponse, success
from app.services.dashboard import get_dashboard_overview

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# S6-PORTAL-001: 工作台聚合 API
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ApiResponse[dict])
async def get_overview_endpoint(
    plantId: str | None = Query(None, description="按装置/单元筛选"),
    granularity: str = Query(
        "day",
        description="时间粒度：day/week/month（day=最近24小时，week=最近7天，month=最近30天）",
    ),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """工作台聚合数据（所有角色可访问，不同角色数据范围不同）。

    - ADMIN/EXPERT：全厂数据
    - IC_ENGINEER/PE_ENGINEER：装置级数据
    - SPONSOR：工厂级汇总（仅 KPI 卡片，不返回低效回路列表）

    Redis 缓存 5 分钟，缓存 key 含 plant_id + granularity + user_role。
    """
    data = await get_dashboard_overview(
        db=db,
        user_role=user.role,
        plant_id=plantId,
        granularity=granularity,
    )
    return success(data=data)


def _build_board_item(summary: UnitKpiSummary, node_name: str) -> dict:
    """构建看板单项响应字典."""
    return {
        "nodeId": str(summary.node_id),
        "nodeName": node_name,
        "snapshotTime": (summary.snapshot_time.isoformat() if summary.snapshot_time else None),
        "avgScore": float(summary.avg_score) if summary.avg_score is not None else None,
        "autoModeRate": (
            float(summary.auto_mode_rate) if summary.auto_mode_rate is not None else None
        ),
        "stabilityRate": (
            float(summary.stability_rate) if summary.stability_rate is not None else None
        ),
        "effectiveAutoRate": (
            float(summary.effective_auto_rate) if summary.effective_auto_rate is not None else None
        ),
        "accuracyRate": (
            float(summary.accuracy_rate) if summary.accuracy_rate is not None else None
        ),
        "fastRate": float(summary.fast_rate) if summary.fast_rate is not None else None,
        "goodValueRate": (
            float(summary.good_value_rate) if summary.good_value_rate is not None else None
        ),
        "oscillationRate": (
            float(summary.oscillation_rate) if summary.oscillation_rate is not None else None
        ),
        "saturationRate": (
            float(summary.saturation_rate) if summary.saturation_rate is not None else None
        ),
        "totalLoops": summary.total_loops,
        "evaluatedLoops": summary.evaluated_loops,
        "inconclusiveLoops": summary.inconclusive_loops,
        "excludedLoops": summary.excluded_loops,
        "status": summary.status,
        "algorithmVersion": summary.algorithm_version,
    }


# ---------------------------------------------------------------------------
# S6-PORTAL-003: 实时自控率（UIUX v5.3 ① — 半圆径向仪表盘）
# ---------------------------------------------------------------------------


@router.get("/auto-rate-rt", response_model=ApiResponse[dict])
async def get_auto_rate_rt_endpoint(
    plantId: str | None = Query(None, description="按节点筛选；为空统计全厂；递归包含所有下属节点"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """实时自控率（来自 TDengine 最新 MODE 值，每分钟刷新）.

    返回：
    - ``rate``: 自控率百分比（0-100）
    - ``autoCount``: 自动模式回路数
    - ``manualCount``: 手动模式回路数
    - ``totalCount``: 有效回路总数
    - ``modeCounts``: 5 种 MODE 各自的回路数（dict[int,int]，key 为 0/1/2/3/4）
    - ``readAt``: 统计时间（ISO 字符串）

    若 TDengine 不可用或无 MODE 数据，返回 ``rate=null``。

    设计依据：FDS v5.1 §5.3.6, UIUX v5.3 ①

    v6.1 更新：
    - 支持递归聚合当前节点及所有下属节点的回路
    - modeCounts 字段用于前端饼图按 5 种 MODE 中文展示（0-手动/1-自动/2-串级/3-远程/4-先控）
    """
    from app.services.node_performance import collect_descendant_loop_ids, query_realtime_auto_rate

    # 查询活跃回路 ID 列表（递归包含当前节点及所有下属节点）
    if plantId:
        loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_query = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        result = await db.execute(loop_query)
        loop_ids = [str(row[0]) for row in result.all()]

    if not loop_ids:
        return success(
            data={
                "rate": None,
                "autoCount": 0,
                "manualCount": 0,
                "totalCount": 0,
                "modeCounts": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0},
                "readAt": None,
                "message": "无活跃回路",
            }
        )

    data = await query_realtime_auto_rate(db, loop_ids)
    if data is None:
        return success(
            data={
                "rate": None,
                "autoCount": 0,
                "manualCount": 0,
                "totalCount": 0,
                "modeCounts": {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0},
                "readAt": None,
                "message": "TDengine 不可用或无 MODE 数据",
            }
        )

    return success(
        data={
            "rate": float(data["rate"]),
            "autoCount": data["auto_count"],
            "manualCount": data["manual_count"],
            "totalCount": data["total_count"],
            "modeCounts": {str(k): v for k, v in data["mode_counts"].items()},
            "readAt": data["read_at"],
        }
    )


# ---------------------------------------------------------------------------
# S6-PORTAL-004: 节点级聚合 KPI（v6.1 新增）
# ---------------------------------------------------------------------------


@router.get("/board/trend", response_model=ApiResponse[dict])
async def get_board_trend_endpoint(
    plantId: str | None = Query(None, description="按节点筛选；为空统计全厂；递归包含所有下属节点"),
    timeWindow: str = Query(
        "today", description="时间窗：last_8_hours/today/yesterday/last_7_days/last_30_days"
    ),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """节点级聚合趋势数据（来自 ``unit_kpi_summary`` 表，递归聚合当前节点及所有下属节点）.

    返回当前节点及其所有下属节点的趋势数据，按小时聚合：
    - ``avgScore``: 综合性能评分（加权平均）
    - ``autoModeRate``: 平均自控率（加权平均）
    - ``stabilityRate``: 稳定率（加权平均）
    - ``evaluatedLoops``: 参评回路数（求和）

    若指定 ``plantId``，返回该节点及其所有下属节点的聚合趋势；
    若未指定 ``plantId``，返回全厂所有节点的聚合趋势。

    设计依据：FDS v5.1 §5.3.7, UIUX v5.3 ①, DDS v4.1 §2.17

    v6.1 更新：支持递归聚合当前节点及所有下属节点的趋势数据（使用 PostgreSQL 递归 CTE）
    """
    from datetime import datetime, timedelta

    now = datetime.now(UTC).replace(tzinfo=None)
    if timeWindow == "last_8_hours":
        start = now - timedelta(hours=8)
    elif timeWindow == "today":
        start = now - timedelta(hours=24)
    elif timeWindow == "yesterday":
        start = now - timedelta(days=2)
        now = now - timedelta(days=1)
    elif timeWindow == "last_7_days":
        start = now - timedelta(days=7)
    elif timeWindow == "last_30_days":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(hours=24)

    # v6.1.2 修复：只聚合 UNIT 级节点，避免 FACTORY/AREA/UNIT 父子节点重复累加
    if plantId:
        cte_sql = text("""
            WITH RECURSIVE node_tree AS (
                SELECT id, type FROM plant_node WHERE id = :node_id
                UNION ALL
                SELECT child.id, child.type FROM plant_node child
                JOIN node_tree ON child.parent_id = node_tree.id
            )
            SELECT id FROM node_tree WHERE type = 'UNIT'
        """)
        result = await db.execute(cte_sql, {"node_id": plantId})
        descendant_ids = [str(row.id) for row in result.all()]
    else:
        result = await db.execute(select(PlantNode.id).where(PlantNode.type == "UNIT"))
        descendant_ids = [str(row.id) for row in result.all()]

    # 查询实际回路总数（去重，避免父子节点重复累加）
    from app.services.node_performance import collect_descendant_loop_ids

    if plantId:
        total_loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_result = await db.execute(select(LoopLedger.id).where(LoopLedger.is_active.is_(True)))
        total_loop_ids = [str(row[0]) for row in loop_result.all()]
    total_loops_count = len(total_loop_ids)

    if not descendant_ids:
        return success(
            data={
                "timestamps": [],
                "avgScore": [],
                "autoModeRate": [],
                "stabilityRate": [],
                "evaluatedLoops": [],
                "totalLoops": total_loops_count,
            }
        )

    hour_col = func.date_trunc("hour", UnitKpiSummary.snapshot_time).label("hour")

    subq = (
        select(
            hour_col,
            UnitKpiSummary.node_id.label("nid"),
            UnitKpiSummary.evaluated_loops,
            UnitKpiSummary.avg_score,
            UnitKpiSummary.auto_mode_rate,
            UnitKpiSummary.stability_rate,
        ).where(
            UnitKpiSummary.node_id.in_(descendant_ids),
            UnitKpiSummary.snapshot_time >= start,
            UnitKpiSummary.snapshot_time <= now,
        )
    ).subquery()

    stmt = (
        select(
            subq.c.hour,
            func.sum(subq.c.evaluated_loops).label("total_evaluated"),
            func.sum(subq.c.avg_score * subq.c.evaluated_loops).label("score_weighted_sum"),
            func.sum(subq.c.auto_mode_rate * subq.c.evaluated_loops).label("auto_weighted_sum"),
            func.sum(subq.c.stability_rate * subq.c.evaluated_loops).label("stable_weighted_sum"),
        )
        .group_by(subq.c.hour)
        .order_by(subq.c.hour.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    timestamps: list[str] = []
    avg_score: list[float | None] = []
    auto_mode_rate: list[float | None] = []
    stability_rate: list[float | None] = []
    evaluated_loops: list[int] = []

    for row in rows:
        timestamps.append(row.hour.strftime("%Y-%m-%dT%H:00:00"))
        total = row.total_evaluated or 0
        evaluated_loops.append(total)
        if total > 0:
            avg_score.append(round(float(row.score_weighted_sum or 0) / total, 2))
            auto_mode_rate.append(round(float(row.auto_weighted_sum or 0) / total, 2))
            stability_rate.append(round(float(row.stable_weighted_sum or 0) / total, 2))
        else:
            avg_score.append(None)
            auto_mode_rate.append(None)
            stability_rate.append(None)

    return success(
        data={
            "timestamps": timestamps,
            "avgScore": avg_score,
            "autoModeRate": auto_mode_rate,
            "stabilityRate": stability_rate,
            "evaluatedLoops": evaluated_loops,
            "totalLoops": total_loops_count,
        }
    )


# ---------------------------------------------------------------------------
# S6-PORTAL-005: 节点级聚合 KPI（v6.1 新增）
# ---------------------------------------------------------------------------


@router.get("/board/aggregate", response_model=ApiResponse[dict])
async def get_board_aggregate_endpoint(
    plantId: str | None = Query(None, description="按节点筛选；为空统计全厂；递归包含所有下属节点"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """节点级聚合 KPI（来自 ``unit_kpi_summary`` 表，递归聚合当前节点及所有下属节点）.

    返回当前节点及其所有下属节点的最新 KPI 汇总快照：
    - ``avgScore``: 综合性能评分（加权平均）
    - ``autoModeRate``: 平均自控率（加权平均）
    - ``stabilityRate``: 稳定率（加权平均）
    - ``effectiveAutoRate``: 有效自控率（加权平均）
    - ``accuracyRate``: 准确率（加权平均）
    - ``fastRate``: 快速率（加权平均）
    - ``goodValueRate``: 好值率（加权平均）
    - ``totalLoops``: 总回路数（去重后实际回路数）
    - ``evaluatedLoops``: 参评回路数（去重后实际回路数）
    - ``inconclusiveLoops``: INCONCLUSIVE 回路数（去重后实际回路数）
    - ``excludedLoops``: 排除回路数（去重后实际回路数）

    若指定 ``plantId``，返回该节点及其所有下属节点的聚合 KPI；
    若未指定 ``plantId``，返回全厂所有节点的聚合 KPI。

    设计依据：FDS v5.1 §5.3.7, UIUX v5.3 ①, DDS v4.1 §2.17

    v6.1 更新：支持递归聚合当前节点及所有下属节点的 KPI（使用 PostgreSQL 递归 CTE）

    v6.1.1 修复：回路数统计改为从数据库直接查询实际回路数，避免父子节点重复累加
    """
    from app.services.node_performance import collect_descendant_loop_ids

    # 获取实际回路数（去重，避免父子节点重复累加）
    if plantId:
        loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_query = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        result = await db.execute(loop_query)
        loop_ids = [str(row[0]) for row in result.all()]

    # 使用递归 CTE 获取当前节点及所有下属节点 ID 列表（用于查询 unit_kpi_summary）
    if plantId:
        cte_sql = text("""
            WITH RECURSIVE node_tree AS (
                SELECT id FROM plant_node WHERE id = :node_id
                UNION ALL
                SELECT child.id FROM plant_node child
                JOIN node_tree ON child.parent_id = node_tree.id
            )
            SELECT id FROM node_tree
        """)
        result = await db.execute(cte_sql, {"node_id": plantId})
        descendant_ids = [str(row.id) for row in result.all()]
    else:
        result = await db.execute(select(PlantNode.id))
        descendant_ids = [str(row.id) for row in result.all()]

    if not descendant_ids:
        return success(data={"items": [], "total": 0})

    # 子查询：每个 node_id 的最大 snapshot_time
    subq = (
        select(
            UnitKpiSummary.node_id.label("nid"),
            func.max(UnitKpiSummary.snapshot_time).label("max_ts"),
        )
        .where(UnitKpiSummary.node_id.in_(descendant_ids))
        .group_by(UnitKpiSummary.node_id)
        .subquery()
    )

    # 查询每个子节点的最新快照
    stmt = (
        select(UnitKpiSummary, PlantNode.name.label("node_name"))
        .join(PlantNode, UnitKpiSummary.node_id == PlantNode.id)
        .join(
            subq,
            (UnitKpiSummary.node_id == subq.c.nid)
            & (UnitKpiSummary.snapshot_time == subq.c.max_ts),
        )
        .order_by(PlantNode.name)
    )

    result = await db.execute(stmt)
    rows = result.all()
    items = [_build_board_item(summary, node_name) for summary, node_name in rows]

    # 获取聚合节点名称
    node_name = None
    if plantId:
        node_result = await db.execute(select(PlantNode.name).where(PlantNode.id == plantId))
        node_name = node_result.scalar_one_or_none()

    # 从数据库直接查询实际回路数统计（避免父子节点重复累加）
    total_loops = len(loop_ids)
    excluded_loops = 0
    evaluated_loops = 0
    inconclusive_loops = 0

    if loop_ids:
        # excluded_loops: include_in_evaluation=false 的回路数
        ex_result = await db.execute(
            select(func.count())
            .select_from(LoopLedger)
            .where(
                LoopLedger.id.in_(loop_ids),
                LoopLedger.include_in_evaluation.is_(False),
            )
        )
        excluded_loops = int(ex_result.scalar() or 0)

        # 获取最近时间窗
        from datetime import datetime, timedelta

        now = datetime.now(UTC).replace(tzinfo=None)
        start_time = now - timedelta(hours=24)

        # 查询有 SUCCESS 快照的回路 ID
        success_result = await db.execute(
            select(func.distinct(KpiSnapshotHourly.loop_id))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start_time,
                KpiSnapshotHourly.status == "SUCCESS",
                LoopLedger.include_in_evaluation.is_(True),
            )
        )
        success_loop_ids = [str(row[0]) for row in success_result.all()]
        evaluated_loops = len(success_loop_ids)

        # inconclusive_loops: 只有 INCONCLUSIVE 快照但没有 SUCCESS 快照的回路数
        ic_result = await db.execute(
            select(func.count(func.distinct(KpiSnapshotHourly.loop_id)))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(
                KpiSnapshotHourly.loop_id.in_(loop_ids),
                KpiSnapshotHourly.ts_start >= start_time,
                KpiSnapshotHourly.status == "INCONCLUSIVE",
                KpiSnapshotHourly.loop_id.not_in(success_loop_ids) if success_loop_ids else True,
                LoopLedger.include_in_evaluation.is_(True),
            )
        )
        inconclusive_loops = int(ic_result.scalar() or 0)

    # 计算聚合值（仅使用 UNIT 级节点数据加权，避免父子节点重复）
    # 过滤出 UNIT 类型的节点
    unit_items = []
    for item in items:
        node_type_result = await db.execute(
            select(PlantNode.type).where(PlantNode.id == item["nodeId"])
        )
        node_type = node_type_result.scalar_one_or_none()
        if node_type == "UNIT":
            unit_items.append(item)

    # 按 UNIT 节点的 evaluatedLoops 加权平均
    total_unit_evaluated = sum(
        item.get("evaluatedLoops", 0) for item in unit_items if item.get("evaluatedLoops") > 0
    )

    def weighted_avg(field: str) -> float | None:
        if total_unit_evaluated == 0:
            return None
        total = 0.0
        count = 0
        for item in unit_items:
            val = item.get(field)
            weight = item.get("evaluatedLoops", 0)
            if val is not None and weight > 0:
                total += float(val) * weight
                count += weight
        return round(total / count, 2) if count > 0 else None

    aggregate = {
        "nodeId": plantId,
        "nodeName": node_name or "全厂",
        "avgScore": weighted_avg("avgScore"),
        "autoModeRate": weighted_avg("autoModeRate"),
        "stabilityRate": weighted_avg("stabilityRate"),
        "effectiveAutoRate": weighted_avg("effectiveAutoRate"),
        "accuracyRate": weighted_avg("accuracyRate"),
        "fastRate": weighted_avg("fastRate"),
        "goodValueRate": weighted_avg("goodValueRate"),
        "totalLoops": total_loops,
        "evaluatedLoops": evaluated_loops,
        "inconclusiveLoops": inconclusive_loops,
        "excludedLoops": excluded_loops,
    }

    return success(data={"items": items, "total": len(items), "aggregate": aggregate})


__all__ = ["router"]
