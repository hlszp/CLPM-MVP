"""Celery tasks for AAS Tag synchronization (IDS v3.2 §2.2.6).

- ``sync_aas_tags`` — 定时同步任务（5 分钟触发一次）
- ``sync_aas_tags_task`` — 手动触发的同步任务（返回 task_id）

设计要点：
- 使用 asyncio.run 在 Celery 同步 worker 中执行 async 代码
- 失败重试 3 次，指数退避
- **严禁任何 Write 操作到 AAS**
"""

from __future__ import annotations

import asyncio
import logging

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


async def _do_sync() -> dict:
    """执行 AAS 同步的实际 async 逻辑。"""
    from app.core.db import AsyncSessionLocal
    from app.services.aas_sync import sync_tags_from_aas

    async with AsyncSessionLocal() as db:
        return await sync_tags_from_aas(db)


@celery_app.task(
    name="app.tasks.aas_sync.sync_aas_tags",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def sync_aas_tags(self: AsyncTask) -> dict:
    """定时 AAS Tag 同步任务（Celery Beat 每 5 分钟触发）。

    失败自动重试 3 次，指数退避。
    """
    logger.info("AAS Tag 同步任务开始, task_id=%s", self.request.id)
    try:
        result = self.run_async(_do_sync())
        logger.info("AAS Tag 同步任务完成: %s", result)
        return result
    except Exception:
        logger.exception("AAS Tag 同步任务失败")
        raise


@celery_app.task(
    name="app.tasks.aas_sync.trigger_sync",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def trigger_sync() -> dict:
    """手动触发的 AAS Tag 同步任务（POST /api/v1/aas/sync 调用）。"""
    logger.info("手动触发 AAS Tag 同步任务")
    task = AsyncTask()
    return task.run_async(_do_sync())


# ---------------------------------------------------------------------------
# Celery Beat 配置：每 5 分钟同步一次
# ---------------------------------------------------------------------------


celery_app.conf.beat_schedule = {
    "aas-tag-sync-every-5-minutes": {
        "task": "app.tasks.aas_sync.sync_aas_tags",
        "schedule": 300.0,  # 5 分钟
    },
}
celery_app.conf.timezone = "Asia/Shanghai"


__all__ = ["sync_aas_tags", "trigger_sync"]
