"""监控模块 API 端点——关注队列（整改方案 §8.1）。

路由清单：
- GET /api/v1/monitor/attention  统一关注队列（分页+筛选）

关注队列聚合五类来源（ALERT/DEGRADATION/DATA_QUALITY/TRACKER/VERIFICATION），
不新增数据库表；动作由服务端按角色生成。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.services.monitor_attention import list_attention

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/monitor", tags=["monitor"])

#: 合法来源
_VALID_SOURCES = frozenset(("ALERT", "DEGRADATION", "DATA_QUALITY", "TRACKER", "VERIFICATION"))
#: 合法优先级
_VALID_PRIORITIES = frozenset(("URGENT", "HIGH", "MEDIUM", "LOW"))
#: 合法状态
_VALID_STATUSES = frozenset(("OPEN", "ACKNOWLEDGED", "SUPPRESSED", "IN_PROGRESS", "VERIFYING"))


@router.get("/attention", response_model=ApiResponse[dict])
async def list_attention_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    source: list[str] | None = Query(
        None, description="来源筛选（可重复）：ALERT/DEGRADATION/DATA_QUALITY/TRACKER/VERIFICATION"
    ),
    priority: list[str] | None = Query(
        None, description="优先级筛选（可重复）：URGENT/HIGH/MEDIUM/LOW"
    ),
    status: list[str] | None = Query(
        None, description="状态筛选（可重复）：OPEN/ACKNOWLEDGED/SUPPRESSED/IN_PROGRESS/VERIFYING"
    ),
    loopId: str | None = Query(None, description="按回路精确筛选"),
    keyword: str | None = Query(None, description="按位号/标题模糊查询"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """统一关注队列——聚合预警/评分恶化/数据质量/Tracker/验证超期。"""
    # 过滤非法枚举值（静默忽略，不报 400）
    sources = [s for s in (source or []) if s in _VALID_SOURCES] or None
    priorities = [p for p in (priority or []) if p in _VALID_PRIORITIES] or None
    statuses = [s for s in (status or []) if s in _VALID_STATUSES] or None

    data = await list_attention(
        db=db,
        plant_node_id=plantNodeId,
        sources=sources,
        priorities=priorities,
        statuses=statuses,
        loop_id=loopId,
        keyword=keyword,
        page=page,
        page_size=pageSize,
        role=user.role,
    )
    return success(data=data)
