"""Plant node endpoints (IDS v3.2 §2.2.1~2.2.4).

- GET    /api/v1/plant-nodes          — List plant node tree
- POST   /api/v1/plant-nodes          — Create plant node (ADMIN)
- PUT    /api/v1/plant-nodes/{nodeId} — Update plant node name (ADMIN)
- DELETE /api/v1/plant-nodes/{nodeId} — Delete plant node (ADMIN)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import success
from app.schemas.plant_node import PlantNodeCreate, PlantNodeUpdate
from app.services.plant_node import (
    create_plant_node,
    delete_plant_node,
    list_plant_tree,
    update_plant_node,
)

router = APIRouter(prefix="/plant-nodes", tags=["plant-node"])


@router.get("")
async def list_plant_nodes(
    parentId: str | None = Query(None, description="父节点 ID，不传则返回顶层节点及其完整子树"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取工厂层级树（递归 children）。"""
    tree = await list_plant_tree(db=db, parent_id=parentId)
    return success(data=tree)


@router.post("", status_code=201)
async def create_plant_node_endpoint(
    body: PlantNodeCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建工厂节点（仅 ADMIN）。"""
    data = await create_plant_node(
        db=db,
        name=body.name,
        node_type=body.type,
        parent_id=body.parentId,
        operator=user.username,
    )
    return success(data=data, message="创建成功")


@router.put("/{node_id}")
async def update_plant_node_endpoint(
    node_id: str,
    body: PlantNodeUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新工厂节点名称（仅 ADMIN）。"""
    data = await update_plant_node(
        db=db,
        node_id=node_id,
        name=body.name,
        operator=user.username,
    )
    return success(data=data, message="更新成功")


@router.delete("/{node_id}")
async def delete_plant_node_endpoint(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除工厂节点（仅 ADMIN）。

    校验：节点存在子节点 → ERR_NODE_HAS_CHILDREN；节点关联回路 → ERR_NODE_HAS_LOOPS。
    """
    data = await delete_plant_node(db=db, node_id=node_id, operator=user.username)
    return success(data=data, message="删除成功")


__all__ = ["router"]
