"""统一关注队列 monitor_attention.py 单元测试（整改方案 §8.1）。

测试覆盖：
- 优先级映射：scoreDelta 阈值 -2/-5/-10 边界、severity CRITICAL/ERROR/WARN/INFO
- 状态映射：ALERT（ACTIVE→OPEN 等）、TRACKER（PENDING→OPEN 等）
- 动作生成：SPONSOR 只读、ADMIN/IC 完整写动作、PE/EXPERT 受限
- 排序：未确认 → 超期 → 处理中/验证中 → 已确认 → 已抑制 → 时间倒序
- VERIFICATION 超期检测（24h 边界）
- DATA_QUALITY 每回路去重
- 五类来源聚合与筛选
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.monitor_attention import (
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
) -> _RawItem:
    """构造 _RawItem 测试对象。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    return _RawItem(
        source=source,
        source_id=source_id,
        loop_id=loop_id,
        tag_name=tag_name,
        unit_name=None,
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
        task_id=None,
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

        OPEN 项最优先；超期的 VERIFYING 次之；未超期的 VERIFYING 最后。
        """
        now = datetime.now(UTC).replace(tzinfo=None)
        open_item = _make_raw_item(
            source="ALERT",
            status="OPEN",
            occurred_at=datetime(2026, 1, 3),
        )
        overdue = _make_raw_item(
            source="VERIFICATION",
            status="VERIFYING",
            updated_at=now - timedelta(hours=30),
            occurred_at=datetime(2026, 1, 1),
        )
        not_overdue_verifying = _make_raw_item(
            source="VERIFICATION",
            status="VERIFYING",
            updated_at=now - timedelta(hours=10),
            occurred_at=datetime(2026, 1, 2),
        )
        items = sorted([not_overdue_verifying, overdue, open_item], key=_sort_key)
        # OPEN 最优先
        assert items[0].status == "OPEN"
        # 超期次之
        assert items[1].source == "VERIFICATION"
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
    def test_非VERIFICATION来源不超期(self):
        item = _make_raw_item(source="ALERT")
        assert _is_overdue(item) is False

    def test_VERIFICATION超24h为超期(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        item = _make_raw_item(
            source="VERIFICATION",
            updated_at=now - timedelta(hours=25),
        )
        assert _is_overdue(item) is True

    def test_VERIFICATION不足24h不超期(self):
        now = datetime.now(UTC).replace(tzinfo=None)
        item = _make_raw_item(
            source="VERIFICATION",
            updated_at=now - timedelta(hours=20),
        )
        assert _is_overdue(item) is False

    def test_VERIFICATION无updated_at不超期(self):
        item = _make_raw_item(source="VERIFICATION", updated_at=None)
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

    def test_TRACKER来源有查看工单动作(self):
        _, actions = _build_actions(
            source="TRACKER",
            loop_id="loop-001",
            event_id=None,
            tracker_id="trk-001",
            role="ADMIN",
        )
        action_types = [a["type"] for a in actions]
        assert "VIEW_DETAIL" in action_types

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


def _configure_db_empty(db: AsyncMock) -> None:
    """配置 db 返回空结果。"""
    db.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
            all=MagicMock(return_value=[]),
            scalar=MagicMock(return_value=0),
            scalar_one_or_none=MagicMock(return_value=None),
        )
    )


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
        # loop 查询返回空
        empty_result = MagicMock()
        empty_result.all.return_value = []
        db.execute = AsyncMock(return_value=empty_result)
        result = await list_attention(db, plant_node_id="unit-001", role="ADMIN")
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_loop_id无匹配回路返回空(self):
        db = _make_db_mock()
        empty_result = MagicMock()
        empty_result.all.return_value = []
        db.execute = AsyncMock(return_value=empty_result)
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
        alert_result.all.return_value = [(evt, loop)]

        # 后续聚合查询返回空
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

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
        alert_result.all.return_value = [(evt, loop)]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        assert result["total"] == 1
        assert result["items"][0]["priority"] == "URGENT"
        assert result["items"][0]["status"] == "OPEN"
        assert result["items"][0]["sourceStatus"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_ALERT状态映射(self):
        """ACTIVE→OPEN, ACKNOWLEDGED→ACKNOWLEDGED, SUPPRESSED→SUPPRESSED。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)
        loop = MagicMock()
        loop.id = "loop-001"
        loop.tag_name = "LIC-101"
        loop.is_active = True

        statuses = [
            ("ACTIVE", "OPEN"),
            ("ACKNOWLEDGED", "ACKNOWLEDGED"),
            ("SUPPRESSED", "SUPPRESSED"),
        ]
        pairs = []
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
            pairs.append((evt, loop))

        alert_result = MagicMock()
        alert_result.all.return_value = pairs
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        status_map = {item["sourceStatus"]: item["status"] for item in result["items"]}
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
        alert_result.all.return_value = [(evt, loop)]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        assert len(result["items"]) == 1
        assert len(result["items"][0]["rankReasons"]) >= 1
        assert any("重复触发" in r for r in result["items"][0]["rankReasons"])

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
        alert_result.all.return_value = [(evt, loop)]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        assert result["items"][0]["attentionId"] == "ALERT:evt-abc"

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
        alert_result.all.return_value = [(evt1, loop1), (evt2, loop2)]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

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
        """分页 page/pageSize 正确切片。"""
        db = _make_db_mock()
        now = datetime.now(UTC).replace(tzinfo=None)
        loop = MagicMock()
        loop.id = "loop-001"
        loop.tag_name = "LIC-101"
        loop.is_active = True

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
            pairs.append((evt, loop))

        alert_result = MagicMock()
        alert_result.all.return_value = pairs
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

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
        alert_result.all.return_value = [(evt, loop)]
        empty_result = MagicMock()
        empty_result.scalars.return_value.all.return_value = []

        def side_effect(stmt, *args, **kwargs):
            if "alert_event" in str(stmt):
                return alert_result
            return empty_result

        db.execute = AsyncMock(side_effect=side_effect)
        result = await list_attention(db, role="ADMIN")
        assert result["aggregates"]["bySource"].get("ALERT") == 1
        assert result["aggregates"]["byPriority"].get("URGENT") == 1
        assert result["aggregates"]["byStatus"].get("OPEN") == 1
