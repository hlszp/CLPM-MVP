"""诊断批量任务（MVP v2）。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §4.2
仅手动触发（POST /diagnosis/run → TaskTracker 建单 → 本任务逐回路执行）；
细粒度进度：回路内 取数(0.1)→门禁(0.2)→算子(0.2~0.9)→落库(1.0)，
多回路按回路均分段上报。

P3-02（自动任务 ID、幂等与恢复）：
- **checkpoint 幂等**：重试/重投递重跑批次时，同 (task_id, loop_id) 已有
  SUCCESS/PARTIAL run 的回路跳过（不重复已完成 run 与 SYSTEM 建议）；
  上一尝试遗留的 FAILED/RUNNING 行先脱链（task_id=NULL，证据保留）再重做。
  唯一约束 ``uq_diagnosis_run_task_loop`` 兜底并发竞态。
- **协作取消**：回间 + 每次进度上报点检查任务状态（08§7.4 取数后/算子间/
  提交前）；取消后未开始回路不再执行、不发布结果；在途回路写取消审计行
  （status=FAILED + assessment_meta.cancelled，judgementStatus=NOT_ASSESSED
  ——08§4.2 行 8，取消优先不计运行失败），不生成 SYSTEM 建议。
- **终结统计**：收尾按 08§4.2 互斥口径（取消→运行失败→不可判定→可判定）
  统计对象终结并落盘任务 ``terminal_stats``（六类守恒 + PARTIAL/算子覆盖
  独立另报，见 services/diagnosis_batch_accounting.py）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update

from app.core.db import AsyncSessionLocal
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger
from app.schemas.task import TaskStatus
from app.services.diagnosis_assessment import build_assessment_meta
from app.services.diagnosis_batch_accounting import summarize_task
from app.services.diagnosis_orchestrator import run_diagnosis_for_loop
from app.services.diagnosis_system_actions import generate_system_actions_best_effort
from app.services.task_tracker import (
    get_task,
    get_task_status,
    set_terminal_stats,
    update_status,
)
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)


class DiagnosisTaskCancelledError(Exception):
    """批任务协作取消信号（回间/进度上报点检查到 CANCELLED 抛出）。"""


@celery_app.task(
    name="app.tasks.diagnosis_v2.run_diagnosis_batch",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_diagnosis_batch(
    self: AsyncTask,
    loop_ids: list[str],
    start: str,
    end: str,
    task_id: str,
    operator_group: str = "full",
    triggered_by: str = "user",
    operators: list[str] | None = None,
    trigger_type: str = "MANUAL",
) -> dict:
    """批量诊断入口（同步壳，异步执行）。

    operators=单算子细选白名单；trigger_type=MANUAL/SCHEDULED/EVENT（§12）。
    """
    return self.run_async(
        _do_run_batch(
            loop_ids, start, end, task_id, operator_group, triggered_by, operators, trigger_type
        )
    )


async def _task_cancelled(task_id: str) -> bool:
    """协作取消检查（轻量 hget；任务不存在视为未取消继续收尾）。"""
    status = await get_task_status(task_id)
    return status == TaskStatus.CANCELLED.value


async def _find_batch_run(task_id: str, loop_id: str) -> DiagnosisRun | None:
    """checkpoint 查询：该任务该回路名下最新 run 行。"""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(DiagnosisRun)
                .where(
                    DiagnosisRun.task_id == task_id,
                    DiagnosisRun.loop_id == loop_id,
                )
                .order_by(DiagnosisRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return row


async def _detach_stale_attempt(run_id: str) -> None:
    """上一尝试遗留行脱链（task_id=NULL，保留证据、让位唯一约束）。"""
    async with AsyncSessionLocal() as db:
        await db.execute(update(DiagnosisRun).where(DiagnosisRun.id == run_id).values(task_id=None))
        await db.commit()


def _cancelled_assessment_meta() -> dict:
    """取消审计行的科学结论元信息（D1/D2 预留参数位消费，08§4.2 行 8）。

    selected 为空 + cancelled=True → judgementStatus=NOT_ASSESSED（不发布
    正式结论）；coverage 全零等式自校验照常落库供读端对账。
    """
    return build_assessment_meta(
        op_results={},
        explicitly_excluded={},
        profile_expected_operators=[],
        primary_category=None,
        gate_passed=False,
        gate_reason="任务取消",
        selected_is_empty=True,
        cancelled=True,
    )


async def _record_cancelled_run(
    loop_id: str,
    start_dt: datetime,
    end_dt: datetime,
    task_id: str,
    triggered_by: str,
    trigger_type: str = "MANUAL",
) -> None:
    """取消审计行（08§4.2 行 8）：status=FAILED + reasonCode=CANCELLED。

    任务审计计取消（终结统计取消优先，不再计运行失败一次）；不生成
    SYSTEM 建议（取消后不发布后续结果）。
    """
    try:
        async with AsyncSessionLocal() as db:
            exists = (
                await db.execute(select(LoopLedger.id).where(LoopLedger.id == loop_id))
            ).scalar_one_or_none()
            if exists is None:
                return
            now = datetime.utcnow()
            run = DiagnosisRun(
                id=str(uuid4()),
                task_id=task_id,
                loop_id=loop_id,
                triggered_by=triggered_by,
                trigger_type=trigger_type,
                time_window_start=start_dt,
                time_window_end=end_dt,
                status="FAILED",
                data_gate={"passed": False, "reason": "任务取消", "reasonCode": "CANCELLED"},
                assessment_meta=_cancelled_assessment_meta(),
                algorithm_version="MVP_DIAG_V2_v1.0",
                started_at=now,
                finished_at=now,
                duration_ms=0,
            )
            db.add(run)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("取消审计行写入失败（忽略）: %s", exc)


async def _converge_running_placeholders(task_id: str) -> int:
    """取消收敛：RUNNING 占位行按 08§4.2 行 8 收敛为 FAILED+CANCELLED。

    现行编排为原子落库（结束才写行），不产 RUNNING 占位；本函数为防御
    收敛（C17-D02「取消已有 RUNNING 占位」语义就位，未来占位式写入方
    自动被覆盖）。
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(DiagnosisRun)
                .where(DiagnosisRun.task_id == task_id, DiagnosisRun.status == "RUNNING")
                .values(
                    status="FAILED",
                    finished_at=datetime.utcnow(),
                    assessment_meta=_cancelled_assessment_meta(),
                    data_gate={"passed": False, "reason": "任务取消", "reasonCode": "CANCELLED"},
                )
            )
            await db.commit()
            return int(result.rowcount or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("取消 RUNNING 占位收敛失败（忽略）: %s", exc)
        return 0


async def _do_run_batch(
    loop_ids: list[str],
    start: str,
    end: str,
    task_id: str,
    operator_group: str,
    triggered_by: str,
    operators: list[str] | None = None,
    trigger_type: str = "MANUAL",
) -> dict:
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    n = len(loop_ids)
    ok_count = 0
    resumed_done = 0
    cancelled_count = 0
    failed: list[dict[str, str]] = []

    await update_status(
        task_id,
        TaskStatus.RUNNING,
        progress=0.0,
        loops_total=n,
        loops_done=0,
        current_stage="开始诊断",
    )

    for i, loop_id in enumerate(loop_ids):
        base = i / n
        span = 1.0 / n

        # 取消检查（回间）：未开始回路不再执行、不发布结果
        if await _task_cancelled(task_id):
            cancelled_count = n - i
            logger.info("诊断任务已取消，剩余 %d 回路不再执行: task=%s", cancelled_count, task_id)
            break

        async def _progress(
            frac: float,
            stage: str,
            _base: float = base,
            _span: float = span,
            _i: int = i,
        ) -> None:
            # 取消检查（进度上报点=取数后/算子间/提交前，08§7.4）
            if await _task_cancelled(task_id):
                raise DiagnosisTaskCancelledError(stage)
            await update_status(
                task_id,
                TaskStatus.RUNNING,
                progress=round(_base + _span * frac, 4),
                current_stage=f"回路 {_i + 1}/{n}：{stage}",
            )

        # checkpoint：同任务同回路已有 run 行（重试/重投递幂等）
        existing = await _find_batch_run(task_id, loop_id)
        if existing is not None and existing.status in ("SUCCESS", "PARTIAL"):
            # 已完成：不重复 run/建议，仅推进进度
            resumed_done += 1
            await update_status(
                task_id,
                TaskStatus.RUNNING,
                progress=round((i + 1) / n, 4),
                loops_done=i + 1,
                current_stage=f"回路 {i + 1}/{n} 已完成（幂等跳过）",
            )
            continue
        if existing is not None:
            # 上一尝试的 FAILED/RUNNING 遗留行：脱链后重做（证据保留）
            await _detach_stale_attempt(existing.id)

        try:
            async with AsyncSessionLocal() as db:
                run = await run_diagnosis_for_loop(
                    db,
                    loop_id,
                    start=start_dt,
                    end=end_dt,
                    task_id=task_id,
                    triggered_by=triggered_by,
                    operator_group=operator_group,
                    operators=operators,
                    trigger_type=trigger_type,
                    progress_cb=_progress,
                )
                if run is None:
                    failed.append({"loopId": loop_id, "error": "回路不存在或缺少 PV Tag"})
                else:
                    ok_count += 1
        except DiagnosisTaskCancelledError:
            # 在途回路取消：写审计行（不计失败、不发布建议），余下回路全部取消
            await _record_cancelled_run(
                loop_id, start_dt, end_dt, task_id, triggered_by, trigger_type
            )
            cancelled_count = n - i
            logger.info(
                "诊断任务取消于回路 %d/%d（%s），剩余回路不再执行: task=%s",
                i + 1,
                n,
                loop_id,
                task_id,
            )
            break
        except Exception as exc:  # noqa: BLE001
            logger.exception("诊断回路 %s 执行失败: %s", loop_id, exc)
            failed.append({"loopId": loop_id, "error": str(exc)})
            await _record_failed_run(
                loop_id, start_dt, end_dt, task_id, triggered_by, str(exc), trigger_type
            )

        await update_status(
            task_id,
            TaskStatus.RUNNING,
            progress=round((i + 1) / n, 4),
            loops_done=i + 1,
            current_stage=f"回路 {i + 1}/{n} 完成",
        )

    task_cancelled = await _task_cancelled(task_id)
    if task_cancelled:
        # 取消已由端点 CAS 抢占终态：不覆盖（也不伪报 SUCCESS/FAILED）；
        # 收敛 RUNNING 占位（防御）后返回审计结果。
        await _converge_running_placeholders(task_id)
    elif ok_count == 0 and resumed_done == 0:
        await update_status(
            task_id,
            TaskStatus.FAILED,
            progress=1.0,
            error_message=f"全部 {n} 个回路诊断失败：{failed[:3]}",
            current_stage="诊断失败",
        )
    else:
        done = ok_count + resumed_done
        summary = f"完成 {done}/{n}" + (f"，失败 {len(failed)}" if failed else "")
        if resumed_done:
            summary += f"（含幂等跳过 {resumed_done}）"
        await update_status(task_id, TaskStatus.SUCCESS, progress=1.0, current_stage=summary)

    # ---- 终结统计（终态写定后计算，无 run 行对象按任务终态兜底归类；
    # 08§4.2 互斥口径，含取消/幂等跳过/失败混合批次对账）----
    stats = await _finalize_accounting(task_id, loop_ids)

    result: dict = {"ok": ok_count, "failed": failed}
    if resumed_done:
        result["resumedDone"] = resumed_done
    if cancelled_count:
        result["cancelled"] = cancelled_count
    if stats is not None:
        result["terminalStats"] = stats
    return result


async def _finalize_accounting(task_id: str, loop_ids: list[str]) -> dict | None:
    """计算并落盘批任务对象终结统计（best-effort，失败不阻塞收尾）。

    统计同时写入任务 hash ``terminal_stats`` 字段（读端/D6 与验收对账消费）。
    """
    try:
        task_record = await get_task(task_id)
        if not task_record:
            task_record = {
                "task_id": task_id,
                "status": TaskStatus.RUNNING.value,
                "loop_ids": json.dumps(loop_ids),
            }
        async with AsyncSessionLocal() as db:
            stats = await summarize_task(db, task_record=task_record)
        if stats is not None:
            await set_terminal_stats(task_id, stats)
        return stats
    except Exception as exc:  # noqa: BLE001
        logger.warning("诊断批任务终结统计失败（忽略）: task=%s %s", task_id, exc)
        return None


async def _record_failed_run(
    loop_id: str,
    start_dt: datetime,
    end_dt: datetime,
    task_id: str,
    triggered_by: str,
    error: str,
    trigger_type: str = "MANUAL",
) -> None:
    """失败回路留痕：status=FAILED 的 run 行（回路存在时）。"""
    try:
        async with AsyncSessionLocal() as db:
            exists = (
                await db.execute(select(LoopLedger.id).where(LoopLedger.id == loop_id))
            ).scalar_one_or_none()
            if exists is None:
                return
            now = datetime.utcnow()
            run = DiagnosisRun(
                id=str(uuid4()),
                task_id=task_id,
                loop_id=loop_id,
                triggered_by=triggered_by,
                trigger_type=trigger_type,
                time_window_start=start_dt,
                time_window_end=end_dt,
                status="FAILED",
                data_gate={"passed": False, "reason": f"执行异常: {error[:500]}"},
                algorithm_version="MVP_DIAG_V2_v1.0",
                started_at=now,
                finished_at=now,
                duration_ms=0,
            )
            db.add(run)
            await db.commit()
            # A3：落库点统一即时生成 SYSTEM 建议（FAILED run 无分类时为空操作）
            await generate_system_actions_best_effort(db, run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("失败回路留痕写入失败（忽略）: %s", exc)
