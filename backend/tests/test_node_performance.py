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
    fast_rate: Decimal = Decimal("90.00"),
    good_value_rate: Decimal = Decimal("100.00"),
    oscillation_rate: Decimal = Decimal("10.00"),
    saturation_rate: Decimal = Decimal("5.00"),
    instrument_fault_rate: Decimal = Decimal("3.00"),
    stiction_index: Decimal = Decimal("0.10"),
    settling_time: Decimal = Decimal("120.00"),
    output_trip_index: Decimal = Decimal("35.00"),
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
    snap.fast_rate = fast_rate
    snap.good_value_rate = good_value_rate
    snap.oscillation_rate = oscillation_rate
    snap.saturation_rate = saturation_rate
    snap.instrument_fault_rate = instrument_fault_rate
    snap.stiction_index = stiction_index
    snap.settling_time = settling_time
    snap.output_trip_index = output_trip_index
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
    snap.fast_rate = Decimal("82.00")
    snap.oscillation_rate = Decimal("15.00")
    snap.saturation_rate = Decimal("8.00")
    snap.instrument_fault_rate = Decimal("3.00")
    snap.stiction_index = Decimal("0.15")
    snap.settling_time = Decimal("150.00")
    snap.output_trip_index = Decimal("42.00")
    snap.ideal_settling_time = Decimal("180.00")
    snap.auto_loop_ratio = Decimal("90.00")
    snap.loop_count = 5
    snap.status = status
    snap.algorithm_version = "KPI_CALC_v2.0"
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


def _make_loop_row(
    loop_id: str,
    *,
    weight: Decimal = Decimal("1.0"),
    score: Decimal = Decimal("80"),
    auto_mode_rate: Decimal = Decimal("88"),
    confidence_level: str | None = "A",
    complex_group_id: str | None = None,
    complex_role: str | None = None,
) -> MagicMock:
    """构造 _fetch_and_aggregate_loops 返回的单回路行 mock。

    S3 重构后聚合改为 Python 层：每行需含 KPI 字段 + 复杂分组/角色/权重/confidence。
    未指定的 KPI 字段使用与 test_aggregate_calculates_weighted_average 一致的默认值。
    """
    row = MagicMock()
    row.loop_id = loop_id
    row.weight = weight
    row.confidence_level = confidence_level
    row.complex_loop_group_id = complex_group_id
    row.complex_role = complex_role
    row.score = score
    row.auto_mode_rate = auto_mode_rate
    # 其余 KPI 字段默认值（与原 test_aggregate_calculates_weighted_average 对齐）
    row.good_value_rate = Decimal("95.00")
    row.effective_auto_rate = Decimal("85.00")
    row.steady_rate = Decimal("80.00")
    row.accuracy_rate = Decimal("78.00")
    row.fast_rate = Decimal("82.00")
    row.oscillation_rate = Decimal("15.00")
    row.saturation_rate = Decimal("8.00")
    row.instrument_fault_rate = Decimal("3.00")
    row.stiction_index = Decimal("0.12")
    row.settling_time = Decimal("135.00")
    row.output_trip_index = Decimal("38.00")
    row.ideal_settling_time = Decimal("180.00")
    return row


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
        """有回路但无 SUCCESS 快照时返回 None。"""
        db = AsyncMock()
        # _fetch_and_aggregate_loops 调 result.all() 返回空列表 → None
        mock_result = MagicMock()
        mock_result.all.return_value = []
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
        """正确计算加权平均值（3 个普通单回路，等权 1.0）。"""
        db = AsyncMock()
        # 3 个普通单回路（complex_loop_group_id=None），权重均为 1.0
        rows = [
            _make_loop_row(
                "loop-001",
                weight=Decimal("1.0"),
                score=Decimal("80"),
                auto_mode_rate=Decimal("88"),
            ),
            _make_loop_row(
                "loop-002",
                weight=Decimal("1.0"),
                score=Decimal("80"),
                auto_mode_rate=Decimal("88"),
            ),
            _make_loop_row(
                "loop-003",
                weight=Decimal("1.0"),
                score=Decimal("80"),
                auto_mode_rate=Decimal("0"),  # 非自动 → auto_loop_ratio=2/3
            ),
        ]
        main_result = MagicMock()
        main_result.all.return_value = rows
        # 后续 excluded/inconclusive 计数查询返回 0
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 0

        async def _execute(stmt, *a, **kw):
            return main_result if stmt.is_select else scalar_result

        db.execute = AsyncMock(side_effect=_execute)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["loop-001", "loop-002", "loop-003"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value=None,
            ),
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
        # Phase 1 新增：仪表故障率参与节点级聚合
        assert result["instrument_fault_rate"] == Decimal("3.00")
        # P1 #14: 验证 4 个新增字段被正确序列化
        assert result["stiction_index"] == Decimal("0.12")
        assert result["settling_time"] == Decimal("135.00")
        assert result["output_trip_index"] == Decimal("38.00")
        assert result["ideal_settling_time"] == Decimal("180.00")

    @pytest.mark.asyncio
    async def test_aggregation_filters_include_in_evaluation_false(self):
        """S1：主聚合 SQL 须含 include_in_evaluation = true 过滤。

        回归背景：该字段此前仅在 dashboard 计数与 inconclusive 查询使用，
        主聚合 SQL 漏过滤，导致 include_in_evaluation=False 回路仍被聚合。
        S3 重构后过滤逻辑在 _fetch_and_aggregate_loops，仍须验证 SQL 含过滤。
        """
        db = AsyncMock()
        captured_stmts: list = []

        rows = [
            _make_loop_row("loop-001", weight=Decimal("1.0")),
            _make_loop_row("loop-002", weight=Decimal("1.0")),
        ]
        main_result = MagicMock()
        main_result.all.return_value = rows
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 0

        async def _capture(stmt, *args, **kwargs):
            captured_stmts.append(stmt)
            return main_result if len(captured_stmts) == 1 else scalar_result

        db.execute = AsyncMock(side_effect=_capture)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["loop-001", "loop-002", "loop-003"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value=None,
            ),
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-001",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )

        assert result is not None
        # 主聚合 SQL（第一次 db.execute）须含 include_in_evaluation 过滤
        main_sql = str(captured_stmts[0].compile(compile_kwargs={"literal_binds": True}))
        assert "include_in_evaluation" in main_sql
        assert "true" in main_sql.lower()
        # loop_count 反映去重后回路数（2 个单回路）
        assert result["loop_count"] == 2

    @pytest.mark.asyncio
    async def test_aggregate_dedup_complex_group_picks_main(self):
        """S3：复杂回路组去重——MAIN+SUB 同组，仅 MAIN 进入聚合。

        2 个单回路 + 1 个串级组（MAIN score=90 + SUB score=50），
        去重后 loop_count=3（2 单回路 + 1 组代表），score 受 MAIN 主导。
        """
        db = AsyncMock()
        rows = [
            _make_loop_row("s1", weight=Decimal("1.0"), score=Decimal("70")),
            _make_loop_row("s2", weight=Decimal("1.0"), score=Decimal("70")),
            _make_loop_row(
                "cascade-main",
                weight=Decimal("1.0"),
                score=Decimal("90"),
                complex_group_id="grp-1",
                complex_role="MAIN",
            ),
            _make_loop_row(
                "cascade-sub",
                weight=Decimal("1.0"),
                score=Decimal("50"),
                complex_group_id="grp-1",
                complex_role="SUB",
            ),
        ]
        main_result = MagicMock()
        main_result.all.return_value = rows
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 0

        async def _execute(stmt, *a, **kw):
            return main_result if stmt.is_select else scalar_result

        db.execute = AsyncMock(side_effect=_execute)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["s1", "s2", "cascade-main", "cascade-sub"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value=None,
            ),
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-001",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )

        assert result is not None
        # 去重后：2 单回路 + 1 组代表 = 3（SUB 被排除）
        assert result["loop_count"] == 3
        # 加权平均 = (70 + 70 + 90) / 3 = 76.67（SUB 的 50 不参与）
        assert result["score"] == Decimal("76.67")

    @pytest.mark.asyncio
    async def test_aggregate_dedup_falls_back_to_highest_confidence(self):
        """S3：MAIN 缺席时退化取 confidence 最高的 SUB 代表。

        1 个串级组含 2 个 SUB（无 MAIN）：confidence B(score=60) 与 D(score=80)，
        去重后取 confidence=B 的 SUB（A>B>C>D>E，B 优于 D），score=60 进入聚合。
        """
        db = AsyncMock()
        rows = [
            _make_loop_row(
                "sub-b",
                weight=Decimal("1.0"),
                score=Decimal("60"),
                complex_group_id="grp-1",
                complex_role="SUB",
                confidence_level="B",
            ),
            _make_loop_row(
                "sub-d",
                weight=Decimal("1.0"),
                score=Decimal("80"),
                complex_group_id="grp-1",
                complex_role="SUB",
                confidence_level="D",
            ),
        ]
        main_result = MagicMock()
        main_result.all.return_value = rows
        scalar_result = MagicMock()
        scalar_result.scalar.return_value = 0

        async def _execute(stmt, *a, **kw):
            return main_result if stmt.is_select else scalar_result

        db.execute = AsyncMock(side_effect=_execute)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["sub-b", "sub-d"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value=None,
            ),
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-001",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )

        assert result is not None
        # 去重后：1 组代表 = 1
        assert result["loop_count"] == 1
        # 取 confidence=B 的 SUB（score=60），而非 score 更高但 confidence=D 的 SUB
        assert result["score"] == Decimal("60.00")


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
            "fast_rate": Decimal("82.00"),
            "oscillation_rate": Decimal("15.00"),
            "saturation_rate": Decimal("8.00"),
            "stiction_index": Decimal("0.12"),
            "settling_time": Decimal("135.00"),
            "output_trip_index": Decimal("38.00"),
            "ideal_settling_time": Decimal("180.00"),
            "auto_loop_ratio": Decimal("66.67"),
            "loop_count": 3,
            "status": "FAIR",
            "algorithm_version": "KPI_CALC_v2.0",
            # v5.3 新增 unit_kpi_summary 聚合字段
            "total_loops": 5,
            "evaluated_loops": 3,
            "excluded_loops": 1,
            "inconclusive_loops": 1,
            "unit_status": "PARTIAL",
        }

        result = await save_node_snapshot(db, snap_data)
        assert result["plant_node_id"] == "node-001"
        # v5.3：并行写入 KpiNodeSnapshotHourly + UnitKpiSummary → db.add 调用 2 次
        assert db.add.call_count == 2

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
            "fast_rate": Decimal("82.00"),
            "oscillation_rate": Decimal("15.00"),
            "saturation_rate": Decimal("8.00"),
            "stiction_index": Decimal("0.12"),
            "settling_time": Decimal("135.00"),
            "output_trip_index": Decimal("38.00"),
            "ideal_settling_time": Decimal("180.00"),
            "auto_loop_ratio": Decimal("66.67"),
            "loop_count": 3,
            "status": "GOOD",
            "algorithm_version": "KPI_CALC_v2.0",
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
