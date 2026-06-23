"""Tuning center service (IDS v3.2 §2.5 — S7-TUNE-006).

业务逻辑：
- 模型辨识：从 TDengine 拉取波形数据 → 调用辨识算法 → 返回模型参数
- PID 整定：基于模型参数 → 调用整定算法 → 返回推荐 PID 参数
- 闭环仿真：基于模型 + 当前/推荐 PID → 仿真对比
- 整定任务管理：CRUD + 历史统计
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.tuning import TuningRecord
from app.services.tuning_algorithms import (
    TUNING_ALGORITHM_VERSION,
    TUNING_METHODS_INFO,
    PIDParams,
    identify_fopdt,
    identify_ipdt,
    identify_sopdt,
    simulate_closed_loop,
    tune_cohen_coon,
    tune_imc,
    tune_lambda,
    tune_simc,
    tune_zn,
)
from app.services.waveform import get_waveform

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 模型辨识
# ---------------------------------------------------------------------------


async def identify_model(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    model_type: str = "FOPDT",
    method: str | None = None,
) -> dict[str, Any]:
    """模型辨识。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TUNING_DATA_INSUFFICIENT
    """
    # 校验回路
    loop = await _get_loop(db, loop_id)

    # 拉取波形数据
    waveform = await get_waveform(
        db, loop_id, start_time=start_time, end_time=end_time, max_points=10000
    )

    pv_values_raw = waveform.get("pv", [])
    timestamps_raw = waveform.get("timestamps", [])

    # 过滤 None 值（Bad 质量码）
    pv_values: list[float] = []
    timestamps: list[float] = []
    for i, pv in enumerate(pv_values_raw):
        if pv is not None and i < len(timestamps_raw):
            pv_values.append(float(pv))
            # timestamps 是毫秒，转为秒
            ts_sec = timestamps_raw[i] / 1000.0
            timestamps.append(ts_sec)

    if len(pv_values) < 10:
        raise BizError(
            code="ERR_TUNING_DATA_INSUFFICIENT",
            message=f"波形数据不足（{len(pv_values)} 点），至少需要 10 个有效数据点",
            status_code=400,
        )

    # 估算 MV 阶跃幅值（从 OP 数据）
    op_values_raw = waveform.get("op", [])
    mv_step = _estimate_mv_step(op_values_raw)
    if mv_step == 0:
        # 如果无法从 OP 估算，使用 PV 变化范围作为默认
        mv_step = max(pv_values[-1] - pv_values[0], 1.0)
        logger.info("无法从 OP 估算 MV 阶跃，使用默认值: %s", mv_step)

    # 调用辨识算法
    if model_type == "FOPDT":
        result = identify_fopdt(pv_values, timestamps, mv_step, method or "TWO_POINT")
        params = {"K": result["K"], "tau": result["tau"], "theta": result["theta"]}
    elif model_type == "SOPDT":
        result = identify_sopdt(pv_values, timestamps, mv_step)
        params = {
            "K": result["K"],
            "T1": result["T1"],
            "T2": result["T2"],
            "theta": result["theta"],
        }
    elif model_type == "IPDT":
        result = identify_ipdt(pv_values, timestamps, mv_step)
        params = {"K": result["K"], "theta": result["theta"]}
    else:
        raise BizError(
            code="ERR_INVALID_MODEL_TYPE",
            message=f"不支持的模型类型: {model_type}",
            status_code=400,
        )

    # 构建拟合曲线响应
    fitted_curve = None
    if result.get("fitted_pv"):
        fitted_curve = {
            "timestamps": [int(t * 1000) for t in timestamps],  # 转回毫秒
            "pv": pv_values,
            "fitted": result["fitted_pv"],
        }

    return {
        "modelType": model_type,
        "params": params,
        "fittingScore": result["fitting_score"],
        "algorithmVersion": TUNING_ALGORITHM_VERSION,
        "dataPoints": len(pv_values),
        "fittedCurve": fitted_curve,
        "tagName": loop.tag_name,
        "mvStep": mv_step,
    }


def _estimate_mv_step(op_values: list[float | None]) -> float:
    """从 OP 数据估算阶跃幅值。"""
    valid_ops = [float(v) for v in op_values if v is not None]
    if len(valid_ops) < 2:
        return 0.0
    # 找最大变化段
    max_change = 0.0
    for i in range(1, len(valid_ops)):
        change = abs(valid_ops[i] - valid_ops[i - 1])
        if change > max_change:
            max_change = change
    # 如果整体变化范围更大，用整体范围
    total_range = abs(valid_ops[-1] - valid_ops[0])
    return max(max_change, total_range)


# ---------------------------------------------------------------------------
# PID 整定
# ---------------------------------------------------------------------------


async def tune_pid(
    model_type: str,
    model_params: dict[str, Any],
    algorithm: str,
    algorithm_params: dict[str, Any] | None = None,
    current_pid: dict[str, Any] | None = None,
    loop_id: str | None = None,
) -> dict[str, Any]:
    """PID 整定。

    Raises:
        BizError: ERR_INVALID_ALGORITHM / ERR_MODEL_PARAMS_MISSING
    """
    K = float(model_params.get("K") or 0)
    tau = float(model_params.get("tau") or 0)
    theta = float(model_params.get("theta") or 0)

    if K == 0:
        raise BizError(
            code="ERR_MODEL_PARAMS_MISSING",
            message="模型参数 K（过程增益）缺失或为零",
            status_code=400,
        )

    params = algorithm_params or {}

    if algorithm == "IMC":
        pid = tune_imc(K, tau, theta, lambda_ratio=float(params.get("lambdaRatio", 1.0)))
        notes = f"IMC 整定：λ = {params.get('lambdaRatio', 1.0)} × θ"
    elif algorithm == "LAMBDA":
        pid = tune_lambda(K, tau, theta, lambda_ratio=float(params.get("lambdaRatio", 1.0)))
        notes = f"Lambda 整定：λ = {params.get('lambdaRatio', 1.0)} × τ"
    elif algorithm == "ZN":
        controller_type = str(params.get("controllerType", "PID"))
        pid = tune_zn(K, tau, theta, controller_type=controller_type)
        notes = f"Z-N 开环法：控制器类型 = {controller_type}"
    elif algorithm == "COHEN_COON":
        controller_type = str(params.get("controllerType", "PID"))
        pid = tune_cohen_coon(K, tau, theta, controller_type=controller_type)
        notes = f"Cohen-Coon 整定：控制器类型 = {controller_type}"
    elif algorithm == "SIMC":
        tau_c_ratio = float(params.get("tauCRatio", 1.0))
        pid = tune_simc(K, tau, theta, tau_c_ratio=tau_c_ratio)
        notes = f"SIMC 整定：τc = {tau_c_ratio} × θ"
    else:
        raise BizError(
            code="ERR_INVALID_ALGORITHM",
            message=f"不支持的整定算法: {algorithm}",
            status_code=400,
        )

    result = {
        "algorithm": algorithm,
        "recommendedPid": {"kp": pid.kp, "ti": pid.ti, "td": pid.td},
        "algorithmParams": params,
        "algorithmVersion": TUNING_ALGORITHM_VERSION,
        "notes": notes,
    }

    if current_pid:
        result["currentPid"] = current_pid

    return result


# ---------------------------------------------------------------------------
# 闭环仿真
# ---------------------------------------------------------------------------


async def run_simulation(
    model_type: str,
    model_params: dict[str, Any],
    current_pid: dict[str, Any],
    recommended_pid: dict[str, Any],
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
    disturbance_type: str = "step",
) -> dict[str, Any]:
    """闭环仿真。"""
    current = PIDParams(
        kp=float(current_pid.get("kp", 0)),
        ti=float(current_pid.get("ti", 0)),
        td=float(current_pid.get("td", 0)),
    )
    recommended = PIDParams(
        kp=float(recommended_pid.get("kp", 0)),
        ti=float(recommended_pid.get("ti", 0)),
        td=float(recommended_pid.get("td", 0)),
    )

    result = simulate_closed_loop(
        model_type=model_type,
        model_params=model_params,
        current_pid=current,
        recommended_pid=recommended,
        sim_duration=sim_duration,
        sim_step=sim_step,
        setpoint_step=setpoint_step,
        disturbance_type=disturbance_type,
    )

    return result


# ---------------------------------------------------------------------------
# 整定任务管理
# ---------------------------------------------------------------------------


async def create_tuning_task(
    db: AsyncSession,
    loop_id: str,
    model_type: str,
    model_params: dict[str, Any],
    algorithm: str,
    recommended_pid: dict[str, Any],
    current_pid: dict[str, Any] | None = None,
    fitting_score: float | None = None,
    simulation_result: dict[str, Any] | None = None,
    status: str = "SIMULATED",
    created_by: str | None = None,
) -> dict[str, Any]:
    """创建整定任务记录。"""
    # 校验回路
    loop = await _get_loop(db, loop_id)

    record = TuningRecord(
        id=str(uuid4()),
        loop_id=loop_id,
        model_type=model_type,
        model_params=model_params,
        algorithm=algorithm,
        recommended_pid=recommended_pid,
        simulation_result=simulation_result,
        fitting_score=fitting_score,
        status=status,
        created_by=created_by,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return _record_to_dict(record, loop.tag_name)


async def list_tuning_tasks(
    db: AsyncSession,
    *,
    loop_id: str | None = None,
    algorithm: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """查询整定任务列表（分页）。"""
    query = select(TuningRecord, LoopLedger.tag_name).outerjoin(
        LoopLedger, TuningRecord.loop_id == LoopLedger.id
    )

    if loop_id:
        query = query.where(TuningRecord.loop_id == loop_id)
    if algorithm:
        query = query.where(TuningRecord.algorithm == algorithm)
    if status:
        query = query.where(TuningRecord.status == status)

    # 总数
    count_query = select(func.count()).select_from(TuningRecord)
    if loop_id:
        count_query = count_query.where(TuningRecord.loop_id == loop_id)
    if algorithm:
        count_query = count_query.where(TuningRecord.algorithm == algorithm)
    if status:
        count_query = count_query.where(TuningRecord.status == status)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页
    query = query.order_by(TuningRecord.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    items = [_record_to_dict(r[0], r[1]) for r in rows]

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_tuning_task_detail(db: AsyncSession, task_id: str) -> dict[str, Any]:
    """获取整定任务详情。"""
    result = await db.execute(
        select(TuningRecord, LoopLedger.tag_name)
        .outerjoin(LoopLedger, TuningRecord.loop_id == LoopLedger.id)
        .where(TuningRecord.id == task_id)
    )
    row = result.first()
    if row is None:
        raise BizError(
            code="ERR_TUNING_TASK_NOT_FOUND",
            message="整定任务不存在",
            status_code=404,
        )
    return _record_to_dict(row[0], row[1], include_detail=True)


async def get_tuning_history_stats(db: AsyncSession) -> dict[str, Any]:
    """整定历史统计。"""
    # 总数
    total_result = await db.execute(select(func.count()).select_from(TuningRecord))
    total = total_result.scalar() or 0

    # 按算法分组
    algo_result = await db.execute(
        select(TuningRecord.algorithm, func.count())
        .group_by(TuningRecord.algorithm)
    )
    by_algorithm = {row[0]: row[1] for row in algo_result.all()}

    # 按状态分组
    status_result = await db.execute(
        select(TuningRecord.status, func.count())
        .group_by(TuningRecord.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    # 平均拟合度
    avg_result = await db.execute(
        select(func.avg(TuningRecord.fitting_score)).where(TuningRecord.fitting_score.isnot(None))
    )
    avg_fitting = avg_result.scalar()
    avg_fitting_score = round(float(avg_fitting), 2) if avg_fitting else None

    # 最近 10 条任务
    recent_result = await db.execute(
        select(TuningRecord, LoopLedger.tag_name)
        .outerjoin(LoopLedger, TuningRecord.loop_id == LoopLedger.id)
        .order_by(TuningRecord.created_at.desc())
        .limit(10)
    )
    recent_tasks = [_record_to_dict(r[0], r[1]) for r in recent_result.all()]

    return {
        "totalTasks": total,
        "byAlgorithm": by_algorithm,
        "byStatus": by_status,
        "avgFittingScore": avg_fitting_score,
        "recentTasks": recent_tasks,
    }


def get_tuning_methods() -> list[dict[str, Any]]:
    """获取整定方法信息。"""
    return TUNING_METHODS_INFO


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _get_loop(db: AsyncSession, loop_id: str) -> LoopLedger:
    """获取回路，不存在则抛错。"""
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )
    return loop


def _record_to_dict(
    record: TuningRecord,
    tag_name: str | None = None,
    include_detail: bool = False,
) -> dict[str, Any]:
    """TuningRecord → dict（camelCase）。"""
    data: dict[str, Any] = {
        "id": str(record.id),
        "loopId": str(record.loop_id),
        "tagName": tag_name,
        "modelType": record.model_type,
        "modelParams": record.model_params,
        "algorithm": record.algorithm,
        "recommendedPid": record.recommended_pid,
        "fittingScore": float(record.fitting_score) if record.fitting_score else None,
        "status": record.status,
        "createdBy": record.created_by,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
    }
    if include_detail:
        data["simulationResult"] = record.simulation_result
    return data


__all__ = [
    "identify_model",
    "tune_pid",
    "run_simulation",
    "create_tuning_task",
    "list_tuning_tasks",
    "get_tuning_task_detail",
    "get_tuning_history_stats",
    "get_tuning_methods",
]
