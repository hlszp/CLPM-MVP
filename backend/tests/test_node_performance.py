"""Node-level performance evaluation tests (GB/T 44693.2-2024 §6.4).

覆盖：
- 递归收集节点下属回路
- 加权聚合回路级快照
- 节点级快照幂等写入
- 查询服务（最新快照、趋势、排名、总览）
- API 端点（5 个）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.node_performance import (
    aggregate_node_snapshot,
    collect_descendant_loop_ids,
    get_node_latest_snapshot,
    get_nodes_overview,
    save_node_snapshot,
)
from app.services.performance import _score_to_status

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_loop_snapshot(
    loop_id: str,
    score: Decimal = Decimal("80.00"),
    auto_mode_rate: Decimal = Decimal("90.00"),
    steady_rate: Decimal = Decimal("85.00"),
    effective_auto_rate: Decimal = Decimal("88.00"),
    accuracy_rate: Decimal = Decimal("82.00"),
    fast_response_rate: Decimal = Decimal("90.00"),
    good_value_rate: Decimal = Decimal("100.00"),
    oscillation_rate: Decimal = Decimal("10.00"),
    saturation_rate: Decimal = Decimal("5.00"),
    stiction_coeff: Decimal = Decimal("0.10"),
    steady_state_time: Decimal = Decimal("120.00"),
    output_travel_index: Decimal = Decimal("35.00"),
    ideal_settling_time: Decimal = Decimal("180.00"),
    ts_start: datetime | None = None,
) -> MagicMock:
    """构造回路级快照 mock（P1 #14: 补全 4 个诊断字段）。"""
    snap = MagicMock()
    snap.loop_id = loop_id
    snap.ts_start = ts_start or datetime.now(UTC).replace(tzinfo=None)
    snap.ts_end = snap.ts_start + timedelta(hours=1)
    snap.status = "SUCCESS"
    snap.score = score
    snap.auto_mode_rate = auto_mode_rate
    snap.steady_rate = steady_rate
    snap.effective_auto_rate = effective_auto_rate
    snap.accuracy_rate = accuracy_rate
    snap.fast_response_rate = fast_response_rate
    snap.good_value_rate = good_value_rate
    snap.oscillation_rate = oscillation_rate
    snap.saturation_rate = saturation_rate
    snap.stiction_coeff = stiction_coeff
    snap.steady_state_time = steady_state_time
    snap.output_travel_index = output_travel_index
    snap.ideal_settling_time = ideal_settling_time
    return snap


def _make_node_snapshot(
    plant_node_id: str,
    score: Decimal = Decimal("75.00"),
    status: str = "FAIR",
    ts_start: datetime | None = None,
) -> MagicMock:
    """构造节点级快照 mock。"""
    snap = MagicMock()
    snap.id = "snap-001"
    snap.plant_node_id = plant_node_id
    snap.ts_start = ts_start or datetime.now(UTC).replace(tzinfo=None)
    snap.ts_end = snap.ts_start + timedelta(hours=1)
    snap.score = score
    snap.good_value_rate = Decimal("95.00")
    snap.auto_mode_rate = Decimal("88.00")
    snap.effective_auto_rate = Decimal("85.00")
    snap.steady_rate = Decimal("80.00")
    snap.accuracy_rate = Decimal("78.00")
    snap.fast_response_rate = Decimal("82.00")
    snap.oscillation_rate = Decimal("15.00")
    snap.saturation_rate = Decimal("8.00")
    snap.stiction_coeff = Decimal("0.15")
    snap.steady_state_time = Decimal("150.00")
    snap.output_travel_index = Decimal("42.00")
    snap.ideal_settling_time = Decimal("180.00")
    snap.auto_loop_ratio = Decimal("90.00")
    snap.loop_count = 5
    snap.status = status
    snap.algorithm_version = "KPI_CALC_v1.0"
    snap.created_at = datetime.now(UTC).replace(tzinfo=None)
    return snap


# ---------------------------------------------------------------------------
# collect_descendant_loop_ids 测试
# ---------------------------------------------------------------------------


class TestCollectDescendantLoopIds:
    """递归收集节点下属回路。"""

    @pytest.mark.asyncio
    async def test_collect_returns_loop_ids(self):
        """递归 CTE 返回回路 ID 列表。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [
            MagicMock(loop_id="loop-001"),
            MagicMock(loop_id="loop-002"),
            MagicMock(loop_id="loop-003"),
        ]
        db.execute = AsyncMock(return_value=mock_result)

        result = await collect_descendant_loop_ids(db, "node-001")

        assert result == ["loop-001", "loop-002", "loop-003"]
        assert db.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_collect_empty_when_no_loops(self):
        """无下属回路时返回空列表。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        db.execute = AsyncMock(return_value=mock_result)

        result = await collect_descendant_loop_ids(db, "node-empty")

        assert result == []


# ---------------------------------------------------------------------------
# aggregate_node_snapshot 测试
# ---------------------------------------------------------------------------


class TestAggregateNodeSnapshot:
    """加权聚合回路级快照。"""

    @pytest.mark.asyncio
    async def test_aggregate_returns_none_when_no_loops(self):
        """无下属回路时返回 None。"""
        db = AsyncMock()
        with patch(
            "app.services.node_performance.collect_descendant_loop_ids",
            return_value=[],
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-001",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_aggregate_returns_none_when_no_snapshots(self):
        """有回路但无快照时返回 None。"""
        db = AsyncMock()
        # 子查询返回空
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.cnt = 0
        mock_row.weight_sum = None
        mock_row.auto_loop_count = 0
        mock_result.one.return_value = mock_row
        db.execute = AsyncMock(return_value=mock_result)

        with patch(
            "app.services.node_performance.collect_descendant_loop_ids",
            return_value=["loop-001", "loop-002"],
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-001",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_aggregate_calculates_weighted_average(self):
        """正确计算加权平均值。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()
        mock_row.cnt = 3
        mock_row.weight_sum = Decimal("3.0")
        mock_row.auto_loop_count = 2
        mock_row.score = Decimal("80.00")
        mock_row.good_value_rate = Decimal("95.00")
        mock_row.auto_mode_rate = Decimal("88.00")
        mock_row.effective_auto_rate = Decimal("85.00")
        mock_row.steady_rate = Decimal("80.00")
        mock_row.accuracy_rate = Decimal("78.00")
        mock_row.fast_response_rate = Decimal("82.00")
        mock_row.oscillation_rate = Decimal("15.00")
        mock_row.saturation_rate = Decimal("8.00")
        # P1 #14: 4 个新增诊断字段
        mock_row.stiction_coeff = Decimal("0.12")
        mock_row.steady_state_time = Decimal("135.00")
        mock_row.output_travel_index = Decimal("38.00")
        mock_row.ideal_settling_time = Decimal("180.00")
        mock_result.one.return_value = mock_row
        db.execute = AsyncMock(return_value=mock_result)

        with patch(
            "app.services.node_performance.collect_descendant_loop_ids",
            return_value=["loop-001", "loop-002", "loop-003"],
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-001",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )

        assert result is not None
        assert result["plant_node_id"] == "node-001"
        assert result["loop_count"] == 3
        assert result["auto_loop_ratio"] == Decimal("66.67")  # 2/3*100
        assert result["status"] == "GOOD"  # score=80 → GOOD
        assert result["score"] == Decimal("80.00")
        # P1 #14: 验证 4 个新增字段被正确序列化
        assert result["stiction_coeff"] == Decimal("0.12")
        assert result["steady_state_time"] == Decimal("135.00")
        assert result["output_travel_index"] == Decimal("38.00")
        assert result["ideal_settling_time"] == Decimal("180.00")


# ---------------------------------------------------------------------------
# save_node_snapshot 测试
# ---------------------------------------------------------------------------


class TestSaveNodeSnapshot:
    """节点级快照幂等写入。"""

    @pytest.mark.asyncio
    async def test_save_new_snapshot(self):
        """新增快照。"""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # 不存在
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()

        snap_data = {
            "plant_node_id": "node-001",
            "ts_start": datetime.now(UTC).replace(tzinfo=None),
            "ts_end": datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            "score": Decimal("75.00"),
            "good_value_rate": Decimal("95.00"),
            "auto_mode_rate": Decimal("88.00"),
            "effective_auto_rate": Decimal("85.00"),
            "steady_rate": Decimal("80.00"),
            "accuracy_rate": Decimal("78.00"),
            "fast_response_rate": Decimal("82.00"),
            "oscillation_rate": Decimal("15.00"),
            "saturation_rate": Decimal("8.00"),
            "stiction_coeff": Decimal("0.12"),
            "steady_state_time": Decimal("135.00"),
            "output_travel_index": Decimal("38.00"),
            "ideal_settling_time": Decimal("180.00"),
            "auto_loop_ratio": Decimal("66.67"),
            "loop_count": 3,
            "status": "FAIR",
            "algorithm_version": "KPI_CALC_v1.0",
        }

        result = await save_node_snapshot(db, snap_data)
        assert result["plant_node_id"] == "node-001"
        assert db.add.call_count == 1

    @pytest.mark.asyncio
    async def test_save_overwrite_existing(self):
        """覆盖已存在快照。"""
        db = AsyncMock()
        existing = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=mock_result)
        db.flush = AsyncMock()

        snap_data = {
            "plant_node_id": "node-001",
            "ts_start": datetime.now(UTC).replace(tzinfo=None),
            "ts_end": datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            "score": Decimal("85.00"),
            "good_value_rate": Decimal("95.00"),
            "auto_mode_rate": Decimal("88.00"),
            "effective_auto_rate": Decimal("85.00"),
            "steady_rate": Decimal("80.00"),
            "accuracy_rate": Decimal("78.00"),
            "fast_response_rate": Decimal("82.00"),
            "oscillation_rate": Decimal("15.00"),
            "saturation_rate": Decimal("8.00"),
            "stiction_coeff": Decimal("0.12"),
            "steady_state_time": Decimal("135.00"),
            "output_travel_index": Decimal("38.00"),
            "ideal_settling_time": Decimal("180.00"),
            "auto_loop_ratio": Decimal("66.67"),
            "loop_count": 3,
            "status": "GOOD",
            "algorithm_version": "KPI_CALC_v1.0",
        }

        result = await save_node_snapshot(db, snap_data)
        assert result["status"] == "GOOD"
        assert db.add.call_count == 0  # 不新增，只更新


# ---------------------------------------------------------------------------
# 查询服务测试
# ---------------------------------------------------------------------------


class TestQueryServices:
    """查询服务。"""

    @pytest.mark.asyncio
    async def test_get_node_latest_snapshot(self):
        """获取节点最新快照。"""
        db = AsyncMock()
        snap = _make_node_snapshot("node-001")
        snap_result = MagicMock()
        snap_result.scalar_one_or_none.return_value = snap

        node = MagicMock()
        node.name = "HDS 装置"
        node_result = MagicMock()
        node_result.scalar_one_or_none.return_value = node

        db.execute = AsyncMock(side_effect=[snap_result, node_result])

        result = await get_node_latest_snapshot(db, "node-001")
        assert result is not None
        assert result["plantNodeId"] == "node-001"
        assert result["plantNodeName"] == "HDS 装置"
        assert result["status"] == "FAIR"

    @pytest.mark.asyncio
    async def test_get_node_latest_snapshot_not_found(self):
        """节点无快照时返回 None。"""
        db = AsyncMock()
        snap_result = MagicMock()
        snap_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=snap_result)

        result = await get_node_latest_snapshot(db, "node-empty")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_nodes_overview_empty(self):
        """无启用节点时返回空总览。"""
        db = AsyncMock()
        node_result = MagicMock()
        node_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=node_result)

        result = await get_nodes_overview(
            db,
            datetime.now(UTC).replace(tzinfo=None),
            datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
        assert result["totalNodes"] == 0
        assert result["nodes"] == []


# ---------------------------------------------------------------------------
# _score_to_status 5 级定级测试（节点级复用）
# ---------------------------------------------------------------------------


class TestNodeScoreToStatus:
    """节点级 5 级定级（复用回路级 _score_to_status）。"""

    @pytest.mark.parametrize(
        "score,expected",
        [
            (Decimal("95"), "EXCELLENT"),
            (Decimal("90"), "EXCELLENT"),
            (Decimal("89.99"), "GOOD"),
            (Decimal("80"), "GOOD"),
            (Decimal("79.99"), "FAIR"),
            (Decimal("70"), "FAIR"),
            (Decimal("69.99"), "WARNING"),
            (Decimal("60"), "WARNING"),
            (Decimal("59.99"), "POOR"),
            (Decimal("0"), "POOR"),
            (None, "INCONCLUSIVE"),
        ],
    )
    def test_score_to_status(self, score, expected):
        assert _score_to_status(score) == expected
