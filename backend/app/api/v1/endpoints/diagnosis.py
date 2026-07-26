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
- GET    /api/v1/diagnosis/tags                          — 查询诊断标签列表（IDS §2.4.10）
- GET    /api/v1/diagnosis/tags/{loopId}                 — 查询回路诊断标签（IDS §2.4.11）
- PUT    /api/v1/diagnosis/tags/{tagId}/resolve          — 处理诊断标签（IDS §2.4.12）
- POST   /api/v1/diagnosis/trigger                       — 触发诊断任务（手动，支持批量）
- GET    /api/v1/diagnosis/tasks                         — 诊断任务列表（未归档）
- GET    /api/v1/diagnosis/tasks/{taskId}                — 诊断任务详情
- POST   /api/v1/diagnosis/tasks/{taskId}/archive        — 归档诊断任务
- POST   /api/v1/diagnosis/tasks/{taskId}/cancel         — 取消诊断任务
- GET    /api/v1/diagnosis/records                       — 诊断记录列表（已归档）
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisTag
from app.models.loop import LoopLedger
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.diagnosis import (
    AnalyticsExportData,
    AnalyticsExportRequest,
    ConfigChangeCreateRequest,
    ConfigChangeRequestItem,
    ConfigChangeReviewRequest,
    DiagnosisAlgorithmMetaList,
    DiagnosisAnalyticsData,
    DiagnosisConfigItem,
    DiagnosisConfigUpdate,
    DiagnosisListData,
    DiagnosisRecordListData,
    DiagnosisReportRequest,
    DiagnosisRuleItem,
    DiagnosisRuleUpdate,
    DiagnosisTagListResponse,
    DiagnosisTagSchema,
    DiagnosisTaskDetail,
    DiagnosisTaskListData,
    DiagnosisThresholdRollbackRequest,
    DiagnosisThresholdVersionItem,
    DiagnosisTriggerData,
    DiagnosisTriggerRequest,
    RecommendationData,
    TagResolveRequest,
    ThresholdOverrideItem,
    ThresholdOverrideUpsert,
    TrackerEffectivenessData,
    TrackerStatusData,
    TrackerStatusUpdate,
    TrackerVerificationConfig,
    WaveformData,
)
from app.services.diagnosis import (
    archive_diagnosis_task,
    cancel_diagnosis_task,
    delete_diagnosis_task,
    get_diagnosis_analytics,
    get_diagnosis_detail,
    get_diagnosis_task_detail,
    get_diagnosis_visualization,
    list_algorithm_meta,
    list_diagnosis,
    list_diagnosis_configs,
    list_diagnosis_records,
    list_diagnosis_tasks,
    run_diagnosis_task,
    trigger_diagnosis,
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
from app.services.diagnosis_rule import list_rules, update_rule
from app.services.diagnosis_threshold import (
    delete_override,
    list_templates,
    upsert_override,
)
from app.services.diagnosis_threshold import (
    list_overrides as list_threshold_overrides,
)
from app.services.tracker import (
    export_tracker_pdf,
    get_ab_compare,
    get_tracker_effectiveness,
    get_verification_config,
    update_tracker_status,
    update_verification_config,
)
from app.services.waveform import get_waveform

logger = logging.getLogger(__name__)

# 诊断中心路由
router = APIRouter(prefix="/diagnosis", tags=["diagnosis"])

# 波形路由（独立前缀）
timeseries_router = APIRouter(prefix="/timeseries", tags=["timeseries"])

# Tracker 路由（独立前缀）
tracker_router = APIRouter(prefix="/tracker", tags=["tracker"])

# 诊断标签路由（独立前缀，IDS §2.4.10-2.4.12）
tags_router = APIRouter(prefix="/diagnosis/tags", tags=["diagnosis-tags"])


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
# C2: 专家规则引擎 API
# ---------------------------------------------------------------------------


@router.get("/rules", response_model=ApiResponse[list[DiagnosisRuleItem]])
async def list_rules_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取专家规则列表（所有角色可查看）。"""
    data = await list_rules(db)
    return success(data=data)


@router.put("/rules/{rule_id}", response_model=ApiResponse[DiagnosisRuleItem])
async def update_rule_endpoint(
    rule_id: str,
    body: DiagnosisRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新专家规则配置（仅 ADMIN）。"""
    data = await update_rule(
        db=db,
        rule_id=rule_id,
        operator=user.username,
        rule_name=body.ruleName,
        condition_expr=body.conditionExpr,
        action_type=body.actionType,
        action_params=body.actionParams,
        priority=body.priority,
        is_enabled=body.isEnabled,
    )
    return success(data=data, message="更新成功")


# ---------------------------------------------------------------------------
# C3: 差异化阈值覆盖 API
# ---------------------------------------------------------------------------


@router.get("/threshold-overrides", response_model=ApiResponse[list[ThresholdOverrideItem]])
async def list_threshold_overrides_endpoint(
    scopeType: str | None = Query(None, description="覆盖范围：loop_type/plant/loop"),
    scopeId: str | None = Query(None, description="范围标识"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取阈值覆盖列表（可按 scope 筛选，所有角色可查看）。"""
    data = await list_threshold_overrides(db, scope_type=scopeType, scope_id=scopeId)
    return success(data=data)


@router.get("/threshold-templates", response_model=ApiResponse[list[ThresholdOverrideItem]])
async def list_threshold_templates_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取控制类型模板列表（loop_type scope 的预置阈值模板）。"""
    data = await list_templates(db)
    return success(data=data)


@router.post(
    "/threshold-overrides",
    response_model=ApiResponse[ThresholdOverrideItem],
)
async def upsert_threshold_override_endpoint(
    body: ThresholdOverrideUpsert,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建或更新阈值覆盖（仅 ADMIN）。"""
    data = await upsert_override(
        db=db,
        operator=user.username,
        diag_code=body.diagCode,
        scope_type=body.scopeType,
        scope_id=body.scopeId,
        threshold=body.threshold,
    )
    return success(data=data, message="阈值覆盖已保存")


@router.delete("/threshold-overrides/{override_id}")
async def delete_threshold_override_endpoint(
    override_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除阈值覆盖（仅 ADMIN）。"""
    await delete_override(db, override_id, user.username)
    return success(data=None, message="阈值覆盖已删除")


# ---------------------------------------------------------------------------
# C4: 配置版本与回滚 API
# ---------------------------------------------------------------------------


@router.get(
    "/metrics/{diag_id}/versions",
    response_model=ApiResponse[list[DiagnosisThresholdVersionItem]],
)
async def list_threshold_versions_endpoint(
    diag_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取诊断配置的版本历史（从 sys_audit_log 读取，所有角色可查看）。"""
    from app.services.diagnosis_threshold import list_config_versions

    data = await list_config_versions(db, diag_id)
    return success(data=data)


@router.post(
    "/metrics/{diag_id}/rollback",
    response_model=ApiResponse[DiagnosisConfigItem],
)
async def rollback_threshold_endpoint(
    diag_id: str,
    body: DiagnosisThresholdRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """回滚诊断配置到指定版本（仅 ADMIN）。"""
    from app.services.diagnosis_threshold import rollback_config

    data = await rollback_config(db, diag_id, body.auditLogId, user.username)
    return success(data=data, message="已回滚到指定版本")


# ---------------------------------------------------------------------------
# C5: 关键配置审批流 API
# ---------------------------------------------------------------------------


@router.get("/config-changes", response_model=ApiResponse[list[ConfigChangeRequestItem]])
async def list_config_changes_endpoint(
    status: str | None = Query(None, description="状态筛选：PENDING/APPROVED/REJECTED"),
    targetType: str | None = Query(None, description="目标类型：config/rule/trigger"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取关键配置变更请求列表（所有角色可查看）。"""
    from app.services.diagnosis_approval import list_change_requests

    data = await list_change_requests(db, status=status, target_type=targetType)
    return success(data=data)


@router.post(
    "/config-changes",
    response_model=ApiResponse[ConfigChangeRequestItem],
)
async def create_config_change_endpoint(
    body: ConfigChangeCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建关键配置变更请求（仅 ADMIN，需第二人审批后生效）。"""
    from app.services.diagnosis_approval import create_change_request

    data = await create_change_request(
        db=db,
        operator=user.username,
        target_type=body.targetType,
        target_id=body.targetId,
        change_type=body.changeType,
        before_value=body.beforeValue,
        after_value=body.afterValue,
    )
    return success(data=data, message="变更请求已创建，待审批")


@router.post(
    "/config-changes/{change_id}/approve",
    response_model=ApiResponse[ConfigChangeRequestItem],
)
async def approve_config_change_endpoint(
    change_id: str,
    body: ConfigChangeReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "EXPERT")),
) -> dict:
    """审批通过变更请求并自动应用（ADMIN/EXPERT，审批人不能与申请人相同）。"""
    from app.services.diagnosis_approval import approve_change_request

    data = await approve_change_request(
        db=db,
        change_id=change_id,
        reviewer=user.username,
        review_note=body.reviewNote,
    )
    return success(data=data, message="变更已审批通过并生效")


@router.post(
    "/config-changes/{change_id}/reject",
    response_model=ApiResponse[ConfigChangeRequestItem],
)
async def reject_config_change_endpoint(
    change_id: str,
    body: ConfigChangeReviewRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "EXPERT")),
) -> dict:
    """拒绝变更请求（ADMIN/EXPERT，审批人不能与申请人相同）。"""
    from app.services.diagnosis_approval import reject_change_request

    data = await reject_change_request(
        db=db,
        change_id=change_id,
        reviewer=user.username,
        review_note=body.reviewNote,
    )
    return success(data=data, message="变更已拒绝")


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
    sortBy: str | None = Query(
        None,
        description="排序字段：diagnosed_at(默认,诊断时间) / created_at(tracker建单时间)",
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
        sort_by=sortBy,
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
    user: SysUser = Depends(get_current_user),
) -> dict:
    """导出统计报表（异步任务，返回 taskId）。

    设计依据：IDS §2.4 — POST /api/v1/diagnosis/analytics/export

    触发 Celery 异步导出任务，返回真实 task_id。
    任务完成后可通过 GET /api/v1/algorithms/tasks/{task_id} 查询状态。
    """
    from app.tasks.report_generator import export_diagnosis_statistics

    # 触发 Celery 异步导出任务
    async_result = export_diagnosis_statistics.delay(
        start_time=body.startTime,
        end_time=body.endTime,
        plant_node_id=body.plantNodeId,
        diagnosis_label=body.diagnosisLabel,
        action_status=body.actionStatus,
        user_id=user.id,
        granularity=body.granularity,
        file_format=body.format,
    )
    task_id = async_result.id
    data = {"taskId": task_id, "status": "PENDING"}
    logger.info(
        "诊断统计异步导出任务已提交, task_id=%s, user=%s, range=%s~%s",
        task_id,
        user.username,
        body.startTime,
        body.endTime,
    )
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


@router.get("/ab-compare", response_model=ApiResponse[dict])
async def ab_compare_endpoint(
    loopId: uuid.UUID = Query(..., description="回路 ID"),
    implementedAt: str | None = Query(
        None, description="实施时刻（ISO 8601），提供时自动截取 [T-7d,T) 与 (T,T+7d]"
    ),
    beforeStartTime: str | None = Query(None, description="Before 窗口开始（ISO 8601）"),
    beforeEndTime: str | None = Query(None, description="Before 窗口结束（ISO 8601）"),
    afterStartTime: str | None = Query(None, description="After 窗口开始（ISO 8601）"),
    afterEndTime: str | None = Query(None, description="After 窗口结束（ISO 8601）"),
    includeDiagnosis: bool = Query(
        False, description="是否返回诊断标签对比（before/after 标签 + 标签变化）"
    ),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """A/B 对比：实施前后两窗口 KPI 均值对比（kpi_snapshot_hourly）。

    窗口二选一：implementedAt 自动截取 [T-7d,T) 与 (T,T+7d]；
    或显式传入 before/after 窗口。实施后窗口数据不足 24h 时 dataInsufficient=true。

    includeDiagnosis=true 时额外返回 beforeDiagnosisLabels/afterDiagnosisLabels/
    labelChanges（Batch 4 回路分析页 A/B 对比增强）。

    Note: response_model 改为 dict 以兼容 includeDiagnosis 扩展字段。
    """
    data = await get_ab_compare(
        db=db,
        loop_id=str(loopId),
        implemented_at=implementedAt,
        before_start=beforeStartTime,
        before_end=beforeEndTime,
        after_start=afterStartTime,
        after_end=afterEndTime,
        include_diagnosis=includeDiagnosis,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# 诊断任务管理 API (PRD §5.6 诊断中心 — 诊断任务子模块)
# ---------------------------------------------------------------------------


@router.post("/trigger", response_model=ApiResponse[DiagnosisTriggerData])
async def trigger_diagnosis_endpoint(
    body: DiagnosisTriggerRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """触发诊断任务（手动，支持批量）。

    为每个回路创建一条 DiagnosisTask 记录，并通过 Celery 异步执行诊断。
    仅 ADMIN/IC_ENGINEER/PE_ENGINEER 角色可操作。

    设计依据：PRD §5.6 / IDS §2.4 — POST /api/v1/diagnosis/trigger
    """
    data = await trigger_diagnosis(
        db=db,
        loop_ids=body.loopIds,
        start_time=body.startTime,
        end_time=body.endTime,
        operator=user.username,
        labels=body.labels,
    )
    return success(data=data, message=f"已触发 {len(data['tasks'])} 个诊断任务")


@router.get("/tasks", response_model=ApiResponse[DiagnosisTaskListData])
async def list_tasks_endpoint(
    status: str | None = Query(
        None, description="任务状态筛选（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED）"
    ),
    triggerType: str | None = Query(None, description="触发方式筛选（manual/auto）"),
    loopId: str | None = Query(None, description="按回路 ID 筛选"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """诊断任务列表（仅未归档，分页 + 筛选）。

    设计依据：PRD §5.6 / IDS §2.4 — GET /api/v1/diagnosis/tasks
    """
    data = await list_diagnosis_tasks(
        db=db,
        status=status,
        trigger_type=triggerType,
        loop_id=loopId,
        plant_node_id=plantNodeId,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.get("/tasks/{task_id}", response_model=ApiResponse[DiagnosisTaskDetail])
async def get_task_detail_endpoint(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """诊断任务详情（含关联的诊断结果列表）。

    设计依据：PRD §5.6 / IDS §2.4 — GET /api/v1/diagnosis/tasks/{taskId}
    """
    data = await get_diagnosis_task_detail(db=db, task_id=task_id)
    return success(data=data)


@router.post("/tasks/{task_id}/run", response_model=ApiResponse[dict])
async def run_task_endpoint(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """对已有诊断任务执行诊断（不创建新任务）。

    重置任务状态为 PENDING 并通过 Celery 异步执行诊断。
    适用于行级"诊断"按钮。仅 ADMIN/IC_ENGINEER/PE_ENGINEER 角色可操作。

    设计依据：PRD §5.6 / IDS §2.4 — POST /api/v1/diagnosis/tasks/{taskId}/run
    """
    data = await run_diagnosis_task(db=db, task_id=task_id)
    return success(data=data, message="诊断任务已执行")


@router.post("/tasks/{task_id}/archive", response_model=ApiResponse[dict])
async def archive_task_endpoint(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """归档诊断任务（仅终态任务可归档：SUCCESS/FAILED/CANCELLED）。

    归档后任务从任务列表移入诊断记录。仅 ADMIN/IC_ENGINEER/PE_ENGINEER 角色可操作。

    设计依据：PRD §5.6 / IDS §2.4 — POST /api/v1/diagnosis/tasks/{taskId}/archive
    """
    data = await archive_diagnosis_task(db=db, task_id=task_id, operator=user.username)
    return success(data=data, message="任务已归档")


@router.post("/tasks/{task_id}/cancel", response_model=ApiResponse[dict])
async def cancel_task_endpoint(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """取消诊断任务（仅 PENDING/RUNNING 可取消）。

    仅 ADMIN/IC_ENGINEER/PE_ENGINEER 角色可操作。

    设计依据：PRD §5.6 / IDS §2.4 — POST /api/v1/diagnosis/tasks/{taskId}/cancel
    """
    data = await cancel_diagnosis_task(db=db, task_id=task_id, operator=user.username)
    return success(data=data, message="任务已取消")


@router.delete("/tasks/{task_id}", response_model=ApiResponse[dict])
async def delete_task_endpoint(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER")),
) -> dict:
    """物理删除诊断任务（RUNNING 不可删除，须先取消）。

    仅 ADMIN/IC_ENGINEER/PE_ENGINEER 角色可操作。

    设计依据：PRD §5.6 / IDS §2.4 — DELETE /api/v1/diagnosis/tasks/{taskId}
    """
    data = await delete_diagnosis_task(db=db, task_id=task_id, operator=user.username)
    return success(data=data, message="任务已删除")


@router.get("/records", response_model=ApiResponse[DiagnosisRecordListData])
async def list_records_endpoint(
    status: str | None = Query(None, description="任务状态筛选（SUCCESS/FAILED/CANCELLED）"),
    triggerType: str | None = Query(None, description="触发方式筛选（manual/auto）"),
    loopId: str | None = Query(None, description="按回路 ID 筛选"),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    timeWindow: str | None = Query(None, description="时间窗筛选（兼容前端参数，暂不后端过滤）"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """诊断记录列表（仅已归档，分页 + 筛选）。

    设计依据：PRD §5.6 / IDS §2.4 — GET /api/v1/diagnosis/records
    """
    data = await list_diagnosis_records(
        db=db,
        status=status,
        trigger_type=triggerType,
        loop_id=loopId,
        plant_node_id=plantNodeId,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.get("/{loop_id}", response_model=ApiResponse[dict])
async def get_diagnosis_detail_endpoint(
    loop_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # WS-D 性能#7 R1：SPONSOR 只读工作台，禁止下钻诊断详情
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> dict:
    """诊断详情（含 8 类标签数组 + 证据链 + 特征值）。"""
    data = await get_diagnosis_detail(db=db, loop_id=str(loop_id))
    return success(data=data)


@router.get("/algorithms/meta", response_model=ApiResponse[DiagnosisAlgorithmMetaList])
async def get_algorithm_meta_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取 8 类诊断算法展示元数据 + 当前生效阈值快照（Batch 4 算法价值传递）。

    返回每类标签的算法中文名、原理说明、关键特征值字段名、阈值字段名、
    对应可视化数据块键名、可信度等级释义、以及从 DiagnosisConfig 读取的当前阈值快照。
    供前端"算法价值传递卡片"渲染，避免前端硬编码算法说明。

    设计依据：Batch 4 F1 — 算法价值传递
    """
    data = await list_algorithm_meta(db)
    return success(data=data)


@router.get("/{loop_id}/visualization", response_model=ApiResponse[dict])
async def get_diagnosis_visualization_endpoint(
    loop_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    # WS-D 性能#7 R1：SPONSOR 只读工作台，禁止下钻诊断可视化
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> dict:
    """诊断可视化数据（包含 8 类算法的完整可视化数组）。

    返回数据结构：
    - spectrum: FFT 频谱数据（频率、振幅数组）
    - stepResponse: 阶跃响应数据（PV/SP/时间戳数组）
    - cusumAnalysis: CUSUM 累积和数据（正负累积和数组）
    - scatterPlot: PV-OP 散点图数据
    - qualityTimeline: PV 质量码时序数据
    - saturationAnalysis: OP 饱和分析数据
    - slowResponse: 响应迟缓分析数据
    - choudhury: Choudhury 非线性检测数据
    - iaeAnalysis: IAE 零交叉分析数据
    - kano: Kano 统计法数据
    """
    data = await get_diagnosis_visualization(db=db, loop_id=str(loop_id))
    return success(data=data)


# ---------------------------------------------------------------------------
# SVC-11: 诊断解决方案推荐
# ---------------------------------------------------------------------------


@router.get(
    "/{loop_id}/recommendations",
    response_model=ApiResponse[RecommendationData],
)
async def get_recommendations_endpoint(
    loop_id: uuid.UUID,
    tagCodes: str | None = Query(
        None,
        description="诊断标签列表（逗号分隔，可选。不传则从数据库读取该回路最新诊断标签）",
    ),
    db: AsyncSession = Depends(get_db),
    # WS-D 性能#7 R1：SPONSOR 只读工作台，禁止下钻诊断解决方案推荐
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> dict:
    """获取解决方案推荐（SVC-11）。

    根据诊断标签返回标准化解决方案推荐，每条建议包含优先级、行动项、描述和目标模块。
    """
    loop_id_str = str(loop_id)
    if tagCodes:
        tag_list = [t.strip() for t in tagCodes.split(",") if t.strip()]
        data = get_recommendations(loop_id_str, tag_list)
    else:
        data = await get_recommendations_for_loop(db=db, loop_id=loop_id_str)
    return success(data=data)


# ---------------------------------------------------------------------------
# SVC-12: 诊断建议书 PDF 生成
# ---------------------------------------------------------------------------


@router.post("/{loop_id}/report")
async def generate_report_endpoint(
    loop_id: uuid.UUID,
    body: DiagnosisReportRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> Response:
    """生成并下载 PDF 建议书（SVC-12）。

    内容：回路信息 + 诊断结果 + 性能指标 + 可能原因 + 解决方案推荐 + 生成时间。
    返回 PDF 文件 bytes。
    """
    loop_id_str = str(loop_id)
    # 1. 获取诊断详情作为快照数据
    snapshot_data = await get_diagnosis_detail(db=db, loop_id=loop_id_str)

    # 2. 获取推荐方案
    if body and body.tag_codes:
        recommendations = get_recommendations(loop_id_str, body.tag_codes)
    else:
        recommendations = await get_recommendations_for_loop(db=db, loop_id=loop_id_str)

    # 3. 生成 PDF
    pdf_bytes = generate_diagnosis_report(
        loop_id=loop_id_str,
        snapshot_data=snapshot_data,
        recommendations=recommendations,
    )

    filename = f"diagnosis_report_{loop_id_str}.pdf"
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
    loop_id: uuid.UUID,
    startTime: str = Query(..., description="开始时间（ISO 8601）"),
    endTime: str = Query(..., description="结束时间（ISO 8601）"),
    maxPoints: int = Query(5000, ge=100, le=50000, description="最大数据点数"),
    db: AsyncSession = Depends(get_db),
    # WS-D 性能#7 R1：SPONSOR 只读工作台，禁止下钻波形数据
    _: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")),
) -> dict:
    """波形数据（含 PV 质量码 + LTTB 降采样）。

    - PV 质量码为 Bad 时，pv 值为 null
    - 超过 maxPoints 触发 LTTB 降采样
    - 时间窗超过 30 天返回 ERR_TS_001
    """
    data = await get_waveform(
        db=db,
        loop_id=str(loop_id),
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
    loop_id: uuid.UUID,
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
        loop_id=str(loop_id),
        operator=user.username,
        status=body.status,
        evidence_url=body.evidenceUrl,
        remark=body.remark,
        comment=body.comment,
        moc_ref=body.mocRef,
        moc_not_applicable=body.mocNotApplicable,
        moc_reason=body.mocReason,
    )
    return success(data=data, message="状态更新成功")


@tracker_router.post("/{loop_id}/export")
async def export_tracker_endpoint(
    loop_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("IC_ENGINEER", "ADMIN", "PE_ENGINEER")),
) -> Response:
    """导出诊断建议书 PDF（同步生成，直接下载）。

    复用 SVC-12 报告生成器，文件名格式：CLPM-诊断建议书-[位号]-[日期].pdf
    """
    pdf_bytes, filename = await export_tracker_pdf(db=db, loop_id=str(loop_id))
    quoted_filename = quote(filename)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="CLPM-diagnosis-{loop_id}.pdf"; '
                f"filename*=UTF-8''{quoted_filename}"
            ),
        },
    )


# ---------------------------------------------------------------------------
# D4-2 整改效果验证周期配置 API
# ---------------------------------------------------------------------------


@tracker_router.get(
    "/verification-config",
    response_model=ApiResponse[TrackerVerificationConfig],
)
async def get_verification_config_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """读取整改效果验证周期配置（所有角色可查）。

    返回 sys_config 中 ``tracker.verification_interval_hours`` 的值，默认 24 小时。
    Celery 周期任务每小时读取此配置，决定哪些 IMPLEMENTED tracker 到达验证时机。
    """
    data = await get_verification_config(db)
    return success(data=data)


@tracker_router.patch(
    "/verification-config",
    response_model=ApiResponse[TrackerVerificationConfig],
)
async def update_verification_config_endpoint(
    body: TrackerVerificationConfig,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新整改效果验证周期配置（仅 ADMIN）。

    验证周期范围 1~720 小时，修改后下一周期任务执行时立即生效（无需重启）。
    """
    data = await update_verification_config(
        db, interval_hours=body.intervalHours, operator=user.username
    )
    return success(data=data, message="验证周期已更新")


# ---------------------------------------------------------------------------
# D4-3 整改有效率统计 API
# ---------------------------------------------------------------------------


@tracker_router.get(
    "/effectiveness",
    response_model=ApiResponse[TrackerEffectivenessData],
)
async def get_tracker_effectiveness_endpoint(
    timeWindow: str = Query(
        "last_30_days",
        description="时间窗口：last_7_days / last_30_days / last_90_days",
    ),
    plantNodeId: str | None = Query(None, description="按装置/单元筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """整改有效率统计（所有角色可查）。

    返回时间窗口内的已实施/已验证/改善/恶化数量、整改有效率、待验证数和每日趋势。
    依赖 D4-1 的 effect_verified 字段和 D4-2 的周期任务回写结果。
    """
    data = await get_tracker_effectiveness(db, time_window=timeWindow, plant_node_id=plantNodeId)
    return success(data=data)


# ---------------------------------------------------------------------------
# 诊断标签管理 API (IDS §2.4.10-2.4.12, PRD §5.6)
# ---------------------------------------------------------------------------

# 有效的标签处理目标状态（resolve 接口）
_VALID_RESOLVE_STATUSES = ("RESOLVED", "SUPPRESSED")

# 有效的标签筛选值
_VALID_TAG_TYPES = (
    "OSCILLATION",
    "VALVE_STICTION",
    "OVERAGGRESSIVE",
    "OVERCONSERVATIVE",
    "EXTERNAL_DISTURBANCE",
    "QUALITY_ABNORMAL",
    "OUTPUT_SATURATION",
    "MANUAL_REVIEW",
)
_VALID_SEVERITIES = ("INFO", "WARN", "ERROR", "CRITICAL")
_VALID_TAG_STATUSES = ("ACTIVE", "RESOLVED", "SUPPRESSED")


def _parse_iso_dt(value: str) -> datetime:
    """解析 ISO 8601 时间字符串为 naive datetime。

    Raises:
        BizError: ERR_VALIDATION — 时间格式无效
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"时间格式无效: {value}",
            status_code=422,
        ) from exc
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _validate_tag_query_filters(
    tag_type: str | None,
    severity: str | None,
    status: str | None,
) -> None:
    """校验诊断标签查询筛选参数。

    Raises:
        BizError: ERR_VALIDATION — 筛选值不在允许范围内
    """
    if tag_type is not None and tag_type not in _VALID_TAG_TYPES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"无效的标签类型，必须为 {', '.join(_VALID_TAG_TYPES)} 之一",
            status_code=422,
        )
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"无效的严重等级，必须为 {', '.join(_VALID_SEVERITIES)} 之一",
            status_code=422,
        )
    if status is not None and status not in _VALID_TAG_STATUSES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"无效的处理状态，必须为 {', '.join(_VALID_TAG_STATUSES)} 之一",
            status_code=422,
        )


def _tag_to_dict(tag: DiagnosisTag) -> dict:
    """将 DiagnosisTag ORM 模型转换为响应字典（对齐 DiagnosisTagSchema）。"""
    trigger_condition = tag.trigger_condition
    if not isinstance(trigger_condition, dict):
        trigger_condition = None

    # 从 trigger_condition 中提取 threshold 数值
    threshold = None
    if trigger_condition and "threshold" in trigger_condition:
        raw_threshold = trigger_condition["threshold"]
        if isinstance(raw_threshold, (int, float, Decimal)):
            threshold = float(raw_threshold)

    trigger_value = float(tag.trigger_value) if tag.trigger_value is not None else None

    return {
        "id": str(tag.id),
        "loop_id": str(tag.loop_id),
        "tag_type": tag.tag_code,
        "severity": tag.severity,
        "status": tag.status,
        "source_metric": tag.source_metric,
        "trigger_condition": trigger_condition,
        "trigger_value": trigger_value,
        "threshold": threshold,
        "confidence_level": None,  # 模型无独立列，由 trigger_condition 承载
        "description": tag.tag_name,
        "detected_at": tag.triggered_at.isoformat() if tag.triggered_at else None,
        "resolved_at": tag.resolved_at.isoformat() if tag.resolved_at else None,
        "resolved_by": str(tag.resolved_by) if tag.resolved_by else None,
        "resolution_note": tag.resolution_note,
    }


async def _query_diagnosis_tags(
    db: AsyncSession,
    *,
    loop_id: str | None = None,
    tag_type: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    plant_node_id: str | None = None,
    ts_start: str | None = None,
    ts_end: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """诊断标签分页查询（共享逻辑）。

    通过 JOIN loop_ledger 支持按装置节点筛选（plant_node_id → loop_ledger.unit_id）。
    """
    conditions: list = []
    if loop_id:
        conditions.append(DiagnosisTag.loop_id == loop_id)
    if tag_type:
        conditions.append(DiagnosisTag.tag_code == tag_type)
    if severity:
        conditions.append(DiagnosisTag.severity == severity)
    if status:
        conditions.append(DiagnosisTag.status == status)
    if ts_start:
        conditions.append(DiagnosisTag.triggered_at >= _parse_iso_dt(ts_start))
    if ts_end:
        conditions.append(DiagnosisTag.triggered_at <= _parse_iso_dt(ts_end))

    base_stmt = select(DiagnosisTag)
    if plant_node_id:
        base_stmt = base_stmt.join(LoopLedger, DiagnosisTag.loop_id == LoopLedger.id).where(
            LoopLedger.unit_id == plant_node_id
        )
    for cond in conditions:
        base_stmt = base_stmt.where(cond)

    # 计数
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # 分页查询（按检测时间倒序）
    stmt = (
        base_stmt.order_by(DiagnosisTag.triggered_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    tags = result.scalars().all()

    items = [_tag_to_dict(t) for t in tags]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@tags_router.get("", response_model=ApiResponse[DiagnosisTagListResponse])
async def list_diagnosis_tags_endpoint(
    tagType: str | None = Query(None, description="标签类型筛选（OSCILLATION/VALVE_STICTION/...）"),
    severity: str | None = Query(None, description="严重等级筛选（INFO/WARN/ERROR/CRITICAL）"),
    status: str | None = Query(None, description="处理状态筛选（ACTIVE/RESOLVED/SUPPRESSED）"),
    plantNodeId: str | None = Query(None, description="装置节点 ID 筛选"),
    tsStart: str | None = Query(None, description="时间范围开始（ISO 8601）"),
    tsEnd: str | None = Query(None, description="时间范围结束（ISO 8601）"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询诊断标签列表（多条件筛选，IDS §2.4.10）。

    支持按标签类型、严重等级、处理状态、装置节点、时间范围多维筛选，分页返回。
    所有认证用户可查询。
    """
    _validate_tag_query_filters(tagType, severity, status)
    data = await _query_diagnosis_tags(
        db=db,
        tag_type=tagType,
        severity=severity,
        status=status,
        plant_node_id=plantNodeId,
        ts_start=tsStart,
        ts_end=tsEnd,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@tags_router.get("/{loop_id}", response_model=ApiResponse[DiagnosisTagListResponse])
async def list_loop_diagnosis_tags_endpoint(
    loop_id: uuid.UUID,
    tagType: str | None = Query(None, description="标签类型筛选"),
    severity: str | None = Query(None, description="严重等级筛选"),
    status: str | None = Query(None, description="处理状态筛选"),
    tsStart: str | None = Query(None, description="时间范围开始（ISO 8601）"),
    tsEnd: str | None = Query(None, description="时间范围结束（ISO 8601）"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """查询指定回路的诊断标签（IDS §2.4.11）。

    在 /diagnosis/tags 基础上增加 loop_id 固定筛选，支持标签类型/严重等级/状态/时间范围二次筛选。
    所有认证用户可查询。
    """
    _validate_tag_query_filters(tagType, severity, status)
    data = await _query_diagnosis_tags(
        db=db,
        loop_id=str(loop_id),
        tag_type=tagType,
        severity=severity,
        status=status,
        ts_start=tsStart,
        ts_end=tsEnd,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@tags_router.put("/{tag_id}/resolve", response_model=ApiResponse[DiagnosisTagSchema])
async def resolve_diagnosis_tag_endpoint(
    tag_id: uuid.UUID,
    body: TagResolveRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("IC_ENGINEER", "PE_ENGINEER", "ADMIN")),
) -> dict:
    """处理诊断标签（IDS §2.4.12）。

    更新标签处理状态为 RESOLVED（已处理）或 SUPPRESSED（已抑制），
    记录处理人、处理时间和处理说明，写入审计日志。

    仅 IC_ENGINEER/PE_ENGINEER/ADMIN 角色可操作。处理人从认证上下文获取。
    """
    # 校验目标状态
    if body.status not in _VALID_RESOLVE_STATUSES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"无效的处理状态，必须为 {', '.join(_VALID_RESOLVE_STATUSES)} 之一",
            status_code=422,
        )

    # 查询标签
    result = await db.execute(select(DiagnosisTag).where(DiagnosisTag.id == str(tag_id)))
    tag = result.scalar_one_or_none()
    if tag is None:
        raise BizError(
            code="ERR_DIAG_TAG_NOT_FOUND",
            message="诊断标签不存在",
            status_code=404,
        )

    # 记录变更前快照
    before_snapshot = json.dumps(
        {
            "id": str(tag.id),
            "status": tag.status,
            "resolvedBy": str(tag.resolved_by) if tag.resolved_by else None,
            "resolutionNote": tag.resolution_note,
        },
        ensure_ascii=False,
        default=str,
    )

    # 更新字段
    now = datetime.now(UTC).replace(tzinfo=None)
    tag.status = body.status
    tag.resolved_at = now
    tag.resolved_by = user.id
    if body.resolution_note is not None:
        tag.resolution_note = body.resolution_note

    # 记录变更后快照
    after_snapshot = json.dumps(
        {
            "id": str(tag.id),
            "status": tag.status,
            "resolvedBy": str(tag.resolved_by),
            "resolvedAt": tag.resolved_at.isoformat() if tag.resolved_at else None,
            "resolutionNote": tag.resolution_note,
        },
        ensure_ascii=False,
        default=str,
    )

    # 写入审计日志
    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=user.username,
        operation_type="DIAG_TAG_RESOLVE",
        target_type="diagnosis_tag",
        target_id=str(tag.id),
        before_value=before_snapshot,
        after_value=after_snapshot,
        operated_at=now,
    )
    db.add(audit_log)
    await db.commit()

    logger.info(
        "诊断标签 %s 已处理: status=%s, operator=%s",
        tag_id,
        body.status,
        user.username,
    )

    return success(data=_tag_to_dict(tag), message="处理成功")


__all__ = ["router", "timeseries_router", "tracker_router", "tags_router"]
