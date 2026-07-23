"""Node-level daily/monthly aggregation tests (GB/T 44693.2-2024 §6.4).

覆盖：
- aggregate_daily_snapshot: 日聚合正确性（按 loop_count 加权平均）
- aggregate_daily_snapshot: 幂等性（相同 plant_node_id + stat_date 不重复写入）
- aggregate_monthly_snapshot: 月聚合正确性
- aggregate_all_nodes_daily: 批量日聚合（mock 2 个节点）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.node_aggregation import (
    aggregate_all_nodes_daily,
    aggregate_daily_snapshot,
    aggregate_monthly_snapshot,
)

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_hourly_snapshot(
    plant_node_id: str = "node-001",
    ts_start: datetime | None = None,
    score: Decimal = Decimal("80.00"),
    good_value_rate: Decimal = Decimal("95.00"),
    auto_mode_rate: Decimal = Decimal("88.00"),
    effective_auto_rate: Decimal = Decimal("85.00"),
    steady_rate: Decimal = Decimal("80.00"),
    accuracy_rate: Decimal = Decimal("78.00"),
    fast_rate: Decimal = Decimal("82.00"),
    oscillation_rate: Decimal = Decimal("15.00"),
    saturation_rate: Decimal = Decimal("8.00"),
    instrument_fault_rate: Decimal = Decimal("5.00"),
    auto_loop_ratio: Decimal = Decimal("66.67"),
    realtime_auto_rate: Decimal | None = Decimal("70.00"),
    loop_count: int = 10,
    status: str = "GOOD",
    algorithm_version: str = "KPI_CALC_v2.0",
) -> MagicMock:
    """构造小时快照 mock。"""
    snap = MagicMock()
    snap.plant_node_id = plant_node_id
    snap.ts_start = ts_start or datetime(2026, 6, 24, 8, 0, 0)
    snap.ts_end = snap.ts_start + timedelta(hours=1)
    snap.score = score
    snap.good_value_rate = good_value_rate
    snap.auto_mode_rate = auto_mode_rate
    snap.effective_auto_rate = effective_auto_rate
    snap.steady_rate = steady_rate
    snap.accuracy_rate = accuracy_rate
    snap.fast_rate = fast_rate
    snap.oscillation_rate = oscillation_rate
    snap.saturation_rate = saturation_rate
    snap.instrument_fault_rate = instrument_fault_rate
    snap.auto_loop_ratio = auto_loop_ratio
    snap.realtime_auto_rate = realtime_auto_rate
    snap.loop_count = loop_count
    snap.status = status
    snap.algorithm_version = algorithm_version
    return snap


def _make_daily_snapshot(
    plant_node_id: str = "node-001",
    stat_date: date | None = None,
    score: Decimal = Decimal("78.00"),
    good_value_rate: Decimal = Decimal("94.00"),
    auto_mode_rate: Decimal = Decimal("87.00"),
    effective_auto_rate: Decimal = Decimal("84.00"),
    steady_rate: Decimal = Decimal("79.00"),
    accuracy_rate: Decimal = Decimal("77.00"),
    fast_rate: Decimal = Decimal("81.00"),
    oscillation_rate: Decimal = Decimal("14.00"),
    saturation_rate: Decimal = Decimal("7.00"),
    instrument_fault_rate: Decimal = Decimal("4.00"),
    auto_loop_ratio: Decimal = Decimal("65.00"),
    realtime_auto_rate: Decimal | None = Decimal("68.00"),
    loop_count: int = 10,
    status: str = "FAIR",
    algorithm_version: str = "KPI_CALC_v2.0",
) -> MagicMock:
    """构造日快照 mock。"""
    snap = MagicMock()
    snap.plant_node_id = plant_node_id
    snap.stat_date = stat_date or date(2026, 6, 24)
    snap.score = score
    snap.good_value_rate = good_value_rate
    snap.auto_mode_rate = auto_mode_rate
    snap.effective_auto_rate = effective_auto_rate
    snap.steady_rate = steady_rate
    snap.accuracy_rate = accuracy_rate
    snap.fast_rate = fast_rate
    snap.oscillation_rate = oscillation_rate
    snap.saturation_rate = saturation_rate
    snap.instrument_fault_rate = instrument_fault_rate
    snap.auto_loop_ratio = auto_loop_ratio
    snap.realtime_auto_rate = realtime_auto_rate
    snap.loop_count = loop_count
    snap.status = status
    snap.algorithm_version = algorithm_version
    return snap


def _make_scalars_result(items: list) -> MagicMock:
    """构造 execute 返回值，支持 scalars().all()。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_result(value: object) -> MagicMock:
    """构造 execute 返回值，支持 scalar_one_or_none()。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# aggregate_daily_snapshot 测试
# ---------------------------------------------------------------------------


class TestAggregateDailySnapshot:
    """日聚合测试。"""

    @pytest.mark.asyncio
    async def test_aggregate_daily_snapshot_success(self):
        """日聚合正确性：3 条小时快照按 loop_count 加权平均。"""
        # 构造 3 条小时快照，loop_count 不同
        snap1 = _make_hourly_snapshot(
            ts_start=datetime(2026, 6, 24, 8, 0, 0),
            score=Decimal("80.00"),
            loop_count=10,
            realtime_auto_rate=Decimal("60.00"),
        )
        snap2 = _make_hourly_snapshot(
            ts_start=datetime(2026, 6, 24, 9, 0, 0),
            score=Decimal("90.00"),
            loop_count=20,
            realtime_auto_rate=Decimal("70.00"),
        )
        snap3 = _make_hourly_snapshot(
            ts_start=datetime(2026, 6, 24, 10, 0, 0),
            score=Decimal("70.00"),
            loop_count=30,
            realtime_auto_rate=Decimal("80.00"),
        )
        hourly_snaps = [snap1, snap2, snap3]

        db = AsyncMock()
        # 第一次 execute: 查询小时快照
        # 第二次 execute: 查询已存在的日快照（None = 新增）
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result(hourly_snaps),
                _make_scalar_one_or_none_result(None),
            ]
        )
        db.flush = AsyncMock()
        db.add = MagicMock()

        result = await aggregate_daily_snapshot(db, "node-001", date(2026, 6, 24))

        assert result is not None
        assert result["plant_node_id"] == "node-001"
        assert result["stat_date"] == date(2026, 6, 24)

        # 加权平均 score = (80*10 + 90*20 + 70*30) / (10+20+30) = 4700/60 = 78.33
        assert result["score"] == Decimal("78.33")
        # loop_count 取最大值
        assert result["loop_count"] == 30
        # realtime_auto_rate 取最后一条小时快照的值（snap3, ts_start=10:00）
        assert result["realtime_auto_rate"] == Decimal("80.00")
        # status 由 score 78.33 定级 → FAIR (70 <= 78.33 < 80)
        assert result["status"] == "FAIR"
        # algorithm_version 取最后一条
        assert result["algorithm_version"] == "KPI_CALC_v2.0"
        # 新增时调用 db.add
        assert db.add.call_count == 1

    @pytest.mark.asyncio
    async def test_aggregate_daily_snapshot_idempotent(self):
        """幂等性：相同 plant_node_id + stat_date 不重复写入（覆盖更新）。"""
        snap1 = _make_hourly_snapshot(
            ts_start=datetime(2026, 6, 24, 8, 0, 0),
            score=Decimal("80.00"),
            loop_count=10,
        )
        snap2 = _make_hourly_snapshot(
            ts_start=datetime(2026, 6, 24, 9, 0, 0),
            score=Decimal("90.00"),
            loop_count=20,
        )
        hourly_snaps = [snap1, snap2]

        existing_daily = MagicMock()  # 已存在的日快照

        db = AsyncMock()
        # 第一次调用：查询小时快照 → 2 条；查询已存在日快照 → None（新增）
        # 第二次调用：查询小时快照 → 2 条；查询已存在日快照 → existing（覆盖更新）
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result(hourly_snaps),
                _make_scalar_one_or_none_result(None),
                _make_scalars_result(hourly_snaps),
                _make_scalar_one_or_none_result(existing_daily),
            ]
        )
        db.flush = AsyncMock()
        db.add = MagicMock()

        # 第一次调用：新增
        result1 = await aggregate_daily_snapshot(db, "node-001", date(2026, 6, 24))
        assert result1 is not None
        assert db.add.call_count == 1  # 新增一次

        # 第二次调用：覆盖更新，不新增
        result2 = await aggregate_daily_snapshot(db, "node-001", date(2026, 6, 24))
        assert result2 is not None
        assert db.add.call_count == 1  # 仍然只有一次（第二次是覆盖更新）
        # 验证已存在对象的字段被更新
        assert existing_daily.score == result2["score"]
        assert existing_daily.status == result2["status"]

    @pytest.mark.asyncio
    async def test_aggregate_daily_snapshot_no_data(self):
        """无小时快照时返回 None。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await aggregate_daily_snapshot(db, "node-empty", date(2026, 6, 24))

        assert result is None

    @pytest.mark.asyncio
    async def test_aggregate_daily_snapshot_realtime_auto_rate_none(self):
        """最后一条小时快照 realtime_auto_rate 为 None 时，日快照也为 None。"""
        snap1 = _make_hourly_snapshot(
            ts_start=datetime(2026, 6, 24, 8, 0, 0),
            realtime_auto_rate=Decimal("60.00"),
            loop_count=10,
        )
        snap2 = _make_hourly_snapshot(
            ts_start=datetime(2026, 6, 24, 9, 0, 0),
            realtime_auto_rate=None,  # 最后一条为 None
            loop_count=20,
        )

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result([snap1, snap2]),
                _make_scalar_one_or_none_result(None),
            ]
        )
        db.flush = AsyncMock()
        db.add = MagicMock()

        result = await aggregate_daily_snapshot(db, "node-001", date(2026, 6, 24))

        assert result is not None
        assert result["realtime_auto_rate"] is None


# ---------------------------------------------------------------------------
# aggregate_monthly_snapshot 测试
# ---------------------------------------------------------------------------


class TestAggregateMonthlySnapshot:
    """月聚合测试。"""

    @pytest.mark.asyncio
    async def test_aggregate_monthly_snapshot_success(self):
        """月聚合正确性：2 条日快照按 loop_count 加权平均。"""
        daily1 = _make_daily_snapshot(
            stat_date=date(2026, 6, 1),
            score=Decimal("80.00"),
            loop_count=10,
            realtime_auto_rate=Decimal("60.00"),
        )
        daily2 = _make_daily_snapshot(
            stat_date=date(2026, 6, 2),
            score=Decimal("90.00"),
            loop_count=20,
            realtime_auto_rate=Decimal("70.00"),
        )
        daily_snaps = [daily1, daily2]

        # 当月最后一条小时快照（用于取 realtime_auto_rate）
        last_hourly = MagicMock()
        last_hourly.realtime_auto_rate = Decimal("85.00")

        db = AsyncMock()
        # execute 调用顺序：
        # 1. 查询日快照
        # 2. 查询当月最后一条小时快照
        # 3. 查询已存在的月快照（None = 新增）
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result(daily_snaps),
                _make_scalar_one_or_none_result(last_hourly),
                _make_scalar_one_or_none_result(None),
            ]
        )
        db.flush = AsyncMock()
        db.add = MagicMock()

        result = await aggregate_monthly_snapshot(db, "node-001", date(2026, 6, 1))

        assert result is not None
        assert result["plant_node_id"] == "node-001"
        assert result["stat_month"] == date(2026, 6, 1)

        # 加权平均 score = (80*10 + 90*20) / (10+20) = 2600/30 = 86.67
        assert result["score"] == Decimal("86.67")
        # loop_count 取最大值
        assert result["loop_count"] == 20
        # realtime_auto_rate 取当月最后一条小时快照的值
        assert result["realtime_auto_rate"] == Decimal("85.00")
        # status 由 score 86.67 定级 → GOOD (80 <= 86.67 < 90)
        assert result["status"] == "GOOD"
        # 新增时调用 db.add
        assert db.add.call_count == 1

    @pytest.mark.asyncio
    async def test_aggregate_monthly_snapshot_no_data(self):
        """无日快照时返回 None。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_result([]))

        result = await aggregate_monthly_snapshot(db, "node-empty", date(2026, 6, 1))

        assert result is None

    @pytest.mark.asyncio
    async def test_aggregate_monthly_snapshot_idempotent(self):
        """月聚合幂等性：相同 plant_node_id + stat_month 覆盖更新。"""
        daily1 = _make_daily_snapshot(stat_date=date(2026, 6, 1), loop_count=10)
        daily_snaps = [daily1]

        last_hourly = MagicMock()
        last_hourly.realtime_auto_rate = Decimal("85.00")

        existing_monthly = MagicMock()

        db = AsyncMock()
        # 第一次调用：日快照 → 1 条；最后小时快照 → last_hourly；已存在月快照 → None（新增）
        # 第二次调用：日快照 → 1 条；最后小时快照 → last_hourly；已存在月快照 → existing（覆盖）
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_result(daily_snaps),
                _make_scalar_one_or_none_result(last_hourly),
                _make_scalar_one_or_none_result(None),
                _make_scalars_result(daily_snaps),
                _make_scalar_one_or_none_result(last_hourly),
                _make_scalar_one_or_none_result(existing_monthly),
            ]
        )
        db.flush = AsyncMock()
        db.add = MagicMock()

        result1 = await aggregate_monthly_snapshot(db, "node-001", date(2026, 6, 1))
        assert result1 is not None
        assert db.add.call_count == 1

        result2 = await aggregate_monthly_snapshot(db, "node-001", date(2026, 6, 1))
        assert result2 is not None
        assert db.add.call_count == 1  # 仍然只有一次
        assert existing_monthly.score == result2["score"]


# ---------------------------------------------------------------------------
# aggregate_all_nodes_daily 测试
# ---------------------------------------------------------------------------


class TestAggregateAllNodesDaily:
    """批量日聚合测试。"""

    @pytest.mark.asyncio
    async def test_aggregate_all_nodes_daily_with_two_nodes(self):
        """批量日聚合：mock 2 个 is_kpi_enabled 节点。"""
        node1 = MagicMock()
        node1.id = "node-001"
        node1.name = "装置 A"

        node2 = MagicMock()
        node2.id = "node-002"
        node2.name = "装置 B"

        # mock session：第一次 execute 返回节点列表
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([node1, node2]))
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.services.node_aggregation.aggregate_daily_snapshot", new_callable=AsyncMock
            ) as mock_agg,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            # 两个节点都聚合成功
            mock_agg.side_effect = [
                {"plant_node_id": "node-001", "score": Decimal("80.00")},
                {"plant_node_id": "node-002", "score": Decimal("85.00")},
            ]

            result = await aggregate_all_nodes_daily(date(2026, 6, 24))

        assert result["total"] == 2
        assert result["success"] == 2
        assert result["skipped"] == 0
        assert result["failed"] == 0
        assert result["stat_date"] == "2026-06-24"
        # 验证 aggregate_daily_snapshot 被调用 2 次
        assert mock_agg.call_count == 2

    @pytest.mark.asyncio
    async def test_aggregate_all_nodes_daily_no_enabled_nodes(self):
        """无启用 KPI 评估的节点时返回全零结果。"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([]))
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await aggregate_all_nodes_daily(date(2026, 6, 24))

        assert result["total"] == 0
        assert result["success"] == 0
        assert result["skipped"] == 0
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_aggregate_all_nodes_daily_with_skipped_and_failed(self):
        """批量日聚合：1 个成功、1 个跳过（无数据）、1 个失败（异常）。"""
        node1 = MagicMock()
        node1.id = "node-001"
        node1.name = "装置 A"

        node2 = MagicMock()
        node2.id = "node-002"
        node2.name = "装置 B"

        node3 = MagicMock()
        node3.id = "node-003"
        node3.name = "装置 C"

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_scalars_result([node1, node2, node3]))
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.services.node_aggregation.aggregate_daily_snapshot", new_callable=AsyncMock
            ) as mock_agg,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            # node1 成功，node2 跳过（None），node3 失败（异常）
            mock_agg.side_effect = [
                {"plant_node_id": "node-001", "score": Decimal("80.00")},
                None,
                RuntimeError("DB error"),
            ]

            result = await aggregate_all_nodes_daily(date(2026, 6, 24))

        assert result["total"] == 3
        assert result["success"] == 1
        assert result["skipped"] == 1
        assert result["failed"] == 1


# ---------------------------------------------------------------------------
# 加权平均算法单元测试
# ---------------------------------------------------------------------------


class TestWeightedAverage:
    """加权平均算法测试。"""

    def test_weighted_average_basic(self):
        """基本加权平均计算。"""
        from app.services.node_aggregation import _weighted_average

        snaps = [
            MagicMock(score=Decimal("80.00"), loop_count=10),
            MagicMock(score=Decimal("90.00"), loop_count=20),
        ]
        result = _weighted_average(snaps, ("score",))
        # (80*10 + 90*20) / 30 = 2600/30 = 86.67
        assert result["score"] == Decimal("86.67")

    def test_weighted_average_all_none(self):
        """所有值为 None 时返回 None。"""
        from app.services.node_aggregation import _weighted_average

        snaps = [
            MagicMock(score=None, loop_count=10),
            MagicMock(score=None, loop_count=20),
        ]
        result = _weighted_average(snaps, ("score",))
        assert result["score"] is None

    def test_weighted_average_zero_loop_count(self):
        """所有 loop_count=0 时退化为简单平均。"""
        from app.services.node_aggregation import _weighted_average

        snaps = [
            MagicMock(score=Decimal("80.00"), loop_count=0),
            MagicMock(score=Decimal("90.00"), loop_count=0),
        ]
        result = _weighted_average(snaps, ("score",))
        # 简单平均 (80+90)/2 = 85.00
        assert result["score"] == Decimal("85.00")

    def test_weighted_average_partial_none(self):
        """部分值为 None 时只加权非 None 值。"""
        from app.services.node_aggregation import _weighted_average

        snaps = [
            MagicMock(score=Decimal("80.00"), loop_count=10),
            MagicMock(score=None, loop_count=20),
            MagicMock(score=Decimal("90.00"), loop_count=30),
        ]
        result = _weighted_average(snaps, ("score",))
        # (80*10 + 90*30) / (10+30) = 3500/40 = 87.50
        # 注意：分母是所有 loop_count 之和（包括 None 的），还是非 None 的？
        # 实现中分母是所有 loop_count 之和
        assert result["score"] == Decimal("87.50")


# ---------------------------------------------------------------------------
# P2 #28 R4: 节点级聚合权重体系设计意图验证
# ---------------------------------------------------------------------------


class TestNodeAggregationWeightDesign:
    """验证节点级聚合的权重体系设计意图（P2 #28 R4）。

    设计决策：
    - 小时聚合（node_performance.py）：LoopLevelWeight（回路级别 1:3, 2:2, 3:1）
    - 日/月聚合（node_aggregation.py）：loop_count（节点规模）

    这是两套不同的权重体系，处理不同维度的聚合：
    - 小时聚合按"回路重要性"加权（重要回路占更高权重）
    - 日/月聚合按"节点规模"加权（回路数多的小时/日代表性更强）
    """

    def test_loop_count_weighting_differs_from_simple_average(self):
        """loop_count 加权与简单平均产生不同结果，证明加权是有意义的。"""
        from app.services.node_aggregation import _weighted_average

        # 两条快照：score=80 loop_count=1, score=90 loop_count=99
        # 简单平均：(80+90)/2 = 85.00
        # loop_count 加权：(80*1 + 90*99)/(1+99) = (80+8910)/100 = 89.90
        snaps = [
            MagicMock(score=Decimal("80.00"), loop_count=1),
            MagicMock(score=Decimal("90.00"), loop_count=99),
        ]
        result = _weighted_average(snaps, ("score",))
        assert result["score"] == Decimal("89.90")
        # 验证与简单平均不同（证明加权生效）
        assert result["score"] != Decimal("85.00")

    def test_higher_loop_count_dominates_result(self):
        """loop_count 更高的快照对结果影响更大。"""
        from app.services.node_aggregation import _weighted_average

        # 极端情况：loop_count=1000 的快照几乎决定结果
        snaps = [
            MagicMock(score=Decimal("50.00"), loop_count=1),
            MagicMock(score=Decimal("95.00"), loop_count=1000),
        ]
        result = _weighted_average(snaps, ("score",))
        # (50*1 + 95*1000)/(1+1000) = (50+95000)/1001 ≈ 94.96
        assert result["score"] == Decimal("94.96")
        # 结果接近 95（loop_count=1000 的快照主导）

    def test_module_docstring_documents_weight_design(self):
        """模块 docstring 应明确文档化两套权重体系的设计依据。"""
        from app.services import node_aggregation

        doc = node_aggregation.__doc__ or ""
        # 验证 docstring 包含关键设计说明
        assert "LoopLevelWeight" in doc, "docstring 应说明小时聚合使用 LoopLevelWeight"
        assert "loop_count" in doc, "docstring 应说明日/月聚合使用 loop_count"
        assert "回路重要性" in doc or "回路级别" in doc, "docstring 应说明小时聚合的权重依据"
        assert "节点规模" in doc or "代表性" in doc, "docstring 应说明日/月聚合的权重依据"
        assert "P2 #28" in doc, "docstring 应标注此修复对应的 P2 #28 编号"
