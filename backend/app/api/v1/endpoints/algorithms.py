"""算法服务接口 (IDS v3.2 §2.7).

本组 API 包装现有 services 层逻辑（MetricCalculator / diagnosis_engine /
tuning_algorithms），为外部系统提供同步计算接口；并提供算法任务状态查询
（Celery AsyncResult）。

路由清单：
- POST /api/v1/algorithms/kpi/calculate       — 同步 KPI 计算（单回路单指标）
- POST /api/v1/algorithms/diagnosis/analyze    — 同步诊断分析（单回路）
- POST /api/v1/algorithms/tuning/calculate     — 同步整定计算（PID 参数）
- GET  /api/v1/algorithms/tasks/{task_id}      — 查询算法任务状态（Celery）

设计依据：IDS §2.7.1/§2.7.2/§2.7.3/§2.7.4
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.models.sys_user import SysUser
from app.schemas.algorithm import (
    AlgorithmTaskStatus,
    DiagnosisAnalyzeRequest,
    DiagnosisAnalyzeResponse,
    DiagnosisLabelResult,
    KpiCalculateRequest,
    KpiMetricResult,
    TuningCalculateRequest,
    TuningCalculateResponse,
)
from app.schemas.common import ApiResponse, success

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/algorithms", tags=["algorithms"])

# 允许调用算法服务的角色
_KPI_DIAG_ROLES = ("ADMIN",)
_TUNING_ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT")

# 有效的诊断标签枚举（对齐 IDS §2.7.2，8 类）
_VALID_DIAG_LABELS = frozenset(
    {
        "OSCILLATION",
        "VALVE_STICTION",
        "OVERAGGRESSIVE",
        "OVERCONSERVATIVE",
        "EXTERNAL_DISTURBANCE",
        "QUALITY_ABNORMAL",
        "OUTPUT_SATURATION",
        "MANUAL_REVIEW",
    }
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _parse_time(start: str, end: str) -> tuple[datetime, datetime]:
    """解析 ISO 8601 时间字符串.

    Raises:
        BizError: 时间格式无效或起始时间不早于结束时间
    """
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        raise BizError(
            code="ERR_ALGORITHM_INVALID_PARAMS",
            message=f"无效的起始时间格式: {start}",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from None
    try:
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        raise BizError(
            code="ERR_ALGORITHM_INVALID_PARAMS",
            message=f"无效的结束时间格式: {end}",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from None
    if start_dt >= end_dt:
        raise BizError(
            code="ERR_ALGORITHM_INVALID_PARAMS",
            message="起始时间必须早于结束时间",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return start_dt, end_dt


def _confidence_from_valid_rate(valid_rate: float) -> str:
    """根据有效数据率推断置信度等级（IDS §2.7.1）.

    A（≥0.95）/B（0.90-0.95）/C（0.80-0.90）/D（0.60-0.80）/E（<0.60）
    """
    if valid_rate >= 0.95:
        return "A"
    if valid_rate >= 0.90:
        return "B"
    if valid_rate >= 0.80:
        return "C"
    if valid_rate >= 0.60:
        return "D"
    return "E"


def _validate_labels(labels: list[str]) -> None:
    """校验诊断标签枚举.

    Raises:
        BizError: ERR_LABEL_INVALID — 标签不在 8 类枚举内
    """
    for label in labels:
        if label not in _VALID_DIAG_LABELS:
            raise BizError(
                code="ERR_LABEL_INVALID",
                message=(
                    f"无效的诊断标签: {label}，可选值: {', '.join(sorted(_VALID_DIAG_LABELS))}"
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )


# ---------------------------------------------------------------------------
# §2.7.1 POST /algorithms/kpi/calculate — 同步 KPI 计算
# ---------------------------------------------------------------------------


@router.post("/kpi/calculate", response_model=ApiResponse[KpiMetricResult])
async def calculate_kpi(
    body: KpiCalculateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_KPI_DIAG_ROLES)),
) -> dict:
    """同步 KPI 计算（单回路单指标，不走 Celery）.

    调用 ``app.services.metric_calculator`` 注册表中的指标计算器，
    通过 DataPlanner 拉取数据后执行同步计算，返回指标值与数据血缘。

    设计依据：IDS §2.7.1
    """
    from app.services.metric_calculator import get_calculator

    _parse_time(body.startTime, body.endTime)

    calculator = get_calculator(body.metric)
    if calculator is None:
        raise BizError(
            code="ERR_ALGORITHM_INVALID_PARAMS",
            message=f"未知的指标代码: {body.metric}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 通过 DataPlanner 拉取该指标的数据包
    try:
        from app.api.v1.endpoints.dataplanner import _build_data_planner
        from app.contracts.data_types import ControlType, TimeWindow
    except ImportError:
        # DataPlanner 不可用时，回退到空结果（保持接口可用）
        logger.warning("DataPlanner 不可用，返回 INCONCLUSIVE 结果")
        resp = KpiMetricResult(
            loopId=body.loopId,
            metric=body.metric,
            value=None,
            confidenceLevel="E",
            validRate=0.0,
        )
        return success(data=resp.model_dump())

    # 默认使用 BASE 控制类型；具体回路类型可由调用方扩展
    control_type = ControlType.FLOW  # FLOW 对应 FC，作为通用默认值
    start_dt, end_dt = _parse_time(body.startTime, body.endTime)
    time_window = TimeWindow(start=start_dt, end=end_dt)
    planner = _build_data_planner(db)

    try:
        bundles = await planner.request_bundles(
            loop_id=body.loopId,
            metrics=[body.metric],
            time_window=time_window,
            control_type=control_type,
        )
    except Exception:
        logger.exception(
            "KPI 计算取数失败: loop=%s, metric=%s",
            body.loopId,
            body.metric,
        )
        raise BizError(
            code="ERR_ALGORITHM_DATA_INSUFFICIENT",
            message=f"取数失败: loop={body.loopId}, metric={body.metric}",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from None

    if not bundles:
        logger.warning(
            "KPI 计算无可用数据: loop=%s, metric=%s",
            body.loopId,
            body.metric,
        )
        resp = KpiMetricResult(
            loopId=body.loopId,
            metric=body.metric,
            value=None,
            confidenceLevel="E",
            validRate=0.0,
        )
        return success(data=resp.model_dump())

    # 取第一个 Bundle 计算（单指标通常只有一个 Bundle）
    bundle = bundles[0]
    try:
        metric_result = calculator.calculate(bundle)
    except Exception:
        logger.exception(
            "KPI 计算异常: loop=%s, metric=%s",
            body.loopId,
            body.metric,
        )
        raise BizError(
            code="ERR_ALGORITHM_INVALID_PARAMS",
            message=f"指标计算异常: {body.metric}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    valid_rate = float(getattr(metric_result, "valid_rate", 0.0) or 0.0)
    confidence = _confidence_from_valid_rate(valid_rate)
    value = float(metric_result.value) if metric_result.value is not None else None

    # 数据血缘：从 bundle.lineage 转换
    lineage_dict: dict[str, Any] | None = None
    lineage = getattr(bundle, "lineage", None)
    if lineage is not None:
        lineage_dict = {
            "sampling_freq": getattr(lineage, "sampling_freq", "") or "",
            "quality_policy": getattr(lineage, "quality_policy", "") or "",
            "tag_group": getattr(bundle.data_block, "tag_group", "") or "",
            "valid_rate": valid_rate,
        }

    resp = KpiMetricResult(
        loopId=body.loopId,
        metric=body.metric,
        value=value,
        confidenceLevel=confidence,
        validRate=round(valid_rate, 4),
        dataLineage=lineage_dict,
    )

    logger.info(
        "KPI 计算完成: loop=%s, metric=%s, value=%s, confidence=%s",
        body.loopId,
        body.metric,
        value,
        confidence,
    )
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# §2.7.2 POST /algorithms/diagnosis/analyze — 同步诊断分析
# ---------------------------------------------------------------------------


@router.post("/diagnosis/analyze", response_model=ApiResponse[DiagnosisAnalyzeResponse])
async def analyze_diagnosis(
    body: DiagnosisAnalyzeRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_KPI_DIAG_ROLES)),
) -> dict:
    """同步诊断分析（单回路，不走 Celery）.

    调用 ``app.tasks.diagnosis_engine._do_diagnose_single_loop`` 执行单回路
    诊断，返回 8 类诊断标签结果（含置信度与证据）。

    设计依据：IDS §2.7.2
    """
    _parse_time(body.startTime, body.endTime)
    _validate_labels(body.labels)

    from app.tasks.diagnosis_engine import _do_diagnose_single_loop

    try:
        result = await _do_diagnose_single_loop(body.loopId, ts_start=body.startTime)
    except Exception:
        logger.exception("诊断分析失败: loop=%s", body.loopId)
        raise BizError(
            code="ERR_ALGORITHM_DATA_INSUFFICIENT",
            message=f"诊断分析失败: loop={body.loopId}",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from None

    # 解析诊断引擎返回结果，转换为 DiagnosisLabelResult 列表
    diag_labels: list[DiagnosisLabelResult] = []
    label_list = result.get("diagnosis_labels", []) if isinstance(result, dict) else []
    requested = set(body.labels) if body.labels else None

    for item in label_list:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or item.get("diag_label") or ""
        if not label:
            continue
        if requested is not None and label not in requested:
            continue
        confidence = float(item.get("confidence", 0.0) or 0.0)
        # confidence 在 DB 中以 0-100 存储，转换为 0-1
        if confidence > 1.0:
            confidence = confidence / 100.0
        evidence = item.get("evidence_chain") or item.get("evidence") or {}
        algorithm = item.get("algorithm") or item.get("algorithm_version")
        fused = None
        if body.enableFusion:
            fused_raw = item.get("fused_confidence")
            if fused_raw is not None:
                fused = float(fused_raw)
                if fused > 1.0:
                    fused = fused / 100.0
        diag_labels.append(
            DiagnosisLabelResult(
                label=label,
                confidence=round(confidence, 4),
                evidence=evidence if isinstance(evidence, dict) else {},
                algorithm=algorithm,
                fusedConfidence=round(fused, 4) if fused is not None else None,
            )
        )

    tag_name = result.get("tag_name") if isinstance(result, dict) else None
    algorithm_version = (
        result.get("algorithm_version", "DIAG_ENGINE_v1.0")
        if isinstance(result, dict)
        else "DIAG_ENGINE_v1.0"
    )

    resp = DiagnosisAnalyzeResponse(
        loopId=body.loopId,
        tagName=tag_name,
        diagnosisLabels=diag_labels,
        algorithmVersion=algorithm_version,
    )

    logger.info(
        "诊断分析完成: loop=%s, labels=%d, user=%s",
        body.loopId,
        len(diag_labels),
        user.username,
    )
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# §2.7.3 POST /algorithms/tuning/calculate — 同步整定计算
# ---------------------------------------------------------------------------


@router.post("/tuning/calculate", response_model=ApiResponse[TuningCalculateResponse])
async def calculate_tuning(
    body: TuningCalculateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles(*_TUNING_ROLES)),
) -> dict:
    """同步整定计算（PID 参数，不走 Celery）.

    串联调用 ``app.services.tuning.identify_model`` → ``tune_pid`` →
    ``run_simulation``，返回模型参数、PID 参数与仿真性能指标。

    设计依据：IDS §2.7.3
    """
    from app.schemas.algorithm import (
        ModelParamsSchema,
        PIDParamsSchema,
        SimulationResultSchema,
    )
    from app.services.tuning import identify_model, run_simulation, tune_pid

    seg = body.identificationParams.dataSegment
    _parse_time(seg.startTime, seg.endTime)

    # 1. 模型辨识
    try:
        identify_result = await identify_model(
            db=db,
            loop_id=body.loopId,
            start_time=seg.startTime,
            end_time=seg.endTime,
            model_type=body.identificationParams.modelType,
            method=body.identificationParams.method,
        )
    except BizError:
        raise
    except Exception:
        logger.exception("整定模型辨识失败: loop=%s", body.loopId)
        raise BizError(
            code="ERR_ALGORITHM_DATA_INSUFFICIENT",
            message=f"模型辨识失败: loop={body.loopId}",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        ) from None

    model_type = identify_result.get("modelType", body.identificationParams.modelType)
    params_dict = identify_result.get("params", {}) or {}
    model_params = ModelParamsSchema(
        K=params_dict.get("K"),
        tau=params_dict.get("tau"),
        theta=params_dict.get("theta"),
        T1=params_dict.get("T1"),
        T2=params_dict.get("T2"),
    )
    fitting_score = identify_result.get("fittingScore")

    # 2. PID 整定
    try:
        tune_result = await tune_pid(
            model_type=model_type,
            model_params=params_dict,
            algorithm=body.tuningParams.method,
            algorithm_params=body.tuningParams.params or None,
            loop_id=body.loopId,
        )
    except BizError:
        raise
    except Exception:
        logger.exception("PID 整定失败: loop=%s", body.loopId)
        raise BizError(
            code="ERR_ALGORITHM_INVALID_PARAMS",
            message=f"PID 整定失败: loop={body.loopId}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    recommended_pid_raw = tune_result.get("recommended_pid") or tune_result.get("pid") or {}
    pid_params = PIDParamsSchema(
        Kp=recommended_pid_raw.get("Kp") or recommended_pid_raw.get("kp"),
        Ti=recommended_pid_raw.get("Ti") or recommended_pid_raw.get("ti"),
        Td=recommended_pid_raw.get("Td") or recommended_pid_raw.get("td"),
    )

    # 3. 闭环仿真（可选）
    sim_schema: SimulationResultSchema | None = None
    if body.enableSimulation and body.simulationConfig is not None:
        try:
            sim_result = await run_simulation(
                model_type=model_type,
                model_params=params_dict,
                current_pid=tune_result.get("current_pid", {}) or tune_result.get("current_pid"),
                recommended_pid=recommended_pid_raw,
                sim_duration=body.simulationConfig.simulationDuration,
                sim_step=body.identificationParams.samplePeriod,
                setpoint_step=1.0,
                disturbance_type=body.simulationConfig.disturbanceType,
            )
            sim_metrics = sim_result.get("metrics") or sim_result
            sim_schema = SimulationResultSchema(
                riseTime=sim_metrics.get("riseTime") or sim_metrics.get("rise_time"),
                overshoot=sim_metrics.get("overshoot"),
                settlingTime=sim_metrics.get("settlingTime") or sim_metrics.get("settling_time"),
                itae=sim_metrics.get("itae"),
            )
        except Exception:
            # 仿真失败不阻断整体计算，仅记录告警
            logger.warning(
                "闭环仿真失败（不影响整定结果）: loop=%s",
                body.loopId,
                exc_info=True,
            )

    algorithm_version = tune_result.get("algorithmVersion") or "TUNE_ENGINE_v1.0"

    resp = TuningCalculateResponse(
        loopId=body.loopId,
        modelType=model_type,
        modelParams=model_params,
        fittingScore=fitting_score,
        pidParams=pid_params,
        simulationResult=sim_schema,
        algorithmVersion=algorithm_version,
    )

    logger.info(
        "整定计算完成: loop=%s, model=%s, pid=(Kp=%s, Ti=%s, Td=%s)",
        body.loopId,
        model_type,
        pid_params.Kp,
        pid_params.Ti,
        pid_params.Td,
    )
    return success(data=resp.model_dump())


# ---------------------------------------------------------------------------
# §2.7.4 GET /algorithms/tasks/{task_id} — 算法任务状态查询
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}", response_model=ApiResponse[AlgorithmTaskStatus])
async def get_algorithm_task_status(
    task_id: str,
    _: SysUser = Depends(require_roles(*_KPI_DIAG_ROLES, *_TUNING_ROLES)),
) -> dict:
    """查询算法任务状态（Celery AsyncResult）.

    通过 Celery ``AsyncResult`` 查询任务状态与结果，统一返回
    PENDING/STARTED/SUCCESS/FAILURE/REVOKED 状态。

    设计依据：IDS §2.7.4
    """
    from celery.result import AsyncResult

    from app.tasks.celery_app import celery_app

    try:
        result = AsyncResult(task_id, app=celery_app)
    except Exception:
        logger.exception("查询 Celery 任务状态失败: task_id=%s", task_id)
        raise BizError(
            code="ERR_TASK_NOT_FOUND",
            message=f"任务查询失败: {task_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from None

    state = result.state or "PENDING"
    progress: float | None = None
    task_result: Any = None
    error: str | None = None
    received_at: str | None = None

    if state == "SUCCESS":
        try:
            task_result = result.result
        except Exception:
            logger.warning("读取任务结果失败: task_id=%s", task_id, exc_info=True)
    elif state in ("FAILURE", "REVOKED"):
        try:
            exc = result.result
            error = str(exc) if exc is not None else state
        except Exception:
            error = state

    # 尝试读取任务信息（progress / received_at）
    try:
        info = result.info
        if isinstance(info, dict):
            if "progress" in info:
                progress = float(info["progress"])
            if "received_at" in info:
                received_at = str(info["received_at"])
    except Exception:
        # info 在 PENDING/STARTED 状态下可能不可读，忽略
        pass

    resp = AlgorithmTaskStatus(
        taskId=task_id,
        status=state,
        progress=progress,
        result=task_result,
        error=error,
        receivedAt=received_at,
    )

    logger.info(
        "算法任务状态查询: task_id=%s, status=%s",
        task_id,
        state,
    )
    return success(data=resp.model_dump())


__all__ = ["router"]
