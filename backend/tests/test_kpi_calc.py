"""KPI 计算 Celery 任务模块测试（v4.0 三层架构）.

测试覆盖：
- 纯函数: _get_tag_name / _ts_to_float / _build_ts_index / _find_nearest_value / _quantize
- _save_snapshot: 幂等写入（新增/更新/不同 status）+ 7 个数据血缘字段写入
- _calculate_loop_kpi: DataPlanner 取数 + 三层计算编排（注入 mock data_planner）
    - DataPlanner 异常 → INCONCLUSIVE
    - DataPlanner 返回空 → INCONCLUSIVE
    - 综合评分为 None（R 可信度 E 级）→ INCONCLUSIVE
    - 必需指标齐全 → SUCCESS
    - 必需指标缺失 → PARTIAL
- _do_calculate / _do_calculate_single_loop: mock AsyncSessionLocal
- Celery 任务入口: calculate_hourly_kpi / calculate_loop_kpi
- v4.0 辅助函数: _loop_type_to_control_type / _build_config_bundle / _build_weights_map
- 三层计算流程: _compute_kpis_three_layer（mock 计算器 + mock ConfidenceEvaluator）
- 数据提取: _extract_kpi_values / _extract_lineage_info
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.contracts.data_types import (
    ControlType,
    DataBlock,
    DataLineage,
    MetricDataBundle,
    MetricResult,
    QualitySummary,
    TagGroup,
)
from app.tasks.kpi_calc import (
    _ALL_METRIC_CODES_DB,
    _CALCULATOR_TO_DB_METRIC_CODE,
    _DB_TO_CALCULATOR_METRIC_CODE,
    ALGORITHM_VERSION,
    _build_config_bundle,
    _build_ts_index,
    _build_weights_map,
    _calculate_loop_kpi,
    _compute_kpis_three_layer,
    _do_calculate,
    _do_calculate_single_loop,
    _extract_kpi_values,
    _extract_lineage_info,
    _find_nearest_value,
    _get_tag_name,
    _loop_type_to_control_type,
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
    loop_type: str = "FLOW",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.loop_type = loop_type
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


def _make_data_lineage(
    sampling_freq: str = "1s",
    quality_policy: str = "KEEP_ALL_WITH_VALIDITY",
    tag_group: str = TagGroup.BASE.value,
    valid_rate: float = 1.0,
    algorithm_version: str = ALGORITHM_VERSION,
) -> DataLineage:
    """构造测试用 DataLineage。"""
    return DataLineage(
        sampling_freq=sampling_freq,
        aggregation_policy="LAST",
        quality_policy=quality_policy,
        tag_group=tag_group,
        data_block_ids=["db_test_1s"],
        valid_rate=valid_rate,
        data_policy_version="pre_v1",
        algorithm_version=algorithm_version,
    )


def _make_metric_result(
    code: str,
    value: float | None,
    confidence: str = "A",
    lineage: DataLineage | None = None,
) -> MetricResult:
    """构造测试用 MetricResult。"""
    return MetricResult(
        metric_code=code,
        value=value,
        confidence_level=confidence,
        lineage=lineage or _make_data_lineage(),
        details={},
    )


def _make_bundle(
    metric_code: str,
    loop_id: str = "loop-1",
    tag_group: str = TagGroup.BASE.value,
    sampling_freq: str = "1s",
) -> MetricDataBundle:
    """构造测试用 MetricDataBundle（含最小化 DataBlock）。"""
    ts = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
    data_block = DataBlock(
        data_block_id=f"db_{loop_id}_{metric_code}_{sampling_freq}",
        loop_id=loop_id,
        tag_group=tag_group,
        sampling_freq=sampling_freq,
        timestamps=[ts],
        signals={"pv": [50.0], "sp": [50.0]},
        validity={"pv_valid": [True]},
        quality_summary=QualitySummary(total_count=1, valid_count=1, valid_rate=1.0),
        point_count=1,
    )
    return MetricDataBundle(
        metric_code=metric_code,
        data_block=data_block,
        mask_expression="pv_valid",
        masked_indices=[0],
        lineage=_make_data_lineage(sampling_freq=sampling_freq, tag_group=tag_group),
    )


def _make_full_metric_results(
    accuracy: float | None = 90.0,
    fast: float | None = 80.0,
    stability: float | None = 70.0,
    effective_auto: float | None = 60.0,
    good_value: float | None = 95.0,
    auto_mode: float | None = 88.0,
    oscillation: float | None = 15.0,
    saturation: float | None = 8.0,
    stiction: float | None = 0.5,
    output_trip: float | None = 12.0,
    settling: float | None = 45.0,
    ideal_settling: float | None = 30.0,
) -> dict[str, MetricResult]:
    """构造完整的 12 指标 MetricResult 字典（calculator_code 为键）。"""
    return {
        "accuracy_rate": _make_metric_result("accuracy_rate", accuracy),
        "fast_rate": _make_metric_result("fast_rate", fast),
        "stability_rate": _make_metric_result("stability_rate", stability),
        "effective_auto_rate": _make_metric_result("effective_auto_rate", effective_auto),
        "good_value_rate": _make_metric_result("good_value_rate", good_value),
        "auto_mode_rate": _make_metric_result("auto_mode_rate", auto_mode),
        "oscillation_rate": _make_metric_result("oscillation_rate", oscillation),
        "saturation_rate": _make_metric_result("saturation_rate", saturation),
        "stiction_index": _make_metric_result("stiction_index", stiction),
        "output_trip_index": _make_metric_result("output_trip_index", output_trip),
        "settling_time": _make_metric_result("settling_time", settling),
        "ideal_settling_time": _make_metric_result("ideal_settling_time", ideal_settling),
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
        # v4.0 血缘字段
        existing.ideal_settling_time = None
        existing.algorithm_version = None
        existing.sampling_freq = None
        existing.quality_policy = None
        existing.valid_rate = None
        existing.confidence_level = None
        existing.data_lineage = None

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
        # 字段被更新（_save_snapshot 剥离 tzinfo 适配 PG TIMESTAMP WITHOUT TIME ZONE）
        assert existing.ts_end == ts_end.replace(tzinfo=None)
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

    @pytest.mark.asyncio
    async def test_lineage_fields_written_on_new(self) -> None:
        """新增快照时 7 个数据血缘字段正确写入。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)

        lineage_dict = {
            "sampling_freq": "1s",
            "quality_policy": "KEEP_ALL_WITH_VALIDITY",
            "valid_rate": 0.95,
        }

        await _save_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=ts_start,
            ts_end=ts_end,
            status="SUCCESS",
            score=Decimal("76.00"),
            ideal_settling_time=Decimal("30.0"),
            algorithm_version="KPI_CALC_v2.0",
            sampling_freq="1s",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            valid_rate=Decimal("0.9500"),
            confidence_level="A",
            data_lineage=lineage_dict,
        )

        # 验证 db.add 被调用，且传入的 KpiSnapshotHourly 对象含血缘字段
        db.add.assert_called_once()
        snapshot = db.add.call_args[0][0]
        assert snapshot.ideal_settling_time == Decimal("30.0")
        assert snapshot.algorithm_version == "KPI_CALC_v2.0"
        assert snapshot.sampling_freq == "1s"
        assert snapshot.quality_policy == "KEEP_ALL_WITH_VALIDITY"
        assert snapshot.valid_rate == Decimal("0.9500")
        assert snapshot.confidence_level == "A"
        assert snapshot.data_lineage == lineage_dict

    @pytest.mark.asyncio
    async def test_lineage_fields_updated_on_existing(self) -> None:
        """更新已有快照时 7 个数据血缘字段被正确更新。"""
        existing = MagicMock()
        existing.id = "snap-1"
        # 所有字段初始为 None
        for attr in (
            "ts_end",
            "status",
            "score",
            "good_value_rate",
            "auto_mode_rate",
            "effective_auto_rate",
            "steady_rate",
            "accuracy_rate",
            "fast_response_rate",
            "oscillation_rate",
            "saturation_rate",
            "stiction_coeff",
            "steady_state_time",
            "output_travel_index",
            "ideal_settling_time",
            "algorithm_version",
            "sampling_freq",
            "quality_policy",
            "valid_rate",
            "confidence_level",
            "data_lineage",
        ):
            setattr(existing, attr, None)

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(existing))
        db.add = MagicMock()

        ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)

        await _save_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=ts_start,
            ts_end=ts_end,
            status="SUCCESS",
            score=Decimal("80.00"),
            ideal_settling_time=Decimal("60.0"),
            algorithm_version="KPI_CALC_v2.0",
            sampling_freq="5s",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            valid_rate=Decimal("0.8800"),
            confidence_level="B",
            data_lineage={"key": "value"},
        )

        db.add.assert_not_called()
        assert existing.ideal_settling_time == Decimal("60.0")
        assert existing.algorithm_version == "KPI_CALC_v2.0"
        assert existing.sampling_freq == "5s"
        assert existing.quality_policy == "KEEP_ALL_WITH_VALIDITY"
        assert existing.valid_rate == Decimal("0.8800")
        assert existing.confidence_level == "B"
        assert existing.data_lineage == {"key": "value"}


# ===========================================================================
# 3. _calculate_loop_kpi 集成测试（v4.0 DataPlanner + 三层架构）
# ===========================================================================


class TestCalculateLoopKpi:
    """测试 _calculate_loop_kpi() 核心计算逻辑。

    v4.0 三层架构：
        DataPlanner.request_bundles → _compute_kpis_three_layer → _save_snapshot

    通过注入 mock data_planner + patch _compute_kpis_three_layer 隔离计算器依赖。
    """

    @pytest.mark.asyncio
    async def test_dataplanner_exception_returns_inconclusive(self) -> None:
        """DataPlanner 取数异常时返回 INCONCLUSIVE。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        mock_planner = AsyncMock()
        mock_planner.request_bundles = AsyncMock(
            side_effect=RuntimeError("TDengine connection failed")
        )

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs={},
            ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
            data_planner=mock_planner,
        )

        assert result["status"] == "INCONCLUSIVE"
        assert result["score"] is None

    @pytest.mark.asyncio
    async def test_empty_bundles_returns_inconclusive(self) -> None:
        """DataPlanner 返回空 Bundle 列表时返回 INCONCLUSIVE。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        mock_planner = AsyncMock()
        mock_planner.request_bundles = AsyncMock(return_value=[])

        result = await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs={},
            ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
            data_planner=mock_planner,
        )

        assert result["status"] == "INCONCLUSIVE"

    @pytest.mark.asyncio
    async def test_composite_score_none_returns_inconclusive(self) -> None:
        """综合评分为 None（R 可信度 E 级）时返回 INCONCLUSIVE。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        mock_planner = AsyncMock()
        mock_planner.request_bundles = AsyncMock(return_value=[_make_bundle("accuracy_rate")])

        # mock 三层计算：返回有效指标但综合评分为 None（E 级）
        metric_results = _make_full_metric_results(effective_auto=None)
        composite_result = MetricResult(
            metric_code="composite_score",
            value=None,
            confidence_level="E",
            lineage=_make_data_lineage(),
        )

        with patch(
            "app.tasks.kpi_calc._compute_kpis_three_layer",
            return_value=(metric_results, composite_result),
        ):
            result = await _calculate_loop_kpi(
                db=db,
                loop=loop,
                metric_configs={},
                ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
                ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
                data_planner=mock_planner,
            )

        assert result["status"] == "INCONCLUSIVE"
        assert result["score"] is None

    @pytest.mark.asyncio
    async def test_normal_calculation_returns_success(self) -> None:
        """正常计算流程：必需指标齐全 + 综合评分有值 → SUCCESS。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        mock_planner = AsyncMock()
        mock_planner.request_bundles = AsyncMock(return_value=[_make_bundle("accuracy_rate")])

        metric_results = _make_full_metric_results()
        composite_result = MetricResult(
            metric_code="composite_score",
            value=76.0,
            confidence_level="A",
            lineage=_make_data_lineage(),
        )

        with patch(
            "app.tasks.kpi_calc._compute_kpis_three_layer",
            return_value=(metric_results, composite_result),
        ):
            result = await _calculate_loop_kpi(
                db=db,
                loop=loop,
                metric_configs=_make_full_metric_configs(),
                ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
                ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
                data_planner=mock_planner,
            )

        assert result["status"] == "SUCCESS"
        assert result["score"] == 76.0

    @pytest.mark.asyncio
    async def test_partial_when_required_metric_none(self) -> None:
        """必需指标（good_value_rate/auto_mode_rate/steady_rate）为 None → PARTIAL。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        mock_planner = AsyncMock()
        mock_planner.request_bundles = AsyncMock(return_value=[_make_bundle("accuracy_rate")])

        # steady_rate (stability_rate) = None → PARTIAL
        metric_results = _make_full_metric_results(stability=None)
        composite_result = MetricResult(
            metric_code="composite_score",
            value=50.0,
            confidence_level="C",
            lineage=_make_data_lineage(),
        )

        with patch(
            "app.tasks.kpi_calc._compute_kpis_three_layer",
            return_value=(metric_results, composite_result),
        ):
            result = await _calculate_loop_kpi(
                db=db,
                loop=loop,
                metric_configs=_make_full_metric_configs(),
                ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
                ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
                data_planner=mock_planner,
            )

        assert result["status"] == "PARTIAL"

    @pytest.mark.asyncio
    async def test_dataplanner_called_with_correct_args(self) -> None:
        """DataPlanner.request_bundles 被正确调用（loop_id/metrics/time_window/control_type）。"""
        loop = _make_loop(loop_type="TEMPERATURE")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        mock_planner = AsyncMock()
        mock_planner.request_bundles = AsyncMock(return_value=[])

        ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC)

        await _calculate_loop_kpi(
            db=db,
            loop=loop,
            metric_configs={},
            ts_start=ts_start,
            ts_end=ts_end,
            data_planner=mock_planner,
        )

        mock_planner.request_bundles.assert_called_once()
        call_kwargs = mock_planner.request_bundles.call_args.kwargs
        assert call_kwargs["loop_id"] == str(loop.id)
        assert call_kwargs["metrics"] == _ALL_METRIC_CODES_DB
        assert call_kwargs["control_type"] == ControlType.TEMPERATURE


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
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
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
                _make_scalars_mock([]),  # loop_type_weight 查询（v2 算法）
            ]
        )
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new_callable=AsyncMock,
            ) as mock_calc,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
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
                _make_scalars_mock([]),
            ]
        )
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new_callable=AsyncMock,
            ) as mock_calc,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {
                "status": "INCONCLUSIVE",
                "loopId": str(loop.id),
            }

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
                _make_scalars_mock([]),
            ]
        )
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new_callable=AsyncMock,
            ) as mock_calc,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
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
        mock_session.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
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
                _make_scalars_mock([]),
            ]
        )
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new_callable=AsyncMock,
            ) as mock_calc,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
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
                _make_scalars_mock([]),
            ]
        )
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new_callable=AsyncMock,
            ) as mock_calc,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"loopId": str(loop.id), "status": "SUCCESS"}

            await _do_calculate_single_loop(str(loop.id), "2026-06-22T08:00:00Z")

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
                _make_scalars_mock([]),
            ]
        )
        mock_session.commit = AsyncMock()

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.tasks.kpi_calc._calculate_loop_kpi",
                new_callable=AsyncMock,
            ) as mock_calc,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
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
        with patch("app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock) as mock_calc:
            mock_calc.return_value = expected
            result = calculate_hourly_kpi.run()
            assert result == expected

    def test_calculate_hourly_kpi_exception_reraises(self) -> None:
        """calculate_hourly_kpi 异常时重新抛出。"""
        with patch("app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock) as mock_calc:
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
# 7. v4.0 辅助函数测试
# ===========================================================================


class TestLoopTypeToControlType:
    """测试 _loop_type_to_control_type() 映射。"""

    @pytest.mark.parametrize(
        "loop_type,expected",
        [
            ("FLOW", ControlType.FLOW),
            ("PRESSURE", ControlType.PRESSURE),
            ("TEMPERATURE", ControlType.TEMPERATURE),
            ("LEVEL", ControlType.LEVEL),
            ("ANALYSIS", ControlType.COMPOSITION),
            ("SPEED", ControlType.FLOW),  # SPEED 回退为 FLOW
            ("OTHER", ControlType.FLOW),  # OTHER 回退为 FLOW
        ],
    )
    def test_known_types(self, loop_type: str, expected: ControlType) -> None:
        """已知的 loop_type 正确映射为 ControlType。"""
        assert _loop_type_to_control_type(loop_type) == expected

    def test_none_returns_flow(self) -> None:
        """loop_type=None 回退为 FLOW。"""
        assert _loop_type_to_control_type(None) == ControlType.FLOW

    def test_unknown_returns_flow(self) -> None:
        """未知 loop_type 回退为 FLOW。"""
        assert _loop_type_to_control_type("UNKNOWN") == ControlType.FLOW


class TestBuildConfigBundle:
    """测试 _build_config_bundle() 虚拟 CONFIG bundle 构造。"""

    def test_returns_metric_data_bundle(self) -> None:
        """返回 MetricDataBundle 实例。"""
        bundle = _build_config_bundle("loop-1", ControlType.FLOW)
        assert isinstance(bundle, MetricDataBundle)

    def test_metric_code_is_ideal_settling_time(self) -> None:
        """metric_code 为 ideal_settling_time。"""
        bundle = _build_config_bundle("loop-1", ControlType.FLOW)
        assert bundle.metric_code == "ideal_settling_time"

    def test_tag_group_is_config(self) -> None:
        """data_block.tag_group 为 CONFIG。"""
        bundle = _build_config_bundle("loop-1", ControlType.FLOW)
        assert bundle.data_block.tag_group == TagGroup.CONFIG.value

    def test_signals_contain_control_type(self) -> None:
        """signals 包含 control_type 信号。"""
        bundle = _build_config_bundle("loop-1", ControlType.PRESSURE)
        assert "control_type" in bundle.data_block.signals
        assert bundle.data_block.signals["control_type"] == [ControlType.PRESSURE.value]

    def test_lineage_has_algorithm_version(self) -> None:
        """lineage 包含正确的 algorithm_version。"""
        bundle = _build_config_bundle("loop-1", ControlType.FLOW)
        assert bundle.lineage.algorithm_version == ALGORITHM_VERSION

    def test_valid_rate_is_one(self) -> None:
        """CONFIG bundle 的 valid_rate 为 1.0（配置数据始终有效）。"""
        bundle = _build_config_bundle("loop-1", ControlType.FLOW)
        assert bundle.lineage.valid_rate == 1.0


class TestBuildWeightsMap:
    """测试 _build_weights_map() 权重映射。"""

    def test_none_type_weights_returns_none(self) -> None:
        """type_weights=None 返回 None（使用默认权重）。"""
        assert _build_weights_map(None, "STABLE") is None

    def test_missing_score_type_returns_none(self) -> None:
        """score_type 不在 type_weights 中返回 None。"""
        type_weights = {"STABLE": {"weight_a": Decimal("0.2")}}
        assert _build_weights_map(type_weights, "FAST") is None

    def test_stable_weights_mapped(self) -> None:
        """STABLE 类型权重正确映射到计算器代码。"""
        type_weights = {
            "STABLE": {
                "weight_a": Decimal("0.2"),
                "weight_f": Decimal("0.3"),
                "weight_s": Decimal("0.5"),
            }
        }
        result = _build_weights_map(type_weights, "STABLE")
        assert result == {
            "accuracy_rate": 0.2,
            "fast_rate": 0.3,
            "stability_rate": 0.5,
        }

    def test_decimal_weights_converted_to_float(self) -> None:
        """Decimal 权重转换为 float。"""
        type_weights = {
            "FAST": {
                "weight_a": Decimal("0.25"),
                "weight_f": Decimal("0.50"),
                "weight_s": Decimal("0.25"),
            }
        }
        result = _build_weights_map(type_weights, "FAST")
        assert all(isinstance(v, float) for v in result.values())

    def test_missing_weight_keys_default_zero(self) -> None:
        """缺少 weight 键时默认为 0。"""
        type_weights = {"LOGIC": {"weight_a": Decimal("0.3")}}
        result = _build_weights_map(type_weights, "LOGIC")
        assert result == {
            "accuracy_rate": 0.3,
            "fast_rate": 0.0,
            "stability_rate": 0.0,
        }


# ===========================================================================
# 8. 三层计算流程测试
# ===========================================================================


class TestComputeKpisThreeLayer:
    """测试 _compute_kpis_three_layer() 三层计算流程。

    通过 mock get_calculator 和 ConfidenceEvaluator.compute_composite_score
    隔离具体计算器实现，验证编排逻辑（Layer1/Layer2 依赖注入/Layer3 评分）。
    """

    def test_layer1_calculates_all_10_independent_metrics(self) -> None:
        """Layer1 对 10 个无依赖指标调用计算器。"""
        # 构造 10 个 bundle（使用数据库列名）
        db_codes = [
            "accuracy_rate",
            "effective_auto_rate",
            "good_value_rate",
            "oscillation_rate",
            "saturation_rate",
            "stiction_coeff",
            "output_travel_index",
            "auto_mode_rate",
            "steady_state_time",
        ]
        bundles = [_make_bundle(code) for code in db_codes]
        config_bundle = _build_config_bundle("loop-1", ControlType.FLOW)

        # mock 计算器
        mock_calc = MagicMock()
        mock_calc.calculate.return_value = _make_metric_result("test", 90.0)
        mock_calc.with_dependencies.return_value = mock_calc

        composite_result = _make_metric_result("composite_score", 80.0)

        with (
            patch("app.tasks.kpi_calc.get_calculator", return_value=mock_calc),
            patch("app.tasks.kpi_calc.ConfidenceEvaluator") as mock_conf,
        ):
            mock_conf.compute_composite_score.return_value = composite_result
            results, composite = _compute_kpis_three_layer(bundles, config_bundle, None)

        # 10 个 Layer1 指标 + ideal_settling_time（config_bundle）都被计算
        layer1_calc_codes = [
            "accuracy_rate",
            "effective_auto_rate",
            "good_value_rate",
            "oscillation_rate",
            "saturation_rate",
            "stiction_index",
            "output_trip_index",
            "auto_mode_rate",
            "settling_time",
            "ideal_settling_time",
        ]
        for code in layer1_calc_codes:
            assert code in results, f"Layer1 指标 {code} 未被计算"

        assert composite == composite_result

    def test_bundle_db_code_mapped_to_calculator_code(self) -> None:
        """数据库列名正确映射为计算器代码（如 fast_response_rate → fast_rate）。"""
        bundles = [_make_bundle("fast_response_rate")]
        config_bundle = _build_config_bundle("loop-1", ControlType.FLOW)

        mock_calc = MagicMock()
        mock_calc.calculate.return_value = _make_metric_result("fast_rate", 80.0)
        mock_calc.with_dependencies.return_value = mock_calc

        with (
            patch("app.tasks.kpi_calc.get_calculator") as mock_get_calc,
            patch("app.tasks.kpi_calc.ConfidenceEvaluator") as mock_conf,
        ):
            mock_get_calc.return_value = mock_calc
            mock_conf.compute_composite_score.return_value = _make_metric_result(
                "composite_score", 50.0
            )
            _compute_kpis_three_layer(bundles, config_bundle, None)

        # get_calculator 被调用时传入的是计算器代码 "fast_rate" 而非 "fast_response_rate"
        called_codes = [call.args[0] for call in mock_get_calc.call_args_list]
        assert "fast_rate" in called_codes

    def test_layer2_stability_rate_gets_oscillation_dependency(self) -> None:
        """Layer2 stability_rate 注入 oscillation_rate 依赖。"""
        bundles = [
            _make_bundle("oscillation_rate"),
            _make_bundle("steady_rate"),  # DB code for stability_rate
        ]
        config_bundle = _build_config_bundle("loop-1", ControlType.FLOW)

        mock_calc = MagicMock()
        mock_calc.calculate.return_value = _make_metric_result("test", 70.0)
        mock_calc.with_dependencies.return_value = mock_calc

        with (
            patch("app.tasks.kpi_calc.get_calculator", return_value=mock_calc),
            patch("app.tasks.kpi_calc.ConfidenceEvaluator") as mock_conf,
        ):
            mock_conf.compute_composite_score.return_value = _make_metric_result(
                "composite_score", 60.0
            )
            _compute_kpis_three_layer(bundles, config_bundle, None)

        # with_dependencies 被调用时包含 oscillation_rate
        dep_calls = mock_calc.with_dependencies.call_args_list
        dep_args = [call.args[0] for call in dep_calls]
        assert any("oscillation_rate" in dep for dep in dep_args)

    def test_layer2_fast_rate_gets_settling_dependencies(self) -> None:
        """Layer2 fast_rate 注入 settling_time + ideal_settling_time 依赖。"""
        bundles = [
            _make_bundle("steady_state_time"),  # DB code for settling_time
            _make_bundle("fast_response_rate"),  # DB code for fast_rate
        ]
        config_bundle = _build_config_bundle("loop-1", ControlType.FLOW)

        mock_calc = MagicMock()
        mock_calc.calculate.return_value = _make_metric_result("test", 80.0)
        mock_calc.with_dependencies.return_value = mock_calc

        with (
            patch("app.tasks.kpi_calc.get_calculator", return_value=mock_calc),
            patch("app.tasks.kpi_calc.ConfidenceEvaluator") as mock_conf,
        ):
            mock_conf.compute_composite_score.return_value = _make_metric_result(
                "composite_score", 70.0
            )
            _compute_kpis_three_layer(bundles, config_bundle, None)

        dep_calls = mock_calc.with_dependencies.call_args_list
        dep_args = [call.args[0] for call in dep_calls]
        # 至少有一次 with_dependencies 包含 settling_time 和 ideal_settling_time
        found_fast_dep = False
        for dep in dep_args:
            if "settling_time" in dep and "ideal_settling_time" in dep:
                found_fast_dep = True
                break
        assert found_fast_dep, "fast_rate 未注入 settling_time + ideal_settling_time 依赖"

    def test_missing_bundle_skipped(self) -> None:
        """缺少 bundle 的指标被跳过（不调用计算器）。"""
        # 仅提供 accuracy_rate 的 bundle，其他 9 个缺失
        bundles = [_make_bundle("accuracy_rate")]
        config_bundle = _build_config_bundle("loop-1", ControlType.FLOW)

        mock_calc = MagicMock()
        mock_calc.calculate.return_value = _make_metric_result("accuracy_rate", 90.0)
        mock_calc.with_dependencies.return_value = mock_calc

        with (
            patch("app.tasks.kpi_calc.get_calculator", return_value=mock_calc),
            patch("app.tasks.kpi_calc.ConfidenceEvaluator") as mock_conf,
        ):
            mock_conf.compute_composite_score.return_value = _make_metric_result(
                "composite_score", 50.0
            )
            results, _ = _compute_kpis_three_layer(bundles, config_bundle, None)

        # accuracy_rate 和 ideal_settling_time 被计算
        assert "accuracy_rate" in results
        assert "ideal_settling_time" in results
        # oscillation_rate 没有 bundle，不应在 results 中
        assert "oscillation_rate" not in results

    def test_layer3_composite_score_called_with_weights(self) -> None:
        """Layer3 调用 ConfidenceEvaluator.compute_composite_score 并传入权重。"""
        bundles = [_make_bundle("accuracy_rate")]
        config_bundle = _build_config_bundle("loop-1", ControlType.FLOW)
        weights = {"accuracy_rate": 0.3, "fast_rate": 0.2, "stability_rate": 0.5}

        mock_calc = MagicMock()
        mock_calc.calculate.return_value = _make_metric_result("test", 90.0)
        mock_calc.with_dependencies.return_value = mock_calc

        with (
            patch("app.tasks.kpi_calc.get_calculator", return_value=mock_calc),
            patch("app.tasks.kpi_calc.ConfidenceEvaluator") as mock_conf,
        ):
            mock_conf.compute_composite_score.return_value = _make_metric_result(
                "composite_score", 75.0
            )
            _compute_kpis_three_layer(bundles, config_bundle, weights)

        mock_conf.compute_composite_score.assert_called_once()
        call_kwargs = mock_conf.compute_composite_score.call_args.kwargs
        assert call_kwargs["weights"] == weights

    def test_composite_result_stored_in_results(self) -> None:
        """综合评分结果被存入 results["composite_score"]。"""
        bundles = [_make_bundle("accuracy_rate")]
        config_bundle = _build_config_bundle("loop-1", ControlType.FLOW)

        mock_calc = MagicMock()
        mock_calc.calculate.return_value = _make_metric_result("test", 90.0)
        mock_calc.with_dependencies.return_value = mock_calc

        composite = _make_metric_result("composite_score", 85.0)

        with (
            patch("app.tasks.kpi_calc.get_calculator", return_value=mock_calc),
            patch("app.tasks.kpi_calc.ConfidenceEvaluator") as mock_conf,
        ):
            mock_conf.compute_composite_score.return_value = composite
            results, returned_composite = _compute_kpis_three_layer(bundles, config_bundle, None)

        assert results["composite_score"] == composite
        assert returned_composite == composite


# ===========================================================================
# 9. 数据提取函数测试
# ===========================================================================


class TestExtractKpiValues:
    """测试 _extract_kpi_values() 指标值提取。"""

    def test_all_metrics_mapped_to_db_codes(self) -> None:
        """所有计算器代码正确映射为数据库列名。"""
        metric_results = _make_full_metric_results()
        kpi_values = _extract_kpi_values(metric_results)

        # 验证关键映射
        assert kpi_values["accuracy_rate"] == Decimal("90.0")
        assert kpi_values["fast_response_rate"] == Decimal("80.0")  # fast_rate → fast_response_rate
        assert kpi_values["steady_rate"] == Decimal("70.0")  # stability_rate → steady_rate
        assert kpi_values["effective_auto_rate"] == Decimal("60.0")
        assert kpi_values["stiction_coeff"] == Decimal("0.5")  # stiction_index → stiction_coeff
        assert kpi_values["steady_state_time"] == Decimal(
            "45.0"
        )  # settling_time → steady_state_time
        assert kpi_values["output_travel_index"] == Decimal(
            "12.0"
        )  # output_trip_index → output_travel_index
        assert kpi_values["ideal_settling_time"] == Decimal("30.0")

    def test_none_values_preserved(self) -> None:
        """value=None 的指标保持 None。"""
        metric_results = _make_full_metric_results(accuracy=None, fast=None)
        kpi_values = _extract_kpi_values(metric_results)

        assert kpi_values["accuracy_rate"] is None
        assert kpi_values["fast_response_rate"] is None
        assert kpi_values["steady_rate"] == Decimal("70.0")

    def test_composite_score_skipped(self) -> None:
        """composite_score 不写入指标列。"""
        metric_results = _make_full_metric_results()
        metric_results["composite_score"] = _make_metric_result("composite_score", 80.0)
        kpi_values = _extract_kpi_values(metric_results)

        assert "composite_score" not in kpi_values

    def test_float_value_converted_to_decimal(self) -> None:
        """float 值正确转换为 Decimal。"""
        metric_results = {
            "accuracy_rate": _make_metric_result("accuracy_rate", 95.5),
        }
        kpi_values = _extract_kpi_values(metric_results)
        assert isinstance(kpi_values["accuracy_rate"], Decimal)
        assert kpi_values["accuracy_rate"] == Decimal("95.5")


class TestExtractLineageInfo:
    """测试 _extract_lineage_info() 数据血缘提取。"""

    def test_lineage_from_accuracy_rate(self) -> None:
        """优先从 accuracy_rate 的 lineage 取血缘信息。"""
        lineage = _make_data_lineage(
            sampling_freq="5s",
            valid_rate=0.88,
            algorithm_version="KPI_CALC_v2.0",
        )
        metric_results = {
            "accuracy_rate": _make_metric_result("accuracy_rate", 90.0, "B", lineage),
        }
        composite = _make_metric_result("composite_score", 80.0, "B")

        info = _extract_lineage_info(metric_results, composite)

        assert info["sampling_freq"] == "5s"
        assert info["valid_rate"] == Decimal("0.8800")
        assert info["algorithm_version"] == "KPI_CALC_v2.0"
        assert info["confidence_level"] == "B"

    def test_lineage_from_composite_when_accuracy_missing(self) -> None:
        """accuracy_rate 缺失时从 composite_result 的 lineage 取。"""
        lineage = _make_data_lineage(sampling_freq="1s", valid_rate=0.95)
        metric_results = {}
        composite = _make_metric_result("composite_score", 80.0, "A", lineage)

        info = _extract_lineage_info(metric_results, composite)

        assert info["sampling_freq"] == "1s"
        assert info["valid_rate"] == Decimal("0.9500")

    def test_lineage_default_when_both_missing(self) -> None:
        """accuracy_rate 和 composite 都无 lineage 时使用默认值。"""
        metric_results = {}
        composite = MetricResult(
            metric_code="composite_score",
            value=80.0,
            confidence_level="A",
            lineage=None,
        )

        info = _extract_lineage_info(metric_results, composite)

        assert info["algorithm_version"] == ALGORITHM_VERSION
        assert info["confidence_level"] == "A"

    def test_valid_rate_quantized_to_4_decimals(self) -> None:
        """valid_rate 量化到 4 位小数（Decimal(5,4) 精度）。"""
        lineage = _make_data_lineage(valid_rate=0.123456)
        metric_results = {
            "accuracy_rate": _make_metric_result("accuracy_rate", 90.0, "A", lineage),
        }
        composite = _make_metric_result("composite_score", 80.0, "A")

        info = _extract_lineage_info(metric_results, composite)

        assert info["valid_rate"] == Decimal("0.1235")

    def test_confidence_level_from_composite(self) -> None:
        """confidence_level 取自 composite_result。"""
        lineage = _make_data_lineage()
        metric_results = {
            "accuracy_rate": _make_metric_result("accuracy_rate", 90.0, "A", lineage),
        }
        composite = _make_metric_result("composite_score", 80.0, "C")

        info = _extract_lineage_info(metric_results, composite)

        assert info["confidence_level"] == "C"

    def test_confidence_level_defaults_to_e_when_none(self) -> None:
        """composite_result.confidence_level 为 None 时默认为 E。"""
        lineage = _make_data_lineage()
        metric_results = {
            "accuracy_rate": _make_metric_result("accuracy_rate", 90.0, "A", lineage),
        }
        composite = MetricResult(
            metric_code="composite_score",
            value=None,
            confidence_level=None,
            lineage=lineage,
        )

        info = _extract_lineage_info(metric_results, composite)

        assert info["confidence_level"] == "E"

    def test_data_lineage_dict_returned(self) -> None:
        """data_lineage 字段为 lineage 的字典序列化。"""
        lineage = _make_data_lineage(sampling_freq="1s", quality_policy="KEEP_ALL_WITH_VALIDITY")
        metric_results = {
            "accuracy_rate": _make_metric_result("accuracy_rate", 90.0, "A", lineage),
        }
        composite = _make_metric_result("composite_score", 80.0, "A")

        info = _extract_lineage_info(metric_results, composite)

        assert isinstance(info["data_lineage"], dict)
        assert info["data_lineage"]["sampling_freq"] == "1s"
        assert info["data_lineage"]["quality_policy"] == "KEEP_ALL_WITH_VALIDITY"


# ===========================================================================
# 10. metric_code 映射常量测试
# ===========================================================================


class TestMetricCodeMapping:
    """测试 metric_code 双向映射常量。"""

    def test_db_to_calculator_mapping_complete(self) -> None:
        """_DB_TO_CALCULATOR_METRIC_CODE 包含全部 12 对映射。"""
        assert len(_DB_TO_CALCULATOR_METRIC_CODE) == 12

    def test_reverse_mapping_consistent(self) -> None:
        """反向映射与正向映射一致。"""
        for db_code, calc_code in _DB_TO_CALCULATOR_METRIC_CODE.items():
            assert _CALCULATOR_TO_DB_METRIC_CODE[calc_code] == db_code

    def test_all_metric_codes_db_matches_keys(self) -> None:
        """_ALL_METRIC_CODES_DB 与映射表的 keys 一致。"""
        assert set(_ALL_METRIC_CODES_DB) == set(_DB_TO_CALCULATOR_METRIC_CODE.keys())

    def test_key_mappings_correct(self) -> None:
        """关键映射对正确（DB 列名 → 计算器代码）。"""
        assert _DB_TO_CALCULATOR_METRIC_CODE["fast_response_rate"] == "fast_rate"
        assert _DB_TO_CALCULATOR_METRIC_CODE["steady_rate"] == "stability_rate"
        assert _DB_TO_CALCULATOR_METRIC_CODE["stiction_coeff"] == "stiction_index"
        assert _DB_TO_CALCULATOR_METRIC_CODE["steady_state_time"] == "settling_time"
        assert _DB_TO_CALCULATOR_METRIC_CODE["output_travel_index"] == "output_trip_index"
