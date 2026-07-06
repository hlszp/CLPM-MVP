"""Celery tasks for KPI performance calculation (IDS v3.2 §2.3 — S3-METRIC-003).

v4.0 架构升级（Phase 4 整合重构）：
- 移除直接 TDengine 查询，改用 DataPlanner 数据编排器取数
- 移除自行实现的指标计算，改用 Phase 3 MetricCalculator 12 个计算器
- 移除 v1 评分回退，强制使用 ConfidenceEvaluator v2 综合评分
- 三层计算流程：Layer1 无依赖指标 → Layer2 有依赖指标 → Layer3 综合评分
- _save_snapshot 写入 7 个数据血缘字段，支持审计追溯

设计要点：
- Celery Beat 定时任务（每小时触发全量计算）
- DataPlanner 按 tagGroup 合并查询 + L1 DataBlock 缓存
- 任务幂等（相同 loop_id + ts_start 不重复写入）
- 失败自动重试 3 次
- 数据不足返回 INCONCLUSIVE 状态
- 完整数据血缘（sampling_freq / quality_policy / valid_rate / confidence_level）
"""

from __future__ import annotations

import asyncio
import logging
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select

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
from app.models.metric import KpiSnapshotHourly, MetricConfig
from app.models.tag import TagRegistry
from app.services.confidence_evaluator import (
    ALGORITHM_VERSION as CONFIDENCE_ALGORITHM_VERSION,
)
from app.services.confidence_evaluator import (
    ConfidenceEvaluator,
)
from app.services.data_planner import DataPlanner
from app.services.metric_calculator import get_calculator
from app.services.metric_data_bundle import MetricDataBundleAssembler
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

# 算法版本号（与 ConfidenceEvaluator 对齐）
ALGORITHM_VERSION = CONFIDENCE_ALGORITHM_VERSION
ALGORITHM_VERSION_V1 = "KPI_CALC_v1.0"  # 向后兼容标识（不再用于评分回退）

# 数据不足阈值：Good 数据占比 < 20% 视为 INCONCLUSIVE
MIN_GOOD_RATIO = 0.20

# 并发 worker 数
CONCURRENCY = 10

# ---------------------------------------------------------------------------
# metric_code 映射：数据库列名 ↔ 计算器注册表代码
# ---------------------------------------------------------------------------
# clpm_metric_data_requirement 表使用数据库列名（如 fast_rate），
# 而 CALCULATOR_REGISTRY 使用计算器代码（如 fast_rate）。
# 调用 DataPlanner 时传入数据库列名，调用计算器时映射为计算器代码。
_DB_TO_CALCULATOR_METRIC_CODE: dict[str, str] = {
    "accuracy_rate": "accuracy_rate",
    "fast_rate": "fast_rate",
    "steady_rate": "stability_rate",
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

# 反向映射：计算器代码 → 数据库列名（用于 _save_snapshot 写入快照表）
_CALCULATOR_TO_DB_METRIC_CODE: dict[str, str] = {
    v: k for k, v in _DB_TO_CALCULATOR_METRIC_CODE.items()
}

# 全部指标数据库列名列表（传给 DataPlanner.request_bundles）
_ALL_METRIC_CODES_DB: list[str] = list(_DB_TO_CALCULATOR_METRIC_CODE.keys())


def _is_metric_enabled(
    calc_code: str,
    metric_configs: dict[str, MetricConfig] | None,
) -> bool:
    """检查指标是否启用（MetricConfig.is_enabled）。

    PRD §5.1.3 / FDS §5.3.1.2：指标停用后该指标显示 INCONCLUSIVE，
    并联动影响综合评分。

    Args:
        calc_code: 计算器代码（如 accuracy_rate / fast_rate / stability_rate）
        metric_configs: 指标配置字典 {metric_code: MetricConfig}，键为数据库列名小写

    Returns:
        True 表示启用（或无配置时默认启用），False 表示被禁用
    """
    if metric_configs is None:
        return True
    db_code = _CALCULATOR_TO_DB_METRIC_CODE.get(calc_code, calc_code)
    mc = metric_configs.get(db_code) or metric_configs.get(calc_code)
    if mc and mc.is_enabled is False:
        return False
    return True


def _inject_threshold_to_bundle(
    bundle: MetricDataBundle | None,
    calc_code: str,
    metric_configs: dict[str, MetricConfig] | None,
) -> None:
    """从 MetricConfig.threshold 读取阈值参数，注入到 bundle.data_block.signals。

    FDS §5.3.1：指标配置的 threshold（JSONB {min,max,alert} 等）应被计算器使用。
    v4.0 计算器通过 _read_config_scalar 从 data_block.signals 读取参数，
    本函数将 MetricConfig.threshold 中的阈值注入 signals，使配置生效。

    Args:
        bundle: 指标数据包（None 时跳过）
        calc_code: 计算器代码
        metric_configs: 指标配置字典
    """
    if bundle is None or metric_configs is None:
        return
    db_code = _CALCULATOR_TO_DB_METRIC_CODE.get(calc_code, calc_code)
    mc = metric_configs.get(db_code) or metric_configs.get(calc_code)
    if mc and mc.threshold:
        signals = bundle.data_block.signals
        for key, val in mc.threshold.items():
            # signals 约定：标量值存储为单元素列表 [val]（CONFIG tagGroup 规范）
            if key not in signals:
                signals[key] = [val] if not isinstance(val, list) else val


def _make_disabled_result(calc_code: str) -> MetricResult:
    """构造指标被禁用时的 INCONCLUSIVE 结果。

    PRD §5.1.3：指标停用后该指标显示 INCONCLUSIVE。
    """
    return MetricResult(
        metric_code=calc_code,
        value=None,
        confidence_level="E",
        lineage=DataLineage(algorithm_version=ALGORITHM_VERSION),
        details={"reason": "metric_disabled_by_config"},
    )


# LoopLedger.loop_type → ControlType 映射
# SPEED/OTHER 无直接对应控制类型，回退为 FLOW（采样率 1s，最宽松）
_LOOP_TYPE_TO_CONTROL_TYPE: dict[str, ControlType] = {
    "FLOW": ControlType.FLOW,
    "PRESSURE": ControlType.PRESSURE,
    "TEMPERATURE": ControlType.TEMPERATURE,
    "LEVEL": ControlType.LEVEL,
    "ANALYSIS": ControlType.COMPOSITION,
    "SPEED": ControlType.FLOW,
    "OTHER": ControlType.FLOW,
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

    Phase 5 补齐：集成 task_tracker，定时触发时创建任务记录，
    使定时任务在 ``GET /tasks`` 列表可见（``triggered_by=system``）。
    任务跟踪失败不影响计算本身。

    P1 #11 修正：接收 ``ts_start`` 参数，使手动触发标准评估任务时
    用户指定的时间窗能传递到实际计算逻辑（cron 定时触发时不传参，
    默认 ``None`` 表示取上一个完整计算周期）。

    Args:
        ts_start: 时间窗起始（ISO 8601 字符串，UTC）；None 时取上一个完整计算周期
    """
    logger.info("KPI 计算任务开始, task_id=%s, ts_start=%s", self.request.id, ts_start)
    try:
        result = self.run_async(_track_hourly_calculation(self.request.id, ts_start))
        logger.info("KPI 计算任务完成: %s", result)
        return result
    except Exception:
        logger.exception("KPI 计算任务失败")
        raise


def _parse_ts_start(ts_start: str | None) -> datetime | None:
    """将 ISO 8601 字符串解析为 datetime（UTC），None 时返回 None。

    兼容带 ``Z`` 后缀和不带后缀两种格式。
    """
    if not ts_start:
        return None
    try:
        return datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromisoformat(ts_start)


async def _track_hourly_calculation(celery_task_id: str, ts_start: str | None = None) -> dict:
    """包装 _do_calculate，加入任务跟踪记录（cron 定时触发专用）.

    创建任务记录 → 标记 RUNNING → 执行计算 → 标记 SUCCESS/FAILED。
    跟踪失败时降级为直接执行 _do_calculate（不影响计算本身）。

    P1 #11 修正：``ts_start`` 透传到 ``_do_calculate``，使手动触发的
    标准评估任务能使用用户指定的时间窗。

    Args:
        celery_task_id: Celery 任务 ID
        ts_start: 时间窗起始（ISO 8601 字符串，UTC）；None 时取上一个完整计算周期

    Returns:
        _do_calculate 的返回值
    """
    from app.schemas.task import TaskStatus, TaskType
    from app.services import task_tracker

    ts_start_dt = _parse_ts_start(ts_start)

    tracker_id: str | None = None
    try:
        tracker_id = await task_tracker.create_task(
            task_type=TaskType.STANDARD,
            created_by="system",
            created_by_id="",  # 系统任务无对应用户
            celery_task_id=celery_task_id,
            triggered_by="system",
            current_stage="初始化",
        )
        await task_tracker.update_status(
            tracker_id,
            TaskStatus.RUNNING,
            started_at=task_tracker._now_iso(),
            progress=0.0,
        )
    except Exception:
        logger.warning("任务跟踪记录创建失败，降级为直接执行计算", exc_info=True)
        return await _do_calculate(ts_start=ts_start_dt)

    try:
        result = await _do_calculate(ts_start=ts_start_dt)
        await task_tracker.update_status(
            tracker_id,
            TaskStatus.SUCCESS,
            finished_at=task_tracker._now_iso(),
            progress=1.0,
            loops_total=result.get("total"),
            loops_done=result.get("success", 0),
        )
        return result
    except Exception as exc:
        await task_tracker.update_status(
            tracker_id,
            TaskStatus.FAILED,
            finished_at=task_tracker._now_iso(),
            error_message=str(exc),
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
    """单回路 KPI 计算（标准任务，写入 kpi_snapshot_hourly，参与聚合）。"""
    logger.info("单回路 KPI 计算（标准任务）, loop_id=%s", loop_id)
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
    ts_start: str | None = None,
    ts_end: str | None = None,
) -> dict:
    """单回路 KPI 计算（自定义任务，写入 kpi_snapshot_custom，不参与聚合）.

    PRD §4.3.7.B / FDS §5.3.11：自定义评估任务结果写入独立快照表，
    通过 task_id 区分，不参与装置级/单元级/工厂级聚合。

    Args:
        task_id: 自定义任务 ID（用于区分独立任务）
        loop_id: 回路 ID
        ts_start: 时间窗起始（ISO 8601，UTC）；None 时取上一个完整计算周期
        ts_end: 时间窗结束（ISO 8601，UTC）；None 时按 cycle_minutes 计算。
            P1 #12 修正：透传用户指定的时间窗结束，使自定义任务支持
            非 cycle_minutes 长度的时间窗。
    """
    logger.info(
        "单回路 KPI 计算（自定义任务）, task_id=%s, loop_id=%s, ts_start=%s, ts_end=%s",
        task_id,
        loop_id,
        ts_start,
        ts_end,
    )
    return AsyncTask().run_async(_do_calculate_custom_loop(task_id, loop_id, ts_start, ts_end))


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
        loop_ids: 回路 ID 过滤列表。None=全量（保持原行为）；
            非空列表=仅这些回路（HTTP API 历史重算按回路精准过滤）；
            空列表=直接返回 0 结果。
        task_id: Redis 任务跟踪 ID（HTTP API 触发时传入）。
            传入则主动更新 Redis 中的任务状态/进度；
            None（CLI 触发）则只打日志，不更新 Redis。

    用途：
        - 补齐因数据空档或服务中断缺失的历史 KPI 快照
        - 修复契约配置后重新计算指定时段的指标
        - 按回路/装置精准重算历史数据
    """
    logger.info(
        "KPI 回填任务开始, celery_id=%s, task_id=%s, range=%s~%s, loop_ids=%s",
        self.request.id,
        task_id or "(none)",
        ts_start,
        ts_end,
        "all" if loop_ids is None else f"{len(loop_ids)} loops",
    )

    # 同步初始状态：PENDING → RUNNING
    if task_id:
        try:
            self.run_async(_update_task_running(task_id))
        except Exception:
            logger.warning("更新任务 RUNNING 状态失败: task_id=%s", task_id, exc_info=True)

    try:
        result = self.run_async(_do_backfill(ts_start, ts_end, loop_ids=loop_ids, task_id=task_id))
        logger.info("KPI 回填任务完成: %s", result)

        # 同步终态：RUNNING → SUCCESS
        if task_id:
            try:
                self.run_async(_update_task_success(task_id, result))
            except Exception:
                logger.warning("更新任务 SUCCESS 状态失败: task_id=%s", task_id, exc_info=True)

        return result
    except Exception as exc:
        logger.exception("KPI 回填任务失败")

        # 同步终态：RUNNING → FAILED
        if task_id:
            try:
                self.run_async(_update_task_failed(task_id, str(exc)))
            except Exception:
                logger.warning("更新任务 FAILED 状态失败: task_id=%s", task_id, exc_info=True)
        raise


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

    await task_tracker.update_status(
        task_id,
        TaskStatus.FAILED,
        current_stage="失败",
        error_message=error_message,
        finished_at=task_tracker._now_iso(),
    )


async def _do_backfill(
    ts_start: str,
    ts_end: str,
    loop_ids: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    """批量回填 async 逻辑：遍历小时窗口，每窗口全量回路计算 + 节点聚合。

    在 Celery worker 的 event loop 内执行，复用 worker 的 httpx client，
    避免脚本进程 asyncio.run 环境下 TDengine 查询异常的问题。

    Args:
        ts_start: 起始时间 ISO 8601
        ts_end: 结束时间 ISO 8601（不包含）
        loop_ids: 回路 ID 过滤列表；None=全量，空列表=跳过计算
        task_id: Redis 任务跟踪 ID；传入则每窗口完成后更新进度
    """
    start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))

    # 生成小时窗口列表
    windows: list[datetime] = []
    cur = start_dt.replace(minute=0, second=0, microsecond=0)
    while cur < end_dt:
        windows.append(cur)
        cur += timedelta(hours=1)

    total = len(windows)

    # 空列表提前返回，避免遍历窗口调用 _do_calculate
    # 注意：窗口数已计算（total_windows），仅跳过计算逻辑
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

    agg_loop_success = 0
    agg_loop_inconclusive = 0
    agg_loop_failed = 0
    agg_node_success = 0
    failed_windows: list[str] = []

    for i, w in enumerate(windows, 1):
        # 每窗口开始前检查任务是否已被取消（POST /tasks/{task_id}/cancel 触发）
        if task_id:
            try:
                if await _is_task_cancelled(task_id):
                    logger.info(
                        "检测到取消标志，提前终止回填: task_id=%s, completed=%d/%d",
                        task_id,
                        i - 1,
                        total,
                    )
                    return {
                        "total_windows": total,
                        "failed_windows": len(failed_windows),
                        "loop_success": agg_loop_success,
                        "loop_inconclusive": agg_loop_inconclusive,
                        "loop_failed": agg_loop_failed,
                        "node_success": agg_node_success,
                        "failed_window_list": failed_windows,
                        "cancelled": True,
                        "completed_windows": i - 1,
                    }
            except Exception:
                logger.warning("查询取消标志失败，继续执行: task_id=%s", task_id, exc_info=True)

        try:
            loop_result = await _do_calculate(ts_start=w, cascade_node=False, loop_ids=loop_ids)
            node_result = await _do_calculate_node_kpi(ts_start=w)
            agg_loop_success += loop_result.get("success", 0)
            agg_loop_inconclusive += loop_result.get("inconclusive", 0)
            agg_loop_failed += loop_result.get("failed", 0)
            agg_node_success += node_result.get("success", 0)
            logger.info(
                "回填进度 [%d/%d] %s: loop_ok=%d, node_ok=%d",
                i,
                total,
                w.isoformat(),
                loop_result.get("success", 0),
                node_result.get("success", 0),
            )
        except Exception as exc:  # noqa: BLE001
            failed_windows.append(w.isoformat())
            logger.warning("回填窗口 %s 失败: %s", w.isoformat(), exc)

        # 每窗口完成后更新 Redis 进度
        if task_id:
            try:
                await _update_task_progress(task_id, i, total)
            except Exception:
                logger.warning(
                    "更新任务进度失败: task_id=%s, window=%d/%d",
                    task_id,
                    i,
                    total,
                    exc_info=True,
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


async def _update_task_progress(task_id: str, done: int, total: int) -> None:
    """更新任务进度（每窗口完成后调用）。

    注意：``loops_total`` 字段在任务创建时已填为真实回路数（如 27），
    此处不应覆盖为窗口数（如 219）——否则前端会误显示"219 个回路"。
    ``loops_done`` 此处填窗口完成数，配合 ``current_stage`` 文本展示进度。
    ``progress`` = done/total 反映窗口级完成比例。
    """
    from app.schemas.task import TaskStatus
    from app.services import task_tracker

    progress = done / total if total > 0 else 0.0
    await task_tracker.update_status(
        task_id,
        TaskStatus.RUNNING,
        progress=progress,
        loops_done=done,
        current_stage=f"回填计算 [{done}/{total}]",
    )


async def _is_task_cancelled(task_id: str) -> bool:
    """检查任务是否已被取消（POST /tasks/{task_id}/cancel 触发）。

    直接读 Redis 中的 task:{task_id} 哈希，避免 task_tracker 服务的额外封装。
    取消是终态，一旦置为 CANCELLED，worker 在下个窗口开始前应主动终止。
    """
    from app.core.redis import redis_client

    raw = await redis_client.hget(f"task:{task_id}", "status")
    if raw is None:
        return False
    return str(raw).upper() == "CANCELLED"


# ---------------------------------------------------------------------------
# Beat 调度配置：计算周期由 EngineRule EVAL_CALC_CYCLE 决定 + 每日 00:05 + 每月 1 日 00:10
# ---------------------------------------------------------------------------


from celery.schedules import crontab  # noqa: E402


def _load_calc_cycle_seconds_from_db() -> float:
    """同步读取 EngineRule EVAL_CALC_CYCLE.cycle_minutes（refresh_beat_schedule 调用）.

    PRD §5.4.2 / FDS §5.3.3：计算周期由引擎规则配置，不硬编码。
    本函数在 refresh_beat_schedule 任务中调用（Celery worker 进程内），
    通过同步 SQLAlchemy 查询读取 EngineRule。

    Returns:
        计算周期秒数（默认 3600）
    """
    try:
        from sqlalchemy import create_engine, text

        from app.core.config import settings

        sync_url = settings.postgres_dsn.replace("+asyncpg", "")
        engine = create_engine(sync_url)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT params FROM engine_rule "
                        "WHERE rule_code = 'EVAL_CALC_CYCLE' AND is_enabled = TRUE"
                    )
                ).fetchone()
                if row and row[0]:
                    import json

                    params = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    minutes = int(params.get("cycle_minutes", 60))
                    if minutes > 0:
                        logger.info(
                            "EngineRule EVAL_CALC_CYCLE.cycle_minutes=%d → beat schedule %ds",
                            minutes,
                            minutes * 60,
                        )
                        return float(minutes * 60)
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 EngineRule EVAL_CALC_CYCLE 失败，回退默认 3600s: %s", exc)
    return 3600.0


# Beat schedule 默认 3600s（1 小时）。
# 实际计算周期由 EngineRule EVAL_CALC_CYCLE.cycle_minutes 决定，
# 通过 refresh_beat_schedule 任务动态更新（update_engine_rule 后触发）。
# 模块加载时不查数据库，避免阻塞导入和 psycopg2 依赖。
# 修改 EVAL_CALC_CYCLE 后需重启 Beat 进程才能生效。
_beat_entry = {
    "task": "app.tasks.kpi_calc.calculate_hourly_kpi",
    "schedule": 3600.0,  # 默认 1 小时，可由 EngineRule 动态覆盖
}

# 合并到 celery_app 的 beat_schedule（与 aas_sync 的 beat 共存）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["kpi-calc-hourly"] = _beat_entry
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


@celery_app.task(name="app.tasks.kpi_calc.refresh_beat_schedule")
def refresh_beat_schedule() -> dict:
    """刷新 Beat 调度配置（EngineRule EVAL_CALC_CYCLE 变更后触发）.

    PRD §5.4.2：计算周期变更后需刷新 Beat schedule 才能生效。
    本任务重新读取 EngineRule 并更新 celery_app.conf.beat_schedule。
    注意：Beat 进程需重启才能加载新的 schedule（Celery Beat 限制）。

    Returns:
        {"calc_cycle_seconds": float, "beat_schedule_updated": bool}
    """
    new_seconds = _load_calc_cycle_seconds_from_db()
    beat = getattr(celery_app.conf, "beat_schedule", None) or {}
    beat["kpi-calc-hourly"] = {
        "task": "app.tasks.kpi_calc.calculate_hourly_kpi",
        "schedule": new_seconds,
    }
    celery_app.conf.beat_schedule = beat
    # 同时清除 EngineRuleLoader 缓存
    from app.services.engine_rule_loader import get_engine_rule_loader

    get_engine_rule_loader().invalidate_cache()
    logger.warning(
        "Beat schedule 已刷新: kpi-calc-hourly schedule=%.0fs（需重启 Beat 进程生效）",
        new_seconds,
    )
    return {
        "calc_cycle_seconds": new_seconds,
        "beat_schedule_updated": True,
        "note": "Beat 进程需重启才能加载新 schedule",
    }


# ---------------------------------------------------------------------------
# 异步计算逻辑
# ---------------------------------------------------------------------------


async def _do_calculate(
    ts_start: datetime | None = None,
    cascade_node: bool = True,
    loop_ids: list[str] | None = None,
) -> dict:
    """执行全量 KPI 计算的实际 async 逻辑。

    Args:
        ts_start: 时间窗起始（UTC，naive 视为 UTC）。None 时取「上一个完整计算周期」，
            周期长度由 EngineRule EVAL_CALC_CYCLE.cycle_minutes 决定（默认 60 分钟）。
        cascade_node: 是否在回路级计算完成后级联触发节点级聚合任务。
            脚本批量回填时设为 False，由脚本同步调用 _do_calculate_node_kpi
            避免大量 .delay() 调用堆积到 Celery 队列。
        loop_ids: 回路 ID 过滤列表。None=全量 ACTIVE/READY 回路（保持原行为）；
            非空列表=仅这些回路（用于历史重算按回路精准过滤）；
            空列表=直接返回 0 结果。

    引擎规则（PRD §5.4.2 / FDS §5.3.3）：
        - EVAL_CALC_CYCLE.cycle_minutes → 计算周期 + 时间窗长度
        - SCHEDULE_CONCURRENCY.concurrency → 并发处理数量
    """
    from app.core.db import AsyncSessionLocal
    from app.services.engine_rule_loader import get_engine_rule_loader

    engine_loader = get_engine_rule_loader()

    # 主 session 仅用于查询回路列表、指标配置和引擎规则（只读，无并发）
    async with AsyncSessionLocal() as db:
        # 引擎规则：计算周期（分钟）+ 并发数
        cycle_minutes = await engine_loader.get_calc_cycle_minutes(db)
        concurrency = await engine_loader.get_concurrency(db)
        logger.info("引擎规则: calc_cycle=%dmin, concurrency=%d", cycle_minutes, concurrency)

        # 计算时间窗：周期长度由 EngineRule 决定（对齐 EVAL_CALC_CYCLE.cycle_minutes）
        if ts_start is not None:
            ts_end = ts_start + timedelta(minutes=cycle_minutes)
        else:
            now = datetime.now(UTC)
            # 对齐到计算周期边界（cycle_minutes 整数倍）
            ts_end = now.replace(second=0, microsecond=0)
            ts_end = ts_end.replace(minute=(ts_end.minute // cycle_minutes) * cycle_minutes)
            ts_start = ts_end - timedelta(minutes=cycle_minutes)

        # 1. 查询回路（支持 loop_ids 过滤）
        # loop_ids=[] 表示空集，直接返回 0 结果（避免误查全量）
        if loop_ids is not None and len(loop_ids) == 0:
            return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

        stmt = select(LoopLedger).where(
            LoopLedger.is_active.is_(True),
            LoopLedger.status == "READY",
        )
        if loop_ids is not None:
            stmt = stmt.where(LoopLedger.id.in_(loop_ids))

        loop_result = await db.execute(stmt)
        loops = list(loop_result.scalars().all())
        logger.info(
            "待计算回路数: %d (loop_ids=%s)",
            len(loops),
            "all" if loop_ids is None else f"{len(loop_ids)} filtered",
        )

        if not loops:
            return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

        # 2. 加载指标配置（PRD §5.1.3：is_enabled 控制计算器执行，threshold 注入 bundle）
        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        # 2.1 批量加载回路类型权重（v2 算法用）
        from app.services.loop_config import get_loop_type_weights_map

        type_weights = await get_loop_type_weights_map(db)
        logger.info("已加载回路类型权重: %s", list(type_weights.keys()))

    # 3. 并发计算（信号量限制并发数，每协程独立 session 避免并发共享）
    # 并发数由 EngineRule SCHEDULE_CONCURRENCY.concurrency 决定
    sem = asyncio.Semaphore(concurrency)

    async def _calc_with_sem(loop: LoopLedger) -> dict | None:
        async with sem:
            # 每协程独立 session，避免 AsyncSession 并发共享导致的不可预期错误
            async with AsyncSessionLocal() as worker_db:
                try:
                    result = await _calculate_loop_kpi(
                        db=worker_db,
                        loop=loop,
                        metric_configs=metric_configs,
                        ts_start=ts_start,
                        ts_end=ts_end,
                        type_weights=type_weights,
                    )
                    await worker_db.commit()
                    return result
                except Exception:
                    await worker_db.rollback()
                    raise

    tasks = [asyncio.create_task(_calc_with_sem(loop)) for loop in loops]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = 0
    inconclusive_count = 0
    failed_count = 0
    for r in results:
        if isinstance(r, Exception):
            failed_count += 1
            logger.warning("回路计算失败: %s", r)
        elif r is None:
            failed_count += 1
        elif r.get("status") == "INCONCLUSIVE":
            inconclusive_count += 1
        else:
            success_count += 1

    # 级联触发节点级 KPI 聚合（确保回路快照已写入后再聚合，消除时序竞态）
    if cascade_node:
        try:
            calculate_node_kpi_hourly.delay()
            logger.info("已触发节点级 KPI 聚合任务（回路级计算完成后级联）")
        except Exception as exc:  # noqa: BLE001
            logger.warning("触发节点级 KPI 聚合任务失败: %s", exc)

    return {
        "total": len(loops),
        "success": success_count,
        "inconclusive": inconclusive_count,
        "failed": failed_count,
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
    }


async def _do_calculate_single_loop(loop_id: str, ts_start: str | None = None) -> dict:
    """单回路 KPI 计算。"""
    from app.core.db import AsyncSessionLocal
    from app.services.engine_rule_loader import get_engine_rule_loader

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
        loop = result.scalar_one_or_none()
        if loop is None:
            return {"loopId": loop_id, "status": "FAILED", "error": "回路不存在"}

        # 引擎规则：计算周期决定时间窗长度
        engine_loader = get_engine_rule_loader()
        cycle_minutes = await engine_loader.get_calc_cycle_minutes(db)

        # 时间窗
        now = datetime.now(UTC)
        if ts_start:
            try:
                ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
            except ValueError:
                ts_start_dt = datetime.fromisoformat(ts_start)
        else:
            ts_start_dt = (now - timedelta(minutes=cycle_minutes)).replace(second=0, microsecond=0)
            # 对齐到计算周期边界
            ts_start_dt = ts_start_dt.replace(
                minute=(ts_start_dt.minute // cycle_minutes) * cycle_minutes
            )
        ts_end_dt = ts_start_dt + timedelta(minutes=cycle_minutes)

        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        # 加载回路类型权重（v2 算法用）
        from app.services.loop_config import get_loop_type_weights_map

        type_weights = await get_loop_type_weights_map(db)

        snap = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs=metric_configs,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
            type_weights=type_weights,
        )
        await db.commit()
        return snap or {"loopId": loop_id, "status": "FAILED"}


async def _do_calculate_custom_loop(
    task_id: str,
    loop_id: str,
    ts_start: str | None = None,
    ts_end: str | None = None,
) -> dict:
    """单回路 KPI 计算（自定义任务，写入 kpi_snapshot_custom）.

    PRD §4.3.7.B / FDS §5.3.11：自定义评估任务与标准任务计算逻辑相同，
    但结果写入 kpi_snapshot_custom 表（通过 task_id 区分），
    不参与装置级/单元级/工厂级聚合。

    P1 #12: ts_end 透传后支持用户指定非 cycle_minutes 长度的时间窗。
    """
    from app.core.db import AsyncSessionLocal
    from app.services.engine_rule_loader import get_engine_rule_loader

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
        loop = result.scalar_one_or_none()
        if loop is None:
            return {"loopId": loop_id, "taskId": task_id, "status": "FAILED", "error": "回路不存在"}

        # 引擎规则：计算周期决定时间窗长度
        engine_loader = get_engine_rule_loader()
        cycle_minutes = await engine_loader.get_calc_cycle_minutes(db)

        # 时间窗起始
        now = datetime.now(UTC)
        ts_start_dt = _parse_ts_start(ts_start)
        if ts_start_dt is None:
            ts_start_dt = (now - timedelta(minutes=cycle_minutes)).replace(second=0, microsecond=0)
            ts_start_dt = ts_start_dt.replace(
                minute=(ts_start_dt.minute // cycle_minutes) * cycle_minutes
            )

        # 时间窗结束（P1 #12: 用户指定 ts_end 优先；否则按 cycle_minutes 计算）
        ts_end_dt = _parse_ts_start(ts_end)
        if ts_end_dt is None:
            ts_end_dt = ts_start_dt + timedelta(minutes=cycle_minutes)

        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code.lower(): c for c in metric_result.scalars().all()}

        # 加载回路类型权重（v2 算法用）
        from app.services.loop_config import get_loop_type_weights_map

        type_weights = await get_loop_type_weights_map(db)

        # 关键：传入 custom_task_id → 写入 kpi_snapshot_custom（不参与聚合）
        snap = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs=metric_configs,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
            type_weights=type_weights,
            custom_task_id=task_id,
        )
        await db.commit()
        return snap or {"loopId": loop_id, "taskId": task_id, "status": "FAILED"}


async def _calculate_loop_kpi(
    db,
    loop: LoopLedger,
    metric_configs: dict[str, MetricConfig],
    ts_start: datetime,
    ts_end: datetime,
    data_planner: DataPlanner | None = None,
    type_weights: dict[str, dict] | None = None,
    custom_task_id: str | None = None,
) -> dict | None:
    """计算单回路 KPI 并写入快照（幂等）。

    v4.0 三层架构：
        1. DataPlanner 获取 MetricDataBundle 列表（含预处理 + 缓存）
        2. _compute_kpis_three_layer 执行三层计算（无依赖 → 有依赖 → 综合评分）
        3. 写入快照表（含 7 个数据血缘字段）

    任务策略（PRD §4.3.7 / FDS §5.3.11）：
        - custom_task_id=None：标准任务，写入 kpi_snapshot_hourly，**参与装置级聚合**
        - custom_task_id=<uuid>：自定义任务，写入 kpi_snapshot_custom，**不参与聚合**

    Args:
        db: 异步数据库会话
        loop: 回路对象
        metric_configs: 指标配置字典 {metric_code: MetricConfig}。
            PRD §5.1.3 / FDS §5.3.1：is_enabled 控制计算器是否执行，
            threshold 阈值参数注入 bundle 供计算器读取。
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        data_planner: 数据编排器实例（注入便于测试）；None 时内部构造
        type_weights: 回路类型权重映射（v2 算法用）
        custom_task_id: 自定义任务 ID。None 表示标准任务（写入
            kpi_snapshot_hourly）；非 None 表示自定义任务（写入
            kpi_snapshot_custom，不参与聚合）。

    Returns:
        快照字典，包含 status 字段
    """
    # 构造 DataPlanner（未注入时从 db 构造）
    if data_planner is None:
        data_planner = _build_data_planner(db)

    # 推断控制类型
    control_type = _loop_type_to_control_type(loop.loop_type)

    logger.info(
        "[回路KPI] 开始计算 loop_id=%s tag_name=%s loop_type=%s control_type=%s "
        "ts_start=%s ts_end=%s custom_task_id=%s",
        loop.id,
        loop.tag_name,
        loop.loop_type,
        control_type.value,
        ts_start.isoformat(),
        ts_end.isoformat(),
        custom_task_id,
    )

    # 通过 DataPlanner 获取所有指标的 MetricDataBundle
    try:
        bundles = await data_planner.request_bundles(
            loop_id=str(loop.id),
            metrics=_ALL_METRIC_CODES_DB,
            time_window=TimeWindow(start=ts_start, end=ts_end),
            control_type=control_type,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DataPlanner 取数失败（回路 %s 跳过）: %s", loop.tag_name, exc)
        snap = await _persist_snapshot(
            db=db,
            custom_task_id=custom_task_id,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )
        return snap

    if not bundles:
        logger.warning("DataPlanner 返回空 Bundle 列表（回路 %s）", loop.tag_name)
        snap = await _persist_snapshot(
            db=db,
            custom_task_id=custom_task_id,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )
        return snap

    # 构造虚拟 CONFIG bundle（ideal_settling_time 的数据源，DataPlanner 跳过 CONFIG tagGroup）
    config_bundle = _build_config_bundle(str(loop.id), control_type)

    # 构造权重映射：优先级 MetricConfig.weight > LoopTypeWeight > DEFAULT_WEIGHTS
    # P2 #27: MetricConfig.weight 实际参与计算，管理员修改 /configs/metrics 即时生效
    # v5.3 对齐 DDS v4.1：直接使用 LoopLedger.control_type（STABLE/SLOW/FAST/LOGIC）
    # 而非由 loop_type 推断，避免业务类型与评分模板耦合
    score_type = loop.control_type or "LOGIC"
    weights = _build_weights_map(type_weights, score_type, metric_configs)

    # 三层计算：Layer1 无依赖 → Layer2 有依赖 → Layer3 综合评分
    # PRD §5.1.3 / FDS §5.3.1：metric_configs 控制 is_enabled 过滤 + threshold 注入
    metric_results, composite_result = _compute_kpis_three_layer(
        bundles=bundles,
        config_bundle=config_bundle,
        weights=weights,
        metric_configs=metric_configs,
    )

    # 将 MetricResult 映射为数据库列名 → Decimal 值（用于 _save_snapshot）
    kpi_values = _extract_kpi_values(metric_results)

    # 提取综合评分
    score = Decimal(str(composite_result.value)) if composite_result.value is not None else None

    # v5.3 对齐 FDS §5.2.3 / DDS v4.1：不参评回路（include_in_evaluation=false）
    # 单回路 KPI 指标仍计算，但综合评分 score 设为 None（不写入快照 score 字段）
    if not loop.include_in_evaluation:
        score = None
        logger.info(
            "[回路KPI] 回路 %s 未参评（include_in_evaluation=false），score 不写入",
            loop.tag_name,
        )

    # 提取数据血缘信息（从 accuracy_rate 的 lineage 取，若不存在则从任意结果取）
    lineage_info = _extract_lineage_info(metric_results, composite_result)

    # 判定状态
    status = "SUCCESS"
    required_db_codes = ("good_value_rate", "auto_mode_rate", "steady_rate")
    if any(kpi_values.get(k) is None for k in required_db_codes):
        status = "PARTIAL"
    # 综合评分为 None 时状态降级——仅当回路参评时才降级为 INCONCLUSIVE
    # 不参评回路 score=None 是设计行为（非数据质量问题），不应降级
    if score is None and loop.include_in_evaluation:
        status = "INCONCLUSIVE"

    snap = await _persist_snapshot(
        db=db,
        custom_task_id=custom_task_id,
        loop_id=str(loop.id),
        ts_start=ts_start,
        ts_end=ts_end,
        status=status,
        score=score,
        good_value_rate=kpi_values.get("good_value_rate"),
        auto_mode_rate=kpi_values.get("auto_mode_rate"),
        effective_auto_rate=kpi_values.get("effective_auto_rate"),
        steady_rate=kpi_values.get("steady_rate"),
        accuracy_rate=kpi_values.get("accuracy_rate"),
        fast_rate=kpi_values.get("fast_rate"),
        oscillation_rate=kpi_values.get("oscillation_rate"),
        saturation_rate=kpi_values.get("saturation_rate"),
        stiction_index=kpi_values.get("stiction_index"),
        settling_time=kpi_values.get("settling_time"),
        output_trip_index=kpi_values.get("output_trip_index"),
        ideal_settling_time=kpi_values.get("ideal_settling_time"),
        algorithm_version=lineage_info["algorithm_version"],
        sampling_freq=lineage_info["sampling_freq"],
        quality_policy=lineage_info["quality_policy"],
        valid_rate=lineage_info["valid_rate"],
        confidence_level=lineage_info["confidence_level"],
        data_lineage=lineage_info["data_lineage"],
    )
    logger.info(
        "[回路KPI] 计算完成 loop_id=%s tag_name=%s status=%s score=%s confidence=%s "
        "valid_rate=%s custom_task_id=%s",
        loop.id,
        loop.tag_name,
        status,
        float(score) if score is not None else None,
        lineage_info["confidence_level"],
        lineage_info["valid_rate"],
        custom_task_id,
    )
    return snap


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


# ---------------------------------------------------------------------------
# v4.0 三层计算流程辅助函数
# ---------------------------------------------------------------------------


def _loop_type_to_control_type(loop_type: str | None) -> ControlType:
    """将 LoopLedger.loop_type 映射为 ControlType 枚举。

    LoopLedger.loop_type 取值：TEMPERATURE/PRESSURE/LEVEL/FLOW/ANALYSIS/SPEED/OTHER
    ControlType 取值：FC/PC/TC/LC/CC

    SPEED/OTHER 无直接对应控制类型，回退为 FLOW（采样率 1s，最宽松）。

    Args:
        loop_type: LoopLedger.loop_type 字段值

    Returns:
        对应的 ControlType 枚举
    """
    if not loop_type:
        return ControlType.FLOW
    return _LOOP_TYPE_TO_CONTROL_TYPE.get(loop_type, ControlType.FLOW)


def _build_data_planner(db) -> DataPlanner:
    """构造 DataPlanner 实例。

    通过数据源工厂获取 Provider（tdengine / remote_api），
    支持 DATA_SOURCE_TYPE 配置切换数据源。

    Args:
        db: 异步数据库会话

    Returns:
        DataPlanner 实例
    """
    from app.core.redis import redis_client
    from app.services.cache.l1_datablock import L1DataBlockCache
    from app.services.data_source.factory import get_provider

    provider = get_provider()
    query_fn = provider.make_query_fn(db)
    cache = L1DataBlockCache(redis_client)
    assembler = MetricDataBundleAssembler()

    return DataPlanner(
        cache=cache,
        tdengine_query_fn=query_fn,
        assembler=assembler,
        db=db,
        config_loader=None,  # 使用 DataPlanner 默认配置加载器
    )


def _build_config_bundle(
    loop_id: str,
    control_type: ControlType,
) -> MetricDataBundle:
    """构造虚拟 CONFIG MetricDataBundle（ideal_settling_time 的数据源）。

    DataPlanner 跳过 CONFIG tagGroup（无时序数据），不为 ideal_settling_time
    生成 bundle。本方法构造一个虚拟 CONFIG bundle，signals 中包含 control_type
    信号，供 IdealSettlingTimeCalculator 读取。

    Args:
        loop_id: 回路 ID
        control_type: 控制类型枚举

    Returns:
        MetricDataBundle 实例（tag_group=CONFIG，signals 含 control_type）
    """
    data_block = DataBlock(
        data_block_id=f"db_{loop_id}_CONFIG_0s",
        loop_id=loop_id,
        tag_group=TagGroup.CONFIG.value,
        sampling_freq="0s",
        timestamps=[datetime.now(UTC)],
        signals={"control_type": [control_type.value]},
        validity={},
        outlier_reasons={},
        quality_summary=QualitySummary(total_count=1, valid_count=1, valid_rate=1.0),
        config_version="v1",
        preprocess_version="config_v1",
        point_count=1,
    )

    lineage = DataLineage(
        sampling_freq="0s",
        aggregation_policy="LAST",
        quality_policy="NONE",
        tag_group=TagGroup.CONFIG.value,
        data_block_ids=[data_block.data_block_id],
        valid_rate=1.0,
        data_policy_version="config_v1",
        algorithm_version=ALGORITHM_VERSION,
    )

    return MetricDataBundle(
        metric_code="ideal_settling_time",
        data_block=data_block,
        mask_expression="",
        masked_indices=[0],
        lineage=lineage,
    )


def _build_weights_map(
    type_weights: dict[str, dict] | None,
    score_type: str,
    metric_configs: dict[str, MetricConfig] | None = None,
) -> dict[str, float] | None:
    """构造 ConfidenceEvaluator 所需的权重映射。

    权重解析优先级（对齐 FDS §5.3.1 / 项目记忆约束：
    "Metric configuration weights apply to 3 core quality indicators
    (accuracy/fast/steady) with sum=100"）：

    1. **MetricConfig.weight 全局配置**（管理员通过 PUT /configs/metrics 设置）：
       若 3 个核心指标（accuracy_rate / fast_rate / steady_rate）
       的 weight 均已设置（非 null 且 > 0），则使用此全局权重（覆盖控制类型模板）。
       MetricConfig.weight sum=100（百分比），需除以 100 归一化为 a+f+s=1.0 比例。

    2. **LoopTypeWeight 控制类型模板**（按 STABLE/SLOW/FAST/LOGIC 自动套用）：
       若 MetricConfig.weight 未配置（任一为 null 或全为 0），则按控制类型回退。

    3. **DEFAULT_WEIGHTS**（ConfidenceEvaluator 内部 STABLE 模板）：
       若 LoopTypeWeight 也未配置，返回 None 由调用方回退。

    Args:
        type_weights: 回路类型权重映射 ``{score_type: {weight_a, weight_f, weight_s}}``
        score_type: 评分类型（STABLE/SLOW/FAST/LOGIC）
        metric_configs: 指标配置字典 ``{metric_code: MetricConfig}``（键为数据库列名小写）；
            None 时跳过优先级 1，直接走 LoopTypeWeight

    Returns:
        权重映射字典 ``{accuracy_rate, fast_rate, stability_rate}``；
        无配置时返回 None（ConfidenceEvaluator 使用默认权重）
    """
    # 优先级 1：MetricConfig.weight 全局配置（管理员通过 /configs/metrics 设置）
    if metric_configs:
        a_cfg = metric_configs.get("accuracy_rate")
        f_cfg = metric_configs.get("fast_rate")
        s_cfg = metric_configs.get("steady_rate")
        if (
            a_cfg
            and a_cfg.weight is not None
            and float(a_cfg.weight) > 0
            and f_cfg
            and f_cfg.weight is not None
            and float(f_cfg.weight) > 0
            and s_cfg
            and s_cfg.weight is not None
            and float(s_cfg.weight) > 0
        ):
            total = float(a_cfg.weight) + float(f_cfg.weight) + float(s_cfg.weight)
            if total > 0:
                logger.info(
                    "[权重解析] 使用 MetricConfig.weight 全局配置: a=%s f=%s s=%s "
                    "(sum=%s, 归一化后 sum=1.0)",
                    a_cfg.weight,
                    f_cfg.weight,
                    s_cfg.weight,
                    total,
                )
                return {
                    "accuracy_rate": float(a_cfg.weight) / total,
                    "fast_rate": float(f_cfg.weight) / total,
                    "stability_rate": float(s_cfg.weight) / total,
                }
            logger.warning(
                "[权重解析] MetricConfig.weight 总和为 0，回退到 LoopTypeWeight: a=%s f=%s s=%s",
                a_cfg.weight,
                f_cfg.weight,
                s_cfg.weight,
            )

    # 优先级 2：LoopTypeWeight 按控制类型模板
    if not type_weights or score_type not in type_weights:
        return None

    w = type_weights[score_type]
    logger.info(
        "[权重解析] 使用 LoopTypeWeight 控制类型模板: score_type=%s a=%s f=%s s=%s",
        score_type,
        w.get("weight_a"),
        w.get("weight_f"),
        w.get("weight_s"),
    )
    return {
        "accuracy_rate": float(w.get("weight_a", 0)),
        "fast_rate": float(w.get("weight_f", 0)),
        "stability_rate": float(w.get("weight_s", 0)),
    }


def _compute_kpis_three_layer(
    bundles: list[MetricDataBundle],
    config_bundle: MetricDataBundle,
    weights: dict[str, float] | None = None,
    metric_configs: dict[str, MetricConfig] | None = None,
) -> tuple[dict[str, MetricResult], MetricResult]:
    """三层计算流程（数据流程图 §7.1）。

    Layer 1: 无依赖指标（10 个）— 并行计算
        accuracy_rate, effective_auto_rate, good_value_rate,
        oscillation_rate, saturation_rate, stiction_index,
        output_trip_index, auto_mode_rate, settling_time,
        ideal_settling_time

    Layer 2: 有依赖指标（2 个）— 注入 Layer 1 结果后计算
        stability_rate (depends_on: oscillation_rate)
        fast_rate (depends_on: settling_time, ideal_settling_time)

    Layer 3: 综合评分 — ConfidenceEvaluator.compute_composite_score()
        P = (A·a + F·f + S·s)/(a+f+s) × R

    Args:
        bundles: DataPlanner 返回的 MetricDataBundle 列表（metric_code 为数据库列名）
        config_bundle: 虚拟 CONFIG bundle（ideal_settling_time 数据源）
        weights: 权重映射 {accuracy_rate, fast_rate, stability_rate}
        metric_configs: 指标配置字典 {metric_code: MetricConfig}（键为数据库列名小写）。
            PRD §5.1.3 / FDS §5.3.1：is_enabled 控制计算器是否执行，
            threshold 中的阈值参数注入 bundle 供计算器读取。

    Returns:
        (metric_results, composite_result)
        metric_results: {calculator_code: MetricResult}
        composite_result: 综合评分 MetricResult
    """
    # 构建 bundle 索引：{calculator_metric_code: bundle}
    # bundles 中的 metric_code 是数据库列名，需映射为计算器代码
    bundle_map: dict[str, MetricDataBundle] = {}
    for bundle in bundles:
        calc_code = _DB_TO_CALCULATOR_METRIC_CODE.get(bundle.metric_code, bundle.metric_code)
        bundle_map[calc_code] = bundle

    # 添加虚拟 CONFIG bundle（ideal_settling_time）
    bundle_map["ideal_settling_time"] = config_bundle

    results: dict[str, MetricResult] = {}

    # ── Layer 1: 无依赖指标（10 个） ──
    layer1_codes = [
        "accuracy_rate",
        "effective_auto_rate",
        "good_value_rate",
        "oscillation_rate",
        "saturation_rate",
        "stiction_index",
        "output_trip_index",
        "auto_mode_rate",
        "settling_time",
        "ideal_settling_time",
    ]
    for code in layer1_codes:
        # PRD §5.1.3：指标停用后返回 INCONCLUSIVE，不执行计算
        if not _is_metric_enabled(code, metric_configs):
            logger.info(
                "[三层计算] Layer1: 指标 %s 已禁用（is_enabled=False），返回 INCONCLUSIVE", code
            )
            results[code] = _make_disabled_result(code)
            continue
        bundle = bundle_map.get(code)
        if bundle is None:
            logger.debug("[三层计算] Layer1: 指标 %s 无 bundle，跳过", code)
            continue
        # FDS §5.3.1：注入 threshold 阈值参数到 bundle
        _inject_threshold_to_bundle(bundle, code, metric_configs)
        calculator = get_calculator(code)
        if calculator is None:
            logger.warning("[三层计算] Layer1: 指标 %s 无计算器注册，跳过", code)
            continue
        # 输入摘要：数据点数 + 有效率
        block = bundle.data_block
        logger.info(
            "[三层计算] Layer1: 指标=%s 输入 points=%d valid_rate=%.4f signals=%s",
            code,
            block.point_count,
            block.quality_summary.valid_rate if block.quality_summary else 0.0,
            list(block.signals.keys()),
        )
        results[code] = calculator.calculate(bundle)
        logger.info(
            "[三层计算] Layer1: 指标=%s 结果 value=%s confidence=%s",
            code,
            results[code].value,
            results[code].confidence_level,
        )

    # ── Layer 2: 有依赖指标（2 个） ──
    # stability_rate depends_on: oscillation_rate
    if not _is_metric_enabled("stability_rate", metric_configs):
        logger.info("[三层计算] Layer2: 指标 stability_rate 已禁用，返回 INCONCLUSIVE")
        results["stability_rate"] = _make_disabled_result("stability_rate")
    else:
        stability_bundle = bundle_map.get("stability_rate")
        if stability_bundle is not None:
            _inject_threshold_to_bundle(stability_bundle, "stability_rate", metric_configs)
            calc = get_calculator("stability_rate")
            if calc is not None:
                dep_osc = results.get("oscillation_rate")
                logger.info(
                    "[三层计算] Layer2: stability_rate 依赖 oscillation_rate=%s",
                    dep_osc.value if dep_osc else None,
                )
                calc.with_dependencies({"oscillation_rate": dep_osc})
                results["stability_rate"] = calc.calculate(stability_bundle)
                logger.info(
                    "[三层计算] Layer2: stability_rate 结果 value=%s confidence=%s",
                    results["stability_rate"].value,
                    results["stability_rate"].confidence_level,
                )

    # fast_rate depends_on: settling_time, ideal_settling_time
    if not _is_metric_enabled("fast_rate", metric_configs):
        logger.info("[三层计算] Layer2: 指标 fast_rate 已禁用，返回 INCONCLUSIVE")
        results["fast_rate"] = _make_disabled_result("fast_rate")
    else:
        fast_bundle = bundle_map.get("fast_rate")
        if fast_bundle is not None:
            _inject_threshold_to_bundle(fast_bundle, "fast_rate", metric_configs)
            calc = get_calculator("fast_rate")
            if calc is not None:
                dep_st = results.get("settling_time")
                dep_ist = results.get("ideal_settling_time")
                logger.info(
                    "[三层计算] Layer2: fast_rate 依赖 settling_time=%s ideal_settling_time=%s",
                    dep_st.value if dep_st else None,
                    dep_ist.value if dep_ist else None,
                )
                calc.with_dependencies(
                    {
                        "settling_time": dep_st,
                        "ideal_settling_time": dep_ist,
                    }
                )
                results["fast_rate"] = calc.calculate(fast_bundle)
                logger.info(
                    "[三层计算] Layer2: fast_rate 结果 value=%s confidence=%s",
                    results["fast_rate"].value,
                    results["fast_rate"].confidence_level,
                )

    # ── Layer 3: 综合评分 ──
    logger.info(
        "[三层计算] Layer3: 综合评分输入 A=%s F=%s S=%s R=%s weights=%s",
        results.get("accuracy_rate").value if results.get("accuracy_rate") else None,
        results.get("fast_rate").value if results.get("fast_rate") else None,
        results.get("stability_rate").value if results.get("stability_rate") else None,
        results.get("effective_auto_rate").value if results.get("effective_auto_rate") else None,
        weights,
    )
    composite_result = ConfidenceEvaluator.compute_composite_score(
        metric_results=results,
        weights=weights,
    )
    results["composite_score"] = composite_result
    logger.info(
        "[三层计算] Layer3: composite_score 结果 value=%s confidence=%s details=%s",
        composite_result.value,
        composite_result.confidence_level,
        composite_result.details,
    )

    return results, composite_result


def _extract_kpi_values(
    metric_results: dict[str, MetricResult],
) -> dict[str, Decimal | None]:
    """将 MetricResult 字典转换为数据库列名 → Decimal 值映射。

    计算器代码（如 fast_rate）需映射为数据库列名（如 fast_rate）。
    value 为 None 时保持 None（表示 INCONCLUSIVE）。

    Args:
        metric_results: {calculator_code: MetricResult}

    Returns:
        {db_column_name: Decimal | None}
    """
    kpi_values: dict[str, Decimal | None] = {}
    for calc_code, result in metric_results.items():
        # 跳过综合评分（不写入指标列）
        if calc_code == "composite_score":
            continue
        db_code = _CALCULATOR_TO_DB_METRIC_CODE.get(calc_code, calc_code)
        if result.value is not None:
            kpi_values[db_code] = Decimal(str(result.value))
        else:
            kpi_values[db_code] = None
    return kpi_values


def _extract_lineage_info(
    metric_results: dict[str, MetricResult],
    composite_result: MetricResult,
) -> dict[str, Any]:
    """从指标结果中提取数据血缘信息（用于 _save_snapshot）。

    优先从 accuracy_rate 的 lineage 取（BASE tagGroup，代表主数据源）；
    若不存在则从 composite_result 的 lineage 取。

    Args:
        metric_results: 指标结果字典
        composite_result: 综合评分结果

    Returns:
        含 7 个血缘字段的字典：
        algorithm_version, sampling_freq, quality_policy,
        valid_rate, confidence_level, data_lineage
    """
    # 优先从 accuracy_rate 取 lineage（BASE tagGroup 代表主数据源）
    accuracy_result = metric_results.get("accuracy_rate")
    lineage: DataLineage | None = None
    if accuracy_result is not None:
        lineage = accuracy_result.lineage
    if lineage is None and composite_result.lineage is not None:
        lineage = composite_result.lineage
    if lineage is None:
        lineage = DataLineage(algorithm_version=ALGORITHM_VERSION)

    # 可信度等级：取综合评分的 confidence_level
    confidence_level = composite_result.confidence_level or "E"

    # valid_rate: 从 lineage 取，转为 Decimal（5,4 精度）
    valid_rate_val = lineage.valid_rate
    valid_rate = (
        Decimal(str(valid_rate_val)).quantize(Decimal("0.0001"))
        if valid_rate_val is not None
        else None
    )

    return {
        "algorithm_version": lineage.algorithm_version or ALGORITHM_VERSION,
        "sampling_freq": lineage.sampling_freq or None,
        "quality_policy": lineage.quality_policy or None,
        "valid_rate": valid_rate,
        "confidence_level": confidence_level,
        "data_lineage": lineage.to_dict(),
    }


def _quantize(value: Decimal) -> Decimal:
    """量化到 2 位小数。"""
    return value.quantize(Decimal("0.01"))


async def _persist_snapshot(
    db,
    custom_task_id: str | None = None,
    **kwargs,
) -> dict:
    """快照写入分流器 — 根据任务类型写入对应快照表.

    任务策略（PRD §4.3.7 / FDS §5.3.11）：
    - custom_task_id=None → 标准任务 → 写入 kpi_snapshot_hourly（参与聚合）
    - custom_task_id=<uuid> → 自定义任务 → 写入 kpi_snapshot_custom（不参与聚合）

    Args:
        db: 异步数据库会话
        custom_task_id: 自定义任务 ID；None 表示标准任务
        **kwargs: 传给 _save_snapshot / _save_custom_snapshot 的快照字段

    Returns:
        快照字典
    """
    loop_id = kwargs.get("loop_id", "?")
    status = kwargs.get("status", "?")
    score = kwargs.get("score")
    if custom_task_id is not None:
        # 自定义任务 → 写入 kpi_snapshot_custom（不参与聚合）
        logger.info(
            "[快照分流] 自定义任务 task_id=%s loop=%s status=%s score=%s "
            "→ kpi_snapshot_custom（不参与聚合）",
            custom_task_id,
            loop_id,
            status,
            score,
        )
        # P2 #29 B6: _save_custom_snapshot 已支持 sampling_freq/quality_policy
        # 与 kpi_snapshot_hourly 对齐，自定义任务具备完整数据血缘追溯能力
        return await _save_custom_snapshot(
            db=db,
            task_id=custom_task_id,
            **kwargs,
        )
    # 标准任务 → 写入 kpi_snapshot_hourly（参与聚合）
    logger.info(
        "[快照分流] 标准任务 loop=%s status=%s score=%s → kpi_snapshot_hourly（参与聚合）",
        loop_id,
        status,
        score,
    )
    return await _save_snapshot(db=db, **kwargs)


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
    # v4.0 数据血缘字段（7 个）
    ideal_settling_time: Decimal | None = None,
    algorithm_version: str | None = None,
    sampling_freq: str | None = None,
    quality_policy: str | None = None,
    valid_rate: Decimal | None = None,
    confidence_level: str | None = None,
    data_lineage: dict | None = None,
) -> dict:
    """幂等写入快照（UPSERT：相同 loop_id + ts_start 覆盖更新）。

    使用 PostgreSQL ``INSERT ... ON CONFLICT (loop_id, ts_start) DO UPDATE``，
    数据库层保证唯一性，避免应用层 SELECT-then-INSERT 的并发竞态。

    v4.0 新增 7 个数据血缘字段，支持审计追溯：
        - ideal_settling_time: 理想稳态时间（秒）
        - algorithm_version: 算法版本号
        - sampling_freq: 数据采样频率
        - quality_policy: 质量策略
        - valid_rate: 有效数据率
        - confidence_level: 可信度等级
        - data_lineage: 数据血缘 JSON
    """
    # PostgreSQL kpi_snapshot_hourly 表使用 TIMESTAMP WITHOUT TIME ZONE，
    # 而 _do_calculate 使用 datetime.now(UTC) 生成 timezone-aware datetime。
    # 需剥离 tzinfo 避免 "can't subtract offset-naive and offset-aware datetimes" 错误。
    ts_start_naive = ts_start.replace(tzinfo=None) if ts_start.tzinfo else ts_start
    ts_end_naive = ts_end.replace(tzinfo=None) if ts_end.tzinfo else ts_end

    snapshot_id = str(uuid4())
    # 使用 PostgreSQL INSERT ... ON CONFLICT DO UPDATE（真 UPSERT）
    # 数据库层保证 (loop_id, ts_start) 唯一，避免并发竞态产生重复
    from sqlalchemy.dialects import postgresql

    insert_stmt = postgresql.insert(KpiSnapshotHourly).values(
        id=snapshot_id,
        loop_id=loop_id,
        ts_start=ts_start_naive,
        ts_end=ts_end_naive,
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
        # v4.0 数据血缘字段
        ideal_settling_time=ideal_settling_time,
        algorithm_version=algorithm_version,
        sampling_freq=sampling_freq,
        quality_policy=quality_policy,
        valid_rate=valid_rate,
        confidence_level=confidence_level,
        data_lineage=data_lineage,
    )
    # ON CONFLICT (loop_id, ts_start) DO UPDATE：覆盖除 id 外的所有字段
    update_cols = {
        "ts_end": ts_end_naive,
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
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=["loop_id", "ts_start"],
        set_=update_cols,
    )
    await db.execute(upsert_stmt)
    # 注意：不在此处 commit，由调用方统一管理事务（_do_calculate 第 794 行）
    # 这样保证单回路计算原子性：UPSERT + 后续操作一起提交或回滚

    # PostgreSQL RETURNING 可获取实际写入的 id（INSERT 时为新 id，UPDATE 时为旧 id）
    # 但 ON CONFLICT DO UPDATE 不自动 RETURNING，需用 SELECT 查询实际 id
    actual_id_result = await db.execute(
        select(KpiSnapshotHourly.id).where(
            KpiSnapshotHourly.loop_id == loop_id,
            KpiSnapshotHourly.ts_start == ts_start_naive,
        )
    )
    actual_id_row = actual_id_result.first()
    snapshot_id = str(actual_id_row[0]) if actual_id_row else snapshot_id

    logger.info(
        "[快照写入] UPSERT kpi_snapshot_hourly loop=%s ts_start=%s "
        "status=%s score=%s confidence=%s",
        loop_id,
        ts_start_naive.isoformat(),
        status,
        float(score) if score is not None else None,
        confidence_level,
    )

    return {
        "loopId": loop_id,
        "snapshotId": snapshot_id,
        "tsStart": ts_start.isoformat(),
        "tsEnd": ts_end.isoformat(),
        "status": status,
        "score": float(score) if score is not None else None,
        "algorithmVersion": algorithm_version or ALGORITHM_VERSION,
    }


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
    """幂等写入自定义任务快照到 kpi_snapshot_custom 表.

    PRD §4.3.7.B / FDS §5.3.11：自定义任务结果写入 kpi_snapshot_custom，
    通过 task_id 区分独立任务，**不参与装置级聚合**（节点聚合仅查
    kpi_snapshot_hourly）。

    P2 #29 B6：补齐 ``sampling_freq`` / ``quality_policy`` 数据血缘字段，
    与 ``kpi_snapshot_hourly`` 对齐，使自定义任务具备完整数据血缘追溯能力。

    幂等：相同 (task_id, loop_id) 不重复写入，覆盖更新（对齐
    UniqueConstraint("task_id", "loop_id")）。
    """
    from app.models.metric import KpiSnapshotCustom

    ts_start_naive = ts_start.replace(tzinfo=None) if ts_start.tzinfo else ts_start
    ts_end_naive = ts_end.replace(tzinfo=None) if ts_end.tzinfo else ts_end

    existing_result = await db.execute(
        select(KpiSnapshotCustom).where(
            KpiSnapshotCustom.task_id == task_id,
            KpiSnapshotCustom.loop_id == loop_id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        existing.ts_start = ts_start_naive
        existing.ts_end = ts_end_naive
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
        logger.info(
            "[快照写入] 覆盖更新 kpi_snapshot_custom task=%s loop=%s "
            "status=%s score=%s confidence=%s",
            task_id,
            loop_id,
            status,
            float(score) if score is not None else None,
            confidence_level,
        )
    else:
        snapshot_id = str(uuid4())
        snapshot = KpiSnapshotCustom(
            id=snapshot_id,
            task_id=task_id,
            loop_id=loop_id,
            ts_start=ts_start_naive,
            ts_end=ts_end_naive,
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
        logger.info(
            "[快照写入] 新增 kpi_snapshot_custom task=%s loop=%s status=%s score=%s confidence=%s",
            task_id,
            loop_id,
            status,
            float(score) if score is not None else None,
            confidence_level,
        )

    return {
        "loopId": loop_id,
        "taskId": task_id,
        "snapshotId": snapshot_id,
        "tsStart": ts_start.isoformat(),
        "tsEnd": ts_end.isoformat(),
        "status": status,
        "score": float(score) if score is not None else None,
        "algorithmVersion": algorithm_version or ALGORITHM_VERSION,
    }


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
    "refresh_beat_schedule",
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


async def _do_calculate_node_kpi(ts_start: datetime | None = None) -> dict:
    """执行节点级 KPI 聚合的实际 async 逻辑。

    Args:
        ts_start: 时间窗起始（UTC，naive 视为 UTC）。None 时取「上一个完整小时」，
            与回路级 _do_calculate 默认行为一致。
    """
    from app.core.db import AsyncSessionLocal
    from app.models.plant_node import PlantNode
    from app.services.node_performance import calculate_and_save_node_snapshot

    # 时间窗
    if ts_start is not None:
        ts_end = ts_start + timedelta(hours=1)
    else:
        now = datetime.now(UTC)
        ts_end = now.replace(minute=0, second=0, microsecond=0)
        ts_start = ts_end - timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        # 查询所有启用 KPI 评估的节点
        node_result = await db.execute(select(PlantNode).where(PlantNode.is_kpi_enabled.is_(True)))
        nodes = list(node_result.scalars().all())

        if not nodes:
            logger.info("无启用 KPI 评估的节点，跳过节点级聚合")
            return {"total": 0, "success": 0, "skipped": 0}

        logger.info("待聚合节点数: %d", len(nodes))

        success_count = 0
        skipped_count = 0
        for node in nodes:
            try:
                snap = await calculate_and_save_node_snapshot(
                    db=db,
                    plant_node_id=str(node.id),
                    ts_start=ts_start,
                    ts_end=ts_end,
                )
                if snap is None:
                    skipped_count += 1
                    logger.debug("节点 %s 无数据，跳过", node.name)
                else:
                    success_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("节点 %s 聚合失败: %s", node.name, exc)

        await db.commit()

    return {
        "total": len(nodes),
        "success": success_count,
        "skipped": skipped_count,
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
    }


async def _do_calculate_single_node(
    plant_node_id: str,
    ts_start: str | None = None,
    ts_end: str | None = None,
) -> dict:
    """单节点 KPI 聚合（支持指定时间段）。"""
    from app.core.db import AsyncSessionLocal
    from app.services.node_performance import calculate_and_save_node_snapshot

    now = datetime.now(UTC)
    if ts_start:
        try:
            ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
        except ValueError:
            ts_start_dt = datetime.fromisoformat(ts_start)
    else:
        ts_start_dt = (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    if ts_end:
        try:
            ts_end_dt = datetime.fromisoformat(ts_end.replace("Z", "+00:00"))
        except ValueError:
            ts_end_dt = datetime.fromisoformat(ts_end)
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


# 节点级聚合不再使用独立 Beat 调度，改为回路级任务 _do_calculate() 完成后级联触发
# calculate_node_kpi_hourly.delay()，消除时序竞态（原 node-kpi-hourly Beat 已移除）


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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
        # 上个月月初
        if now.month == 1:
            stat_month_dt = date(now.year - 1, 12, 1)
        else:
            stat_month_dt = date(now.year, now.month - 1, 1)

    return await aggregate_all_nodes_monthly(stat_month_dt)
