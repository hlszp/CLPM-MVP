"""Tuning center endpoints (IDS v3.2 §2.5 — S7-TUNE-006).

路由清单：
- GET    /api/v1/tuning/methods                — 获取整定方法信息
- GET    /api/v1/tuning/tasks                  — 整定任务列表（分页 + 筛选）
- GET    /api/v1/tuning/tasks/{taskId}         — 整定任务详情
- POST   /api/v1/tuning/tasks                  — 保存整定任务
- POST   /api/v1/tuning/identify               — 模型辨识（阶跃实验路径，同步）
- POST   /api/v1/tuning/identify/history        — 历史数据辨识（Phase 2，异步）
- POST   /api/v1/tuning/identify/segments       — 可辨识片段预览（Phase 2）
- GET    /api/v1/tuning/tasks/{taskId}/status   — 异步任务进度查询（Phase 2）
- POST   /api/v1/tuning/tasks/{taskId}/cancel   — 取消异步任务（Phase 2）
- POST   /api/v1/tuning/tune                   — PID 整定
- POST   /api/v1/tuning/simulate               — 闭环仿真（支持多 PID 对比）
- POST   /api/v1/tuning/compare                — 多 PID 对比仿真（Phase 2）
- GET    /api/v1/tuning/history                — 整定历史统计
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_perms, require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.sys_user import SysUser
from app.schemas.common import ApiResponse, success
from app.schemas.tuning import (
    CompareRequest,
    CreateTuningTaskRequest,
    IdentifyHistoryAsyncResponse,
    IdentifySegmentsRequest,
    IdentifySegmentsResult,
    ModelIdentifyHistoryRequest,
    ModelIdentifyRequest,
    ModelIdentifyResult,
    SimulateRequest,
    SimulationResult,
    TaskProgress,
    TuneMatrixRequest,
    TuneRequest,
    TuneResult,
    TuningHistoryStats,
    TuningMethodInfo,
    TuningTaskDetail,
)
from app.schemas.tuning_knowledge import (
    TuningKnowledgeEntryItem,
    TuningKnowledgeListData,
    TuningKnowledgeListStats,
    TuningKnowledgeSimilarData,
)
from app.services.kpi_snapshot import (
    iso_z,
    kpi_summary,
    latest_snapshot_in_window,
)
from app.services.tuning import (
    authorize_tuning_model,
    create_tuning_task,
    get_tuning_history_stats,
    get_tuning_methods,
    get_tuning_task_detail,
    identify_model,
    list_tuning_tasks,
    persist_step_identification_record,
    preview_identify_segments,
    run_simulation,
    tune_pid,
)
from app.services.tuning_knowledge import (
    get_knowledge_entry,
    list_knowledge_entries,
    recommend_similar,
)
from app.services.waveform import get_waveform

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tuning", tags=["tuning"])


# ---------------------------------------------------------------------------
# 整定方法信息
# ---------------------------------------------------------------------------


@router.get("/methods", response_model=ApiResponse[list[TuningMethodInfo]])
async def get_methods_endpoint(
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """获取整定方法信息（所有角色可查看）。"""
    data = get_tuning_methods()
    return success(data=data)


# ---------------------------------------------------------------------------
# 模型辨识
# ---------------------------------------------------------------------------


@router.post("/identify", response_model=ApiResponse[ModelIdentifyResult])
async def identify_model_endpoint(
    body: ModelIdentifyRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """模型辨识（阶跃实验路径，同步；ADMIN/IC_ENGINEER/EXPERT）。

    从 TDengine 拉取波形数据，执行 FOPDT/SOPDT/IPDT 模型辨识。
    """
    data = await identify_model(
        db=db,
        loop_id=body.loopId,
        start_time=body.startTime,
        end_time=body.endTime,
        model_type=body.modelType,
        method=body.method,
    )
    record_id = await persist_step_identification_record(
        db=db,
        loop_id=body.loopId,
        result=data,
        created_by=user.username,
        requested_method=body.method,
    )
    data = {**data, "recordId": record_id}
    return success(data=data)


@router.post("/identify/history", response_model=ApiResponse[dict])
async def identify_history_endpoint(
    body: ModelIdentifyHistoryRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """历史数据辨识（Phase 2，异步任务；ADMIN/IC_ENGINEER/EXPERT）。

    提交异步 Celery 任务，返回 taskId 供前端轮询进度。
    辨识策略 AUTO=优先历史,失败兜底阶跃（任务内自动降级并标注
    dataSource=fallback_step）/ HISTORY_ONLY / STEP_ONLY。

    V62-P1-012: 响应使用 typed model 构造——
    - STEP_ONLY → ``ModelIdentifyResult``（同步辨识结果）
    - AUTO/HISTORY_ONLY → ``IdentifyHistoryAsyncResponse``（异步任务提交）
    """
    from app.tasks.tuning import identify_model_task

    # STEP_ONLY 策略走同步阶跃路径（向后兼容）
    if body.identifyStrategy == "STEP_ONLY":
        data = await identify_model(
            db=db,
            loop_id=body.loopId,
            start_time=body.startTime,
            end_time=body.endTime,
            model_type="FOPDT",
            method=None,
        )
        record_id = await persist_step_identification_record(
            db=db,
            loop_id=body.loopId,
            result=data,
            created_by=user.username,
            requested_method=None,
        )
        # V62-P1-012: 使用 typed model 构造响应，确保 contract 一致
        typed = ModelIdentifyResult.model_validate({**data, "recordId": record_id})
        return success(data=typed.model_dump(by_alias=True, exclude_none=True))

    # AUTO / HISTORY_ONLY → 异步任务
    # AUTO 策略由任务侧在历史辨识失败/数据不足时自动降级阶跃实验路径（P1-6）
    # V62-P1-013: 传递 created_by_id 桥接 TaskTracker
    task = identify_model_task.delay(
        loop_id=body.loopId,
        start_time=body.startTime,
        end_time=body.endTime,
        candidate_model_types=body.candidateModelTypes,
        theta_estimate=body.thetaEstimate,
        created_by=user.username,
        identify_strategy=body.identifyStrategy,
        created_by_id=str(user.id),
    )
    # V62-P1-012: 使用 typed model 构造响应
    typed = IdentifyHistoryAsyncResponse(
        taskId=task.id,
        status="PENDING",
        identifyStrategy=body.identifyStrategy,
    )
    return success(data=typed.model_dump(by_alias=True, exclude_none=True))


@router.post("/identify/segments", response_model=ApiResponse[IdentifySegmentsResult])
async def identify_segments_endpoint(
    body: IdentifySegmentsRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """可辨识片段预览（Phase 2；ADMIN/IC_ENGINEER/EXPERT）。

    对数据窗口执行激励检测，返回可辨识片段列表（不执行辨识）。
    """
    data = await preview_identify_segments(
        db=db,
        loop_id=body.loopId,
        start_time=body.startTime,
        end_time=body.endTime,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# PID 整定
# ---------------------------------------------------------------------------


@router.post("/tune", response_model=ApiResponse[TuneResult])
async def tune_pid_endpoint(
    body: TuneRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """PID 整定（ADMIN/IC_ENGINEER/EXPERT）。

    基于模型参数，使用 IMC/Lambda/ZN/Cohen-Coon/SIMC 算法计算推荐 PID 参数。
    """
    source_context = await authorize_tuning_model(
        db=db,
        requested_model_type=body.modelType,
        requested_model_params=body.modelParams.model_dump(exclude_none=True),
        loop_id=body.loopId,
        source_record_id=body.sourceRecordId,
        model_source=body.modelSource,
        risk_confirmed=body.riskConfirmed,
    )
    data = await tune_pid(
        model_type=source_context.model_type,
        model_params=source_context.model_params,
        algorithm=body.algorithm,
        algorithm_params=body.algorithmParams,
        current_pid=body.currentPid.model_dump() if body.currentPid else None,
        loop_id=source_context.loop_id,
        source_context=source_context,
    )
    # 审计日志（S1-B7）
    log = SysAuditLog(
        id=str(uuid4()),
        operator=user.username,
        operation_type="TUNE_PID",
        target_type="Loop",
        target_id=source_context.loop_id,
        after_value=(
            f"algorithm={body.algorithm}, modelType={source_context.model_type}, "
            f"source={source_context.model_source}, "
            f"record={source_context.source_record_id or '-'}, "
            f"riskConfirmed={str(source_context.risk_confirmed).lower()}"
        ),
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)
    await db.commit()
    return success(data=data)


@router.post("/tune/matrix", response_model=ApiResponse[dict])
async def tune_matrix_endpoint(
    body: TuneMatrixRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """全算法矩阵整定（09 设计方案 §4.2；ADMIN/IC_ENGINEER/EXPERT）。

    IMC/LAMBDA/ZN/COHEN_COON/SIMC 5 算法一次全算；单算法失败不阻断，
    该行返回 {"ok": False, "error": ...} 由前端置灰。不写审计日志——
    审计由单行 /tune 微调与最终保存方案（POST /tasks）承担。
    """
    source_context = await authorize_tuning_model(
        db=db,
        requested_model_type=body.modelType,
        requested_model_params=body.modelParams.model_dump(exclude_none=True),
        loop_id=body.loopId,
        source_record_id=body.sourceRecordId,
        model_source=body.modelSource,
        risk_confirmed=body.riskConfirmed,
    )
    rows: list[dict[str, Any]] = []
    for algo in ("IMC", "LAMBDA", "ZN", "COHEN_COON", "SIMC"):
        try:
            result = await tune_pid(
                model_type=source_context.model_type,
                model_params=source_context.model_params,
                algorithm=algo,
                algorithm_params=body.algorithmParams,
                current_pid=body.currentPid.model_dump() if body.currentPid else None,
                loop_id=source_context.loop_id,
                source_context=source_context,
            )
            rows.append({"algorithm": algo, "ok": True, "result": result})
        except BizError as exc:
            rows.append({"algorithm": algo, "ok": False, "error": exc.message})
        except Exception as exc:  # noqa: BLE001  # 单算法异常不阻断矩阵其余行
            logger.warning("整定矩阵 %s 算法计算失败: %s", algo, exc)
            rows.append({"algorithm": algo, "ok": False, "error": str(exc)})
    return success(data={"rows": rows})


# ---------------------------------------------------------------------------
# 闭环仿真
# ---------------------------------------------------------------------------


@router.post("/simulate", response_model=ApiResponse[SimulationResult])
async def simulate_endpoint(
    body: SimulateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """闭环仿真（ADMIN/IC_ENGINEER/EXPERT）。

    对比当前 PID 与推荐 PID 的阶跃响应（Phase 2 支持多 PID 候选对比）。
    """
    source_context = await authorize_tuning_model(
        db=db,
        requested_model_type=body.modelType,
        requested_model_params=body.modelParams.model_dump(exclude_none=True),
        loop_id=body.loopId,
        source_record_id=body.sourceRecordId,
        model_source=body.modelSource,
        risk_confirmed=body.riskConfirmed,
    )

    # Phase 2：pid_candidates 转为 dict 列表透传
    pid_candidates_dicts: list[dict] | None = None
    if body.pidCandidates:
        pid_candidates_dicts = [c.model_dump() for c in body.pidCandidates]

    data = await run_simulation(
        model_type=source_context.model_type,
        model_params=source_context.model_params,
        current_pid=body.currentPid.model_dump(),
        recommended_pid=body.recommendedPid.model_dump(),
        sim_duration=body.simDuration,
        sim_step=body.simStep,
        setpoint_step=body.setpointStep,
        disturbance_type=body.disturbanceType,
        pid_candidates=pid_candidates_dicts,
    )
    return success(data=data)


@router.post("/compare", response_model=ApiResponse[SimulationResult])
async def compare_pids_endpoint(
    body: CompareRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """多 PID 对比仿真（Phase 2；ADMIN/IC_ENGINEER/EXPERT）。

    使用独立 ``CompareRequest``（V62-P0-030）：``pidCandidates`` 必填且 ≥2 组，
    ``currentPid`` 可选，不接受 ``recommendedPid``（端点从不消费该字段）。
    返回 candidateResponses 含每组 PID 的响应曲线与指标。
    """
    if not body.pidCandidates or len(body.pidCandidates) < 2:
        from app.core.exceptions import BizError

        raise BizError(
            code="ERR_INVALID_REQUEST",
            message="多 PID 对比至少需要 2 组候选 PID 参数",
            status_code=400,
        )

    source_context = await authorize_tuning_model(
        db=db,
        requested_model_type=body.modelType,
        requested_model_params=body.modelParams.model_dump(exclude_none=True),
        loop_id=body.loopId,
        source_record_id=body.sourceRecordId,
        model_source=body.modelSource,
        risk_confirmed=body.riskConfirmed,
    )

    from app.services.tuning import _simulate_multi_pid

    candidates_dicts = [c.model_dump() for c in body.pidCandidates]
    data = _simulate_multi_pid(
        model_type=source_context.model_type,
        model_params=source_context.model_params,
        current_pid=body.currentPid.model_dump() if body.currentPid else None,
        pid_candidates=candidates_dicts,
        sim_duration=body.simDuration,
        sim_step=body.simStep,
        setpoint_step=body.setpointStep,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# 效果验证（09 设计方案 §4.5）
# ---------------------------------------------------------------------------


def _parse_point_time(s: str) -> datetime:
    """ISO 8601 → naive UTC（aware 输入归一化，禁止 aware/naive 混入库查询）。"""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise BizError(
            code="ERR_PARAM",
            message=f"pointTime 不是合法 ISO 8601 时间: {s}",
            status_code=400,
        ) from None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


@router.get("/verification/data", response_model=ApiResponse[dict])
async def verification_data_endpoint(
    loopId: str = Query(..., description="回路 ID"),
    pointTime: str = Query(..., description="对比时点 ISO 8601（naive UTC 或带时区）"),
    windowHours: int = Query(..., description="窗口小时数：1/2/24"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """效果验证前后窗曲线数据（全部登录用户只读；实时拉取不落库）。

    前窗 [pointTime−window, pointTime] 与后窗 [pointTime, pointTime+window]
    各拉一次 SP/PV/OP 波形 + 窗口内最新 KPI 快照摘要（无快照侧为 null）；
    后窗超出当前时刻时 afterTruncated=true，前端标注"数据截至当前时刻"。
    """
    if windowHours not in (1, 2, 24):
        raise BizError(code="ERR_PARAM", message="windowHours 仅支持 1/2/24", status_code=400)
    point = _parse_point_time(pointTime)
    delta = timedelta(hours=windowHours)
    before = await get_waveform(db, loopId, start_time=iso_z(point - delta), end_time=iso_z(point))
    after = await get_waveform(db, loopId, start_time=iso_z(point), end_time=iso_z(point + delta))
    kpi_before = kpi_summary(await latest_snapshot_in_window(db, loopId, point - delta, point))
    kpi_after = kpi_summary(await latest_snapshot_in_window(db, loopId, point, point + delta))
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    return success(
        data={
            "loopId": loopId,
            "pointTime": iso_z(point),
            "windowHours": windowHours,
            "before": before,
            "after": after,
            "kpiBefore": kpi_before,
            "kpiAfter": kpi_after,
            "afterTruncated": (point + delta) > now_naive,
        }
    )


# ---------------------------------------------------------------------------
# 整定任务管理
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=ApiResponse[dict])
async def list_tasks_endpoint(
    loopId: uuid.UUID | None = Query(None, description="回路 ID 筛选"),
    algorithm: str | None = Query(None, description="算法筛选"),
    status: str | None = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """整定任务列表（分页 + 筛选）。"""
    data = await list_tuning_tasks(
        db=db,
        loop_id=str(loopId) if loopId else None,
        algorithm=algorithm,
        status=status,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.get("/tasks/{task_id}", response_model=ApiResponse[TuningTaskDetail])
async def get_task_detail_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """整定任务详情。"""
    data = await get_tuning_task_detail(db=db, task_id=str(task_id))
    return success(data=data)


@router.get("/tasks/{task_id}/status", response_model=ApiResponse[TaskProgress])
async def get_task_status_endpoint(
    task_id: str,
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """异步任务进度查询（Phase 2；需 tuning:view 权限码）。

    task_id 为 Celery 任务 ID（字符串），非 TuningRecord UUID。
    """
    from app.services.tuning_progress import get_progress

    data = await get_progress(task_id)
    if data is None:
        from app.core.exceptions import BizError

        raise BizError(
            code="ERR_TUNING_TASK_NOT_FOUND",
            message="任务进度记录不存在",
            status_code=404,
        )
    return success(data=data)


@router.post("/tasks/{task_id}/cancel", response_model=ApiResponse[dict])
async def cancel_task_endpoint(
    task_id: str,
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """取消异步整定任务（Phase 2；ADMIN/IC_ENGINEER/EXPERT）。

    task_id 为 Celery 任务 ID。仅 PENDING/RUNNING 状态可取消。
    V62-P1-013: 同步 tuning_progress 和 TaskTracker 状态为 CANCELLED。
    """
    from celery.result import AsyncResult

    from app.services.tuning_progress import update_progress
    from app.tasks.celery_app import celery_app

    result = AsyncResult(task_id, app=celery_app)
    if result.state in ("PENDING", "RUNNING", "STARTED"):
        result.revoke(terminate=True, signal="SIGTERM")
        # V62-P1-013: 同步 tuning_progress → TaskTracker
        await update_progress(
            task_id,
            status="CANCELLED",
            message="用户主动取消",
        )
        return success(data={"taskId": task_id, "status": "CANCELLED"})
    # 已终态（SUCCESS/FAILED/REVOKED）不可取消
    return success(data={"taskId": task_id, "status": result.state})


@router.post("/tasks", status_code=201, response_model=ApiResponse[dict])
async def create_task_endpoint(
    body: CreateTuningTaskRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """保存整定任务（ADMIN/IC_ENGINEER/EXPERT）。"""
    data = await create_tuning_task(
        db=db,
        loop_id=body.loopId,
        model_type=body.modelType,
        model_params=body.modelParams.model_dump(exclude_none=True),
        algorithm=body.algorithm,
        recommended_pid=body.recommendedPid.model_dump(),
        current_pid=body.currentPid.model_dump() if body.currentPid else None,
        fitting_score=body.fittingScore,
        simulation_result=body.simulationResult,
        status=body.status,
        created_by=user.username,
        # Phase 2.2 元数据
        identify_method=body.identifyMethod,
        data_source=body.dataSource,
        confidence_level=body.confidenceLevel,
        confidence_reason=body.confidenceReason,
        excitation_score=body.excitationScore,
        residual_test_passed=body.residualTestPassed,
        pid_candidates=body.pidCandidates,
        candidate_results=body.candidateResults,
    )
    # 审计日志（S1-B7）
    log = SysAuditLog(
        id=str(uuid4()),
        operator=user.username,
        operation_type="CREATE_TUNING_TASK",
        target_type="TuningTask",
        target_id=data.get("taskId"),
        after_value=f"algorithm={body.algorithm}, status={body.status}",
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)
    await db.commit()
    return success(data=data)


# ---------------------------------------------------------------------------
# 整定历史统计
# ---------------------------------------------------------------------------


@router.get("/history", response_model=ApiResponse[TuningHistoryStats])
async def get_history_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """整定历史统计。"""
    data = await get_tuning_history_stats(db=db)
    return success(data=data)


# ---------------------------------------------------------------------------
# P3-01: 整定知识库 API
# ---------------------------------------------------------------------------


def _entry_to_item(entry) -> TuningKnowledgeEntryItem:
    """将 TuningKnowledgeEntry ORM 模型转换为 schema（datetime → ISO 字符串）。"""

    def _iso(dt) -> str | None:
        return dt.isoformat() if dt else None

    return TuningKnowledgeEntryItem(
        id=str(entry.id),
        trackerId=str(entry.tracker_id),
        tuningRecordId=str(entry.tuning_record_id) if entry.tuning_record_id else None,
        loopId=str(entry.loop_id),
        loopType=entry.loop_type,
        controlType=entry.control_type,
        tagName=entry.tag_name,
        diagnosisLabel=entry.diagnosis_label,
        severity=entry.severity,
        modelType=entry.model_type,
        algorithm=entry.algorithm,
        identifyMethod=entry.identify_method,
        confidenceLevel=entry.confidence_level,
        pidBefore=entry.pid_before,
        pidAfter=entry.pid_after,
        kpiSummary=entry.kpi_summary,
        effectVerified=entry.effect_verified,
        improvedCount=entry.improved_count,
        deterioratedCount=entry.deteriorated_count,
        matchSource=entry.match_source,
        implementedAt=_iso(entry.implemented_at),
        verifiedAt=_iso(entry.verified_at),
        createdAt=_iso(entry.created_at),
    )


@router.get("/knowledge-base", response_model=ApiResponse[TuningKnowledgeListData])
async def list_knowledge_base_endpoint(
    loopType: str | None = Query(None, description="控制类型筛选"),
    diagnosisLabel: str | None = Query(None, description="问题类型筛选"),
    algorithm: str | None = Query(None, description="算法筛选"),
    effectVerified: bool | None = Query(None, description="效果筛选：True=改善/False=恶化"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """知识库列表（支持筛选+分页，权限 tuning:view）。"""
    result = await list_knowledge_entries(
        db,
        loop_type=loopType,
        diagnosis_label=diagnosisLabel,
        algorithm=algorithm,
        effect_verified=effectVerified,
        page=page,
        page_size=pageSize,
    )
    data = TuningKnowledgeListData(
        items=[_entry_to_item(e) for e in result["items"]],
        total=result["total"],
        page=result["page"],
        pageSize=result["pageSize"],
        stats=TuningKnowledgeListStats(**result["stats"]) if result.get("stats") else None,
    )
    return success(data=data)


@router.get("/knowledge-base/similar", response_model=ApiResponse[TuningKnowledgeSimilarData])
async def recommend_similar_endpoint(
    loopId: str | None = Query(None, description="当前回路 ID（排除自身）"),
    loopType: str | None = Query(None, description="控制类型（loopId 为空时用此匹配）"),
    diagnosisLabel: str | None = Query(None, description="问题类型（loopId 为空时用此匹配）"),
    limit: int = Query(5, ge=1, le=20, description="返回条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """相似案例推荐（优先 label 相同 > loop_type 相同，改善案例优先）。"""
    items = await recommend_similar(
        db,
        loop_id=loopId,
        loop_type=loopType,
        diagnosis_label=diagnosisLabel,
        limit=limit,
    )
    data = TuningKnowledgeSimilarData(
        items=[_entry_to_item(e) for e in items],
        total=len(items),
    )
    return success(data=data)


@router.get(
    "/knowledge-base/{entry_id}",
    response_model=ApiResponse[TuningKnowledgeEntryItem],
)
async def get_knowledge_base_entry_endpoint(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("tuning:view")),
) -> dict:
    """知识库条目详情。"""
    entry = await get_knowledge_entry(db, entry_id)
    if entry is None:
        from app.core.exceptions import BizError

        raise BizError(
            code="ERR_NOT_FOUND",
            message="知识库条目不存在",
            status_code=404,
        )
    return success(data=_entry_to_item(entry))
