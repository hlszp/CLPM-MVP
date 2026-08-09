"""统一关注队列聚合服务（整改方案 §8.1）。

聚合五类关注来源为统一关注队列：
- ALERT：ACTIVE/ACKNOWLEDGED/SUPPRESSED 预警事件
- DEGRADATION：dayTrend=WORSENED 且 scoreDelta<=-2
- DATA_QUALITY：完整性 WARNING/CRITICAL 或 可信度 D/E
- TRACKER：PENDING/IN_PROGRESS 的 Action Tracker
- VERIFICATION：VERIFYING 超过验证周期（24h）

不新增数据库表；聚合现有 alert_event / kpi_snapshot_hourly /
loop_integrity_snapshot / loop_confidence_latest / action_tracker 数据。

优先级规则（透明可解释）：
- URGENT：CRITICAL 活跃预警
- HIGH：ERROR 活跃预警、验证超期、完整性 CRITICAL、scoreDelta <= -10
- MEDIUM：WARN、开放 Tracker、完整性 WARNING、-10 < scoreDelta <= -5
- LOW：INFO、-5 < scoreDelta <= -2、可信度 D/E 但无安全预警
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.loop import LoopLedger
from app.models.metric import (
    KpiSnapshotHourly,
    LoopConfidenceLatest,
    LoopIntegritySnapshot,
)
from app.models.tracker import ActionTracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 验证周期（小时）——对齐 diagnosis.py verifyOverdueCount 口径
VERIFICATION_PERIOD_HOURS = 24

#: 评分恶化阈值
SCORE_DELTA_DEGRADATION = -2  # 进入关注队列的最低门槛
SCORE_DELTA_HIGH = -10  # HIGH 优先级
SCORE_DELTA_MEDIUM = -5  # MEDIUM 优先级

#: 预警事件状态映射
ALERT_STATUS_MAP: dict[str, str] = {
    "ACTIVE": "OPEN",
    "ACKNOWLEDGED": "ACKNOWLEDGED",
    "SUPPRESSED": "SUPPRESSED",
}

#: 预警严重度到优先级
ALERT_SEVERITY_PRIORITY: dict[str, str] = {
    "CRITICAL": "URGENT",
    "ERROR": "HIGH",
    "WARN": "MEDIUM",
    "INFO": "LOW",
}

#: Tracker 状态映射
TRACKER_STATUS_MAP: dict[str, str] = {
    "PENDING": "OPEN",
    "IN_PROGRESS": "IN_PROGRESS",
}

# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------


class _RawItem:
    """聚合过程中的中间关注项（排序前）。"""

    def __init__(
        self,
        *,
        source: str,
        source_id: str,
        loop_id: str,
        tag_name: str,
        unit_name: str | None,
        title: str,
        summary: str,
        priority: str,
        source_severity: str | None,
        status: str,
        source_status: str,
        rank_reasons: list[str],
        occurred_at: datetime,
        updated_at: datetime | None,
        confidence_level: str | None,
        score: float | None,
        score_delta: float | None,
        event_id: str | None,
        tracker_id: str | None,
        task_id: str | None,
    ) -> None:
        self.source = source
        self.source_id = source_id
        self.loop_id = loop_id
        self.tag_name = tag_name
        self.unit_name = unit_name
        self.title = title
        self.summary = summary
        self.priority = priority
        self.source_severity = source_severity
        self.status = status
        self.source_status = source_status
        self.rank_reasons = rank_reasons
        self.occurred_at = occurred_at
        self.updated_at = updated_at
        self.confidence_level = confidence_level
        self.score = score
        self.score_delta = score_delta
        self.event_id = event_id
        self.tracker_id = tracker_id
        self.task_id = task_id


# ---------------------------------------------------------------------------
# 排序权重
# ---------------------------------------------------------------------------

_PRIORITY_ORDER: dict[str, int] = {
    "URGENT": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}

_STATUS_ORDER: dict[str, int] = {
    "OPEN": 0,
    "VERIFYING": 1,
    "IN_PROGRESS": 2,
    "ACKNOWLEDGED": 3,
    "SUPPRESSED": 4,
}


def _sort_stage(item: _RawItem) -> int:
    """同级排序阶段：未确认 → 超期 → 处理中/验证中 → 已确认 → 已抑制。

    - OPEN（未确认）= 0
    - 超期（VERIFICATION overdue）= 1
    - IN_PROGRESS / VERIFYING（处理中/验证中，未超期）= 2
    - ACKNOWLEDGED（已确认）= 3
    - SUPPRESSED（已抑制）= 4
    - 其他 = 5
    """
    if item.status == "OPEN":
        return 0
    if _is_overdue(item):
        return 1
    if item.status in ("IN_PROGRESS", "VERIFYING"):
        return 2
    if item.status == "ACKNOWLEDGED":
        return 3
    if item.status == "SUPPRESSED":
        return 4
    return 5


def _sort_key(item: _RawItem) -> tuple[int, int, float]:
    """排序键：优先级 → 同级阶段 → 时间倒序。"""
    priority_rank = _PRIORITY_ORDER.get(item.priority, 9)
    stage = _sort_stage(item)
    # 时间倒序：越新越小（取负）
    time_rank = -item.occurred_at.timestamp() if item.occurred_at else 0.0
    return (priority_rank, stage, time_rank)


def _upgrade_priority(current: str, target: str) -> str:
    """返回 current 和 target 中更高（数值更小）的优先级。"""
    if _PRIORITY_ORDER[target] < _PRIORITY_ORDER[current]:
        return target
    return current


def _is_overdue(item: _RawItem) -> bool:
    """判断是否超期（仅 VERIFICATION 来源）。"""
    if item.source != "VERIFICATION":
        return False
    if item.updated_at is None:
        return False
    now = datetime.now(UTC).replace(tzinfo=None)
    return (now - item.updated_at) > timedelta(hours=VERIFICATION_PERIOD_HOURS)


# ---------------------------------------------------------------------------
# 动作生成
# ---------------------------------------------------------------------------


def _build_actions(
    *,
    source: str,
    loop_id: str,
    event_id: str | None,
    tracker_id: str | None,
    role: str,
) -> tuple[dict, list[dict]]:
    """按角色和来源生成 primaryAction 和 actions 列表。

    返回 (primary_action, actions_list)，均为 dict 便于后续构造。
    """
    workbench_target = {
        "route": "/monitor/loop-workbench",
        "query": {"loopId": loop_id, "section": "overview"},
    }
    if event_id:
        workbench_target["query"]["eventId"] = event_id
    if tracker_id:
        workbench_target["query"]["trackerId"] = tracker_id

    alert_history_target = {
        "route": "/monitor/alerts",
        "query": {"loopId": loop_id},
    }
    if event_id:
        alert_history_target["query"]["eventId"] = event_id

    overview_target = {
        "route": "/dashboard/workbench",
        "query": {},
    }

    detail_target = {
        "route": "/monitor/attention",
        "query": {"loopId": loop_id},
    }
    if event_id:
        detail_target["query"]["eventId"] = event_id

    # Sponsor：只读，不返回 OPEN_WORKBENCH
    if role == "SPONSOR":
        view_detail = {
            "type": "VIEW_DETAIL",
            "label": "查看详情",
            "enabled": True,
            "target": detail_target,
        }
        back_overview = {
            "type": "BACK_TO_OVERVIEW",
            "label": "返回运行总览",
            "enabled": True,
            "target": overview_target,
        }
        return view_detail, [view_detail, back_overview]

    actions: list[dict] = []

    # 所有非 Sponsor 角色均可进入工作台
    actions.append(
        {
            "type": "OPEN_WORKBENCH",
            "label": "进入工作台",
            "enabled": True,
            "target": workbench_target,
        }
    )

    # ALERT 来源：确认/处置/误报/查看预警记录
    if source == "ALERT" and event_id:
        # PE/EXPERT 无写权限
        can_write = role in ("ADMIN", "IC_ENGINEER")
        actions.append(
            {
                "type": "ACKNOWLEDGE",
                "label": "确认",
                "enabled": can_write,
                "disabled_reason": None if can_write else "当前角色无确认权限",
            }
        )
        actions.append(
            {
                "type": "RESOLVE",
                "label": "处置",
                "enabled": can_write,
                "disabled_reason": None if can_write else "当前角色无处置权限",
            }
        )
        actions.append(
            {
                "type": "MARK_FALSE_POSITIVE",
                "label": "标记误报",
                "enabled": can_write,
                "disabled_reason": None if can_write else "当前角色无误报标记权限",
            }
        )
        actions.append(
            {
                "type": "VIEW_ALERT_HISTORY",
                "label": "查看预警记录",
                "enabled": True,
                "target": alert_history_target,
            }
        )

    # TRACKER/VERIFICATION 来源：创建 Tracker（ADMIN/IC）
    if source in ("TRACKER", "VERIFICATION") and tracker_id:
        actions.append(
            {
                "type": "VIEW_DETAIL",
                "label": "查看工单",
                "enabled": True,
                "target": {
                    "route": "/monitor/attention",
                    "query": {"trackerId": tracker_id},
                },
            }
        )

    # 主动作：优先 OPEN_WORKBENCH
    primary = (
        actions[0]
        if actions
        else {
            "type": "VIEW_DETAIL",
            "label": "查看详情",
            "enabled": True,
            "target": detail_target,
        }
    )

    return primary, actions


# ---------------------------------------------------------------------------
# 聚合：ALERT
# ---------------------------------------------------------------------------


async def _aggregate_alerts(
    db: AsyncSession,
    loop_ids: set[str] | None,
) -> list[_RawItem]:
    """聚合活跃预警（ACTIVE/ACKNOWLEDGED/SUPPRESSED）。"""
    stmt = (
        select(AlertEvent, LoopLedger)
        .join(LoopLedger, AlertEvent.loop_id == LoopLedger.id)
        .where(
            AlertEvent.status.in_(("ACTIVE", "ACKNOWLEDGED", "SUPPRESSED")),
            LoopLedger.is_active.is_(True),
        )
        .order_by(AlertEvent.triggered_at.desc())
    )
    if loop_ids is not None:
        stmt = stmt.where(AlertEvent.loop_id.in_(list(loop_ids)))

    result = await db.execute(stmt)
    items: list[_RawItem] = []
    for evt, loop in result.all():
        status = ALERT_STATUS_MAP.get(evt.status, "OPEN")
        priority = ALERT_SEVERITY_PRIORITY.get(evt.severity, "LOW")
        reasons: list[str] = []
        if evt.severity == "CRITICAL":
            reasons.append("严重预警未确认")
        elif evt.severity == "ERROR":
            reasons.append("高级别预警未处置")
        else:
            reasons.append(f"{evt.severity} 级预警待处理")
        if evt.trigger_count and evt.trigger_count > 1:
            reasons.append(f"重复触发 {evt.trigger_count} 次")

        items.append(
            _RawItem(
                source="ALERT",
                source_id=str(evt.id),
                loop_id=str(loop.id),
                tag_name=loop.tag_name,
                unit_name=None,
                title=f"预警 {evt.rule_code}",
                summary=f"规则 {evt.rule_code} 触发，严重度 {evt.severity}",
                priority=priority,
                source_severity=evt.severity,
                status=status,
                source_status=evt.status,
                rank_reasons=reasons,
                occurred_at=evt.triggered_at,
                updated_at=evt.acknowledged_at or evt.resolved_at,
                confidence_level=evt.confidence_level,
                score=None,
                score_delta=None,
                event_id=str(evt.id),
                tracker_id=str(evt.tracker_id) if evt.tracker_id else None,
                task_id=None,
            )
        )
    return items


# ---------------------------------------------------------------------------
# 聚合：DEGRADATION + DATA_QUALITY（批量查 KPI 快照/完整性/可信度）
# ---------------------------------------------------------------------------


async def _aggregate_degradation_and_data_quality(
    db: AsyncSession,
    loop_ids: set[str] | None,
) -> list[_RawItem]:
    """聚合评分恶化（DEGRADATION）和数据质量（DATA_QUALITY）。

    DEGRADATION：dayTrend=WORSENED 且 scoreDelta<=-2
    DATA_QUALITY：完整性 WARNING/CRITICAL 或 可信度 D/E
    每回路每来源最多一项。
    """
    # 查询活跃回路
    loop_stmt = select(LoopLedger).where(LoopLedger.is_active.is_(True))
    if loop_ids is not None:
        loop_stmt = loop_stmt.where(LoopLedger.id.in_(list(loop_ids)))
    loop_result = await db.execute(loop_stmt)
    loops = loop_result.scalars().all()
    if not loops:
        return []

    active_loop_ids = [str(loop.id) for loop in loops]
    loop_map = {str(loop.id): loop for loop in loops}

    # 批量查最新 KPI 快照 + 昨日基线（DISTINCT ON）
    snap_map: dict[str, KpiSnapshotHourly] = {}
    prev_map: dict[str, KpiSnapshotHourly] = {}
    if active_loop_ids:
        s_stmt = (
            select(KpiSnapshotHourly)
            .where(KpiSnapshotHourly.loop_id.in_(active_loop_ids))
            .distinct(KpiSnapshotHourly.loop_id)
            .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_end.desc())
        )
        for snap in (await db.execute(s_stmt)).scalars().all():
            snap_map[str(snap.loop_id)] = snap

        p_stmt = (
            select(KpiSnapshotHourly)
            .where(
                KpiSnapshotHourly.loop_id.in_(active_loop_ids),
                KpiSnapshotHourly.ts_end < func.date_trunc("day", func.now()),
            )
            .distinct(KpiSnapshotHourly.loop_id)
            .order_by(KpiSnapshotHourly.loop_id, KpiSnapshotHourly.ts_end.desc())
        )
        for snap in (await db.execute(p_stmt)).scalars().all():
            prev_map[str(snap.loop_id)] = snap

    # 批量查最新完整性快照
    integrity_map: dict[str, LoopIntegritySnapshot] = {}
    if active_loop_ids:
        i_stmt = (
            select(LoopIntegritySnapshot)
            .where(LoopIntegritySnapshot.loop_id.in_(active_loop_ids))
            .distinct(LoopIntegritySnapshot.loop_id)
            .order_by(
                LoopIntegritySnapshot.loop_id,
                LoopIntegritySnapshot.check_date.desc(),
            )
        )
        for snap in (await db.execute(i_stmt)).scalars().all():
            integrity_map[str(snap.loop_id)] = snap

    # 批量查最新可信度
    confidence_map: dict[str, LoopConfidenceLatest] = {}
    if active_loop_ids:
        c_stmt = select(LoopConfidenceLatest).where(
            LoopConfidenceLatest.loop_id.in_(active_loop_ids)
        )
        for snap in (await db.execute(c_stmt)).scalars().all():
            confidence_map[str(snap.loop_id)] = snap

    items: list[_RawItem] = []

    for lid, loop in loop_map.items():
        snap = snap_map.get(lid)
        prev = prev_map.get(lid)

        # --- DEGRADATION ---
        if snap and snap.score is not None:
            list_score = float(snap.score)
            if prev is None or prev.score is None:
                score_delta: float | None = None
                day_trend = "NEW"
            else:
                score_delta = round(list_score - float(prev.score), 1)
                if score_delta <= -2:
                    day_trend = "WORSENED"
                elif score_delta >= 2:
                    day_trend = "IMPROVED"
                else:
                    day_trend = "FLAT"

            if day_trend == "WORSENED" and score_delta is not None:
                if score_delta <= SCORE_DELTA_HIGH:
                    priority = "HIGH"
                elif score_delta <= SCORE_DELTA_MEDIUM:
                    priority = "MEDIUM"
                else:
                    priority = "LOW"

                reasons = [f"评分下降 {score_delta} 分"]
                if list_score < 60:
                    reasons.append(f"当前评分 {list_score} 偏低")

                items.append(
                    _RawItem(
                        source="DEGRADATION",
                        source_id=str(snap.id),
                        loop_id=lid,
                        tag_name=loop.tag_name,
                        unit_name=None,
                        title=f"评分恶化 {score_delta} 分",
                        summary=f"回路 {loop.tag_name} 评分较昨日下降 {score_delta} 分",
                        priority=priority,
                        source_severity=None,
                        status="OPEN",
                        source_status="WORSENED",
                        rank_reasons=reasons,
                        occurred_at=snap.ts_end,
                        updated_at=snap.ts_end,
                        confidence_level=snap.confidence_level,
                        score=list_score,
                        score_delta=score_delta,
                        event_id=None,
                        tracker_id=None,
                        task_id=None,
                    )
                )

        # --- DATA_QUALITY ---
        dq_reasons: list[str] = []
        dq_priority = "LOW"
        dq_time: datetime | None = None
        integrity = integrity_map.get(lid)
        confidence = confidence_map.get(lid)

        if integrity and integrity.status in ("WARNING", "CRITICAL"):
            if integrity.status == "CRITICAL":
                dq_priority = "HIGH"
                dq_reasons.append("数据完整性严重不足")
            else:
                dq_priority = _upgrade_priority(dq_priority, "MEDIUM")
                dq_reasons.append("数据完整性告警")
            pv_comp = integrity.pv_completeness
            if pv_comp is not None:
                dq_reasons.append(f"PV 完整度 {pv_comp:.0%}")
            dq_time = integrity.ts_end

        if confidence and confidence.confidence_level in ("D", "E"):
            dq_priority = _upgrade_priority(dq_priority, "MEDIUM")
            dq_reasons.append(f"可信度等级 {confidence.confidence_level}")
            conf_time = confidence.eval_time
            if dq_time is None or (conf_time and conf_time > dq_time):
                dq_time = conf_time

        if dq_reasons and dq_time:
            items.append(
                _RawItem(
                    source="DATA_QUALITY",
                    source_id=lid,
                    loop_id=lid,
                    tag_name=loop.tag_name,
                    unit_name=None,
                    title="数据质量异常",
                    summary=f"回路 {loop.tag_name} " + "；".join(dq_reasons),
                    priority=dq_priority,
                    source_severity=None,
                    status="OPEN",
                    source_status="DATA_QUALITY_ISSUE",
                    rank_reasons=dq_reasons,
                    occurred_at=dq_time,
                    updated_at=dq_time,
                    confidence_level=confidence.confidence_level if confidence else None,
                    score=float(confidence.score) if confidence and confidence.score else None,
                    score_delta=None,
                    event_id=None,
                    tracker_id=None,
                    task_id=None,
                )
            )

    return items


# ---------------------------------------------------------------------------
# 聚合：TRACKER + VERIFICATION
# ---------------------------------------------------------------------------


async def _aggregate_trackers(
    db: AsyncSession,
    loop_ids: set[str] | None,
) -> list[_RawItem]:
    """聚合开放 Tracker（PENDING/IN_PROGRESS）和验证超期（VERIFYING）。

    VERIFYING 超过验证周期的归入 VERIFICATION 来源，不进入 TRACKER。
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    overdue_threshold = now - timedelta(hours=VERIFICATION_PERIOD_HOURS)

    stmt = (
        select(ActionTracker, LoopLedger)
        .join(LoopLedger, ActionTracker.loop_id == LoopLedger.id, isouter=True)
        .where(
            ActionTracker.action_status.in_(("PENDING", "IN_PROGRESS", "VERIFYING")),
        )
        .order_by(ActionTracker.created_at.desc())
    )
    if loop_ids is not None:
        stmt = stmt.where(ActionTracker.loop_id.in_(list(loop_ids)))

    result = await db.execute(stmt)
    items: list[_RawItem] = []
    for tracker, loop in result.all():
        if loop is None:
            continue
        if not loop.is_active:
            continue
        lid = str(loop.id)

        if tracker.action_status == "VERIFYING":
            # 判断是否超期
            updated = tracker.updated_at or tracker.created_at
            is_overdue = updated < overdue_threshold
            if not is_overdue:
                continue  # 未超期的 VERIFYING 不进入关注队列

            overdue_hours = (now - updated).total_seconds() / 3600
            reasons = [f"验证已超期 {overdue_hours:.0f} 小时"]
            if tracker.severity:
                reasons.append(f"严重度 {tracker.severity}")

            items.append(
                _RawItem(
                    source="VERIFICATION",
                    source_id=str(tracker.id),
                    loop_id=lid,
                    tag_name=loop.tag_name,
                    unit_name=None,
                    title="验证超期",
                    summary=(f"回路 {loop.tag_name} 实施后验证超期 {overdue_hours:.0f} 小时"),
                    priority="HIGH",
                    source_severity=tracker.severity,
                    status="VERIFYING",
                    source_status="VERIFYING",
                    rank_reasons=reasons,
                    occurred_at=tracker.created_at,
                    updated_at=updated,
                    confidence_level=None,
                    score=None,
                    score_delta=None,
                    event_id=None,
                    tracker_id=str(tracker.id),
                    task_id=None,
                )
            )
        else:
            # PENDING / IN_PROGRESS → TRACKER 来源
            status = TRACKER_STATUS_MAP.get(tracker.action_status, "OPEN")
            reasons: list[str] = []
            if tracker.action_status == "PENDING":
                reasons.append("待处置工单")
            else:
                reasons.append("处理中工单")
            if tracker.severity:
                reasons.append(f"严重度 {tracker.severity}")

            priority = "MEDIUM"
            if tracker.severity == "CRITICAL":
                priority = "URGENT"
            elif tracker.severity == "ERROR":
                priority = "HIGH"

            label = tracker.diagnosis_label or "异常"
            items.append(
                _RawItem(
                    source="TRACKER",
                    source_id=str(tracker.id),
                    loop_id=lid,
                    tag_name=loop.tag_name,
                    unit_name=None,
                    title=f"工单：{label}",
                    summary=f"回路 {loop.tag_name} 工单 {label} 待处置",
                    priority=priority,
                    source_severity=tracker.severity,
                    status=status,
                    source_status=tracker.action_status,
                    rank_reasons=reasons,
                    occurred_at=tracker.created_at,
                    updated_at=tracker.updated_at,
                    confidence_level=None,
                    score=None,
                    score_delta=None,
                    event_id=None,
                    tracker_id=str(tracker.id),
                    task_id=None,
                )
            )
    return items


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def list_attention(
    db: AsyncSession,
    *,
    plant_node_id: str | None = None,
    sources: list[str] | None = None,
    priorities: list[str] | None = None,
    statuses: list[str] | None = None,
    loop_id: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    role: str = "ADMIN",
) -> dict:
    """查询统一关注队列。

    Returns:
        ``{items, total, page, pageSize, aggregates}``
    """
    # 确定回路范围（按装置筛选）
    loop_ids: set[str] | None = None
    if plant_node_id or loop_id:
        l_stmt = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        if plant_node_id:
            l_stmt = l_stmt.where(LoopLedger.unit_id == plant_node_id)
        if loop_id:
            l_stmt = l_stmt.where(LoopLedger.id == loop_id)
        lid_result = await db.execute(l_stmt)
        loop_ids = {str(r[0]) for r in lid_result.all()}
        if not loop_ids:
            return _empty_result(page, page_size)

    # 聚合五类来源
    source_filter = set(sources) if sources else None
    raw_items: list[_RawItem] = []

    if source_filter is None or "ALERT" in source_filter:
        raw_items.extend(await _aggregate_alerts(db, loop_ids))
    if source_filter is None or "DEGRADATION" in source_filter or "DATA_QUALITY" in source_filter:
        raw_items.extend(await _aggregate_degradation_and_data_quality(db, loop_ids))
    if source_filter is None or "TRACKER" in source_filter or "VERIFICATION" in source_filter:
        raw_items.extend(await _aggregate_trackers(db, loop_ids))

    # 过滤
    filtered = raw_items
    if source_filter:
        filtered = [i for i in filtered if i.source in source_filter]
    if priorities:
        filtered = [i for i in filtered if i.priority in priorities]
    if statuses:
        filtered = [i for i in filtered if i.status in statuses]
    if keyword:
        kw = keyword.lower()
        filtered = [i for i in filtered if kw in i.tag_name.lower() or kw in i.title.lower()]

    # 排序
    filtered.sort(key=_sort_key)

    # 聚合统计
    aggregates = _build_aggregates(filtered)

    # 分页
    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]

    # 构造响应
    items = [_to_response_item(i, role) for i in page_items]

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "aggregates": aggregates,
    }


def _empty_result(page: int, page_size: int) -> dict:
    return {
        "items": [],
        "total": 0,
        "page": page,
        "pageSize": page_size,
        "aggregates": {
            "bySource": {},
            "byPriority": {},
            "byStatus": {},
        },
    }


def _build_aggregates(items: list[_RawItem]) -> dict:
    by_source: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for i in items:
        by_source[i.source] = by_source.get(i.source, 0) + 1
        by_priority[i.priority] = by_priority.get(i.priority, 0) + 1
        by_status[i.status] = by_status.get(i.status, 0) + 1
    return {
        "bySource": by_source,
        "byPriority": by_priority,
        "byStatus": by_status,
    }


def _to_response_item(raw: _RawItem, role: str) -> dict:
    """将中间项转换为 API 响应 dict。"""
    primary, actions = _build_actions(
        source=raw.source,
        loop_id=raw.loop_id,
        event_id=raw.event_id,
        tracker_id=raw.tracker_id,
        role=role,
    )
    return {
        "attentionId": f"{raw.source}:{raw.source_id}",
        "source": raw.source,
        "sourceId": raw.source_id,
        "loopId": raw.loop_id,
        "tagName": raw.tag_name,
        "unitName": raw.unit_name,
        "title": raw.title,
        "summary": raw.summary,
        "priority": raw.priority,
        "sourceSeverity": raw.source_severity,
        "status": raw.status,
        "sourceStatus": raw.source_status,
        "rankReasons": raw.rank_reasons,
        "occurredAt": raw.occurred_at.isoformat() if raw.occurred_at else None,
        "updatedAt": raw.updated_at.isoformat() if raw.updated_at else None,
        "confidenceLevel": raw.confidence_level,
        "score": raw.score,
        "scoreDelta": raw.score_delta,
        "eventId": raw.event_id,
        "trackerId": raw.tracker_id,
        "taskId": raw.task_id,
        "primaryAction": primary,
        "actions": actions,
    }
