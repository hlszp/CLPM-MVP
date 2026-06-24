"""Diagnosis center endpoints (IDS v3.2 §2.4 — S4-DIAG-001~006 + SVC-11/12/13).

路由清单：
- GET    /api/v1/diagnosis/metrics                       — 获取诊断指标配置列表
- PUT    /api/v1/diagnosis/metrics/{diagId}              — 更新诊断指标配置（仅 ADMIN）
- GET    /api/v1/diagnosis/list                          — 诊断列表（分页 + 筛选）
- GET    /api/v1/diagnosis/{loopId}                      — 诊断详情
- GET    /api/v1/diagnosis/{loopId}/recommendations      — 获取解决方案推荐（SVC-11）
- POST   /api/v1/diagnosis/{loopId}/report               — 生成并下载 PDF 建议书（SVC-12）
- GET    /api/v1/diagnosis/statistics/export             — 导出诊断统计 CSV（SVC-13）
- PATCH  /api/v1/tracker/{loopId}/status                 — 更新处理状态（仅 IC_ENGINEER）
- POST   /api/v1/tracker/{loopId}/export                 — 导出诊断建议书 PDF
- GET    /api/v1/diagnosis/analytics                     — 诊断统计报表
- POST   /api/v1/diagnosis/analytics/export              — 导出统计报表
- GET    /api/v1/timeseries/{loopId}/waveform            — 波形数据
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.diagnosis import (
    AnalyticsExportData,
    AnalyticsExportRequest,
    DiagnosisAnalyticsData,
    DiagnosisConfigItem,
    DiagnosisConfigUpdate,
    DiagnosisListData,
    DiagnosisReportRequest,
    RecommendationData,
    TrackerExportData,
    TrackerStatusData,
    TrackerStatusUpdate,
    WaveformData,
)
from app.services.diagnosis import (
    get_diagnosis_analytics,
    get_diagnosis_detail,
    list_diagnosis,
    list_diagnosis_configs,
    update_diagnosis_config,
)
from app.services.diagnosis_recommendation import (
    get_recommendations,
    get_recommendations_for_loop,
)
from app.services.diagnosis_report import (
    export_diagnosis_statistics,
    generate_diagnosis_report,
)
from app.services.tracker import export_tracker_pdf, update_tracker_status
from app.services.waveform import get_waveform

# 诊断中心路由
router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])

# 波形路由（独立前缀）
timeseries_router = APIRouter(prefix="/timeseries", tags=["timeseries"])

# Tracker 路由（独立前缀）
tracker_router = APIRouter(prefix="/tracker", tags=["tracker"])


# ---------------------------------------------------------------------------
# S4-DIAG-001: 诊断指标配置 API
# ---------------------------------------------------------------------------


@router.get("/metrics", response_model=ApiResponse[list[DiagnosisConfigItem]])
async def list_metrics_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取诊断指标配置列表（所有角色可查看）。"""
    data = await list_diagnosis_configs(db)
    return success(data=data)


@router.put("/metrics/{diag_id}", response_model=ApiResponse[DiagnosisConfigItem])
async def update_metric_endpoint(
    diag_id: str,
    body: DiagnosisConfigUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新诊断指标配置（仅 ADMIN）。"""
    data = await update_diagnosis_config(
        db=db,
        diag_id=diag_id,
        operator=user.username,
        diag_name=body.diagName,
        algorithm_type=body.algorithmType,
        calc_method=body.calcMethod,
        params=body.params,
        threshold=body.threshold,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="更新成功")


# ---------------------------------------------------------------------------
# S4-DIAG-003: 诊断列表与详情 API
# ---------------------------------------------------------------------------


@router.get("/list", response_model=ApiResponse[DiagnosisListData])
async def list_diagnosis_endpoint(
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    diagnosisLabel: str | None = Query(None, description="按诊断标签筛选"),
    actionStatus: str | None = Query(None, description="按处理状态筛选"),
    timeWindow: str | None = Query(
        None, description="时间窗：last_24_hours/last_7_days/last_30_days"
    ),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """诊断列表（分页，支持 plantNodeId/diagnosisLabel/actionStatus/timeWindow 筛选）。"""
    data = await list_diagnosis(
        db=db,
        plant_node_id=plantNodeId,
        diagnosis_label=diagnosisLabel,
        action_status=actionStatus,
        time_window=timeWindow,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.get("/analytics", response_model=ApiResponse[DiagnosisAnalyticsData])
async def get_analytics_endpoint(
    startTime: str = Query(..., description="开始时间（ISO 8601）"),
    endTime: str = Query(..., description="结束时间（ISO 8601）"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    diagnosisLabel: str | None = Query(None, description="按诊断标签筛选"),
    actionStatus: str | None = Query(None, description="按处理状态筛选"),
    granularity: str = Query("day", description="粒度：hour/day/week/month"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """诊断统计报表（标签分布/效率趋势/闭环时长分布）。"""
    data = await get_diagnosis_analytics(
        db=db,
        start_time=startTime,
        end_time=endTime,
        plant_node_id=plantNodeId,
        diagnosis_label=diagnosisLabel,
        action_status=actionStatus,
        granularity=granularity,
    )
    return success(data=data)


@router.post("/analytics/export", response_model=ApiResponse[AnalyticsExportData])
async def export_analytics_endpoint(
    body: AnalyticsExportRequest,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """导出统计报表（异步任务，返回 taskId）。"""
    # Phase 1: 返回模拟任务 ID
    import uuid

    task_id = str(uuid.uuid4())
    data = {"taskId": task_id, "status": "PENDING"}
    return success(data=data, message="导出任务已提交")


# ---------------------------------------------------------------------------
# SVC-13: 诊断统计 CSV 导出
# ---------------------------------------------------------------------------


@router.get("/statistics/export")
async def export_statistics_csv_endpoint(
    startDate: str = Query(..., description="开始日期（ISO 8601）"),
    endDate: str = Query(..., description="结束日期（ISO 8601）"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> Response:
    """导出诊断统计 CSV（SVC-13）。

    统计各标签数量、分布、趋势，返回 CSV 文件（UTF-8 with BOM）。
    """
    csv_bytes = await export_diagnosis_statistics(
        db=db,
        start_date=startDate,
        end_date=endDate,
        plant_node_id=plantNodeId,
    )
    filename = f"diagnosis_statistics_{startDate[:10]}_{endDate[:10]}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{loop_id}", response_model=ApiResponse[dict])
async def get_diagnosis_detail_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """诊断详情（含 8 类标签数组 + 证据链 + 特征值）。"""
    data = await get_diagnosis_detail(db=db, loop_id=loop_id)
    return success(data=data)


# ---------------------------------------------------------------------------
# SVC-11: 诊断解决方案推荐
# ---------------------------------------------------------------------------


@router.get(
    "/{loop_id}/recommendations",
    response_model=ApiResponse[RecommendationData],
)
async def get_recommendations_endpoint(
    loop_id: str,
    tagCodes: str | None = Query(
        None,
        description="诊断标签列表（逗号分隔，可选。不传则从数据库读取该回路最新诊断标签）",
    ),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取解决方案推荐（SVC-11）。

    根据诊断标签返回标准化解决方案推荐，每条建议包含优先级、行动项、描述和目标模块。
    """
    if tagCodes:
        tag_list = [t.strip() for t in tagCodes.split(",") if t.strip()]
        data = get_recommendations(loop_id, tag_list)
    else:
        data = await get_recommendations_for_loop(db=db, loop_id=loop_id)
    return success(data=data)


# ---------------------------------------------------------------------------
# SVC-12: 诊断建议书 PDF 生成
# ---------------------------------------------------------------------------


@router.post("/{loop_id}/report")
async def generate_report_endpoint(
    loop_id: str,
    body: DiagnosisReportRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(
        require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")
    ),
) -> Response:
    """生成并下载 PDF 建议书（SVC-12）。

    内容：回路信息 + 诊断结果 + 性能指标 + 可能原因 + 解决方案推荐 + 生成时间。
    返回 PDF 文件 bytes。
    """
    # 1. 获取诊断详情作为快照数据
    snapshot_data = await get_diagnosis_detail(db=db, loop_id=loop_id)

    # 2. 获取推荐方案
    if body and body.tag_codes:
        recommendations = get_recommendations(loop_id, body.tag_codes)
    else:
        recommendations = await get_recommendations_for_loop(db=db, loop_id=loop_id)

    # 3. 生成 PDF
    pdf_bytes = generate_diagnosis_report(
        loop_id=loop_id,
        snapshot_data=snapshot_data,
        recommendations=recommendations,
    )

    filename = f"diagnosis_report_{loop_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# S4-DIAG-004: 波形查询 API
# ---------------------------------------------------------------------------


@timeseries_router.get("/{loop_id}/waveform", response_model=ApiResponse[WaveformData])
async def get_waveform_endpoint(
    loop_id: str,
    startTime: str = Query(..., description="开始时间（ISO 8601）"),
    endTime: str = Query(..., description="结束时间（ISO 8601）"),
    maxPoints: int = Query(5000, ge=100, le=50000, description="最大数据点数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """波形数据（含 PV 质量码 + LTTB 降采样）。

    - PV 质量码为 Bad 时，pv 值为 null
    - 超过 maxPoints 触发 LTTB 降采样
    - 时间窗超过 30 天返回 ERR_TS_001
    """
    data = await get_waveform(
        db=db,
        loop_id=loop_id,
        start_time=startTime,
        end_time=endTime,
        max_points=maxPoints,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# S4-DIAG-005: Action Tracker API
# ---------------------------------------------------------------------------


@tracker_router.patch("/{loop_id}/status", response_model=ApiResponse[TrackerStatusData])
async def update_tracker_status_endpoint(
    loop_id: str,
    body: TrackerStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("IC_ENGINEER")),
) -> dict:
    """更新处理状态（仅 IC_ENGINEER）。

    - status 枚举: PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED
    - 标记 IMPLEMENTED 后自动生成 A/B 对比视图
    """
    data = await update_tracker_status(
        db=db,
        loop_id=loop_id,
        operator=user.username,
        status=body.status,
        evidence_url=body.evidenceUrl,
        remark=body.remark,
    )
    return success(data=data, message="状态更新成功")


@tracker_router.post("/{loop_id}/export", response_model=ApiResponse[TrackerExportData])
async def export_tracker_endpoint(
    loop_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("IC_ENGINEER", "ADMIN", "PE_ENGINEER")),
) -> dict:
    """导出诊断建议书 PDF（异步任务，返回 taskId）。"""
    data = await export_tracker_pdf(db=db, loop_id=loop_id)
    return success(data=data, message="导出任务已提交")


__all__ = ["router", "timeseries_router", "tracker_router"]
