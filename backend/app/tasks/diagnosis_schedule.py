"""诊断分级定时调度（自动诊断层①，设计文档 §12.3）。

不做每小时全量诊断（1h 窗证据不足、滚动窗冗余），按回路重要性等级排程：
- 1 级（关键）：每日 01:10，近 24h 窗口 → 每日基线
- 2 级（重要）：每周日 02:10，近 7d 窗口 → 周期体检
- 3 级（一般）：不排程（仅事件触发/手动）

前置密度门禁：目标窗口 TDengine 行数 < 预期 50% 的回路跳过（缺数窗口
只会产出 DATA_INSUFFICIENT 噪音），跳过记录日志不发任务。
triggered_by='scheduler-grade{N}'，trigger_type='SCHEDULED'。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.tdengine import execute_sql
from app.models.loop import LoopLedger
from app.schemas.task import TaskType
from app.services.data_import import _batch_get_loop_data
from app.services.task_tracker import create_task
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

#: 密度门禁：窗口行数低于预期点数该比例时跳过
_DENSITY_THRESHOLD = 0.5


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _loops_by_importance(level: int) -> list[str]:
    """查指定重要性等级的 READY 活跃回路 ID。"""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(LoopLedger.id).where(
                LoopLedger.importance_level == level,
                LoopLedger.status == "READY",
                LoopLedger.is_active.is_(True),
            )
        )
        return [str(r) for r in rows.scalars().all()]


async def _density_ok(loop_meta: dict, start: datetime, end: datetime) -> bool:
    """密度门禁：窗口行数 ≥ 预期点数 × 阈值（1s 采样）。

    查询失败视为不通过（数据源不可用时不盲跑）。
    """
    subtable = loop_meta.get("subtable", "")
    if not subtable:
        return False
    expected = max((end - start).total_seconds(), 1.0)
    sql = (
        f"SELECT COUNT(*) FROM {settings.TDENGINE_DB}.{subtable} "
        f"WHERE ts >= '{start.isoformat()}Z' AND ts <= '{end.isoformat()}Z'"
    )
    try:
        rows = await execute_sql(sql)
        count = int(rows[0].get("count(*)", 0)) if rows else 0
        return count >= expected * _DENSITY_THRESHOLD
    except Exception:  # noqa: BLE001
        logger.warning("调度密度门禁查询失败（跳过该回路）: subtable=%s", subtable)
        return False


async def _run_scheduled(level: int, window: timedelta) -> dict:
    """按等级发起定时诊断（密度门禁过滤后批量建任务）。"""
    loop_ids = await _loops_by_importance(level)
    if not loop_ids:
        logger.info("分级定时诊断：等级 %d 无目标回路，跳过", level)
        return {"level": level, "total": 0, "dispatched": 0, "skipped": 0}

    end = _utcnow_naive()
    start = end - window

    async with AsyncSessionLocal() as db:
        loop_meta = await _batch_get_loop_data(db, loop_ids)

    eligible: list[str] = []
    skipped: list[str] = []
    for lid in loop_ids:
        meta = loop_meta.get(lid, {})
        if not meta.get("role_tag_map"):
            skipped.append(lid)
            continue
        if await _density_ok(meta, start, end):
            eligible.append(lid)
        else:
            skipped.append(lid)
            logger.info(
                "分级定时诊断：回路 %s 密度不足（窗口 %s~%s），跳过",
                lid,
                start.isoformat(),
                end.isoformat(),
            )

    if not eligible:
        logger.info(
            "分级定时诊断：等级 %d 全部跳过（共 %d 个：密度不足/无映射）",
            level,
            len(skipped),
        )
        return {"level": level, "total": len(loop_ids), "dispatched": 0, "skipped": len(skipped)}

    # TaskTracker 建单（单任务聚合本批回路，进度按回路分段）
    task_id = str(uuid4())
    await create_task(
        task_type=TaskType.DIAGNOSIS,
        created_by=f"scheduler-grade{level}",
        created_by_id="00000000-0000-0000-0000-000000000001",
        loop_ids=eligible,
        triggered_by="schedule",
        title=f"分级定时诊断（{level} 级，{len(eligible)} 个回路）",
    )

    from app.tasks.diagnosis_v2 import run_diagnosis_batch

    celery_result = run_diagnosis_batch.delay(
        loop_ids=eligible,
        start=start.isoformat(),
        end=end.isoformat(),
        task_id=task_id,
        operator_group="full",
        triggered_by=f"scheduler-grade{level}",
        trigger_type="SCHEDULED",
    )
    from app.services.task_tracker import set_celery_task_ids

    await set_celery_task_ids(task_id, [celery_result.id])
    logger.info(
        "分级定时诊断：等级 %d 发起 %d 个回路（跳过 %d），taskId=%s",
        level,
        len(eligible),
        len(skipped),
        task_id,
    )
    return {
        "level": level,
        "total": len(loop_ids),
        "dispatched": len(eligible),
        "skipped": len(skipped),
        "taskId": task_id,
    }


@celery_app.task(name="app.tasks.diagnosis_schedule.run_daily", base=AsyncTask)
def run_daily(self: AsyncTask) -> dict:
    """每日 01:10：1 级关键回路，近 24h 窗口。"""
    return self.run_async(_run_scheduled(1, timedelta(hours=24)))


@celery_app.task(name="app.tasks.diagnosis_schedule.run_weekly", base=AsyncTask)
def run_weekly(self: AsyncTask) -> dict:
    """每周日 02:10：2 级重要回路，近 7d 窗口。"""
    return self.run_async(_run_scheduled(2, timedelta(days=7)))


# ---------------------------------------------------------------------------
# Beat 调度配置（追加方式注册，避免覆盖其他模块）
# ---------------------------------------------------------------------------
from celery.schedules import crontab  # noqa: E402

_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["diagnosis-scheduled-daily"] = {
    "task": "app.tasks.diagnosis_schedule.run_daily",
    "schedule": crontab(hour=1, minute=10),
}
_existing_beat["diagnosis-scheduled-weekly"] = {
    "task": "app.tasks.diagnosis_schedule.run_weekly",
    "schedule": crontab(day_of_week=0, hour=2, minute=10),
}
celery_app.conf.beat_schedule = _existing_beat
