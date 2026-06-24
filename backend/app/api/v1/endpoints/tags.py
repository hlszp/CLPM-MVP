"""Tag registry endpoints — 测点清单 (IDS §测点管理).

路由顺序：固定路径（/export、/import）必须在 {tag_id} 之前声明。

- GET    /api/v1/tags          — 分页查询测点列表
- GET    /api/v1/tags/export   — 导出测点 Excel
- POST   /api/v1/tags/import   — 批量导入测点 Excel
- GET    /api/v1/tags/{tagId}  — 测点详情
- PUT    /api/v1/tags/{tagId}  — 更新测点
- DELETE /api/v1/tags/{tagId}  — 删除测点
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.tag import (
    TagDeleteResult,
    TagDetail,
    TagImportResult,
    TagListData,
    TagUpdate,
)
from app.services.tag import (
    delete_tag,
    export_tags,
    get_tag_detail,
    import_tags,
    list_tags,
    update_tag,
)

router = APIRouter(prefix="/tags", tags=["tag"])


# ---------------------------------------------------------------------------
# Tag List (固定路径优先)
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[TagListData])
async def list_tags_endpoint(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, description="按位号模糊搜索"),
    measureType: str | None = Query(
        None,
        description="按测点类型筛选: TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/SPEED/OTHER",
    ),
    tagType: str | None = Query(
        None, description="按参数类型筛选: PV/SP/OP/MODE/PID_P/PID_I/PID_D/OTHER"
    ),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选，支持层级查询"),
    isLinked: bool | None = Query(None, description="按关联状态筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """分页查询测点列表。"""
    data = await list_tags(
        db=db,
        keyword=keyword,
        measure_type=measureType,
        tag_type=tagType,
        plant_node_id=plantNodeId,
        is_linked=isLinked,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# Tag Export / Import (固定路径，必须在 {tag_id} 之前)
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_tags_endpoint(
    keyword: str | None = Query(None, description="按位号模糊搜索"),
    measureType: str | None = Query(None, description="按测点类型筛选"),
    tagType: str | None = Query(None, description="按参数类型筛选"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选，支持层级查询"),
    isLinked: bool | None = Query(None, description="按关联状态筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> StreamingResponse:
    """导出测点清单为 Excel 文件（.xlsx）。"""
    content = await export_tags(
        db=db,
        keyword=keyword,
        measure_type=measureType,
        tag_type=tagType,
        plant_node_id=plantNodeId,
        is_linked=isLinked,
    )
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=tags_export.xlsx",
        },
    )


@router.post("/import", response_model=ApiResponse[TagImportResult])
async def import_tags_endpoint(
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """批量导入测点清单（Excel .xlsx）。

    逐行处理：位号已存在则更新，否则新建。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    file_bytes = await file.read()
    data = await import_tags(db=db, file_bytes=file_bytes, operator=user.username)
    return success(data=data, message="导入完成")


# ---------------------------------------------------------------------------
# Tag CRUD by ID
# ---------------------------------------------------------------------------


@router.get("/{tag_id}", response_model=ApiResponse[TagDetail])
async def get_tag_detail_endpoint(
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取测点详情。"""
    data = await get_tag_detail(db=db, tag_id=tag_id)
    return success(data=data)


@router.put("/{tag_id}", response_model=ApiResponse[TagDetail])
async def update_tag_endpoint(
    tag_id: str,
    body: TagUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """更新测点（描述/量程/单位/测点类型/TDengine tag ID）。"""
    data = await update_tag(
        db=db,
        tag_id=tag_id,
        operator=user.username,
        tag_description=body.tagDescription,
        range_min=body.rangeMin,
        range_max=body.rangeMax,
        unit=body.unit,
        measure_type=body.measureType,
        tdengine_tag_id=body.tdengineTagId,
    )
    return success(data=data, message="更新成功")


@router.delete("/{tag_id}", response_model=ApiResponse[TagDeleteResult])
async def delete_tag_endpoint(
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除测点（仅 ADMIN）。

    校验：已关联的测点不能删除（返回 ERR_TAG_LINKED）。
    """
    data = await delete_tag(db=db, tag_id=tag_id, operator=user.username)
    return success(data=data, message="删除成功")


__all__ = ["router"]
