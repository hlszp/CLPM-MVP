"""Dashboard aggregation service (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

业务逻辑：
- 工作台聚合数据（6 大 KPI 卡片 + 低效回路 Top 10 + 趋势摘要 + 待处理异常）
- Redis 缓存（5 分钟，key 含 plant_id + granularity + user_role）
- 角色数据范围控制（ADMIN/EXPERT 全厂、IC/PE 装置级、SPONSOR 工厂级汇总）
- Redis 不可用时降级为直接查询
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.core.redis import redis_client
from app.models.diagnosis import DiagnosisResult
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.models.plant_node import PlantNode
from app.models.tracker import ActionTracker

logger = logging.getLogger(__name__)

# Redis 缓存配置
DASHBOARD_CACHE_KEY_TEMPLATE = "dashboard:overview:{plant_id}:{granularity}:{role}"
DASHBOARD_CACHE_TTL = 300  # 5 分钟

# 时间粒度映射
GRANULARITY_DELTA: dict[str, timedelta] = {
    "day": timedelta(hours=24),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}

# 角色数据范围
ROLE_FULL_PLANT = {"ADMIN", "EXPERT"}
ROLE_PLANT_LEVEL = {"IC_ENGINEER", "PE_ENGINEER"}
ROLE_FACTORY_SUMMARY = {"SPONSOR"}

# 趋势阈值（变化幅度小于此值视为 stable）
TREND_STABLE_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# 主入口：工作台聚合
# ---------------------------------------------------------------------------


async def get_dashboard_overview(
    db: AsyncSession,
    *,
    user_role: str,
    plant_id: str | None = None,
    granularity: str = "day",
) -> dict[str, Any]:
    """获取工作台聚合数据。

    Args:
        db: 异步数据库会话
        user_role: 用户角色（ADMIN/IC_ENGINEER/PE_ENGINEER/SPONSOR/EXPERT）
        plant_id: 装置 ID 筛选（可选）
        granularity: 时间粒度 day/week/month

    Returns:
        工作台聚合数据字典
    """
    cache_key = _build_cache_key(plant_id, granularity, user_role)
    cached = await _read_cache(cache_key)
    if cached is not None:
        cached["cached"] = True
        return cached

    # 缓存未命中：尝试获取 dogpile 互斥锁，避免惊群效应
    lock_key = f"{cache_key}:lock"
    acquired = await _acquire_lock(lock_key)

    if acquired:
        # 获取锁成功：执行聚合并写入缓存
        try:
            data = await _aggregate_dashboard(
                db=db,
                user_role=user_role,
                plant_id=plant_id,
                granularity=granularity,
            )
            data["cached"] = False
            await _write_cache(cache_key, data)
            return data
        finally:
            await _release_lock(lock_key)

    # 获取锁失败：等待锁持有者写入缓存，轮询 3 次，每次间隔 0.5s
    for _ in range(3):
        await asyncio.sleep(0.5)
        cached = await _read_cache(cache_key)
        if cached is not None:
            cached["cached"] = True
            return cached

    # 轮询后仍无缓存：优雅降级，直接聚合（不写缓存）
    data = await _aggregate_dashboard(
        db=db,
        user_role=user_role,
        plant_id=plant_id,
        granularity=granularity,
    )
    data["cached"] = False
    return data


# ---------------------------------------------------------------------------
# 聚合核心逻辑
# ---------------------------------------------------------------------------


async def _aggregate_dashboard(
    db: AsyncSession,
    *,
    user_role: str,
    plant_id: str | None,
    granularity: str,
) -> dict[str, Any]:
    """聚合工作台数据（并行查询 + SQL 聚合）。

    通过 asyncio.gather 并行执行 5 个独立查询组，每组使用独立 session：
    a. KPI 卡片聚合（_aggregate_kpi_cards_sql）
    b. 计数聚合（_aggregate_counts_sql）
    c. 趋势摘要（_aggregate_trend_summary_sql）
    d. 待处理异常（_build_pending_alerts）
    e. 低效回路（_build_inefficient_loops，SPONSOR 跳过）
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    delta = GRANULARITY_DELTA.get(granularity, timedelta(hours=24))
    current_start = now - delta
    previous_start = current_start - delta

    # 获取装置名称（使用传入的 db session）
    plant_name = await _get_plant_name(db, plant_id)

    # 构建并行查询任务（每个使用独立 session）
    kpi_cards_task = _run_in_session(
        lambda s: _aggregate_kpi_cards_sql(
            s,
            plant_id=plant_id,
            current_start=current_start,
            now=now,
            previous_start=previous_start,
        )
    )
    counts_task = _run_in_session(
        lambda s: _aggregate_counts_sql(
            s,
            plant_id=plant_id,
            current_start=current_start,
            now=now,
            previous_start=previous_start,
        )
    )
    trend_task = _run_in_session(
        lambda s: _aggregate_trend_summary_sql(s, plant_id=plant_id, now=now)
    )
    alerts_task = _run_in_session(
        lambda s: _build_pending_alerts(s, plant_id=plant_id)
    )

    # SPONSOR 角色跳过低效回路
    if user_role in ROLE_FACTORY_SUMMARY:
        results = await asyncio.gather(
            kpi_cards_task, counts_task, trend_task, alerts_task
        )
        kpi_cards, counts, trend_summary, pending_alerts = results
        inefficient_loops: list[dict[str, Any]] = []
    else:
        loops_task = _run_in_session(
            lambda s: _build_inefficient_loops(
                s, plant_id=plant_id, start=current_start, end=now
            )
        )
        results = await asyncio.gather(
            kpi_cards_task, counts_task, trend_task, alerts_task, loops_task
        )
        kpi_cards, counts, trend_summary, pending_alerts, inefficient_loops = results

    # 合并计数结果到 kpi_cards
    kpi_cards["alarm_count"] = counts["alarm_count"]
    kpi_cards["operation_count"] = counts["operation_count"]

    return {
        "filter_scope": {
            "plant_id": plant_id,
            "plant_name": plant_name,
            "granularity": granularity,
            "user_role": user_role,
        },
        "kpi_cards": kpi_cards,
        "inefficient_loops": inefficient_loops,
        "trend_summary": trend_summary,
        "pending_alerts": pending_alerts,
    }


# ---------------------------------------------------------------------------
# 并行查询辅助
# ---------------------------------------------------------------------------


async def _run_in_session(coro_func):
    """在独立 AsyncSession 中执行协程，用于并行查询。

    AsyncSession 不支持并发使用，因此每个并行任务需要使用独立 session。
    """
    async with AsyncSessionLocal() as session:
        return await coro_func(session)


def _apply_snapshot_filters(
    stmt,
    *,
    plant_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
):
    """为快照查询添加时间/状态/装置过滤条件。"""
    if start is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start >= start)
    if end is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start <= end)
    if status_filter:
        stmt = stmt.where(KpiSnapshotHourly.status == status_filter)
    if plant_id:
        stmt = stmt.join(
            LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id
        ).where(LoopLedger.unit_id == plant_id)
    return stmt


# ---------------------------------------------------------------------------
# SQL 聚合函数
# ---------------------------------------------------------------------------


async def _aggregate_kpi_cards_sql(
    session: AsyncSession,
    *,
    plant_id: str | None,
    current_start: datetime,
    now: datetime,
    previous_start: datetime,
) -> dict[str, Any]:
    """SQL 聚合 KPI 卡片（score/auto_mode_rate/steady_rate/good_value_rate）。

    使用 case() 条件聚合在一次查询中同时计算 current 和 previous 周期的平均值。
    """
    fields = ("score", "auto_mode_rate", "steady_rate", "good_value_rate")

    # current/previous 周期条件
    cur_cond = KpiSnapshotHourly.ts_start >= current_start
    prev_cond = KpiSnapshotHourly.ts_start < current_start

    # 计数列
    cur_cnt = func.count(case((cur_cond, 1), else_=None)).label("cur_cnt")
    prev_cnt = func.count(case((prev_cond, 1), else_=None)).label("prev_cnt")

    # 条件平均列
    avg_cols = []
    for f in fields:
        col = getattr(KpiSnapshotHourly, f)
        avg_cols.append(
            func.avg(case((cur_cond, col), else_=None)).label(f"cur_{f}")
        )
        avg_cols.append(
            func.avg(case((prev_cond, col), else_=None)).label(f"prev_{f}")
        )

    stmt = _apply_snapshot_filters(
        select(cur_cnt, prev_cnt, *avg_cols),
        plant_id=plant_id,
        start=previous_start,
        end=now,
        status_filter="SUCCESS",
    )
    result = await session.execute(stmt)
    row = result.one()

    def make_field(field: str, unit: str) -> dict[str, Any]:
        """构建单个 KPI 卡片字段。"""
        cur_val = _to_float(getattr(row, f"cur_{field}"))
        prev_val = _to_float(getattr(row, f"prev_{field}"))
        cur = round(cur_val, 2) if cur_val is not None else None
        prev = round(prev_val, 2) if prev_val is not None else None
        return _make_card(cur, prev, unit=unit)

    return {
        "auto_mode_rate": make_field("auto_mode_rate", "%"),
        "steady_rate": make_field("steady_rate", "%"),
        "composite_score": make_field("score", "分"),
        "alarm_count": _make_card(0, 0, unit="次"),  # 由 _aggregate_counts_sql 填充
        "operation_count": _make_card(0, 0, unit="次"),  # 由 _aggregate_counts_sql 填充
        "good_value_rate": make_field("good_value_rate", "%"),
    }


async def _aggregate_counts_sql(
    session: AsyncSession,
    *,
    plant_id: str | None,
    current_start: datetime,
    now: datetime,
    previous_start: datetime,
) -> dict[str, dict[str, Any]]:
    """SQL 聚合 alarm_count 和 operation_count（合并 4 次查询为 2 次）。

    使用 case() 条件聚合同时计算 current 和 previous 周期的计数。
    """
    # alarm_count: 诊断结果数
    diag_cur_cond = DiagnosisResult.diagnosed_at >= current_start
    diag_prev_cond = DiagnosisResult.diagnosed_at < current_start

    diag_stmt = select(
        func.count(case((diag_cur_cond, 1), else_=None)).label("cur_cnt"),
        func.count(case((diag_prev_cond, 1), else_=None)).label("prev_cnt"),
    ).where(
        DiagnosisResult.diagnosed_at >= previous_start,
        DiagnosisResult.diagnosed_at <= now,
    )
    if plant_id:
        diag_stmt = diag_stmt.join(
            LoopLedger, DiagnosisResult.loop_id == LoopLedger.id, isouter=True
        ).where(LoopLedger.unit_id == plant_id)

    diag_result = await session.execute(diag_stmt)
    diag_row = diag_result.one()
    alarm_count = _make_card(diag_row.cur_cnt or 0, diag_row.prev_cnt or 0, unit="次")

    # operation_count: ActionTracker 更新数
    tracker_cur_cond = ActionTracker.updated_at >= current_start
    tracker_prev_cond = ActionTracker.updated_at < current_start

    tracker_stmt = select(
        func.count(case((tracker_cur_cond, 1), else_=None)).label("cur_cnt"),
        func.count(case((tracker_prev_cond, 1), else_=None)).label("prev_cnt"),
    ).where(
        ActionTracker.updated_at.is_not(None),
        ActionTracker.updated_at >= previous_start,
        ActionTracker.updated_at <= now,
    )
    if plant_id:
        tracker_stmt = tracker_stmt.join(
            LoopLedger, ActionTracker.loop_id == LoopLedger.id, isouter=True
        ).where(LoopLedger.unit_id == plant_id)

    tracker_result = await session.execute(tracker_stmt)
    tracker_row = tracker_result.one()
    operation_count = _make_card(
        tracker_row.cur_cnt or 0, tracker_row.prev_cnt or 0, unit="次"
    )

    return {"alarm_count": alarm_count, "operation_count": operation_count}


async def _aggregate_trend_summary_sql(
    session: AsyncSession,
    *,
    plant_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    """SQL 聚合趋势摘要（最近 7 天每日综合评分）。

    使用 func.date_trunc('day', ...) + GROUP BY + func.avg(score)。
    """
    start = now - timedelta(days=7)
    day_col = func.date_trunc("day", KpiSnapshotHourly.ts_start).label("day")
    avg_col = func.avg(KpiSnapshotHourly.score).label("avg_score")
    stmt = (
        _apply_snapshot_filters(
            select(day_col, avg_col),
            plant_id=plant_id,
            start=start,
            end=now,
            status_filter="SUCCESS",
        )
        .group_by(day_col)
        .order_by(day_col.asc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    # 构建日期 → 平均分映射
    daily_scores: dict[str, float | None] = {}
    for r in rows:
        ts = r.day
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        date_key = ts.strftime("%Y-%m-%d")
        score = _to_float(r.avg_score)
        daily_scores[date_key] = round(score, 2) if score is not None else None

    # 构建最近 7 天日期列表
    dates: list[str] = []
    composite_scores: list[float | None] = []
    for i in range(7):
        day = now - timedelta(days=6 - i)
        date_key = day.strftime("%Y-%m-%d")
        dates.append(date_key)
        composite_scores.append(daily_scores.get(date_key))

    return {"dates": dates, "composite_scores": composite_scores}


# ---------------------------------------------------------------------------
# KPI 卡片聚合
# ---------------------------------------------------------------------------


def _build_kpi_cards(
    *,
    current_snapshots: list[KpiSnapshotHourly],
    previous_snapshots: list[KpiSnapshotHourly],
) -> dict[str, Any]:
    """构建 6 大 KPI 卡片数据。

    注：alarm_count 和 operation_count 为异步查询，此处先构建同步部分，
    异步部分在 _fill_async_kpi_cards 中补充。
    """
    current_valid = [s for s in current_snapshots if s.status == "SUCCESS"]
    previous_valid = [s for s in previous_snapshots if s.status == "SUCCESS"]

    def avg(field: str, snapshots: list[KpiSnapshotHourly]) -> float | None:
        vals = [getattr(s, field) for s in snapshots if getattr(s, field) is not None]
        if not vals:
            return None
        return round(float(sum(vals)) / len(vals), 2)

    # 综合评分
    cur_score = avg("score", current_valid)
    prev_score = avg("score", previous_valid)
    # 自控投用率
    cur_auto = avg("auto_mode_rate", current_valid)
    prev_auto = avg("auto_mode_rate", previous_valid)
    # 平稳率
    cur_steady = avg("steady_rate", current_valid)
    prev_steady = avg("steady_rate", previous_valid)
    # 好值率
    cur_good = avg("good_value_rate", current_valid)
    prev_good = avg("good_value_rate", previous_valid)

    return {
        "auto_mode_rate": _make_card(cur_auto, prev_auto, unit="%"),
        "steady_rate": _make_card(cur_steady, prev_steady, unit="%"),
        "composite_score": _make_card(cur_score, prev_score, unit="分"),
        "alarm_count": _make_card(0, 0, unit="次"),  # 同步初始化，异步填充
        "operation_count": _make_card(0, 0, unit="次"),  # 同步初始化，异步填充
        "good_value_rate": _make_card(cur_good, prev_good, unit="%"),
    }


async def _fill_async_kpi_cards(
    db: AsyncSession,
    kpi_cards: dict[str, Any],
    *,
    plant_id: str | None,
    current_start: datetime,
    now: datetime,
    previous_start: datetime,
) -> None:
    """填充 alarm_count 和 operation_count（需要异步查询）。"""
    # alarm_count: 当前周期诊断结果数
    alarm_count_current = await _count_diagnoses(
        db=db, plant_id=plant_id, start=current_start, end=now
    )
    alarm_count_previous = await _count_diagnoses(
        db=db, plant_id=plant_id, start=previous_start, end=current_start
    )
    kpi_cards["alarm_count"] = _make_card(
        alarm_count_current, alarm_count_previous, unit="次"
    )

    # operation_count: 当前周期 ActionTracker 更新数
    operation_count_current = await _count_tracker_updates(
        db=db, plant_id=plant_id, start=current_start, end=now
    )
    operation_count_previous = await _count_tracker_updates(
        db=db, plant_id=plant_id, start=previous_start, end=current_start
    )
    kpi_cards["operation_count"] = _make_card(
        operation_count_current, operation_count_previous, unit="次"
    )


def _make_card(
    current: float | int | None,
    previous: float | int | None,
    *,
    unit: str = "",
) -> dict[str, Any]:
    """构建单个 KPI 卡片。"""
    if current is None:
        value = None
        delta = 0.0
        trend = "stable"
    else:
        value = current
        if previous is None:
            delta = 0.0
            trend = "stable"
        else:
            delta = round(float(current) - float(previous), 2)
            trend = _calc_trend(delta)
    return {"value": value, "unit": unit, "trend": trend, "delta": delta}


def _calc_trend(delta: float) -> str:
    """计算趋势方向。"""
    if delta > TREND_STABLE_THRESHOLD:
        return "up"
    if delta < -TREND_STABLE_THRESHOLD:
        return "down"
    return "stable"


# ---------------------------------------------------------------------------
# 低效回路 Top 10
# ---------------------------------------------------------------------------


async def _build_inefficient_loops(
    db: AsyncSession,
    *,
    plant_id: str | None,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """构建低效回路 Top 10（按综合评分升序）。"""
    # 查询时间窗内每个回路的最新快照
    snapshots = await _query_snapshots(
        db=db, plant_id=plant_id, start=start, end=end, status_filter="SUCCESS"
    )

    # 按回路取最新快照
    loop_latest: dict[str, KpiSnapshotHourly] = {}
    for snap in snapshots:
        lid = str(snap.loop_id) if snap.loop_id else ""
        if not lid:
            continue
        if lid not in loop_latest or snap.ts_start > loop_latest[lid].ts_start:
            loop_latest[lid] = snap

    if not loop_latest:
        return []

    loop_ids = list(loop_latest.keys())

    # 批量查询回路基础信息（避免 N+1）
    loop_map: dict[str, LoopLedger] = {}
    plant_map: dict[str, str] = {}
    l_result = await db.execute(select(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
    for loop in l_result.scalars().all():
        loop_map[str(loop.id)] = loop

    # 批量查询单元/装置名称
    unit_ids = [str(loop.unit_id) for loop in loop_map.values() if loop.unit_id]
    if unit_ids:
        u_result = await db.execute(select(PlantNode).where(PlantNode.id.in_(unit_ids)))
        for node in u_result.scalars().all():
            plant_map[str(node.id)] = node.name

    # 批量查询诊断标签（避免 N+1）
    diagnosis_labels_map = await _batch_query_diagnosis_labels(db, loop_ids)

    # 构建列表
    items: list[dict[str, Any]] = []
    for loop_id, snap in loop_latest.items():
        loop = loop_map.get(loop_id)
        if not loop:
            continue
        plant_name = plant_map.get(str(loop.unit_id)) if loop.unit_id else None
        items.append(
            {
                "loop_id": loop_id,
                "loop_tag": loop.tag_name,
                "loop_name": loop.description,
                "plant_name": plant_name,
                "composite_score": _to_float(snap.score),
                "diagnosis_labels": diagnosis_labels_map.get(loop_id, []),
                "key_metric": {
                    "auto_mode_rate": _to_float(snap.auto_mode_rate),
                    "steady_rate": _to_float(snap.steady_rate),
                },
            }
        )

    # 按综合评分升序排序，取前 10
    items.sort(
        key=lambda x: (
            x["composite_score"] is None,
            x["composite_score"] if x["composite_score"] is not None else 0,
        )
    )
    return items[:10]


async def _batch_query_diagnosis_labels(
    db: AsyncSession, loop_ids: list[str]
) -> dict[str, list[str]]:
    """批量查询回路的诊断标签（取最新一条诊断结果）。"""
    if not loop_ids:
        return {}

    # 查询每个回路的最新诊断标签
    result = await db.execute(
        select(DiagnosisResult.loop_id, DiagnosisResult.diag_label)
        .where(DiagnosisResult.loop_id.in_(loop_ids))
        .where(DiagnosisResult.diag_label.is_not(None))
        .order_by(DiagnosisResult.diagnosed_at.desc())
    )
    labels_map: dict[str, list[str]] = {}
    for lid, label in result.all():
        lid_str = str(lid) if lid else ""
        if not lid_str or not label:
            continue
        if lid_str not in labels_map:
            labels_map[lid_str] = [label]
    return labels_map


# ---------------------------------------------------------------------------
# 回路趋势摘要
# ---------------------------------------------------------------------------


async def _build_trend_summary(
    db: AsyncSession,
    *,
    plant_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    """构建回路趋势摘要（最近 7 天每日综合评分）。"""
    start = now - timedelta(days=7)
    snapshots = await _query_snapshots(db=db, plant_id=plant_id, start=start, end=now)

    # 按日期分组
    daily_scores: dict[str, list[float]] = {}
    for snap in snapshots:
        if snap.score is None or snap.status != "SUCCESS":
            continue
        # 转为本地日期（UTC）
        ts = snap.ts_start
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        date_key = ts.strftime("%Y-%m-%d")
        daily_scores.setdefault(date_key, []).append(float(snap.score))

    # 构建最近 7 天日期列表
    dates: list[str] = []
    composite_scores: list[float | None] = []
    for i in range(7):
        day = now - timedelta(days=6 - i)
        date_key = day.strftime("%Y-%m-%d")
        dates.append(date_key)
        scores = daily_scores.get(date_key)
        if scores:
            composite_scores.append(round(sum(scores) / len(scores), 2))
        else:
            composite_scores.append(None)

    return {"dates": dates, "composite_scores": composite_scores}


# ---------------------------------------------------------------------------
# 待处理异常数
# ---------------------------------------------------------------------------


async def _build_pending_alerts(
    db: AsyncSession,
    *,
    plant_id: str | None,
) -> dict[str, Any]:
    """构建待处理异常数。"""
    # open_trackers: ActionTracker 状态为 PENDING/IN_PROGRESS 的记录数
    # open_diagnoses: 有诊断结果且 ActionTracker 状态为 PENDING/IN_PROGRESS 的回路数

    if plant_id:
        # 按装置过滤：JOIN loop_ledger
        tracker_stmt = (
            select(ActionTracker)
            .join(LoopLedger, ActionTracker.loop_id == LoopLedger.id, isouter=True)
            .where(LoopLedger.unit_id == plant_id)
            .where(ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"]))
        )
        diag_stmt = (
            select(func.count(func.distinct(DiagnosisResult.loop_id)))
            .join(LoopLedger, DiagnosisResult.loop_id == LoopLedger.id, isouter=True)
            .join(
                ActionTracker,
                ActionTracker.loop_id == LoopLedger.id,
                isouter=True,
            )
            .where(LoopLedger.unit_id == plant_id)
            .where(ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"]))
        )
    else:
        tracker_stmt = select(ActionTracker).where(
            ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"])
        )
        diag_stmt = (
            select(func.count(func.distinct(DiagnosisResult.loop_id)))
            .join(
                ActionTracker,
                ActionTracker.loop_id == DiagnosisResult.loop_id,
                isouter=True,
            )
            .where(ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"]))
        )

    tracker_result = await db.execute(tracker_stmt)
    open_trackers = len(list(tracker_result.scalars().all()))

    diag_result = await db.execute(diag_stmt)
    open_diagnoses = diag_result.scalar() or 0

    return {"open_diagnoses": open_diagnoses, "open_trackers": open_trackers}


# ---------------------------------------------------------------------------
# 计数辅助
# ---------------------------------------------------------------------------


async def _count_diagnoses(
    db: AsyncSession,
    *,
    plant_id: str | None,
    start: datetime,
    end: datetime,
) -> int:
    """统计时间窗内的诊断结果数。"""
    if plant_id:
        stmt = (
            select(func.count())
            .select_from(DiagnosisResult)
            .join(LoopLedger, DiagnosisResult.loop_id == LoopLedger.id, isouter=True)
            .where(DiagnosisResult.diagnosed_at >= start)
            .where(DiagnosisResult.diagnosed_at <= end)
            .where(LoopLedger.unit_id == plant_id)
        )
    else:
        stmt = (
            select(func.count())
            .select_from(DiagnosisResult)
            .where(DiagnosisResult.diagnosed_at >= start)
            .where(DiagnosisResult.diagnosed_at <= end)
        )
    result = await db.execute(stmt)
    return result.scalar() or 0


async def _count_tracker_updates(
    db: AsyncSession,
    *,
    plant_id: str | None,
    start: datetime,
    end: datetime,
) -> int:
    """统计时间窗内的 ActionTracker 更新数。"""
    if plant_id:
        stmt = (
            select(func.count())
            .select_from(ActionTracker)
            .join(LoopLedger, ActionTracker.loop_id == LoopLedger.id, isouter=True)
            .where(ActionTracker.updated_at.is_not(None))
            .where(ActionTracker.updated_at >= start)
            .where(ActionTracker.updated_at <= end)
            .where(LoopLedger.unit_id == plant_id)
        )
    else:
        stmt = (
            select(func.count())
            .select_from(ActionTracker)
            .where(ActionTracker.updated_at.is_not(None))
            .where(ActionTracker.updated_at >= start)
            .where(ActionTracker.updated_at <= end)
        )
    result = await db.execute(stmt)
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------


async def _query_snapshots(
    db: AsyncSession,
    *,
    plant_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    status_filter: str | None = None,
) -> list[KpiSnapshotHourly]:
    """查询快照数据，可选按装置/时间/状态过滤。"""
    stmt = select(KpiSnapshotHourly)
    if start is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start >= start)
    if end is not None:
        stmt = stmt.where(KpiSnapshotHourly.ts_start <= end)
    if status_filter:
        stmt = stmt.where(KpiSnapshotHourly.status == status_filter)
    if plant_id:
        # 通过 join loop_ledger 过滤
        stmt = stmt.join(LoopLedger, KpiSnapshotHourly.loop_id == LoopLedger.id).where(
            LoopLedger.unit_id == plant_id
        )
    stmt = stmt.order_by(KpiSnapshotHourly.ts_start.asc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_plant_name(db: AsyncSession, plant_id: str | None) -> str | None:
    """获取装置名称。"""
    if not plant_id:
        return None
    result = await db.execute(select(PlantNode).where(PlantNode.id == plant_id))
    node = result.scalar_one_or_none()
    return node.name if node else None


def _to_float(value: Decimal | float | None) -> float | None:
    """Decimal/float → float，None 透传。"""
    if value is None:
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Redis 缓存
# ---------------------------------------------------------------------------


def _build_cache_key(
    plant_id: str | None, granularity: str, user_role: str
) -> str:
    """构建 Redis 缓存 key。"""
    return DASHBOARD_CACHE_KEY_TEMPLATE.format(
        plant_id=plant_id or "all",
        granularity=granularity,
        role=user_role,
    )


async def _read_cache(cache_key: str) -> dict[str, Any] | None:
    """读取 Redis 缓存，失败时返回 None（降级为直接查询）。"""
    try:
        cached = await redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取工作台缓存失败，降级为直接查询: %s", exc)
    return None


async def _write_cache(cache_key: str, data: dict[str, Any]) -> None:
    """写入 Redis 缓存，失败时不报错（降级模式）。

    使用 TTL 抖动（±30s）避免大量 key 同时过期导致惊群效应。
    """
    try:
        ttl = DASHBOARD_CACHE_TTL + random.randint(-30, 30)
        await redis_client.setex(
            cache_key, ttl, json.dumps(data, default=str)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入工作台缓存失败: %s", exc)


async def _acquire_lock(lock_key: str) -> bool:
    """尝试获取 dogpile 互斥锁（SET key 1 NX EX 10）。

    成功返回 True，失败（已被持有或 Redis 不可用）返回 False。
    """
    try:
        result = await redis_client.set(lock_key, 1, nx=True, ex=10)
        return result is not None
    except Exception as exc:  # noqa: BLE001
        logger.warning("获取工作台缓存锁失败，降级为直接查询: %s", exc)
        return False


async def _release_lock(lock_key: str) -> None:
    """释放 dogpile 互斥锁，失败时不报错。"""
    try:
        await redis_client.delete(lock_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("释放工作台缓存锁失败: %s", exc)


__all__ = [
    "DASHBOARD_CACHE_TTL",
    "GRANULARITY_DELTA",
    "get_dashboard_overview",
]
