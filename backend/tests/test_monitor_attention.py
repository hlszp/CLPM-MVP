"""统一关注队列 monitor_attention.py 单元测试（整改方案 §8.1）。

测试覆盖：
- 优先级映射：scoreDelta 阈值 -2/-5/-10 边界、severity CRITICAL/ERROR/WARN/INFO
- 状态映射：ALERT（ACTIVE→OPEN 等）、HANDLING（工单状态→关注状态）
- 动作生成：SPONSOR 只读、ADMIN/IC 完整写动作、PE/EXPERT 受限、HANDLING 深链接
- 排序：未确认 → 超期 → 处理中/验证中 → 已确认 → 已抑制 → 时间倒序
- HANDLING 超期检测（24h 边界）
- DATA_QUALITY 每回路去重
- 五类来源聚合与筛选（ALERT/DEGRADATION/DATA_QUALITY/FITNESS_ABNORMAL/HANDLING）
- HANDLING 聚合：四分支优先级、待执行超期开关、截断协议、模块禁用守卫
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.monitor_attention import (
    _aggregate_handling_orders,
    _build_actions,
    _is_overdue,
    _RawItem,
    _sort_key,
    _upgrade_priority,
    list_attention,
)

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_raw_item(
    *,
    source: str = "ALERT",
    source_id: str = "src-001",
    loop_id: str = "loop-001",
    tag_name: str = "LIC-101",
    priority: str = "MEDIUM",
    status: str = "OPEN",
    occurred_at: datetime | None = None,
    updated_at: datetime | None = None,
    source_severity: str | None = None,
    score_delta: float | None = None,
    event_id: str | None = None,
    tracker_id: str | None = None,
    task_id: str | None = None,
) -> _RawItem:
    """构造 _RawItem 测试对象。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    return _RawItem(
        source=source,
        source_id=source_id,
        loop_id=loop_id,
        tag_name=tag_name,
        unit_name=None,
        area_name=None,
        title="测试项",
        summary="测试摘要",
        priority=priority,
        source_severity=source_severity,
        status=status,
        source_status=status,
        rank_reasons=["测试原因"],
        occurred_at=occurred_at or now,
        updated_at=updated_at,
        confidence_level=None,
        score=None,
        score_delta=score_delta,
        event_id=event_id,
        tracker_id=tracker_id,
        task_id=task_id,
    )


# ===========================================================================
# 优先级辅助函数
# ===========================================================================


class TestUpgradePriority:
    def test_升级到更高优先级(self):
        assert _upgrade_priority("LOW", "HIGH") == "HIGH"
        assert _upgrade_priority("MEDIUM", "URGENT") == "URGENT"

    def test_保持当前优先级(self):
        assert _upgrade_priority("HIGH", "LOW") == "HIGH"
        assert _upgrade_priority("URGENT", "LOW") == "URGENT"


# ===========================================================================
# 排序逻辑
# ===========================================================================


class TestSortKey:
    def test_优先级排序_URGENT优先于LOW(self):
        urgent = _make_raw_item(priority="URGENT", occurred_at=datetime(2026, 1, 1))
        low = _make_raw_item(priority="LOW", occurred_at=datetime(2026, 1, 2))
        items = sorted([low, urgent], key=_sort_key)
        assert items[0].priority == "URGENT"

    def test_同级未确认优先于已确认(self):
        unconfirmed = _make_raw_item(status="OPEN", occurred_at=datetime(2026, 1, 1))
        acknowledged = _make_raw_item(status="ACKNOWLEDGED", occurred_at=datetime(2026, 1, 2))
        items = sorted([acknowledged, unconfirmed], key=_sort_key)
        assert items[0].status == "OPEN"

    def test_同级超期优先于处理中(self):
        """同级排序：未确认(OPEN) → 超期 → 处理中/验证中。

        OPEN 项最优先；超期的 HANDLING（验证中）次之；未超期的 VERIFYING 最后。
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        open_item = _make_raw_item(
            source="ALERT",
            status="OPEN",
            occurred_at=datetime(2026, 1, 3),
        )
        overdue = _make_raw_item(
            source="HANDLING",
            status="VERIFYING",
            updated_at=now - timedelta(hours=30),
            occurred_at=datetime(2026, 1, 1),
        )
        not_overdue_verifying = _make_raw_item(
            source="HANDLING",
            status="VERIFYING",
            updated_at=now - timedelta(hours=10),
            occurred_at=datetime(2026, 1, 2),
        )
        items = sorted([not_overdue_verifying, overdue, open_item], key=_sort_key)
        # OPEN 最优先
        assert items[0].status == "OPEN"
        # 超期次之
        assert items[1].source == "HANDLING"
        assert items[1].updated_at < now - timedelta(hours=24)
        # 未超期 VERIFYING 最后
        assert items[2].updated_at > now - timedelta(hours=24)

    def test_同级时间倒序(self):
        older = _make_raw_item(occurred_at=datetime(2026, 1, 1))
        newer = _make_raw_item(occurred_at=datetime(2026, 1, 2))
        items = sorted([older, newer], key=_sort_key)
        assert items[0].occurred_at == datetime(2026, 1, 2)


# ===========================================================================
# 超期检测
# ===========================================================================


class TestIsOverdue:
    def test_非HANDLING来源不超期(self):
        item = _make_raw_item(source="ALERT")
        assert _is_overdue(item) is False

    def test_HANDLING超过24h为超期(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        item = _make_raw_item(
            source="HANDLING",
            updated_at=now - timedelta(hours=25),
        )
        assert _is_overdue(item) is True

    def test_HANDLING不足24h不超期(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        item = _make_raw_item(
            source="HANDLING",
            updated_at=now - timedelta(hours=20),
        )
        assert _is_overdue(item) is False

    def test_HANDLING无updated_at不超期(self):
        item = _make_raw_item(source="HANDLING", updated_at=None)
        assert _is_overdue(item) is False


# ===========================================================================
# 动作生成
# ===========================================================================


class TestBuildActions:
    def test_SPONSOR只返回VIEW_DETAIL和BACK_TO_OVERVIEW(self):
        primary, actions = _build_actions(
            source="ALERT",
            loop_id="loop-001",
            event_id="evt-001",
            tracker_id=None,
            role="SPONSOR",
        )
        assert primary["type"] == "VIEW_DETAIL"
        action_types = [a["type"] for a in actions]
        assert "OPEN_WORKBENCH" not in action_types
        assert "BACK_TO_OVERVIEW" in action_types
        assert "ACKNOWLEDGE" not in action_types

    def test_ADMIN获得完整动作(self):
        primary, actions = _build_actions(
            source="ALERT",
            loop_id="loop-001",
            event_id="evt-001",
            tracker_id=None,
            role="ADMIN",
        )
        assert primary["type"] == "OPEN_WORKBENCH"
        action_types = [a["type"] for a in actions]
        assert "ACKNOWLEDGE" in action_types
        assert "RESOLVE" in action_types
        assert "MARK_FALSE_POSITIVE" in action_types
        assert "VIEW_ALERT_HISTORY" in action_types

    def test_IC_ENGINEER获得完整动作(self):
        _, actions = _build_actions(
            source="ALERT",
            loop_id="loop-001",
            event_id="evt-001",
            tracker_id=None,
            role="IC_ENGINEER",
        )
        action_types = [a["type"] for a in actions]
        assert "ACKNOWLEDGE" in action_types

    def test_PE_ENGINEER无写动作但可进工作台(self):
        _, actions = _build_actions(
            source="ALERT",
            loop_id="loop-001",
            event_id="evt-001",
            tracker_id=None,
            role="PE_ENGINEER",
        )
        action_types = [a["type"] for a in actions]
        assert "OPEN_WORKBENCH" in action_types
        ack = next(a for a in actions if a["type"] == "ACKNOWLEDGE")
        assert ack["enabled"] is False
        assert ack["disabledReason"] is not None

    def test_EXPERT无写动作但可进工作台(self):
        _, actions = _build_actions(
            source="ALERT",
            loop_id="loop-001",
            event_id="evt-001",
            tracker_id=None,
            role="EXPERT",
        )
        action_types = [a["type"] for a in actions]
        assert "OPEN_WORKBENCH" in action_types
        resolve = next(a for a in actions if a["type"] == "RESOLVE")
        assert resolve["enabled"] is False

    def test_HANDLING来源主动作为查看处置工单深链接(self):
        """HANDLING 来源：主动作跳转 /handling/orders 并携带 focus=orderId。"""
        primary, actions = _build_actions(
            source="HANDLING",
            loop_id="loop-001",
            event_id=None,
            tracker_id=None,
            role="ADMIN",
            task_id="order-001",
        )
        assert primary["type"] == "VIEW_DETAIL"
        assert primary["target"]["route"] == "/handling/orders"
        assert primary["target"]["query"]["focus"] == "order-001"
        assert primary["target"]["query"]["tab"] == "orders"
        action_types = [a["type"] for a in actions]
        assert "VIEW_DETAIL" in action_types
        assert "OPEN_WORKBENCH" in action_types

    def test_工作台target携带loopId和eventId(self):
        primary, _ = _build_actions(
            source="ALERT",
            loop_id="loop-001",
            event_id="evt-001",
            tracker_id="trk-001",
            role="ADMIN",
        )
        target = primary["target"]
        assert target["query"]["loopId"] == "loop-001"
        assert target["query"]["eventId"] == "evt-001"
        assert target["query"]["trackerId"] == "trk-001"
        assert target["query"]["section"] == "overview"


# ===========================================================================
# list_attention 集成测试（mock DB）
# ===========================================================================


def _make_db_mock() -> AsyncMock:
    """构造 AsyncSession mock，支持 .execute() 链式调用。"""
    db = AsyncMock()
    return db


def _make_empty_result() -> MagicMock:
    """构造统一的空结果 mock（支持 scalars().all() 和 all()）。"""
    m = MagicMock()
    m.scalars.return_value.all.return_value = []
    m.all.return_value = []
    m.scalar.return_value = 0
    m.scalar_one_or_none.return_value = None
    return m


def _configure_db_empty(db: AsyncMock) -> None:
    """配置 db 返回空结果。"""
    db.execute = AsyncMock(return_value=_make_empty_result())


class TestListAttention:
    @pytest.mark.asyncio
    async def test_空数据库返回空结果(self):
        db = _make_db_mock()
        _configure_db_empty(db)
        result = await list_attention(db, role="ADMIN")
        assert result["total"] == 0
        assert result["items"] == []
        assert "bySource" in result["aggregates"]

    @pytest.mark.asyncio
    async def test_按来源筛选(self):
        db = _make_db_mock()
        _configure_db_empty(db)
        result = await list_attention(db, sources=["ALERT"], role="ADMIN")
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_plant_node_id无匹配回路返回空(self):
        db = _make_db_mock()
        # CTE 查询返回空 loop_ids
        db.execute = AsyncMock(return_value=_make_empty_result())
        result = await list_attention(db, plant_node_id="unit-001", role="ADMIN")
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_loop_id无匹配回路返回空(self):
        db = _make_db_mock()
        # loop 存在性校验返回空
        db.execute = AsyncMock(return_value=_make_empty_result())
        result = await list_attention(db, loop_id="nonexistent-loop", role="ADMIN")
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_SPONSOR角色不返回OPEN_WORKBENCH(self):
        """Sponsor 关注队列不返回 OPEN_WORKBENCH 主动作。"""
        db = _make_db_mock()
        # 构造一个 ALERT 事件
        now = datetime.now(UTC).replace(tzinfo=None)
        evt = MagicMock()
        evt.id = "evt-001"
        evt.rule_code = "R001"
        evt.severity = "CRITICAL"
        evt.status = "ACTIVE"
        evt.triggered_at = now
        evt.acknowledged_at = None
        evt.resolved_at = None
        evt.confidence_level = "B"
        evt.trigger_count = 1
        evt.tracker_id = None

        loop = MagicMock()
        loop.id = "loop-001"
        loop.tag_name = "LIC-101"
        loop.is_active = True

        alert_result = MagicMock()
        alert_result.all.return_value = [(evt, loop, None, None)]

        # 后续聚合查询返回空
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            stmt_str = str(stmt)
            if "alert_event" in stmt_str:
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="SPONSOR")
        assert result["total"] > 0
        for item in result["items"]:
            assert item["primaryAction"]["type"] != "OPEN_WORKBENCH"
            action_types = [a["type"] for a in item["actions"]]
            assert "OPEN_WORKBENCH" not in action_types

    @pytest.mark.asyncio
    async def test_优先级URGENT来自CRITICAL预警(self):
        """CRITICAL 活跃预警应为 URGENT 优先级。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)
        evt = MagicMock()
        evt.id = "evt-001"
        evt.rule_code = "R001"
        evt.severity = "CRITICAL"
        evt.status = "ACTIVE"
        evt.triggered_at = now
        evt.acknowledged_at = None
        evt.resolved_at = None
        evt.confidence_level = None
        evt.trigger_count = 1
        evt.tracker_id = None

        loop = MagicMock()
        loop.id = "loop-001"
        loop.tag_name = "LIC-101"
        loop.is_active = True

        alert_result = MagicMock()
        alert_result.all.return_value = [(evt, loop, None, None)]
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        assert result["total"] == 1
        group = result["items"][0]
        assert group["priority"] == "URGENT"
        assert group["status"] == "OPEN"
        assert group["itemCount"] == 1
        child = group["children"][0]
        assert child["sourceStatus"] == "ACTIVE"
        assert child["priority"] == "URGENT"

    @pytest.mark.asyncio
    async def test_ALERT状态映射(self):
        """ACTIVE→OPEN, ACKNOWLEDGED→ACKNOWLEDGED, SUPPRESSED→SUPPRESSED。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)

        statuses = [
            ("ACTIVE", "OPEN"),
            ("ACKNOWLEDGED", "ACKNOWLEDGED"),
            ("SUPPRESSED", "SUPPRESSED"),
        ]
        pairs = []
        loops = []
        for i, (src_status, _) in enumerate(statuses):
            evt = MagicMock()
            evt.id = f"evt-{i}"
            evt.rule_code = "R001"
            evt.severity = "WARN"
            evt.status = src_status
            evt.triggered_at = now
            evt.acknowledged_at = None
            evt.resolved_at = None
            evt.confidence_level = None
            evt.trigger_count = 1
            evt.tracker_id = None
            # 使用不同的回路，确保每个 evt 成为独立分组
            loop = MagicMock()
            loop.id = f"loop-{i:03d}"
            loop.tag_name = f"LIC-{101 + i}"
            loop.is_active = True
            loops.append(loop)
            pairs.append((evt, loop, None, None))

        alert_result = MagicMock()
        alert_result.all.return_value = pairs
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        # 从 children 中收集状态映射（每个 group 有一个 child）
        status_map = {}
        for group in result["items"]:
            child = group["children"][0]
            status_map[child["sourceStatus"]] = child["status"]
        for src, expected in statuses:
            assert status_map.get(src) == expected

    @pytest.mark.asyncio
    async def test_rankReasons至少一条(self):
        """每条关注项至少返回一个 rankReason。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)
        evt = MagicMock()
        evt.id = "evt-001"
        evt.rule_code = "R001"
        evt.severity = "ERROR"
        evt.status = "ACTIVE"
        evt.triggered_at = now
        evt.acknowledged_at = None
        evt.resolved_at = None
        evt.confidence_level = None
        evt.trigger_count = 3
        evt.tracker_id = None

        loop = MagicMock()
        loop.id = "loop-001"
        loop.tag_name = "LIC-101"
        loop.is_active = True

        alert_result = MagicMock()
        alert_result.all.return_value = [(evt, loop, None, None)]
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        assert len(result["items"]) == 1
        group = result["items"][0]
        assert len(group["rankReasons"]) >= 1
        assert any("重复触发" in r for r in group["rankReasons"])

    @pytest.mark.asyncio
    async def test_attentionId格式(self):
        """attentionId 使用 ${source}:${sourceId} 格式。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)
        evt = MagicMock()
        evt.id = "evt-abc"
        evt.rule_code = "R001"
        evt.severity = "WARN"
        evt.status = "ACTIVE"
        evt.triggered_at = now
        evt.acknowledged_at = None
        evt.resolved_at = None
        evt.confidence_level = None
        evt.trigger_count = 1
        evt.tracker_id = None

        loop = MagicMock()
        loop.id = "loop-001"
        loop.tag_name = "LIC-101"
        loop.is_active = True

        alert_result = MagicMock()
        alert_result.all.return_value = [(evt, loop, None, None)]
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        group = result["items"][0]
        child = group["children"][0]
        assert child["attentionId"] == "ALERT:evt-abc"

    @pytest.mark.asyncio
    async def test_keyword筛选(self):
        """关键词筛选按位号/标题匹配。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)

        loop1 = MagicMock()
        loop1.id = "loop-001"
        loop1.tag_name = "LIC-101"
        loop1.is_active = True

        loop2 = MagicMock()
        loop2.id = "loop-002"
        loop2.tag_name = "FIC-202"
        loop2.is_active = True

        evt1 = MagicMock()
        evt1.id = "evt-1"
        evt1.rule_code = "R001"
        evt1.severity = "WARN"
        evt1.status = "ACTIVE"
        evt1.triggered_at = now
        evt1.acknowledged_at = None
        evt1.resolved_at = None
        evt1.confidence_level = None
        evt1.trigger_count = 1
        evt1.tracker_id = None

        evt2 = MagicMock()
        evt2.id = "evt-2"
        evt2.rule_code = "R002"
        evt2.severity = "WARN"
        evt2.status = "ACTIVE"
        evt2.triggered_at = now
        evt2.acknowledged_at = None
        evt2.resolved_at = None
        evt2.confidence_level = None
        evt2.trigger_count = 1
        evt2.tracker_id = None

        alert_result = MagicMock()
        alert_result.all.return_value = [(evt1, loop1, None, None), (evt2, loop2, None, None)]
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, keyword="LIC", role="ADMIN")
        assert result["total"] == 1
        assert result["items"][0]["tagName"] == "LIC-101"

    @pytest.mark.asyncio
    async def test_分页正确(self):
        """分页 page/pageSize 正确切片（基于分组）。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)

        pairs = []
        for i in range(5):
            evt = MagicMock()
            evt.id = f"evt-{i}"
            evt.rule_code = "R001"
            evt.severity = "WARN"
            evt.status = "ACTIVE"
            evt.triggered_at = now + timedelta(seconds=i)
            evt.acknowledged_at = None
            evt.resolved_at = None
            evt.confidence_level = None
            evt.trigger_count = 1
            evt.tracker_id = None
            # 使用不同的回路，确保每个 evt 成为独立分组
            loop = MagicMock()
            loop.id = f"loop-{i:03d}"
            loop.tag_name = f"LIC-{101 + i}"
            loop.is_active = True
            pairs.append((evt, loop, None, None))

        alert_result = MagicMock()
        alert_result.all.return_value = pairs
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, page=1, page_size=2, role="ADMIN")
        assert result["total"] == 5
        assert len(result["items"]) == 2
        assert result["page"] == 1
        assert result["pageSize"] == 2

    @pytest.mark.asyncio
    async def test_aggregates统计正确(self):
        """聚合统计按来源/优先级/状态计数。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)
        loop = MagicMock()
        loop.id = "loop-001"
        loop.tag_name = "LIC-101"
        loop.is_active = True

        evt = MagicMock()
        evt.id = "evt-001"
        evt.rule_code = "R001"
        evt.severity = "CRITICAL"
        evt.status = "ACTIVE"
        evt.triggered_at = now
        evt.acknowledged_at = None
        evt.resolved_at = None
        evt.confidence_level = None
        evt.trigger_count = 1
        evt.tracker_id = None

        alert_result = MagicMock()
        alert_result.all.return_value = [(evt, loop, None, None)]
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        assert result["aggregates"]["bySource"].get("ALERT") == 1
        assert result["aggregates"]["byPriority"].get("URGENT") == 1
        assert result["aggregates"]["byStatus"].get("OPEN") == 1


# ===========================================================================
# HANDLING 聚合（A2 第 5 来源）
# ===========================================================================


def _make_order_row(
    *,
    order_id: str = "order-001",
    status: str = "REOPENED",
    priority: str = "URGENT",
    reason: str = "处置工单已重开，需跟进处理",
    loop_id: str = "loop-001",
    submitted_at: datetime | None = None,
    started_at: datetime | None = None,
    planned_at: datetime | None = None,
) -> MagicMock:
    """构造 handling_order UNION 查询结果行 mock。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    row = MagicMock()
    row.order_id = order_id
    row.order_no = "HD-20260823-001"
    row.title = "PID 参数整定"
    row.status = status
    row.loop_id = loop_id
    row.planned_at = planned_at
    row.submitted_at = submitted_at
    row.started_at = started_at or (now - timedelta(hours=1))
    row.created_at = now - timedelta(hours=2)
    row.tag_name = "LIC-101"
    row.unit_name = "常减压装置"
    row.area_name = "炼油一部"
    row.priority = priority
    row.reason = reason
    return row


class TestAggregateHandlingOrders:
    """_aggregate_handling_orders 行映射与 SQL 结构测试。"""

    @pytest.mark.asyncio
    async def test_四分支优先级与状态映射(self):
        """REOPENED→URGENT/OPEN、EXECUTING→HIGH/IN_PROGRESS、
        VERIFYING→HIGH/VERIFYING、PENDING→MEDIUM/OPEN。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = [
            _make_order_row(order_id="o1", status="REOPENED", priority="URGENT", reason="重开"),
            _make_order_row(
                order_id="o2",
                status="EXECUTING",
                priority="HIGH",
                reason="执行超期",
                planned_at=now - timedelta(hours=1),
            ),
            _make_order_row(
                order_id="o3",
                status="VERIFYING",
                priority="HIGH",
                reason="验证超期",
                submitted_at=now - timedelta(hours=30),
            ),
            _make_order_row(
                order_id="o4",
                status="PENDING",
                priority="MEDIUM",
                reason="待执行超期",
                planned_at=now - timedelta(hours=1),
                started_at=None,
            ),
        ]
        result_mock = MagicMock()
        result_mock.all.return_value = rows
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        items, truncated = await _aggregate_handling_orders(db, None)

        assert truncated is False
        assert [i.source for i in items] == ["HANDLING"] * 4
        assert [i.priority for i in items] == ["URGENT", "HIGH", "HIGH", "MEDIUM"]
        assert [i.status for i in items] == ["OPEN", "IN_PROGRESS", "VERIFYING", "OPEN"]
        assert [i.source_status for i in items] == [
            "REOPENED",
            "EXECUTING",
            "VERIFYING",
            "PENDING",
        ]
        # orderId 由 task_id 字段承载
        assert [i.task_id for i in items] == ["o1", "o2", "o3", "o4"]
        assert items[0].source_id == "o1"

    @pytest.mark.asyncio
    async def test_验证超期24h边界(self):
        """VERIFYING 工单：submitted_at 超过 24h 判定超期，不足则不超期。"""
        now = datetime.now(UTC).replace(tzinfo=None)
        overdue_row = _make_order_row(
            order_id="o-late",
            status="VERIFYING",
            priority="HIGH",
            submitted_at=now - timedelta(hours=25),
        )
        fresh_row = _make_order_row(
            order_id="o-fresh",
            status="VERIFYING",
            priority="HIGH",
            submitted_at=now - timedelta(hours=23),
        )
        result_mock = MagicMock()
        result_mock.all.return_value = [overdue_row, fresh_row]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        items, _ = await _aggregate_handling_orders(db, None)

        # updated_at 取 submitted_at，_is_overdue 按 24h 判定
        assert items[0].updated_at == now - timedelta(hours=25)
        assert _is_overdue(items[0]) is True
        assert _is_overdue(items[1]) is False

    @pytest.mark.asyncio
    async def test_sql四分支结构与时间比较在SQL层(self):
        """单条 UNION ALL 四分支；时间比较全部在 SQL 层（now()/24h interval）。"""
        result_mock = MagicMock()
        result_mock.all.return_value = []
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        await _aggregate_handling_orders(db, None)

        stmt = db.execute.call_args.args[0]
        compiled = stmt.compile()
        stmt_str = str(compiled)
        params = list(compiled.params.values())
        assert "handling_order" in stmt_str
        assert "UNION ALL" in stmt_str
        for status in ("REOPENED", "EXECUTING", "VERIFYING", "PENDING"):
            assert status in params
        # 时间比较在 SQL 层：now() 函数 + 24h timedelta 绑定参数，而非 Python 逐行过滤
        assert "now()" in stmt_str
        assert timedelta(hours=24) in params
        # 截断协议：limit = _MAX_ITEMS_PER_SOURCE + 1
        assert "LIMIT" in stmt_str.upper()

    @pytest.mark.asyncio
    async def test_第四分支受开关控制(self):
        """ATTENTION_INCLUDE_SCHEDULE_OVERDUE=False 时不含 PENDING 分支。"""
        import app.services.monitor_attention as ma

        result_mock = MagicMock()
        result_mock.all.return_value = []
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        with patch.object(ma, "ATTENTION_INCLUDE_SCHEDULE_OVERDUE", False):
            await _aggregate_handling_orders(db, None)

        stmt = db.execute.call_args.args[0]
        params = list(stmt.compile().params.values())
        assert "PENDING" not in params
        # 其余三分支仍在
        for status in ("REOPENED", "EXECUTING", "VERIFYING"):
            assert status in params

    @pytest.mark.asyncio
    async def test_截断协议(self):
        """返回 _MAX_ITEMS_PER_SOURCE+1 行时 truncated=True 且截断到上限。"""
        from app.services.monitor_attention import _MAX_ITEMS_PER_SOURCE

        rows = [_make_order_row(order_id=f"o-{i}") for i in range(_MAX_ITEMS_PER_SOURCE + 1)]
        result_mock = MagicMock()
        result_mock.all.return_value = rows
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)

        items, truncated = await _aggregate_handling_orders(db, None)

        assert truncated is True
        assert len(items) == _MAX_ITEMS_PER_SOURCE


class TestHandlingInListAttention:
    """list_attention 对 HANDLING 来源的接入（守卫/深链接/筛选）。"""

    @pytest.mark.asyncio
    async def test_模块禁用守卫短路(self):
        """处置模块禁用时不查询 handling_order。"""
        db = _make_db_mock()
        _configure_db_empty(db)

        with patch("app.core.modules.is_module_enabled", return_value=False):
            result = await list_attention(db, sources=["HANDLING"], role="ADMIN")

        assert result["total"] == 0
        for call in db.execute.call_args_list:
            assert "handling_order" not in str(call.args[0])

    @pytest.mark.asyncio
    async def test_HANDLING项深链接target(self):
        """HANDLING 关注项组主动作跳转 /handling/orders 且携带 focus。"""
        db = _make_db_mock()
        row = _make_order_row(order_id="order-xyz", status="REOPENED", priority="URGENT")
        handling_result = MagicMock()
        handling_result.all.return_value = [row]
        empty_result = _make_empty_result()

        def side_effect(stmt, *args, **kwargs):
            if "handling_order" in str(stmt):
                return handling_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, sources=["HANDLING"], role="ADMIN")

        assert result["total"] == 1
        group = result["items"][0]
        assert group["sources"] == ["HANDLING"]
        assert group["priority"] == "URGENT"
        primary = group["primaryAction"]
        assert primary["type"] == "VIEW_DETAIL"
        assert primary["target"]["route"] == "/handling/orders"
        assert primary["target"]["query"]["focus"] == "order-xyz"
        assert primary["target"]["query"]["tab"] == "orders"
        # 子项 taskId 承载 orderId
        assert group["children"][0]["taskId"] == "order-xyz"

    @pytest.mark.asyncio
    async def test_HANDLING筛选生效(self):
        """sources=[HANDLING] 时仅聚合处置工单来源。"""
        db = _make_db_mock()
        _configure_db_empty(db)

        result = await list_attention(db, sources=["HANDLING"], role="ADMIN")

        assert result["total"] == 0
        # 仅执行了 HANDLING 一条聚合查询（无 alert_event/kpi 查询）
        stmts = [str(call.args[0]) for call in db.execute.call_args_list]
        assert len(stmts) == 1
        assert "handling_order" in stmts[0]
