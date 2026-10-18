"""整定知识库服务（P3-01）。

在 ActionTracker 验证完成时聚合生成知识库条目（不可变快照），
支持列表查询和相似案例推荐。

数据来源：ActionTracker + TuningRecord（可选）+ LoopLedger。
幂等：同 tracker_id 重复生成时 ON CONFLICT(tracker_id) DO UPDATE。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.loop import LoopLedger
from app.models.tracker import ActionTracker
from app.models.tuning import TuningRecord
from app.models.tuning_knowledge import TuningKnowledgeEntry

logger = logging.getLogger(__name__)

# 时间窗口兜底匹配范围（实施时间前后 7 天）
_TIME_WINDOW_DAYS = 7

# TuningRecord 可用状态（已辨识/已完成/已验证）
_TUNING_USABLE_STATUSES = ("SIMULATED", "COMPLETED", "VERIFIED")


async def _find_tuning_record(
    db: AsyncSession,
    tracker: ActionTracker,
) -> tuple[TuningRecord | None, str]:
    """关联 TuningRecord（hybrid 策略）。

    Returns:
        (tuning_record, match_source) — match_source: exact/time_window/none
    """
    # 1. 优先用外键精确关联
    if tracker.tuning_record_id:
        result = await db.execute(
            select(TuningRecord).where(TuningRecord.id == tracker.tuning_record_id)
        )
        record = result.scalar_one_or_none()
        if record:
            return record, "exact"

    # 2. 时间窗口兜底：loop_id + implemented_at ±7d 查最近可用 TuningRecord
    implemented_at = tracker.implemented_at or tracker.updated_at
    if not implemented_at or not tracker.loop_id:
        return None, "none"

    window_start = implemented_at - timedelta(days=_TIME_WINDOW_DAYS)
    window_end = implemented_at + timedelta(days=_TIME_WINDOW_DAYS)
    result = await db.execute(
        select(TuningRecord)
        .where(TuningRecord.loop_id == tracker.loop_id)
        .where(TuningRecord.status.in_(_TUNING_USABLE_STATUSES))
        .where(TuningRecord.created_at >= window_start)
        .where(TuningRecord.created_at <= window_end)
        .order_by(TuningRecord.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record:
        return record, "time_window"

    return None, "none"


async def generate_knowledge_entry(
    db: AsyncSession,
    tracker: ActionTracker,
) -> TuningKnowledgeEntry | None:
    """验证完成时聚合生成知识库条目（幂等）。

    聚合 ActionTracker + TuningRecord（可选）+ LoopLedger 生成不可变快照。
    幂等：同 tracker_id 重复生成时 ON CONFLICT(tracker_id) DO UPDATE 刷新。

    Returns:
        TuningKnowledgeEntry 或 None（缺少必要数据时跳过）
    """
    if not tracker.loop_id:
        logger.warning("tracker %s 无 loop_id，跳过知识库生成", tracker.id)
        return None

    # 获取 LoopLedger
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == tracker.loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        logger.warning(
            "tracker %s 的 loop %s 不存在，跳过知识库生成",
            tracker.id,
            tracker.loop_id,
        )
        return None

    # 关联 TuningRecord（hybrid）
    tuning_record, match_source = await _find_tuning_record(db, tracker)

    # 构建 PID 快照
    pid_after = None
    if (
        tracker.new_pid_p is not None
        or tracker.new_pid_i is not None
        or tracker.new_pid_d is not None
    ):
        pid_after = {
            "p": tracker.new_pid_p,
            "i": tracker.new_pid_i,
            "d": tracker.new_pid_d,
        }
    pid_before = tuning_record.current_pid if tuning_record else None

    # 改善幅度（直接复用 ab_compare_summary）
    kpi_summary = tracker.ab_compare_summary
    improved_count = None
    deteriorated_count = None
    if isinstance(kpi_summary, dict):
        improved_count = kpi_summary.get("improvedCount")
        deteriorated_count = kpi_summary.get("deterioratedCount")

    values = {
        "id": str(uuid4()),
        "tracker_id": tracker.id,
        "tuning_record_id": tuning_record.id if tuning_record else None,
        "loop_id": tracker.loop_id,
        "loop_type": loop.loop_type,
        "control_type": loop.control_type,
        "tag_name": loop.tag_name,
        "diagnosis_label": tracker.diagnosis_label,
        "severity": tracker.severity,
        "model_type": tuning_record.model_type if tuning_record else None,
        "algorithm": tuning_record.algorithm if tuning_record else None,
        "identify_method": tuning_record.identify_method if tuning_record else None,
        "confidence_level": tuning_record.confidence_level if tuning_record else None,
        "pid_before": pid_before,
        "pid_after": pid_after,
        "kpi_summary": kpi_summary,
        "effect_verified": tracker.effect_verified,
        "improved_count": improved_count,
        "deteriorated_count": deteriorated_count,
        "match_source": match_source,
        "implemented_at": tracker.implemented_at,
        "verified_at": tracker.effect_verified_at,
    }

    # 幂等：ON CONFLICT(tracker_id) DO UPDATE（验证任务重试时刷新）
    update_fields = {k: v for k, v in values.items() if k not in ("id", "tracker_id")}
    stmt = insert(TuningKnowledgeEntry).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tracker_id"],
        set_=update_fields,
    )
    await db.execute(stmt)
    await db.commit()

    # 返回生成的条目
    result = await db.execute(
        select(TuningKnowledgeEntry).where(TuningKnowledgeEntry.tracker_id == tracker.id)
    )
    entry = result.scalar_one_or_none()
    logger.info(
        "知识库条目生成: tracker=%s, loop=%s, label=%s, match=%s, effect=%s",
        tracker.id,
        tracker.loop_id,
        tracker.diagnosis_label,
        match_source,
        tracker.effect_verified,
    )
    return entry


async def list_knowledge_entries(
    db: AsyncSession,
    *,
    loop_type: str | None = None,
    diagnosis_label: str | None = None,
    algorithm: str | None = None,
    effect_verified: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """知识库列表查询（支持筛选+分页）。

    Returns dict keys: items / total / page / pageSize / stats。
    其中 stats 为当前筛选条件下的全局聚合（非当前页），见 TuningKnowledgeListStats。
    """
    stmt = select(TuningKnowledgeEntry)
    count_stmt = select(func.count(TuningKnowledgeEntry.id))

    # IA 整改 C-2/T-3：stats 复用完全相同的 WHERE 条件，保证 total 与 stats 维度一致
    stats_stmt = select(
        func.count(TuningKnowledgeEntry.id).label("total"),
        func.count(case((TuningKnowledgeEntry.effect_verified.is_(True), 1))).label("improved"),
        func.count(case((TuningKnowledgeEntry.effect_verified.is_(False), 1))).label(
            "deteriorated"
        ),
        func.count(case((TuningKnowledgeEntry.effect_verified.is_(None), 1))).label("unverified"),
        func.avg(TuningKnowledgeEntry.improved_count).label("avg_improved"),
    )

    if loop_type:
        stmt = stmt.where(TuningKnowledgeEntry.loop_type == loop_type)
        count_stmt = count_stmt.where(TuningKnowledgeEntry.loop_type == loop_type)
        stats_stmt = stats_stmt.where(TuningKnowledgeEntry.loop_type == loop_type)
    if diagnosis_label:
        stmt = stmt.where(TuningKnowledgeEntry.diagnosis_label == diagnosis_label)
        count_stmt = count_stmt.where(TuningKnowledgeEntry.diagnosis_label == diagnosis_label)
        stats_stmt = stats_stmt.where(TuningKnowledgeEntry.diagnosis_label == diagnosis_label)
    if algorithm:
        stmt = stmt.where(TuningKnowledgeEntry.algorithm == algorithm)
        count_stmt = count_stmt.where(TuningKnowledgeEntry.algorithm == algorithm)
        stats_stmt = stats_stmt.where(TuningKnowledgeEntry.algorithm == algorithm)
    if effect_verified is not None:
        stmt = stmt.where(TuningKnowledgeEntry.effect_verified == effect_verified)
        count_stmt = count_stmt.where(TuningKnowledgeEntry.effect_verified == effect_verified)
        stats_stmt = stats_stmt.where(TuningKnowledgeEntry.effect_verified == effect_verified)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # stats 聚合一次查询拿到全部指标
    # 用 .one() 拿到单行（聚合查询无 GROUP BY 必返回一行，即使全表空）；
    # SQLite/PG 行为一致：count 返回 0，avg 返回 NULL。
    stats_row = (await db.execute(stats_stmt)).one()
    stats_total = stats_row.total or 0
    improved = stats_row.improved or 0
    deteriorated = stats_row.deteriorated or 0
    unverified = stats_row.unverified or 0
    avg_improved_raw = stats_row.avg_improved
    # avg_improved: Decimal|None → float|None，四舍五入保留 2 位
    if avg_improved_raw is None:
        avg_improved = None
    else:
        try:
            avg_improved = round(float(avg_improved_raw), 2)
        except (TypeError, ValueError):
            avg_improved = None

    stats = {
        "total": int(stats_total or 0),
        "improvedCount": int(improved or 0),
        "deterioratedCount": int(deteriorated or 0),
        "unverifiedCount": int(unverified or 0),
        "avgImprovedMetrics": avg_improved,
    }

    stmt = stmt.order_by(desc(TuningKnowledgeEntry.created_at))
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    items = list(result.scalars().all())

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "stats": stats,
    }


async def get_knowledge_entry(
    db: AsyncSession,
    entry_id: str,
) -> TuningKnowledgeEntry | None:
    """获取知识库条目详情。"""
    result = await db.execute(
        select(TuningKnowledgeEntry).where(TuningKnowledgeEntry.id == entry_id)
    )
    return result.scalar_one_or_none()


async def recommend_similar(
    db: AsyncSession,
    *,
    loop_id: str | None = None,
    loop_type: str | None = None,
    diagnosis_label: str | None = None,
    limit: int = 5,
) -> list[TuningKnowledgeEntry]:
    """相似案例推荐。

    优先级：diagnosis_label 相同 > loop_type 相同，
    effect_verified=True 优先 + improved_count 降序，排除当前 loop_id 自身。
    """
    stmt = select(TuningKnowledgeEntry)

    # 排除自身
    if loop_id:
        stmt = stmt.where(TuningKnowledgeEntry.loop_id != loop_id)

    # 筛选条件：label 或 loop_type 匹配
    conditions = []
    if diagnosis_label:
        conditions.append(TuningKnowledgeEntry.diagnosis_label == diagnosis_label)
    if loop_type:
        conditions.append(TuningKnowledgeEntry.loop_type == loop_type)

    if conditions:
        stmt = stmt.where(or_(*conditions))

    # 排序：effect_verified=True 优先，improved_count 降序
    stmt = stmt.order_by(
        desc(TuningKnowledgeEntry.effect_verified),
        desc(TuningKnowledgeEntry.improved_count),
    )
    stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())
