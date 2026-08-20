"""Loop batch operations tests (配置增强).

测试覆盖：
- TEST-01: batch_update_loops — 批量更新监控状态
- TEST-02: batch_update_loops — 批量更新级别
- TEST-03: batch_delete_loops — 批量硬删除（解绑映射 + 级联删除）
- TEST-04: batch_update_loops — 空列表抛异常
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import BizError
from app.services.loop_batch import (
    batch_delete_loops,
    batch_update_loops,
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


def _make_scalar_mock(value: object) -> MagicMock:
    """构造 execute 返回值，支持 scalar()（如 COUNT 聚合）。"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _make_mapping(
    loop_id: str = "loop-001",
    tag_id: str = "tag-001",
) -> MagicMock:
    """构造 LoopTagMapping mock。"""
    mapping = MagicMock()
    mapping.loop_id = loop_id
    mapping.tag_id = tag_id
    return mapping


def _make_loop(
    loop_id: str = "loop-001",
    tag_name: str = "TAG-001",
    is_active: bool = True,
    importance_level: int | None = 3,
    include_in_evaluation: bool = True,
    status: str = "PARTIAL",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.is_active = is_active
    loop.importance_level = importance_level
    loop.include_in_evaluation = include_in_evaluation
    loop.status = status
    loop.updated_by = None
    return loop


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
        # 验证审计日志写入（每回路一条）
        assert db.add.call_count == 2
        db.commit.assert_called_once()


# ===========================================================================
# TEST-02: 批量更新级别
# ===========================================================================


class TestBatchUpdateLoopsLevel:
    """批量更新重要等级测试。"""

    @pytest.mark.asyncio
    async def test_batch_update_loops_level(self) -> None:
        """批量更新 importance_level=1，应将所有回路 importance_level 置为 1。"""
        loop1 = _make_loop("loop-001", importance_level=3)
        loop2 = _make_loop("loop-002", importance_level=2)

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
        assert db.add.call_count == 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_update_loops_include_in_evaluation(self) -> None:
        """批量更新 include_in_evaluation=False。"""
        loop1 = _make_loop("loop-001", include_in_evaluation=True)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([loop1]))
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_update_loops(
            db=db,
            loop_ids=["loop-001"],
            updates={"include_in_evaluation": False},
            operator="admin",
        )

        assert result == 1
        assert loop1.include_in_evaluation is False
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
# TEST-03: 批量硬删除
# ===========================================================================


class TestBatchDeleteLoops:
    """批量硬删除测试（解绑 Tag 映射 + 级联删除，不可恢复）。"""

    @pytest.mark.asyncio
    async def test_batch_delete_loops(self) -> None:
        """批量硬删除：每回路删除映射、db.delete 本体、写审计、单事务提交。"""
        loop1 = _make_loop("loop-001", is_active=True, status="READY")
        loop2 = _make_loop("loop-002", is_active=True, status="PARTIAL")
        mapping1 = _make_mapping("loop-001", "tag-101")
        mapping2 = _make_mapping("loop-002", "tag-201")
        tag1 = _make_tag("tag-101")
        tag2 = _make_tag("tag-201")

        db = AsyncMock()
        # 调用序列：
        # 1. select(LoopLedger) → [loop1, loop2]
        # 2. loop1: select(LoopTagMapping) → [mapping1]
        # 3. loop1: delete(LoopTagMapping)（返回值未用）
        # 4. loop1/tag-101: COUNT 引用 → 0（无其他回路引用）
        # 5. loop1/tag-101: select(TagRegistry) → tag1（清除 is_linked）
        # 6-9. loop2 同上
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop1, loop2]),
                _make_scalars_mock([mapping1]),
                MagicMock(),
                _make_scalar_mock(0),
                _make_scalar_one_or_none_mock(tag1),
                _make_scalars_mock([mapping2]),
                MagicMock(),
                _make_scalar_mock(0),
                _make_scalar_one_or_none_mock(tag2),
            ]
        )
        db.delete = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_delete_loops(
            db=db,
            loop_ids=["loop-001", "loop-002"],
            operator="admin",
        )

        assert result == {"deleted": 2, "skipped": []}
        # 硬删除：回路本体通过 db.delete 删除（而非改 is_active）
        assert db.delete.await_count == 2
        db.delete.assert_any_await(loop1)
        db.delete.assert_any_await(loop2)
        assert loop1.is_active is True  # 不再翻转 is_active
        # 解绑后未被引用的 Tag 清除 is_linked
        assert tag1.is_linked is False
        assert tag2.is_linked is False
        # 每回路一条审计
        assert db.add.call_count == 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_delete_loops_shared_tag_keeps_linked(self) -> None:
        """Tag 仍被其他回路引用时不清除 is_linked（is_linked 由映射派生）。"""
        loop1 = _make_loop("loop-001", is_active=True, status="READY")
        mapping1 = _make_mapping("loop-001", "tag-shared")
        tag = _make_tag("tag-shared")
        tag.is_linked = True

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop1]),
                _make_scalars_mock([mapping1]),
                MagicMock(),
                _make_scalar_mock(3),  # 仍被 3 个其他回路引用
            ]
        )
        db.delete = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_delete_loops(
            db=db,
            loop_ids=["loop-001"],
            operator="admin",
        )

        assert result == {"deleted": 1, "skipped": []}
        assert tag.is_linked is True  # 仍被引用，保持关联状态

    @pytest.mark.asyncio
    async def test_batch_delete_loops_not_found(self) -> None:
        """无匹配回路时 deleted=0，全部进入 skipped。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        db.delete = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_delete_loops(
            db=db,
            loop_ids=["loop-999"],
            operator="admin",
        )

        assert result["deleted"] == 0
        assert result["skipped"] == [{"loopId": "loop-999", "reason": "回路不存在"}]
        db.delete.assert_not_awaited()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_batch_delete_loops_partial_found(self) -> None:
        """部分回路不存在时，不存在的进入 skipped。"""
        loop1 = _make_loop("loop-001", is_active=True, status="READY")

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop1]),
                _make_scalars_mock([]),  # loop1 无 Tag 映射
            ]
        )
        db.delete = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        result = await batch_delete_loops(
            db=db,
            loop_ids=["loop-001", "loop-999"],
            operator="admin",
        )

        assert result["deleted"] == 1
        assert result["skipped"] == [{"loopId": "loop-999", "reason": "回路不存在"}]
        db.delete.assert_awaited_once_with(loop1)


# ===========================================================================
# TEST-04: 空列表抛异常
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
# SVC-10 位号触发监控（check_node_monitor_trigger）测试已随死代码一并移除
# （2026-08-20：功能从未接线，plant_node.monitor_tag_id/monitor_trigger_value
#  字段已由迁移 e1f2a3b4c5d6 删除）。
# ===========================================================================
