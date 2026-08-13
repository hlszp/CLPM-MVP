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
- URGENT：CRITICAL 活跃预警 或 CRITICAL 工单
- HIGH：ERROR 活跃预警、验证超期、完整性 CRITICAL、scoreDelta <= -10
- MEDIUM：WARN、开放 Tracker、完整性 WARNING、-10 < scoreDelta <= -5
- LOW：INFO、-5 < scoreDelta <= -2、可信度 D/E 但无安全预警

v1.1/v1.2 更新：
- G1: unit_name 填充为"装置·单元"格式（修复恒 null）
- G2: plantNodeId 递归解析子节点（使用 collect_descendant_node_ids）
- G3: 同回路合并分组，主表一行=一个回路组，children[] 存子项
- E-2: 截断标志 truncated，记录各来源是否达上限
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.loop import LoopLedger
from app.models.metric import (
    KpiSnapshotHourly,
    LoopConfidenceLatest,
    LoopIntegritySnapshot,
)
from app.models.plant_node import PlantNode
from app.models.tracker import ActionTracker
from app.services.plant_node_tree import collect_descendant_node_ids

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

#: 中文优先级标签
PRIORITY_LABEL: dict[str, str] = {
    "URGENT": "紧急",
    "HIGH": "高",
    "MEDIUM": "中",
    "LOW": "低",
}

#: 来源中文标签
SOURCE_LABEL: dict[str, str] = {
    "ALERT": "活跃预警",
    "DEGRADATION": "评分恶化",
    "DATA_QUALITY": "数据质量",
    "TRACKER": "待处置工单",
    "VERIFICATION": "验证超期",
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
        area_name: str | None,
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
        self.area_name = area_name
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


def _group_sort_key(group: dict[str, Any]) -> tuple[int, int, float]:
    """回路组排序键：取组首项排序键。"""
    return _sort_key(group["_first_item"])


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
# 节点名称查询（G1 修复：获取装置·单元名称）
# ---------------------------------------------------------------------------


async def _build_node_name_map(
    db: AsyncSession,
    loop_ids: set[str] | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """构建 loop_id → (area_name, unit_name) 映射。

    通过 LoopLedger.unit_id → PlantNode → parent (AREA) 两级查询，
    返回 (装置名, 单元名) 元组，用于"装置·单元"列显示。
    """
    # 1. 查询所有有 unit_id 的回路
    stmt = select(LoopLedger.id, LoopLedger.unit_id).where(
        LoopLedger.is_active.is_(True),
        LoopLedger.unit_id.isnot(None),
    )
    if loop_ids is not None:
        stmt = stmt.where(LoopLedger.id.in_(list(loop_ids)))
    result = await db.execute(stmt)
    loop_unit_pairs = result.all()

    if not loop_unit_pairs:
        return {}

    unit_ids = {str(r[1]) for r in loop_unit_pairs if r[1]}
    if not unit_ids:
        return {}

    # 2. 批量查询 UNIT 节点及其父节点
    node_stmt = select(PlantNode.id, PlantNode.name, PlantNode.parent_id, PlantNode.type).where(
        PlantNode.id.in_(list(unit_ids))
    )
    nodes_result = await db.execute(node_stmt)
    unit_nodes = {str(n.id): (n.name, n.parent_id, n.type) for n in nodes_result.all()}

    # 3. 查询父节点（AREA 级）
    parent_ids = {str(pid) for _, pid, _ in unit_nodes.values() if pid}
    parent_names: dict[str, str] = {}
    if parent_ids:
        parent_stmt = select(PlantNode.id, PlantNode.name).where(PlantNode.id.in_(list(parent_ids)))
        parent_result = await db.execute(parent_stmt)
        parent_names = {str(p.id): p.name for p in parent_result.all()}

    # 4. 构建 loop_id → (area_name, unit_name)
    name_map: dict[str, tuple[str | None, str | None]] = {}
    for loop_id, unit_id in loop_unit_pairs:
        lid = str(loop_id)
        uid = str(unit_id) if unit_id else None
        if uid and uid in unit_nodes:
            unit_name, parent_id, _ = unit_nodes[uid]
            area_name = parent_names.get(str(parent_id)) if parent_id else None
            name_map[lid] = (area_name, unit_name)
        else:
            name_map[lid] = (None, None)

    return name_map


def _format_unit_display(area_name: str | None, unit_name: str | None) -> str | None:
    """格式化装置·单元显示字符串。"""
    if area_name and unit_name:
        return f"{area_name}·{unit_name}"
    if unit_name:
        return unit_name
    if area_name:
        return area_name
    return None


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
        "query": {"loopId": loop_id, "from": "/monitor/attention", "section": "overview"},
    }
    if event_id:
        workbench_target["query"]["eventId"] = event_id
    if tracker_id:
        workbench_target["query"]["trackerId"] = tracker_id

    alert_history_target = {
        "route": "/alert/events",
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

    # Sponsor：只读，仅查看详情和返回概览
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

    # 所有非 Sponsor 角色均可进入工作台（主按钮）
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
                "disabledReason": None if can_write else "当前角色无确认权限",
            }
        )
        actions.append(
            {
                "type": "RESOLVE",
                "label": "处置",
                "enabled": can_write,
                "disabledReason": None if can_write else "当前角色无处置权限",
            }
        )
        actions.append(
            {
                "type": "MARK_FALSE_POSITIVE",
                "label": "标记误报",
                "enabled": can_write,
                "disabledReason": None if can_write else "当前角色无误报标记权限",
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

    # TRACKER/VERIFICATION 来源：查看工单
    if source in ("TRACKER", "VERIFICATION") and tracker_id:
        actions.append(
            {
                "type": "VIEW_DETAIL",
                "label": "查看工单",
                "enabled": True,
                "target": {
                    "route": "/diagnosis/tracker",
                    "query": {"trackerId": tracker_id},
                },
            }
        )

    # 主动作：优先 OPEN_WORKBENCH（工作台）
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


#: 单来源最多聚合条数（MW-P5-04 性能优化：避免 10k+ 预警全量加载）
#: 500 条足够覆盖前 25 页（pageSize=20），超出部分通过分页引导用户细化筛选
_MAX_ITEMS_PER_SOURCE = 500


async def _aggregate_alerts(
    db: AsyncSession,
    loop_ids: set[str] | None,
    name_map: dict[str, tuple[str | None, str | None]],
) -> tuple[list[_RawItem], bool]:
    """聚合活跃预警（ACTIVE/ACKNOWLEDGED/SUPPRESSED）。

    Returns:
        (items, truncated) - truncated 表示是否达到 _MAX_ITEMS_PER_SOURCE 上限
    """
    stmt = (
        select(AlertEvent, LoopLedger)
        .join(LoopLedger, AlertEvent.loop_id == LoopLedger.id)
        .where(
            AlertEvent.status.in_(("ACTIVE", "ACKNOWLEDGED", "SUPPRESSED")),
            LoopLedger.is_active.is_(True),
        )
        .order_by(AlertEvent.triggered_at.desc())
        .limit(_MAX_ITEMS_PER_SOURCE + 1)  # +1 检测是否截断
    )
    if loop_ids is not None:
        stmt = stmt.where(AlertEvent.loop_id.in_(list(loop_ids)))

    result = await db.execute(stmt)
    rows = result.all()
    truncated = len(rows) > _MAX_ITEMS_PER_SOURCE
    rows = rows[:_MAX_ITEMS_PER_SOURCE]

    items: list[_RawItem] = []
    for evt, loop in rows:
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

        lid = str(loop.id)
        area_name, unit_name = name_map.get(lid, (None, None))

        items.append(
            _RawItem(
                source="ALERT",
                source_id=str(evt.id),
                loop_id=lid,
                tag_name=loop.tag_name,
                unit_name=unit_name,
                area_name=area_name,
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
    return items, truncated


# ---------------------------------------------------------------------------
# 聚合：DEGRADATION + DATA_QUALITY（批量查 KPI 快照/完整性/可信度）
# ---------------------------------------------------------------------------


async def _aggregate_degradation_and_data_quality(
    db: AsyncSession,
    loop_ids: set[str] | None,
    name_map: dict[str, tuple[str | None, str | None]],
) -> tuple[list[_RawItem], bool]:
    """聚合评分恶化（DEGRADATION）和数据质量（DATA_QUALITY）。

    DEGRADATION：dayTrend=WORSENED 且 scoreDelta<=-2
    DATA_QUALITY：完整性 WARNING/CRITICAL 或 可信度 D/E
    每回路每来源最多一项。

    Returns:
        (items, truncated) - 本来源无上限概念，truncated 恒为 False
    """
    # 查询活跃回路
    loop_stmt = select(LoopLedger).where(LoopLedger.is_active.is_(True))
    if loop_ids is not None:
        loop_stmt = loop_stmt.where(LoopLedger.id.in_(list(loop_ids)))
    loop_result = await db.execute(loop_stmt)
    loops = loop_result.scalars().all()
    if not loops:
        return [], False

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
        area_name, unit_name = name_map.get(lid, (None, None))

        # --- DEGRADATION ---
        if snap and snap.score is not None:
            list_score = float(snap.score)
            if prev is None or prev.score is None:
                score_delta: float | None = None
                day_trend = "NEW"
            else:
                score_delta = round(list_score - float(prev.score), 1)
                if score_delta <= SCORE_DELTA_DEGRADATION:
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
                        unit_name=unit_name,
                        area_name=area_name,
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
                    unit_name=unit_name,
                    area_name=area_name,
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

    return items, False


# ---------------------------------------------------------------------------
# 聚合：TRACKER + VERIFICATION
# ---------------------------------------------------------------------------


async def _aggregate_trackers(
    db: AsyncSession,
    loop_ids: set[str] | None,
    name_map: dict[str, tuple[str | None, str | None]],
) -> tuple[list[_RawItem], bool]:
    """聚合开放 Tracker（PENDING/IN_PROGRESS）和验证超期（VERIFYING）。

    VERIFYING 超过验证周期的归入 VERIFICATION 来源，不进入 TRACKER。

    Returns:
        (items, truncated) - truncated 表示是否达到 _MAX_ITEMS_PER_SOURCE 上限
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
        .limit(_MAX_ITEMS_PER_SOURCE + 1)
    )
    if loop_ids is not None:
        stmt = stmt.where(ActionTracker.loop_id.in_(list(loop_ids)))

    result = await db.execute(stmt)
    rows = result.all()
    truncated = len(rows) > _MAX_ITEMS_PER_SOURCE
    rows = rows[:_MAX_ITEMS_PER_SOURCE]

    items: list[_RawItem] = []
    for tracker, loop in rows:
        if loop is None:
            continue
        if not loop.is_active:
            continue
        lid = str(loop.id)
        area_name, unit_name = name_map.get(lid, (None, None))

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
                    unit_name=unit_name,
                    area_name=area_name,
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
                    unit_name=unit_name,
                    area_name=area_name,
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
    return items, truncated


# ---------------------------------------------------------------------------
# 分组逻辑（G3：同回路合并）
# ---------------------------------------------------------------------------


def _group_items_by_loop(
    items: list[_RawItem],
    role: str,
) -> list[dict[str, Any]]:
    """将关注项按回路分组，每组代表一个"问题回路"。

    分组规则（v1.1）：
    - 分组主键=回路位号（tag_name + loop_id）
    - 组优先级=组内最高
    - 组首项=最高优先级、排序最靠前的项
    - 组状态/摘要/主动作=组首项
    - 时间=组内最新
    - 来源 chips 并列、rankReasons 合并去重
    """
    groups: dict[str, dict[str, Any]] = {}

    for item in items:
        key = item.loop_id
        if key not in groups:
            groups[key] = {
                "loopId": item.loop_id,
                "tagName": item.tag_name,
                "unitName": item.unit_name,
                "areaName": item.area_name,
                "children": [],
                "sources": set(),
                "allReasons": set(),
                "priority": item.priority,
                "_first_item": item,
                "_latest_time": item.occurred_at,
                "itemCount": 0,
            }

        g = groups[key]
        g["children"].append(item)
        g["sources"].add(item.source)
        for r in item.rank_reasons:
            g["allReasons"].add(r)
        g["itemCount"] += 1

        # 升级组优先级
        if _PRIORITY_ORDER[item.priority] < _PRIORITY_ORDER[g["priority"]]:
            g["priority"] = item.priority
            g["_first_item"] = item

        # 更新最新时间
        if item.occurred_at and (g["_latest_time"] is None or item.occurred_at > g["_latest_time"]):
            g["_latest_time"] = item.occurred_at

    # 组内 children 排序（按组排序键）
    for g in groups.values():
        g["children"].sort(key=_sort_key)

    # 构建组列表并排序
    group_list = list(groups.values())
    group_list.sort(key=_group_sort_key)

    # 转换为响应格式
    result: list[dict[str, Any]] = []
    for _idx, g in enumerate(group_list, 1):
        first = g["_first_item"]
        primary, actions = _build_actions(
            source=first.source,
            loop_id=g["loopId"],
            event_id=first.event_id,
            tracker_id=first.tracker_id,
            role=role,
        )

        # 子项转换为响应格式
        children_resp = []
        for child in g["children"]:
            child_primary, child_actions = _build_actions(
                source=child.source,
                loop_id=child.loop_id,
                event_id=child.event_id,
                tracker_id=child.tracker_id,
                role=role,
            )
            children_resp.append(_item_to_dict(child, child_primary, child_actions))

        # 组摘要：首项摘要 + "等 N 项"
        summary = first.summary
        if g["itemCount"] > 1:
            summary = f"{summary} · 等 {g['itemCount']} 项"

        # 显示名称：装置·单元
        location = _format_unit_display(g["areaName"], g["unitName"])

        result.append(
            {
                "groupId": f"group:{g['loopId']}",
                "loopId": g["loopId"],
                "tagName": g["tagName"],
                "unitName": location,
                "priority": g["priority"],
                "priorityLabel": PRIORITY_LABEL.get(g["priority"], g["priority"]),
                "status": first.status,
                "sources": sorted(g["sources"]),
                "sourceLabels": [SOURCE_LABEL.get(s, s) for s in sorted(g["sources"])],
                "summary": summary,
                "title": first.title,
                "updatedAt": g["_latest_time"].isoformat() if g["_latest_time"] else None,
                "isOverdue": any(_is_overdue(c) for c in g["children"]),
                "itemCount": g["itemCount"],
                "rankReasons": sorted(g["allReasons"]),
                "primaryAction": primary,
                "actions": actions,
                "children": children_resp,
            }
        )

    return result


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
    """查询统一关注队列（v1.2：同回路合并分组）。

    Returns:
        ``{groups, totalGroups, totalItems, page, pageSize, aggregates, truncated, loadedAt}``
    """
    # G2 修复：递归解析 plantNodeId 子节点
    target_unit_ids: set[str] | None = None
    loop_ids: set[str] | None = None

    if plant_node_id:
        # 查询节点类型决定递归深度
        node_stmt = select(PlantNode.type).where(PlantNode.id == plant_node_id)
        node_type_result = await db.execute(node_stmt)
        node_type_row = node_type_result.first()
        node_type = node_type_row[0] if node_type_row else None

        if node_type in ("FACTORY", "AREA"):
            # 递归获取所有子孙节点 ID
            descendant_ids = await collect_descendant_node_ids(db, plant_node_id)
            # 收集所有 UNIT 节点 ID（回路直接挂在 UNIT 下）
            all_node_ids = set(descendant_ids)
            all_node_ids.add(plant_node_id)
            # 查询这些节点下的 UNIT
            unit_stmt = select(PlantNode.id).where(
                PlantNode.id.in_(list(all_node_ids)),
                PlantNode.type == "UNIT",
            )
            unit_result = await db.execute(unit_stmt)
            target_unit_ids = {str(r[0]) for r in unit_result.all()}
        else:
            # UNIT 节点：直接匹配
            target_unit_ids = {plant_node_id}

    # 确定回路范围
    if target_unit_ids or loop_id:
        l_stmt = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
        if target_unit_ids:
            l_stmt = l_stmt.where(LoopLedger.unit_id.in_(list(target_unit_ids)))
        if loop_id:
            l_stmt = l_stmt.where(LoopLedger.id == loop_id)
        lid_result = await db.execute(l_stmt)
        loop_ids = {str(r[0]) for r in lid_result.all()}
        if not loop_ids:
            return _empty_result(page, page_size)

    # 预先构建节点名称映射（G1）
    name_map = await _build_node_name_map(db, loop_ids)

    # 聚合五类来源
    source_filter = set(sources) if sources else None
    raw_items: list[_RawItem] = []
    truncated: dict[str, bool] = {}

    if source_filter is None or "ALERT" in source_filter:
        alert_items, alert_trunc = await _aggregate_alerts(db, loop_ids, name_map)
        raw_items.extend(alert_items)
        if alert_trunc:
            truncated["ALERT"] = True

    if source_filter is None or "DEGRADATION" in source_filter or "DATA_QUALITY" in source_filter:
        dq_items, dq_trunc = await _aggregate_degradation_and_data_quality(db, loop_ids, name_map)
        raw_items.extend(dq_items)

    if source_filter is None or "TRACKER" in source_filter or "VERIFICATION" in source_filter:
        tracker_items, tracker_trunc = await _aggregate_trackers(db, loop_ids, name_map)
        raw_items.extend(tracker_items)
        if tracker_trunc:
            truncated["TRACKER"] = True

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
        filtered = [
            i
            for i in filtered
            if kw in i.tag_name.lower() or kw in i.title.lower() or kw in i.summary.lower()
        ]

    # 排序
    filtered.sort(key=_sort_key)

    # G3：分组
    groups = _group_items_by_loop(filtered, role)

    # 聚合统计（项口径）
    aggregates = _build_aggregates(filtered)

    # 组优先级统计（组口径）
    by_group_priority: dict[str, int] = {}
    for g in groups:
        p = g["priority"]
        by_group_priority[p] = by_group_priority.get(p, 0) + 1
    aggregates["byGroupPriority"] = by_group_priority
    aggregates["groupCount"] = len(groups)

    # 额外统计：验证超期数、数据质量数
    verification_overdue = sum(1 for i in filtered if i.source == "VERIFICATION")
    data_quality_count = sum(1 for i in filtered if i.source == "DATA_QUALITY")
    aggregates["verificationOverdue"] = verification_overdue
    aggregates["dataQualityCount"] = data_quality_count
    open_count = aggregates["byStatus"].get("OPEN", 0)
    aggregates["openCount"] = open_count
    urgent_count = aggregates["byPriority"].get("URGENT", 0)
    aggregates["urgentCount"] = urgent_count

    # 分页（按组分页）
    total_groups = len(groups)
    total_items = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_groups = groups[start:end]

    loaded_at = datetime.now(UTC).isoformat()

    return {
        "items": page_groups,
        "total": total_groups,
        "totalGroups": total_groups,
        "totalItems": total_items,
        "page": page,
        "pageSize": page_size,
        "aggregates": aggregates,
        "truncated": truncated,
        "loadedAt": loaded_at,
    }


def _empty_result(page: int, page_size: int) -> dict:
    return {
        "items": [],
        "total": 0,
        "totalGroups": 0,
        "totalItems": 0,
        "page": page,
        "pageSize": page_size,
        "aggregates": {
            "bySource": {},
            "byPriority": {},
            "byStatus": {},
            "byGroupPriority": {},
            "groupCount": 0,
            "openCount": 0,
            "urgentCount": 0,
            "verificationOverdue": 0,
            "dataQualityCount": 0,
        },
        "truncated": {},
        "loadedAt": datetime.now(UTC).isoformat(),
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


def _item_to_dict(raw: _RawItem, primary: dict, actions: list[dict]) -> dict:
    """将单个 RawItem 转换为 API 响应 dict。"""
    unit_display = _format_unit_display(raw.area_name, raw.unit_name)
    return {
        "attentionId": f"{raw.source}:{raw.source_id}",
        "source": raw.source,
        "sourceId": raw.source_id,
        "loopId": raw.loop_id,
        "tagName": raw.tag_name,
        "unitName": unit_display,
        "title": raw.title,
        "summary": raw.summary,
        "priority": raw.priority,
        "priorityLabel": PRIORITY_LABEL.get(raw.priority, raw.priority),
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
        "isOverdue": _is_overdue(raw),
        "primaryAction": primary,
        "actions": actions,
    }
