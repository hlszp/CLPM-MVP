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
from app.services.ten_minute_submission import REQUEST_LIFETIME, TASK_NAME
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
        if not await control.fenced_step(redis_client, raw, lock.local.token, renew=True):
            raise LockError("影子 cohort 或执行租约已失效")


async def _prepare_report(state, report_id):
    cfg = await control.read_config()
    loop_id = state["loopIds"][state["cursor"]]
    result = (
        await _work(state)
        if cfg["enabled"] and loop_id in cfg["loopIds"]
        else {"status": "DISABLED"}
    )
    cfg = await control.read_config()
    if not cfg["enabled"] or loop_id not in cfg["loopIds"]:
        result = {"status": "DISABLED"}
    report = {
        **result,
        "id": report_id,
        "cohortId": state["id"],
        "cursor": state["cursor"],
        "loopId": loop_id,
        "kind": state["kind"],
        "sourceTaskId": state["sourceTaskId"],
        "start": state["start"],
        "end": state["end"],
        "finishedAt": datetime.now(UTC).isoformat(),
    }
    # 文件暂存不是发布；只有 Redis 同时确认 token 与 cohort 后才能写终态。
    control.save_report(
        report_id, {**report, "status": "PENDING", "intendedStatus": result["status"]}
    )
    next_state = {
        **state,
        "cursor": state["cursor"] + 1,
        "reports": [
            *state["reports"],
            {"id": report_id, "status": result["status"], "loopId": loop_id},
        ],
    }
    final = next_state["cursor"] >= len(state["loopIds"]) or result["status"] == "DISABLED"
    next_state["status"] = (
        "STOPPED" if result["status"] == "DISABLED" else "FINISHED" if final else "QUEUED"
    )
    return report, next_state, final


async def _stop_tasks(*tasks):
    for task in tasks:
        if task is not None:
            task.cancel()
    await asyncio.gather(*(t for t in tasks if t is not None), return_exceptions=True)


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
    token = lock.local.token
    work = renewal = None
    committed = released = False
    next_raw = None
    report_id = str(uuid4())
    try:
        if await redis_client.get(control.ACTIVE_KEY) != raw:
            return {"status": "DUPLICATE_OR_STALE"}
        renewal = asyncio.create_task(_renew(lock, raw))
        work = asyncio.create_task(asyncio.wait_for(_prepare_report(state, report_id), timeout=180))
        done, _ = await asyncio.wait((work, renewal), return_when=asyncio.FIRST_COMPLETED)
        if renewal in done:
            await renewal
        report, next_state, final = await work
        await _stop_tasks(renewal)
        if not await control.fenced_step(redis_client, raw, token, None if final else next_state):
            raise LockError("影子执行租约或 cohort 丢失，拒绝发布一致结论")
        committed = released = True
        next_raw = None if final else control.canonical_json(next_state)
        control.save_report(report_id, report)
        control.save_report(owner, next_state)
        if not final:
            # fenced_step 已原子释放执行租约，后继不需要等待 BUSY 重试。
            await asyncio.to_thread(control.dispatch, owner, next_state["cursor"])
        logger.info("10分钟影子步骤 cohort=%s cursor=%s status=%s", owner, cursor, report["status"])
        return report
    except (Exception, asyncio.CancelledError) as exc:
        await _stop_tasks(work, renewal)
        # 先清理自己仍持有的状态，再尝试写错误报告；磁盘失败不能阻止释放。
        cleaned = False
        try:
            if not committed:
                cleaned = await control.fenced_step(redis_client, raw, token)
                released = cleaned
            elif next_raw is not None:
                cleaned = await control.abort_pending(redis_client, next_raw)
        except RedisError:
            logger.exception("影子失败状态回收失败，等待租约过期")
        try:
            if not committed:
                control.save_report(
                    report_id,
                    {
                        "id": report_id,
                        "cohortId": owner,
                        "cursor": cursor,
                        "status": "ERROR" if cleaned else "REJECTED",
                        "error": str(exc),
                    },
                )
            if cleaned or (committed and next_raw is None):
                control.save_report(owner, {**state, "status": "ERROR", "error": str(exc)})
        except Exception:
            logger.exception("影子错误报告写入失败")
        logger.exception("10分钟影子步骤失败 cohort=%s cursor=%s", owner, cursor)
        raise
    finally:
        await _stop_tasks(work, renewal)
        if not released:
            try:
                await lock.release()
            except (LockError, RedisError):
                logger.info("影子租约已丢失，不释放后继租约")


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


async def _request(*, loop_ids, start, end, kind, source_task_id, requested_at):
    created = datetime.fromisoformat(requested_at).astimezone(UTC)
    age = datetime.now(UTC) - created
    if not timedelta(0) <= age < REQUEST_LIFETIME:
        return {"status": "EXPIRED"}
    return await control.enqueue(
        redis_client,
        loop_ids=loop_ids,
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        kind=kind,
        source_task_id=source_task_id,
    )


@celery_app.task(bind=True, base=AsyncTask, name=TASK_NAME, soft_time_limit=60, time_limit=90)
def shadow_request(self, **kwargs):
    return self.run_async(_request(**kwargs))


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
