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
        "app.tasks.data_link_monitor",
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
    # Redis broker 默认 visibility_timeout=3600s（1h），而 import_history_data
    # 的 time_limit=7200s（2h）。未配置时任务跑满 1h 即被 broker 自动重投给另一个
    # worker，造成并发执行。设为 9000s（2.5h，> time_limit 留 0.5h 缓冲）。
    broker_transport_options={"visibility_timeout": 9000},
    result_backend_transport_options={"visibility_timeout": 9000},
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


# v6.1 修复：显式导入任务模块，确保 Celery Beat 进程也能加载 beat_schedule。
# include 参数只对 worker 生效，Beat 进程不会自动导入这些模块，
# 导致 beat_schedule 中的定时调度计划（kpi-calc-hourly 等）不会被注册。
# 必须放在 AsyncTask 类定义之后，避免循环导入。
import app.tasks.aas_sync  # noqa: E402, F401
import app.tasks.audit_archive  # noqa: E402, F401
import app.tasks.data_link_monitor  # noqa: E402, F401
import app.tasks.diagnosis_engine  # noqa: E402, F401
import app.tasks.kpi_calc  # noqa: E402, F401
import app.tasks.report_generator  # noqa: E402, F401


def _preload_datasource_config_sync() -> None:
    """在新事件循环中同步执行 sys_config 预载（供 worker 信号处理器调用）。"""
    import asyncio

    from app.core.db import AsyncSessionLocal
    from app.services.datasource_config import preload_datasource_config
    from app.services.preprocessing.outlier_params import preload_outlier_params

    async def _preload() -> None:
        async with AsyncSessionLocal() as db:
            await preload_datasource_config(db)
            # 同一会话继续预载异常值检测参数/开关到进程内缓存，
            # 保证 worker 子进程的 Pipeline/诊断引擎读取到 sys_config 配置；
            # 失败独立兜底（回落算法默认），不影响数据源配置预载结果
            try:
                await preload_outlier_params(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker 子进程预载异常值检测参数失败（将使用算法默认值）: %s", exc)
            # 预载诊断触发条件（整改计划 C6，失败回落默认值）
            try:
                from app.services.diagnosis_trigger_config import preload_diagnosis_trigger

                await preload_diagnosis_trigger(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker 子进程预载诊断触发条件失败（将使用默认值）: %s", exc)
            # 预载诊断专家规则（整改计划 C2，失败回退到空列表，触发硬编码规则兜底）
            try:
                from app.services.diagnosis_rule import preload_rules

                await preload_rules(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker 子进程预载诊断专家规则失败（将回退到硬编码规则）: %s", exc)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_preload())
    finally:
        loop.close()


# worker_process_init 在每个 prefork 子进程初始化时触发（主进程不触发，
# 但任务只在子进程执行）。Celery worker 是独立进程，不经过 FastAPI lifespan，
# 若不预载，settings 中的业务 URL/Token 为空（.env 已移除），导入与远端取数
# 任务会报 "HISTORY_DATA_API_URL 未配置"。子进程每次重建都会重新预载，
# 因此 worker 生命周期内的配置变更最多在子进程回收后生效。
from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def _on_worker_process_init(**kwargs: object) -> None:
    try:
        _preload_datasource_config_sync()
        logger.info("worker 子进程已从 sys_config 预载数据源配置")
    except Exception as exc:  # noqa: BLE001
        # 预载失败不阻塞 worker 启动，兜底 .env 默认值（与 API lifespan 行为一致）
        logger.warning("worker 子进程预载数据源配置失败（将使用 .env 默认值）: %s", exc)
