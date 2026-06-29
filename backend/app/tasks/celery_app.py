"""Celery application instance configured for Redis broker/backend.

Time zone is Asia/Shanghai and tasks are JSON-serialised. Concrete task modules
are added in later tasks.

Sprint 2 加固：
- S2-A2: task_reject_on_worker_lost — Worker 崩溃时任务重投
- S2-A3: task_time_limit / task_soft_time_limit — 任务超时保护
- S2-A5: PersistentScheduler — Beat 调度持久化
- S2-A6: dead_letter 队列 — 失败任务进入死信
"""

from __future__ import annotations

import logging

from celery import Celery, Task
from kombu import Queue

from app.core.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "clpm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.kpi_calc",
        "app.tasks.aas_sync",
        "app.tasks.diagnosis_engine",
        "app.tasks.report_generator",
        "app.tasks.audit_archive",
        "app.tasks.dead_letter",
    ],
)

celery_app.conf.update(
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # S2-A2: Worker 崩溃时任务重投（避免任务丢失）
    task_reject_on_worker_lost=True,
    # S2-A3: 任务超时保护（硬超时 30 分钟，软超时 25 分钟）
    task_time_limit=1800,
    task_soft_time_limit=1500,
    # S2-A5: Beat 调度持久化（Redis 重启后 Beat 调度状态可恢复）
    beat_scheduler="celery.beat.PersistentScheduler",
    beat_schedule_filename="celerybeat-schedule",
    # S2-A6: 死信队列定义
    task_queues=(
        Queue("default", routing_key="default"),
        Queue("dead_letter", routing_key="dead_letter"),
    ),
    task_default_queue="default",
    task_default_routing_key="default",
)

# Task modules are explicitly listed in the include parameter above
# to ensure reliable registration when the worker starts.


class AsyncTask(Task):
    """Base task that runs an async function in a fresh event loop.

    S2-A6: on_failure 将耗尽重试的失败任务元数据发送到 dead_letter 队列，
    供独立 worker（celery worker -Q dead_letter）消费排查。
    """

    abstract = True

    def run_async(self, coro):
        """Run a coroutine in a fresh event loop."""
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """任务最终失败（重试耗尽）时发送到死信队列。"""
        logger.error(
            "任务最终失败（已耗尽重试）, task_id=%s, task_name=%s, exc=%s",
            task_id,
            self.name,
            exc,
        )
        try:
            celery_app.send_task(
                "app.tasks.dead_letter.record",
                args=[task_id, self.name, str(exc), args, kwargs],
                queue="dead_letter",
            )
        except Exception:
            logger.exception("发送死信队列失败")
        super().on_failure(exc, task_id, args, kwargs, einfo)
