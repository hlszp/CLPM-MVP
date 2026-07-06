"""Loop batch operations & monitor trigger tests (配置增强).

测试覆盖：
- TEST-01: batch_update_loops — 批量更新监控状态
- TEST-02: batch_update_loops — 批量更新级别
- TEST-03: batch_delete_loops — 批量软删除
- TEST-04: batch_update_loops — 空列表抛异常
- TEST-05: check_node_monitor_trigger — 无位号配置返回 True
- TEST-06: check_node_monitor_trigger — 位号值匹配返回 True
- TEST-07: check_node_monitor_trigger — 位号值不匹配返回 False
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from app.services.loop_batch import (
    batch_delete_loops,
    batch_update_loops,
    check_node_monitor_trigger,
)

# ===========================================================================
# 辅助函数：构造 mock 对象
# ===========================================================================


def _make_scalars_mock(items: list) -> MagicMock:
    """构造 execute 返回值，支持 scalars().all()。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value: object) -> MagicMock:
    """构造 execute 返回值，支持 scalar_one_or_none()。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_loop(
    loop_id: str = "loop-001",
    tag_name: str = "TAG-001",
    is_active: bool = True,
    level: int | None = 3,
    status: str = "PARTIAL",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.is_active = is_active
    loop.importance_level = level
    loop.status = status
    loop.updated_by = None
    return loop


def _make_plant_node(
    node_id: str = "node-001",
    monitor_tag_id: str | None = None,
    monitor_trigger_value: str | None = None,
) -> MagicMock:
    """构造 PlantNode mock。"""
    node = MagicMock()
    node.id = node_id
    node.name = "测试装置"
    node.monitor_tag_id = monitor_tag_id
    node.monitor_trigger_value = monitor_trigger_value
    return node


def _make_tag(
    tag_id: str = "tag-001",
    tag_name: str = "MONITOR_TAG_001",
) -> MagicMock:
    """构造 TagRegistry mock。"""
    tag = MagicMock()
    tag.id = tag_id
    tag.tag_name = tag_name
    return tag


# ===========================================================================
# TEST-01: 批量更新监控状态
# ===========================================================================


class TestBatchUpdateLoopsMonitored:
    """批量更新监控状态测试。"""

    @pytest.mark.asyncio
    async def test_batch_update_loops_monitored(self) -> None:
        """批量更新 is_monitored=True，应将所有回路 is_active 置为 True。"""
        loop1 = _make_loop("loop-001", is_active=False)
        loop2 = _make_loop("loop-002", is_active=False)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([loop1, loop2]))
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_update_loops(
            db=db,
            loop_ids=["loop-001", "loop-002"],
            updates={"is_monitored": True},
            operator="admin",
        )

        assert result == 2
        # 验证 is_active 被置为 True
        assert loop1.is_active is True
        assert loop2.is_active is True
        # 验证审计日志写入
        db.add.assert_called_once()
        db.commit.assert_called_once()


# ===========================================================================
# TEST-02: 批量更新级别
# ===========================================================================


class TestBatchUpdateLoopsLevel:
    """批量更新级别测试。"""

    @pytest.mark.asyncio
    async def test_batch_update_loops_level(self) -> None:
        """批量更新 importance_level=1，应将所有回路 importance_level 置为 1。"""
        loop1 = _make_loop("loop-001", level=3)
        loop2 = _make_loop("loop-002", level=2)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([loop1, loop2]))
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_update_loops(
            db=db,
            loop_ids=["loop-001", "loop-002"],
            updates={"importance_level": 1},
            operator="admin",
        )

        assert result == 2
        assert loop1.importance_level == 1
        assert loop2.importance_level == 1
        db.add.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_update_loops_invalid_level(self) -> None:
        """importance_level=4 应抛 ERR_BATCH_INVALID_FIELD。"""
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await batch_update_loops(
                db=db,
                loop_ids=["loop-001"],
                updates={"importance_level": 4},
                operator="admin",
            )
        assert exc_info.value.code == "ERR_BATCH_INVALID_FIELD"

    @pytest.mark.asyncio
    async def test_batch_update_loops_invalid_field(self) -> None:
        """非法字段应抛 ERR_BATCH_INVALID_FIELD。"""
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await batch_update_loops(
                db=db,
                loop_ids=["loop-001"],
                updates={"tag_name": "X"},  # type: ignore[dict-item]
                operator="admin",
            )
        assert exc_info.value.code == "ERR_BATCH_INVALID_FIELD"


# ===========================================================================
# TEST-03: 批量软删除
# ===========================================================================


class TestBatchDeleteLoops:
    """批量软删除测试。"""

    @pytest.mark.asyncio
    async def test_batch_delete_loops(self) -> None:
        """批量软删除应将 is_active=False, status=INACTIVE。"""
        loop1 = _make_loop("loop-001", is_active=True, status="READY")
        loop2 = _make_loop("loop-002", is_active=True, status="PARTIAL")

        db = AsyncMock()
        # 1st execute: 查询回路列表；2nd execute: 查询有 Tag 的回路（返回空 → 无 Tag）
        db.execute = AsyncMock(
            side_effect=[_make_scalars_mock([loop1, loop2]), _make_scalars_mock([])]
        )
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_delete_loops(
            db=db,
            loop_ids=["loop-001", "loop-002"],
            operator="admin",
        )

        assert result["deleted"] == 2
        assert result["skipped"] == []
        assert loop1.is_active is False
        assert loop1.status == "INACTIVE"
        assert loop2.is_active is False
        assert loop2.status == "INACTIVE"
        db.add.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_delete_loops_not_found(self) -> None:
        """无匹配回路时返回 deleted=0。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_delete_loops(
            db=db,
            loop_ids=["loop-999"],
            operator="admin",
        )

        assert result["deleted"] == 0
        assert result["skipped"] == []
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_loops_skip_with_tags(self) -> None:
        """P1 #9: 有关联 Tag 的回路应跳过并记入 skipped 列表。"""
        loop1 = _make_loop("loop-001", is_active=True, status="READY")
        loop2 = _make_loop("loop-002", is_active=True, status="PARTIAL")

        db = AsyncMock()
        # 1st execute: 查询回路列表（scalars().all()）
        # 2nd execute: 查询有 Tag 的回路（.all() 返回 [(loop_id,)]）
        rows_result = MagicMock()
        rows_result.all.return_value = [("loop-001",)]
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop1, loop2]),
                rows_result,
            ]
        )
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_delete_loops(
            db=db,
            loop_ids=["loop-001", "loop-002"],
            operator="admin",
        )

        assert result["deleted"] == 1
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["loopId"] == "loop-001"
        assert "Tag" in result["skipped"][0]["reason"]
        # loop-001 未被修改（跳过）
        assert loop1.is_active is True
        # loop-002 被软删
        assert loop2.is_active is False
        assert loop2.status == "INACTIVE"


# ===========================================================================
# TEST-04: Schema 互斥校验（P1 #10）
# ===========================================================================


class TestLoopBatchUpdatesMutex:
    """P1 #10: isMonitored 与 isStatEnabled 不能同时更新。"""

    def test_both_monitor_and_stat_rejected(self) -> None:
        """同时传 isMonitored 和 isStatEnabled 应被 Schema 拒绝。"""
        from pydantic import ValidationError

        from app.schemas.loop_batch import LoopBatchUpdates

        with pytest.raises(ValidationError) as exc_info:
            LoopBatchUpdates(is_monitored=True, is_stat_enabled=False)
        assert "不能同时更新" in str(exc_info.value)

    def test_only_monitored_accepted(self) -> None:
        """仅传 isMonitored 应通过。"""
        from app.schemas.loop_batch import LoopBatchUpdates

        updates = LoopBatchUpdates(is_monitored=True)
        assert updates.is_monitored is True
        assert updates.is_stat_enabled is None

    def test_only_stat_accepted(self) -> None:
        """仅传 isStatEnabled 应通过。"""
        from app.schemas.loop_batch import LoopBatchUpdates

        updates = LoopBatchUpdates(is_stat_enabled=False)
        assert updates.is_stat_enabled is False
        assert updates.is_monitored is None


# ===========================================================================
# TEST-05: 空列表抛异常
# ===========================================================================


class TestBatchUpdateEmptyList:
    """空列表异常测试。"""

    @pytest.mark.asyncio
    async def test_batch_update_empty_list(self) -> None:
        """空 loop_ids 应抛 ERR_BATCH_EMPTY。"""
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await batch_update_loops(
                db=db,
                loop_ids=[],
                updates={"importance_level": 1},
                operator="admin",
            )
        assert exc_info.value.code == "ERR_BATCH_EMPTY"

    @pytest.mark.asyncio
    async def test_batch_delete_empty_list(self) -> None:
        """空 loop_ids 应抛 ERR_BATCH_EMPTY。"""
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await batch_delete_loops(
                db=db,
                loop_ids=[],
                operator="admin",
            )
        assert exc_info.value.code == "ERR_BATCH_EMPTY"


# ===========================================================================
# TEST-05: 无位号配置返回 True
# ===========================================================================


class TestCheckNodeMonitorTriggerNoTag:
    """无位号配置测试。"""

    @pytest.mark.asyncio
    async def test_check_node_monitor_trigger_no_tag(self) -> None:
        """plant_node 无 monitor_tag_id 时返回 True（默认监控）。"""
        node = _make_plant_node("node-001", monitor_tag_id=None)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(node))

        result = await check_node_monitor_trigger(db, "node-001")

        assert result is True


# ===========================================================================
# TEST-06: 位号值匹配返回 True
# ===========================================================================


class TestCheckNodeMonitorTriggerMatch:
    """位号值匹配测试。"""

    @pytest.mark.asyncio
    async def test_check_node_monitor_trigger_match(self) -> None:
        """位号最新值等于 trigger_value 时返回 True。"""
        node = _make_plant_node(
            "node-001",
            monitor_tag_id="tag-001",
            monitor_trigger_value="ON",
        )
        tag = _make_tag("tag-001", "MONITOR_TAG_001")

        db = AsyncMock()
        # 1st execute: 查 plant_node；2nd execute: 查 tag_registry
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(node),
                _make_scalar_one_or_none_mock(tag),
            ]
        )

        async def _mock_query_trend(tag_name: str, start_time: str, end_time: str):
            return [{"ts": "2026-06-24T08:00:00Z", "value": "ON", "quality": "GOOD"}]

        with patch(
            "app.services.loop_batch.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await check_node_monitor_trigger(db, "node-001")

        assert result is True

    @pytest.mark.asyncio
    async def test_check_node_monitor_trigger_match_numeric(self) -> None:
        """数值型触发值匹配（trigger_value="1", latest_value=1）。"""
        node = _make_plant_node(
            "node-001",
            monitor_tag_id="tag-001",
            monitor_trigger_value="1",
        )
        tag = _make_tag("tag-001", "MONITOR_TAG_001")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(node),
                _make_scalar_one_or_none_mock(tag),
            ]
        )

        async def _mock_query_trend(tag_name: str, start_time: str, end_time: str):
            return [{"ts": "2026-06-24T08:00:00Z", "value": 1, "quality": "GOOD"}]

        with patch(
            "app.services.loop_batch.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await check_node_monitor_trigger(db, "node-001")

        assert result is True


# ===========================================================================
# TEST-07: 位号值不匹配返回 False
# ===========================================================================


class TestCheckNodeMonitorTriggerMismatch:
    """位号值不匹配测试。"""

    @pytest.mark.asyncio
    async def test_check_node_monitor_trigger_mismatch(self) -> None:
        """位号最新值不等于 trigger_value 时返回 False。"""
        node = _make_plant_node(
            "node-001",
            monitor_tag_id="tag-001",
            monitor_trigger_value="ON",
        )
        tag = _make_tag("tag-001", "MONITOR_TAG_001")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(node),
                _make_scalar_one_or_none_mock(tag),
            ]
        )

        async def _mock_query_trend(tag_name: str, start_time: str, end_time: str):
            return [{"ts": "2026-06-24T08:00:00Z", "value": "OFF", "quality": "GOOD"}]

        with patch(
            "app.services.loop_batch.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await check_node_monitor_trigger(db, "node-001")

        assert result is False

    @pytest.mark.asyncio
    async def test_check_node_monitor_trigger_no_data(self) -> None:
        """TDengine 无数据时返回 False。"""
        node = _make_plant_node(
            "node-001",
            monitor_tag_id="tag-001",
            monitor_trigger_value="ON",
        )
        tag = _make_tag("tag-001", "MONITOR_TAG_001")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(node),
                _make_scalar_one_or_none_mock(tag),
            ]
        )

        async def _mock_query_trend(tag_name: str, start_time: str, end_time: str):
            return []

        with patch(
            "app.services.loop_batch.query_trend_data",
            new=AsyncMock(side_effect=_mock_query_trend),
        ):
            result = await check_node_monitor_trigger(db, "node-001")

        assert result is False

    @pytest.mark.asyncio
    async def test_check_node_monitor_trigger_node_not_found(self) -> None:
        """节点不存在时抛 ERR_NODE_NOT_FOUND。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        with pytest.raises(BizError) as exc_info:
            await check_node_monitor_trigger(db, "node-999")
        assert exc_info.value.code == "ERR_NODE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_check_node_monitor_trigger_tag_deleted(self) -> None:
        """monitor_tag_id 配置但 tag 已删除时回退默认监控（True）。"""
        node = _make_plant_node(
            "node-001",
            monitor_tag_id="tag-001",
            monitor_trigger_value="ON",
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(node),
                _make_scalar_one_or_none_mock(None),  # tag 已删除
            ]
        )

        result = await check_node_monitor_trigger(db, "node-001")

        assert result is True
