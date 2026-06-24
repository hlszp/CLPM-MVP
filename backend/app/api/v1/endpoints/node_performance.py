"""Node-level performance evaluation endpoints (GB/T 44693.2-2024 §6.4).

路由清单：
- GET  /api/v1/performance/nodes/{nodeId}/snapshot    — 节点最新快照
- GET  /api/v1/performance/nodes/{nodeId}/trend       — 节点历史趋势
- GET  /api/v1/performance/nodes/ranking              — 节点间排名
- POST /api/v1/performance/nodes/{nodeId}/calculate   — 手动触发指定时段计算
- GET  /api/v1/performance/nodes/overview             — 全厂总览
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.node_performance import (
    NodeCalculateRequest,
    NodeCalculateResult,
    NodeMonitorData,
    NodeOverviewData,
    NodeRankingItem,
    NodeSnapshotItem,
    NodeTrendData,
)
from app.services.node_performance import (
    get_node_latest_snapshot,
    get_node_monitor_data,
    get_node_ranking,
    get_node_trend,
    get_nodes_overview,
)

router = APIRouter(prefix="/performance/nodes", tags=["performance-node"])


def _parse_time_window(time_window: str) -> tuple[datetime, datetime]:
    """时间窗字符串 → (start, end)。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    if time_window == "today":
        return now - timedelta(hours=24), now
    if time_window == "yesterday":
        end = now - timedelta(days=1)
        return end - timedelta(days=1), end
    if time_window == "last_7_days":
        return now - timedelta(days=7), now
    if time_window == "last_30_days":
        return now - timedelta(days=30), now
    return now - timedelta(hours=24), now


# ---------------------------------------------------------------------------
# GET /nodes/{nodeId}/snapshot — 节点最新快照
# ---------------------------------------------------------------------------


@router.get("/{node_id}/snapshot", response_model=ApiResponse[NodeSnapshotItem])
async def get_node_snapshot_endpoint(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取节点最新性能快照（所有角色）。"""
    data = await get_node_latest_snapshot(db, node_id)
    if data is None:
        return success(data=None, message="该节点暂无快照数据")
    return success(data=data)


# ---------------------------------------------------------------------------
# GET /nodes/{nodeId}/trend — 节点历史趋势
# ---------------------------------------------------------------------------


@router.get("/{node_id}/trend", response_model=ApiResponse[NodeTrendData])
async def get_node_trend_endpoint(
    node_id: str,
    startTime: str = Query(..., description="起始时间（ISO 8601）"),
    endTime: str = Query(..., description="结束时间（ISO 8601）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取节点历史趋势（所有角色）。"""
    try:
        start_dt = datetime.fromisoformat(startTime.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        start_dt = datetime.fromisoformat(startTime)
    try:
        end_dt = datetime.fromisoformat(endTime.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        end_dt = datetime.fromisoformat(endTime)

    data = await get_node_trend(db, node_id, start_dt, end_dt)
    return success(data=data)


# ---------------------------------------------------------------------------
# GET /nodes/ranking — 节点间排名
# ---------------------------------------------------------------------------


@router.get("/ranking", response_model=ApiResponse[list[NodeRankingItem]])
async def get_node_ranking_endpoint(
    timeWindow: str = Query("today", description="时间窗：today/yesterday/last_7_days/last_30_days"),
    nodeType: str | None = Query(None, description="节点类型筛选：FACTORY/UNIT/EQUIPMENT"),
    sortBy: str = Query("score", description="排序字段：score/steady_rate/auto_loop_ratio/effective_auto_rate"),
    sortOrder: str = Query("desc", description="排序方向：asc/desc"),
    limit: int = Query(50, ge=1, le=200, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """节点间性能排名（所有角色）。"""
    start, end = _parse_time_window(timeWindow)
    data = await get_node_ranking(
        db=db,
        start=start,
        end=end,
        node_type=nodeType,
        sort_by=sortBy,
        sort_order=sortOrder,
        limit=limit,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# POST /nodes/{nodeId}/calculate — 手动触发指定时段计算
# ---------------------------------------------------------------------------


@router.post(
    "/{node_id}/calculate",
    response_model=ApiResponse[NodeCalculateResult],
)
async def calculate_node_endpoint(
    node_id: str,
    body: NodeCalculateRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "ENGINEER")),
) -> dict:
    """手动触发节点级 KPI 聚合（仅 ADMIN/ENGINEER）。

    支持指定时间段计算，用于补算/回填历史数据。
    不传 tsStart/tsEnd 时默认计算上一个完整小时。
    """
    from app.services.node_performance import calculate_and_save_node_snapshot

    now = datetime.now(UTC).replace(tzinfo=None)
    ts_start_str = body.tsStart if body else None
    ts_end_str = body.tsEnd if body else None

    if ts_start_str:
        try:
            ts_start_dt = datetime.fromisoformat(ts_start_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            ts_start_dt = datetime.fromisoformat(ts_start_str)
    else:
        ts_start_dt = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    if ts_end_str:
        try:
            ts_end_dt = datetime.fromisoformat(ts_end_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            ts_end_dt = datetime.fromisoformat(ts_end_str)
    else:
        ts_end_dt = ts_start_dt + timedelta(hours=1)

    snap = await calculate_and_save_node_snapshot(
        db=db,
        plant_node_id=node_id,
        ts_start=ts_start_dt,
        ts_end=ts_end_dt,
    )
    await db.commit()

    if snap is None:
        result = {
            "plantNodeId": node_id,
            "status": "SKIPPED",
            "reason": "无下属回路数据或无 SUCCESS 快照",
        }
    else:
        result = {
            "plantNodeId": node_id,
            "status": "SUCCESS",
            "snapshot": snap,
        }
    return success(data=result, message="节点级 KPI 计算完成")


# ---------------------------------------------------------------------------
# GET /nodes/overview — 全厂总览
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ApiResponse[NodeOverviewData])
async def get_nodes_overview_endpoint(
    timeWindow: str = Query("today", description="时间窗：today/yesterday/last_7_days/last_30_days"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """全厂总览：所有启用 KPI 评估的节点最新快照汇总（所有角色）。"""
    start, end = _parse_time_window(time_window=timeWindow)
    data = await get_nodes_overview(db, start, end)
    return success(data=data)


# ---------------------------------------------------------------------------
# GET /nodes/{nodeId}/monitor — 节点多维度监控（hour/day/month）
# ---------------------------------------------------------------------------


@router.get("/{node_id}/monitor", response_model=ApiResponse[NodeMonitorData])
async def get_node_monitor_endpoint(
    node_id: str,
    dimension: str = Query("hour", description="维度：hour/day/month"),
    start: str = Query(..., description="起始时间（ISO 8601，date 或 datetime）"),
    end: str = Query(..., description="结束时间（ISO 8601，date 或 datetime）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取节点多维度监控数据（hour/day/month，所有角色）。

    - dimension=hour：读 kpi_node_snapshot_hourly（按 ts_start 过滤）
    - dimension=day：读 kpi_node_snapshot_daily（按 stat_date 过滤）
    - dimension=month：读 kpi_node_snapshot_monthly（按 stat_month 过滤）
    """
    if dimension not in ("hour", "day", "month"):
        return success(
            data=None,
            message=f"不支持的维度: {dimension}，可选值: hour/day/month",
        )

    # 解析时间参数（兼容带 Z 后缀的 ISO 8601 和纯日期）
    def _parse_dt(s: str) -> datetime:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return datetime.fromisoformat(s)

    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)

    data = await get_node_monitor_data(
        db=db,
        plant_node_id=node_id,
        dimension=dimension,
        start=start_dt,
        end=end_dt,
    )
    return success(data=data)


__all__ = ["router"]
