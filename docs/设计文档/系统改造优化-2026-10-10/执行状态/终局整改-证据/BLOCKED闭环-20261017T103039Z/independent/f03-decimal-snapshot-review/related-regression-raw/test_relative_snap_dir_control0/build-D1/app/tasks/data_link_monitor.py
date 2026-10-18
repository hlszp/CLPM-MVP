"""数据链路监控 + 导入任务生命周期 Celery 任务（WS-B2）.

Beat 调度：
- ``data-link-check``：每 ``DATA_LINK_CHECK_INTERVAL_MINUTES`` 分钟检查 TDengine
  数据新鲜度 + AAS 连接状态，异常时经 alerting 发送告警（原 data_link_monitor
  模块为死代码，本模块接 Celery beat 激活）。
- ``import-task-sweep``：每 15 分钟清扫超时 RUNNING 导入任务 + 修剪过期索引
  （worker 被杀导致任务永久卡"执行中"的兜底）。
"""

from __future__ import annotations

import logging

from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.data_link_monitor.run_data_link_check",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def run_data_link_check(self: AsyncTask) -> dict:
    """执行数据采集链路健康检查（TDengine 新鲜度 + AAS 连接 + 告警）."""
    from app.services.data_link_monitor import run_data_link_check as _do_check

    logger.info("数据链路健康检查任务开始")
    return self.run_async(_do_check())


@celery_app.task(
    name="app.tasks.data_link_monitor.sweep_import_tasks",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 1, "countdown": 120},
)
def sweep_import_tasks(self: AsyncTask) -> dict:
    """清扫超时 RUNNING 导入任务 + 修剪过期索引."""
    from app.services.data_import import prune_import_task_index, sweep_stale_running_tasks

    logger.info("导入任务生命周期清扫任务开始")

    async def _do_sweep() -> dict:
        swept = await sweep_stale_running_tasks()
        pruned = await prune_import_task_index()
        return {"swept": swept["swept"], "pruned": pruned}

    return self.run_async(_do_sweep())


# ---------------------------------------------------------------------------
# Beat 调度配置
# ---------------------------------------------------------------------------

from celery.schedules import crontab  # noqa: E402

from app.core.config import settings  # noqa: E402

# 追加方式注册 Beat 任务（避免覆盖其他模块的 beat_schedule）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
# 数据链路检查：每 DATA_LINK_CHECK_INTERVAL_MINUTES 分钟执行
_interval = int(settings.DATA_LINK_CHECK_INTERVAL_MINUTES)
if 0 < _interval < 60:
    _link_schedule: crontab | float = crontab(minute=f"*/{_interval}")
elif _interval >= 60:
    _link_schedule = crontab(minute=0)
else:
    _link_schedule = crontab(minute="*/10")
_existing_beat["data-link-check"] = {
    "task": "app.tasks.data_link_monitor.run_data_link_check",
    "schedule": _link_schedule,
}
# 导入任务清扫：每 15 分钟执行
_existing_beat["import-task-sweep"] = {
    "task": "app.tasks.data_link_monitor.sweep_import_tasks",
    "schedule": crontab(minute="*/15"),
}
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


__all__ = ["run_data_link_check", "sweep_import_tasks"]
