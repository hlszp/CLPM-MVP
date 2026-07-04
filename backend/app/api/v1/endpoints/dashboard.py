"""Dashboard aggregation endpoints (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

路由清单：
- GET /api/v1/dashboard/overview      — 工作台聚合数据（6 大 KPI + 低效回路 Top 10 + 趋势 + 待处理异常）
- GET /api/v1/dashboard/board         — 装置级三大 KPI 看板（综合性能/平均自控率/稳定率）
- GET /api/v1/dashboard/auto-rate-rt  — 实时自控率（每分钟刷新，来自 TDengine 最新 MODE 值）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.loop import LoopLedger
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


# ---------------------------------------------------------------------------
# S6-PORTAL-002: 装置级三大 KPI 看板（UIUX v5.3 ①）
# ---------------------------------------------------------------------------


@router.get("/board", response_model=ApiResponse[dict])
async def get_board_endpoint(
    plantId: str | None = Query(None, description="按装置/单元筛选；为空返回全部装置"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """装置级三大 KPI 看板（来自 ``unit_kpi_summary`` 表）.

    返回每个装置的最新 KPI 汇总快照：
    - ``avgScore``: 综合性能评分
    - ``autoModeRate``: 平均自控率
    - ``stabilityRate``: 稳定率

    若指定 ``plantId``，仅返回该装置的 KPI；否则返回全部 ``type=UNIT`` 的装置 KPI 列表。

    设计依据：FDS v5.1 §5.3.7, UIUX v5.3 ①, DDS v4.1 §2.17
    """
    # 构建查询：每个装置的最新一条 unit_kpi_summary
    if plantId:
        # 指定装置：查询该装置最新快照
        stmt = (
            select(UnitKpiSummary, PlantNode.name.label("node_name"))
            .join(PlantNode, UnitKpiSummary.node_id == PlantNode.id)
            .where(UnitKpiSummary.node_id == plantId)
            .order_by(desc(UnitKpiSummary.snapshot_time))
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.first()
        if row is None:
            return success(data={"items": [], "total": 0})
        summary, node_name = row
        item = _build_board_item(summary, node_name)
        return success(data={"items": [item], "total": 1})
    else:
        # 全部装置：使用 DISTINCT ON 等效查询每个装置最新快照
        # 子查询：每个 node_id 的最大 snapshot_time
        subq = (
            select(
                UnitKpiSummary.node_id.label("nid"),
                func.max(UnitKpiSummary.snapshot_time).label("max_ts"),
            )
            .group_by(UnitKpiSummary.node_id)
            .subquery()
        )
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
        return success(data={"items": items, "total": len(items)})


def _build_board_item(summary: UnitKpiSummary, node_name: str) -> dict:
    """构建看板单项响应字典."""
    return {
        "nodeId": str(summary.node_id),
        "nodeName": node_name,
        "snapshotTime": (
            summary.snapshot_time.isoformat() if summary.snapshot_time else None
        ),
        "avgScore": float(summary.avg_score) if summary.avg_score is not None else None,
        "autoModeRate": (
            float(summary.auto_mode_rate) if summary.auto_mode_rate is not None else None
        ),
        "stabilityRate": (
            float(summary.stability_rate) if summary.stability_rate is not None else None
        ),
        "effectiveAutoRate": (
            float(summary.effective_auto_rate)
            if summary.effective_auto_rate is not None
            else None
        ),
        "accuracyRate": (
            float(summary.accuracy_rate) if summary.accuracy_rate is not None else None
        ),
        "fastRate": float(summary.fast_rate) if summary.fast_rate is not None else None,
        "goodValueRate": (
            float(summary.good_value_rate)
            if summary.good_value_rate is not None
            else None
        ),
        "oscillationRate": (
            float(summary.oscillation_rate)
            if summary.oscillation_rate is not None
            else None
        ),
        "saturationRate": (
            float(summary.saturation_rate)
            if summary.saturation_rate is not None
            else None
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
    plantId: str | None = Query(None, description="按装置筛选；为空统计全厂"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """实时自控率（来自 TDengine 最新 MODE 值，每分钟刷新）.

    返回：
    - ``rate``: 自控率百分比（0-100）
    - ``autoCount``: 自动模式回路数
    - ``manualCount``: 手动模式回路数
    - ``totalCount``: 有效回路总数
    - ``readAt``: 统计时间（ISO 字符串）

    若 TDengine 不可用或无 MODE 数据，返回 ``rate=null``。

    设计依据：FDS v5.1 §5.3.6, UIUX v5.3 ①
    """
    from app.services.node_performance import query_realtime_auto_rate

    # 查询活跃回路 ID 列表
    loop_query = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
    if plantId:
        loop_query = loop_query.where(LoopLedger.unit_id == plantId)
    result = await db.execute(loop_query)
    loop_ids = [str(row[0]) for row in result.all()]

    if not loop_ids:
        return success(
            data={
                "rate": None,
                "autoCount": 0,
                "manualCount": 0,
                "totalCount": 0,
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
            "readAt": data["read_at"],
        }
    )


__all__ = ["router"]
