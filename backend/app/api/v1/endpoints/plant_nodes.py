"""Plant node endpoints (IDS v3.2 §2.2.1~2.2.4).

路由顺序：固定路径（/list、/export、/import）必须在 {node_id} 之前声明。

- GET    /api/v1/plant-nodes          — List plant node tree
- GET    /api/v1/plant-nodes/list     — 工厂节点分页列表（工厂配置页）
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.api.upload_guard import read_excel_upload
from app.core.db import get_db
from app.models.plant_node import PlantNode
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.plant_node import (
    PlantNodeCreate,
    PlantNodeImportResult,
    PlantNodeInfo,
    PlantNodeListItem,
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
    parentId: uuid.UUID | None = Query(
        None, description="父节点 ID，不传则返回顶层节点及其完整子树"
    ),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取工厂层级树（递归 children）。"""
    tree = await list_plant_tree(db=db, parent_id=str(parentId) if parentId else None)
    return success(data=tree)


@router.get("/list", response_model=ApiResponse[dict])
async def list_plant_nodes_paged(
    keyword: str | None = Query(None, max_length=50, description="按名称模糊搜索"),
    nodeType: str | None = Query(None, description="按类型筛选：FACTORY/AREA/UNIT"),
    source: str | None = Query(
        None, description="按来源筛选：aas（AAS 同步节点）/ local（本地维护）"
    ),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """工厂节点分页列表（工厂配置页：含层级路径/父节点名/来源标记）。"""
    stmt = select(PlantNode)
    if keyword:
        stmt = stmt.where(PlantNode.name.ilike(f"%{keyword}%"))
    if nodeType:
        stmt = stmt.where(PlantNode.type == nodeType)
    if source == "aas":
        stmt = stmt.where(PlantNode.source_node_id.isnot(None))
    elif source == "local":
        stmt = stmt.where(PlantNode.source_node_id.is_(None))

    # 计数
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页（名称排序）
    rows = (
        (
            await db.execute(
                stmt.order_by(PlantNode.type, PlantNode.name)
                .offset((page - 1) * pageSize)
                .limit(pageSize)
            )
        )
        .scalars()
        .all()
    )

    # 全量 id→节点映射（构建路径与父名）
    all_nodes = (await db.execute(select(PlantNode))).scalars().all()
    node_map = {n.id: n for n in all_nodes}

    def build_path(node: PlantNode) -> str:
        parts: list[str] = []
        cur: PlantNode | None = node
        guard = 0
        while cur is not None and guard < 10:
            parts.insert(0, cur.name)
            cur = node_map.get(cur.parent_id) if cur.parent_id else None
            guard += 1
        return " / ".join(parts)

    items = [
        PlantNodeListItem(
            id=n.id,
            name=n.name,
            type=n.type,
            parentId=n.parent_id,
            parentName=(
                node_map[n.parent_id].name if n.parent_id and n.parent_id in node_map else None
            ),
            path=build_path(n),
            isKpiEnabled=n.is_kpi_enabled,
            sourceNodeId=n.source_node_id,
            updatedAt=n.updated_at.isoformat() if n.updated_at else None,
        )
        for n in rows
    ]
    return success(data={"items": [i.model_dump() for i in items], "total": total})


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
    file_bytes = await read_excel_upload(file)
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
