"""诊断分级定时调度（自动诊断层①，设计文档 §12.3；2026-10-01 裁决更新）。

- 每日 01:10（run_daily）：**全量** READY 活跃回路，近 24h 窗口 → 每日基线
  （2026-10-01 用户裁决"每天全回路诊断一次"；原 1 级过滤在生产
  importance_level 全=2 下实际空跑）
- 每周日 02:10（run_weekly）：2 级重要回路，近 7d 窗口 → 周期体检（保留长窗视角）

前置门禁（缺数/不可信窗口只产出噪音，跳过记录日志不发任务）：
1. 密度门禁：目标窗口 TDengine 行数 < 预期 50% 的回路跳过；
2. fitness 门禁（2026-10-01 裁决 3，同日更新：仅拦 L0）：最新适用性
   L0（数据严重不足，跑诊断必产出噪音）的回路跳过；L1 手动主导随
   裁决放开诊断（仪表/质量码算子不受自控模式限制）；快照缺失不拦。

分批派发：eligible 按 50 回路/批切独立任务（全局 task_time_limit=1800s
硬杀 + autoretry 放大风险，单任务串行回路数必须受限；分批独立
TaskTracker，进度与失败互不污染）。
triggered_by='schedule'，trigger_type='SCHEDULED'。
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

#: 定时派发分批大小（全局 task_time_limit=1800s + autoretry 放大，单批回路数受限）
_DISPATCH_BATCH_SIZE = 50


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _loops_by_importance(level: int | None) -> list[str]:
    """查 READY 活跃回路 ID；level=None 时全量（2026-10-01 裁决：daily 全回路）。"""
    conditions = [LoopLedger.status == "READY", LoopLedger.is_active.is_(True)]
    if level is not None:
        conditions.append(LoopLedger.importance_level == level)
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(LoopLedger.id).where(*conditions))
        return [str(r) for r in rows.scalars().all()]


async def _fitness_blocked_ids(loop_ids: list[str]) -> set[str]:
    """fitness 门禁（裁决 3）：返回 L0/L1 回路集合；快照缺失/查询失败不拦。"""
    if not loop_ids:
        return set()
    try:
        from app.services.loop_fitness import get_latest_fitness_per_loop

        async with AsyncSessionLocal() as db:
            latest = await get_latest_fitness_per_loop(db, loop_ids)
        return {lid for lid, f in latest.items() if f.level == "L0"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("调度 fitness 门禁查询失败（本轮不拦 L0/L1）: %s", exc)
        return set()


async def _density_ok(loop_id: str, loop_meta: dict, start: datetime, end: datetime) -> bool:
    """密度门禁：legacy=窗口行数 ≥ 预期 × 阈值；point=逻辑时间覆盖 ≥ 阈值。

    point 布局下行数是 COV 物理事件数（常值回路稀疏不判缺失，V02/T03），
    改按该回路当前绑定点的 confirmed 覆盖段计算逻辑时间覆盖率。
    查询失败视为不通过（数据源不可用时不盲跑）。
    """
    layout = "legacy"
    if loop_id:
        try:
            from app.services.data_source import point_history_metadata as _phm

            async with AsyncSessionLocal() as _db:
                layout = await _phm.resolve_layout(_db, loop_id=loop_id, at=end)
        except Exception:  # noqa: BLE001 — 布局读取失败按 legacy 口径
            layout = "legacy"
    if layout == "point":
        return await _logical_coverage_ok(loop_id, start, end)
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


async def _logical_coverage_ok(loop_id: str, start: datetime, end: datetime) -> bool:
    """point 布局密度门禁：回路当前绑定点的 confirmed 覆盖时长占比 ≥ 阈值."""
    try:
        from app.services.data_source.logical_coverage import loop_window_coverage_ratio

        ratio = await loop_window_coverage_ratio(loop_id, start, end)
        return ratio >= _DENSITY_THRESHOLD
    except Exception:  # noqa: BLE001
        logger.warning("调度密度门禁（point 覆盖）查询失败（跳过该回路）: loop=%s", loop_id)
        return False


async def _run_scheduled(level: int, window: timedelta) -> dict:
    """发起定时诊断（level=None 全量；密度+fitness 双门禁过滤，分批建任务）。"""
    loop_ids = await _loops_by_importance(level)
    scope = "全量" if level is None else f"{level} 级"
    if not loop_ids:
        logger.info("分级定时诊断：%s 无目标回路，跳过", scope)
        return {"level": level, "total": 0, "dispatched": 0, "skipped": 0}

    end = _utcnow_naive()
    start = end - window

    async with AsyncSessionLocal() as db:
        loop_meta = await _batch_get_loop_data(db, loop_ids)

    fitness_blocked = await _fitness_blocked_ids(loop_ids)

    eligible: list[str] = []
    skipped: list[str] = []
    for lid in loop_ids:
        meta = loop_meta.get(lid, {})
        if not meta.get("role_tag_map"):
            skipped.append(lid)
            continue
        if lid in fitness_blocked:
            skipped.append(lid)
            continue
        if await _density_ok(lid, meta, start, end):
            eligible.append(lid)
        else:
            skipped.append(lid)
            logger.info(
                "分级定时诊断：回路 %s 密度不足（窗口 %s~%s），跳过",
                lid,
                start.isoformat(),
                end.isoformat(),
            )

    logger.info(
        "分级定时诊断：%s 目标 %d，fitness 门禁拦 %d，密度/映射拦 %d，可跑 %d",
        scope,
        len(loop_ids),
        len(fitness_blocked),
        len(skipped) - len(fitness_blocked),
        len(eligible),
    )
    if not eligible:
        return {"level": level, "total": len(loop_ids), "dispatched": 0, "skipped": len(skipped)}

    from app.tasks.diagnosis_v2 import run_diagnosis_batch

    # 分批派发：每批独立 TaskTracker（全局 30min 硬杀 + autoretry 3 次，
    # 单任务串行回路数必须受限；批间进度/失败互不污染）
    batches = [
        eligible[i : i + _DISPATCH_BATCH_SIZE]
        for i in range(0, len(eligible), _DISPATCH_BATCH_SIZE)
    ]
    task_ids: list[str] = []
    for bi, batch in enumerate(batches, start=1):
        task_id = str(uuid4())
        await create_task(
            task_type=TaskType.DIAGNOSIS,
            created_by=f"scheduler-grade{level}" if level is not None else "scheduler-daily-all",
            created_by_id="00000000-0000-0000-0000-000000000001",
            loop_ids=batch,
            triggered_by="schedule",
            title=f"定时诊断（{scope}，第 {bi}/{len(batches)} 批，{len(batch)} 个回路）",
        )
        celery_result = run_diagnosis_batch.delay(
            loop_ids=batch,
            start=start.isoformat(),
            end=end.isoformat(),
            task_id=task_id,
            operator_group="full",
            triggered_by="schedule",
            trigger_type="SCHEDULED",
        )
        from app.services.task_tracker import set_celery_task_ids

        await set_celery_task_ids(task_id, [celery_result.id])
        task_ids.append(task_id)
    logger.info(
        "分级定时诊断：%s 发起 %d 个回路（%d 批，跳过 %d），taskIds=%s",
        scope,
        len(eligible),
        len(batches),
        len(skipped),
        task_ids,
    )
    return {
        "level": level,
        "total": len(loop_ids),
        "dispatched": len(eligible),
        "skipped": len(skipped),
        "taskIds": task_ids,
    }


@celery_app.task(name="app.tasks.diagnosis_schedule.run_daily", bind=True, base=AsyncTask)
def run_daily(self: AsyncTask) -> dict:
    """每日 01:10：全量 READY 活跃回路，近 24h 窗口（2026-10-01 裁决）。"""
    return self.run_async(_run_scheduled(None, timedelta(hours=24)))


@celery_app.task(name="app.tasks.diagnosis_schedule.run_weekly", bind=True, base=AsyncTask)
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
