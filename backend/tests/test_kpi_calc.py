"""KPI 计算 Celery 任务模块测试。

测试覆盖：
- 纯函数：_get_tag_name / _ts_to_float / _build_ts_index / _find_nearest_value / _quantize
- _save_snapshot：幂等写入（新增/更新/不同 status）
- _calculate_loop_kpi：核心计算（缺 PV/查询失败/数据不足/正常/PARTIAL）
- _do_calculate / _do_calculate_single_loop：mock AsyncSessionLocal
- Celery 任务入口：calculate_hourly_kpi / calculate_loop_kpi
- _compute_composite_score 边界场景
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.kpi_calc import (
    ALGORITHM_VERSION,
    _build_ts_index,
    _calculate_loop_kpi,
    _compute_composite_score,
    _do_calculate,
    _do_calculate_single_loop,
    _find_nearest_value,
    _get_tag_name,
    _quantize,
    _save_snapshot,
    _ts_to_float,
    calculate_hourly_kpi,
    calculate_loop_kpi,
)

# ===========================================================================
# 辅助函数：构造 mock 对象
# ===========================================================================


def _make_metric_config(
    metric_code: str = "good_value_rate",
    weight: Decimal | None = Decimal("20"),
    is_enabled: bool = True,
) -> MagicMock:
    """构造 MetricConfig mock。"""
    c = MagicMock()
    c.metric_code = metric_code
    c.weight = weight
    c.is_enabled = is_enabled
    return c


def _make_loop(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    tag_name: str = "101-FC-1023",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.is_active = True
    loop.status = "READY"
    loop.unit_id = "00000000-0000-0000-0000-000000000111"
    return loop


def _make_mapping(tag_id: str, tag_role: str) -> MagicMock:
    """构造 LoopTagMapping mock。"""
    m = MagicMock()
    m.id = f"mapping-{tag_role}"
    m.loop_id = "00000000-0000-0000-0000-000000000201"
    m.tag_id = tag_id
    m.tag_role = tag_role
    m.is_required = True
    return m


def _make_tag(tag_id: str, tag_name: str) -> MagicMock:
    """构造 TagRegistry mock。"""
    t = MagicMock()
    t.id = tag_id
    t.tag_name = tag_name
    t.tag_type = "PV"
    return t


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


def _make_trend_point(ts: object, value: float, quality: str = "GOOD") -> dict:
    """构造 TDengine 时序数据点。"""
    return {"ts": ts, "value": value, "quality": quality}


def _make_full_metric_configs() -> dict[str, MagicMock]:
    """构造完整的 8 大 KPI 指标配置（对齐国标 4 分项评分公式）。

    参与评分的 4 指标（weight > 0）：
        accuracy_rate(30) + fast_response_rate(20) + steady_rate(30) + effective_auto_rate(20) = 100
    仅显示的指标（weight = 0）：好值率/自控率/振荡率/饱和率
    """
    return {
        "good_value_rate": _make_metric_config("good_value_rate", Decimal("0")),
        "auto_mode_rate": _make_metric_config("auto_mode_rate", Decimal("0")),
        "effective_auto_rate": _make_metric_config("effective_auto_rate", Decimal("20")),
        "steady_rate": _make_metric_config("steady_rate", Decimal("30")),
        "accuracy_rate": _make_metric_config("accuracy_rate", Decimal("30")),
        "fast_response_rate": _make_metric_config("fast_response_rate", Decimal("20")),
        "oscillation_rate": _make_metric_config("oscillation_rate", Decimal("0")),
        "saturation_rate": _make_metric_config("saturation_rate", Decimal("0")),
    }


# ===========================================================================
# 1. 纯函数测试
# ===========================================================================


class TestGetTagName:
    """测试 _get_tag_name()。"""

    def test_existing_role(self) -> None:
        """角色存在且 tag 存在时返回 tag_name。"""
        mappings = {"PV": _make_mapping("tag-1", "PV")}
        tags_map = {"tag-1": _make_tag("tag-1", "PV_TAG")}
        assert _get_tag_name(mappings, tags_map, "PV") == "PV_TAG"

    def test_missing_role(self) -> None:
        """角色不存在返回 None。"""
        tags_map = {"tag-1": _make_tag("tag-1", "PV_TAG")}
        assert _get_tag_name({}, tags_map, "PV") is None

    def test_missing_tag(self) -> None:
        """角色存在但 tag 不在 tags_map 中返回 None。"""
        mappings = {"PV": _make_mapping("tag-1", "PV")}
        assert _get_tag_name(mappings, {}, "PV") is None

    def test_empty_mappings_and_tags(self) -> None:
        """空 mappings 和空 tags_map 返回 None。"""
        assert _get_tag_name({}, {}, "PV") is None


class TestTsToFloat:
    """测试 _ts_to_float()。"""

    def test_none(self) -> None:
        """None 输入返回 None。"""
        assert _ts_to_float(None) is None

    def test_int(self) -> None:
        """整数输入返回浮点数。"""
        assert _ts_to_float(1000) == 1000.0

    def test_float(self) -> None:
        """浮点数输入返回浮点数。"""
        assert _ts_to_float(1000.5) == 1000.5

    def test_datetime(self) -> None:
        """datetime 输入返回 epoch 秒。"""
        dt = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        assert _ts_to_float(dt) == dt.timestamp()

    def test_iso_string_with_z(self) -> None:
        """带 Z 后缀的 ISO 字符串能解析为 epoch 秒。"""
        s = "2026-06-22T08:00:00Z"
        expected = datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        assert _ts_to_float(s) == expected

    def test_numeric_string(self) -> None:
        """数值字符串输入返回浮点数。"""
        assert _ts_to_float("1000.5") == 1000.5

    def test_invalid_string(self) -> None:
        """无效字符串返回 None。"""
        assert _ts_to_float("not-a-timestamp") is None


class TestBuildTsIndex:
    """测试 _build_ts_index()。"""

    def test_empty_data(self) -> None:
        """空数据返回空列表。"""
        ts_floats, ts_orig = _build_ts_index([])
        assert ts_floats == []
        assert ts_orig == []

    def test_numeric_ts_sorted(self) -> None:
        """数值时间戳构建索引并排序。"""
        data = [
            _make_trend_point(1000.0, 10.0),
            _make_trend_point(999.0, 20.0),
        ]
        ts_floats, ts_orig = _build_ts_index(data)
        assert ts_floats == [999.0, 1000.0]
        assert ts_orig == [999.0, 1000.0]

    def test_string_ts(self) -> None:
        """ISO 字符串时间戳构建索引。"""
        data = [
            _make_trend_point("2026-06-22T08:00:01Z", 10.0),
            _make_trend_point("2026-06-22T08:00:00Z", 20.0),
        ]
        ts_floats, ts_orig = _build_ts_index(data)
        assert len(ts_floats) == 2
        # 排序后第一个是 08:00:00
        assert ts_orig[0] == "2026-06-22T08:00:00Z"

    def test_unconvertible_ts_returns_empty(self) -> None:
        """任意 ts 无法转数值时返回空列表（退化为精确匹配）。"""
        data = [
            _make_trend_point("t1", 10.0),
            _make_trend_point("t2", 20.0),
        ]
        ts_floats, ts_orig = _build_ts_index(data)
        assert ts_floats == []
        assert ts_orig == []


class TestFindNearestValue:
    """测试 _find_nearest_value()。"""

    def test_exact_match(self) -> None:
        """精确匹配返回对应值。"""
        exact_map = {"t1": 11.0}
        assert _find_nearest_value("t1", [], exact_map, None) == 11.0

    def test_tolerance_match(self) -> None:
        """容差范围内（<500ms）匹配最近邻。"""
        sorted_ts = [1000.0, 2000.0]
        sorted_values = [11.0, 21.0]
        # 偏差 200ms，匹配第一个
        result = _find_nearest_value(1000.2, sorted_ts, {}, sorted_values)
        assert result == 11.0

    def test_out_of_tolerance(self) -> None:
        """超出容差范围（>500ms）返回 None。"""
        sorted_ts = [1000.0]
        sorted_values = [11.0]
        # 偏差 600ms
        assert _find_nearest_value(1000.6, sorted_ts, {}, sorted_values) is None

    def test_empty_sorted_ts(self) -> None:
        """空 sorted_ts_floats 返回 None。"""
        assert _find_nearest_value(1000.0, [], {}, None) is None

    def test_none_sorted_values(self) -> None:
        """sorted_values 为 None 返回 None。"""
        assert _find_nearest_value(1000.0, [1000.0], {}, None) is None

    def test_unconvertible_target(self) -> None:
        """target_ts 无法转数值返回 None。"""
        assert _find_nearest_value("invalid", [1000.0], {}, [11.0]) is None


class TestQuantize:
    """测试 _quantize()。"""

    def test_quantize_two_decimals(self) -> None:
        """量化到 2 位小数（四舍五入）。"""
        assert _quantize(Decimal("78.567")) == Decimal("78.57")

    def test_quantize_integer(self) -> None:
        """整数也量化到 2 位小数。"""
        assert _quantize(Decimal("100")) == Decimal("100.00")

    def test_quantize_negative(self) -> None:
        """负数量化。"""
        assert _quantize(Decimal("-3.141")) == Decimal("-3.14")


# ===========================================================================
# 2. _save_snapshot 集成测试
# ===========================================================================


class TestSaveSnapshot:
    """测试 _save_snapshot() 幂等写入。"""

    @pytest.mark.asyncio
    async def test_new_snapshot_calls_add(self) -> None:
        """新增快照（existing=None）调用 db.add，返回正确字典。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)

        result = await _save_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=ts_start,
            ts_end=ts_end,
            status="SUCCESS",
            score=Decimal("78.50"),
            good_value_rate=Decimal("96.80"),
        )

        db.add.assert_called_once()
        assert result["loopId"] == "loop-1"
        assert result["status"] == "SUCCESS"
        assert result["score"] == 78.5
        assert result["algorithmVersion"] == ALGORITHM_VERSION
        assert result["tsStart"] == ts_start.isoformat()
        assert result["tsEnd"] == ts_end.isoformat()

    @pytest.mark.asyncio
    async def test_update_existing_snapshot_no_add(self) -> None:
        """更新已有快照（existing 存在）不调用 db.add，更新字段。"""
        existing = MagicMock()
        existing.id = "existing-snapshot-id"
        existing.ts_end = None
        existing.status = None
        existing.score = None
        existing.good_value_rate = None
        existing.auto_mode_rate = None
        existing.steady_rate = None
        existing.accuracy_rate = None
        existing.oscillation_rate = None
        existing.saturation_rate = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(existing))
        db.add = MagicMock()

        ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)

        result = await _save_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )

        db.add.assert_not_called()
        # 字段被更新
        assert existing.ts_end == ts_end
        assert existing.status == "INCONCLUSIVE"
        assert result["snapshotId"] == "existing-snapshot-id"
        assert result["status"] == "INCONCLUSIVE"
        assert result["score"] is None

    @pytest.mark.asyncio
    async def test_partial_status(self) -> None:
        """PARTIAL 状态写入。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)

        result = await _save_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=ts_start,
            ts_end=ts_end,
            status="PARTIAL",
            score=Decimal("50.00"),
            steady_rate=Decimal("80.00"),
        )

        assert result["status"] == "PARTIAL"
        assert result["score"] == 50.0


# ===========================================================================
# 3. _calculate_loop_kpi 集成测试
# ===========================================================================


class TestCalculateLoopKpi:
    """测试 _calculate_loop_kpi() 核心计算逻辑。"""

    @pytest.mark.asyncio
    async def test_missing_pv_tag_returns_inconclusive(self) -> None:
        """缺少 PV Tag 返回 INCONCLUSIVE。"""
        loop = _make_loop()
        # 只有 SP mapping，没有 PV
        mappings = [_make_mapping("tag-sp", "SP")]
        tags = [_make_tag("tag-sp", "SP_TAG")]

        db = AsyncMock()
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(mappings)
            if call_count[0] == 2:
                return _make_scalars_mock(tags)
            # _save_snapshot 查询 existing
            return _make_scalar_one_or_none_mock(None)

        db.execute = AsyncMock(side_effect=execute_side_effect)
        db.add = MagicMock()

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs={},
            ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
            query_trend_fn=AsyncMock(),
        )

        assert result["status"] == "INCONCLUSIVE"

    @pytest.mark.asyncio
    async def test_tdengine_failure_returns_inconclusive(self) -> None:
        """TDengine 查询失败返回 INCONCLUSIVE。"""
        loop = _make_loop()
        mappings = [
            _make_mapping("tag-pv", "PV"),
            _make_mapping("tag-sp", "SP"),
        ]
        tags = [
            _make_tag("tag-pv", "PV_TAG"),
            _make_tag("tag-sp", "SP_TAG"),
        ]

        db = AsyncMock()
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(mappings)
            if call_count[0] == 2:
                return _make_scalars_mock(tags)
            return _make_scalar_one_or_none_mock(None)

        db.execute = AsyncMock(side_effect=execute_side_effect)
        db.add = MagicMock()

        async def query_fail(tag_name, start, end):
            raise RuntimeError("TDengine connection failed")

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs={},
            ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
            query_trend_fn=query_fail,
        )

        assert result["status"] == "INCONCLUSIVE"

    @pytest.mark.asyncio
    async def test_insufficient_data_returns_inconclusive(self) -> None:
        """Good 数据占比 < 20% 返回 INCONCLUSIVE。"""
        loop = _make_loop()
        mappings = [
            _make_mapping("tag-pv", "PV"),
            _make_mapping("tag-sp", "SP"),
        ]
        tags = [
            _make_tag("tag-pv", "PV_TAG"),
            _make_tag("tag-sp", "SP_TAG"),
        ]

        # 10 个数据点，9 个 BAD，1 个 GOOD → 10% < 20%
        pv_data = [
            _make_trend_point(1000.0 + i, 50.0, quality="BAD") for i in range(9)
        ] + [_make_trend_point(1009.0, 50.0, quality="GOOD")]

        db = AsyncMock()
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(mappings)
            if call_count[0] == 2:
                return _make_scalars_mock(tags)
            return _make_scalar_one_or_none_mock(None)

        db.execute = AsyncMock(side_effect=execute_side_effect)
        db.add = MagicMock()

        async def query_side_effect(tag_name, start, end):
            if tag_name == "PV_TAG":
                return pv_data
            return []

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs={},
            ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
            query_trend_fn=query_side_effect,
        )

        assert result["status"] == "INCONCLUSIVE"

    @pytest.mark.asyncio
    async def test_normal_calculation_returns_success(self) -> None:
        """正常计算流程返回 SUCCESS + KPI 值。"""
        loop = _make_loop()
        mappings = [
            _make_mapping("tag-pv", "PV"),
            _make_mapping("tag-sp", "SP"),
            _make_mapping("tag-op", "OP"),
            _make_mapping("tag-mode", "MODE"),
        ]
        tags = [
            _make_tag("tag-pv", "PV_TAG"),
            _make_tag("tag-sp", "SP_TAG"),
            _make_tag("tag-op", "OP_TAG"),
            _make_tag("tag-mode", "MODE_TAG"),
        ]

        # 10 个数据点，全部 GOOD，PV=SP=50，mode=Auto，op=50
        pv_data = [_make_trend_point(1000.0 + i, 50.0) for i in range(10)]
        sp_data = [_make_trend_point(1000.0 + i, 50.0) for i in range(10)]
        op_data = [_make_trend_point(1000.0 + i, 50.0) for i in range(10)]
        mode_data = [_make_trend_point(1000.0 + i, 1) for i in range(10)]

        db = AsyncMock()
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(mappings)
            if call_count[0] == 2:
                return _make_scalars_mock(tags)
            return _make_scalar_one_or_none_mock(None)

        db.execute = AsyncMock(side_effect=execute_side_effect)
        db.add = MagicMock()

        async def query_side_effect(tag_name, start, end):
            if tag_name == "PV_TAG":
                return pv_data
            if tag_name == "SP_TAG":
                return sp_data
            if tag_name == "OP_TAG":
                return op_data
            if tag_name == "MODE_TAG":
                return mode_data
            return []

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs=_make_full_metric_configs(),
            ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
            query_trend_fn=query_side_effect,
        )

        assert result["status"] == "SUCCESS"
        assert result["score"] == 100.0

    @pytest.mark.asyncio
    async def test_partial_when_steady_rate_none(self) -> None:
        """SP 数据为空导致 steady_rate=None 时返回 PARTIAL。"""
        loop = _make_loop()
        mappings = [
            _make_mapping("tag-pv", "PV"),
            _make_mapping("tag-sp", "SP"),
        ]
        tags = [
            _make_tag("tag-pv", "PV_TAG"),
            _make_tag("tag-sp", "SP_TAG"),
        ]

        # PV 有数据，SP 无数据 → steady_rate=None → PARTIAL
        pv_data = [_make_trend_point(1000.0 + i, 50.0) for i in range(10)]

        db = AsyncMock()
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalars_mock(mappings)
            if call_count[0] == 2:
                return _make_scalars_mock(tags)
            return _make_scalar_one_or_none_mock(None)

        db.execute = AsyncMock(side_effect=execute_side_effect)
        db.add = MagicMock()

        async def query_side_effect(tag_name, start, end):
            if tag_name == "PV_TAG":
                return pv_data
            return []

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs=_make_full_metric_configs(),
            ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
            query_trend_fn=query_side_effect,
        )

        assert result["status"] == "PARTIAL"


# ===========================================================================
# 4. _do_calculate 集成测试
# ===========================================================================


class TestDoCalculate:
    """测试 _do_calculate() 全量计算编排。"""

    @pytest.mark.asyncio
    async def test_no_loops_returns_empty(self) -> None:
        """无待计算回路时返回全零结果。"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_make_scalars_mock([]))

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _do_calculate()

        assert result == {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}

    @pytest.mark.asyncio
    async def test_with_loops_counts_success(self) -> None:
        """有回路且计算成功时正确计数。"""
        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch("app.core.tdengine.query_trend_data"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"status": "SUCCESS", "loopId": str(loop.id)}

            result = await _do_calculate()

        assert result["total"] == 1
        assert result["success"] == 1
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_with_loops_counts_inconclusive(self) -> None:
        """回路计算返回 INCONCLUSIVE 时正确计数。"""
        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch("app.core.tdengine.query_trend_data"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"status": "INCONCLUSIVE", "loopId": str(loop.id)}

            result = await _do_calculate()

        assert result["total"] == 1
        assert result["inconclusive"] == 1

    @pytest.mark.asyncio
    async def test_with_loops_counts_failed_on_exception(self) -> None:
        """回路计算抛异常时计入 failed。"""
        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch("app.core.tdengine.query_trend_data"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.side_effect = RuntimeError("calc failed")

            result = await _do_calculate()

        assert result["total"] == 1
        assert result["failed"] == 1


# ===========================================================================
# 5. _do_calculate_single_loop 集成测试
# ===========================================================================


class TestDoCalculateSingleLoop:
    """测试 _do_calculate_single_loop() 单回路计算。"""

    @pytest.mark.asyncio
    async def test_loop_not_found_returns_failed(self) -> None:
        """回路不存在返回 FAILED。"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_make_scalar_one_or_none_mock(None)
        )

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await _do_calculate_single_loop("nonexistent", None)

        assert result["status"] == "FAILED"
        assert result["error"] == "回路不存在"

    @pytest.mark.asyncio
    async def test_normal_calculation(self) -> None:
        """正常计算流程。"""
        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch("app.core.tdengine.query_trend_data"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"loopId": str(loop.id), "status": "SUCCESS"}

            result = await _do_calculate_single_loop(str(loop.id), None)

        assert result["status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_ts_start_with_z_suffix(self) -> None:
        """ts_start 带 Z 后缀时正确解析为 UTC datetime。"""
        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch("app.core.tdengine.query_trend_data"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"loopId": str(loop.id), "status": "SUCCESS"}

            await _do_calculate_single_loop(str(loop.id), "2026-06-22T08:00:00Z")

        # 验证 ts_start 被正确解析并传递给 _calculate_loop_kpi
        call_kwargs = mock_calc.call_args.kwargs
        assert call_kwargs["ts_start"] == datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_ts_start_without_z(self) -> None:
        """ts_start 不带 Z 时也能解析。"""
        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch("app.core.tdengine.query_trend_data"),
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"loopId": str(loop.id), "status": "SUCCESS"}

            await _do_calculate_single_loop(str(loop.id), "2026-06-22T08:00:00")

        call_kwargs = mock_calc.call_args.kwargs
        assert call_kwargs["ts_start"] == datetime(2026, 6, 22, 8, 0, 0)


# ===========================================================================
# 6. Celery 任务入口测试
# ===========================================================================


class TestCeleryTasks:
    """测试 Celery 任务入口函数。"""

    def test_calculate_hourly_kpi_success(self) -> None:
        """calculate_hourly_kpi 正常执行返回结果。"""
        expected = {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}
        with patch(
            "app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock
        ) as mock_calc:
            mock_calc.return_value = expected
            result = calculate_hourly_kpi.run()
            assert result == expected

    def test_calculate_hourly_kpi_exception_reraises(self) -> None:
        """calculate_hourly_kpi 异常时重新抛出。"""
        with patch(
            "app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock
        ) as mock_calc:
            mock_calc.side_effect = RuntimeError("DB down")
            with pytest.raises(RuntimeError, match="DB down"):
                calculate_hourly_kpi.run()

    def test_calculate_loop_kpi_task(self) -> None:
        """calculate_loop_kpi 任务正常执行。"""
        expected = {"loopId": "loop-1", "status": "SUCCESS"}
        with patch(
            "app.tasks.kpi_calc._do_calculate_single_loop", new_callable=AsyncMock
        ) as mock_fn:
            mock_fn.return_value = expected
            result = calculate_loop_kpi.run("loop-1", None)
            assert result == expected


# ===========================================================================
# 7. _compute_composite_score 边界场景
# ===========================================================================


class TestComputeCompositeScoreEdgeCases:
    """测试 _compute_composite_score() 边界场景（国标 4 分项加法公式）。"""

    def test_all_four_metrics_full_score(self) -> None:
        """4 分项指标全部 100 时，综合评分 = 100。"""
        configs = {
            "accuracy_rate": _make_metric_config(
                metric_code="accuracy_rate", weight=Decimal("30")
            ),
            "fast_response_rate": _make_metric_config(
                metric_code="fast_response_rate", weight=Decimal("20")
            ),
            "steady_rate": _make_metric_config(
                metric_code="steady_rate", weight=Decimal("30")
            ),
            "effective_auto_rate": _make_metric_config(
                metric_code="effective_auto_rate", weight=Decimal("20")
            ),
        }
        kpi_values = {
            "accuracy_rate": Decimal("100"),
            "fast_response_rate": Decimal("100"),
            "steady_rate": Decimal("100"),
            "effective_auto_rate": Decimal("100"),
        }
        score = _compute_composite_score(kpi_values, configs)
        # P = (30*1 + 20*1 + 30*1 + 20*1) / 100 * 100 = 100
        assert score == Decimal("100.00")

    def test_none_value_skipped(self) -> None:
        """KPI 值为 None 的指标不参与评分（仅有效指标加权平均）。"""
        configs = {
            "accuracy_rate": _make_metric_config(
                metric_code="accuracy_rate", weight=Decimal("30")
            ),
            "fast_response_rate": _make_metric_config(
                metric_code="fast_response_rate", weight=Decimal("20")
            ),
            "steady_rate": _make_metric_config(
                metric_code="steady_rate", weight=Decimal("30")
            ),
            "effective_auto_rate": _make_metric_config(
                metric_code="effective_auto_rate", weight=Decimal("20")
            ),
        }
        kpi_values = {
            "accuracy_rate": Decimal("100"),
            "fast_response_rate": None,  # 跳过
            "steady_rate": Decimal("100"),
            "effective_auto_rate": Decimal("100"),
        }
        score = _compute_composite_score(kpi_values, configs)
        # P = (30*1 + 30*1 + 20*1) / (30+30+20) * 100 = 80/80 * 100 = 100
        assert score == Decimal("100.00")

    def test_none_weight_skipped(self) -> None:
        """weight 为 None 的指标不参与评分。"""
        configs = {
            "accuracy_rate": _make_metric_config(
                metric_code="accuracy_rate", weight=None
            ),
            "fast_response_rate": _make_metric_config(
                metric_code="fast_response_rate", weight=Decimal("20")
            ),
        }
        kpi_values = {
            "accuracy_rate": Decimal("100"),
            "fast_response_rate": Decimal("80"),
        }
        score = _compute_composite_score(kpi_values, configs)
        # 仅 fast_response_rate 参与：20 * 0.8 / 20 * 100 = 80
        assert score == Decimal("80.00")

    def test_missing_config_skipped(self) -> None:
        """metric_configs 中不存在的指标不参与评分。"""
        configs = {}
        kpi_values = {
            "accuracy_rate": Decimal("100"),
            "fast_response_rate": Decimal("100"),
        }
        score = _compute_composite_score(kpi_values, configs)
        # 所有权重总和为 0，返回 0
        assert score == Decimal("0.00")

    def test_weighted_average_calculation(self) -> None:
        """验证加权平均计算：P = (λA·A + λF·F + λS·S + λR·R) / (Σλ) × 100。"""
        configs = {
            "accuracy_rate": _make_metric_config(
                metric_code="accuracy_rate", weight=Decimal("30")
            ),
            "fast_response_rate": _make_metric_config(
                metric_code="fast_response_rate", weight=Decimal("20")
            ),
            "steady_rate": _make_metric_config(
                metric_code="steady_rate", weight=Decimal("30")
            ),
            "effective_auto_rate": _make_metric_config(
                metric_code="effective_auto_rate", weight=Decimal("20")
            ),
        }
        kpi_values = {
            "accuracy_rate": Decimal("90"),    # A = 90
            "fast_response_rate": Decimal("80"),  # F = 80
            "steady_rate": Decimal("70"),      # S = 70
            "effective_auto_rate": Decimal("60"),  # R = 60
        }
        score = _compute_composite_score(kpi_values, configs)
        # P = (30*0.9 + 20*0.8 + 30*0.7 + 20*0.6) / 100 * 100
        #   = (27 + 16 + 21 + 12) / 100 * 100 = 76.00
        assert score == Decimal("76.00")
