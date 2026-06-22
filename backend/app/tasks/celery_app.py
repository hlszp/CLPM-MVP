"""Celery application instance configured for Redis broker/backend.

Time zone is Asia/Shanghai and tasks are JSON-serialised. Concrete task modules
are added in later tasks.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "clpm",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
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
)

# Auto-discover task modules under app.tasks (excluding the celery_app itself).
celery_app.autodiscover_tasks(["app.tasks"])
