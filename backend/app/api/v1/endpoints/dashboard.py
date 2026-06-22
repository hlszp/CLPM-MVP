"""Dashboard aggregation endpoints (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

路由清单：
- GET /api/v1/dashboard/overview — 工作台聚合数据（6 大 KPI + 低效回路 Top 10 + 趋势 + 待处理异常）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import success
from app.services.dashboard import get_dashboard_overview

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# S6-PORTAL-001: 工作台聚合 API
# ---------------------------------------------------------------------------


@router.get("/overview")
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


__all__ = ["router"]
