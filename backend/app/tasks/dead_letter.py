"""Dead letter queue handler (S2-A6).

记录永久失败的任务元数据，供独立 worker 消费排查：

    celery -A app.tasks.celery_app worker -Q dead_letter --loglevel=info
"""

from __future__ import annotations

import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.dead_letter.record")
def record(
    task_id: str,
    task_name: str,
    exc: str,
    args: tuple | None = None,
    kwargs: dict | None = None,
) -> dict:
    """记录永久失败的任务（来自 AsyncTask.on_failure）。"""
    logger.error(
        "[DEAD_LETTER] task_id=%s task_name=%s exc=%s args=%s kwargs=%s",
        task_id,
        task_name,
        exc,
        args,
        kwargs,
    )
    return {
        "taskId": task_id,
        "taskName": task_name,
        "exc": exc,
        "status": "DEAD_LETTER",
    }
