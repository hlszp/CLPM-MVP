"""实时数据查询 API.

提供 ``GET /api/v1/realtime`` 端点，从 Redis 缓存读取实时值。
数据由 ``RealtimeSubscriber`` 后台任务持续更新。

P4-03（01§3.5 原始 tag 接口执行数据范围）：
- tag 按 ``tag_registry → loop_tag_mapping → loop_ledger.unit_id → plant_node``
  实际绑定关系解析可见性——非 ADMIN 仅可读"有授权回路绑定"的 tag；
  未绑定 tag 默认仅 ADMIN 可读（ALL 不推定可看任何 tag）；
- 请求集合与授权集合取交集；越界 tag 进入 ``rejected`` 列表（与 WS 订阅
  协议同口径的通用拒绝，不泄露对象详情）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_scope_ctx
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.base import CamelModel
from app.schemas.common import ApiResponse
from app.services.authz_scope import ScopeContext, classify_tags
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
    #: P4-03：越界/未知 tag 通用拒绝列表（不泄露对象详情；None=无拒绝）
    rejected: list[str] | None = None


@router.get("", response_model=ApiResponse[RealtimeResponse])
async def get_realtime_values(
    tagCodes: list[str] = Query(..., description="Tag 编码列表"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    scope: ScopeContext = Depends(get_scope_ctx),
) -> dict:
    """查询 Tag 的实时值（从 Redis 缓存读取；P4-03 按归属过滤）.

    数据来源：RealtimeSubscriber 后台任务订阅 SignalR Hub，
    实时值缓存在 Redis（TTL 3600 秒，确保页面刷新时能从缓存读取实时值）。

    Args:
        tagCodes: Tag 编码列表（tag_name 格式，如 "LIC-101.PV"）
        scope: 当前用户数据范围（ALL/NODE_SUBTREE）

    Returns:
        实时值列表（未缓存的 Tag 不含在结果中）；越界 tag 在 rejected 列表
    """
    visible, rejected = await classify_tags(db, scope, list(tagCodes))
    subscriber = get_subscriber()
    cached = await subscriber.get_cached_values(visible) if visible else []

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
        "data": {"items": items, "rejected": rejected or None},
    }
