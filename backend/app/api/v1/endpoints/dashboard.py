"""Dashboard aggregation endpoints (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

路由清单：
- GET /api/v1/dashboard/overview      — 工作台聚合数据（6 大 KPI + 低效回路 Top 10 + 趋势 + 异常）
- GET /api/v1/dashboard/board         — 装置级三大 KPI 看板（综合性能/平均自控率/稳定率）
- GET /api/v1/dashboard/auto-rate-rt  — 实时自控率（每分钟刷新，来自 TDengine 最新 MODE 值）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

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
        "instrumentFaultRate": (
            float(summary.instrument_fault_rate)
            if summary.instrument_fault_rate is not None
            else None
        ),
        "totalLoops": summary.total_loops or 0,
        "evaluatedLoops": summary.evaluated_loops or 0,
        "inconclusiveLoops": summary.inconclusive_loops or 0,
        "excludedLoops": summary.excluded_loops or 0,
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
    - ``readAt``: 数据最新时间（实时缓存 collectTime 最大值，ISO 字符串；
      全部回退 DB 值时为 None，表示实时流中断）

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

    # 构建按小时索引的数据字典
    row_map: dict[str, any] = {}
    for row in rows:
        hour_key = row.hour.strftime("%Y-%m-%dT%H:00:00")
        row_map[hour_key] = row

    # 生成完整的小时序列（填充缺失的小时）
    timestamps: list[str] = []
    avg_score: list[float | None] = []
    auto_mode_rate: list[float | None] = []
    stability_rate: list[float | None] = []
    evaluated_loops: list[int] = []

    current = start.replace(minute=0, second=0, microsecond=0)
    while current <= now:
        hour_key = current.strftime("%Y-%m-%dT%H:00:00")
        timestamps.append(hour_key)
        row = row_map.get(hour_key)
        if row is not None:
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
        else:
            evaluated_loops.append(0)
            avg_score.append(None)
            auto_mode_rate.append(None)
            stability_rate.append(None)
        current += timedelta(hours=1)

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


def _resolve_aggregate_window(time_window: str | None) -> tuple[datetime, datetime] | None:
    """解析 board/aggregate 时间窗为 (start, end)（naive UTC）；None=不启用窗口.

    取值与 board/trend 对齐：last_8_hours/today/yesterday/last_7_days/last_30_days，
    未识别值回退 today（24 小时）。
    """
    if not time_window:
        return None
    now = datetime.now(UTC).replace(tzinfo=None)
    if time_window == "last_8_hours":
        start = now - timedelta(hours=8)
    elif time_window == "today":
        start = now - timedelta(hours=24)
    elif time_window == "yesterday":
        return now - timedelta(days=2), now - timedelta(days=1)
    elif time_window == "last_7_days":
        start = now - timedelta(days=7)
    elif time_window == "last_30_days":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(hours=24)
    return start, now


# 窗口聚合的 rate 字段（unit_kpi_summary 列名 → 响应 item 键名）
_WINDOW_RATE_FIELD_KEYS = {
    "avg_score": "avgScore",
    "auto_mode_rate": "autoModeRate",
    "stability_rate": "stabilityRate",
    "effective_auto_rate": "effectiveAutoRate",
    "accuracy_rate": "accuracyRate",
    "fast_rate": "fastRate",
    "good_value_rate": "goodValueRate",
    "oscillation_rate": "oscillationRate",
    "saturation_rate": "saturationRate",
    "instrument_fault_rate": "instrumentFaultRate",
}


async def _load_window_items(
    db: AsyncSession,
    item_node_ids: list[str],
    window: tuple[datetime, datetime],
) -> list[dict]:
    """窗口模式明细行：rate 字段取窗口内 evaluated_loops 加权平均.

    计数字段（totalLoops/evaluatedLoops/inconclusiveLoops/excludedLoops/status 等）
    取窗口内最新快照行；snapshotTime=窗口内最新快照时间。
    """
    start, end = window

    # 每节点窗口内最新快照（计数字段来源）
    subq = (
        select(
            UnitKpiSummary.node_id.label("nid"),
            func.max(UnitKpiSummary.snapshot_time).label("max_ts"),
        )
        .where(
            UnitKpiSummary.node_id.in_(item_node_ids),
            UnitKpiSummary.snapshot_time >= start,
            UnitKpiSummary.snapshot_time <= end,
        )
        .group_by(UnitKpiSummary.node_id)
        .subquery()
    )
    latest_stmt = (
        select(UnitKpiSummary, PlantNode.name.label("node_name"))
        .join(PlantNode, UnitKpiSummary.node_id == PlantNode.id)
        .join(
            subq,
            (UnitKpiSummary.node_id == subq.c.nid)
            & (UnitKpiSummary.snapshot_time == subq.c.max_ts),
        )
        .order_by(PlantNode.name)
    )
    result = await db.execute(latest_stmt)
    latest_rows = result.all()

    # 每节点窗口内 rate 字段加权和（权重=evaluated_loops，与 board/trend 同口径）
    w_stmt = (
        select(
            UnitKpiSummary.node_id.label("nid"),
            *[
                func.sum(getattr(UnitKpiSummary, field) * UnitKpiSummary.evaluated_loops).label(
                    field
                )
                for field in _WINDOW_RATE_FIELD_KEYS
            ],
            func.sum(UnitKpiSummary.evaluated_loops).label("eval_sum"),
        )
        .where(
            UnitKpiSummary.node_id.in_(item_node_ids),
            UnitKpiSummary.snapshot_time >= start,
            UnitKpiSummary.snapshot_time <= end,
        )
        .group_by(UnitKpiSummary.node_id)
    )
    w_result = await db.execute(w_stmt)
    w_map = {str(row.nid): row for row in w_result.all()}

    items: list[dict] = []
    for summary, node_name in latest_rows:
        item = _build_board_item(summary, node_name)
        w = w_map.get(str(summary.node_id))
        eval_sum = int(w.eval_sum or 0) if w is not None else 0
        for field, key in _WINDOW_RATE_FIELD_KEYS.items():
            if eval_sum > 0:
                weighted_sum = getattr(w, field)
                item[key] = round(float(weighted_sum or 0) / eval_sum, 2)
            else:
                item[key] = None
        items.append(item)
    return items


@router.get("/board/aggregate", response_model=ApiResponse[dict])
async def get_board_aggregate_endpoint(
    plantId: str | None = Query(None, description="按节点筛选；为空统计全厂；递归包含所有下属节点"),
    timeWindow: str | None = Query(
        None,
        description="时间窗：last_8_hours/today/yesterday/last_7_days/last_30_days；"
        "缺省=每节点最新快照（现状）",
    ),
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

    v6.1.4 更新：新增可选 ``timeWindow``——缺省时保持"每节点最新快照"现状；
    指定后 rate 类字段改为窗口内 evaluated_loops 加权平均（与 board/trend 同口径），
    计数字段取窗口内最新快照，evaluatedLoops/inconclusiveLoops 按窗口边界统计，
    响应回显 timeWindow/windowStart/windowEnd 供前端标注统计窗口。
    """
    from app.services.node_performance import collect_descendant_loop_ids

    window = _resolve_aggregate_window(timeWindow)

    # 获取实际回路数（去重，避免父子节点重复累加）
    if plantId:
        loop_ids = await collect_descendant_loop_ids(db, plantId)
    else:
        loop_query = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        result = await db.execute(loop_query)
        loop_ids = [str(row[0]) for row in result.all()]

    # 明细表只列出当前节点及其直接子节点（不含本节点之外的下下层节点）：
    # 第一行固定为当前节点统计，后续行为下一层子节点性能数据
    if plantId:
        result = await db.execute(
            select(PlantNode.id).where((PlantNode.id == plantId) | (PlantNode.parent_id == plantId))
        )
        item_node_ids = [str(row.id) for row in result.all()]
    else:
        # 未指定节点时：列出全部根节点（顶层）
        result = await db.execute(select(PlantNode.id).where(PlantNode.parent_id.is_(None)))
        item_node_ids = [str(row.id) for row in result.all()]

    if not item_node_ids:
        return success(data={"items": [], "total": 0})

    if window is None:
        # 缺省（现状）：每个 node_id 取最新快照
        subq = (
            select(
                UnitKpiSummary.node_id.label("nid"),
                func.max(UnitKpiSummary.snapshot_time).label("max_ts"),
            )
            .where(UnitKpiSummary.node_id.in_(item_node_ids))
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
    else:
        # 窗口模式：rate 字段按窗口加权，计数字段取窗口内最新快照
        items = await _load_window_items(db, item_node_ids, window)

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

        # 参评/INCONCLUSIVE 统计窗口：窗口模式用窗口边界，缺省保持近 24 小时现状
        if window is not None:
            start_time = window[0]
        else:
            start_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)

        # 查询有 SUCCESS 快照的回路 ID
        success_conds = [
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= start_time,
            KpiSnapshotHourly.status == "SUCCESS",
            LoopLedger.include_in_evaluation.is_(True),
        ]
        if window is not None:
            success_conds.append(KpiSnapshotHourly.ts_start <= window[1])
        success_result = await db.execute(
            select(func.distinct(KpiSnapshotHourly.loop_id))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(*success_conds)
        )
        success_loop_ids = [str(row[0]) for row in success_result.all()]
        evaluated_loops = len(success_loop_ids)

        # inconclusive_loops: 只有 INCONCLUSIVE 快照但没有 SUCCESS 快照的回路数
        ic_conds = [
            KpiSnapshotHourly.loop_id.in_(loop_ids),
            KpiSnapshotHourly.ts_start >= start_time,
            KpiSnapshotHourly.status == "INCONCLUSIVE",
            KpiSnapshotHourly.loop_id.not_in(success_loop_ids) if success_loop_ids else True,
            LoopLedger.include_in_evaluation.is_(True),
        ]
        if window is not None:
            ic_conds.append(KpiSnapshotHourly.ts_start <= window[1])
        ic_result = await db.execute(
            select(func.count(func.distinct(KpiSnapshotHourly.loop_id)))
            .select_from(KpiSnapshotHourly)
            .join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id)
            .where(*ic_conds)
        )
        inconclusive_loops = int(ic_result.scalar() or 0)

    # 计算聚合值
    # v6.1.3 修复：优先取当前节点自身的 unit_kpi_summary 行——该表按节点持久化了
    # 递归聚合结果（含重要等级加权），是装置级聚合的权威口径，且与明细表首行一致；
    # 原实现仅对 items 中的 UNIT 级节点加权，选中 FACTORY 或全厂（items 全为
    # FACTORY 根节点）时无 UNIT 可加，聚合指标全部返回 NULL。
    # 当前节点无快照（或全厂视图）时，退化为 items 间按 evaluatedLoops 加权平均：
    # items 仅含当前节点+直接子节点（或全厂根节点），兄弟/根节点间回路不重叠，
    # 不存在父子重复计数。
    self_item = next((it for it in items if it.get("nodeId") == plantId), None) if plantId else None

    def weighted_avg(field: str) -> float | None:
        if self_item is not None and self_item.get(field) is not None:
            return round(float(self_item[field]), 2)
        total = 0.0
        count = 0
        for item in items:
            val = item.get(field)
            weight = item.get("evaluatedLoops") or 0
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
        "instrumentFaultRate": weighted_avg("instrumentFaultRate"),
        "totalLoops": total_loops,
        "evaluatedLoops": evaluated_loops,
        "inconclusiveLoops": inconclusive_loops,
        "excludedLoops": excluded_loops,
    }

    data: dict = {"items": items, "total": len(items), "aggregate": aggregate}
    if window is not None:
        # 回显统计窗口，供前端 gauges 卡片标注
        data["timeWindow"] = timeWindow
        data["windowStart"] = window[0].isoformat()
        data["windowEnd"] = window[1].isoformat()
    return success(data=data)


__all__ = ["router"]
