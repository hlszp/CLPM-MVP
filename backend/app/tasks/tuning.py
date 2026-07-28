"""Celery tasks for loop tuning Phase 2 (tuning-phase2-technical-plan §3.4).

任务清单：
- ``identify_model_task`` — 异步历史数据模型辨识（DataPlanner → 算法栈 → 可信度评估）
- ``tune_and_simulate_task`` — 异步 PID 整定 + 多 PID 闭环仿真对比

设计要点：
- 使用 AsyncTask 基类在 Celery 同步 worker 中执行 async 代码
- 进度通过 ``tuning_progress`` 写入 Redis（自包含，不依赖共享 TaskTracker）
- 结果落 ``TuningRecord`` 表，通过 ``task_id`` 关联 Celery 任务
- 失败不自动重试（辨识失败 → INCONCLUSIVE 状态，需用户调整数据窗口）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from celery import Task

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


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


async def _do_identify(
    task_id: str,
    loop_id: str,
    start_time: str,
    end_time: str,
    candidate_model_types: list[str] | None,
    theta_estimate: float | None,
    created_by: str,
) -> dict[str, Any]:
    """执行历史数据辨识的 async 逻辑."""
    from app.core.db import AsyncSessionLocal
    from app.models.tuning import TuningRecord
    from app.services.tuning import identify_model_from_history
    from app.services.tuning_progress import init_progress, update_progress

    await init_progress(task_id, task_type="identify", loop_id=loop_id)

    async with AsyncSessionLocal() as db:
        # 创建占位 TuningRecord（status=RUNNING）
        record = TuningRecord(
            id=str(uuid4()),
            loop_id=loop_id,
            model_type="FOPDT",  # 占位，辨识完成后更新
            model_params=None,
            algorithm="IMC",  # 占位，整定时更新
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

            result = await identify_model_from_history(
                db=db,
                loop_id=loop_id,
                start_time=start_time,
                end_time=end_time,
                candidate_model_types=candidate_model_types,
                theta_estimate=theta_estimate,
            )

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

                db_record.model_type = model_type
                db_record.model_params = params
                db_record.fitting_score = result.get("fittingScore")
                db_record.identify_method = result.get("identifyMethod")
                db_record.confidence_level = result.get("confidenceLevel")
                db_record.confidence_reason = result.get("confidenceReason")
                db_record.excitation_score = result.get("excitationScore")
                db_record.residual_test_passed = result.get("residualTestPassed")
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
) -> dict[str, Any]:
    """异步历史数据模型辨识任务.

    Args:
        loop_id: 回路 ID
        start_time: 起始时间（ISO 8601）
        end_time: 结束时间（ISO 8601）
        candidate_model_types: 候选模型类型（默认 FOPDT+SOPDT）
        theta_estimate: 纯滞后预估（秒）
        created_by: 创建人
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
) -> dict[str, Any]:
    """执行整定 + 仿真的 async 逻辑."""
    from app.core.db import AsyncSessionLocal
    from app.models.tuning import TuningRecord
    from app.services.tuning import _simulate_multi_pid, tune_pid
    from app.services.tuning_progress import init_progress, update_progress

    await init_progress(task_id, task_type="tune_and_simulate", loop_id=loop_id)

    async with AsyncSessionLocal() as db:
        record = TuningRecord(
            id=str(uuid4()),
            loop_id=loop_id,
            model_type=model_type,
            model_params=model_params,
            algorithm=algorithms[0] if algorithms else "IMC",
            status="RUNNING",
            created_by=created_by,
            task_id=task_id,
            data_source="HISTORY",
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
            for algo in algorithms:
                tune_result = await tune_pid(
                    model_type=model_type,
                    model_params=model_params,
                    algorithm=algo,
                    algorithm_params=None,
                    current_pid=current_pid,
                    loop_id=loop_id,
                )
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


__all__ = ["identify_model_task", "tune_and_simulate_task"]
