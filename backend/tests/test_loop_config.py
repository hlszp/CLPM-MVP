"""Loop configuration & 评分算法 v2 & 节点聚合 & 实时自控率 P0 单元测试。

测试覆盖：
- TEST-01: 投用定义 CRUD（list/replace/get_auto/get_effective）
- TEST-02: 评分算法 v2（4 种回路类型 + R 缺失 + 无权重回退 + infer_score_type）
- TEST-03: 节点聚合 v2（按 level 加权 / level=NULL 回退 1.0）
- TEST-04: 实时自控率读投用定义（有配置/无配置/空回路列表）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from app.services.loop_config import (
    get_auto_mode_values,
    get_effective_mode_values,
    infer_score_type,
    list_mode_mappings,
    replace_mode_mappings,
)
from app.services.node_performance import (
    aggregate_node_snapshot,
    query_realtime_auto_rate,
)

# ===========================================================================
# 辅助函数：构造 mock 对象
# ===========================================================================


def _make_mode_mapping(
    loop_id: str = "loop-001",
    mode_value: int = 1,
    mode_label: str = "AUTO",
    is_auto: bool = True,
    is_effective: bool = True,
) -> MagicMock:
    """构造 LoopModeMapping mock。"""
    m = MagicMock()
    m.id = f"mm-{loop_id}-{mode_value}"
    m.loop_id = loop_id
    m.mode_value = mode_value
    m.mode_label = mode_label
    m.is_auto = is_auto
    m.is_effective = is_effective
    m.created_at = datetime(2026, 6, 22, 8, 0, 0)
    return m


def _make_scalars_mock(items: list) -> MagicMock:
    """构造 execute 返回值，支持 scalars().all()。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_rows_mock(rows: list) -> MagicMock:
    """构造 execute 返回值，支持 .all()。"""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _make_scalar_one_or_none_mock(value: object) -> MagicMock:
    """构造 execute 返回值，支持 scalar_one_or_none()。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_agg_row(
    cnt: int = 3,
    auto_loop_count: int = 2,
    weight_sum: Decimal | None = Decimal("6.0"),
    score: Decimal | None = Decimal("80.00"),
) -> MagicMock:
    """构造 aggregate_node_snapshot 的聚合行 mock。"""
    row = MagicMock()
    row.cnt = cnt
    row.auto_loop_count = auto_loop_count
    row.weight_sum = weight_sum
    row.score = score
    row.good_value_rate = Decimal("95.00")
    row.auto_mode_rate = Decimal("88.00")
    row.effective_auto_rate = Decimal("85.00")
    row.steady_rate = Decimal("80.00")
    row.accuracy_rate = Decimal("78.00")
    row.fast_rate = Decimal("82.00")
    row.oscillation_rate = Decimal("15.00")
    row.saturation_rate = Decimal("8.00")
    # P1 #14: 4 个诊断字段（None 表示无数据，avg_value 会返回 None）
    row.stiction_index = None
    row.settling_time = None
    row.output_trip_index = None
    row.ideal_settling_time = None
    return row


# ===========================================================================
# TEST-01: 投用定义 CRUD
# ===========================================================================


class TestModeMappingCRUD:
    """投用定义 CRUD 测试。"""

    @pytest.mark.asyncio
    async def test_list_mode_mappings_empty(self) -> None:
        """无配置时返回空列表。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalars_mock([]))

        result = await list_mode_mappings(db, "loop-001")

        assert result == []

    @pytest.mark.asyncio
    async def test_replace_mode_mappings_success(self) -> None:
        """全量替换成功（3 条映射）。"""
        db = AsyncMock()
        # 1st execute: 查询旧数据（空）；2nd execute: delete（返回值不使用）
        db.execute = AsyncMock(side_effect=[_make_scalars_mock([]), MagicMock()])
        db.add = MagicMock()
        db.commit = AsyncMock()

        mappings = [
            {"modeValue": 1, "modeLabel": "AUTO", "isAuto": True, "isEffective": True},
            {"modeValue": 2, "modeLabel": "CAS", "isAuto": True, "isEffective": False},
            {"modeValue": 0, "modeLabel": "MANUAL", "isAuto": False, "isEffective": False},
        ]

        result = await replace_mode_mappings(db, "loop-001", "admin", mappings)

        assert len(result) == 3
        assert result[0]["modeValue"] == 1
        assert result[0]["modeLabel"] == "AUTO"
        assert result[0]["isAuto"] is True
        assert result[1]["modeValue"] == 2
        assert result[1]["modeLabel"] == "CAS"
        assert result[2]["modeValue"] == 0
        assert result[2]["modeLabel"] == "MANUAL"
        assert result[2]["isAuto"] is False
        # db.add 调用 4 次：3 条映射 + 1 条审计日志
        assert db.add.call_count == 4
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_replace_mode_mappings_duplicate(self) -> None:
        """MODE 值重复时抛 ERR_MODE_MAPPING_DUPLICATE。"""
        db = AsyncMock()

        mappings = [
            {"modeValue": 1, "modeLabel": "AUTO", "isAuto": True, "isEffective": True},
            {"modeValue": 1, "modeLabel": "CAS", "isAuto": True, "isEffective": False},
        ]

        with pytest.raises(BizError) as exc_info:
            await replace_mode_mappings(db, "loop-001", "admin", mappings)
        assert exc_info.value.code == "ERR_MODE_MAPPING_DUPLICATE"

    @pytest.mark.asyncio
    async def test_replace_mode_mappings_invalid_label(self) -> None:
        """无效 modeLabel 抛 ERR_MODE_MAPPING_INVALID。"""
        db = AsyncMock()

        mappings = [
            {"modeValue": 1, "modeLabel": "INVALID", "isAuto": True, "isEffective": True},
        ]

        with pytest.raises(BizError) as exc_info:
            await replace_mode_mappings(db, "loop-001", "admin", mappings)
        assert exc_info.value.code == "ERR_MODE_MAPPING_INVALID"

    @pytest.mark.asyncio
    async def test_get_auto_mode_values_with_config(self) -> None:
        """有配置时返回配置的自动 MODE 值。"""
        db = AsyncMock()
        rows = [
            MagicMock(loop_id="loop-001", mode_value=1),
            MagicMock(loop_id="loop-001", mode_value=2),
        ]
        db.execute = AsyncMock(return_value=_make_rows_mock(rows))

        result = await get_auto_mode_values(db, "loop-001")

        assert result == {1, 2}

    @pytest.mark.asyncio
    async def test_get_auto_mode_values_no_config(self) -> None:
        """无配置时回退默认 {1,2,3}。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_rows_mock([]))

        result = await get_auto_mode_values(db, "loop-001")

        assert result == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_get_effective_mode_values_with_config(self) -> None:
        """有配置时返回有效 MODE 值。"""
        db = AsyncMock()
        rows = [
            MagicMock(loop_id="loop-001", mode_value=1),
            MagicMock(loop_id="loop-001", mode_value=3),
        ]
        db.execute = AsyncMock(return_value=_make_rows_mock(rows))

        result = await get_effective_mode_values(db, "loop-001")

        assert result == {1, 3}

    @pytest.mark.asyncio
    async def test_get_effective_mode_values_no_config(self) -> None:
        """无配置时回退默认 {1,2,3}。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_rows_mock([]))

        result = await get_effective_mode_values(db, "loop-001")

        assert result == {1, 2, 3}


# ===========================================================================
# TEST-02: 评分算法 v2（4 种回路类型）
# ===========================================================================
# v4.0 重构后 _compute_composite_score_v2 已被 ConfidenceEvaluator.compute_composite_score
# 取代，旧测试已移除。评分算法测试在 test_kpi_calc.py::TestComputeKpisThreeLayer 中覆盖。


class TestInferScoreType:
    """工艺类型→评分类型映射测试。"""

    @pytest.mark.parametrize(
        "loop_type,expected",
        [
            ("TEMPERATURE", "STABLE"),
            ("PRESSURE", "STABLE"),
            ("LEVEL", "SLOW"),
            ("ANALYSIS", "SLOW"),
            ("FLOW", "FAST"),
            ("SPEED", "FAST"),
            ("OTHER", "LOGIC"),
            (None, "LOGIC"),
            ("UNKNOWN", "LOGIC"),
        ],
    )
    def test_infer_score_type(self, loop_type: str | None, expected: str) -> None:
        """工艺类型→评分类型映射（TEMPERATURE→STABLE 等）。"""
        assert infer_score_type(loop_type) == expected


# ===========================================================================
# TEST-03: 节点聚合 v2（3 种级别加权）
# ===========================================================================


class TestAggregateNodeSnapshotLevelWeighting:
    """节点聚合 v2 级别加权测试。

    验证 aggregate_node_snapshot 在不同 level 权重场景下的处理。
    level 权重由 SQL 中 func.coalesce(LoopLevelWeight.weight, 1.0) 计算，
    测试通过 mock 聚合行验证函数对结果的正确处理。
    """

    @pytest.mark.asyncio
    async def test_aggregate_node_snapshot_level_weighting(self) -> None:
        """验证按 level 加权（mock loop_level_weight 表数据）。

        3 条回路分别 level=1/2/3，权重 3.0/2.0/1.0，weight_sum=6.0。
        """
        db = AsyncMock()
        # mock 聚合查询返回（加权计算由 SQL 完成，mock 返回最终聚合值）
        agg_row = _make_agg_row(
            cnt=3,
            auto_loop_count=2,
            weight_sum=Decimal("6.0"),
            score=Decimal("80.00"),
        )
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row
        db.execute = AsyncMock(return_value=agg_result)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["loop-001", "loop-002", "loop-003"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value={
                    "rate": Decimal("66.67"),
                    "auto_count": 2,
                    "manual_count": 1,
                    "total_count": 3,
                    "read_at": "2026-06-22T08:00:00Z",
                },
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
        assert result["score"] == Decimal("80.00")
        assert result["auto_loop_ratio"] == Decimal("66.67")  # 2/3*100
        assert result["realtime_auto_rate"] == Decimal("66.67")
        assert result["status"] == "GOOD"  # score=80 → GOOD

    @pytest.mark.asyncio
    async def test_aggregate_node_snapshot_no_level(self) -> None:
        """level=NULL 时回退 1.0。

        2 条回路 level=NULL，COALESCE 到 1.0，weight_sum=2.0。
        """
        db = AsyncMock()
        agg_row = _make_agg_row(
            cnt=2,
            auto_loop_count=1,
            weight_sum=Decimal("2.0"),
            score=Decimal("75.00"),
        )
        agg_result = MagicMock()
        agg_result.one.return_value = agg_row
        db.execute = AsyncMock(return_value=agg_result)

        with (
            patch(
                "app.services.node_performance.collect_descendant_loop_ids",
                return_value=["loop-001", "loop-002"],
            ),
            patch(
                "app.services.node_performance.query_realtime_auto_rate",
                return_value=None,
            ),
        ):
            result = await aggregate_node_snapshot(
                db,
                "node-002",
                datetime.now(UTC).replace(tzinfo=None),
                datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
            )

        assert result is not None
        assert result["plant_node_id"] == "node-002"
        assert result["loop_count"] == 2
        assert result["score"] == Decimal("75.00")
        assert result["auto_loop_ratio"] == Decimal("50.00")  # 1/2*100
        assert result["realtime_auto_rate"] is None
        assert result["status"] == "FAIR"  # score=75 → FAIR


# ===========================================================================
# TEST-04: 实时自控率读投用定义
# ===========================================================================


def _mock_redis_subscriber(cached: list[dict] | None = None) -> MagicMock:
    """构造 realtime subscriber mock，get_cached_values 返回指定缓存列表。"""
    subscriber = MagicMock()
    subscriber.get_cached_values = AsyncMock(return_value=cached or [])
    return subscriber


class TestRealtimeAutoRate:
    """实时自控率读投用定义测试。

    验证 query_realtime_auto_rate 在有/无投用定义时的行为。
    使用 mock_db（PG 回退路径）+ mock Redis 订阅器（空缓存）。
    """

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_with_config(self) -> None:
        """有投用定义时按配置判断。

        loop-001 配置自动 MODE={1,2}，loop-002 无配置回退默认 {1,2,3}。
        TAG_001 current_value=1（在 {1,2} → 自动），
        TAG_002 current_value=4（不在 {1,2,3} → 非自动）。
        期望：1/2 = 50.0%
        """
        db = AsyncMock()
        # 1st execute: 投用定义查询（loop-001 有配置）
        mm_rows = [
            MagicMock(loop_id="loop-001", mode_value=1),
            MagicMock(loop_id="loop-001", mode_value=2),
        ]
        # 2nd execute: MODE tag 映射查询
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        # 3rd execute: Redis 缓存缺失，回退 tag_registry.current_value
        current_rows = [
            MagicMock(tag_name="TAG_001", current_value=1),
            MagicMock(tag_name="TAG_002", current_value=4),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_rows_mock(mm_rows),
                _make_rows_mock(tag_rows),
                _make_rows_mock(current_rows),
            ]
        )

        with patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=_mock_redis_subscriber(),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("50.00")
        assert result["auto_count"] == 1
        assert result["manual_count"] == 1
        assert result["total_count"] == 2

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_no_config(self) -> None:
        """无配置时回退 {1,2,3}。

        两个回路均无投用定义，回退默认 {1,2,3}。
        TAG_001 current_value=1（在 {1,2,3} → 自动），TAG_002 current_value=2（在 {1,2,3} → 自动）。
        期望：2/2 = 100.0%
        """
        db = AsyncMock()
        # 1st execute: 投用定义查询（空，无配置）
        # 2nd execute: MODE tag 映射查询
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        # 3rd execute: Redis 缓存缺失，回退 tag_registry.current_value
        current_rows = [
            MagicMock(tag_name="TAG_001", current_value=1),
            MagicMock(tag_name="TAG_002", current_value=2),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
                _make_rows_mock(current_rows),
            ]
        )

        with patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=_mock_redis_subscriber(),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("100.00")
        assert result["auto_count"] == 2
        assert result["total_count"] == 2

    @pytest.mark.asyncio
    async def test_realtime_auto_rate_no_loops(self) -> None:
        """空回路列表返回 None。"""
        db = AsyncMock()

        result = await query_realtime_auto_rate(db, [])

        assert result is None
        # 空列表时应立即返回，不查询 DB
        db.execute.assert_not_called()


class TestRealtimeAutoRateRedisSource:
    """实时自控率 Redis 优先数据源测试。

    回归背景：原实现只读 PG tag_registry.current_value（仅 AAS 同步写入，
    AAS_SYNC_ENABLED=False 时数据永久过期），与 SignalR 实时订阅维护的
    Redis 实时缓存（realtime:{tagCode}）脱节，导致回路状态统计/实时自控率
    不随真实 MODE 变化。修复后：Redis 优先，缺失回退 PG。
    """

    @pytest.mark.asyncio
    async def test_redis_hit_takes_priority_and_skips_pg(self) -> None:
        """Redis 全命中时直接使用 Redis 值，不再查询 PG current_value。

        Redis: TAG_001=1（自动）、TAG_002=0（手动）。
        期望：1/2 = 50.0%，且 db.execute 只调用 2 次（投用定义 + tag 映射）。
        """
        db = AsyncMock()
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
            ]
        )
        cached = [
            {"tagCode": "TAG_001", "value": "1", "quality": 1},
            {"tagCode": "TAG_002", "value": "0", "quality": 1},
        ]

        with patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=_mock_redis_subscriber(cached),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("50.00")
        assert result["auto_count"] == 1
        assert result["manual_count"] == 1
        assert result["total_count"] == 2
        assert result["mode_counts"] == {0: 1, 1: 1, 2: 0, 3: 0, 4: 0}
        # Redis 全命中：仅 2 次 DB 查询（投用定义 + tag 映射），无 current_value 回退查询
        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_redis_partial_hit_falls_back_to_pg(self) -> None:
        """Redis 部分命中时，缺失的 tag 回退 PG current_value。

        Redis: TAG_001=1；PG: TAG_002=2。两者都计入统计。
        期望：2/2 = 100.0%（默认 {1,2,3}）。
        """
        db = AsyncMock()
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        current_rows = [MagicMock(tag_name="TAG_002", current_value=2)]
        db.execute = AsyncMock(
            side_effect=[
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
                _make_rows_mock(current_rows),
            ]
        )
        cached = [{"tagCode": "TAG_001", "value": "1", "quality": 1}]

        with patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=_mock_redis_subscriber(cached),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("100.00")
        assert result["auto_count"] == 2
        assert result["total_count"] == 2
        assert db.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_pg(self) -> None:
        """Redis 读取异常时回退 PG，不中断统计。"""
        db = AsyncMock()
        tag_rows = [
            MagicMock(loop_id="loop-001", tag_name="TAG_001"),
            MagicMock(loop_id="loop-002", tag_name="TAG_002"),
        ]
        current_rows = [
            MagicMock(tag_name="TAG_001", current_value=1),
            MagicMock(tag_name="TAG_002", current_value=2),
        ]
        db.execute = AsyncMock(
            side_effect=[
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
                _make_rows_mock(current_rows),
            ]
        )
        subscriber = MagicMock()
        subscriber.get_cached_values = AsyncMock(side_effect=ConnectionError("redis down"))

        with patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=subscriber,
        ):
            result = await query_realtime_auto_rate(db, ["loop-001", "loop-002"])

        assert result is not None
        assert result["rate"] == Decimal("100.00")
        assert result["auto_count"] == 2

    @pytest.mark.asyncio
    async def test_redis_string_float_value_parsed(self) -> None:
        """Redis 字符串值如 "1.0" 应解析为 MODE 1（自动）。"""
        db = AsyncMock()
        tag_rows = [MagicMock(loop_id="loop-001", tag_name="TAG_001")]
        db.execute = AsyncMock(
            side_effect=[
                _make_rows_mock([]),
                _make_rows_mock(tag_rows),
            ]
        )
        cached = [{"tagCode": "TAG_001", "value": "1.0", "quality": 1}]

        with patch(
            "app.services.data_source.realtime_subscriber.get_subscriber",
            return_value=_mock_redis_subscriber(cached),
        ):
            result = await query_realtime_auto_rate(db, ["loop-001"])

        assert result is not None
        assert result["auto_count"] == 1
        assert result["mode_counts"][1] == 1
