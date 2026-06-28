"""Plant node endpoints (IDS v3.2 §2.2.1~2.2.4).

路由顺序：固定路径（/export、/import）必须在 {node_id} 之前声明。

- GET    /api/v1/plant-nodes          — List plant node tree
- POST   /api/v1/plant-nodes          — Create plant node (ADMIN)
- GET    /api/v1/plant-nodes/export   — 导出工厂层级 Excel
- POST   /api/v1/plant-nodes/import   — 批量导入工厂层级 Excel
- PUT    /api/v1/plant-nodes/{nodeId} — Update plant node name (ADMIN)
- DELETE /api/v1/plant-nodes/{nodeId} — Delete plant node (ADMIN)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.plant_node import (
    PlantNodeCreate,
    PlantNodeImportResult,
    PlantNodeInfo,
    PlantNodeTree,
    PlantNodeUpdate,
)
from app.services.plant_node import (
    create_plant_node,
    delete_plant_node,
    export_plant_nodes,
    import_plant_nodes,
    list_plant_tree,
    update_plant_node,
)

router = APIRouter(prefix="/plant-nodes", tags=["plant-node"])


@router.get("", response_model=ApiResponse[list[PlantNodeTree]])
async def list_plant_nodes(
    parentId: uuid.UUID | None = Query(None, description="父节点 ID，不传则返回顶层节点及其完整子树"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取工厂层级树（递归 children）。"""
    tree = await list_plant_tree(db=db, parent_id=str(parentId) if parentId else None)
    return success(data=tree)


@router.post("", status_code=201, response_model=ApiResponse[PlantNodeInfo])
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


# ---------------------------------------------------------------------------
# Plant Node Export / Import (固定路径，必须在 {node_id} 之前)
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_plant_nodes_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> StreamingResponse:
    """导出工厂层级为 Excel 文件（.xlsx）。

    列结构（4 列）：节点名称 / 节点类型 / 父节点名称 / 层级路径。
    """
    content = await export_plant_nodes(db=db)
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=plant_nodes_export.xlsx",
        },
    )


@router.post("/import", response_model=ApiResponse[PlantNodeImportResult])
async def import_plant_nodes_endpoint(
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """批量导入工厂层级（Excel .xlsx）。

    逐行处理：节点名称 + 父节点已存在则更新，否则新建。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    file_bytes = await file.read()
    data = await import_plant_nodes(db=db, file_bytes=file_bytes, operator=user.username)
    return success(data=data, message="导入完成")


# ---------------------------------------------------------------------------
# Plant Node CRUD by ID
# ---------------------------------------------------------------------------


@router.put("/{node_id}", response_model=ApiResponse[PlantNodeInfo])
async def update_plant_node_endpoint(
    node_id: uuid.UUID,
    body: PlantNodeUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新工厂节点（名称 + 是否纳入性能评估，仅 ADMIN）。"""
    data = await update_plant_node(
        db=db,
        node_id=str(node_id),
        name=body.name,
        operator=user.username,
        is_kpi_enabled=body.isKpiEnabled,
    )
    return success(data=data, message="更新成功")


@router.delete("/{node_id}", response_model=ApiResponse[dict])
async def delete_plant_node_endpoint(
    node_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除工厂节点（仅 ADMIN）。

    校验：节点存在子节点 → ERR_NODE_HAS_CHILDREN；节点关联回路 → ERR_NODE_HAS_LOOPS。
    """
    data = await delete_plant_node(db=db, node_id=str(node_id), operator=user.username)
    return success(data=data, message="删除成功")


__all__ = ["router"]
