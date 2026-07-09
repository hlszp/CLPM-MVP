"""DCS 配置 endpoints（品牌/型号/MODE 定义/映射矩阵）.

对齐 DDS §3.1 / 算法说明 §4.0.3，配置驱动的 DCS 管理。

路由清单：
- GET    /api/v1/dcs/vendors                    — 获取全部品牌（所有认证用户）
- POST   /api/v1/dcs/vendors                    — 创建品牌（仅 ADMIN）
- GET    /api/v1/dcs/vendors/export             — 导出品牌 Excel（所有认证用户）
- POST   /api/v1/dcs/vendors/import             — 批量导入品牌 Excel（仅 ADMIN）
- PUT    /api/v1/dcs/vendors/{vendor_id}        — 更新品牌（仅 ADMIN）
- DELETE /api/v1/dcs/vendors/{vendor_id}        — 删除品牌（仅 ADMIN）
- GET    /api/v1/dcs/models                     — 获取全部型号（所有认证用户）
- POST   /api/v1/dcs/models                     — 创建型号（仅 ADMIN）
- GET    /api/v1/dcs/models/export              — 导出型号 Excel（所有认证用户）
- POST   /api/v1/dcs/models/import              — 批量导入型号 Excel（仅 ADMIN）
- PUT    /api/v1/dcs/models/{model_id}          — 更新型号（仅 ADMIN）
- DELETE /api/v1/dcs/models/{model_id}         — 删除型号（仅 ADMIN）
- GET    /api/v1/dcs/mode-definitions           — 获取全部标准 MODE 定义
- PUT    /api/v1/dcs/mode-definitions/{standard_mode} — 更新 MODE 定义（仅 ADMIN）
- GET    /api/v1/dcs/mode-mappings              — 获取 MODE 映射列表
- POST   /api/v1/dcs/mode-mappings              — 创建/更新 MODE 映射（仅 ADMIN）
- DELETE /api/v1/dcs/mode-mappings/{mapping_id} — 删除 MODE 映射（仅 ADMIN）
- GET    /api/v1/dcs/mode-matrix                — 获取 MODE 映射矩阵视图
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.dcs_config import (
    DcsImportResult,
    DcsModelCreate,
    DcsModelItem,
    DcsModelUpdate,
    DcsModeMappingCreate,
    DcsModeMappingItem,
    DcsVendorCreate,
    DcsVendorItem,
    DcsVendorUpdate,
    ModeDefinitionItem,
    ModeDefinitionUpdate,
    ModeMatrixView,
)
from app.services.dcs_config import (
    create_model as svc_create_model,
)
from app.services.dcs_config import (
    create_vendor as svc_create_vendor,
)
from app.services.dcs_config import (
    delete_mode_mapping as svc_delete_mapping,
)
from app.services.dcs_config import (
    delete_model as svc_delete_model,
)
from app.services.dcs_config import (
    delete_vendor as svc_delete_vendor,
)
from app.services.dcs_config import (
    export_models as svc_export_models,
)
from app.services.dcs_config import (
    export_vendors as svc_export_vendors,
)
from app.services.dcs_config import (
    get_mode_matrix as svc_get_mode_matrix,
)
from app.services.dcs_config import (
    import_models as svc_import_models,
)
from app.services.dcs_config import (
    import_vendors as svc_import_vendors,
)
from app.services.dcs_config import (
    list_mode_definitions as svc_list_mode_definitions,
)
from app.services.dcs_config import (
    list_mode_mappings as svc_list_mappings,
)
from app.services.dcs_config import (
    list_models as svc_list_models,
)
from app.services.dcs_config import (
    list_vendors as svc_list_vendors,
)
from app.services.dcs_config import (
    update_mode_definition as svc_update_mode_definition,
)
from app.services.dcs_config import (
    update_model as svc_update_model,
)
from app.services.dcs_config import (
    update_vendor as svc_update_vendor,
)
from app.services.dcs_config import (
    upsert_mode_mapping as svc_upsert_mapping,
)

router = APIRouter(prefix="/dcs", tags=["dcs"])


# ---------------------------------------------------------------------------
# DcsVendor（DCS 品牌）
# ---------------------------------------------------------------------------


@router.get("/vendors", response_model=ApiResponse[list[DcsVendorItem]])
async def list_vendors_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取全部 DCS 品牌（所有认证用户可读）。"""
    data = await svc_list_vendors(db)
    return success(data=data)


@router.post("/vendors", response_model=ApiResponse[DcsVendorItem])
async def create_vendor_endpoint(
    body: DcsVendorCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建 DCS 品牌（仅 ADMIN）。code 唯一。"""
    data = await svc_create_vendor(
        db=db,
        code=body.code,
        name=body.name,
        name_en=body.name_en,
        description=body.description,
        sort_order=body.sort_order,
        operator=user.username,
    )
    return success(data=data, message="创建成功")


@router.get("/vendors/export")
async def export_vendors_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> StreamingResponse:
    """导出全部 DCS 品牌为 Excel 文件（.xlsx）。"""
    content = await svc_export_vendors(db)
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=dcs_vendors_export.xlsx",
        },
    )


@router.post("/vendors/import", response_model=ApiResponse[DcsImportResult])
async def import_vendors_endpoint(
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """批量导入 DCS 品牌（Excel .xlsx）。

    逐行处理：品牌代码已存在则更新，否则新建。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    file_bytes = await file.read()
    data = await svc_import_vendors(db=db, file_bytes=file_bytes, operator=user.username)
    return success(data=data, message="导入完成")


@router.put("/vendors/{vendor_id}", response_model=ApiResponse[DcsVendorItem])
async def update_vendor_endpoint(
    vendor_id: str,
    body: DcsVendorUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新 DCS 品牌（仅 ADMIN，code 不可改）。"""
    data = await svc_update_vendor(
        db=db,
        vendor_id=vendor_id,
        name=body.name,
        name_en=body.name_en,
        description=body.description,
        sort_order=body.sort_order,
        is_active=body.is_active,
        operator=user.username,
    )
    return success(data=data, message="更新成功")


@router.delete("/vendors/{vendor_id}", response_model=ApiResponse[dict])
async def delete_vendor_endpoint(
    vendor_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除 DCS 品牌（仅 ADMIN，有关联型号时禁止删除）。"""
    await svc_delete_vendor(db=db, vendor_id=vendor_id, operator=user.username)
    return success(data={"deleted": True}, message="删除成功")


# ---------------------------------------------------------------------------
# DcsModel（DCS 型号，全局唯一 code）
# ---------------------------------------------------------------------------


@router.get("/models", response_model=ApiResponse[list[DcsModelItem]])
async def list_models_endpoint(
    vendor_id: str | None = Query(None, description="按品牌筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取全部 DCS 型号（所有认证用户可读，可按品牌筛选）。"""
    data = await svc_list_models(db=db, vendor_id=vendor_id)
    return success(data=data)


@router.post("/models", response_model=ApiResponse[DcsModelItem])
async def create_model_endpoint(
    body: DcsModelCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建 DCS 型号（仅 ADMIN）。code 全局唯一。"""
    data = await svc_create_model(
        db=db,
        vendor_id=body.vendor_id,
        code=body.code,
        name=body.name,
        description=body.description,
        sort_order=body.sort_order,
        operator=user.username,
    )
    return success(data=data, message="创建成功")


@router.get("/models/export")
async def export_models_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> StreamingResponse:
    """导出全部 DCS 型号为 Excel 文件（.xlsx）。"""
    content = await svc_export_models(db)
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=dcs_models_export.xlsx",
        },
    )


@router.post("/models/import", response_model=ApiResponse[DcsImportResult])
async def import_models_endpoint(
    file: UploadFile = File(..., description="Excel 文件 (.xlsx)"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """批量导入 DCS 型号（Excel .xlsx）。

    逐行处理：型号代码已存在则更新，否则新建。
    通过品牌代码查找 vendor_id（品牌必须存在）。
    返回 {total, inserted, updated, failed, errors[]}。
    """
    file_bytes = await file.read()
    data = await svc_import_models(db=db, file_bytes=file_bytes, operator=user.username)
    return success(data=data, message="导入完成")


@router.put("/models/{model_id}", response_model=ApiResponse[DcsModelItem])
async def update_model_endpoint(
    model_id: str,
    body: DcsModelUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新 DCS 型号（仅 ADMIN，code/vendor_id 不可改）。"""
    data = await svc_update_model(
        db=db,
        model_id=model_id,
        name=body.name,
        description=body.description,
        sort_order=body.sort_order,
        is_active=body.is_active,
        operator=user.username,
    )
    return success(data=data, message="更新成功")


@router.delete("/models/{model_id}", response_model=ApiResponse[dict])
async def delete_model_endpoint(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除 DCS 型号（仅 ADMIN，级联删除映射，loop_ledger.dcs_model_id SET NULL）。"""
    await svc_delete_model(db=db, model_id=model_id, operator=user.username)
    return success(data={"deleted": True}, message="删除成功")


# ---------------------------------------------------------------------------
# ModeDefinition（标准 MODE 定义）
# ---------------------------------------------------------------------------


@router.get("/mode-definitions", response_model=ApiResponse[list[ModeDefinitionItem]])
async def list_mode_definitions_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取全部标准 MODE 定义（所有认证用户可读）。"""
    data = await svc_list_mode_definitions(db)
    return success(data=data)


@router.put("/mode-definitions/{standard_mode}", response_model=ApiResponse[ModeDefinitionItem])
async def update_mode_definition_endpoint(
    standard_mode: int,
    body: ModeDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新标准 MODE 定义（仅 ADMIN，standard_mode 不可改）。

    is_auto 字段影响自控率计算：修改后实时自控率与历史 KPI 计算口径会变化。
    """
    data = await svc_update_mode_definition(
        db=db,
        standard_mode=standard_mode,
        label_zh=body.label_zh,
        label_en=body.label_en,
        is_auto=body.is_auto,
        color=body.color,
        description=body.description,
        operator=user.username,
    )
    return success(data=data, message="更新成功")


# ---------------------------------------------------------------------------
# DcsModeMapping（MODE 映射矩阵）
# ---------------------------------------------------------------------------


@router.get("/mode-mappings", response_model=ApiResponse[list[DcsModeMappingItem]])
async def list_mode_mappings_endpoint(
    dcs_model_id: str | None = Query(None, description="按型号筛选（含本系统默认）"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取 MODE 映射列表（所有认证用户可读，可按型号筛选）。"""
    data = await svc_list_mappings(db=db, dcs_model_id=dcs_model_id)
    return success(data=data)


@router.post("/mode-mappings", response_model=ApiResponse[DcsModeMappingItem])
async def upsert_mode_mapping_endpoint(
    body: DcsModeMappingCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建或更新 MODE 映射（仅 ADMIN，按 dcs_model_id+standard_mode 幂等）。

    dcs_model_id 为 null 时表示本系统默认映射。
    """
    data = await svc_upsert_mapping(
        db=db,
        dcs_model_id=body.dcs_model_id,
        standard_mode=body.standard_mode,
        raw_mode_value=body.raw_mode_value,
        description=body.description,
        operator=user.username,
    )
    return success(data=data, message="保存成功")


@router.delete("/mode-mappings/{mapping_id}", response_model=ApiResponse[dict])
async def delete_mode_mapping_endpoint(
    mapping_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除 MODE 映射（仅 ADMIN）。"""
    await svc_delete_mapping(db=db, mapping_id=mapping_id, operator=user.username)
    return success(data={"deleted": True}, message="删除成功")


# ---------------------------------------------------------------------------
# MODE 映射矩阵视图
# ---------------------------------------------------------------------------


@router.get("/mode-matrix", response_model=ApiResponse[ModeMatrixView])
async def get_mode_matrix_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取 MODE 映射矩阵视图（行=标准 MODE，列=各型号，第一列为本系统默认）。

    用于前端矩阵表展示：表头为各型号，行为标准 MODE 值，
    单元格为该型号的实际 MODE 值。
    """
    data = await svc_get_mode_matrix(db)
    return success(data=data)


__all__ = ["router"]
