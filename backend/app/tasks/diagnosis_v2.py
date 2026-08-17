"""诊断批量任务（MVP v2）。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §4.2
仅手动触发（POST /diagnosis/run → TaskTracker 建单 → 本任务逐回路执行）；
细粒度进度：回路内 取数(0.1)→门禁(0.2)→算子(0.2~0.9)→落库(1.0)，
多回路按回路均分段上报。
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger
from app.schemas.task import TaskStatus
from app.services.diagnosis_orchestrator import run_diagnosis_for_loop
from app.services.task_tracker import update_status
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)


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

        async def _progress(
            frac: float, stage: str, _base: float = base, _span: float = span, _i: int = i
        ) -> None:
            await update_status(
                task_id,
                TaskStatus.RUNNING,
                progress=round(_base + _span * frac, 4),
                current_stage=f"回路 {_i + 1}/{n}：{stage}",
            )

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

    if ok_count == 0:
        await update_status(
            task_id,
            TaskStatus.FAILED,
            progress=1.0,
            error_message=f"全部 {n} 个回路诊断失败：{failed[:3]}",
            current_stage="诊断失败",
        )
    else:
        summary = f"完成 {ok_count}/{n}" + (f"，失败 {len(failed)}" if failed else "")
        await update_status(task_id, TaskStatus.SUCCESS, progress=1.0, current_stage=summary)
    return {"ok": ok_count, "failed": failed}


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
            db.add(
                DiagnosisRun(
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
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("失败回路留痕写入失败（忽略）: %s", exc)
