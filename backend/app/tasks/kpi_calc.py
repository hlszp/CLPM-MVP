"""Celery tasks for KPI performance calculation (IDS v3.2 §2.3 — S3-METRIC-003).

v4.0 三层架构：
- DataPlanner 统一取数（L1/L2 缓存 + 8 步预处理 + 查询计划合并）
- 12 个 MetricCalculator 指标计算器（3 核心 + 1 综合 + 8 辅助）
- ConfidenceEvaluator 可信度评估 + 综合评分（P = (A·a+F·f+S·s)/(a+f+s) × R）

设计要点：
- Celery Beat 定时任务（每小时触发全量计算）
- 通过 DataPlanner 获取预处理后的 MetricDataBundle
- 三层计算编排：Layer1（无依赖指标）→ Layer2（有依赖指标）→ Layer3（综合评分）
- 计算结果通过 UPSERT 写入 kpi_snapshot_hourly（含 7 个数据血缘字段）
- 任务幂等（相同 loop_id + ts_start 的 UPSERT 覆盖更新）
- 失败自动重试 3 次
- 数据不足返回 INCONCLUSIVE 状态
"""

from __future__ import annotations

import asyncio
import logging
import time
from bisect import bisect_left
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.contracts.data_types import (
    ControlType,
    DataBlock,
    DataLineage,
    MetricDataBundle,
    MetricResult,
    QualitySummary,
    TagGroup,
    TimeWindow,
)
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import (
    KpiSnapshotCustom,
    KpiSnapshotHourly,
    LoopConfidenceLatest,
    MetricConfig,
)
from app.models.tag import TagRegistry
from app.services.confidence_evaluator import ConfidenceEvaluator
from app.services.metric_calculator import get_calculator
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

# 算法版本号
ALGORITHM_VERSION = "KPI_CALC_v2.0"
ALGORITHM_VERSION_V1 = "KPI_CALC_v1.0"  # 向后兼容回退

# 数据不足阈值：Good 数据占比 < 20% 视为 INCONCLUSIVE
MIN_GOOD_RATIO = 0.20

# 单个 KPI 任务的数据库并发预算。Celery 可同时运行多个任务，且 FastAPI
# 也需要连接余量，因此不能把 PostgreSQL 的连接上限全部交给单次批处理。
CONCURRENCY = 5

# ---------------------------------------------------------------------------
# v4.0 指标代码映射（DB 列名 ↔ Calculator 代码）
# ---------------------------------------------------------------------------

# DB 列名 → Calculator 代码（唯一差异：steady_rate → stability_rate）
_DB_TO_CALCULATOR_METRIC_CODE: dict[str, str] = {
    "accuracy_rate": "accuracy_rate",
    "fast_rate": "fast_rate",
    "steady_rate": "stability_rate",  # DB 列名 steady_rate → Calculator stability_rate
    "effective_auto_rate": "effective_auto_rate",
    "good_value_rate": "good_value_rate",
    "oscillation_rate": "oscillation_rate",
    "saturation_rate": "saturation_rate",
    "stiction_index": "stiction_index",
    "output_trip_index": "output_trip_index",
    "auto_mode_rate": "auto_mode_rate",
    "settling_time": "settling_time",
    "ideal_settling_time": "ideal_settling_time",
}

# Calculator 代码 → DB 列名（反向映射）
_CALCULATOR_TO_DB_METRIC_CODE: dict[str, str] = {
    v: k for k, v in _DB_TO_CALCULATOR_METRIC_CODE.items()
}

# 所有 DB 列名指标代码列表（传递给 DataPlanner.request_bundles 的 metrics 参数）
_ALL_METRIC_CODES_DB: list[str] = list(_DB_TO_CALCULATOR_METRIC_CODE.keys())

# Layer2 依赖关系：Calculator 代码 → 依赖的 Calculator 代码列表
_LAYER2_DEPENDENCIES: dict[str, list[str]] = {
    "stability_rate": ["oscillation_rate"],
    "fast_rate": ["settling_time", "ideal_settling_time"],
}


# ---------------------------------------------------------------------------
# Celery Beat 定时任务
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_hourly_kpi",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_hourly_kpi(self: AsyncTask, ts_start: str | None = None) -> dict:
    """每小时全量计算所有 ACTIVE 回路的 KPI 快照。

    失败自动重试 3 次，指数退避。
    Beat 自动触发时创建 TaskRecord（triggered_by=system），使定时任务
    也出现在「自动任务」页面。

    若 task_tracker 不可用（Redis 异常），回退到直接调用 _do_calculate。

    Args:
        ts_start: 可选，指定计算时间窗起始（ISO 格式），None 时取上一个完整小时
    """
    logger.info("KPI 计算任务开始, task_id=%s, ts_start=%s", self.request.id, ts_start)
    try:
        result = self.run_async(_do_hourly_with_tracking(ts_start=ts_start))
        logger.info("KPI 计算任务完成: %s", result)
        return result
    except Exception as exc:
        # task_tracker 不可用时回退到直接计算（无任务跟踪）
        logger.warning("KPI 计算任务跟踪失败，回退到直接计算: %s", exc)
        ts_start_dt = _parse_ts_start(ts_start)
        return self.run_async(_do_calculate(ts_start=ts_start_dt))


def _parse_ts_start(ts_start: str | None) -> datetime | None:
    """将 ISO 格式字符串解析为 datetime，None 时返回 None。"""
    if not ts_start:
        return None
    try:
        return datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromisoformat(ts_start)


async def _do_hourly_with_tracking(ts_start: str | None = None) -> dict:
    """执行每小时 KPI 计算并创建任务跟踪记录（系统自动触发）。

    Beat 自动触发时调用此函数：先创建 TaskRecord（STANDARD / triggered_by=system），
    再执行计算，最后更新终态。手动触发（API）仍走 ``trigger_standard_evaluation``，
    由 API 层创建 TaskRecord。
    """
    from app.schemas.task import TaskStatus, TaskType
    from app.services import task_tracker

    # 生成标题：自动评估-YYMMDDHH（Shanghai 时区）
    _SHANGHAI = timezone(timedelta(hours=8))
    title = f"自动评估-{datetime.now(_SHANGHAI).strftime('%y%m%d%H')}"

    task_id = await task_tracker.create_task(
        task_type=TaskType.STANDARD,
        created_by="system",
        created_by_id="",
        ts_start=ts_start,
        triggered_by="system",
        title=title,
    )

    await task_tracker.update_status(
        task_id,
        TaskStatus.RUNNING,
        started_at=datetime.now(UTC).isoformat(),
        current_stage="开始计算",
    )

    try:
        result = await _do_calculate(
            ts_start=_parse_ts_start(ts_start),
            task_id=task_id,
            window_index=1,
            total_windows=1,
        )
        await task_tracker.update_status(
            task_id,
            TaskStatus.SUCCESS,
            progress=1.0,
            current_stage="完成",
            finished_at=datetime.now(UTC).isoformat(),
        )
        return result
    except Exception as exc:
        await task_tracker.update_status(
            task_id,
            TaskStatus.FAILED,
            error_message=str(exc),
            finished_at=datetime.now(UTC).isoformat(),
        )
        raise


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_loop_kpi",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_loop_kpi(loop_id: str, ts_start: str | None = None) -> dict:
    """单回路 KPI 计算（可手动触发）。"""
    logger.info("单回路 KPI 计算, loop_id=%s", loop_id)
    return AsyncTask().run_async(_do_calculate_single_loop(loop_id, ts_start))


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_custom_loop_kpi",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_custom_loop_kpi(
    task_id: str,
    loop_id: str,
    ts_start: str,
    ts_end: str | None = None,
) -> dict:
    """自定义任务单回路 KPI 计算（Celery 入口，P1 #12）。"""
    logger.info(
        "自定义任务单回路 KPI 计算, task_id=%s, loop_id=%s, ts_start=%s, ts_end=%s",
        task_id,
        loop_id,
        ts_start,
        ts_end,
    )
    return AsyncTask().run_async(_do_calculate_custom_loop(task_id, loop_id, ts_start, ts_end))


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_custom_batch_kpi",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_custom_batch_kpi(
    task_id: str,
    loop_ids: list[str],
    ts_start: str,
    ts_end: str | None = None,
) -> dict:
    """自定义任务批量 KPI 计算（批量预加载 + 并发处理，优化性能）。"""
    logger.info(
        "自定义任务批量 KPI 计算, task_id=%s, loop_count=%d, ts_start=%s, ts_end=%s",
        task_id,
        len(loop_ids),
        ts_start,
        ts_end,
    )
    return AsyncTask().run_async(_do_calculate_custom_batch(task_id, loop_ids, ts_start, ts_end))


def _on_prewarm_failure(
    exc: Exception,
    task_id: str,
    args: tuple,
    kwargs: dict,
    einfo,
) -> None:
    """prewarm_cache 任务失败回调。

    记录详细错误日志，便于监控和告警。
    """
    logger.error(
        "缓存预热任务失败: task_id=%s, args=%s, kwargs=%s, error=%s",
        task_id,
        args,
        kwargs,
        exc,
        exc_info=True,
    )
    try:
        import asyncio

        from app.core.redis import redis_client

        asyncio.run(
            redis_client.incr("clpm:metrics:prewarm_failure_count"),
        )
    except Exception:  # noqa: BLE001
        logger.warning("预热失败指标记录失败", exc_info=True)


@celery_app.task(
    name="app.tasks.kpi_calc.prewarm_cache",
    on_failure=_on_prewarm_failure,
)
def prewarm_cache(ts_start: str | None = None) -> dict:
    """预热 L1/L2 缓存（仅供手工/运维调用，定时预热策略已废止）。

    原"每小时 55 分"定时预热已废止：其预热窗口与整点任务窗口错位一小时，
    从未命中。整点任务数据来源为 realtime 滚动 1 小时缓存 + TDengine 回源。
    本任务保留用于运维场景（如大规模重算前手工预热指定窗口）。

    Args:
        ts_start: 可选，指定预热的时间窗起始（ISO 格式），None 时取上一个完整小时
    """
    logger.info("缓存预热任务开始, ts_start=%s", ts_start)
    return AsyncTask().run_async(_do_prewarm(ts_start))


async def _do_prewarm(ts_start: str | None = None) -> dict:
    """执行缓存预热的实际逻辑。"""
    from app.core.db import AsyncSessionLocal

    now = datetime.now(UTC).replace(tzinfo=None)
    if ts_start:
        try:
            ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            ts_start_dt = datetime.fromisoformat(ts_start).replace(tzinfo=None)
    else:
        ts_end_dt = now.replace(minute=0, second=0, microsecond=0)
        ts_start_dt = ts_end_dt - timedelta(hours=1)
    ts_end_dt = ts_start_dt + timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        stmt = select(LoopLedger).where(
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
        result = await db.execute(stmt)
        loops = list(result.scalars().all())
        loop_configs = await _batch_load_loop_configs(db, [str(lp.id) for lp in loops])

    if not loops:
        return {"total": 0, "succeeded": 0, "failed": 0, "errors": [], "elapsed": 0.0}

    t_start = time.perf_counter()
    prewarm_result = await _prewarm_cache_for_loops(loops, ts_start_dt, ts_end_dt, loop_configs)
    elapsed = time.perf_counter() - t_start
    prewarm_result["elapsed"] = elapsed
    logger.info(
        "缓存预热完成: total=%d, succeeded=%d, failed=%d, elapsed=%.3fs",
        prewarm_result["total"],
        prewarm_result["succeeded"],
        prewarm_result["failed"],
        elapsed,
    )
    if prewarm_result["succeeded"] == 0:
        raise RuntimeError("缓存预热失败：没有任何回路完成")
    return prewarm_result


# ---------------------------------------------------------------------------
# Beat 调度配置：每小时执行一次 + 每日 00:05 + 每月 1 日 00:10
# ---------------------------------------------------------------------------

from celery import chord, group  # noqa: E402
from celery.schedules import crontab  # noqa: E402

# 默认计算周期（秒）— 可被 EngineRule EVAL_CALC_CYCLE 覆盖
_DEFAULT_CALC_CYCLE_SECONDS = 3600.0

_beat_entry = {
    "task": "app.tasks.kpi_calc.calculate_hourly_kpi",
    "schedule": crontab(minute=0, hour="*"),  # 每小时 0 分触发（默认值，beat_init 时从 DB 覆盖）
}

# 合并到 celery_app 的 beat_schedule（与 aas_sync 的 beat 共存）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["kpi-calc-hourly"] = _beat_entry
# 注：原 "prewarm-cache"（每小时 55 分）预热已废止 —— 其预热窗口为
# [floor(now)-1h, floor(now))，与下一整点任务需要的窗口错位一小时，从未命中；
# 且未闭合窗口预热会产生不完整数据。整点任务的数据来源统一为
# realtime 滚动 1 小时缓存（provider 自动探测）+ TDengine 回源。
# 节点级日聚合：每日 00:05 执行（聚合前一天的数据）
_existing_beat["node-kpi-daily"] = {
    "task": "app.tasks.kpi_calc.calculate_daily_kpi",
    "schedule": crontab(hour=0, minute=5),
}
# 节点级月聚合：每月 1 日 00:10 执行（聚合上一个月的数据）
_existing_beat["node-kpi-monthly"] = {
    "task": "app.tasks.kpi_calc.calculate_monthly_kpi",
    "schedule": crontab(hour=0, minute=10, day_of_month=1),
}
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


# ---------------------------------------------------------------------------
# beat_init 信号：Beat 启动时从 EngineRule 读取策略配置，动态更新调度周期
# + Redis Pub/Sub 监听线程：策略配置变更后即时重载（无需重启 Beat）
# ---------------------------------------------------------------------------

from celery.signals import beat_init  # noqa: E402

# Redis pub/sub 频道：API 更新 EngineRule 后发布通知，Beat 订阅后即时重载
BEAT_RELOAD_CHANNEL = "clpm:beat:reload"


def _apply_rules_to_schedule(rules: dict) -> None:
    """将 EngineRule 配置应用到 celery_app.conf.beat_schedule。

    支持 EngineRule：
    - EVAL_CALC_CYCLE (cycle_minutes): KPI 计算周期，覆盖 kpi-calc-hourly 的 schedule
    - EVAL_CALC_CYCLE.is_enabled=False: 禁用自动计算（从 beat_schedule 删除条目）
    """
    if not rules:
        return

    calc_cycle = rules.get("EVAL_CALC_CYCLE")
    if not calc_cycle:
        return

    is_enabled = calc_cycle.get("is_enabled", True)
    cycle_minutes = calc_cycle.get("cycle_minutes", 60)

    current_schedule = dict(celery_app.conf.beat_schedule or {})

    if not is_enabled:
        current_schedule.pop("kpi-calc-hourly", None)
        logger.info("beat_schedule: EVAL_CALC_CYCLE 已禁用，kpi-calc-hourly 调度已移除")
    else:
        if int(cycle_minutes) == 60:
            schedule_expr = crontab(minute=0, hour="*")
        elif int(cycle_minutes) > 0 and int(cycle_minutes) < 60:
            schedule_expr = crontab(minute=f"*/{int(cycle_minutes)}")
        else:
            schedule_expr = float(cycle_minutes) * 60.0

        current_schedule["kpi-calc-hourly"] = {
            "task": "app.tasks.kpi_calc.calculate_hourly_kpi",
            "schedule": schedule_expr,
        }
        logger.info(
            "beat_schedule: EVAL_CALC_CYCLE 已应用，kpi-calc-hourly 周期 = %s 分钟",
            cycle_minutes,
        )

    celery_app.conf.beat_schedule = current_schedule


def _reload_beat_schedule_from_db() -> None:
    """从 DB 读取 EngineRule 并更新 beat_schedule（同步包装，可在子线程调用）."""
    try:
        rules = asyncio.run(_load_engine_rules_from_db())
    except Exception as exc:
        logger.warning("reload_beat: 从 DB 读取 EngineRule 失败，保持当前调度周期: %s", exc)
        return
    _apply_rules_to_schedule(rules)


def _start_beat_reload_listener() -> None:
    """启动 Redis pub/sub 监听线程，收到通知后动态重载 beat_schedule。

    API 端 update_engine_rule() 更新 EVAL_CALC_CYCLE 后会向
    ``BEAT_RELOAD_CHANNEL`` 发布消息，本线程订阅后即时调用
    ``_reload_beat_schedule_from_db()`` 完成热重载，无需重启 Beat 进程。
    """
    import threading

    import redis as sync_redis

    from app.core.config import settings

    def _listener() -> None:
        try:
            client = sync_redis.from_url(settings.redis_url)
            pubsub = client.pubsub()
            pubsub.subscribe(BEAT_RELOAD_CHANNEL)
            logger.info("Beat reload listener 已启动，订阅频道=%s", BEAT_RELOAD_CHANNEL)
            for message in pubsub.listen():
                if message["type"] == "message":
                    logger.info("收到 Beat 重载通知: %s", message["data"])
                    _reload_beat_schedule_from_db()
        except Exception as exc:
            logger.warning("Beat reload listener 异常: %s", exc)

    thread = threading.Thread(target=_listener, daemon=True, name="beat-reload-listener")
    thread.start()


@beat_init.connect
def _apply_engine_rules(sender=None, **kwargs):
    """Beat 启动时从 DB 读取 EngineRule，动态更新调度周期。

    同时启动 Redis pub/sub 监听线程，支持策略配置变更后即时生效（无需重启 Beat）。
    """
    _reload_beat_schedule_from_db()
    _start_beat_reload_listener()


async def _load_engine_rules_from_db() -> dict:
    """从 DB 读取 EngineRule 配置，返回 {rule_code: {params, is_enabled}} 字典。"""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.models.engine import EngineRule

    result = {}
    async with AsyncSessionLocal() as db:
        stmt = select(EngineRule).where(
            EngineRule.rule_code.in_(
                [
                    "EVAL_CALC_CYCLE",
                    "DATA_FETCH_WINDOW",
                    "SCHEDULE_CONCURRENCY",
                ]
            )
        )
        rows = await db.execute(stmt)
        for rule in rows.scalars().all():
            cycle_minutes = None
            if rule.rule_code == "EVAL_CALC_CYCLE":
                cycle_minutes = (rule.params or {}).get("cycle_minutes", 60)
            result[rule.rule_code] = {
                "params": rule.params or {},
                "is_enabled": rule.is_enabled if rule.is_enabled is not None else True,
                "cycle_minutes": cycle_minutes,
            }
    return result


# ---------------------------------------------------------------------------
# 异步计算逻辑
# ---------------------------------------------------------------------------

# 预热阶段仍会为每个回路创建独立 DB session；必须纳入 PostgreSQL
# 连接预算，不能按当前回路总数无界扩张。
_PREWARM_CONCURRENCY = 5


async def _prewarm_cache_for_loops(
    loops: list[LoopLedger],
    ts_start: datetime,
    ts_end: datetime,
    loop_configs: dict[str, dict],
) -> dict[str, Any]:
    """预热 L1/L2 缓存：并行调用 DataPlanner.request_bundles 取数并缓存。

    两阶段计算的核心：将 I/O 密集的取数+预处理与 CPU 密集的指标计算分离。
    本函数仅负责取数+预处理+写缓存，不做指标计算和 DB UPSERT；但每个
    回路仍会占用独立 DB session，因此与计算阶段共用保守的连接预算。

    完成后，后续计算阶段调用 request_bundles 将全部命中 L2 缓存（~0.04s/loop）。

    Args:
        loops: 待计算的回路列表
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        loop_configs: 批量预加载的回路配置（OP 限位 + PV 量程 + config_version）
    """
    from app.core.db import AsyncSessionLocal

    sem = asyncio.Semaphore(_PREWARM_CONCURRENCY)
    op_limits_map = {
        str(lid): (cfg["op_lower"], cfg["op_upper"]) for lid, cfg in loop_configs.items()
    }

    async def _prewarm_one(loop: LoopLedger) -> str | None:
        async with sem:
            async with AsyncSessionLocal() as warm_db:
                try:
                    loop_cfg = loop_configs.get(str(loop.id))
                    config_loader = _make_config_loader(loop_cfg)
                    data_planner = _build_data_planner(
                        warm_db,
                        bundle_cache=_get_shared_bundle_cache(),
                    )
                    data_planner._config_loader = config_loader
                    data_planner._preloaded_op_limits = op_limits_map
                    control_type = _loop_type_to_control_type(loop.loop_type)
                    time_window = TimeWindow(start=ts_start, end=ts_end)
                    await data_planner.request_bundles(
                        loop_id=str(loop.id),
                        metrics=_ALL_METRIC_CODES_DB,
                        time_window=time_window,
                        control_type=control_type,
                    )
                    return None
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "缓存预热失败: loop=%s",
                        loop.tag_name,
                        exc_info=True,
                    )
                    return f"{loop.id}: {exc}"

    errors = [
        error
        for error in await asyncio.gather(*[_prewarm_one(loop) for loop in loops])
        if error is not None
    ]
    return {
        "total": len(loops),
        "succeeded": len(loops) - len(errors),
        "failed": len(errors),
        "errors": errors,
    }


async def _run_batch_loop_calculations(
    *,
    loops: list[LoopLedger],
    loop_configs: dict[str, dict],
    metric_configs: dict[str, MetricConfig],
    ts_start: datetime,
    ts_end: datetime,
    type_weights: dict[str, dict] | None,
    custom_task_id: str | None = None,
    on_completed=None,
    bundle_cache=None,
    concurrency: int = CONCURRENCY,
) -> list[dict | None | Exception]:
    """Run loop KPI calculations with shared bounded-concurrency orchestration.

    Each loop receives an independent database session. Standard and custom
    batches differ only in snapshot destination and optional progress callback.

    Args:
        bundle_cache: L2 Bundle 缓存实例。None=使用共享缓存（标准/自定义评估），
            False=禁用 L1/L2 缓存（backfill 场景，避免无用的缓存读写 I/O）。
    """
    from app.core.db import AsyncSessionLocal

    sem = asyncio.Semaphore(concurrency)
    op_limits_map = {
        str(loop_id): (cfg["op_lower"], cfg["op_upper"]) for loop_id, cfg in loop_configs.items()
    }

    async def _calculate_one(loop: LoopLedger) -> dict | None:
        async with sem:
            async with AsyncSessionLocal() as worker_db:
                try:
                    config_loader = _make_config_loader(loop_configs.get(str(loop.id)))
                    data_planner = _build_data_planner(
                        worker_db,
                        bundle_cache=False if bundle_cache is False else _get_shared_bundle_cache(),
                    )
                    data_planner._config_loader = config_loader
                    data_planner._preloaded_op_limits = op_limits_map
                    result = await _calculate_loop_kpi(
                        db=worker_db,
                        loop=loop,
                        metric_configs=metric_configs,
                        ts_start=ts_start,
                        ts_end=ts_end,
                        data_planner=data_planner,
                        type_weights=type_weights,
                        custom_task_id=custom_task_id,
                    )
                    await worker_db.commit()
                    return result
                except Exception:
                    await worker_db.rollback()
                    raise
                finally:
                    if on_completed is not None:
                        try:
                            await on_completed(str(loop.id))
                        except Exception:  # noqa: BLE001
                            logger.warning("批量 KPI 进度更新失败", exc_info=True)

    return await asyncio.gather(*[_calculate_one(loop) for loop in loops], return_exceptions=True)


def _summarize_batch_results(results: list[dict | None | Exception]) -> dict[str, int]:
    """Classify completed loop calculations consistently across batch entrypoints."""
    summary = {"success": 0, "inconclusive": 0, "failed": 0}
    for result in results:
        if isinstance(result, Exception):
            summary["failed"] += 1
            logger.warning("回路计算失败: %s", result)
        elif result is None:
            summary["failed"] += 1
        elif result.get("status") == "INCONCLUSIVE":
            summary["inconclusive"] += 1
        else:
            summary["success"] += 1
    return summary


async def _do_calculate(
    ts_start: str | datetime | None = None,
    loop_ids: list[str] | None = None,
    task_id: str | None = None,
    window_index: int = 0,
    total_windows: int = 0,
) -> dict:
    """执行全量 KPI 计算的实际 async 逻辑。

    Args:
        ts_start: 时间窗起始（ISO 8601 字符串或 datetime）；None 时取上一个完整计算周期
        loop_ids: 回路 ID 过滤列表。None=全量；非空列表=仅这些回路；
            空列表=直接返回 0 结果（用于 backfill 精准重算）。
        task_id: Redis 任务跟踪 ID（backfill 调用时传入，用于逐回路进度更新）。
        window_index: 当前窗口序号（1-based），用于细粒度进度计算。
        total_windows: 总窗口数，用于细粒度进度计算。
    """
    from app.core.db import AsyncSessionLocal

    # 空列表提前返回（backfill 调用时明确不需要计算任何回路）
    if loop_ids is not None and len(loop_ids) == 0:
        return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

    # 计算时间窗 — naive UTC，对齐 DB TIMESTAMP WITHOUT TIME ZONE
    now = datetime.now(UTC).replace(tzinfo=None)
    if ts_start:
        if isinstance(ts_start, datetime):
            ts_start_dt = ts_start.replace(tzinfo=None) if ts_start.tzinfo else ts_start
        else:
            try:
                ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except ValueError:
                ts_start_dt = datetime.fromisoformat(ts_start).replace(tzinfo=None)
        ts_end_dt = ts_start_dt + timedelta(hours=1)
    else:
        ts_end_dt = now.replace(minute=0, second=0, microsecond=0)
        ts_start_dt = ts_end_dt - timedelta(hours=1)

    # 主 session 仅用于查询回路列表和指标配置（只读，无并发）
    async with AsyncSessionLocal() as db:
        # 1. 查询所有 ACTIVE/READY 状态回路（支持 loop_ids 精准过滤）
        stmt = select(LoopLedger).where(
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
        if loop_ids is not None:
            stmt = stmt.where(LoopLedger.id.in_(loop_ids))
        loop_result = await db.execute(stmt)
        loops = list(loop_result.scalars().all())
        logger.info("待计算回路数: %d", len(loops))

        if not loops:
            return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

        # 2. 加载指标配置
        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        # 2.1 批量加载回路类型权重（v2 算法用）
        from app.services.loop_config import get_loop_type_weights_map

        type_weights = await get_loop_type_weights_map(db)
        loop_configs = await _batch_load_loop_configs(db, [str(lp.id) for lp in loops])
        logger.info("已加载回路类型权重: %s", list(type_weights.keys()))

    loops_count = len(loops)

    # 注：原 L2 命中率检查 + 兜底预热已废止。计算阶段由 DataPlanner 按需取数：
    # 最近 1 小时窗口自动命中 realtime 滚动缓存，否则回源 TDengine（见 provider）。
    completed_in_window = 0

    async def _on_completed(_loop_id: str) -> None:
        nonlocal completed_in_window
        completed_in_window += 1
        if task_id and total_windows > 0 and loops_count > 0:
            await _update_backfill_progress(
                task_id,
                window_index,
                total_windows,
                completed_in_window,
                loops_count,
            )

    t_calc_start = time.perf_counter()
    results = await _run_batch_loop_calculations(
        loops=loops,
        loop_configs=loop_configs,
        metric_configs=metric_configs,
        ts_start=ts_start_dt,
        ts_end=ts_end_dt,
        type_weights=type_weights,
        on_completed=_on_completed if task_id else None,
    )
    t_calc_elapsed = time.perf_counter() - t_calc_start
    logger.info(
        "并发计算完成: loops=%d, concurrency=%d, elapsed=%.3fs, avg=%.3fs/loop",
        loops_count,
        CONCURRENCY,
        t_calc_elapsed,
        t_calc_elapsed / max(loops_count, 1),
    )
    summary = _summarize_batch_results(results)

    # 级联触发节点级 KPI 聚合（确保回路快照已写入后再聚合，消除时序竞态）
    try:
        calculate_node_kpi_hourly.delay()
        logger.info("已触发节点级 KPI 聚合任务（回路级计算完成后级联）")
    except Exception as exc:  # noqa: BLE001
        logger.warning("触发节点级 KPI 聚合任务失败: %s", exc)

    return {
        "total": len(loops),
        "success": summary["success"],
        "inconclusive": summary["inconclusive"],
        "failed": summary["failed"],
        "ts_start": ts_start_dt.isoformat(),
        "ts_end": ts_end_dt.isoformat(),
    }


async def _do_calculate_single_loop(loop_id: str, ts_start: str | None = None) -> dict:
    """单回路 KPI 计算。"""
    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
        loop = result.scalar_one_or_none()
        if loop is None:
            return {"loopId": loop_id, "status": "FAILED", "error": "回路不存在"}

        # 时间窗（保持 tzinfo：带 Z → aware UTC，不带 Z → naive）
        now = datetime.now(UTC).replace(tzinfo=None)
        if ts_start:
            try:
                ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
            except ValueError:
                ts_start_dt = datetime.fromisoformat(ts_start)
        else:
            ts_start_dt = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        ts_end_dt = ts_start_dt + timedelta(hours=1)

        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        # 加载回路类型权重（v2 算法用）
        from app.services.loop_config import get_loop_type_weights_map

        type_weights = await get_loop_type_weights_map(db)

        data_planner = _build_data_planner(db)
        snap = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs=metric_configs,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
            data_planner=data_planner,
            type_weights=type_weights,
        )
        await db.commit()
        return snap or {"loopId": loop_id, "status": "FAILED"}


async def _do_calculate_custom_loop(
    task_id: str,
    loop_id: str,
    ts_start: str,
    ts_end: str | None = None,
) -> dict:
    """自定义任务单回路 KPI 计算（支持用户指定时间窗，P1 #12）。

    与 _do_calculate_single_loop 的差异：
    - 支持用户指定 ts_end（非默认 1 小时窗口）
    - ts_end 为 None 时使用 EngineRule 的 cycle_minutes
    - 写入 kpi_snapshot_custom（通过 custom_task_id 参数路由）

    Args:
        task_id: 自定义任务 ID
        loop_id: 回路 ID
        ts_start: 时间窗起始（ISO 8601）
        ts_end: 时间窗结束（ISO 8601），None 时使用 EngineRule 的 cycle_minutes
    """
    from app.core.db import AsyncSessionLocal
    from app.services.engine_rule_loader import get_engine_rule_loader
    from app.services.loop_config import get_loop_type_weights_map

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
        loop = result.scalar_one_or_none()
        if loop is None:
            return {"loopId": loop_id, "taskId": task_id, "status": "FAILED", "error": "回路不存在"}

        # 解析 ts_start（保持 tzinfo：带 Z → aware UTC，不带 Z → naive）
        ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))

        # ts_end：用户提供 → 解析；未提供 → ts_start + cycle_minutes
        if ts_end is not None:
            ts_end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))
        else:
            engine = get_engine_rule_loader()
            cycle_minutes = await engine.get_calc_cycle_minutes()
            ts_end_dt = ts_start_dt + timedelta(minutes=cycle_minutes)

        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        type_weights = await get_loop_type_weights_map(db)

        data_planner = _build_data_planner(db)
        snap = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs=metric_configs,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
            data_planner=data_planner,
            type_weights=type_weights,
            custom_task_id=task_id,
        )
        await db.commit()
        return snap or {"loopId": loop_id, "taskId": task_id, "status": "FAILED"}


async def _do_calculate_custom_batch(
    task_id: str,
    loop_ids: list[str],
    ts_start: str,
    ts_end: str | None = None,
) -> dict:
    """批量自定义评估任务（复用标准任务的批量预加载 + 并发处理）。"""
    from app.core.db import AsyncSessionLocal
    from app.services.engine_rule_loader import get_engine_rule_loader
    from app.services.loop_config import get_loop_type_weights_map

    if not loop_ids:
        return {"total": 0, "success": 0, "failed": 0}

    ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
    if ts_end is not None:
        ts_end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))
    else:
        engine = get_engine_rule_loader()
        cycle_minutes = await engine.get_calc_cycle_minutes()
        ts_end_dt = ts_start_dt + timedelta(minutes=cycle_minutes)

    async with AsyncSessionLocal() as db:
        stmt = select(LoopLedger).where(
            LoopLedger.id.in_(loop_ids),
            LoopLedger.is_active.is_(True),
        )
        loop_result = await db.execute(stmt)
        loops = list(loop_result.scalars().all())
        logger.info("待计算回路数: %d", len(loops))

        if not loops:
            return {"total": 0, "success": 0, "failed": 0}

        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        type_weights = await get_loop_type_weights_map(db)

        loop_configs = await _batch_load_loop_configs(db, [str(lp.id) for lp in loops])

        t_prewarm_start = time.perf_counter()
        prewarm_result = await _prewarm_cache_for_loops(loops, ts_start_dt, ts_end_dt, loop_configs)
        t_prewarm_elapsed = time.perf_counter() - t_prewarm_start
        logger.info(
            "自定义批量缓存预热完成: total=%d, succeeded=%d, failed=%d, elapsed=%.3fs",
            prewarm_result["total"],
            prewarm_result["succeeded"],
            prewarm_result["failed"],
            t_prewarm_elapsed,
        )

        t_calc_start = time.perf_counter()
        results = await _run_batch_loop_calculations(
            loops=loops,
            loop_configs=loop_configs,
            metric_configs=metric_configs,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
            type_weights=type_weights,
            custom_task_id=task_id,
        )
        t_calc_elapsed = time.perf_counter() - t_calc_start
        logger.info(
            "自定义批量计算完成: loops=%d, concurrency=%d, elapsed=%.3fs, avg=%.3fs/loop",
            len(loops),
            CONCURRENCY,
            t_calc_elapsed,
            t_calc_elapsed / max(len(loops), 1),
        )
        summary = _summarize_batch_results(results)

        return {
            "total": len(loops),
            "success": summary["success"],
            "inconclusive": summary["inconclusive"],
            "failed": summary["failed"],
            "prewarm": prewarm_result,
            "ts_start": ts_start_dt.isoformat(),
            "ts_end": ts_end_dt.isoformat(),
        }


async def _calculate_loop_kpi(
    db,
    loop: LoopLedger,
    metric_configs: dict[str, MetricConfig],
    ts_start: datetime,
    ts_end: datetime,
    data_planner,
    type_weights: dict[str, dict] | None = None,
    custom_task_id: str | None = None,
) -> dict | None:
    """计算单回路 KPI 并写入快照（v4.0 三层架构，幂等）。

    v4.0 架构：DataPlanner → 12 Calculator → ConfidenceEvaluator
    - 通过 data_planner.request_bundles 获取预处理后的 MetricDataBundle
    - _compute_kpis_three_layer 编排三层计算
    - _persist_snapshot 通过 UPSERT 写入快照（含 7 个数据血缘字段）

    Args:
        db: 异步数据库会话
        loop: 回路对象
        metric_configs: 指标配置字典 {metric_code: MetricConfig}
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        data_planner: DataPlanner 实例（v4.0 统一取数）
        type_weights: 回路类型权重映射（LoopTypeWeight）
        custom_task_id: 自定义任务 ID（非 None 时写入 kpi_snapshot_custom）

    Returns:
        快照字典，包含 status 字段
    """
    from app.services.loop_config import infer_score_type

    control_type = _loop_type_to_control_type(loop.loop_type)
    time_window = TimeWindow(start=ts_start, end=ts_end)

    # 通过 DataPlanner 获取所有指标的 MetricDataBundle
    try:
        bundles = await data_planner.request_bundles(
            loop_id=str(loop.id),
            metrics=_ALL_METRIC_CODES_DB,
            time_window=time_window,
            control_type=control_type,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DataPlanner 取数失败（回路 %s）: %s", loop.tag_name, exc)
        return await _persist_snapshot(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
            custom_task_id=custom_task_id,
        )

    if not bundles:
        logger.info("回路 %s 无数据（空 Bundle），返回 INCONCLUSIVE", loop.tag_name)
        return await _persist_snapshot(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
            custom_task_id=custom_task_id,
        )

    # 构造虚拟 CONFIG bundle（提供 control_type / 手动理想稳态时间信号给计算器）
    config_bundle = _build_config_bundle(str(loop.id), control_type, loop.ideal_settling_time)

    # 构造权重映射（MetricConfig.weight > LoopTypeWeight > None）
    score_type = infer_score_type(loop.loop_type)
    weights = _build_weights_map(type_weights, score_type, metric_configs)

    # 三层计算：Layer1（无依赖）→ Layer2（有依赖）→ Layer3（综合评分）
    metric_results, composite_result = _compute_kpis_three_layer(bundles, config_bundle, weights)

    # 12 子指标值+可信度（随 loop_confidence_latest 存储）
    metrics_detail = _extract_metrics_detail(metric_results)

    # 综合评分为 None（R 可信度 E 级）→ INCONCLUSIVE
    if composite_result.value is None:
        logger.info("回路 %s 综合评分为 None（E 级），返回 INCONCLUSIVE", loop.tag_name)
        return await _persist_snapshot(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
            custom_task_id=custom_task_id,
            confidence_level=composite_result.confidence_level,
            metrics_detail=metrics_detail,
        )

    # 提取 KPI 值（Calculator 代码 → DB 列名）
    kpi_values = _extract_kpi_values(metric_results)

    # 提取数据血缘信息
    lineage_info = _extract_lineage_info(metric_results, composite_result)

    # 判定状态：必需指标缺失 → PARTIAL
    status = "SUCCESS"
    required_kpis = ("good_value_rate", "auto_mode_rate", "steady_rate")
    if any(kpi_values.get(k) is None for k in required_kpis):
        status = "PARTIAL"

    return await _persist_snapshot(
        db=db,
        loop_id=str(loop.id),
        ts_start=ts_start,
        ts_end=ts_end,
        status=status,
        custom_task_id=custom_task_id,
        metrics_detail=metrics_detail,
        score=_quantize(Decimal(str(composite_result.value)))
        if composite_result.value is not None
        else None,
        good_value_rate=kpi_values.get("good_value_rate"),
        auto_mode_rate=kpi_values.get("auto_mode_rate"),
        effective_auto_rate=kpi_values.get("effective_auto_rate"),
        steady_rate=kpi_values.get("steady_rate"),
        accuracy_rate=kpi_values.get("accuracy_rate"),
        fast_rate=kpi_values.get("fast_rate"),
        oscillation_rate=kpi_values.get("oscillation_rate"),
        saturation_rate=kpi_values.get("saturation_rate"),
        stiction_index=kpi_values.get("stiction_index"),
        output_trip_index=kpi_values.get("output_trip_index"),
        settling_time=kpi_values.get("settling_time"),
        ideal_settling_time=kpi_values.get("ideal_settling_time"),
        **lineage_info,
    )


# ---------------------------------------------------------------------------
# v4.0 辅助函数
# ---------------------------------------------------------------------------


def _loop_type_to_control_type(loop_type: str | None) -> ControlType:
    """将回路类型映射为 ControlType（DataPlanner 采样策略用）。

    映射关系（对齐 DDS §2.3）：
        FLOW → FLOW
        PRESSURE → PRESSURE
        TEMPERATURE → TEMPERATURE
        LEVEL → LEVEL
        ANALYSIS → COMPOSITION
        其他（SPEED/OTHER/None/未知）→ FLOW（回退）
    """
    mapping = {
        "FLOW": ControlType.FLOW,
        "PRESSURE": ControlType.PRESSURE,
        "TEMPERATURE": ControlType.TEMPERATURE,
        "LEVEL": ControlType.LEVEL,
        "ANALYSIS": ControlType.COMPOSITION,
    }
    if not loop_type:
        return ControlType.FLOW
    return mapping.get(loop_type.upper(), ControlType.FLOW)


def _build_config_bundle(
    loop_id: str,
    control_type: ControlType,
    ideal_settling_time: float | None = None,
) -> MetricDataBundle:
    """构造虚拟 CONFIG bundle（提供 control_type 信号给 ideal_settling_time 计算器）。

    CONFIG bundle 不查询数据库，直接构造一个 valid_rate=1.0 的 DataBlock，
    signals 中包含 control_type 信号，供 IdealSettlingTimeCalculator 读取。

    Args:
        loop_id: 回路 ID
        control_type: 控制类型（FC/PC/TC/LC/CC）
        ideal_settling_time: 回路级手动配置的理想稳态时间（秒，loop_ledger.ideal_settling_time）。
            非 None 且 > 0 时注入 signals，calculator 走 manual 分支（最高优先级）
    """
    ts = datetime.now(UTC)
    signals: dict[str, list] = {"control_type": [control_type.value]}
    if ideal_settling_time is not None:
        try:
            manual_ist = float(ideal_settling_time)
        except (TypeError, ValueError):
            manual_ist = 0.0
        if manual_ist > 0:
            signals["ideal_settling_time"] = [manual_ist]
    data_block = DataBlock(
        data_block_id=f"config_{loop_id}",
        loop_id=loop_id,
        tag_group=TagGroup.CONFIG.value,
        sampling_freq="config",
        timestamps=[ts],
        signals=signals,
        validity={},
        quality_summary=QualitySummary(total_count=1, valid_count=1, valid_rate=1.0),
        point_count=1,
    )
    return MetricDataBundle(
        metric_code="ideal_settling_time",
        data_block=data_block,
        mask_expression="true",
        masked_indices=[0],
        lineage=DataLineage(
            sampling_freq="config",
            aggregation_policy="NONE",
            quality_policy="CONFIG",
            tag_group=TagGroup.CONFIG.value,
            data_block_ids=[data_block.data_block_id],
            valid_rate=1.0,
            data_policy_version="config_v1",
            algorithm_version=ALGORITHM_VERSION,
        ),
    )


def _build_weights_map(
    type_weights: dict[str, dict] | None,
    score_type: str,
    metric_configs: dict[str, MetricConfig] | None = None,
) -> dict[str, float] | None:
    """构造权重映射（Calculator 代码 → 权重值）。

    优先级链：MetricConfig.weight > LoopTypeWeight > None

    - 若 metric_configs 中 3 个核心指标（accuracy_rate/fast_rate/steady_rate）
      的 weight 全部有效（非 None、非 0），则归一化后使用 MetricConfig 权重
    - 否则回退到 LoopTypeWeight（type_weights[score_type]）
    - 两者都无 → 返回 None（使用 ConfidenceEvaluator 默认权重）

    Returns:
        {"accuracy_rate": float, "fast_rate": float, "stability_rate": float}
        或 None
    """
    # 核心指标 DB 列名 → Calculator 代码
    core_metrics = (
        ("accuracy_rate", "accuracy_rate"),
        ("fast_rate", "fast_rate"),
        ("steady_rate", "stability_rate"),
    )

    # 尝试 MetricConfig.weight 优先
    if metric_configs is not None:
        mc_weights = {}
        all_valid = True
        for db_code, calc_code in core_metrics:
            config = metric_configs.get(db_code)
            if config is not None and config.weight is not None and config.weight > 0:
                mc_weights[calc_code] = float(config.weight)
            else:
                all_valid = False
                break

        if all_valid:
            # 归一化到 0-1（总和应为 100，但容错处理非标准总和）
            total = sum(mc_weights.values())
            if total > 0:
                return {k: v / total for k, v in mc_weights.items()}

    # 回退到 LoopTypeWeight
    if not type_weights or score_type not in type_weights:
        return None

    w = type_weights[score_type]
    weight_a = w.get("weight_a", 0)
    weight_f = w.get("weight_f", 0)
    weight_s = w.get("weight_s", 0)

    return {
        "accuracy_rate": float(weight_a) if weight_a is not None else 0.0,
        "fast_rate": float(weight_f) if weight_f is not None else 0.0,
        "stability_rate": float(weight_s) if weight_s is not None else 0.0,
    }


def _compute_kpis_three_layer(
    bundles: list[MetricDataBundle],
    config_bundle: MetricDataBundle,
    weights: dict[str, float] | None,
) -> tuple[dict[str, MetricResult], MetricResult]:
    """三层计算编排：Layer1（无依赖）→ Layer2（有依赖）→ Layer3（综合评分）。

    Layer1: 10 个无依赖指标（accuracy_rate, effective_auto_rate, good_value_rate,
            oscillation_rate, saturation_rate, stiction_index, output_trip_index,
            auto_mode_rate, settling_time, ideal_settling_time）
    Layer2: 2 个有依赖指标
            - stability_rate ← oscillation_rate
            - fast_rate ← settling_time + ideal_settling_time
    Layer3: ConfidenceEvaluator.compute_composite_score(metric_results, weights)

    Args:
        bundles: DataPlanner 返回的 MetricDataBundle 列表（metric_code 为 DB 列名）
        config_bundle: 虚拟 CONFIG bundle（提供 control_type 信号）
        weights: 权重映射（None 时用默认权重）

    Returns:
        (metric_results dict[calc_code, MetricResult], composite MetricResult)
    """
    metric_results: dict[str, MetricResult] = {}

    # 构建 bundle 索引：DB 列名 → bundle
    bundle_map: dict[str, MetricDataBundle] = {b.metric_code: b for b in bundles}

    # --- Layer1: 10 个无依赖指标 ---
    # DB 列名列表（按 _DB_TO_CALCULATOR_METRIC_CODE 映射为 Calculator 代码）
    layer1_db_codes = [
        "accuracy_rate",
        "effective_auto_rate",
        "good_value_rate",
        "oscillation_rate",
        "saturation_rate",
        "stiction_index",
        "output_trip_index",
        "auto_mode_rate",
        "settling_time",
    ]

    for db_code in layer1_db_codes:
        bundle = bundle_map.get(db_code)
        if bundle is None:
            continue
        calc_code = _DB_TO_CALCULATOR_METRIC_CODE.get(db_code, db_code)
        try:
            calculator = get_calculator(calc_code)
            result = calculator.calculate(bundle)
            metric_results[calc_code] = result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Layer1 指标 %s 计算失败: %s", calc_code, exc)

    # ideal_settling_time 从 config_bundle 计算
    try:
        calculator = get_calculator("ideal_settling_time")
        result = calculator.calculate(config_bundle)
        metric_results["ideal_settling_time"] = result
    except Exception as exc:  # noqa: BLE001
        logger.warning("ideal_settling_time 计算失败: %s", exc)

    # --- Layer2: 2 个有依赖指标 ---
    for calc_code, dep_codes in _LAYER2_DEPENDENCIES.items():
        # 检查所有依赖是否已计算
        deps = {dep: metric_results[dep] for dep in dep_codes if dep in metric_results}
        if len(deps) != len(dep_codes):
            logger.info(
                "Layer2 指标 %s 缺少依赖，跳过: required=%s, present=%s",
                calc_code,
                dep_codes,
                list(deps),
            )
            continue

        # 获取该指标的 bundle（DB 列名）
        db_code = _CALCULATOR_TO_DB_METRIC_CODE.get(calc_code, calc_code)
        bundle = bundle_map.get(db_code)
        if bundle is None:
            continue

        try:
            calculator = get_calculator(calc_code)
            calculator = calculator.with_dependencies(deps)
            result = calculator.calculate(bundle)
            metric_results[calc_code] = result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Layer2 指标 %s 计算失败: %s", calc_code, exc)

    # --- Layer3: 综合评分 ---
    composite_result = ConfidenceEvaluator.compute_composite_score(metric_results, weights=weights)
    metric_results["composite_score"] = composite_result

    return metric_results, composite_result


def _extract_metrics_detail(
    metric_results: dict[str, MetricResult],
) -> dict[str, dict]:
    """提取 12 子指标的计算值与各自可信度（loop_confidence_latest.metrics JSONB）。

    键为 DB 列名（snake_case），形如::

        {"accuracy_rate": {"value": 93.35, "confidence": "A"}, ...}

    - 跳过 composite_score（综合评分单独存储于 score / confidence_level 列）
    - value 为 None 时保留 None（该指标 INCONCLUSIVE）
    """
    detail: dict[str, dict] = {}

    for calc_code, result in metric_results.items():
        if calc_code == "composite_score":
            continue
        db_code = _CALCULATOR_TO_DB_METRIC_CODE.get(calc_code, calc_code)
        detail[db_code] = {
            "value": float(result.value) if result.value is not None else None,
            "confidence": result.confidence_level,
        }

    return detail


def _extract_kpi_values(
    metric_results: dict[str, MetricResult],
) -> dict[str, Decimal | None]:
    """从 MetricResult 字典提取 KPI 值（Calculator 代码 → DB 列名）。

    - 跳过 composite_score（不写入指标列）
    - float 值转换为 Decimal（对齐 DB Numeric 类型）
    - None 值保持 None
    """
    kpi_values: dict[str, Decimal | None] = {}

    for calc_code, result in metric_results.items():
        if calc_code == "composite_score":
            continue
        db_code = _CALCULATOR_TO_DB_METRIC_CODE.get(calc_code, calc_code)
        if result.value is None:
            kpi_values[db_code] = None
        elif isinstance(result.value, Decimal):
            kpi_values[db_code] = result.value
        else:
            kpi_values[db_code] = Decimal(str(result.value))

    return kpi_values


def _extract_lineage_info(
    metric_results: dict[str, MetricResult],
    composite: MetricResult,
) -> dict:
    """提取数据血缘信息（优先 accuracy_rate lineage，其次 composite lineage）。

    Returns:
        dict 含: algorithm_version, sampling_freq, quality_policy,
        valid_rate, confidence_level, data_lineage
    """
    # 优先从 accuracy_rate 的 lineage 取
    accuracy_result = metric_results.get("accuracy_rate")
    lineage = (
        accuracy_result.lineage
        if accuracy_result and accuracy_result.lineage
        else (composite.lineage if composite and composite.lineage else None)
    )

    if lineage is not None:
        valid_rate = (
            Decimal(str(lineage.valid_rate)).quantize(Decimal("0.0001"))
            if lineage.valid_rate is not None
            else None
        )
        data_lineage_dict = {
            "sampling_freq": lineage.sampling_freq,
            "aggregation_policy": lineage.aggregation_policy,
            "quality_policy": lineage.quality_policy,
            "tag_group": lineage.tag_group,
            "data_block_ids": lineage.data_block_ids,
            "valid_rate": lineage.valid_rate,
            "data_policy_version": lineage.data_policy_version,
            "algorithm_version": lineage.algorithm_version,
        }
    else:
        valid_rate = None
        data_lineage_dict = {}

    confidence_level = (
        composite.confidence_level if composite and composite.confidence_level else "E"
    )

    return {
        "algorithm_version": (lineage.algorithm_version if lineage else ALGORITHM_VERSION),
        "sampling_freq": lineage.sampling_freq if lineage else None,
        "quality_policy": lineage.quality_policy if lineage else None,
        "valid_rate": valid_rate,
        "confidence_level": confidence_level,
        "data_lineage": data_lineage_dict,
    }


def _build_data_planner(db, bundle_cache=None):
    """构造 DataPlanner 实例（工厂函数）。

    从 get_provider().make_query_fn(db) 获取 TDengine 查询函数，
    配合 L1DataBlockCache + L2BundleCache + MetricDataBundleAssembler 构造 DataPlanner。

    Args:
        db: 异步数据库会话
        bundle_cache: 可选的 L2 Bundle 缓存实例。None 时自动创建（启用 L2 缓存）。
            传入 False 可显式禁用 L2 缓存（用于测试）。
    """
    from app.core.redis import redis_client
    from app.services.cache.l1_datablock import L1DataBlockCache
    from app.services.cache.l2_bundle import L2BundleCache
    from app.services.data_planner import DataPlanner
    from app.services.data_source.factory import get_provider
    from app.services.metric_data_bundle import MetricDataBundleAssembler

    query_fn = get_provider().make_query_fn(db)
    assembler = MetricDataBundleAssembler()

    # L2 Bundle 缓存（默认启用，传入 False 显式禁用 L1 + L2）
    if bundle_cache is False:
        l2_cache = None
        cache = None  # 同时禁用 L1 缓存（backfill 场景，避免无用 Redis I/O）
    else:
        cache = L1DataBlockCache(redis_client)
        if bundle_cache is not None:
            l2_cache = bundle_cache
        else:
            l2_cache = L2BundleCache(redis_client)

    return DataPlanner(
        cache=cache,
        tdengine_query_fn=query_fn,
        assembler=assembler,
        db=db,
        bundle_cache=l2_cache,
    )


# 共享 L2 Bundle 缓存实例（进程内单例，避免每回路创建）
_shared_bundle_cache = None


def _get_shared_bundle_cache():
    """获取共享 L2 Bundle 缓存实例（懒初始化）."""
    global _shared_bundle_cache
    if _shared_bundle_cache is None:
        from app.core.redis import redis_client
        from app.services.cache.l2_bundle import L2BundleCache

        _shared_bundle_cache = L2BundleCache(redis_client)
    return _shared_bundle_cache


async def _batch_load_loop_configs(db, loop_ids: list[str]) -> dict[str, dict]:
    """批量预加载回路配置（OP 限位 + PV 量程 + config_version）.

    一次性查询所有回路的配置信息，避免 DataPlanner 内部每回路查 3-5 次 DB。
    1000 回路：5000 次 DB 查询 → 3 次批量查询。

    Returns:
        {loop_id: {op_lower, op_upper, range_min, range_max, config_version, updated_at}}
    """
    if not loop_ids:
        return {}

    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.tag import TagRegistry

    configs: dict[str, dict] = {}

    # 1. 批量查询 LoopLedger（OP 限位 + updated_at）
    loop_result = await db.execute(
        select(
            LoopLedger.id,
            LoopLedger.op_output_lower_limit,
            LoopLedger.op_output_upper_limit,
            LoopLedger.updated_at,
        ).where(LoopLedger.id.in_(loop_ids))
    )
    for row in loop_result.all():
        loop_id = str(row[0])
        configs[loop_id] = {
            "op_lower": float(row[1]) if row[1] is not None else None,
            "op_upper": float(row[2]) if row[2] is not None else None,
            "range_min": 0.0,
            "range_max": 100.0,
            "config_version": f"cfg_{int(row[3].timestamp())}" if row[3] else "v1",
        }

    # 2. 批量查询 LoopTagMapping（所有回路的 tag 映射）
    mapping_result = await db.execute(
        select(LoopTagMapping.loop_id, LoopTagMapping.tag_role, LoopTagMapping.tag_id).where(
            LoopTagMapping.loop_id.in_(loop_ids)
        )
    )
    # {loop_id: {role: tag_id}}
    loop_tags: dict[str, dict[str, str]] = {}
    for row in mapping_result.all():
        loop_id = str(row[0])
        if loop_id not in loop_tags:
            loop_tags[loop_id] = {}
        loop_tags[loop_id][row[1]] = str(row[2])

    # 3. 批量查询 PV tag 量程（所有 PV tag_id）
    pv_tag_ids = [
        tag_id for tags in loop_tags.values() for role, tag_id in tags.items() if role == "PV"
    ]
    tag_ranges: dict[str, tuple[float, float]] = {}
    if pv_tag_ids:
        tag_result = await db.execute(
            select(TagRegistry.id, TagRegistry.range_min, TagRegistry.range_max).where(
                TagRegistry.id.in_(pv_tag_ids)
            )
        )
        for row in tag_result.all():
            tag_ranges[str(row[0])] = (
                float(row[1]) if row[1] is not None else 0.0,
                float(row[2]) if row[2] is not None else 100.0,
            )

    # 4. 合并 PV 量程到 configs
    for loop_id, tags in loop_tags.items():
        pv_tag_id = tags.get("PV")
        if pv_tag_id and pv_tag_id in tag_ranges and loop_id in configs:
            configs[loop_id]["range_min"] = tag_ranges[pv_tag_id][0]
            configs[loop_id]["range_max"] = tag_ranges[pv_tag_id][1]

    # 5. OP tag 量程作为回退（Loop 表 OP 限位为 None 时）
    op_tag_ids = [
        tag_id for tags in loop_tags.values() for role, tag_id in tags.items() if role == "OP"
    ]
    op_tag_ranges: dict[str, tuple[float, float]] = {}
    if op_tag_ids:
        op_result = await db.execute(
            select(TagRegistry.id, TagRegistry.range_min, TagRegistry.range_max).where(
                TagRegistry.id.in_(op_tag_ids)
            )
        )
        for row in op_result.all():
            op_tag_ranges[str(row[0])] = (
                float(row[1]) if row[1] is not None else None,
                float(row[2]) if row[2] is not None else None,
            )
    for loop_id, tags in loop_tags.items():
        if loop_id not in configs:
            continue
        cfg = configs[loop_id]
        if cfg["op_lower"] is None or cfg["op_upper"] is None:
            op_tag_id = tags.get("OP")
            if op_tag_id and op_tag_id in op_tag_ranges:
                op_min, op_max = op_tag_ranges[op_tag_id]
                if cfg["op_lower"] is None and op_min is not None:
                    cfg["op_lower"] = op_min
                if cfg["op_upper"] is None and op_max is not None:
                    cfg["op_upper"] = op_max

    logger.info(
        "批量预加载回路配置: %d 回路, %d PV tags, %d OP tags",
        len(configs),
        len(tag_ranges),
        len(op_tag_ranges),
    )
    return configs


def _make_config_loader(loop_cfg: dict | None):
    """构造 config_loader 闭包（使用预加载的配置，避免 DB 查询）.

    返回一个 async 函数，签名对齐 DataPlanner._default_config_loader：
        async def loader(loop_id, control_type) -> LoopPreprocessConfig
    """
    from app.contracts.data_types import LoopPreprocessConfig

    async def _loader(loop_id: str, control_type: ControlType) -> LoopPreprocessConfig:
        if loop_cfg is None:
            return LoopPreprocessConfig(
                loop_id=loop_id,
                control_type=control_type,
                range_min=0.0,
                range_max=100.0,
                config_version="v1",
            )
        return LoopPreprocessConfig(
            loop_id=loop_id,
            control_type=control_type,
            range_min=loop_cfg.get("range_min", 0.0),
            range_max=loop_cfg.get("range_max", 100.0),
            config_version=loop_cfg.get("config_version", "v1"),
        )

    return _loader


def _get_tag_name(
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
    role: str,
) -> str | None:
    """获取指定角色的 tag_name。"""
    mapping = mappings.get(role)
    if not mapping:
        return None
    tag = tags_map.get(str(mapping.tag_id))
    if not tag:
        return None
    return tag.tag_name


def _ts_to_float(ts: Any) -> float | None:
    """将时间戳转换为浮点数（秒级 epoch）。

    支持 int/float/datetime/ISO 字符串；无法转换时返回 None。
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        return float(ts.timestamp())
    # 字符串：先尝试数值，再尝试 ISO 解析
    s = str(ts)
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return float(dt.timestamp())
    except (ValueError, TypeError):
        return None


# 时间戳容差（秒）：±500ms 内视为同一时间点
_TS_TOLERANCE_SEC = 0.5


def _build_ts_index(data: list[dict]) -> tuple[list[float], list[Any]]:
    """构建数值时间戳索引（用于 bisect 最近邻查找）。

    Returns:
        (sorted_ts_floats, sorted_original_ts) — 同序排列；
        若任意 ts 无法转数值，返回空列表。
    """
    pairs: list[tuple[float, Any]] = []
    for d in data:
        ts_orig = d.get("ts")
        ts_f = _ts_to_float(ts_orig)
        if ts_f is None:
            return [], []  # 退化为精确匹配模式
        pairs.append((ts_f, ts_orig))
    pairs.sort(key=lambda p: p[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def _find_nearest_value(
    target_ts: Any,
    sorted_ts_floats: list[float],
    exact_map: dict[Any, Any],
    sorted_values: list[Any] | None = None,
) -> Any:
    """查找目标时间戳对应的值：先精确匹配，再容差最近邻匹配。

    Args:
        target_ts: 目标时间戳（任意类型）
        sorted_ts_floats: 已排序的数值时间戳列表
        exact_map: 原始 ts → value 的精确映射
        sorted_values: 与 sorted_ts_floats 同序的值列表（容差匹配用）
    """
    # 1. 精确匹配（兼容字符串 ts 如 "t1"）
    if target_ts in exact_map:
        return exact_map[target_ts]
    # 2. 数值容差匹配
    target_f = _ts_to_float(target_ts)
    if target_f is None or not sorted_ts_floats or sorted_values is None:
        return None
    idx = bisect_left(sorted_ts_floats, target_f)
    best_idx = -1
    best_diff = float("inf")
    # 检查 idx 和 idx-1 两个候选（bisect_left 返回插入点）
    for cand in (idx - 1, idx):
        if 0 <= cand < len(sorted_ts_floats):
            diff = abs(sorted_ts_floats[cand] - target_f)
            if diff < best_diff:
                best_diff = diff
                best_idx = cand
    if best_idx >= 0 and best_diff <= _TS_TOLERANCE_SEC:
        return sorted_values[best_idx]
    return None


def _quantize(value: Decimal) -> Decimal:
    """量化到 2 位小数。"""
    return value.quantize(Decimal("0.01"))


async def _save_snapshot(
    db,
    loop_id: str,
    ts_start: datetime,
    ts_end: datetime,
    status: str,
    score: Decimal | None = None,
    good_value_rate: Decimal | None = None,
    auto_mode_rate: Decimal | None = None,
    effective_auto_rate: Decimal | None = None,
    steady_rate: Decimal | None = None,
    accuracy_rate: Decimal | None = None,
    fast_rate: Decimal | None = None,
    oscillation_rate: Decimal | None = None,
    saturation_rate: Decimal | None = None,
    stiction_index: Decimal | None = None,
    settling_time: Decimal | None = None,
    output_trip_index: Decimal | None = None,
    ideal_settling_time: Decimal | None = None,
    algorithm_version: str | None = None,
    sampling_freq: str | None = None,
    quality_policy: str | None = None,
    valid_rate: Decimal | None = None,
    confidence_level: str | None = None,
    data_lineage: dict | None = None,
) -> dict:
    """幂等写入快照（UPSERT 模式：相同 loop_id + ts_start 覆盖更新）.

    v4.0 使用 PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE``，
    不再通过 select-then-add 模式，减少一次查询并避免并发竞争。
    7 个数据血缘字段（ideal_settling_time/algorithm_version/sampling_freq/
    quality_policy/valid_rate/confidence_level/data_lineage）随 UPSERT 写入。
    实际写入行的 id 通过 ``RETURNING id`` 随 UPSERT 一并取回（新增与
    UPDATE 分支均返回），不再单独 SELECT 回查。
    """
    snapshot_id = str(uuid4())

    insert_values = {
        "id": snapshot_id,
        "loop_id": loop_id,
        "ts_start": ts_start,
        "ts_end": ts_end,
        "status": status,
        "score": score,
        "good_value_rate": good_value_rate,
        "auto_mode_rate": auto_mode_rate,
        "effective_auto_rate": effective_auto_rate,
        "steady_rate": steady_rate,
        "accuracy_rate": accuracy_rate,
        "fast_rate": fast_rate,
        "oscillation_rate": oscillation_rate,
        "saturation_rate": saturation_rate,
        "stiction_index": stiction_index,
        "settling_time": settling_time,
        "output_trip_index": output_trip_index,
        "ideal_settling_time": ideal_settling_time,
        "algorithm_version": algorithm_version,
        "sampling_freq": sampling_freq,
        "quality_policy": quality_policy,
        "valid_rate": valid_rate,
        "confidence_level": confidence_level,
        "data_lineage": data_lineage,
    }

    update_cols = {k: v for k, v in insert_values.items() if k not in ("id", "loop_id", "ts_start")}

    stmt = (
        pg_insert(KpiSnapshotHourly)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["loop_id", "ts_start"],
            set_=update_cols,
        )
        .returning(KpiSnapshotHourly.id)
    )
    # RETURNING 直接取回实际写入行的 id（新增与 UPDATE 分支均返回），
    # 省去 UPSERT 后再 SELECT 回查 id 的一次额外查询
    id_result = await db.execute(stmt)
    id_row = id_result.first()
    actual_id = str(id_row[0]) if id_row else snapshot_id

    return {
        "loopId": loop_id,
        "snapshotId": actual_id,
        "tsStart": ts_start.isoformat(),
        "tsEnd": ts_end.isoformat(),
        "status": status,
        "score": float(score) if score is not None else None,
        "algorithmVersion": algorithm_version or ALGORITHM_VERSION,
    }


async def _save_confidence_latest(
    db,
    loop_id: str,
    ts_start: datetime,
    ts_end: datetime,
    status: str,
    score: Decimal | None = None,
    confidence_level: str | None = None,
    valid_rate: Decimal | float | None = None,
    metrics: dict | None = None,
    algorithm_version: str | None = None,
) -> None:
    """UPSERT 写入 loop_confidence_latest（按 loop_id 冲突覆盖全部字段）。

    每回路仅保留"最新一次评估"记录：评估时间取写入时刻（naive UTC），
    数据源时间区间取快照窗口。metrics 为 12 子指标 JSONB
    （``_extract_metrics_detail`` 产物），无子指标数据时存空对象。
    """
    now = datetime.now(UTC).replace(tzinfo=None)

    insert_values = {
        "id": str(uuid4()),
        "loop_id": loop_id,
        "eval_time": now,
        "data_ts_start": ts_start,
        "data_ts_end": ts_end,
        "status": status,
        "score": score,
        "confidence_level": confidence_level,
        "valid_rate": float(valid_rate) if valid_rate is not None else None,
        "metrics": metrics if metrics is not None else {},
        "algorithm_version": algorithm_version,
        "updated_at": now,
    }

    update_cols = {k: v for k, v in insert_values.items() if k not in ("id", "loop_id")}

    stmt = (
        pg_insert(LoopConfidenceLatest)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["loop_id"],
            set_=update_cols,
        )
    )
    await db.execute(stmt)


async def _save_custom_snapshot(
    db,
    task_id: str,
    loop_id: str,
    ts_start: datetime,
    ts_end: datetime,
    status: str,
    score: Decimal | None = None,
    good_value_rate: Decimal | None = None,
    auto_mode_rate: Decimal | None = None,
    effective_auto_rate: Decimal | None = None,
    steady_rate: Decimal | None = None,
    accuracy_rate: Decimal | None = None,
    fast_rate: Decimal | None = None,
    oscillation_rate: Decimal | None = None,
    saturation_rate: Decimal | None = None,
    stiction_index: Decimal | None = None,
    settling_time: Decimal | None = None,
    output_trip_index: Decimal | None = None,
    ideal_settling_time: Decimal | None = None,
    algorithm_version: str | None = None,
    sampling_freq: str | None = None,
    quality_policy: str | None = None,
    valid_rate: Decimal | None = None,
    confidence_level: str | None = None,
    data_lineage: dict | None = None,
) -> dict:
    """幂等写入自定义任务快照（select-then-add 模式）.

    自定义任务快照使用 ``(task_id, loop_id)`` 作为唯一键，
    通过 select-then-add/update 模式写入（与 hourly 表的 UPSERT 不同）。
    """
    existing_result = await db.execute(
        select(KpiSnapshotCustom).where(
            KpiSnapshotCustom.task_id == task_id,
            KpiSnapshotCustom.loop_id == loop_id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        existing.ts_end = ts_end
        existing.status = status
        existing.score = score
        existing.good_value_rate = good_value_rate
        existing.auto_mode_rate = auto_mode_rate
        existing.effective_auto_rate = effective_auto_rate
        existing.steady_rate = steady_rate
        existing.accuracy_rate = accuracy_rate
        existing.fast_rate = fast_rate
        existing.oscillation_rate = oscillation_rate
        existing.saturation_rate = saturation_rate
        existing.stiction_index = stiction_index
        existing.settling_time = settling_time
        existing.output_trip_index = output_trip_index
        existing.ideal_settling_time = ideal_settling_time
        existing.algorithm_version = algorithm_version
        existing.sampling_freq = sampling_freq
        existing.quality_policy = quality_policy
        existing.valid_rate = valid_rate
        existing.confidence_level = confidence_level
        existing.data_lineage = data_lineage
        snapshot_id = str(existing.id)
    else:
        snapshot_id = str(uuid4())
        snapshot = KpiSnapshotCustom(
            id=snapshot_id,
            task_id=task_id,
            loop_id=loop_id,
            ts_start=ts_start,
            ts_end=ts_end,
            status=status,
            score=score,
            good_value_rate=good_value_rate,
            auto_mode_rate=auto_mode_rate,
            effective_auto_rate=effective_auto_rate,
            steady_rate=steady_rate,
            accuracy_rate=accuracy_rate,
            fast_rate=fast_rate,
            oscillation_rate=oscillation_rate,
            saturation_rate=saturation_rate,
            stiction_index=stiction_index,
            settling_time=settling_time,
            output_trip_index=output_trip_index,
            ideal_settling_time=ideal_settling_time,
            algorithm_version=algorithm_version,
            sampling_freq=sampling_freq,
            quality_policy=quality_policy,
            valid_rate=valid_rate,
            confidence_level=confidence_level,
            data_lineage=data_lineage,
        )
        db.add(snapshot)

    return {
        "taskId": task_id,
        "loopId": loop_id,
        "snapshotId": snapshot_id,
        "tsStart": ts_start.isoformat(),
        "tsEnd": ts_end.isoformat(),
        "status": status,
        "score": float(score) if score is not None else None,
        "algorithmVersion": algorithm_version or ALGORITHM_VERSION,
    }


async def _persist_snapshot(
    db,
    loop_id: str,
    ts_start: datetime,
    ts_end: datetime,
    status: str,
    custom_task_id: str | None = None,
    **kwargs,
) -> dict:
    """统一快照持久化入口（根据 custom_task_id 分发到对应表）.

    - ``custom_task_id=None`` → 写入 ``kpi_snapshot_hourly``（标准小时快照），
      并同步 UPSERT ``loop_confidence_latest``（每回路最新一条可信度记录）
    - ``custom_task_id`` 非 None → 写入 ``kpi_snapshot_custom``（自定义任务快照，
      不更新 loop_confidence_latest）

    ``metrics_detail`` kwarg（12 子指标值+可信度，``_extract_metrics_detail``
    产物）仅用于 loop_confidence_latest，不透传给 _save_* 函数；其余 kwargs
    透传（KPI 值 + 7 个数据血缘字段）。
    loop_confidence_latest 写入失败仅记日志，不影响主快照结果。

    P0 #3：INCONCLUSIVE 快照的 ``confidence_level`` 缺省落 'E'
    （对齐 §7.15 E↔INCONCLUSIVE 语义，使"非空最差等级"聚合能覆盖
    INCONCLUSIVE 行）；调用方已传血缘等级（如 composite 评估产物）时沿用，
    不覆盖。
    """
    metrics_detail = kwargs.pop("metrics_detail", None)
    if status == "INCONCLUSIVE" and kwargs.get("confidence_level") is None:
        kwargs["confidence_level"] = "E"
    if custom_task_id is not None:
        return await _save_custom_snapshot(
            db=db,
            task_id=custom_task_id,
            loop_id=loop_id,
            ts_start=ts_start,
            ts_end=ts_end,
            status=status,
            **kwargs,
        )
    result = await _save_snapshot(
        db=db,
        loop_id=loop_id,
        ts_start=ts_start,
        ts_end=ts_end,
        status=status,
        **kwargs,
    )
    try:
        await _save_confidence_latest(
            db=db,
            loop_id=loop_id,
            ts_start=ts_start,
            ts_end=ts_end,
            status=status,
            score=kwargs.get("score"),
            confidence_level=kwargs.get("confidence_level"),
            valid_rate=kwargs.get("valid_rate"),
            metrics=metrics_detail,
            algorithm_version=kwargs.get("algorithm_version"),
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "loop_confidence_latest 写入失败（回路 %s），主快照不受影响",
            loop_id,
            exc_info=True,
        )
    return result


__all__ = [
    "ALGORITHM_VERSION",
    "AsyncTask",
    "backfill_kpi_range",
    "calculate_custom_loop_kpi",
    "calculate_daily_kpi",
    "calculate_hourly_kpi",
    "calculate_loop_kpi",
    "calculate_monthly_kpi",
    "calculate_node_kpi",
    "calculate_node_kpi_hourly",
]


# ---------------------------------------------------------------------------
# 节点级性能评估任务（GB/T 44693.2-2024 §6.4 综合评估）
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_node_kpi_hourly",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_node_kpi_hourly(self: AsyncTask) -> dict:
    """每小时节点级聚合任务（在回路级 KPI 计算完成后级联触发）。

    遍历所有 is_kpi_enabled=True 的 PlantNode 节点，
    递归收集下属回路，按 score_weight 加权聚合回路级快照，
    写入 kpi_node_snapshot_hourly。
    """
    logger.info("节点级 KPI 聚合任务开始, task_id=%s", self.request.id)
    try:
        result = self.run_async(_do_calculate_node_kpi())
        logger.info("节点级 KPI 聚合任务完成: %s", result)
        return result
    except Exception:
        logger.exception("节点级 KPI 聚合任务失败")
        raise


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_node_kpi",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_node_kpi(
    plant_node_id: str, ts_start: str | None = None, ts_end: str | None = None
) -> dict:
    """单节点 KPI 聚合（可手动触发，支持指定时间段）。

    Args:
        plant_node_id: 工厂节点 ID
        ts_start: 起始时间（ISO 8601），None 表示上一个完整小时
        ts_end: 结束时间（ISO 8601），None 表示 ts_start + 1 小时
    """
    logger.info(
        "单节点 KPI 聚合, plant_node_id=%s, ts_start=%s, ts_end=%s", plant_node_id, ts_start, ts_end
    )
    return AsyncTask().run_async(_do_calculate_single_node(plant_node_id, ts_start, ts_end))


async def _do_calculate_node_kpi() -> dict:
    """执行节点级 KPI 聚合的实际 async 逻辑。

    Phase 4 优化：使用 batch_calculate_and_save_node_snapshots 替代逐节点串行处理。
    批量预加载树遍历/实时自控率/回路计数 + 并发聚合，将 ~9N 次 DB 查询降至
    ~6 次批量查询 + 3N 次单节点查询。
    """
    from app.core.db import AsyncSessionLocal
    from app.models.plant_node import PlantNode
    from app.services.node_performance import batch_calculate_and_save_node_snapshots

    # 时间窗：上一个完整小时（与回路级一致）— naive UTC
    now = datetime.now(UTC).replace(tzinfo=None)
    ts_end = now.replace(minute=0, second=0, microsecond=0)
    ts_start = ts_end - timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        # 查询所有启用 KPI 评估的节点
        node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
        nodes = list(node_result.scalars().all())

    if not nodes:
        logger.info("无启用 KPI 评估的节点，跳过节点级聚合")
        return {"total": 0, "success": 0, "skipped": 0}

    logger.info("待聚合节点数: %d（批量模式）", len(nodes))

    # 批量预加载 + 并发聚合 + 保存
    result = await batch_calculate_and_save_node_snapshots(
        nodes=nodes,
        ts_start=ts_start,
        ts_end=ts_end,
        concurrency=10,
    )

    logger.info(
        "节点级聚合完成: total=%d, success=%d, skipped=%d, failed=%d",
        result["total"],
        result["success"],
        result["skipped"],
        result["failed"],
    )
    return result


async def _do_calculate_single_node(
    plant_node_id: str,
    ts_start: str | None = None,
    ts_end: str | None = None,
) -> dict:
    """单节点 KPI 聚合（支持指定时间段）。"""
    from app.core.db import AsyncSessionLocal
    from app.services.node_performance import calculate_and_save_node_snapshot

    now = datetime.now(UTC).replace(tzinfo=None)
    if ts_start:
        try:
            ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            ts_start_dt = datetime.fromisoformat(ts_start).replace(tzinfo=None)
    else:
        ts_start_dt = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    if ts_end:
        try:
            ts_end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            ts_end_dt = datetime.fromisoformat(ts_end).replace(tzinfo=None)
    else:
        ts_end_dt = ts_start_dt + timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        snap = await calculate_and_save_node_snapshot(
            db=db,
            plant_node_id=plant_node_id,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
        )
        await db.commit()

    if snap is None:
        return {"plantNodeId": plant_node_id, "status": "SKIPPED", "reason": "无下属回路数据"}
    return {"plantNodeId": plant_node_id, "status": "SUCCESS", "snapshot": snap}


# ---------------------------------------------------------------------------
# 回填任务（backfill_kpi_range）— 按小时窗口批量重算历史 KPI 快照
# ---------------------------------------------------------------------------


# 回填任务使用独立的较低内层并发，避免 Celery 外层并发与回路并发相乘。
# BATCH_SIZE=1：每个小时窗口一个子任务（24 窗口 → 24 子任务），配合 worker
# concurrency=8 分 3 波跑完，进程级 fan-out 拉满；窗口内回路并发仍为 4，
# 受 PG 连接预算约束（见 CONCURRENCY 注释），不能随窗口拆分无界放大。
_BACKFILL_BATCH_SIZE = 1
_BACKFILL_LOOP_CONCURRENCY = 4


@celery_app.task(
    name="app.tasks.kpi_calc._backfill_window_batch",
    bind=True,
    base=AsyncTask,
    max_retries=None,
)
def _backfill_window_batch(
    self: AsyncTask,
    window_isos: list[str],
    loop_ids: list[str] | None = None,
    task_id: str | None = None,
    window_offset: int = 0,
    total_windows: int = 0,
) -> dict:
    """子任务：处理一批小时窗口（在独立 worker 进程中运行，利用多核 CPU）。

    Args:
        window_isos: 窗口起始时间列表（ISO 8601 字符串）
        loop_ids: 回路 ID 过滤列表
    """
    _sub_t0 = time.monotonic()
    batch_id = self.request.id if isinstance(self.request.id, str) else None
    execution_token = str(uuid4())
    has_batch_claim = bool(task_id and batch_id)
    if has_batch_claim:
        from app.services import task_tracker

        assert task_id is not None and batch_id is not None
        claim_state, cached_result = self.run_async(
            task_tracker.claim_backfill_batch(
                task_id,
                batch_id,
                execution_token=execution_token,
            )
        )
        if claim_state == "DONE":
            return cached_result or {}
        if claim_state == "BUSY":
            raise self.retry(countdown=10)
    logger.info(
        "[子任务] 启动: windows=%d, first=%s, loop_ids=%s",
        len(window_isos),
        window_isos[0] if window_isos else "(empty)",
        "all" if loop_ids is None else f"{len(loop_ids)} loops",
    )

    async def _run() -> dict:
        import time as _time

        from app.core.db import AsyncSessionLocal

        windows = [
            datetime.fromisoformat(w.replace("Z", "+00:00")).replace(tzinfo=None)
            for w in window_isos
        ]

        # 预加载（子任务内 1 次）
        _preload_t0 = _time.monotonic()
        async with AsyncSessionLocal() as db:
            stmt = select(LoopLedger).where(
                LoopLedger.is_active.is_(True),
                LoopLedger.status == "READY",
            )
            if loop_ids is not None:
                stmt = stmt.where(LoopLedger.id.in_(loop_ids))
            result = await db.execute(stmt)
            loops = list(result.scalars().all())

            if not loops:
                logger.warning("[子任务] 无可用回路，提前返回")
                return {"success": 0, "inconclusive": 0, "failed": 0, "failed_windows": []}

            metric_result = await db.execute(select(MetricConfig))
            metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

            from app.services.loop_config import get_loop_type_weights_map

            type_weights = await get_loop_type_weights_map(db)
            loop_configs = await _batch_load_loop_configs(db, [str(lp.id) for lp in loops])
        logger.info(
            "[子任务] 预加载完成: loops=%d, metrics=%d, configs=%d, 耗时=%.2fs",
            len(loops),
            len(metric_configs),
            len(loop_configs),
            _time.monotonic() - _preload_t0,
        )

        agg = {
            "success": 0,
            "inconclusive": 0,
            "failed": 0,
            "node_success": 0,
            "failed_windows": [],
        }

        for w_idx, w in enumerate(windows, 1):
            if task_id and await _is_task_cancelled(task_id):
                agg["cancelled"] = True
                break
            w_start = w
            w_end = w + timedelta(hours=1)
            _w_t0 = _time.monotonic()
            try:

                async def _on_completed(
                    loop_id: str,
                    current_window_index: int = window_offset + w_idx,
                    window_start: datetime = w_start,
                ) -> None:
                    if task_id:
                        await _increment_backfill_progress(
                            task_id,
                            current_window_index,
                            total_windows,
                            len(loops),
                            event_id=f"{window_start.isoformat()}:{loop_id}",
                        )

                results = await _run_batch_loop_calculations(
                    loops=loops,
                    loop_configs=loop_configs,
                    metric_configs=metric_configs,
                    ts_start=w_start,
                    ts_end=w_end,
                    type_weights=type_weights,
                    bundle_cache=False,
                    concurrency=_BACKFILL_LOOP_CONCURRENCY,
                    on_completed=_on_completed if task_id else None,
                )
                summary = _summarize_batch_results(results)
                agg["success"] += summary["success"]
                agg["inconclusive"] += summary["inconclusive"]
                agg["failed"] += summary["failed"]
                logger.info(
                    "[子任务] 窗口 %d/%d %s: ok=%d, inconclusive=%d, failed=%d, 耗时=%.2fs",
                    w_idx,
                    len(windows),
                    w.isoformat(),
                    summary["success"],
                    summary["inconclusive"],
                    summary["failed"],
                    _time.monotonic() - _w_t0,
                )
            except Exception as exc:  # noqa: BLE001
                agg["failed_windows"].append(w.isoformat())
                agg["failed"] += len(loops)
                logger.exception(
                    "[子任务] 窗口 %d/%d %s 失败: %s",
                    w_idx,
                    len(windows),
                    w.isoformat(),
                    exc,
                )

        # 节点聚合按窗口批次在 chord header 中并行，避免 30 天聚合
        # 集中到单个 callback 并受全局时限约束。
        if windows and not agg.get("cancelled") and not agg["failed_windows"] and not agg["failed"]:
            agg["node_success"] = await _do_backfill_node_aggregation(
                windows[0], windows[-1] + timedelta(hours=1)
            )

        logger.info(
            "[子任务] 全部完成: windows=%d, ok=%d, inconclusive=%d, failed=%d, 总耗时=%.2fs",
            len(windows),
            agg["success"],
            agg["inconclusive"],
            agg["failed"],
            _time.monotonic() - _sub_t0,
        )
        return agg

    try:
        result = self.run_async(_run())
        if has_batch_claim:
            from app.services import task_tracker

            assert task_id is not None and batch_id is not None
            self.run_async(
                task_tracker.complete_backfill_batch(
                    task_id,
                    batch_id,
                    execution_token=execution_token,
                    result=result,
                )
            )
        return result
    except Exception:
        if has_batch_claim:
            from app.services import task_tracker

            assert task_id is not None and batch_id is not None
            self.run_async(
                task_tracker.release_backfill_batch(
                    task_id,
                    batch_id,
                    execution_token=execution_token,
                )
            )
        raise


@celery_app.task(
    name="app.tasks.kpi_calc.backfill_kpi_range",
    bind=True,
    base=AsyncTask,
)
def backfill_kpi_range(
    self: AsyncTask,
    ts_start: str,
    ts_end: str,
    loop_ids: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    """按小时窗口批量回填 KPI 快照（脚本/HTTP 触发）。

    遍历 [ts_start, ts_end) 范围内的每个完整小时窗口，
    对全量 ACTIVE/READY 回路计算 KPI，并同步触发节点级聚合。
    幂等：相同 (loop_id, ts_start) 的快照会被 UPSERT 覆盖，可重复执行。

    Args:
        ts_start: 起始时间（ISO 8601，UTC）
        ts_end: 结束时间（ISO 8601，UTC，不包含）
        loop_ids: 回路 ID 过滤列表。None=全量；非空列表=仅这些回路；空列表=直接返回。
        task_id: Redis 任务跟踪 ID（HTTP API 触发时传入）。
    """
    logger.info(
        "KPI 回填任务开始, celery_id=%s, task_id=%s, range=%s~%s, loop_ids=%s",
        self.request.id,
        task_id or "(none)",
        ts_start,
        ts_end,
        "all" if loop_ids is None else f"{len(loop_ids)} loops",
    )

    try:
        if task_id:
            try:
                self.run_async(_update_task_running(task_id))
            except Exception:
                logger.warning("更新任务 RUNNING 状态失败: task_id=%s", task_id, exc_info=True)

        windows = _build_backfill_windows(ts_start, ts_end)
        if loop_ids is not None and not loop_ids:
            result = _empty_backfill_result(len(windows))
            if task_id:
                self.run_async(_update_task_success(task_id, result))
            return result

        child_task_ids, callback_task_id = _backfill_canvas_ids(task_id, len(windows))
        reservation_token = str(uuid4())
        dispatch_action = "CLAIMED"
        if task_id:
            from app.services import task_tracker

            dispatch_action, child_task_ids, callback_task_id = self.run_async(
                task_tracker.reserve_backfill_dispatch(
                    task_id,
                    reservation_token=reservation_token,
                    child_task_ids=child_task_ids,
                    callback_task_id=callback_task_id,
                )
            )
        dispatch = {
            "total_windows": len(windows),
            "child_task_ids": child_task_ids,
            "callback_task_id": callback_task_id,
        }
        if dispatch_action == "EXISTING":
            return {"status": "DISPATCHED", **dispatch}

        try:
            dispatch = _dispatch_backfill_chord(
                ts_start,
                ts_end,
                loop_ids=loop_ids,
                task_id=task_id,
                child_task_ids=child_task_ids,
                callback_task_id=callback_task_id,
            )
        except Exception:
            if task_id:
                self.run_async(task_tracker.release_backfill_dispatch(task_id, reservation_token))
            raise
        if task_id:
            self.run_async(task_tracker.complete_backfill_dispatch(task_id, reservation_token))

        return {"status": "DISPATCHED", **dispatch}
    except Exception as exc:
        logger.exception("KPI 回填任务派发失败")

        if task_id:
            try:
                self.run_async(_update_task_failed(task_id, str(exc)))
            except Exception:
                logger.warning("更新任务 FAILED 状态失败: task_id=%s", task_id, exc_info=True)
        raise


def _build_backfill_windows(ts_start: str, ts_end: str) -> list[datetime]:
    """Build aligned hourly windows for a backfill range."""
    start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00")).replace(tzinfo=None)
    end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00")).replace(tzinfo=None)
    windows: list[datetime] = []
    current = start_dt.replace(minute=0, second=0, microsecond=0)
    while current < end_dt:
        windows.append(current)
        current += timedelta(hours=1)
    return windows


def _empty_backfill_result(total_windows: int) -> dict:
    return {
        "total_windows": total_windows,
        "failed_windows": 0,
        "loop_success": 0,
        "loop_inconclusive": 0,
        "loop_failed": 0,
        "node_success": 0,
        "failed_window_list": [],
    }


def _backfill_canvas_ids(task_id: str | None, total_windows: int) -> tuple[list[str], str]:
    """Build stable canvas IDs so a reserved-but-unmarked publish can be retried safely."""
    batch_count = (total_windows + _BACKFILL_BATCH_SIZE - 1) // _BACKFILL_BATCH_SIZE
    if task_id is None:
        return [str(uuid4()) for _ in range(batch_count)], str(uuid4())
    children = [
        str(uuid5(NAMESPACE_URL, f"clpm:backfill:{task_id}:batch:{index}"))
        for index in range(batch_count)
    ]
    callback = str(uuid5(NAMESPACE_URL, f"clpm:backfill:{task_id}:callback"))
    return children, callback


def _dispatch_backfill_chord(
    ts_start: str,
    ts_end: str,
    *,
    loop_ids: list[str] | None,
    task_id: str | None,
    child_task_ids: list[str] | None = None,
    callback_task_id: str | None = None,
) -> dict:
    """Dispatch child batches and return immediately without joining results."""
    windows = _build_backfill_windows(ts_start, ts_end)
    window_isos = [window.isoformat() for window in windows]
    batches = [
        window_isos[index : index + _BACKFILL_BATCH_SIZE]
        for index in range(0, len(window_isos), _BACKFILL_BATCH_SIZE)
    ]
    generated_child_ids, generated_callback_id = _backfill_canvas_ids(task_id, len(windows))
    child_task_ids = child_task_ids or generated_child_ids
    callback_task_id = callback_task_id or generated_callback_id
    if len(child_task_ids) != len(batches):
        raise ValueError("回填子任务 ID 数量与窗口分片数不一致")

    header = []
    for batch_index, (batch, child_id) in enumerate(zip(batches, child_task_ids, strict=True)):
        child = _backfill_window_batch.s(
            batch,
            loop_ids=loop_ids,
            task_id=task_id,
            window_offset=batch_index * _BACKFILL_BATCH_SIZE,
            total_windows=len(windows),
        ).set(task_id=child_id)
        child.link_error(_backfill_chord_error.s(task_id=task_id))
        header.append(child)
    callback = _finalize_backfill.s(
        ts_start=ts_start,
        ts_end=ts_end,
        total_windows=len(windows),
        task_id=task_id,
    ).set(task_id=callback_task_id)
    callback.link_error(_backfill_chord_error.s(task_id=task_id))
    chord(header, callback).apply_async()

    return {
        "total_windows": len(windows),
        "child_task_ids": child_task_ids,
        "callback_task_id": callback_task_id,
    }


async def _update_task_running(task_id: str) -> None:
    """将任务状态从 PENDING 更新为 RUNNING。"""
    from app.schemas.task import TaskStatus
    from app.services import task_tracker

    await task_tracker.update_status(
        task_id,
        TaskStatus.RUNNING,
        current_stage="回填计算",
        started_at=task_tracker._now_iso(),
    )


async def _update_task_success(task_id: str, result: dict) -> None:
    """将任务状态更新为 SUCCESS。"""
    from app.schemas.task import TaskStatus
    from app.services import task_tracker

    if await _is_task_cancelled(task_id):
        return
    await task_tracker.update_status(
        task_id,
        TaskStatus.SUCCESS,
        progress=1.0,
        current_stage="完成",
        finished_at=task_tracker._now_iso(),
    )


async def _update_task_failed(task_id: str, error_message: str) -> None:
    """将任务状态更新为 FAILED。"""
    from app.schemas.task import TaskStatus
    from app.services import task_tracker

    if await _is_task_cancelled(task_id):
        return
    await task_tracker.update_status(
        task_id,
        TaskStatus.FAILED,
        current_stage="失败",
        error_message=error_message,
        finished_at=task_tracker._now_iso(),
    )


@celery_app.task(
    name="app.tasks.kpi_calc._finalize_backfill",
    bind=True,
    base=AsyncTask,
)
def _finalize_backfill(
    self: AsyncTask,
    batch_results: list[dict],
    ts_start: str,
    ts_end: str,
    total_windows: int,
    task_id: str | None = None,
) -> dict:
    """Chord callback: aggregate child results and set the business terminal state."""
    try:
        return self.run_async(
            _do_finalize_backfill(
                batch_results,
                ts_start,
                ts_end,
                total_windows=total_windows,
                task_id=task_id,
            )
        )
    except Exception as exc:
        if task_id:
            self.run_async(_update_task_failed(task_id, str(exc)))
        raise


@celery_app.task(
    name="app.tasks.kpi_calc._backfill_chord_error",
    bind=True,
    base=AsyncTask,
)
def _backfill_chord_error(
    self: AsyncTask,
    *args,
    task_id: str | None = None,
    **kwargs,
) -> dict:
    """Chord errback: make header/callback failures visible in task tracking."""
    error = str(args[-1]) if args else "KPI 回填子任务失败"
    if task_id:
        self.run_async(_update_task_failed(task_id, error))
    return {"status": "FAILED", "error": error}


async def _do_finalize_backfill(
    batch_results: list[dict],
    ts_start: str,
    ts_end: str,
    *,
    total_windows: int,
    task_id: str | None,
) -> dict:
    """Aggregate chord results; node aggregation only runs after clean headers."""
    if task_id and await _is_task_cancelled(task_id):
        return {**_empty_backfill_result(total_windows), "cancelled": True}

    result = _empty_backfill_result(total_windows)
    failed_window_list: list[str] = []
    for batch in batch_results:
        if not isinstance(batch, dict):
            result["loop_failed"] += 1
            continue
        result["loop_success"] += int(batch.get("success", 0))
        result["loop_inconclusive"] += int(batch.get("inconclusive", 0))
        result["loop_failed"] += int(batch.get("failed", 0))
        result["node_success"] += int(batch.get("node_success", 0))
        failed_window_list.extend(batch.get("failed_windows", []))
        if batch.get("cancelled"):
            result["cancelled"] = True

    result["failed_window_list"] = failed_window_list
    result["failed_windows"] = len(failed_window_list)

    if result.get("cancelled") or (task_id and await _is_task_cancelled(task_id)):
        result["cancelled"] = True
        return result

    if result["loop_failed"] or result["failed_windows"]:
        result["status"] = "FAILED"
        if task_id:
            await _update_task_failed(
                task_id,
                f"回填失败: loop_failed={result['loop_failed']}, "
                f"failed_windows={result['failed_windows']}",
            )
        return result

    result["status"] = "SUCCESS"
    if task_id:
        if await _is_task_cancelled(task_id):
            result["cancelled"] = True
            result.pop("status", None)
            return result
        await _update_task_success(task_id, result)
    return result


async def _update_backfill_progress(
    task_id: str,
    window_index: int,
    total_windows: int,
    done_in_window: int,
    loops_per_window: int,
) -> None:
    """更新回填/全量计算任务细粒度进度（逐回路，前端进度条 ≤10s 刷新）。

    进度按工作项（回路 × 窗口）计算：
    进度 = (已完成窗口 × 每窗口回路数 + 当前窗口已完成回路数) / (总窗口 × 每窗口回路数)

    工作项进度写入独立字段 ``work_items_total``/``work_items_done``；
    ``loops_total`` 恒为回路数（即每窗口回路数），不再被工作项数覆盖
    （2026-07-21 P0 根因修复）。``loops_done`` 仅在单窗口场景（STANDARD
    全量计算，工作项 == 回路）写入回路完成数。
    """
    from app.schemas.task import TaskStatus
    from app.services import task_tracker

    total_work_items = total_windows * loops_per_window
    done_work_items = (window_index - 1) * loops_per_window + done_in_window
    progress = done_work_items / total_work_items if total_work_items > 0 else 0.0
    if total_windows > 1:
        stage = (
            f"回填计算 窗口[{window_index}/{total_windows}]"
            f" 回路[{done_in_window}/{loops_per_window}]"
        )
    else:
        stage = f"指标计算 回路[{done_in_window}/{loops_per_window}]"
    await task_tracker.update_status(
        task_id,
        TaskStatus.RUNNING,
        progress=progress,
        loops_total=loops_per_window,
        loops_done=done_in_window if total_windows == 1 else None,
        work_items_total=total_work_items,
        work_items_done=done_work_items,
        current_stage=stage,
    )


async def _increment_backfill_progress(
    task_id: str,
    window_index: int,
    total_windows: int,
    loops_per_window: int,
    *,
    event_id: str | None = None,
) -> None:
    """按窗口+回路幂等计数，原子写入单调进度。

    进度按「回路×窗口」工作项写入 ``work_items_total``/``work_items_done``，
    不覆盖 ``loops_total``（恒为回路数）。
    """
    from app.services import task_tracker

    total_work_items = total_windows * loops_per_window
    unique_event = event_id or f"window:{window_index}:legacy:{uuid4()}"
    await task_tracker.record_backfill_progress_once(
        task_id,
        event_id=unique_event,
        total_work_items=total_work_items,
        current_stage=f"回填计算 窗口[{window_index}/{total_windows}]",
    )


async def _is_task_cancelled(task_id: str) -> bool:
    """检查任务是否已被取消。"""
    from app.core.redis import redis_client

    raw = await redis_client.hget(f"task:{task_id}", "status")
    if raw is None:
        return False
    return str(raw).upper() == "CANCELLED"


# 每个 Celery 子任务处理的窗口数
_BACKFILL_PROCESS_BATCH = _BACKFILL_BATCH_SIZE


def _process_windows_subprocess(
    window_isos: list[str],
    loop_ids: list[str] | None,
) -> dict:
    """顶层函数（可 pickle）：在子进程中计算一批窗口的 KPI。

    每个子进程创建独立事件循环和 DB 连接，实现真正的多核并行。
    """
    import asyncio

    async def _run() -> dict:
        from app.core.db import AsyncSessionLocal
        from app.services.loop_config import get_loop_type_weights_map

        windows_dt = [
            datetime.fromisoformat(w.replace("Z", "+00:00")).replace(tzinfo=None)
            for w in window_isos
        ]

        # 子进程内预加载
        async with AsyncSessionLocal() as db:
            stmt = select(LoopLedger).where(
                LoopLedger.is_active.is_(True),
                LoopLedger.status == "READY",
            )
            if loop_ids is not None:
                stmt = stmt.where(LoopLedger.id.in_(loop_ids))
            result = await db.execute(stmt)
            loops = list(result.scalars().all())

            if not loops:
                return {"success": 0, "inconclusive": 0, "failed": 0, "failed_windows": []}

            metric_result = await db.execute(select(MetricConfig))
            metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

            type_weights = await get_loop_type_weights_map(db)
            loop_configs = await _batch_load_loop_configs(db, [str(lp.id) for lp in loops])

        agg = {"success": 0, "inconclusive": 0, "failed": 0, "failed_windows": []}

        for w in windows_dt:
            try:
                results = await _run_batch_loop_calculations(
                    loops=loops,
                    loop_configs=loop_configs,
                    metric_configs=metric_configs,
                    ts_start=w,
                    ts_end=w + timedelta(hours=1),
                    type_weights=type_weights,
                    bundle_cache=False,
                )
                summary = _summarize_batch_results(results)
                agg["success"] += summary["success"]
                agg["inconclusive"] += summary["inconclusive"]
                agg["failed"] += summary["failed"]
            except Exception as exc:  # noqa: BLE001
                agg["failed_windows"].append(w.isoformat())
                logger.warning("子进程窗口 %s 失败: %s", w.isoformat(), exc)

        return agg

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


async def _do_backfill_node_aggregation(start_dt: datetime, end_dt: datetime) -> int:
    """统一节点级聚合：遍历所有窗口执行节点 KPI 聚合。"""
    from app.core.db import AsyncSessionLocal
    from app.models.plant_node import PlantNode
    from app.services.node_performance import batch_calculate_and_save_node_snapshots

    async with AsyncSessionLocal() as db:
        node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
        nodes = list(node_result.scalars().all())

    if not nodes:
        return 0

    cur = start_dt.replace(minute=0, second=0, microsecond=0)
    agg_node_success = 0
    while cur < end_dt:
        w_start = cur
        w_end = cur + timedelta(hours=1)
        result = await batch_calculate_and_save_node_snapshots(
            nodes=nodes,
            ts_start=w_start,
            ts_end=w_end,
            concurrency=10,
        )
        agg_node_success += result.get("success", 0)
        cur += timedelta(hours=1)

    logger.info(
        "回填节点聚合完成: %s ~ %s, node_success=%d",
        start_dt.isoformat(),
        end_dt.isoformat(),
        agg_node_success,
    )
    return agg_node_success


async def _do_backfill(
    ts_start: str,
    ts_end: str,
    loop_ids: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    """批量回填 async 逻辑：遍历小时窗口，每窗口全量回路计算 + 最终统一节点聚合。

    v4.1 优化（2026-07-16）：
    - 预加载回路列表/配置/指标配置（1 次 PG 查询，而非 N×窗口次）
    - 跳过 L2 缓存检查 + 兜底预热（backfill 场景缓存 Key 含时间窗口，必然 MISS）
    - 使用 bundle_cache=False 禁用 L1/L2 缓存读写（backfill 不会复用，纯浪费 I/O）
    - 节点级聚合改为全部窗口完成后统一执行一次（而非每窗口重复聚合）
    """
    from app.core.db import AsyncSessionLocal

    start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00")).replace(tzinfo=None)
    end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00")).replace(tzinfo=None)

    windows: list[datetime] = []
    cur = start_dt.replace(minute=0, second=0, microsecond=0)
    while cur < end_dt:
        windows.append(cur)
        cur += timedelta(hours=1)

    total = len(windows)

    if loop_ids is not None and len(loop_ids) == 0:
        return {
            "total_windows": total,
            "failed_windows": 0,
            "loop_success": 0,
            "loop_inconclusive": 0,
            "loop_failed": 0,
            "node_success": 0,
            "failed_window_list": [],
        }

    logger.info(
        "回填窗口数: %d (%s ~ %s), loop_ids=%s",
        total,
        start_dt.isoformat(),
        end_dt.isoformat(),
        "all" if loop_ids is None else f"{len(loop_ids)} loops",
    )

    # ── Phase 1: 快速校验（确认有可计算的回路） ──
    async with AsyncSessionLocal() as db:
        count_stmt = select(LoopLedger.id).where(
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
        if loop_ids is not None:
            count_stmt = count_stmt.where(LoopLedger.id.in_(loop_ids))
        count_result = await db.execute(count_stmt)
        loops_count = len(list(count_result.scalars().all()))

    if loops_count == 0:
        return {
            "total_windows": total,
            "failed_windows": 0,
            "loop_success": 0,
            "loop_inconclusive": 0,
            "loop_failed": 0,
            "node_success": 0,
            "failed_window_list": [],
        }

    logger.info(
        "回填窗口数: %d (%s ~ %s), loops=%d, 子进程将各自预加载配置",
        total,
        start_dt.isoformat(),
        end_dt.isoformat(),
        loops_count,
    )

    agg_loop_success = 0
    agg_loop_inconclusive = 0
    agg_loop_failed = 0
    failed_windows: list[str] = []

    # ── Phase 2: 多进程并行计算（Celery group + 异步轮询，不阻塞事件循环） ──
    import time as _time

    _phase2_t0 = _time.monotonic()
    window_isos = [w.isoformat() for w in windows]
    batches = [
        window_isos[i : i + _BACKFILL_PROCESS_BATCH]
        for i in range(0, len(window_isos), _BACKFILL_PROCESS_BATCH)
    ]
    num_batches = len(batches)
    logger.info(
        "[Phase2] 启动 Celery 并行: %d 窗口 → %d 子任务（每批 %d 窗口）",
        total,
        num_batches,
        _BACKFILL_PROCESS_BATCH,
    )

    # 提交所有子任务（非阻塞）
    _dispatch_t0 = _time.monotonic()
    job = group(_backfill_window_batch.s(batch, loop_ids=loop_ids) for batch in batches)
    group_result = job.apply_async()
    _dispatch_ids = [r.id for r in group_result.results]
    logger.info(
        "[Phase2] 子任务已派发: %d 个, celery_ids=%s, 派发耗时=%.2fs",
        len(_dispatch_ids),
        _dispatch_ids[:5],  # 只打印前 5 个避免刷屏
        _time.monotonic() - _dispatch_t0,
    )

    # 异步轮询结果（每 2 秒检查一次，不阻塞事件循环）
    # Celery prefork 16 进程：1 个运行主任务，最多 15 个并行子任务
    _poll_count = 0
    while not group_result.ready():
        _poll_count += 1
        # 每 10 次轮询（~20s）打印一次详细状态
        if _poll_count % 10 == 1:
            _states = []
            for i, r in enumerate(group_result.results):
                st = r.state  # PENDING/STARTED/SUCCESS/FAILURE/RETRY
                _states.append(f"batch{i}:{st}")
            _done = sum(1 for r in group_result.results if r.successful())
            logger.info(
                "[Phase2] 轮询#%d: done=%d/%d, 状态=[%s]",
                _poll_count,
                _done,
                num_batches,
                ", ".join(_states[:8]),  # 前 8 个
            )
        # 更新进度（基于已完成的子任务数）
        completed = sum(1 for r in group_result.results if r.successful())
        if completed > 0 and task_id and total > 0:
            est_progress = (completed / num_batches) * total
            try:
                await _update_backfill_progress(
                    task_id, int(est_progress), total, loops_count, loops_count
                )
            except Exception:
                logger.warning("[Phase2] 更新进度失败", exc_info=True)
        await asyncio.sleep(2)

    logger.info(
        "[Phase2] 所有子任务完成: 轮询次数=%d, 总耗时=%.2fs",
        _poll_count,
        _time.monotonic() - _phase2_t0,
    )

    # 收集所有子任务结果（禁止在 task 内调用 .get()，直接访问 .result 属性）
    _collect_t0 = _time.monotonic()
    for i, r in enumerate(group_result.results):
        batch = batches[i]
        try:
            if r.successful():
                batch_result = r.result
                logger.info(
                    "[Phase2] 收集 batch%d/%d (%s) 成功: celery_id=%s, ok=%d, failed=%d",
                    i + 1,
                    num_batches,
                    batch[0],
                    r.id,
                    batch_result.get("success", 0),
                    batch_result.get("failed", 0),
                )
            else:
                # 子任务失败 — 记录详细错误
                _err = r.result  # 异常对象
                logger.error(
                    "[Phase2] 收集 batch%d/%d (%s) 失败: celery_id=%s, state=%s, error=%s",
                    i + 1,
                    num_batches,
                    batch[0],
                    r.id,
                    r.state,
                    _err,
                )
                batch_result = {
                    "success": 0,
                    "inconclusive": 0,
                    "failed": len(batch),
                    "failed_windows": batch,
                }
        except Exception:
            logger.exception(
                "[Phase2] 收集 batch%d/%d (%s) 异常: celery_id=%s",
                i + 1,
                num_batches,
                batch[0],
                r.id,
            )
            batch_result = {
                "success": 0,
                "inconclusive": 0,
                "failed": len(batch),
                "failed_windows": batch,
            }

        agg_loop_success += batch_result.get("success", 0)
        agg_loop_inconclusive += batch_result.get("inconclusive", 0)
        agg_loop_failed += batch_result.get("failed", 0)
        failed_windows.extend(batch_result.get("failed_windows", []))

    logger.info(
        "[Phase2] 结果汇总: ok=%d, inconclusive=%d, failed=%d, failed_windows=%d, 收集耗时=%.2fs",
        agg_loop_success,
        agg_loop_inconclusive,
        agg_loop_failed,
        len(failed_windows),
        _time.monotonic() - _collect_t0,
    )

    logger.info(
        "回填回路计算完成: ok=%d, inconclusive=%d, failed=%d",
        agg_loop_success,
        agg_loop_inconclusive,
        agg_loop_failed,
    )

    # ── Phase 3: 统一节点级聚合（全部窗口完成后执行一次，而非每窗口重复） ──
    _phase3_t0 = _time.monotonic()
    logger.info(
        "[Phase3] 节点级聚合开始: windows=%d",
        total,
    )
    agg_node_success = 0
    try:
        from app.models.plant_node import PlantNode
        from app.services.node_performance import batch_calculate_and_save_node_snapshots

        async with AsyncSessionLocal() as db:
            node_result = await db.execute(
                select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True))
            )
            nodes = list(node_result.scalars().all())

        logger.info(
            "[Phase3] 加载 KPI 启用节点: %d 个, 耗时=%.2fs",
            len(nodes),
            _time.monotonic() - _phase3_t0,
        )

        if nodes:
            _node_loop_t0 = _time.monotonic()
            for n_idx, w in enumerate(windows, 1):
                w_start = w
                w_end = w + timedelta(hours=1)
                node_result = await batch_calculate_and_save_node_snapshots(
                    nodes=nodes,
                    ts_start=w_start,
                    ts_end=w_end,
                    concurrency=10,
                )
                agg_node_success += node_result.get("success", 0)
                if n_idx % 10 == 0 or n_idx == total:
                    logger.info(
                        "[Phase3] 节点聚合进度 %d/%d %s: node_success=%d, 累计耗时=%.2fs",
                        n_idx,
                        total,
                        w.isoformat(),
                        agg_node_success,
                        _time.monotonic() - _node_loop_t0,
                    )
            logger.info(
                "[Phase3] 节点聚合完成: windows=%d, node_success=%d, 总耗时=%.2fs",
                total,
                agg_node_success,
                _time.monotonic() - _phase3_t0,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "[Phase3] 节点聚合失败: %s (耗时=%.2fs)",
            exc,
            _time.monotonic() - _phase3_t0,
        )

    logger.info(
        "[回填] 全流程完成: windows=%d, loop_ok=%d, loop_failed=%d, "
        "node_success=%d, Phase2=%.2fs, Phase3=%.2fs, 总耗时=%.2fs",
        total,
        agg_loop_success,
        agg_loop_failed,
        agg_node_success,
        _time.monotonic() - _phase2_t0,
        _time.monotonic() - _phase3_t0,
        _time.monotonic() - _phase2_t0,
    )

    return {
        "total_windows": total,
        "failed_windows": len(failed_windows),
        "loop_success": agg_loop_success,
        "loop_inconclusive": agg_loop_inconclusive,
        "loop_failed": agg_loop_failed,
        "node_success": agg_node_success,
        "failed_window_list": failed_windows,
    }


# 节点级聚合不再使用独立 Beat 调度，改为回路级任务 _do_calculate() 完成后级联触发
# calculate_node_kpi_hourly.delay()，消除时序竞态（原 node-kpi-hourly Beat 已移除）


# ---------------------------------------------------------------------------
# 历史数据导入任务（Phase 3：从远端 HTTP API 拉取历史数据写入本地 TDengine）
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.kpi_calc.import_history_data",
    bind=True,
    base=AsyncTask,
    time_limit=7200,  # 2 小时硬超时
    soft_time_limit=6900,  # 115 分钟软超时（27 回路 × 14 小时数据量大）
)
def import_history_data(
    self: AsyncTask,
    loop_ids: list[str],
    ts_start: str,
    ts_end: str,
    interval: int = 1,
    conflict_strategy: str = "overwrite",
    trigger_backfill: bool = False,
    task_id: str | None = None,
) -> dict:
    """历史数据导入 Celery 任务.

    从远端 HTTP API 拉取历史数据，写入本地 TDengine 宽表。
    支持冲突策略：overwrite（先 DELETE 再 INSERT）或 skip（直接 INSERT）。

    Args:
        loop_ids: 回路 ID 列表
        ts_start: 开始时间 (ISO 8601)
        ts_end: 结束时间 (ISO 8601)
        interval: 采样间隔（秒），默认 1
        conflict_strategy: 冲突策略，overwrite 或 skip
        trigger_backfill: 是否在导入完成后触发 KPI 回算
        task_id: Redis 任务跟踪 ID（API 触发时传入）
    """
    logger.info(
        "历史数据导入任务开始, celery_id=%s, task_id=%s, loops=%d, range=%s~%s, strategy=%s",
        self.request.id,
        task_id or "(none)",
        len(loop_ids),
        ts_start,
        ts_end,
        conflict_strategy,
    )

    from app.services.data_import import import_history_data as _do_import

    return self.run_async(
        _do_import(
            loop_ids=loop_ids,
            ts_start=ts_start,
            ts_end=ts_end,
            interval=interval,
            conflict_strategy=conflict_strategy,
            trigger_backfill=trigger_backfill,
            task_id=task_id,
        )
    )


# ---------------------------------------------------------------------------
# 节点级日/月聚合任务（GB/T 44693.2-2024 §6.4 多级时间聚合）
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_daily_kpi",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_daily_kpi(self: AsyncTask, stat_date: str | None = None) -> dict:
    """每日节点级日聚合任务（Beat: 每日 00:05 触发）。

    遍历所有 is_kpi_enabled=True 的 PlantNode 节点，
    按 loop_count 加权聚合当天 24 条小时快照，
    写入 kpi_node_snapshot_daily。

    Args:
        stat_date: 统计日期（ISO 8601），None 表示昨天（Beat 00:05 触发时聚合前一天数据）
    """
    logger.info("节点级日聚合任务开始, task_id=%s, stat_date=%s", self.request.id, stat_date)
    try:
        result = self.run_async(_do_calculate_daily(stat_date))
        logger.info("节点级日聚合任务完成: %s", result)
        return result
    except Exception:
        logger.exception("节点级日聚合任务失败")
        raise


@celery_app.task(
    name="app.tasks.kpi_calc.calculate_monthly_kpi",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def calculate_monthly_kpi(self: AsyncTask, stat_month: str | None = None) -> dict:
    """每月节点级月聚合任务（Beat: 每月 1 日 00:10 触发）。

    遍历所有 is_kpi_enabled=True 的 PlantNode 节点，
    按 loop_count 加权聚合当月所有日快照，
    写入 kpi_node_snapshot_monthly。

    Args:
        stat_month: 统计月份（ISO 8601，月初），None 表示上个月
            （Beat 1 日 00:10 触发时聚合上个月数据）
    """
    logger.info("节点级月聚合任务开始, task_id=%s, stat_month=%s", self.request.id, stat_month)
    try:
        result = self.run_async(_do_calculate_monthly(stat_month))
        logger.info("节点级月聚合任务完成: %s", result)
        return result
    except Exception:
        logger.exception("节点级月聚合任务失败")
        raise


async def _do_calculate_daily(stat_date: str | None = None) -> dict:
    """执行节点级日聚合的实际 async 逻辑。"""
    from datetime import date

    from app.services.node_aggregation import aggregate_all_nodes_daily

    # 默认聚合昨天（Beat 00:05 触发时，前一天的数据已完整）
    if stat_date:
        try:
            stat_date_dt = datetime.fromisoformat(stat_date.replace("Z", "+00:00")).date()
        except ValueError:
            stat_date_dt = date.fromisoformat(stat_date)
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        stat_date_dt = (now - timedelta(days=1)).date()

    return await aggregate_all_nodes_daily(stat_date_dt)


async def _do_calculate_monthly(stat_month: str | None = None) -> dict:
    """执行节点级月聚合的实际 async 逻辑。"""
    from datetime import date

    from app.services.node_aggregation import aggregate_all_nodes_monthly

    # 默认聚合上个月（Beat 1 日 00:10 触发时，上个月的数据已完整）
    if stat_month:
        try:
            stat_month_dt = datetime.fromisoformat(stat_month.replace("Z", "+00:00")).date()
        except ValueError:
            stat_month_dt = date.fromisoformat(stat_month)
        # 规范化为月初
        stat_month_dt = stat_month_dt.replace(day=1)
    else:
        now = datetime.now(UTC).replace(tzinfo=None)
        # 上个月月初
        if now.month == 1:
            stat_month_dt = date(now.year - 1, 12, 1)
        else:
            stat_month_dt = date(now.year, now.month - 1, 1)

    return await aggregate_all_nodes_monthly(stat_month_dt)
