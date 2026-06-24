"""Loop mode mapping endpoints (重构方案 v1.2 — 投用定义 CRUD).

路由清单：
- GET /api/v1/loops/{loopId}/mode-mapping — 获取回路投用定义（所有认证用户）
- PUT /api/v1/loops/{loopId}/mode-mapping — 全量替换投用定义（仅 ADMIN）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.loop_config import (
    ModeMappingItem,
    ModeMappingReplaceRequest,
)
from app.services.loop_config import list_mode_mappings, replace_mode_mappings

router = APIRouter(prefix="/loops", tags=["loop-mode-mapping"])


@router.get(
    "/{loop_id}/mode-mapping",
    response_model=ApiResponse[list[ModeMappingItem]],
)
async def get_mode_mapping_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取回路投用定义列表（所有认证用户可读）。"""
    data = await list_mode_mappings(db, loop_id)
    return success(data=data)


@router.put(
    "/{loop_id}/mode-mapping",
    response_model=ApiResponse[list[ModeMappingItem]],
)
async def replace_mode_mapping_endpoint(
    loop_id: str,
    body: ModeMappingReplaceRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """全量替换回路投用定义（仅 ADMIN）。

    采用"先删后建"策略，保证幂等性。
    校验：
    - modeValue 必须为非负整数（ERR_MODE_MAPPING_INVALID）
    - modeLabel 必须在 {AUTO, CAS, REMOTE, APC, MANUAL} 中（ERR_MODE_MAPPING_INVALID）
    - 同一回路内 modeValue 不重复（ERR_MODE_MAPPING_DUPLICATE）
    """
    mappings = [m.model_dump(by_alias=False) for m in body.mappings]
    data = await replace_mode_mappings(
        db=db,
        loop_id=loop_id,
        operator=user.username,
        mappings=mappings,
    )
    return success(data=data, message="更新成功")


__all__ = ["router"]
