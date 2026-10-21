"""10 分钟影子准备/验证，复用现有 Worker/Beat，默认关闭。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from celery.schedules import crontab
from redis.exceptions import LockError, RedisError

from app.core.redis import redis_client
from app.services import ten_minute_control as control
from app.services.ten_minute_blocks import BLOCK, TenMinuteBlocks, floor_block
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)


async def prepare_loop(loop_id, start, end):
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopTagMapping
    from app.models.point_history import LoopTagBindingHistory

    async with AsyncSessionLocal() as db:
        current = (
            (
                await db.execute(
                    select(LoopTagMapping.tag_id).where(LoopTagMapping.loop_id == loop_id)
                )
            )
            .scalars()
            .all()
        )
        history = (
            (
                await db.execute(
                    select(LoopTagBindingHistory.tag_id).where(
                        LoopTagBindingHistory.loop_id == loop_id,
                        LoopTagBindingHistory.valid_from <= end,
                        (LoopTagBindingHistory.valid_to.is_(None))
                        | (LoopTagBindingHistory.valid_to > start),
                    )
                )
            )
            .scalars()
            .all()
        )
    points = sorted({str(p) for p in current + history if p})
    if len(points) > 14:
        raise ValueError("单块改绑点超过 14 点预算，未截断")
    blocks = TenMinuteBlocks(redis_client)
    for point in points:
        await blocks.prepare(point, start)
    return {"status": "PREPARED" if points else "INSUFFICIENT_DATA", "cache": dict(blocks.stats)}


async def _work(state):
    start, end = (datetime.fromisoformat(state[k]) for k in ("start", "end"))
    loop_id = state["loopIds"][state["cursor"]]
    if state["kind"] == "PREPARE":
        return await prepare_loop(loop_id, start, end)
    from app.services.ten_minute_shadow import validate_loop

    return await validate_loop(redis_client, loop_id, start, end)


async def _renew(lock, raw):
    while True:
        await asyncio.sleep(15)
        if not await control.replace_state(redis_client, raw, json.loads(raw)):
            raise LockError("影子 cohort 已失效")
        await lock.extend(60, replace_ttl=True)


async def run_step(owner, cursor):
    raw = await redis_client.get(control.ACTIVE_KEY)
    if not raw:
        return {"status": "STALE"}
    state = json.loads(raw)
    if state["id"] != owner or state["cursor"] != cursor:
        return {"status": "DUPLICATE_OR_STALE"}
    lock = redis_client.lock(control.LEASE_KEY, timeout=60, blocking=False, thread_local=False)
    if not await lock.acquire():
        return {"status": "BUSY"}
    work = renewal = None
    next_raw = None
    report_id = str(uuid4())
    try:
        # 获取执行锁后再次验证 CAS 身份，拒绝并发旧步骤。
        if await redis_client.get(control.ACTIVE_KEY) != raw:
            return {"status": "DUPLICATE_OR_STALE"}
        cfg = await control.read_config()
        loop_id = state["loopIds"][cursor]
        if not cfg["enabled"] or loop_id not in cfg["loopIds"]:
            result = {"status": "DISABLED"}
        else:
            work = asyncio.create_task(asyncio.wait_for(_work(state), timeout=180))
            renewal = asyncio.create_task(_renew(lock, raw))
            done, _ = await asyncio.wait((work, renewal), return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                await renewal
            result = await work
        if not await lock.owned():
            raise LockError("影子执行租约丢失，拒绝发布一致结论")
        cfg = await control.read_config()
        if not cfg["enabled"] or loop_id not in cfg["loopIds"]:
            result = {"status": "DISABLED"}
        report = {
            **result,
            "id": report_id,
            "cohortId": owner,
            "cursor": cursor,
            "loopId": loop_id,
            "kind": state["kind"],
            "sourceTaskId": state["sourceTaskId"],
            "start": state["start"],
            "end": state["end"],
            "finishedAt": datetime.now(UTC).isoformat(),
        }
        control.save_report(report_id, report)
        state["reports"].append({"id": report_id, "status": result["status"], "loopId": loop_id})
        state["cursor"] += 1
        final = state["cursor"] >= len(state["loopIds"]) or result["status"] == "DISABLED"
        state["status"] = (
            "STOPPED" if result["status"] == "DISABLED" else "FINISHED" if final else "QUEUED"
        )
        # 即使交接前崩溃，已有逐回路报告保留；cohort QUEUED 不伪装全部完成。
        if not await control.replace_state(redis_client, raw, None if final else state):
            raise LockError("影子 cohort 交接失败")
        next_raw = control.canonical_json(state) if not final else None
        control.save_report(owner, state)
        if not final:
            await asyncio.to_thread(control.dispatch, owner, state["cursor"])
        logger.info("10分钟影子步骤 cohort=%s cursor=%s status=%s", owner, cursor, result["status"])
        return report
    except (Exception, asyncio.CancelledError) as exc:
        control.save_report(
            report_id,
            {
                "id": report_id,
                "cohortId": owner,
                "cursor": cursor,
                "status": "ERROR",
                "error": str(exc),
            },
        )
        await control.replace_state(redis_client, raw, None)
        if next_raw is not None:
            await control.replace_state(redis_client, next_raw, None)
        control.save_report(owner, {**state, "status": "ERROR", "error": str(exc)})
        logger.exception("10分钟影子步骤失败 cohort=%s cursor=%s", owner, cursor)
        raise
    finally:
        for task in (work, renewal):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (work, renewal) if t is not None), return_exceptions=True)
        try:
            await lock.release()
        except (LockError, RedisError):
            logger.warning("影子锁释放失败，等待租约过期", exc_info=True)


@celery_app.task(
    bind=True,
    base=AsyncTask,
    name="app.tasks.ten_minute_shadow.step",
    soft_time_limit=240,
    time_limit=300,
    max_retries=5,
)
def shadow_step(self, owner, cursor):
    result = self.run_async(run_step(owner, cursor))
    if result["status"] == "BUSY":
        raise self.retry(countdown=30, priority=9)
    return result


async def _prepare_tick():
    cfg = await control.read_config()
    # 留出 5 分钟迟到容忍；迟到仍由下一次新鲜基线内容寻址失效。
    end = floor_block(datetime.now(UTC) - timedelta(minutes=5))
    return await control.enqueue(
        redis_client, loop_ids=cfg["loopIds"], start=end - BLOCK, end=end, kind="PREPARE"
    )


@celery_app.task(bind=True, base=AsyncTask, name="app.tasks.ten_minute_shadow.prepare")
def prepare_tick(self):
    return self.run_async(_prepare_tick())


_schedule = dict(celery_app.conf.beat_schedule or {})
_schedule["kpi-shadow-10min"] = {
    "task": "app.tasks.ten_minute_shadow.prepare",
    "schedule": crontab(minute="*/10"),
    "options": {"priority": 9, "expires": 120},
}
celery_app.conf.beat_schedule = _schedule
