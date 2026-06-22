"""Celery tasks for KPI performance calculation (IDS v3.2 §2.3 — S3-METRIC-003).

设计要点：
- Celery Beat 定时任务（每小时触发全量计算）
- 从 TDengine 拉取回路时序数据（PV/SP/OP/MODE/PV_QUALITY）
- 按 metric_config 公式计算 6 大 KPI
- 计算结果写入 kpi_snapshot_hourly 快照表
- 任务幂等（相同 loop_id + ts_start 不重复写入）
- 失败自动重试 3 次
- 数据不足返回 INCONCLUSIVE 状态
- PV 质量码为 Bad 的数据点剔除
- TDengine 不可用时优雅降级（记录日志并跳过）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from celery import Task
from sqlalchemy import select

from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import KpiSnapshotHourly, MetricConfig
from app.models.tag import TagRegistry
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# 算法版本号
ALGORITHM_VERSION = "KPI_CALC_v1.0"

# 数据不足阈值：Good 数据占比 < 20% 视为 INCONCLUSIVE
MIN_GOOD_RATIO = 0.20

# 并发 worker 数
CONCURRENCY = 10


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
def calculate_hourly_kpi(self: AsyncTask) -> dict:
    """每小时全量计算所有 ACTIVE 回路的 KPI 快照。

    失败自动重试 3 次，指数退避。
    """
    logger.info("KPI 计算任务开始, task_id=%s", self.request.id)
    try:
        result = self.run_async(_do_calculate())
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
# Beat 调度配置：每小时执行一次
# ---------------------------------------------------------------------------


_beat_entry = {
    "task": "app.tasks.kpi_calc.calculate_hourly_kpi",
    "schedule": 3600.0,  # 1 小时
}

# 合并到 celery_app 的 beat_schedule（与 aas_sync 的 beat 共存）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["kpi-calc-hourly"] = _beat_entry
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


# ---------------------------------------------------------------------------
# 异步计算逻辑
# ---------------------------------------------------------------------------


async def _do_calculate() -> dict:
    """执行全量 KPI 计算的实际 async 逻辑。"""
    from app.core.db import AsyncSessionLocal
    from app.core.tdengine import query_trend_data

    # 计算时间窗（上一个完整小时）
    now = datetime.now(UTC)
    ts_end = now.replace(minute=0, second=0, microsecond=0)
    ts_start = ts_end - timedelta(hours=1)

    async with AsyncSessionLocal() as db:
        # 1. 查询所有 ACTIVE/READY 状态回路
        loop_result = await db.execute(
            select(LoopLedger).where(
                LoopLedger.is_active.is_(True),
                LoopLedger.status == "READY",
            )
        )
        loops = list(loop_result.scalars().all())
        logger.info("待计算回路数: %d", len(loops))

        if not loops:
            return {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

        # 2. 加载指标配置
        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code: c for c in metric_result.scalars().all()}

        # 3. 并发计算（信号量限制并发数）
        sem = asyncio.Semaphore(CONCURRENCY)

        async def _calc_with_sem(loop: LoopLedger) -> dict | None:
            async with sem:
                return await _calculate_loop_kpi(
                    db=db,
                    loop=loop,
                    metric_configs=metric_configs,
                    ts_start=ts_start,
                    ts_end=ts_end,
                    query_trend_fn=query_trend_data,
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

        await db.commit()
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
    from app.core.tdengine import query_trend_data

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
        loop = result.scalar_one_or_none()
        if loop is None:
            return {"loopId": loop_id, "status": "FAILED", "error": "回路不存在"}

        # 时间窗
        now = datetime.now(UTC)
        if ts_start:
            try:
                ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
            except ValueError:
                ts_start_dt = datetime.fromisoformat(ts_start)
        else:
            ts_start_dt = (now - timedelta(hours=1)).replace(
                minute=0, second=0, microsecond=0
            )
        ts_end_dt = ts_start_dt + timedelta(hours=1)

        metric_result = await db.execute(select(MetricConfig))
        metric_configs = {c.metric_code: c for c in metric_result.scalars().all()}

        snap = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs=metric_configs,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
            query_trend_fn=query_trend_data,
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
) -> dict | None:
    """计算单回路 KPI 并写入快照（幂等）。

    Args:
        db: 异步数据库会话
        loop: 回路对象
        metric_configs: 指标配置字典 {metric_code: MetricConfig}
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        query_trend_fn: TDengine 查询函数（注入便于测试）

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

    # 计算 6 大 KPI
    kpi_values = _compute_kpis(aligned, metric_configs)

    # 计算综合评分 Score = (Σ wᵢ × ηᵢ_norm) × R_auto
    score = _compute_composite_score(kpi_values, metric_configs)

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
        steady_rate=kpi_values.get("steady_rate"),
        accuracy_rate=kpi_values.get("accuracy_rate"),
        oscillation_rate=kpi_values.get("oscillation_rate"),
        saturation_rate=kpi_values.get("saturation_rate"),
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


def _align_timeseries(
    pv_data: list[dict],
    sp_data: list[dict],
    op_data: list[dict],
    mode_data: list[dict],
) -> list[dict[str, Any]]:
    """按 ts 对齐 PV/SP/OP/MODE 时序数据。"""
    # 构建 ts → value 映射
    sp_map = {d.get("ts"): d.get("value") for d in sp_data}
    op_map = {d.get("ts"): d.get("value") for d in op_data}
    mode_map = {d.get("ts"): d.get("value") for d in mode_data}

    aligned: list[dict[str, Any]] = []
    for d in pv_data:
        ts = d.get("ts")
        pv = d.get("value")
        aligned.append(
            {
                "ts": ts,
                "pv": pv,
                "sp": sp_map.get(ts),
                "op": op_map.get(ts),
                "mode": mode_map.get(ts),
            }
        )
    return aligned


def _compute_kpis(
    aligned: list[dict[str, Any]],
    metric_configs: dict[str, MetricConfig],
) -> dict[str, Decimal | None]:
    """计算 6 大 KPI。

    Returns:
        {metric_code: Decimal value or None}
    """
    total = len(aligned)
    if total == 0:
        return dict.fromkeys(
            (
                "good_value_rate",
                "auto_mode_rate",
                "steady_rate",
                "accuracy_rate",
                "oscillation_rate",
                "saturation_rate",
            )
        )

    # 好值率：sum(quality==Good) / count(*) * 100
    # 注：quality 已在调用前过滤，aligned 中所有点都是 Good
    good_value_rate = Decimal("100.0")

    # 自控率：sum(mode in [Auto, Cascade]) / count(*) * 100
    # mode 值：0=Manual, 1=Auto, 2/3=Cascade
    auto_count = sum(
        1
        for d in aligned
        if d.get("mode") is not None and _is_auto_mode(d["mode"])
    )
    auto_mode_rate = Decimal(auto_count) / Decimal(total) * Decimal("100")

    # 平稳率：duration(abs(pv - sp) <= pv_range * 0.02) / duration(*) * 100
    # 简化：按点数计算（假设采样均匀）
    pv_values = [d["pv"] for d in aligned if d.get("pv") is not None and d.get("sp") is not None]
    if pv_values:
        pv_range = max(pv_values) - min(pv_values) if len(pv_values) > 1 else 1.0
        if pv_range == 0:
            pv_range = 1.0
        steady_count = sum(
            1
            for d in aligned
            if d.get("pv") is not None
            and d.get("sp") is not None
            and abs(d["pv"] - d["sp"]) <= pv_range * 0.02
        )
        steady_rate = Decimal(steady_count) / Decimal(total) * Decimal("100")
    else:
        steady_rate = None

    # 准确率：duration(abs(pv - sp) <= pv_range * 0.05) / duration(*) * 100
    if pv_values:
        pv_range = max(pv_values) - min(pv_values) if len(pv_values) > 1 else 1.0
        if pv_range == 0:
            pv_range = 1.0
        accuracy_count = sum(
            1
            for d in aligned
            if d.get("pv") is not None
            and d.get("sp") is not None
            and abs(d["pv"] - d["sp"]) <= pv_range * 0.05
        )
        accuracy_rate = Decimal(accuracy_count) / Decimal(total) * Decimal("100")
    else:
        accuracy_rate = None

    # 振荡率：duration(oscillation_detected == true) / duration(*) * 100
    # 简化：检测连续反向变化（相邻点 PV 差值符号变化超过阈值）
    oscillation_count = _detect_oscillation(aligned)
    oscillation_rate = Decimal(oscillation_count) / Decimal(total) * Decimal("100")

    # 饱和率：duration(op >= 95 OR op <= 5) / duration(*) * 100
    saturation_count = sum(
        1
        for d in aligned
        if d.get("op") is not None and (d["op"] >= 95 or d["op"] <= 5)
    )
    saturation_rate = Decimal(saturation_count) / Decimal(total) * Decimal("100")

    return {
        "good_value_rate": _quantize(good_value_rate),
        "auto_mode_rate": _quantize(auto_mode_rate),
        "steady_rate": _quantize(steady_rate) if steady_rate is not None else None,
        "accuracy_rate": _quantize(accuracy_rate) if accuracy_rate is not None else None,
        "oscillation_rate": _quantize(oscillation_rate),
        "saturation_rate": _quantize(saturation_rate),
    }


def _is_auto_mode(mode_value: Any) -> bool:
    """判断 mode 值是否为 Auto 或 Cascade。"""
    try:
        v = int(float(mode_value))
        return v in (1, 2, 3)
    except (ValueError, TypeError):
        return False


def _detect_oscillation(aligned: list[dict[str, Any]]) -> int:
    """检测振荡点数（简化：相邻 PV 差值符号变化次数）。"""
    pv_values = [d.get("pv") for d in aligned if d.get("pv") is not None]
    if len(pv_values) < 3:
        return 0

    oscillation_count = 0
    prev_diff = None
    for i in range(1, len(pv_values)):
        diff = pv_values[i] - pv_values[i - 1]
        if diff == 0:
            continue
        sign = 1 if diff > 0 else -1
        if prev_diff is not None and sign != prev_diff:
            oscillation_count += 1
        prev_diff = sign
    return oscillation_count


def _compute_composite_score(
    kpi_values: dict[str, Decimal | None],
    metric_configs: dict[str, MetricConfig],
) -> Decimal:
    """计算综合评分 Score = (Σ wᵢ × ηᵢ_norm) × R_auto。

    - wᵢ = 指标 i 的权重（仅启用指标参与）
    - ηᵢ_norm = 归一化后的指标值（0-1）
    - R_auto = 自控率（0-1），作为乘数因子
    """
    # 自控率作为乘数
    auto_mode_rate = kpi_values.get("auto_mode_rate")
    r_auto = (auto_mode_rate / Decimal("100")) if auto_mode_rate is not None else Decimal("0")

    # 归一化：oscillation_rate / saturation_rate 越低越好，归一化为 (100 - value) / 100
    # 其他指标越高越好，归一化为 value / 100
    weighted_sum = Decimal("0")
    for code, value in kpi_values.items():
        if value is None:
            continue
        config = metric_configs.get(code)
        if not config or not config.is_enabled or config.weight is None:
            continue
        w = config.weight
        if code in ("oscillation_rate", "saturation_rate"):
            eta_norm = (Decimal("100") - value) / Decimal("100")
        else:
            eta_norm = value / Decimal("100")
        # 限制在 [0, 1]
        eta_norm = max(Decimal("0"), min(Decimal("1"), eta_norm))
        weighted_sum += w * eta_norm

    # Score = weighted_sum × R_auto（权重总和为 100，所以 weighted_sum 已是 0-100）
    score = weighted_sum * r_auto
    return _quantize(score)


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
    steady_rate: Decimal | None = None,
    accuracy_rate: Decimal | None = None,
    oscillation_rate: Decimal | None = None,
    saturation_rate: Decimal | None = None,
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
        existing.steady_rate = steady_rate
        existing.accuracy_rate = accuracy_rate
        existing.oscillation_rate = oscillation_rate
        existing.saturation_rate = saturation_rate
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
            steady_rate=steady_rate,
            accuracy_rate=accuracy_rate,
            oscillation_rate=oscillation_rate,
            saturation_rate=saturation_rate,
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
    "calculate_hourly_kpi",
    "calculate_loop_kpi",
]
