"""Loop ledger endpoints (IDS v3.2 §2.2.7~2.2.15).

路由顺序：固定路径（/monitor、/export、/import）必须在 {loop_id} 之前声明。

- GET    /api/v1/loops              — 分页查询回路列表
- POST   /api/v1/loops              — 创建回路
- GET    /api/v1/loops/monitor      — 回路监控列表
- GET    /api/v1/loops/export       — 导出回路 Excel
- POST   /api/v1/loops/import       — 批量导入回路 Excel
- GET    /api/v1/loops/{id}         — 回路详情
- PUT    /api/v1/loops/{id}         — 更新回路
- DELETE /api/v1/loops/{id}         — 删除回路
- GET    /api/v1/loops/{id}/tags    — 获取回路 Tag 关联状态
- PUT    /api/v1/loops/{id}/tags    — 批量更新 Tag 关联
- GET    /api/v1/loops/{id}/monitor — 回路运行详情
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.loop import (
    LoopCreate,
    LoopDeleteResult,
    LoopImportResult,
    LoopListData,
    LoopTagMappingResponse,
    LoopTagMappingUpdate,
    LoopTagMappingUpdateResponse,
    LoopUpdate,
    LoopUpdateResult,
)
from app.services.loop import (
    create_loop,
    delete_loop,
    export_loops,
    get_loop_detail,
    import_loops,
    list_loops,
    update_loop,
)
from app.services.monitor import get_loop_monitor_detail, list_loop_monitor
from app.services.tag_mapping import get_loop_tags, update_loop_tags

router = APIRouter(prefix="/loops", tags=["loop"])


# ---------------------------------------------------------------------------
# Loop CRUD (固定路径优先)
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[LoopListData])
async def list_loops_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    controlMode: str | None = Query(None, description="按控制方式筛选：Manual/Auto/Cascade"),
    isActive: bool | None = Query(None, description="按启用状态筛选"),
    status: str | None = Query(None, description="按回路状态筛选：READY/PARTIAL/INACTIVE"),
    keyword: str | None = Query(None, description="按回路位号/描述模糊查询"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """分页查询回路列表。"""
    data = await list_loops(
        db=db,
        plant_node_id=plantNodeId,
        control_mode=controlMode,
        is_active=isActive,
        status=status,
        keyword=keyword,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.post("", status_code=201, response_model=ApiResponse[dict])
async def create_loop_endpoint(
    body: LoopCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """创建回路。"""
    score_weights = None
    if body.scoreWeights is not None:
        score_weights = body.scoreWeights.model_dump()
    data = await create_loop(
        db=db,
        tag_name=body.tagName,
        description=body.description,
        unit_id=body.unitId,
        score_weights=score_weights,
        is_active=body.isActive,
        remark=body.remark,
        operator=user.username,
    )
    return success(data=data, message="创建成功")


# ---------------------------------------------------------------------------
# Loop Monitor (固定路径，必须在 {loop_id} 之前)
# ---------------------------------------------------------------------------


@router.get("/monitor", response_model=ApiResponse[dict])
async def list_loop_monitor_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    view: str = Query("list", description="视图模式：list/card"),
    keyword: str | None = Query(None, description="按回路位号/描述模糊查询"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """回路监控列表（含实时 PV/SP/OP/MODE 值、质量码、评分）。"""
    data = await list_loop_monitor(
        db=db,
        plant_node_id=plantNodeId,
        view=view,
        keyword=keyword,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# Loop Export / Import (固定路径，必须在 {loop_id} 之前)
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_loops_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    status: str | None = Query(None, description="按回路状态筛选：READY/PARTIAL/INACTIVE"),
    keyword: str | None = Query(None, description="按回路位号/描述模糊查询"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> StreamingResponse:
    """导出回路台账为 Excel 文件（.xlsx）。"""
    content = await export_loops(
        db=db,
        plant_node_id=plantNodeId,
        status=status,
        keyword=keyword,
    )
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=loops_export.xlsx",
        },
    )


@router.post("/import", response_model=ApiResponse[LoopImportResult])
async def import_loops_endpoint(
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """批量导入回路台账（Excel .xlsx）。

    逐行处理：回路编号已存在则更新，否则新建。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    file_bytes = await file.read()
    data = await import_loops(db=db, file_bytes=file_bytes, operator=user.username)
    return success(data=data, message="导入完成")


# ---------------------------------------------------------------------------
# Loop CRUD by ID
# ---------------------------------------------------------------------------


@router.get("/{loop_id}", response_model=ApiResponse[dict])
async def get_loop_detail_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取回路详情。"""
    data = await get_loop_detail(db=db, loop_id=loop_id)
    return success(data=data)


@router.put("/{loop_id}", response_model=ApiResponse[LoopUpdateResult])
async def update_loop_endpoint(
    loop_id: str,
    body: LoopUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """更新回路（描述/评分权重/启用状态/备注）。"""
    score_weights = None
    if body.scoreWeights is not None:
        score_weights = body.scoreWeights.model_dump()
    data = await update_loop(
        db=db,
        loop_id=loop_id,
        operator=user.username,
        description=body.description,
        score_weights=score_weights,
        is_active=body.isActive,
        remark=body.remark,
    )
    return success(data=data, message="更新成功")


@router.delete("/{loop_id}", response_model=ApiResponse[LoopDeleteResult])
async def delete_loop_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除回路（仅 ADMIN）。

    校验：回路有关联 Tag → ERR_LOOP_HAS_TAGS。
    """
    data = await delete_loop(db=db, loop_id=loop_id, operator=user.username)
    return success(data=data, message="删除成功")


# ---------------------------------------------------------------------------
# Loop Tag Mapping (S2-LOOP-005)
# ---------------------------------------------------------------------------


@router.get("/{loop_id}/tags", response_model=ApiResponse[LoopTagMappingResponse])
async def get_loop_tags_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取回路 7 个 Tag 槽位关联状态。"""
    data = await get_loop_tags(db=db, loop_id=loop_id)
    return success(data=data)


@router.put("/{loop_id}/tags", response_model=ApiResponse[LoopTagMappingUpdateResponse])
async def update_loop_tags_endpoint(
    loop_id: str,
    body: LoopTagMappingUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """批量更新回路 Tag 关联。

    PV/SP/OP/MODE 必填，缺失时 status→PARTIAL（API 调用成功，不报错）。
    PID_P/PID_I/PID_D 可选。
    全部必填为 null → ERR_LOOP_TAG_REQUIRED。
    Tag 不存在于 tag_registry → ERR_TAG_NOT_FOUND。
    """
    data = await update_loop_tags(
        db=db,
        loop_id=loop_id,
        operator=user.username,
        pv=body.pv,
        sp=body.sp,
        op=body.op,
        mode=body.mode,
        pid_p=body.pid_p,
        pid_i=body.pid_i,
        pid_d=body.pid_d,
    )
    return success(data=data, message="Tag 关联更新成功")


# ---------------------------------------------------------------------------
# Loop Monitor Detail (S2-LOOP-006)
# ---------------------------------------------------------------------------


@router.get("/{loop_id}/monitor", response_model=ApiResponse[dict])
async def get_loop_monitor_detail_endpoint(
    loop_id: str,
    trendWindow: str = Query(
        "last_24_hours", description="趋势数据时间窗：last_1_hour/last_24_hours/last_7_days"
    ),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """回路运行详情（7 Tag 当前值、PID 参数、波形数据）。"""
    data = await get_loop_monitor_detail(db=db, loop_id=loop_id, trend_window=trendWindow)
    return success(data=data)


__all__ = ["router"]
