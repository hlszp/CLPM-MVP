"""Report configuration endpoints (S5-SYS-003).

Routes:
- GET  /api/v1/reports/configs   — List report configs (ADMIN only)
- POST /api/v1/reports/configs   — Create report config (ADMIN only)
- PUT  /api/v1/reports/configs/{id} — Update report config (ADMIN only)
- POST /api/v1/reports/generate  — Trigger report generation (ADMIN only, async)
- GET  /api/v1/reports/tasks/{task_id} — Query report task status (ADMIN only)
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.report import (
    ReportConfigCreateRequest,
    ReportConfigItem,
    ReportConfigUpdateRequest,
    ReportGenerateData,
    ReportGenerateRequest,
)
from app.services.report import (
    create_config,
    get_task_status,
    list_configs,
    trigger_report_generation,
    update_config,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/configs", response_model=ApiResponse[list[ReportConfigItem]])
async def list_configs_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """获取报表配置列表（仅 ADMIN）。"""
    data = await list_configs(db)
    return success(data=data)


@router.post("/configs", status_code=201, response_model=ApiResponse[ReportConfigItem])
async def create_config_endpoint(
    body: ReportConfigCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建报表配置（仅 ADMIN）。"""
    data = await create_config(
        db=db,
        operator=user.username,
        name=body.name,
        report_period=body.reportPeriod,
        recipients=body.recipients,
        content_template=body.contentTemplate,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="报表配置创建成功")


@router.put("/configs/{config_id}", response_model=ApiResponse[ReportConfigItem])
async def update_config_endpoint(
    config_id: uuid.UUID,
    body: ReportConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新报表配置（仅 ADMIN）。"""
    data = await update_config(
        db=db,
        operator=user.username,
        config_id=str(config_id),
        name=body.name,
        report_period=body.reportPeriod,
        recipients=body.recipients,
        content_template=body.contentTemplate,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="报表配置更新成功")


@router.post("/generate", response_model=ApiResponse[ReportGenerateData])
async def generate_report_endpoint(
    body: ReportGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """手动触发报表生成（仅 ADMIN，异步任务，返回 taskId）。"""
    data = await trigger_report_generation(
        db=db,
        operator=user.username,
        config_id=body.configId,
        report_period=body.reportPeriod,
    )
    return success(data=data, message="任务已提交")


@router.get("/tasks/{task_id}", response_model=ApiResponse[dict])
async def get_task_status_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """查询报表任务状态（仅 ADMIN，用于前端轮询）。"""
    data = await get_task_status(db=db, task_id=str(task_id))
    return success(data=data)


__all__ = ["router"]
