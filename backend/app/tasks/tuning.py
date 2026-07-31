"""Celery tasks for loop tuning Phase 2 (tuning-phase2-technical-plan §3.4).

任务清单：
- ``identify_model_task`` — 异步历史数据模型辨识（DataPlanner → 算法栈 → 可信度评估）
- ``tune_and_simulate_task`` — 异步 PID 整定 + 多 PID 闭环仿真对比

设计要点：
- 使用 AsyncTask 基类在 Celery 同步 worker 中执行 async 代码
- 进度通过 ``tuning_progress`` 写入 Redis（自包含，不依赖共享 TaskTracker）
- 结果落 ``TuningRecord`` 表，通过 ``task_id`` 关联 Celery 任务
- 失败不自动重试（辨识失败 → INCONCLUSIVE 状态，需用户调整数据窗口）
- AUTO 策略：历史辨识失败/数据不足自动降级阶跃实验路径
  （结果标注 dataSource=fallback_step，P1-6）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from celery import Task

from app.core.exceptions import BizError
from app.tasks.celery_app import celery_app

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.services.process_model_version import create_candidate_version

logger = logging.getLogger(__name__)


def _build_version_metrics(result: dict[str, Any]) -> dict[str, Any] | None:
    """从辨识结果提取验证指标快照（process_model_version.metrics）."""
    metrics: dict[str, Any] = {}
    for key in (
        "fittingScore",
        "excitationScore",
        "r2Train",
        "r2Val",
        "nrmseVal",
        "aic",
        "bic",
    ):
        val = result.get(key)
        if val is not None:
            metrics[key] = val
    return metrics or None


def _build_version_residual_test(result: dict[str, Any]) -> dict[str, Any] | None:
    """从辨识结果提取残差检验快照（process_model_version.residual_test）."""
    passed = result.get("residualTestPassed")
    if passed is None:
        return None
    snapshot: dict[str, Any] = {"passed": bool(passed)}
    detail = result.get("residualTestDetail")
    if detail:
        snapshot["detail"] = detail
    return snapshot


class AsyncTask(Task):
    """Base task that runs an async function in a fresh event loop."""

    abstract = True

    def run_async(self, coro):
        """Run a coroutine in a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _parse_iso_naive(time_str: str) -> datetime:
    """解析 ISO 8601 时间为 naive datetime（DB 存储口径）."""
    return datetime.fromisoformat(time_str.replace("Z", "+00:00")).replace(tzinfo=None)


def _now_naive() -> datetime:
    """当前 naive datetime（DB 存储口径，与其他模型一致）."""
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 异步历史数据模型辨识
# ---------------------------------------------------------------------------


async def _step_identify_fallback(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    history_result: dict[str, Any],
) -> dict[str, Any]:
    """AUTO 策略阶跃兜底（P1-6）：历史辨识失败/数据不足时降级阶跃实验路径.

    调用保留的同步阶跃辨识（FOPDT 两点法）；成功时返回成功形态结果并
    标注 dataSource=fallback_step；兜底亦失败时合并两条失败原因，
    维持失败形态（success=False）。
    """
    from app.services.tuning import identify_model, validate_step_identification_result

    history_reason = history_result.get("reason") or "历史辨识失败"
    try:
        step = await identify_model(
            db=db,
            loop_id=loop_id,
            start_time=start_time,
            end_time=end_time,
            model_type="FOPDT",
            method=None,
        )
    except Exception as exc:  # 兜底失败（数据不足/回路不存在/数据源异常等）
        logger.warning("AUTO 阶跃兜底失败: loop_id=%s, err=%s", loop_id, exc)
        merged = dict(history_result)
        merged["reason"] = f"{history_reason}；AUTO 阶跃兜底亦失败: {exc}"
        return merged

    validation_error = validate_step_identification_result(step)
    if validation_error:
        logger.warning("AUTO 阶跃兜底拒绝: loop_id=%s, reason=%s", loop_id, validation_error)
        merged = dict(history_result)
        merged["reason"] = f"{history_reason}；AUTO 阶跃兜底亦失败: {validation_error}"
        return merged

    logger.info("AUTO 阶跃兜底成功: loop_id=%s（历史失败原因: %s）", loop_id, history_reason)
    return {
        "success": True,
        "modelType": step["modelType"],
        "params": step["params"],
        "fittingScore": step["fittingScore"],
        "identifyMethod": "STEP_TWO_POINT",
        "dataSource": "fallback_step",
        "confidenceReason": (
            f"AUTO 兜底：{history_reason}，已降级阶跃实验路径（FOPDT 两点法）；"
            "step_validation_passed=true"
        ),
        "algorithmVersion": step["algorithmVersion"],
        "dataPoints": step["dataPoints"],
        "validRate": history_result.get("validRate"),
        "tagName": step.get("tagName"),
        "fallbackReason": history_reason,
    }


async def _do_identify(
    task_id: str,
    loop_id: str,
    start_time: str,
    end_time: str,
    candidate_model_types: list[str] | None,
    theta_estimate: float | None,
    created_by: str,
    identify_strategy: str = "HISTORY_ONLY",
    created_by_id: str = "",
) -> dict[str, Any]:
    """执行历史数据辨识的 async 逻辑.

    identify_strategy=AUTO 时，历史辨识失败/数据不足自动降级阶跃实验路径
    （结果标注 dataSource=fallback_step，P1-6）；默认 HISTORY_ONLY
    与未携带该参数的旧队列任务行为一致。

    V62-P1-013: created_by_id 传入 init_progress 以桥接 TaskTracker。
    """
    from app.core.db import AsyncSessionLocal
    from app.models.tuning import TuningRecord
    from app.services.tuning import identify_model_from_history
    from app.services.tuning_progress import init_progress, update_progress

    await init_progress(
        task_id,
        task_type="identify",
        loop_id=loop_id,
        created_by=created_by,
        created_by_id=created_by_id,
        ts_start=start_time,
        ts_end=end_time,
    )

    async with AsyncSessionLocal() as db:
        # 创建占位 TuningRecord（status=RUNNING）
        record = TuningRecord(
            id=str(uuid4()),
            loop_id=loop_id,
            model_type="FOPDT",  # 占位，辨识完成后更新
            model_params=None,
            # V62-P3-006：纯辨识记录不再用 IMC 占位，改为 IDENTIFICATION_ONLY
            algorithm="IDENTIFICATION_ONLY",
            status="RUNNING",
            created_by=created_by,
            task_id=task_id,
            time_window_start=_parse_iso_naive(start_time),
            time_window_end=_parse_iso_naive(end_time),
            data_source="HISTORY",
        )
        db.add(record)
        await db.commit()
        record_id = str(record.id)

        try:
            await update_progress(
                task_id,
                status="RUNNING",
                stage="excitation",
                message="激励检测与数据预处理中...",
            )

            try:
                result = await identify_model_from_history(
                    db=db,
                    loop_id=loop_id,
                    start_time=start_time,
                    end_time=end_time,
                    candidate_model_types=candidate_model_types,
                    theta_estimate=theta_estimate,
                )
            except BizError as exc:
                # 数据不足/回路不存在等业务失败与算法栈失败同口径：
                # 转 INCONCLUSIVE 结果形态，供 AUTO 策略兜底判断
                logger.info("历史辨识业务失败: task_id=%s, %s: %s", task_id, exc.code, exc.message)
                result = {"success": False, "reason": f"{exc.code}: {exc.message}"}

            # P1-6：AUTO 策略兜底 — 历史辨识失败/数据不足时降级阶跃实验路径
            if not result.get("success") and identify_strategy == "AUTO":
                result = await _step_identify_fallback(db, loop_id, start_time, end_time, result)

            await update_progress(
                task_id,
                stage="discrete_to_continuous",
                message="离散→连续转换完成，评估可信度中...",
            )

            # 更新 TuningRecord
            db_record = await db.get(TuningRecord, record_id)
            if db_record is None:
                raise RuntimeError(f"TuningRecord {record_id} 不存在")

            if result.get("success"):
                best = result.get("bestModel") or {}
                params = result.get("params") or best.get("params") or {}
                model_type = result.get("modelType") or best.get("modelType") or "FOPDT"

                # V62-P3-005：停止旧参数新写——model_params 写入 process_model_version
                # tuning_record 仅保留 model_type（NOT NULL，查询便利）+ FK 引用；
                # model_params 字段对新记录保持 NULL，仅遗留记录保留只读快照。
                confidence_reason = _with_theta_source_token(
                    result.get("confidenceReason"),
                    result.get("thetaSource"),
                )
                version = await create_candidate_version(
                    db,
                    loop_id=loop_id,
                    model_type=model_type,
                    model_params=dict(params),
                    identify_method=result.get("identifyMethod"),
                    algorithm_version=result.get("algorithmVersion"),
                    theta_source=result.get("thetaSource"),
                    sampling_period=result.get("samplingPeriod"),
                    data_window_start=_parse_iso_naive(start_time),
                    data_window_end=_parse_iso_naive(end_time),
                    data_hash=result.get("dataHash"),
                    condition_summary=result.get("conditionSummary"),
                    metrics=_build_version_metrics(result),
                    residual_test=_build_version_residual_test(result),
                    uncertainty=result.get("uncertainty"),
                    physical_feasibility=result.get("physicalFeasibility"),
                    confidence_level=result.get("confidenceLevel"),
                    confidence_reason=confidence_reason,
                    created_by=created_by,
                )
                db_record.model_type = model_type
                # P3-005：不再写 db_record.model_params = params（停止旧参数新写）
                db_record.process_model_version_id = str(version.id)
                db_record.fitting_score = result.get("fittingScore")
                db_record.identify_method = result.get("identifyMethod")
                db_record.confidence_level = result.get("confidenceLevel")
                db_record.confidence_reason = confidence_reason
                db_record.excitation_score = result.get("excitationScore")
                db_record.residual_test_passed = result.get("residualTestPassed")
                if result.get("dataSource") == "fallback_step":
                    # AUTO 阶跃兜底结果：标注数据来源（P1-6）
                    db_record.data_source = "fallback_step"
                db_record.status = "IDENTIFIED"
                db_record.completed_at = _now_naive()
            else:
                db_record.status = "INCONCLUSIVE"
                db_record.confidence_reason = result.get("reason", "辨识失败")
                db_record.completed_at = _now_naive()

            await db.commit()

            await update_progress(
                task_id,
                status="SUCCESS",
                stage="discrete_to_continuous",
                progress=100.0,
                message="辨识完成",
                result={"recordId": record_id, **_serialize_result(result)},
            )

            return {"recordId": record_id, **result}

        except Exception as exc:
            logger.exception("辨识任务失败: task_id=%s", task_id)
            db_record = await db.get(TuningRecord, record_id)
            if db_record is not None:
                db_record.status = "INCONCLUSIVE"
                db_record.confidence_reason = f"任务异常: {exc}"
                db_record.completed_at = _now_naive()
                await db.commit()

            await update_progress(
                task_id,
                status="FAILED",
                message=f"辨识任务失败: {exc}",
                error=str(exc),
            )
            raise


@celery_app.task(
    name="app.tasks.tuning.identify_model_task",
    bind=True,
    base=AsyncTask,
    time_limit=120,
    soft_time_limit=100,
)
def identify_model_task(
    self: AsyncTask,
    loop_id: str,
    start_time: str,
    end_time: str,
    candidate_model_types: list[str] | None = None,
    theta_estimate: float | None = None,
    created_by: str = "system",
    identify_strategy: str = "HISTORY_ONLY",
    created_by_id: str = "",
) -> dict[str, Any]:
    """异步历史数据模型辨识任务.

    Args:
        loop_id: 回路 ID
        start_time: 起始时间（ISO 8601）
        end_time: 结束时间（ISO 8601）
        candidate_model_types: 候选模型类型（默认 FOPDT+SOPDT）
        theta_estimate: 纯滞后预估（秒）
        created_by: 创建人
        identify_strategy: 辨识策略 AUTO/HISTORY_ONLY（STEP_ONLY 由端点同步拦截，
            不会进入本任务）；AUTO 时历史辨识失败/数据不足自动降级阶跃实验
            路径（P1-6）。默认 HISTORY_ONLY，与未携带该参数的旧队列任务行为一致
        created_by_id: 创建人用户 ID（V62-P1-013 桥接 TaskTracker 用）
    """
    task_id = self.request.id
    logger.info("辨识任务开始: task_id=%s, loop_id=%s", task_id, loop_id)
    return self.run_async(
        _do_identify(
            task_id=task_id,
            loop_id=loop_id,
            start_time=start_time,
            end_time=end_time,
            candidate_model_types=candidate_model_types,
            theta_estimate=theta_estimate,
            created_by=created_by,
            identify_strategy=identify_strategy,
            created_by_id=created_by_id,
        )
    )


# ---------------------------------------------------------------------------
# 异步 PID 整定 + 多 PID 仿真对比
# ---------------------------------------------------------------------------


async def _do_tune_and_simulate(
    task_id: str,
    loop_id: str,
    model_type: str,
    model_params: dict[str, Any],
    algorithms: list[str],
    current_pid: dict[str, Any] | None,
    sim_duration: float,
    sim_step: float,
    setpoint_step: float,
    created_by: str,
    source_record_id: str | None = None,
    model_source: str | None = None,
    risk_confirmed: bool = False,
    step_validation_passed: bool = False,
    created_by_id: str = "",
) -> dict[str, Any]:
    """执行整定 + 仿真的 async 逻辑.

    V62-P1-013: created_by_id 传入 init_progress 以桥接 TaskTracker。
    """
    from app.core.db import AsyncSessionLocal
    from app.models.tuning import TuningRecord
    from app.services.tuning import (
        _simulate_multi_pid,
        authorize_tuning_model,
        tune_pid,
    )
    from app.services.tuning_progress import init_progress, update_progress

    await init_progress(
        task_id,
        task_type="tune_and_simulate",
        loop_id=loop_id,
        created_by=created_by,
        created_by_id=created_by_id,
    )

    async with AsyncSessionLocal() as db:
        source_context = await authorize_tuning_model(
            db=db,
            requested_model_type=model_type,
            requested_model_params=model_params,
            loop_id=loop_id,
            source_record_id=source_record_id,
            model_source=model_source,
            risk_confirmed=risk_confirmed,
            trusted_step_validation=step_validation_passed,
        )
        model_type = source_context.model_type
        model_params = source_context.model_params
        loop_id = source_context.loop_id or loop_id
        provenance = (
            f"model_source={source_context.model_source};"
            f"source_record={source_context.source_record_id or '-'};"
            f"risk_confirmed={str(source_context.risk_confirmed).lower()}"
        )
        if source_context.model_source == "STEP_EXPERIMENT":
            provenance += ";step_validation_passed=true"

        record = TuningRecord(
            id=str(uuid4()),
            loop_id=loop_id,
            model_type=model_type,
            model_params=model_params,
            algorithm=algorithms[0] if algorithms else "IMC",
            status="RUNNING",
            created_by=created_by,
            task_id=task_id,
            data_source=(
                source_context.data_source
                if source_context.model_source == "IDENTIFICATION_RECORD"
                else (
                    "STEP_EXPERIMENT" if source_context.model_source == "STEP_EXPERIMENT" else None
                )
            ),
            confidence_level=source_context.confidence_level,
            confidence_reason=provenance[:200],
            identify_method=source_context.identify_method,
        )
        db.add(record)
        await db.commit()
        record_id = str(record.id)

        try:
            await update_progress(
                task_id,
                status="RUNNING",
                stage="tune",
                message="执行多算法 PID 整定中...",
            )

            # 多算法整定
            candidates: list[dict[str, Any]] = []
            primary_tune_result: dict[str, Any] | None = None
            for algo in algorithms:
                tune_result = await tune_pid(
                    model_type=model_type,
                    model_params=model_params,
                    algorithm=algo,
                    algorithm_params=None,
                    current_pid=current_pid,
                    loop_id=loop_id,
                    source_context=source_context,
                )
                if primary_tune_result is None:
                    primary_tune_result = tune_result
                candidates.append(
                    {
                        "label": algo,
                        "pid": tune_result["recommendedPid"],
                        "algorithm": algo,
                    }
                )

            await update_progress(
                task_id,
                stage="simulate",
                message="多 PID 闭环仿真对比中...",
            )

            # 多 PID 仿真对比
            sim_result = _simulate_multi_pid(
                model_type=model_type,
                model_params=model_params,
                current_pid=current_pid,
                pid_candidates=candidates,
                sim_duration=sim_duration,
                sim_step=sim_step,
                setpoint_step=setpoint_step,
            )

            # 取第一个算法的推荐 PID 作为主结果（兼容旧字段）
            primary = candidates[0] if candidates else {}
            primary_pid = primary.get("pid", {})

            await update_progress(
                task_id,
                status="SUCCESS",
                stage="simulate",
                progress=100.0,
                message="整定与仿真完成",
                result={"recordId": record_id},
            )

            # 更新 TuningRecord
            db_record = await db.get(TuningRecord, record_id)
            if db_record is not None:
                db_record.recommended_pid = primary_pid
                db_record.simulation_result = sim_result
                db_record.algorithm = primary.get("algorithm", algorithms[0])
                db_record.pid_candidates = candidates
                db_record.candidate_results = sim_result.get("candidateResponses")
                # V62-P3-007：持久化人工实施清单字段
                db_record.current_pid = dict(current_pid) if current_pid else None
                db_record.rollback_pid = dict(current_pid) if current_pid else None
                # 从主算法 tune_pid 结果提取风险评估
                if primary_tune_result and isinstance(primary_tune_result, dict):
                    db_record.risk_assessment = primary_tune_result.get("risk")
                db_record.status = "SIMULATED"
                db_record.completed_at = _now_naive()
                await db.commit()

            return {
                "recordId": record_id,
                "recommendedPid": primary_pid,
                "pidCandidates": candidates,
                "simulationResult": sim_result,
            }

        except Exception as exc:
            logger.exception("整定仿真任务失败: task_id=%s", task_id)
            db_record = await db.get(TuningRecord, record_id)
            if db_record is not None:
                db_record.status = "INCONCLUSIVE"
                db_record.confidence_reason = f"任务异常: {exc}"
                db_record.completed_at = _now_naive()
                await db.commit()

            await update_progress(
                task_id,
                status="FAILED",
                message=f"整定仿真任务失败: {exc}",
                error=str(exc),
            )
            raise


@celery_app.task(
    name="app.tasks.tuning.tune_and_simulate_task",
    bind=True,
    base=AsyncTask,
    time_limit=120,
    soft_time_limit=100,
)
def tune_and_simulate_task(
    self: AsyncTask,
    loop_id: str,
    model_type: str,
    model_params: dict[str, Any],
    algorithms: list[str] | None = None,
    current_pid: dict[str, Any] | None = None,
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
    created_by: str = "system",
    source_record_id: str | None = None,
    model_source: str | None = None,
    risk_confirmed: bool = False,
    step_validation_passed: bool = False,
    created_by_id: str = "",
) -> dict[str, Any]:
    """异步 PID 整定 + 多 PID 仿真对比任务.

    Args:
        loop_id: 回路 ID
        model_type: 模型类型 FOPDT/SOPDT/IPDT
        model_params: 模型参数 {K, tau, theta, ...}
        algorithms: 整定算法列表（如 ["IMC","LAMBDA","SIMC"]）
        current_pid: 当前 PID 参数
        sim_duration: 仿真时长（秒）
        sim_step: 仿真步长（秒）
        setpoint_step: 设定值阶跃幅值
        created_by: 创建人
        source_record_id: 服务端辨识记录 ID
        model_source: IDENTIFICATION_RECORD/STEP_EXPERIMENT/MANUAL
        risk_confirmed: C 级或人工模型风险确认
        step_validation_passed: 仅内部已验证阶跃编排可设置
        created_by_id: 创建人用户 ID（V62-P1-013 桥接 TaskTracker 用）
    """
    task_id = self.request.id
    algorithms = algorithms or ["IMC"]
    logger.info("整定仿真任务开始: task_id=%s, loop_id=%s", task_id, loop_id)
    return self.run_async(
        _do_tune_and_simulate(
            task_id=task_id,
            loop_id=loop_id,
            model_type=model_type,
            model_params=model_params,
            algorithms=algorithms,
            current_pid=current_pid,
            sim_duration=sim_duration,
            sim_step=sim_step,
            setpoint_step=setpoint_step,
            created_by=created_by,
            source_record_id=source_record_id,
            model_source=model_source,
            risk_confirmed=risk_confirmed,
            step_validation_passed=step_validation_passed,
            created_by_id=created_by_id,
        )
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _serialize_result(result: dict[str, Any]) -> dict[str, Any]:
    """将辨识结果转为 JSON 可序列化字典（过滤不可序列化字段）."""
    import json

    try:
        json.dumps(result, default=str)
        return result
    except (TypeError, ValueError):
        return {k: str(v) for k, v in result.items()}


def _with_theta_source_token(reason: Any, theta_source: Any) -> str | None:
    """在既有字段中持久化稳定 theta 来源 token，并保证不超过列长。"""
    if theta_source not in {"EXPLICIT", "HEURISTIC_2TS"}:
        return str(reason) if reason is not None else None

    token = f"theta_source={theta_source}"
    base = str(reason or "")
    if token in base:
        return base[:200]
    if not base:
        return token

    prefix_limit = 200 - len(token) - 1
    return f"{base[:prefix_limit]};{token}"


__all__ = ["identify_model_task", "tune_and_simulate_task"]
