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
from app.core.logging import _request_id_ctx, setup_logging

logger = logging.getLogger(__name__)

celery_app = Celery(
    "clpm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.kpi_calc",
        # MVP 精简：已屏蔽诊断模块 → 不注册 diagnosis_engine / tracker_verification
        # "app.tasks.diagnosis_engine",
        # "app.tasks.tracker_verification",
        # MVP v2 诊断模块（2026-08-16 重设计，仅手动触发；旧引擎保持屏蔽）
        "app.tasks.diagnosis_v2",
        "app.tasks.report_generator",
        "app.tasks.audit_archive",
        "app.tasks.dead_letter",
        "app.tasks.data_link_monitor",
        # 整定模块（09 设计方案恢复：历史辨识异步任务）
        "app.tasks.tuning",
        "app.tasks.alert_patrol",
        # 工作台 v2.0（预计算 / SLA 巡检 / 事件归档 / 缓存清理 / MV 刷新）
        "app.tasks.workbench",
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
    # 整改 G32：visibility_timeout 必须**大于任何任务的 time_limit**，否则未 ack
    # 的消息会被 broker 重投给另一个 worker，造成同一任务并发双跑。
    # 原值 9000s（2.5h）的依据是"import_history_data 的 time_limit=7200s（2h）"，
    # 但该任务现为 86400s（24h，见 kpi_calc.py 的 import_history_data 覆盖），
    # 注释依据早已过时——任何超过 2.5h 的导入都必然触发重投。
    # 取 90000s（25h）= 24h 最大任务 + 1h 缓冲，并由不变量测试守护。
    broker_transport_options={"visibility_timeout": 90000},
    # 注：result_backend_transport_options 的 visibility_timeout 对 Redis **结果后端**
    # 无实际作用（该选项只对 broker 语义生效），保留仅为配置显式化。
    result_backend_transport_options={"visibility_timeout": 90000},
    # 任务结果在 Redis 结果后端保留 7 天后过期，避免无限堆积
    # （与任务状态清扫周期配套，超时未清理的结果由 Redis 自动回收）
    result_expires=7 * 24 * 3600,
    # 每个 prefork 子进程处理 50 个任务后回收重建，抑制长驻 worker
    # 内存只增不减（worker 静默挂死温床），重建时 worker_process_init
    # 会重新预载 sys_config 配置
    worker_max_tasks_per_child=50,
)

# Task modules are explicitly listed in the include parameter above
# to ensure reliable registration when the worker starts.


class AsyncTask(Task):
    """Base task that runs an async function in a fresh event loop.

    S2-A6: on_failure 将耗尽重试的失败任务元数据发送到 dead_letter 队列，
    由 lifespan 自动启动的同一 worker（-Q default,dead_letter）消费排查。
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
# MVP 精简：已移除 AAS/诊断/整定 相关任务 → 不再 import，Beat 也不再注册相应调度
import app.tasks.alert_patrol  # noqa: E402, F401
import app.tasks.audit_archive  # noqa: E402, F401
import app.tasks.beat_registry  # noqa: E402, F401  模块热插拔 beat 条件化
import app.tasks.data_link_monitor  # noqa: E402, F401

# import app.tasks.diagnosis_engine  # noqa: E402, F401
import app.tasks.diagnosis_maintenance  # noqa: E402, F401
import app.tasks.diagnosis_schedule  # noqa: E402, F401
import app.tasks.diagnosis_v2  # noqa: E402, F401
import app.tasks.kpi_calc  # noqa: E402, F401
import app.tasks.report_generator  # noqa: E402, F401

# import app.tasks.tracker_verification  # noqa: E402, F401
import app.tasks.tuning  # noqa: E402, F401  # 整定模块（09 设计方案恢复）
import app.tasks.workbench  # noqa: E402, F401  # 工作台 v2.0（5 beat）


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
            # MVP 精简：已屏蔽诊断模块 → 跳过诊断触发条件预载
            # try:
            #     from app.services.diagnosis_trigger_config import preload_diagnosis_trigger
            #
            #     await preload_diagnosis_trigger(db)
            # except Exception as exc:  # noqa: BLE001
            #     logger.warning("worker 子进程预载诊断触发条件失败（将使用默认值）: %s", exc)
            # 预载诊断专家规则（整改计划 C2，失败回退到空列表，触发硬编码规则兜底）
            # MVP 精简：已屏蔽诊断模块 → 跳过诊断专家规则预载
            # try:
            #     from app.services.diagnosis_rule import preload_rules
            #
            #     await preload_rules(db)
            # except Exception as exc:  # noqa: BLE001
            #     logger.warning("worker 子进程预载诊断专家规则失败（将回退到硬编码规则）: %s", exc)
            # P0-B: 预载指标算法参数（失败回落算法默认值）
            try:
                from app.services.algorithm_config import preload_algorithm_params

                await preload_algorithm_params(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker 子进程预载指标算法参数失败（将使用算法默认值）: %s", exc)
            # 可信度统一 Phase 3（P3-2 / D4）：预载可信度阈值 + 启动 pub/sub 订阅
            try:
                from app.services.confidence_evaluator import (
                    load_thresholds_from_db,
                    start_threshold_subscriber,
                )

                await load_thresholds_from_db(db)
                start_threshold_subscriber()
            except Exception as exc:  # noqa: BLE001
                logger.warning("worker 子进程预载可信度阈值失败（将使用算法默认值）: %s", exc)

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
from celery.signals import (  # noqa: E402
    after_setup_logger,
    beat_init,
    before_task_publish,
    task_postrun,
    task_prerun,
    worker_process_init,
    worker_ready,
)

# 父进程看门狗（防孤儿）：宿主 uvicorn 被 SIGKILL/崩溃时三层退出钩子
# 均无法执行，Celery 独立进程组滞留；beat/worker 主进程监视宿主
# （CLPM_PARENT_PID），prefork 子进程监视直接父进程（worker master，
# 覆盖 master 单独崩溃时 pool 无人派活的瘫痪态），级联自退出。
from app.tasks.parent_watchdog import (  # noqa: E402
    install_direct_parent,
    install_from_env,
)


@beat_init.connect
def _on_beat_init(**kwargs: object) -> None:
    install_from_env("beat")


@worker_ready.connect
def _on_worker_ready(**kwargs: object) -> None:
    install_from_env("worker")


# ---------------------------------------------------------------------------
# 异步链路请求关联（S3-B4 延伸）：request_id 经 Celery headers 跨进程传递，
# 任务侧日志（JsonFormatter）同时输出 request_id 与 task_id，可由任一侧定位全链路。
# Beat 定时派发无请求上下文 → 不注入，任务日志自然不带 request_id。
# ---------------------------------------------------------------------------


@before_task_publish.connect
def _inject_request_id_on_publish(headers: dict | None = None, **kwargs: object) -> None:
    """任务投递时把当前请求上下文的 request_id 写入消息 headers。

    信号覆盖 delay / apply_async / send_task / chord / group 全部投递路径
    （含 worker 内级联投递，链路上下文自动延续），无需逐个修改投递点。
    """
    request_id = _request_id_ctx.get()
    if request_id and isinstance(headers, dict):
        headers.setdefault("request_id", request_id)


@task_prerun.connect
def _restore_request_id_on_prerun(task: Task, **kwargs: object) -> None:
    """任务执行前从消息 headers 恢复 request_id 到 contextvar。

    JsonFormatter 读取同一 contextvar，任务侧日志自动携带 request_id；
    无 headers（旧队列消息 / Beat 任务）时不设置。
    """
    headers = getattr(task.request, "headers", None) or {}
    request_id = headers.get("request_id")
    if request_id:
        _request_id_ctx.set(request_id)


@task_postrun.connect
def _clear_request_id_on_postrun(task: object = None, **kwargs: object) -> None:
    """任务结束：清空 request_id contextvar + 为 Prometheus 打点。

    整改 G32：celery_task_total 此前**只有定义、没有任何埋点**，于是所有
    「任务静默失败 / 静默跳过」在监控侧零信号——deploy/prometheus/alerts.yml
    的 celery 失败率告警因无数据永不触发（该文件自述「无数据时不触发，属预期」）。
    G29/G30 修好的失败可见性也因此只能从日志看。

    **重要限制（未解决，如实标注）**：worker 为 prefork 多进程，每个子进程各持
    一份 prometheus_client registry；此处 .inc() 计入子进程内存，父进程的
    /metrics 读不到。要让该指标真正可采集，需二选一：
      1) 设 PROMETHEUS_MULTIPROC_DIR 并用 MultiProcessCollector 聚合；
      2) worker 侧走 Pushgateway / celery-exporter。
    本处先完成打点，方案 (1)/(2) 落地后即可采集——避免继续「连计数都没有」。
    """
    try:
        from app.core.metrics import celery_task_total

        task_name = getattr(task, "name", None) or "unknown"
        status = str(kwargs.get("state") or "UNKNOWN")
        celery_task_total.labels(task_name=task_name, status=status).inc()
    except Exception:  # noqa: BLE001 - 打点失败绝不影响任务流转
        logger.debug("celery_task_total 打点失败", exc_info=True)
    # 原行为保留：清空 contextvar，防 prefork 子进程串行执行时泄漏到下一任务
    _request_id_ctx.set(None)


@after_setup_logger.connect
def _on_after_setup_logger(**kwargs: object) -> None:
    """celery 完成自身日志配置后，在 fork 前应用结构化日志。

    必须在 fork 前（worker 主进程）完成：在 prefork 子进程的
    worker_process_init 中首次初始化日志会在 macOS 上挂死（fork 后
    logging 初始化陷阱）；fork 前就绪后子进程直接继承 root handlers。
    覆盖 worker 与 beat 两种进程（各自的日志初始化后均触发）。
    """
    setup_logging()


@worker_process_init.connect
def _on_worker_process_init(**kwargs: object) -> None:
    install_direct_parent("worker-pool")
    # 结构化日志已在 fork 前 after_setup_logger 完成（见上方注释），
    # 子进程继承 root handlers，此处不再初始化
    try:
        _preload_datasource_config_sync()
        logger.info("worker 子进程已从 sys_config 预载数据源配置")
    except Exception as exc:  # noqa: BLE001
        # 预载失败不阻塞 worker 启动，兜底 .env 默认值（与 API lifespan 行为一致）
        logger.warning("worker 子进程预载数据源配置失败（将使用 .env 默认值）: %s", exc)
