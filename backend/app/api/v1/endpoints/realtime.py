"""实时数据查询 API.

提供 ``GET /api/v1/realtime`` 端点，从 Redis 缓存读取实时值。
数据由 ``RealtimeSubscriber`` 后台任务持续更新。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.base import CamelModel
from app.schemas.common import ApiResponse
from app.services.data_source.realtime_subscriber import get_subscriber

router = APIRouter(prefix="/realtime", tags=["实时数据"])


class RealtimeValueItem(CamelModel):
    """实时值项."""

    tagCode: str
    value: str = ""
    quality: int = 0
    collectTime: str = ""


class RealtimeResponse(CamelModel):
    """实时数据响应."""

    items: list[RealtimeValueItem] = []


@router.get("", response_model=ApiResponse[RealtimeResponse])
async def get_realtime_values(
    tagCodes: list[str] = Query(..., description="Tag 编码列表"),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询 Tag 的实时值（从 Redis 缓存读取）.

    数据来源：RealtimeSubscriber 后台任务订阅 SignalR Hub，
    实时值缓存在 Redis（TTL 60 秒）。

    Args:
        tagCodes: Tag 编码列表（tag_name 格式，如 "LIC-101.PV"）

    Returns:
        实时值列表（未缓存的 Tag 不含在结果中）
    """
    subscriber = get_subscriber()
    cached = await subscriber.get_cached_values(tagCodes)

    items = [
        RealtimeValueItem(
            tagCode=item.get("tagCode", ""),
            value=item.get("value", ""),
            quality=item.get("quality", 0),
            collectTime=item.get("collectTime", ""),
        )
        for item in cached
    ]

    return {
        "code": "0",
        "message": "success",
        "data": {"items": items},
    }
