"""Dict item endpoints — 通用字典项（可配置枚举）.

路由顺序：固定路径（/types、/items）在 /{dict_type}/items 与 /items/{item_id} 之前。

- GET  /api/v1/dicts/types                        — 已注册字典类型（登录可读）
- GET  /api/v1/dicts/{dictType}/items             — 字典项列表（登录可读，下拉框用）
- GET  /api/v1/dicts/items?dictType=              — 分页管理列表（ADMIN，含引用标记）
- POST /api/v1/dicts/items                        — 新建（ADMIN）
- PUT  /api/v1/dicts/items/{itemId}               — 更新（ADMIN）
- DELETE /api/v1/dicts/items/{itemId}             — 删除（ADMIN，被引用时拒绝）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.dict_item import DictItemCreate, DictItemInfo, DictItemUpdate
from app.services.dict_item import (
    DICT_TYPE_TITLES,
    create_dict_item,
    delete_dict_item,
    get_dict_items,
    list_dict_items_paged,
    update_dict_item,
)

router = APIRouter(prefix="/dicts", tags=["dict"])


@router.get("/types", response_model=ApiResponse[list])
async def list_dict_types(
    _: SysUser = Depends(get_current_user),
) -> dict:
    """已注册字典类型（前端管理页下拉用）。"""
    data = [{"dictType": t, "title": title} for t, title in DICT_TYPE_TITLES.items()]
    return success(data=data)


@router.get("/items", response_model=ApiResponse[dict])
async def list_dict_items_endpoint(
    dictType: str = Query(..., max_length=50, description="字典类型编码"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """字典项分页管理列表（含 isReferenced 引用标记，仅 ADMIN）。"""
    data = await list_dict_items_paged(db=db, dict_type=dictType, page=page, page_size=pageSize)
    return success(data=data)


@router.get("/{dict_type}/items", response_model=ApiResponse[list])
async def get_dict_items_endpoint(
    dict_type: str,
    enabledOnly: bool = Query(True, description="仅启用项（下拉框默认）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """读取字典项 [(code, label)]（登录可读，前端下拉框用）。"""
    items = await get_dict_items(db, dict_type, enabled_only=enabledOnly)
    data = [{"itemCode": code, "itemLabel": label} for code, label in items]
    return success(data=data)


@router.post("/items", response_model=ApiResponse[DictItemInfo])
async def create_dict_item_endpoint(
    body: DictItemCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """新建字典项（仅 ADMIN）。"""
    data = await create_dict_item(
        db=db,
        operator=user.username,
        dict_type=body.dictType,
        item_code=body.itemCode,
        item_label=body.itemLabel,
        sort_order=body.sortOrder,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="字典项已创建")


@router.put("/items/{item_id}", response_model=ApiResponse[DictItemInfo])
async def update_dict_item_endpoint(
    item_id: str,
    body: DictItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新字典项（label/排序/启停，仅 ADMIN）。"""
    data = await update_dict_item(
        db=db,
        item_id=item_id,
        operator=user.username,
        item_label=body.itemLabel,
        sort_order=body.sortOrder,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="字典项已更新")


@router.delete("/items/{item_id}", response_model=ApiResponse[dict])
async def delete_dict_item_endpoint(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除字典项（仅 ADMIN；被业务数据引用时拒绝）。"""
    data = await delete_dict_item(db=db, item_id=item_id, operator=user.username)
    return success(data=data, message="字典项已删除")


__all__ = ["router"]
