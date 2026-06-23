"""Tuning center endpoints (IDS v3.2 §2.5 — S7-TUNE-006).

路由清单：
- GET    /api/v1/tuning/methods                — 获取整定方法信息
- GET    /api/v1/tuning/tasks                  — 整定任务列表（分页 + 筛选）
- GET    /api/v1/tuning/tasks/{taskId}         — 整定任务详情
- POST   /api/v1/tuning/tasks                  — 保存整定任务
- POST   /api/v1/tuning/identify               — 模型辨识
- POST   /api/v1/tuning/tune                   — PID 整定
- POST   /api/v1/tuning/simulate               — 闭环仿真
- GET    /api/v1/tuning/history                — 整定历史统计
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.db import get_db
from app.models.audit import SysAuditLog
from app.models.sys_user import SysUser
from app.schemas.common import success
from app.schemas.tuning import (
    CreateTuningTaskRequest,
    ModelIdentifyRequest,
    SimulateRequest,
    TuneRequest,
)
from app.services.tuning import (
    create_tuning_task,
    get_tuning_history_stats,
    get_tuning_methods,
    get_tuning_task_detail,
    identify_model,
    list_tuning_tasks,
    run_simulation,
    tune_pid,
)

router = APIRouter(prefix="/tuning", tags=["tuning"])


# ---------------------------------------------------------------------------
# 整定方法信息
# ---------------------------------------------------------------------------


@router.get("/methods")
async def get_methods_endpoint(
    _: SysUser = Depends(get_current_user),
) -> dict:
    """获取整定方法信息（所有角色可查看）。"""
    data = get_tuning_methods()
    return success(data=data)


# ---------------------------------------------------------------------------
# 模型辨识
# ---------------------------------------------------------------------------


@router.post("/identify")
async def identify_model_endpoint(
    body: ModelIdentifyRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """模型辨识（ADMIN/IC_ENGINEER/EXPERT）。

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
    return success(data=data)


# ---------------------------------------------------------------------------
# PID 整定
# ---------------------------------------------------------------------------


@router.post("/tune")
async def tune_pid_endpoint(
    body: TuneRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """PID 整定（ADMIN/IC_ENGINEER/EXPERT）。

    基于模型参数，使用 IMC/Lambda/ZN/Cohen-Coon/SIMC 算法计算推荐 PID 参数。
    """
    data = await tune_pid(
        model_type=body.modelType,
        model_params=body.modelParams.model_dump(exclude_none=True),
        algorithm=body.algorithm,
        algorithm_params=body.algorithmParams,
        current_pid=body.currentPid.model_dump() if body.currentPid else None,
        loop_id=body.loopId,
    )
    # 审计日志（S1-B7）
    log = SysAuditLog(
        id=str(uuid4()),
        operator=user.username,
        operation_type="TUNE_PID",
        target_type="Loop",
        target_id=body.loopId,
        after_value=f"algorithm={body.algorithm}, modelType={body.modelType}",
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(log)
    await db.commit()
    return success(data=data)


# ---------------------------------------------------------------------------
# 闭环仿真
# ---------------------------------------------------------------------------


@router.post("/simulate")
async def simulate_endpoint(
    body: SimulateRequest,
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER", "EXPERT")),
) -> dict:
    """闭环仿真（ADMIN/IC_ENGINEER/EXPERT）。

    对比当前 PID 与推荐 PID 的阶跃响应。
    """
    data = await run_simulation(
        model_type=body.modelType,
        model_params=body.modelParams.model_dump(exclude_none=True),
        current_pid=body.currentPid.model_dump(),
        recommended_pid=body.recommendedPid.model_dump(),
        sim_duration=body.simDuration,
        sim_step=body.simStep,
        setpoint_step=body.setpointStep,
        disturbance_type=body.disturbanceType,
    )
    return success(data=data)


# ---------------------------------------------------------------------------
# 整定任务管理
# ---------------------------------------------------------------------------


@router.get("/tasks")
async def list_tasks_endpoint(
    loopId: str | None = Query(None, description="回路 ID 筛选"),
    algorithm: str | None = Query(None, description="算法筛选"),
    status: str | None = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """整定任务列表（分页 + 筛选）。"""
    data = await list_tuning_tasks(
        db=db,
        loop_id=loopId,
        algorithm=algorithm,
        status=status,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


@router.get("/tasks/{task_id}")
async def get_task_detail_endpoint(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """整定任务详情。"""
    data = await get_tuning_task_detail(db=db, task_id=task_id)
    return success(data=data)


@router.post("/tasks", status_code=201)
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


@router.get("/history")
async def get_history_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(get_current_user),
) -> dict:
    """整定历史统计。"""
    data = await get_tuning_history_stats(db=db)
    return success(data=data)
