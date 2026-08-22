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
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.report import (
    ReportBenefitData,
    ReportConfigCreateRequest,
    ReportConfigItem,
    ReportConfigUpdateRequest,
    ReportDiagnosisStatisticsData,
    ReportGenerateData,
    ReportGenerateRequest,
    ReportOverviewData,
)
from app.services.report import (
    create_config,
    get_task_status,
    list_configs,
    trigger_report_generation,
    update_config,
)
from app.services.report_stats import (
    default_report_window,
    get_benefit,
    get_diagnosis_statistics,
    get_overview,
)

router = APIRouter(prefix="/reports", tags=["reports"])


def _parse_date_range(
    start_date: str | None, end_date: str | None
) -> tuple[datetime | None, datetime | None]:
    """解析 YYYY-MM-DD 为 naive UTC 当日 0 点起 / 次日 0 点止（半开区间）。"""
    start = (
        datetime.combine(datetime.strptime(start_date, "%Y-%m-%d").date(), time.min)
        if start_date
        else None
    )
    end = (
        datetime.combine(datetime.strptime(end_date, "%Y-%m-%d").date(), time.min)
        + timedelta(days=1)
        if end_date
        else None
    )
    return start, end


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


# ---------------------------------------------------------------------------
# 统计报告聚合（IA 优化 P0，2026-08-22）
# ---------------------------------------------------------------------------


@router.get("/overview", response_model=ApiResponse[ReportOverviewData])
async def get_report_overview(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    stage: str = Query("S1", pattern="^(S1|S2|S3)$"),
    startDate: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    endDate: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    plantNodeId: str | None = Query(None),
) -> dict:
    """管理总览聚合（P0 仅返回 S1 数据，S2/S3 字段为 null）。"""
    start, end = _parse_date_range(startDate, endDate)
    if not start or not end:
        start, end = default_report_window()
    data = await get_overview(
        db,
        stage=stage,
        start_date=start,
        end_date=end,
        plant_node_id=plantNodeId,
    )
    return success(data=data)


@router.get(
    "/diagnosis-statistics",
    response_model=ApiResponse[ReportDiagnosisStatisticsData],
)
async def get_report_diagnosis_statistics(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    startDate: str | None = Query(None),
    endDate: str | None = Query(None),
    plantNodeId: str | None = Query(None),
) -> dict:
    """诊断统计（基于 DiagnosisRun 表，不复用旧 DiagnosisResult 导出）。"""
    start, end = _parse_date_range(startDate, endDate)
    data = await get_diagnosis_statistics(
        db, start_date=start, end_date=end, plant_node_id=plantNodeId
    )
    return success(data=data)


@router.get("/benefit", response_model=ApiResponse[ReportBenefitData])
async def get_report_benefit(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
    startDate: str | None = Query(None),
    endDate: str | None = Query(None),
    plantNodeId: str | None = Query(None),
) -> dict:
    """收益报告：整定前后 KPI 对比、自控率提升曲线、装置标杆（仅技术指标）。"""
    start, end = _parse_date_range(startDate, endDate)
    data = await get_benefit(db, start_date=start, end_date=end, plant_node_id=plantNodeId)
    return success(data=data)


__all__ = ["router"]
