"""Celery tasks for KPI performance calculation (IDS v3.2 §2.3 — S3-METRIC-003).

设计要点：
- Celery Beat 定时任务（每小时触发全量计算）
- 通过数据源工厂（factory.get_provider）获取历史时序数据（PV/SP/OP/MODE/PV_QUALITY）
  - DATA_SOURCE_TYPE=remote_api: 从 AAS REST API 获取历史数据（生产模式）
  - DATA_SOURCE_TYPE=tdengine: 从本地 TDengine 获取历史数据（开发/测试模式）
- 按 metric_config 公式计算 6 大 KPI
- 计算结果写入 kpi_snapshot_hourly 快照表（PostgreSQL）
- 任务幂等（相同 loop_id + ts_start 不重复写入）
- 失败自动重试 3 次
- 数据不足返回 INCONCLUSIVE 状态
- PV 质量码为 Bad 的数据点剔除
- 数据源不可用时优雅降级（记录日志并跳过）
"""

from __future__ import annotations

import asyncio
import logging
from bisect import bisect_left
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import select

from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import KpiSnapshotHourly, MetricConfig
from app.models.tag import TagRegistry
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

# 算法版本号
ALGORITHM_VERSION = "KPI_CALC_v2.0"
ALGORITHM_VERSION_V1 = "KPI_CALC_v1.0"  # 向后兼容回退

# 数据不足阈值：Good 数据占比 < 20% 视为 INCONCLUSIVE
MIN_GOOD_RATIO = 0.20

# 并发 worker 数
CONCURRENCY = 10


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

    Args:
        ts_start: 可选，指定计算时间窗起始（ISO 格式），None 时取上一个完整小时
    """
    logger.info("KPI 计算任务开始, task_id=%s, ts_start=%s", self.request.id, ts_start)
    try:
        result = self.run_async(_do_calculate(ts_start=ts_start))
        logger.info("KPI 计算任务完成: %s", result)
        return result
    except Exception:
        logger.exception("KPI 计算任务失败")
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


# ---------------------------------------------------------------------------
# Beat 调度配置：每小时执行一次 + 每日 00:05 + 每月 1 日 00:10
# ---------------------------------------------------------------------------


from celery.schedules import crontab  # noqa: E402

# 默认计算周期（秒）— 可被 EngineRule EVAL_CALC_CYCLE 覆盖
_DEFAULT_CALC_CYCLE_SECONDS = 3600.0

_beat_entry = {
    "task": "app.tasks.kpi_calc.calculate_hourly_kpi",
    "schedule": _DEFAULT_CALC_CYCLE_SECONDS,  # 1 小时（默认值，beat_init 时从 DB 覆盖）
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


# ---------------------------------------------------------------------------
# beat_init 信号：Beat 启动时从 EngineRule 读取策略配置，动态更新调度周期
# ---------------------------------------------------------------------------

from celery.signals import beat_init  # noqa: E402


@beat_init.connect
def _apply_engine_rules(sender=None, **kwargs):
    """Beat 启动时从 DB 读取 EngineRule，动态更新 beat_schedule。

    支持 EngineRule：
    - EVAL_CALC_CYCLE (cycle_minutes): KPI 计算周期，覆盖 kpi-calc-hourly 的 schedule
    - EVAL_CALC_CYCLE.is_enabled=False: 禁用自动计算（从 beat_schedule 删除条目）

    注意：修改策略后需重启 Celery Beat 进程才能生效（前端策略页面已提示）。
    """
    import asyncio

    try:
        rules = asyncio.run(_load_engine_rules_from_db())
    except Exception as exc:
        logger.warning("beat_init: 从 DB 读取 EngineRule 失败，使用默认调度周期: %s", exc)
        return

    if not rules:
        return

    calc_cycle = rules.get("EVAL_CALC_CYCLE")
    if not calc_cycle:
        return

    is_enabled = calc_cycle.get("is_enabled", True)
    cycle_minutes = calc_cycle.get("cycle_minutes", 60)

    current_schedule = dict(celery_app.conf.beat_schedule or {})

    if not is_enabled:
        # 策略禁用 → 从 beat_schedule 删除 kpi-calc-hourly
        current_schedule.pop("kpi-calc-hourly", None)
        logger.info("beat_init: EVAL_CALC_CYCLE 已禁用，kpi-calc-hourly 调度已移除")
    else:
        # 策略启用 → 按配置的 cycle_minutes 更新周期
        cycle_seconds = float(cycle_minutes) * 60.0
        current_schedule["kpi-calc-hourly"] = {
            "task": "app.tasks.kpi_calc.calculate_hourly_kpi",
            "schedule": cycle_seconds,
        }
        logger.info(
            "beat_init: EVAL_CALC_CYCLE 已应用，kpi-calc-hourly 周期 = %s 分钟",
            cycle_minutes,
        )

    celery_app.conf.beat_schedule = current_schedule


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


async def _do_calculate(
    ts_start: str | None = None,
    loop_ids: list[str] | None = None,
    task_id: str | None = None,
    window_index: int = 0,
    total_windows: int = 0,
) -> dict:
    """执行全量 KPI 计算的实际 async 逻辑。

    Args:
        ts_start: 时间窗起始（ISO 8601，UTC）；None 时取上一个完整计算周期
        loop_ids: 回路 ID 过滤列表。None=全量；非空列表=仅这些回路；
            空列表=直接返回 0 结果（用于 backfill 精准重算）。
        task_id: Redis 任务跟踪 ID（backfill 调用时传入，用于逐回路进度更新）。
        window_index: 当前窗口序号（1-based），用于细粒度进度计算。
        total_windows: 总窗口数，用于细粒度进度计算。
    """
    from app.core.db import AsyncSessionLocal
    from app.services.data_source.factory import get_provider

    # 通过数据源工厂获取查询函数（适配 tdengine/remote_api）
    # remote_api 模式下从 AAS REST API 获取历史数据；tdengine 模式下查本地 TDengine
    query_trend_data = get_provider().query_trend_data

    # 空列表提前返回（backfill 调用时明确不需要计算任何回路）
    if loop_ids is not None and len(loop_ids) == 0:
        return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

    # 计算时间窗 — naive UTC，对齐 DB TIMESTAMP WITHOUT TIME ZONE
    now = datetime.now(UTC).replace(tzinfo=None)
    if ts_start:
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
        logger.info("已加载回路类型权重: %s", list(type_weights.keys()))

    # 3. 并发计算（信号量限制并发数，每协程独立 session 避免并发共享）
    sem = asyncio.Semaphore(CONCURRENCY)
    loops_count = len(loops)
    completed_in_window = 0  # 当前窗口已完成回路数（闭包计数器）

    async def _calc_with_sem(loop: LoopLedger) -> dict | None:
        nonlocal completed_in_window
        async with sem:
            # 每协程独立 session，避免 AsyncSession 并发共享导致的不可预期错误
            async with AsyncSessionLocal() as worker_db:
                try:
                    result = await _calculate_loop_kpi(
                        db=worker_db,
                        loop=loop,
                        metric_configs=metric_configs,
                        ts_start=ts_start_dt,
                        ts_end=ts_end_dt,
                        query_trend_fn=query_trend_data,
                        type_weights=type_weights,
                    )
                    await worker_db.commit()
                    return result
                except Exception:
                    await worker_db.rollback()
                    raise
                finally:
                    completed_in_window += 1
                    # 逐回路更新进度（backfill 调用时 task_id 非空）
                    if task_id and total_windows > 0 and loops_count > 0:
                        try:
                            await _update_backfill_progress(
                                task_id,
                                window_index,
                                total_windows,
                                completed_in_window,
                                loops_count,
                            )
                        except Exception:
                            logger.warning(
                                "逐回路进度更新失败: task_id=%s, window=%d/%d, loop=%d/%d",
                                task_id,
                                window_index,
                                total_windows,
                                completed_in_window,
                                loops_count,
                                exc_info=True,
                            )

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
        "ts_start": ts_start_dt.isoformat(),
        "ts_end": ts_end_dt.isoformat(),
    }


async def _do_calculate_single_loop(loop_id: str, ts_start: str | None = None) -> dict:
    """单回路 KPI 计算。"""
    from app.core.db import AsyncSessionLocal
    from app.services.data_source.factory import get_provider

    # 通过数据源工厂获取查询函数（适配 tdengine/remote_api）
    query_trend_data = get_provider().query_trend_data

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
        loop = result.scalar_one_or_none()
        if loop is None:
            return {"loopId": loop_id, "status": "FAILED", "error": "回路不存在"}

        # 时间窗 — naive UTC，对齐 DB TIMESTAMP WITHOUT TIME ZONE
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
        ts_end_dt = ts_start_dt + timedelta(hours=1)

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
            query_trend_fn=query_trend_data,
            type_weights=type_weights,
        )
        await db.commit()
        return snap or {"loopId": loop_id, "status": "FAILED"}


async def _calculate_loop_kpi(
    db,
    loop: LoopLedger,
    metric_configs: dict[str, MetricConfig],
    ts_start: datetime,
    ts_end: datetime,
    query_trend_fn,
    type_weights: dict[str, dict] | None = None,
) -> dict | None:
    """计算单回路 KPI 并写入快照（幂等）。

    Args:
        db: 异步数据库会话
        loop: 回路对象
        metric_configs: 指标配置字典 {metric_code: MetricConfig}
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        query_trend_fn: 历史数据查询函数（由 factory.get_provider 路由，注入便于测试）
        type_weights: 回路类型权重映射（v2 算法用），None 时回退 v1

    Returns:
        快照字典，包含 status 字段
    """
    # 查询回路 Tag 关联
    m_result = await db.execute(
        select(LoopTagMapping).where(LoopTagMapping.loop_id == str(loop.id))
    )
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    # 查询 Tag 详情
    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 获取 PV/SP/OP/MODE 的 tag_name
    pv_tag_name = _get_tag_name(mappings, tags_map, "PV")
    sp_tag_name = _get_tag_name(mappings, tags_map, "SP")
    op_tag_name = _get_tag_name(mappings, tags_map, "OP")
    mode_tag_name = _get_tag_name(mappings, tags_map, "MODE")

    if not pv_tag_name or not sp_tag_name:
        # 缺少必要 Tag，无法计算
        snap = await _save_snapshot(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )
        return snap

    # 从 TDengine 拉取时序数据
    start_iso = ts_start.isoformat()
    end_iso = ts_end.isoformat()

    try:
        pv_data = await query_trend_fn(pv_tag_name, start_iso, end_iso)
        sp_data = await query_trend_fn(sp_tag_name, start_iso, end_iso) if sp_tag_name else []
        op_data = await query_trend_fn(op_tag_name, start_iso, end_iso) if op_tag_name else []
        mode_data = await query_trend_fn(mode_tag_name, start_iso, end_iso) if mode_tag_name else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("TDengine 查询失败（回路 %s 跳过）: %s", loop.tag_name, exc)
        snap = await _save_snapshot(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )
        return snap

    # 剔除 PV 质量码为 Bad 的数据点
    pv_data_filtered = [d for d in pv_data if str(d.get("quality", "GOOD")).upper() != "BAD"]

    # 数据不足判定
    total_points = len(pv_data)
    good_points = len(pv_data_filtered)
    if total_points == 0 or good_points / max(total_points, 1) < MIN_GOOD_RATIO:
        snap = await _save_snapshot(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )
        return snap

    # 好值率：在过滤前计算，反映真实数据质量
    good_value_rate = Decimal(good_points) / Decimal(total_points) * Decimal("100")

    # 按 ts 对齐 PV/SP/OP/MODE
    aligned = _align_timeseries(pv_data_filtered, sp_data, op_data, mode_data)
    if not aligned:
        snap = await _save_snapshot(
            db=db,
            loop_id=str(loop.id),
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )
        return snap

    # 计算 6 大 KPI（好值率在过滤前计算，其余指标基于过滤后数据）
    kpi_values = _compute_kpis(aligned, metric_configs, good_value_rate=good_value_rate)

    # 故障诊断扩展指标（基于原始时序数据，简化实现）
    kpi_values["stiction_coeff"] = _calc_stiction_coeff(op_data, mode_data)
    kpi_values["steady_state_time"] = _calc_steady_state_time(pv_data_filtered, sp_data)
    kpi_values["output_travel_index"] = _calc_output_travel_index(op_data)

    # 计算综合评分 — v2 按回路类型加权（对齐国标 GB/T 44693.2-2024）
    # P = [(A*a)+(F*f)+(S*s)]/(a+f+s) * R
    from app.services.loop_config import infer_score_type

    score_type = infer_score_type(loop.loop_type)
    score = _compute_composite_score_v2(kpi_values, type_weights, score_type)

    # 判定状态
    status = "SUCCESS"
    # 如果某些 KPI 缺失（None），状态降级为 PARTIAL
    required_kpis = ("good_value_rate", "auto_mode_rate", "steady_rate")
    if any(kpi_values.get(k) is None for k in required_kpis):
        status = "PARTIAL"

    snap = await _save_snapshot(
        db=db,
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
        fast_response_rate=kpi_values.get("fast_response_rate"),
        oscillation_rate=kpi_values.get("oscillation_rate"),
        saturation_rate=kpi_values.get("saturation_rate"),
        stiction_coeff=kpi_values.get("stiction_coeff"),
        steady_state_time=kpi_values.get("steady_state_time"),
        output_travel_index=kpi_values.get("output_travel_index"),
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


def _align_timeseries(
    pv_data: list[dict],
    sp_data: list[dict],
    op_data: list[dict],
    mode_data: list[dict],
) -> list[dict[str, Any]]:
    """按 ts 对齐 PV/SP/OP/MODE 时序数据。

    对齐策略：
    1. 优先精确时间戳匹配（兼容字符串 ts 如 "t1"）
    2. 若 ts 可转为数值，使用 bisect 最近邻匹配，容差 ±500ms
    """
    # 精确映射（兼容字符串 ts）
    sp_map = {d.get("ts"): d.get("value") for d in sp_data}
    op_map = {d.get("ts"): d.get("value") for d in op_data}
    mode_map = {d.get("ts"): d.get("value") for d in mode_data}

    # 数值索引（用于容差匹配）
    sp_ts_floats, sp_ts_orig = _build_ts_index(sp_data)
    op_ts_floats, op_ts_orig = _build_ts_index(op_data)
    mode_ts_floats, mode_ts_orig = _build_ts_index(mode_data)
    sp_values = [sp_map[t] for t in sp_ts_orig] if sp_ts_floats else None
    op_values = [op_map[t] for t in op_ts_orig] if op_ts_floats else None
    mode_values = [mode_map[t] for t in mode_ts_orig] if mode_ts_floats else None

    aligned: list[dict[str, Any]] = []
    for d in pv_data:
        ts = d.get("ts")
        pv = d.get("value")
        aligned.append(
            {
                "ts": ts,
                "pv": pv,
                "sp": _find_nearest_value(ts, sp_ts_floats, sp_map, sp_values),
                "op": _find_nearest_value(ts, op_ts_floats, op_map, op_values),
                "mode": _find_nearest_value(ts, mode_ts_floats, mode_map, mode_values),
            }
        )
    return aligned


def _compute_kpis(
    aligned: list[dict[str, Any]],
    metric_configs: dict[str, MetricConfig],
    good_value_rate: Decimal | None = None,
) -> dict[str, Decimal | None]:
    """计算 7 大 KPI（对齐 GB/T 44693.2-2024）。

    Args:
        aligned: 对齐后的时序数据（已过滤 Bad 质量码）
        metric_configs: 指标配置字典
        good_value_rate: 好值率（在过滤前计算，反映真实数据质量）。
            None 时默认 100.0（向后兼容）。

    Returns:
        {metric_code: Decimal value or None}

    KPI 列表：
        - good_value_rate: 好值率（仅显示，不参与综合评分加权）
        - auto_mode_rate: 自控率（参与加权）
        - effective_auto_rate: 有效自控率（作为乘数因子 R_auto）
        - steady_rate: 平稳率（参与加权）
        - accuracy_rate: 准确率（参与加权）
        - fast_response_rate: 快速率（参与加权）
        - oscillation_rate: 振荡率（参与加权）
        - saturation_rate: 饱和率（参与加权）
    """
    total = len(aligned)
    if total == 0:
        return dict.fromkeys(
            (
                "good_value_rate",
                "auto_mode_rate",
                "effective_auto_rate",
                "steady_rate",
                "accuracy_rate",
                "fast_response_rate",
                "oscillation_rate",
                "saturation_rate",
            )
        )

    # 好值率：在过滤前计算，反映真实数据质量（由调用方传入）
    good_value_rate_val = good_value_rate if good_value_rate is not None else Decimal("100.0")

    # 自控率：sum(mode in [Auto, Cascade]) / count(*) * 100
    # mode 值：0=Manual, 1=Auto, 2/3=Cascade
    auto_count = sum(1 for d in aligned if d.get("mode") is not None and _is_auto_mode(d["mode"]))
    auto_mode_rate = Decimal(auto_count) / Decimal(total) * Decimal("100")

    # 有效自控率 R = AutoRealTime / AllTime × 100
    # 国标 B.2：自控状态下输出不饱和且控制有效的时长占比
    # pv_quality 质量码兼容两种约定：TDengine schema (1=Good) 和 OPC DA (192=Good)
    effective_auto_count = sum(
        1
        for d in aligned
        if d.get("mode") is not None
        and _is_auto_mode(d["mode"])
        and d.get("op") is not None
        and 5 < d["op"] < 95  # 输出不饱和（非限位）
        and _is_good_quality(d.get("pv_quality", 1))  # PV 质量码为 Good
    )
    effective_auto_rate = Decimal(effective_auto_count) / Decimal(total) * Decimal("100")

    # 振荡率：IAE 零交叉相似率法（对齐 GB/T 44693.2-2024 附录 F.1）
    # 需在平稳率之前计算，因为平稳率公式依赖振荡率
    oscillation_rate, is_oscillating, osc_period = _compute_oscillation_rate(aligned)
    osc_ratio = float(oscillation_rate) / 100.0

    # 平稳率：对齐 GB/T 44693.2-2024 附录 B.5
    # 公式: S = max(0, (1-Osc-k×σ_norm)/(1-Osc)) × 100
    # 其中: σ_norm = σ/U (偏差标准差/量程), Osc = 振荡率(0-1), k=10
    pv_sp_pairs = [
        (d["pv"], d["sp"]) for d in aligned if d.get("pv") is not None and d.get("sp") is not None
    ]
    if pv_sp_pairs:
        errors = [pv - sp for pv, sp in pv_sp_pairs]
        sp_values = [sp for _, sp in pv_sp_pairs]
        sigma = float(np.std(errors)) if len(errors) > 1 else 0.0
        # U = SP 量程（max - min），SP 不变时用 PV 量程兜底
        sp_span = max(sp_values) - min(sp_values)
        if sp_span <= 0:
            pv_vals = [pv for pv, _ in pv_sp_pairs]
            sp_span = max(pv_vals) - min(pv_vals) if len(pv_vals) > 1 else 1.0
        if sp_span <= 0:
            sp_span = 1.0

        # GB/T 44693.2 B.5: max(0, (1-Osc-k×σ_norm)/(1-Osc)) × 100
        sigma_norm = sigma / sp_span
        k = 10.0
        if osc_ratio < 1.0:
            steady_val = (
                max(
                    0.0,
                    (1.0 - osc_ratio - k * sigma_norm) / (1.0 - osc_ratio),
                )
                * 100
            )
        else:
            steady_val = 0.0
        steady_rate = Decimal(str(steady_val))
        steady_rate = max(Decimal("0"), min(Decimal("100"), steady_rate))

        # ── 日志：记录平稳率中间计算值 ──
        logger.debug(
            "[平稳率] σ=%.6f, U(sp_span)=%.4f, σ_norm=%.6f, "
            "osc_ratio=%.4f, k=%.1f, steady_rate=%.2f",
            sigma,
            sp_span,
            sigma_norm,
            osc_ratio,
            k,
            float(steady_rate),
        )
    else:
        steady_rate = None

    # 准确率：对齐 GB/T 44693.2-2024 附录 B.3
    # 公式: A = (1 - |Ē| / |E|max) × 100
    # 其中: |Ē| = 偏差绝对值均值, |E|max = 偏差绝对值最大值
    if pv_sp_pairs:
        abs_errors = [abs(pv - sp) for pv, sp in pv_sp_pairs]
        mean_abs_error = sum(abs_errors) / len(abs_errors)
        max_abs_error = max(abs_errors) if abs_errors else 0.0

        if max_abs_error <= 0:
            # 所有偏差为 0，准确率 100%
            accuracy_rate = Decimal("100")
        else:
            accuracy_val = (1 - mean_abs_error / max_abs_error) * 100
            accuracy_rate = Decimal(str(accuracy_val))
            accuracy_rate = max(Decimal("0"), min(Decimal("100"), accuracy_rate))

        # ── 日志：记录准确率中间计算值 ──
        logger.debug(
            "[准确率] |Ē|=%.6f, |E|max=%.6f, 比值=%.4f, accuracy_rate=%.2f",
            mean_abs_error,
            max_abs_error,
            mean_abs_error / max_abs_error if max_abs_error > 0 else 0.0,
            float(accuracy_rate),
        )
    else:
        accuracy_rate = None

    # 快速率：对齐 GB/T 44693.2-2024 附录 B.4 + F.4
    # F = 理想稳态时间 / 实际稳态时间 × 100
    # 实际稳态时间基于 ARMA Green 函数计算
    pv_vals = [d["pv"] for d in aligned if d.get("pv") is not None]
    pv_range = max(pv_vals) - min(pv_vals) if len(pv_vals) > 1 else 1.0
    if pv_range == 0:
        pv_range = 1.0
    fast_response_rate = _compute_fast_response_rate(aligned, pv_range)

    # 饱和率：duration(op >= 95 OR op <= 5) / duration(*) * 100
    saturation_count = sum(
        1 for d in aligned if d.get("op") is not None and (d["op"] >= 95 or d["op"] <= 5)
    )
    saturation_rate = Decimal(saturation_count) / Decimal(total) * Decimal("100")

    # ── 日志：记录全部 KPI 计算结果汇总 ──
    result = {
        "good_value_rate": _quantize(good_value_rate_val),
        "auto_mode_rate": _quantize(auto_mode_rate),
        "effective_auto_rate": _quantize(effective_auto_rate),
        "steady_rate": _quantize(steady_rate) if steady_rate is not None else None,
        "accuracy_rate": _quantize(accuracy_rate) if accuracy_rate is not None else None,
        "fast_response_rate": _quantize(fast_response_rate),
        "oscillation_rate": _quantize(oscillation_rate),
        "saturation_rate": _quantize(saturation_rate),
    }
    logger.debug(
        "[KPI计算] 汇总: total=%d, gvr=%.2f, amr=%.2f, ear=%.2f, sr=%s, ar=%s, "
        "frr=%.2f, or=%.2f, sat=%.2f",
        total,
        float(result["good_value_rate"]),
        float(result["auto_mode_rate"]),
        float(result["effective_auto_rate"]),
        float(result["steady_rate"]) if result["steady_rate"] else "None",
        float(result["accuracy_rate"]) if result["accuracy_rate"] else "None",
        float(result["fast_response_rate"]),
        float(result["oscillation_rate"]),
        float(result["saturation_rate"]),
    )
    return result


# ---------------------------------------------------------------------------
# 故障诊断扩展指标（简化实现，后续可优化算法精度）
# ---------------------------------------------------------------------------


def _calc_stiction_coeff(
    op_data: list[dict],
    mode_data: list[dict],
) -> Decimal | None:
    """黏滞系数计算（0-100，0=无黏滞）。

    简化算法：统计 OP 一阶差分方向变化频率。阀门黏滞会导致 OP 呈锯齿波，
    方向反转频繁；反转频率越高，黏滞越严重。

    Args:
        op_data: OP 时序数据（list[dict]，含 ts/value）
        mode_data: MODE 时序数据，用于筛选自动模式下的 OP（为空时用全部 OP）

    Returns:
        Decimal(0-100)，数据不足返回 None
    """
    if not op_data or len(op_data) < 3:
        return None

    # 筛选自动模式下的 OP 数据（mode_data 为空时用全部 OP）
    if mode_data:
        mode_map = {d.get("ts"): d.get("value") for d in mode_data}
        op_values: list[float] = []
        for d in op_data:
            v = d.get("value")
            if v is None:
                continue
            mode = mode_map.get(d.get("ts"))
            if mode is not None and not _is_auto_mode(mode):
                continue
            op_values.append(float(v))
    else:
        op_values = [float(d["value"]) for d in op_data if d.get("value") is not None]

    n = len(op_values)
    if n < 3:
        return None

    # OP 一阶差分方向变化次数
    diffs = np.diff(op_values)
    direction_changes = 0
    for i in range(1, len(diffs)):
        if diffs[i - 1] * diffs[i] < 0:
            direction_changes += 1

    # 方向变化频率 = 方向变化次数 / (n-2)，归一化到 0-100
    reversal_rate = direction_changes / max(n - 2, 1)
    stiction = min(reversal_rate * 100, 100.0)

    logger.debug(
        "[黏滞系数] OP 点数=%d, 方向变化次数=%d, reversal_rate=%.4f, stiction=%.2f",
        n,
        direction_changes,
        reversal_rate,
        stiction,
    )
    return _quantize(Decimal(str(stiction)))


def _calc_steady_state_time(
    pv_data: list[dict],
    sp_data: list[dict],
) -> Decimal | None:
    """稳态时间计算（秒）。

    算法：PV 与 SP 偏差在 ±2% 范围内的时间占比 × 时间窗总时长（秒）。
    偏差阈值取 |SP| 的 2%；SP 为 0 时取 PV 量程的 2% 兜底。

    Args:
        pv_data: PV 时序数据（list[dict]，含 ts/value）
        sp_data: SP 时序数据

    Returns:
        Decimal（秒），数据不足返回 None
    """
    if not pv_data or not sp_data:
        return None

    # 对齐 PV/SP（复用容差匹配逻辑）
    sp_map = {d.get("ts"): d.get("value") for d in sp_data}
    sp_ts_floats, sp_ts_orig = _build_ts_index(sp_data)
    sp_values = [sp_map[t] for t in sp_ts_orig] if sp_ts_floats else None

    pairs: list[tuple[float, float, float]] = []  # (ts_float, pv, sp)
    for d in pv_data:
        pv = d.get("value")
        if pv is None:
            continue
        ts = d.get("ts")
        sp = _find_nearest_value(ts, sp_ts_floats, sp_map, sp_values)
        if sp is None:
            continue
        ts_f = _ts_to_float(ts)
        pairs.append((ts_f if ts_f is not None else 0.0, float(pv), float(sp)))

    if len(pairs) < 2:
        return None

    # 计算时间窗时长（秒）：优先用时间戳差值，无法解析时按点数 × 1s 兜底
    ts_floats = [p[0] for p in pairs]
    window_duration = (
        max(ts_floats) - min(ts_floats) if max(ts_floats) > min(ts_floats) else len(pairs) * 1.0
    )

    # PV 量程兜底（SP 为 0 时用）
    pv_vals = [p[1] for p in pairs]
    pv_span = max(pv_vals) - min(pv_vals) if len(pv_vals) > 1 else 1.0
    if pv_span <= 0:
        pv_span = 1.0

    # 偏差在 ±2% 范围内的点数
    in_band = 0
    for _, pv, sp in pairs:
        threshold = abs(sp) * 0.02 if abs(sp) > 1e-9 else pv_span * 0.02
        if abs(pv - sp) <= threshold:
            in_band += 1

    steady_ratio = in_band / len(pairs)
    steady_time = steady_ratio * window_duration

    logger.debug(
        "[稳态时间] 对齐点数=%d, in_band=%d, window=%.1fs, steady_time=%.2f",
        len(pairs),
        in_band,
        window_duration,
        steady_time,
    )
    return _quantize(Decimal(str(steady_time)))


def _calc_output_travel_index(op_data: list[dict]) -> Decimal | None:
    """输出值行程指数计算（0-100）。

    算法：OP 值变化总行程 / (时间窗时长 × 理论最大变化率)，归一化到 0-100。
    理论最大变化率取 100（OP 量程 0-100，每秒最大变化 100）。

    Args:
        op_data: OP 时序数据（list[dict]，含 ts/value）

    Returns:
        Decimal(0-100)，数据不足返回 None
    """
    if not op_data or len(op_data) < 2:
        return None

    op_points: list[tuple[float, float]] = []  # (ts_float, op_value)
    for d in op_data:
        v = d.get("value")
        if v is None:
            continue
        ts_f = _ts_to_float(d.get("ts"))
        op_points.append((ts_f if ts_f is not None else 0.0, float(v)))

    if len(op_points) < 2:
        return None

    op_values = [p[1] for p in op_points]
    # OP 总行程 = Σ|Δop|
    diffs = np.diff(op_values)
    total_travel = float(np.sum(np.abs(diffs)))

    # 时间窗时长（秒）
    ts_floats = [p[0] for p in op_points]
    window_duration = (
        max(ts_floats) - min(ts_floats) if max(ts_floats) > min(ts_floats) else len(op_points) * 1.0
    )

    # 理论最大变化率：OP 范围 0-100，每秒最大变化 100
    theoretical_max_rate = 100.0
    max_possible = window_duration * theoretical_max_rate
    if max_possible <= 0:
        return None

    travel_index = min(total_travel / max_possible * 100, 100.0)

    logger.debug(
        "[行程指数] OP 点数=%d, total_travel=%.4f, window=%.1fs, travel_index=%.2f",
        len(op_points),
        total_travel,
        window_duration,
        travel_index,
    )
    return _quantize(Decimal(str(travel_index)))


def _is_auto_mode(mode_value: Any) -> bool:
    """判断 mode 值是否为 Auto 或 Cascade。"""
    try:
        v = int(float(mode_value))
        return v in (1, 2, 3)
    except (ValueError, TypeError):
        return False


def _is_good_quality(pv_quality: Any) -> bool:
    """判断 PV 质量码是否为 Good。

    兼容两种约定：
        - TDengine schema: 1 = Good
        - OPC DA: 192 (0xC0) = Good
    缺省值（None）视为 Good（容错）。
    """
    if pv_quality is None:
        return True
    try:
        v = int(float(pv_quality))
        return v in (1, 192)
    except (ValueError, TypeError):
        return False


def _compute_oscillation_rate(
    aligned: list[dict[str, Any]],
) -> tuple[Decimal, bool, float | None]:
    """计算振荡率 — IAE 零交叉相似率法（对齐 GB/T 44693.2-2024 附录 F.1）。

    算法步骤：
        1. 计算控制偏差 E = PV - SP
        2. 识别零交叉点（偏差符号变化时刻）
        3. 计算相邻零交叉间的 IAE（积分绝对误差）
        4. 分别对正值段/负值段计算面积相似率 + 持续时间相似率
        5. 振荡率 = min(面积相似率) × 100

    Returns:
        (oscillation_rate, is_oscillating, oscillation_period)
    """
    pv_sp = [
        (d["pv"], d["sp"]) for d in aligned if d.get("pv") is not None and d.get("sp") is not None
    ]
    n = len(pv_sp)

    logger.debug("[振荡率] 输入: 总点数=%d, 有效PV-SP对=%d", len(aligned), n)

    if n < 4:
        logger.debug("[振荡率] 有效点数 < 4，返回 0（数据不足）")
        return Decimal("0"), False, None

    errors = np.array([pv - sp for pv, sp in pv_sp], dtype=float)

    # 步骤 2：识别零交叉点
    zero_crossings: list[int] = []
    for i in range(1, n):
        if errors[i - 1] * errors[i] < 0:
            zero_crossings.append(i)
        elif errors[i - 1] == 0 and errors[i] != 0:
            zero_crossings.append(i)

    logger.debug("[振荡率] 零交叉点数=%d", len(zero_crossings))

    if len(zero_crossings) < 4:
        logger.debug("[振荡率] 零交叉点 < 4（不足 2 个周期），返回 0")
        return Decimal("0"), False, None

    # 步骤 3：计算相邻零交叉间的 IAE
    segments: list[tuple[float, float, int]] = []
    prev_cross = 0
    for cross in zero_crossings + [n]:
        seg = errors[prev_cross:cross]
        if len(seg) == 0:
            prev_cross = cross
            continue
        iae = float(np.sum(np.abs(seg)))
        duration = float(cross - prev_cross)
        sign = 1 if np.mean(seg) > 0 else -1
        segments.append((iae, duration, sign))
        prev_cross = cross

    pos_iae = [s[0] for s in segments if s[2] > 0]
    neg_iae = [s[0] for s in segments if s[2] < 0]

    if not pos_iae or not neg_iae:
        logger.debug("[振荡率] 正值段或负值段为空，返回 0")
        return Decimal("0"), False, None

    # 步骤 4：计算相似率（最小距离法）
    def _similarity(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        arr = np.array(values)
        best_j = 0
        best_dist = float("inf")
        for j in range(len(arr)):
            dist = float(np.sum((arr - arr[j]) ** 2))
            if dist < best_dist:
                best_dist = dist
                best_j = j
        avg = arr[best_j]
        if avg == 0:
            return 0.0
        cleaned = arr[(np.abs(arr / avg) >= 0.05) & (np.abs(arr / avg) <= 15)]
        if len(cleaned) == 0:
            return 0.0
        new_avg = float(np.mean(cleaned))
        similarity = 1.0 - abs(min(new_avg, float(avg)) - float(avg)) / abs(float(avg))
        return max(0.0, min(1.0, similarity))

    s_a = _similarity(pos_iae)
    s_b = _similarity(neg_iae)

    # 步骤 5：综合振荡率
    osc_value = min(s_a, s_b) * 100
    is_osc = s_a >= 0.4 and s_b >= 0.4

    period = None
    if is_osc and len(zero_crossings) >= 3:
        intervals = [
            zero_crossings[i + 1] - zero_crossings[i] for i in range(len(zero_crossings) - 1)
        ]
        period = float(np.median(intervals)) * 2

    logger.debug(
        "[振荡率] s_a(正面积相似率)=%.4f, s_b(负面积相似率)=%.4f, "
        "osc_rate=%.2f, is_osc=%s, period=%s",
        s_a,
        s_b,
        osc_value,
        is_osc,
        f"{period:.1f}s" if period else "None",
    )

    return _quantize(Decimal(str(osc_value))), is_osc, period


def _compute_fast_response_rate(
    aligned: list[dict[str, Any]],
    pv_range: float = 1.0,
) -> Decimal:
    """计算快速率 F = 理想稳态时间 / 实际稳态时间 × 100。

    对齐 GB/T 44693.2-2024 附录 B.4 + F.4。

    算法：
        1. 提取 PV 偏差序列（PV - SP，去均值）
        2. ARMA(p,q) 模型辨识 → Green 函数（单位脉冲响应）
        3. 实际稳态时间 = Green 函数衰减到 5% 的时刻
        4. 理想稳态时间 = 按控制类型取默认值
        5. F = min(理想 / 实际, 1.0) × 100
    """
    from app.tasks.arma import compute_ideal_settling_time, compute_settling_time

    pv_sp = [
        (d["pv"], d["sp"]) for d in aligned if d.get("pv") is not None and d.get("sp") is not None
    ]

    logger.debug("[快速率] 输入: 点数=%d, pv_range=%.4f", len(pv_sp), pv_range)

    if len(pv_sp) < 30:
        logger.debug("[快速率] 数据不足（%d < 30），返回 100（不惩罚）", len(pv_sp))
        return Decimal("100.0")

    # 偏差序列（PV - SP），去均值
    errors = np.array([pv - sp for pv, sp in pv_sp], dtype=float)
    errors = errors - np.mean(errors)

    if np.std(errors) < 1e-9:
        logger.debug("[快速率] 偏差恒定，返回 100（已处于稳态）")
        return Decimal("100.0")

    # ARMA 辨识 + Green 函数 → 实际稳态时间
    actual_settling = compute_settling_time(errors, sample_interval_sec=1.0, threshold=0.05)

    logger.debug("[快速率] 实际稳态时间=%.1f 秒", actual_settling)

    if actual_settling <= 0:
        logger.debug("[快速率] 稳态时间=0，返回 100")
        return Decimal("100.0")

    # 理想稳态时间
    ideal_settling = compute_ideal_settling_time(pv_range, "STABLE")
    fast_rate = min(ideal_settling / actual_settling, 1.0) * 100

    logger.debug(
        "[快速率] 理想稳态时间=%.1f, 实际=%.1f, fast_rate=%.2f",
        ideal_settling,
        actual_settling,
        fast_rate,
    )

    return _quantize(Decimal(str(fast_rate)))


def _compute_composite_score(
    kpi_values: dict[str, Decimal | None],
    metric_configs: dict[str, MetricConfig],
) -> Decimal:
    """计算综合评分 P = (Σ λᵢ × ηᵢ) / (Σ λᵢ) × 100（对齐 GB/T 44693.2-2024）。

    国标 4 分项指标加法关系：
        P = (λA·A + λF·F + λS·S + λR·R) / (λA + λF + λS + λR)

    - A = accuracy_rate（准确率）
    - F = fast_response_rate（快速率）
    - S = steady_rate（平稳率）
    - R = effective_auto_rate（有效自控率，平等参与加权，不再作为乘数）

    不参与评分：好值率（仅显示）、自控率（仅显示）、振荡率/饱和率（已并入平稳率）
    缺失指标按权重 0 处理（仅启用且配置了权重的指标参与）。
    """
    # ── 日志：记录输入参数 ──
    logger.debug(
        "[综合评分] 输入 KPI 值: %s",
        {k: float(v) if v is not None else None for k, v in kpi_values.items()},
    )

    # 国标 4 分项指标（全部为"越高越好"，无需反向归一化）
    score_metrics = (
        "accuracy_rate",  # A 准确率
        "fast_response_rate",  # F 快速率
        "steady_rate",  # S 平稳率
        "effective_auto_rate",  # R 有效自控率
    )

    weighted_sum = Decimal("0")
    weight_total = Decimal("0")
    weight_details: list[str] = []

    for code in score_metrics:
        value = kpi_values.get(code)
        if value is None:
            logger.debug("[综合评分] 指标 %s: 跳过（值为 None）", code)
            continue
        config = metric_configs.get(code)
        if not config or not config.is_enabled or config.weight is None:
            logger.debug(
                "[综合评分] 指标 %s: 跳过（config=%s, enabled=%s, weight=%s）",
                code,
                bool(config),
                config.is_enabled if config else None,
                config.weight if config else None,
            )
            continue
        # 精度保护：确保 value 和 weight 均为 Decimal，防止 float 混入导致精度丢失
        if not isinstance(value, Decimal):
            logger.debug(
                "[综合评分] 指标 %s: value 非 Decimal（%s），转换为 Decimal",
                code,
                type(value).__name__,
            )
            value = Decimal(str(value))
        w = config.weight
        if not isinstance(w, Decimal):
            logger.debug(
                "[综合评分] 指标 %s: weight 非 Decimal（%s），转换为 Decimal",
                code,
                type(w).__name__,
            )
            w = Decimal(str(w))
        # 归一化到 [0, 1]（4 指标均为正向：值/100）
        eta_norm = value / Decimal("100")
        eta_norm = max(Decimal("0"), min(Decimal("1"), eta_norm))
        contribution = w * eta_norm
        weighted_sum += contribution
        weight_total += w
        weight_details.append(
            f"{code}: value={float(value):.2f}, weight={float(w):.2f}, "
            f"eta_norm={float(eta_norm):.4f}(正向), contribution={float(contribution):.4f}"
        )

    # ── 日志：记录各指标加权明细 ──
    for detail in weight_details:
        logger.debug("[综合评分] 加权明细 → %s", detail)

    if weight_total <= 0:
        logger.warning("[综合评分] 所有权重总和为 0，无法计算评分，返回 0")
        return Decimal("0.00")

    # P = (Σ λᵢ × ηᵢ) / (Σ λᵢ) × 100（加法关系，R 平等参与加权）
    score = weighted_sum / weight_total * Decimal("100")
    # 精度日志：记录 weighted_sum 和 weight_total 的有效精度位数
    logger.debug(
        "[综合评分] weighted_sum=%s (digits=%d), weight_total=%s (digits=%d), score=%.6f",
        weighted_sum,
        len(weighted_sum.as_tuple().digits),
        weight_total,
        len(weight_total.as_tuple().digits),
        float(score),
    )

    result = _quantize(score)
    logger.debug("[综合评分] 最终评分: %.2f", float(result))
    return result


def _compute_composite_score_v2(
    kpi_values: dict[str, Decimal | None],
    type_weights: dict[str, dict] | None,
    score_type: str,
) -> Decimal:
    """计算综合评分 v2 — 按回路类型加权（对齐 GB/T 44693.2-2024 附表1）。

    国标公式：P = [(A*a)+(F*f)+(S*s)]/(a+f+s) * R

    - A = accuracy_rate（准确率）
    - F = fast_response_rate（快速率）
    - S = steady_rate（平稳率）
    - R = effective_auto_rate（有效自控率，作为乘数）
    - a/f/s = 按 score_type 查 loop_type_weight 获取

    与 v1 的区别：
    - v1：4 指标平等加权，权重来自 metric_config
    - v2：3 指标按回路类型加权，R 作为乘数，权重来自 loop_type_weight

    缺失指标按权重 0 处理（该指标不参与，但分母仍含其权重）。
    若 type_weights 无配置或 score_type 未找到，回退到 v1 逻辑。

    Args:
        kpi_values: KPI 值字典
        type_weights: {score_type: {weight_a, weight_f, weight_s}} 映射
        score_type: 回路评分类型（STABLE/SLOW/FAST/LOGIC）

    Returns:
        综合评分（Decimal，2 位小数）
    """
    # 回退：无类型权重配置时用 v1 的平等加权
    if not type_weights or score_type not in type_weights:
        logger.debug("[综合评分v2] score_type=%s 无权重配置，回退平等加权", score_type)
        # 平等加权：a=f=s=1/3，R 作为乘数
        a = f = s = Decimal("0.3333")
    else:
        w = type_weights[score_type]
        a = w["weight_a"] if isinstance(w["weight_a"], Decimal) else Decimal(str(w["weight_a"]))
        f = w["weight_f"] if isinstance(w["weight_f"], Decimal) else Decimal(str(w["weight_f"]))
        s = w["weight_s"] if isinstance(w["weight_s"], Decimal) else Decimal(str(w["weight_s"]))

    A = kpi_values.get("accuracy_rate")
    F = kpi_values.get("fast_response_rate")
    S = kpi_values.get("steady_rate")
    R = kpi_values.get("effective_auto_rate")

    logger.debug(
        "[综合评分v2] score_type=%s, a=%s, f=%s, s=%s, A=%s, F=%s, S=%s, R=%s",
        score_type,
        a,
        f,
        s,
        A,
        F,
        S,
        R,
    )

    # 计算加权分子：(A*a + F*f + S*s)，缺失指标按 0 处理
    weighted_sum = Decimal("0")
    for val, w in [(A, a), (F, f), (S, s)]:
        if val is not None:
            if not isinstance(val, Decimal):
                val = Decimal(str(val))
            # 归一化到 [0, 1]
            eta = max(Decimal("0"), min(Decimal("1"), val / Decimal("100")))
            weighted_sum += w * eta

    # 分母：a + f + s（固定，不因缺失指标而变化）
    weight_total = a + f + s
    if weight_total <= 0:
        logger.warning("[综合评分v2] 权重总和为 0，返回 0")
        return Decimal("0.00")

    # 基础评分 = (A*a + F*f + S*s) / (a+f+s) * 100
    base_score = weighted_sum / weight_total * Decimal("100")

    # R 作为乘数：P = base_score * R/100
    if R is not None:
        if not isinstance(R, Decimal):
            R = Decimal(str(R))
        r_norm = max(Decimal("0"), min(Decimal("1"), R / Decimal("100")))
        score = base_score * r_norm
    else:
        # R 缺失时，评分降级（仅用基础评分的 60%）
        logger.debug("[综合评分v2] R 缺失，评分降级为基础评分的 60%%")
        score = base_score * Decimal("0.6")

    result = _quantize(score)
    logger.debug("[综合评分v2] 最终评分: %.2f", float(result))
    return result


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
    fast_response_rate: Decimal | None = None,
    oscillation_rate: Decimal | None = None,
    saturation_rate: Decimal | None = None,
    stiction_coeff: Decimal | None = None,
    steady_state_time: Decimal | None = None,
    output_travel_index: Decimal | None = None,
) -> dict:
    """幂等写入快照（相同 loop_id + ts_start 不重复写入，覆盖更新）。"""
    # 检查是否已存在
    existing_result = await db.execute(
        select(KpiSnapshotHourly).where(
            KpiSnapshotHourly.loop_id == loop_id,
            KpiSnapshotHourly.ts_start == ts_start,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        # 更新已有记录
        existing.ts_end = ts_end
        existing.status = status
        existing.score = score
        existing.good_value_rate = good_value_rate
        existing.auto_mode_rate = auto_mode_rate
        existing.effective_auto_rate = effective_auto_rate
        existing.steady_rate = steady_rate
        existing.accuracy_rate = accuracy_rate
        existing.fast_rate = fast_response_rate
        existing.oscillation_rate = oscillation_rate
        existing.saturation_rate = saturation_rate
        existing.stiction_index = stiction_coeff
        existing.settling_time = steady_state_time
        existing.output_trip_index = output_travel_index
        snapshot_id = str(existing.id)
    else:
        # 新增记录
        snapshot_id = str(uuid4())
        snapshot = KpiSnapshotHourly(
            id=snapshot_id,
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
            fast_rate=fast_response_rate,
            oscillation_rate=oscillation_rate,
            saturation_rate=saturation_rate,
            stiction_index=stiction_coeff,
            settling_time=steady_state_time,
            output_trip_index=output_travel_index,
        )
        db.add(snapshot)

    return {
        "loopId": loop_id,
        "snapshotId": snapshot_id,
        "tsStart": ts_start.isoformat(),
        "tsEnd": ts_end.isoformat(),
        "status": status,
        "score": float(score) if score is not None else None,
        "algorithmVersion": ALGORITHM_VERSION,
    }


__all__ = [
    "ALGORITHM_VERSION",
    "AsyncTask",
    "backfill_kpi_range",
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
    """执行节点级 KPI 聚合的实际 async 逻辑。"""
    from app.core.db import AsyncSessionLocal
    from app.models.plant_node import PlantNode
    from app.services.node_performance import calculate_and_save_node_snapshot

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

    if task_id:
        try:
            self.run_async(_update_task_running(task_id))
        except Exception:
            logger.warning("更新任务 RUNNING 状态失败: task_id=%s", task_id, exc_info=True)

    try:
        result = self.run_async(_do_backfill(ts_start, ts_end, loop_ids=loop_ids, task_id=task_id))
        logger.info("KPI 回填任务完成: %s", result)

        if task_id:
            try:
                self.run_async(_update_task_success(task_id, result))
            except Exception:
                logger.warning("更新任务 SUCCESS 状态失败: task_id=%s", task_id, exc_info=True)

        return result
    except Exception as exc:
        logger.exception("KPI 回填任务失败")

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


async def _update_backfill_progress(
    task_id: str,
    window_index: int,
    total_windows: int,
    done_in_window: int,
    loops_per_window: int,
) -> None:
    """更新回填任务细粒度进度（逐回路，前端进度条 ≤10s 刷新）。

    进度 = (已完成窗口 × 每窗口回路数 + 当前窗口已完成回路数) / (总窗口 × 每窗口回路数)
    """
    from app.schemas.task import TaskStatus
    from app.services import task_tracker

    total_loops = total_windows * loops_per_window
    done_loops = (window_index - 1) * loops_per_window + done_in_window
    progress = done_loops / total_loops if total_loops > 0 else 0.0
    stage = (
        f"回填计算 窗口[{window_index}/{total_windows}] 回路[{done_in_window}/{loops_per_window}]"
    )
    await task_tracker.update_status(
        task_id,
        TaskStatus.RUNNING,
        progress=progress,
        loops_done=done_loops,
        loops_total=total_loops,
        current_stage=stage,
    )


async def _is_task_cancelled(task_id: str) -> bool:
    """检查任务是否已被取消。"""
    from app.core.redis import redis_client

    raw = await redis_client.hget(f"task:{task_id}", "status")
    if raw is None:
        return False
    return str(raw).upper() == "CANCELLED"


async def _do_backfill(
    ts_start: str,
    ts_end: str,
    loop_ids: list[str] | None = None,
    task_id: str | None = None,
) -> dict:
    """批量回填 async 逻辑：遍历小时窗口，每窗口全量回路计算 + 节点聚合。"""
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

    agg_loop_success = 0
    agg_loop_inconclusive = 0
    agg_loop_failed = 0
    agg_node_success = 0
    failed_windows: list[str] = []

    for i, w in enumerate(windows, 1):
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
            loop_result = await _do_calculate(
                ts_start=w.isoformat(),
                loop_ids=loop_ids,
                task_id=task_id,
                window_index=i,
                total_windows=total,
            )
            node_result = await _do_calculate_node_kpi()
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
