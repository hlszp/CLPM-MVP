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

import asyncio
from datetime import UTC, datetime, timedelta
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
    _check_import_idempotency,
    _compute_kpis_three_layer,
    _do_calculate,
    _do_calculate_single_loop,
    _extract_kpi_values,
    _extract_lineage_info,
    _find_nearest_value,
    _get_tag_name,
    _loop_type_to_control_type,
    _persist_snapshot,
    _quantize,
    _save_custom_snapshot,
    _save_snapshot,
    _summarize_batch_results,
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


def _make_returning_id_result_mock(snapshot_id: str) -> MagicMock:
    """构造 _save_snapshot 中 db.execute（UPSERT ... RETURNING id）的返回值。

    _save_snapshot 通过 ``on_conflict_do_update(...).returning(KpiSnapshotHourly.id)``
    随 UPSERT 一并取回实际写入的 id，返回值需支持 ``.first()`` 并返回一个
    可索引的 row（``row[0]`` 为 id 字符串）。
    """
    result = MagicMock()
    result.first.return_value = (snapshot_id,)
    return result


def _extract_upsert_set_values(upsert_stmt: object) -> dict:
    """从 postgresql.insert(...).on_conflict_do_update(...) 语句中提取 set_ 字典。

    _save_snapshot 构造的 UPSERT 语句使用 ``on_conflict_do_update(set_=update_cols)``
    设置冲突时的更新列。SQLAlchemy 将其存储在
    ``stmt._post_values_clause.update_values_to_set`` 中，是一个
    ``(column_name_str, value)`` 元组列表。

    Returns:
        dict[col_name_str, value]：列名到值的映射。
    """
    return dict(upsert_stmt._post_values_clause.update_values_to_set)


def _make_trend_point(ts: object, value: float, quality: str = "GOOD") -> dict:
    """构造 TDengine 时序数据点。"""
    return {"ts": ts, "value": value, "quality": quality}


def _make_full_metric_configs() -> dict[str, MagicMock]:
    """构造完整的 8 大 KPI 指标配置（对齐国标 4 分项评分公式）。

    参与评分的 4 指标（weight > 0）：
        accuracy_rate(30) + fast_rate(20) + steady_rate(30) + effective_auto_rate(20) = 100
    仅显示的指标（weight = 0）：好值率/自控率/振荡率/饱和率
    """
    return {
        "good_value_rate": _make_metric_config("good_value_rate", Decimal("0")),
        "auto_mode_rate": _make_metric_config("auto_mode_rate", Decimal("0")),
        "effective_auto_rate": _make_metric_config("effective_auto_rate", Decimal("20")),
        "steady_rate": _make_metric_config("steady_rate", Decimal("30")),
        "accuracy_rate": _make_metric_config("accuracy_rate", Decimal("30")),
        "fast_rate": _make_metric_config("fast_rate", Decimal("20")),
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
    async def test_new_snapshot_executes_upsert(self) -> None:
        """新增快照（existing=None）通过 UPSERT 写入，db.execute 调用 1 次。"""
        db = AsyncMock()
        db.add = MagicMock()
        # 唯一一次 execute（UPSERT ... RETURNING id）返回新 UUID
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("new-snapshot-id"))

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

        # UPSERT + RETURNING：db.execute 只调用 1 次，不调用 db.add
        assert db.execute.call_count == 1
        db.add.assert_not_called()
        # 返回字典关键字段
        assert result["loopId"] == "loop-1"
        assert result["snapshotId"] == "new-snapshot-id"
        assert result["status"] == "SUCCESS"
        assert result["score"] == 78.5
        assert result["algorithmVersion"] == ALGORITHM_VERSION
        assert result["tsStart"] == ts_start.isoformat()
        assert result["tsEnd"] == ts_end.isoformat()

    @pytest.mark.asyncio
    async def test_update_existing_snapshot_executes_upsert(self) -> None:
        """更新已有快照（UPSERT 触发 UPDATE 分支）不调用 db.add，返回字典正确。

        v4.0 实现使用 ``INSERT ... ON CONFLICT DO UPDATE``，由数据库层处理
        UPDATE 分支，应用层不再读取/修改 ``existing`` 对象。本测试验证：
        1. db.execute 被调用 1 次（UPSERT ... RETURNING id）
        2. 不调用 db.add
        3. 返回字典的 snapshotId 来自 RETURNING 子句（即旧 id）
        """
        db = AsyncMock()
        db.add = MagicMock()
        # RETURNING 返回已存在快照的 id（UPDATE 分支不生成新 id）
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("existing-snapshot-id"))

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
        assert db.execute.call_count == 1
        # 返回字典使用 RETURNING 取回的 id（即已存在快照的 id）
        assert result["snapshotId"] == "existing-snapshot-id"
        assert result["status"] == "INCONCLUSIVE"
        assert result["score"] is None

    @pytest.mark.asyncio
    async def test_partial_status(self) -> None:
        """PARTIAL 状态写入。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("partial-id"))
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
    async def test_lineage_fields_in_upsert_stmt(self) -> None:
        """新增快照时 7 个数据血缘字段在 UPSERT 语句的 set_ 字典中正确写入。

        v4.0 实现使用 ``postgresql.insert(...).on_conflict_do_update(set_=...)``
        构造 UPSERT，不再通过 ``db.add`` 写入 ORM 对象。因此无法再断言
        ``snapshot.ideal_settling_time`` 等属性；改为捕获第一次 ``db.execute``
        调用的 UPSERT 语句，从其 ``set_`` 字典验证血缘字段。
        """
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("new-uuid"))

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

        # 捕获 db.execute 调用的 UPSERT 语句（仅 1 次调用）
        assert db.execute.call_count == 1
        upsert_stmt = db.execute.call_args_list[0][0][0]
        set_dict = _extract_upsert_set_values(upsert_stmt)
        # 验证 7 个数据血缘字段在 ON CONFLICT DO UPDATE 的 set_ 字典中正确写入
        assert set_dict["ideal_settling_time"] == Decimal("30.0")
        assert set_dict["algorithm_version"] == "KPI_CALC_v2.0"
        assert set_dict["sampling_freq"] == "1s"
        assert set_dict["quality_policy"] == "KEEP_ALL_WITH_VALIDITY"
        assert set_dict["valid_rate"] == Decimal("0.9500")
        assert set_dict["confidence_level"] == "A"
        assert set_dict["data_lineage"] == lineage_dict

    @pytest.mark.asyncio
    async def test_lineage_fields_in_upsert_set_on_existing(self) -> None:
        """更新已有快照时 7 个数据血缘字段在 UPSERT 的 set_ 字典中被正确更新。

        UPSERT 的 ON CONFLICT DO UPDATE 分支会用 set_ 字典覆盖所有字段（包括
        血缘字段），无论新增还是更新。本测试验证 UPDATE 分支下 set_ 字典
        的血缘字段值正确（验证覆盖更新语义）。
        """
        db = AsyncMock()
        db.add = MagicMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("existing-snap-id"))

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
        assert db.execute.call_count == 1
        upsert_stmt = db.execute.call_args_list[0][0][0]
        set_dict = _extract_upsert_set_values(upsert_stmt)
        assert set_dict["ideal_settling_time"] == Decimal("60.0")
        assert set_dict["algorithm_version"] == "KPI_CALC_v2.0"
        assert set_dict["sampling_freq"] == "5s"
        assert set_dict["quality_policy"] == "KEEP_ALL_WITH_VALIDITY"
        assert set_dict["valid_rate"] == Decimal("0.8800")
        assert set_dict["confidence_level"] == "B"
        assert set_dict["data_lineage"] == {"key": "value"}


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
# 4. 批量计算编排辅助函数测试
# ===========================================================================


class TestBatchCalculationHelpers:
    """标准与自定义入口共享的结果归类必须保持一致。"""

    def test_summarize_batch_results(self) -> None:
        results = [
            {"status": "SUCCESS"},
            {"status": "INCONCLUSIVE"},
            None,
            RuntimeError("database unavailable"),
        ]

        assert _summarize_batch_results(results) == {
            "success": 1,
            "inconclusive": 1,
            "failed": 2,
        }


# ===========================================================================
# 5. _do_calculate 集成测试
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
            patch(
                "app.tasks.kpi_calc._batch_load_loop_configs",
                new_callable=AsyncMock,
                return_value={},
            ),
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
            patch(
                "app.tasks.kpi_calc._batch_load_loop_configs",
                new_callable=AsyncMock,
                return_value={},
            ),
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
            patch(
                "app.tasks.kpi_calc._batch_load_loop_configs",
                new_callable=AsyncMock,
                return_value={},
            ),
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
        with (
            patch(
                "app.tasks.kpi_calc._do_hourly_with_tracking",
                new_callable=AsyncMock,
                side_effect=RuntimeError("task tracking unavailable"),
            ),
            patch("app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock) as mock_calc,
        ):
            mock_calc.return_value = expected
            result = calculate_hourly_kpi.run()
            assert result == expected

    def test_calculate_hourly_kpi_with_ts_start(self) -> None:
        """calculate_hourly_kpi 透传 ts_start 给 _do_calculate（P1 #11）."""
        from datetime import UTC, datetime

        expected = {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}
        with (
            patch(
                "app.tasks.kpi_calc._do_hourly_with_tracking",
                new_callable=AsyncMock,
                side_effect=RuntimeError("task tracking unavailable"),
            ),
            patch("app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock) as mock_calc,
        ):
            mock_calc.return_value = expected
            # 任务跟踪不可用时走 fallback 分支调用 _do_calculate
            result = calculate_hourly_kpi.run(ts_start="2026-06-22T08:00:00Z")
            assert result == expected
            # 验证 _do_calculate 收到解析后的 datetime（非 None）
            call_kwargs = mock_calc.call_args
            ts_start_arg = call_kwargs.kwargs.get("ts_start")
            assert ts_start_arg is not None
            expected_dt = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
            assert ts_start_arg == expected_dt

    def test_calculate_hourly_kpi_ts_start_none_uses_default(self) -> None:
        """calculate_hourly_kpi 不传 ts_start 时 _do_calculate 收到 None（P1 #11）."""
        expected = {"total": 0, "success": 0, "inconclusive": 0, "failed": 0}
        with (
            patch(
                "app.tasks.kpi_calc._do_hourly_with_tracking",
                new_callable=AsyncMock,
                side_effect=RuntimeError("task tracking unavailable"),
            ),
            patch("app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock) as mock_calc,
        ):
            mock_calc.return_value = expected
            result = calculate_hourly_kpi.run()
            assert result == expected
            call_kwargs = mock_calc.call_args
            assert call_kwargs.kwargs.get("ts_start") is None

    def test_calculate_hourly_kpi_exception_reraises(self) -> None:
        """calculate_hourly_kpi 异常时重新抛出。"""
        with (
            patch(
                "app.tasks.kpi_calc._do_hourly_with_tracking",
                new_callable=AsyncMock,
                side_effect=RuntimeError("task tracking unavailable"),
            ),
            patch("app.tasks.kpi_calc._do_calculate", new_callable=AsyncMock) as mock_calc,
        ):
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

    def test_calculate_custom_loop_kpi_with_ts_end(self) -> None:
        """透传 ts_start+ts_end 给 _do_calculate_custom_loop（P1 #12）."""

        from app.tasks.kpi_calc import calculate_custom_loop_kpi

        expected = {"loopId": "loop-1", "taskId": "t-1", "status": "SUCCESS"}
        with patch(
            "app.tasks.kpi_calc._do_calculate_custom_loop", new_callable=AsyncMock
        ) as mock_fn:
            mock_fn.return_value = expected
            result = calculate_custom_loop_kpi.run(
                "t-1", "loop-1", "2026-06-22T08:00:00Z", "2026-06-22T09:30:00Z"
            )
            assert result == expected
            # 验证 ts_start + ts_end 透传
            call_args = mock_fn.call_args
            assert call_args.args[0] == "t-1"
            assert call_args.args[1] == "loop-1"
            assert call_args.args[2] == "2026-06-22T08:00:00Z"
            assert call_args.args[3] == "2026-06-22T09:30:00Z"

    def test_calculate_custom_loop_kpi_ts_end_none(self) -> None:
        """不传 ts_end 时 _do_calculate_custom_loop 收到 None（P1 #12 默认）."""
        from app.tasks.kpi_calc import calculate_custom_loop_kpi

        expected = {"loopId": "loop-1", "taskId": "t-1", "status": "SUCCESS"}
        with patch(
            "app.tasks.kpi_calc._do_calculate_custom_loop", new_callable=AsyncMock
        ) as mock_fn:
            mock_fn.return_value = expected
            result = calculate_custom_loop_kpi.run("t-1", "loop-1", "2026-06-22T08:00:00Z")
            assert result == expected
            call_args = mock_fn.call_args
            assert call_args.args[2] == "2026-06-22T08:00:00Z"
            assert call_args.args[3] is None


# ===========================================================================
# 6.0b calculate_custom_batch_kpi 批量任务入口测试（回归防护）
# ===========================================================================


class TestCalculateCustomBatchKpi:
    """批量自定义 KPI 任务入口测试。

    回归背景：commit 5cae2e5a 误删 ``calculate_custom_batch_kpi`` 定义，
    但 ``endpoints/tasks.py`` 仍调用它导致 ImportError → 500。本组测试
    锁定「任务可导入 + 参数透传 + 空回路早退」三项契约，防止再次回归。
    """

    def test_calculate_custom_batch_kpi_importable(self) -> None:
        """calculate_custom_batch_kpi 必须可从 kpi_calc 导入（回归防护）。"""
        from app.tasks.kpi_calc import calculate_custom_batch_kpi

        assert calculate_custom_batch_kpi.name == "app.tasks.kpi_calc.calculate_custom_batch_kpi"

    def test_calculate_custom_batch_kpi_passthrough_with_ts_end(self) -> None:
        """透传 task_id/loop_ids/ts_start/ts_end 给 _do_calculate_custom_batch。"""
        from app.tasks.kpi_calc import calculate_custom_batch_kpi

        expected = {"total": 2, "success": 2, "failed": 0}
        with patch(
            "app.tasks.kpi_calc._do_calculate_custom_batch", new_callable=AsyncMock
        ) as mock_fn:
            mock_fn.return_value = expected
            result = calculate_custom_batch_kpi.run(
                "t-batch", ["loop-1", "loop-2"], "2026-06-22T08:00:00Z", "2026-06-22T09:30:00Z"
            )
            assert result == expected
            call_args = mock_fn.call_args
            assert call_args.args[0] == "t-batch"
            assert call_args.args[1] == ["loop-1", "loop-2"]
            assert call_args.args[2] == "2026-06-22T08:00:00Z"
            assert call_args.args[3] == "2026-06-22T09:30:00Z"

    def test_calculate_custom_batch_kpi_passthrough_ts_end_none(self) -> None:
        """不传 ts_end 时 _do_calculate_custom_batch 收到 None。"""
        from app.tasks.kpi_calc import calculate_custom_batch_kpi

        expected = {"total": 1, "success": 1, "failed": 0}
        with patch(
            "app.tasks.kpi_calc._do_calculate_custom_batch", new_callable=AsyncMock
        ) as mock_fn:
            mock_fn.return_value = expected
            result = calculate_custom_batch_kpi.run("t-batch", ["loop-1"], "2026-06-22T08:00:00Z")
            assert result == expected
            call_args = mock_fn.call_args
            assert call_args.args[3] is None

    @pytest.mark.asyncio
    async def test_do_calculate_custom_batch_empty_loops(self) -> None:
        """空 loop_ids 列表直接返回零结果，不触发取数/计算。"""
        from app.tasks.kpi_calc import _do_calculate_custom_batch

        result = await _do_calculate_custom_batch("t-batch", [], "2026-06-22T08:00:00Z")
        assert result == {"total": 0, "success": 0, "failed": 0}


# ===========================================================================
# 6.1 _do_calculate_custom_loop 时间窗测试（P1 #12）
# ===========================================================================


class TestDoCalculateCustomLoopTimeWindow:
    """P1 #12: _do_calculate_custom_loop 时间窗处理测试。"""

    @pytest.mark.asyncio
    async def test_custom_loop_uses_user_specified_ts_end(self) -> None:
        """用户提供 ts_end 时，_calculate_loop_kpi 收到用户指定的时间窗结束。"""
        from datetime import UTC, datetime

        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.get_calc_cycle_minutes = AsyncMock(return_value=60)

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.services.engine_rule_loader.get_engine_rule_loader",
                return_value=mock_engine,
            ),
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch(
                "app.services.loop_config.get_loop_type_weights_map", new_callable=AsyncMock
            ) as mock_weights,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"status": "SUCCESS", "loopId": str(loop.id)}
            mock_weights.return_value = {}

            from app.tasks.kpi_calc import _do_calculate_custom_loop

            await _do_calculate_custom_loop(
                "task-001",
                str(loop.id),
                "2026-06-22T08:00:00Z",
                "2026-06-22T09:30:00Z",  # 用户指定 1.5 小时窗口（非 cycle_minutes=60）
            )

        # 验证 _calculate_loop_kpi 收到用户指定的 ts_end（而非 ts_start + 60min）
        call_kwargs = mock_calc.call_args.kwargs
        expected_ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        expected_ts_end = datetime(2026, 6, 22, 9, 30, 0, tzinfo=UTC)
        assert call_kwargs["ts_start"] == expected_ts_start
        assert call_kwargs["ts_end"] == expected_ts_end
        assert call_kwargs["custom_task_id"] == "task-001"

    @pytest.mark.asyncio
    async def test_custom_loop_ts_end_none_uses_cycle_minutes(self) -> None:
        """不传 ts_end 时，ts_end = ts_start + cycle_minutes（默认行为）。"""
        from datetime import UTC, datetime, timedelta

        loop = _make_loop()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_metric_config()]),
            ]
        )
        mock_session.commit = AsyncMock()

        mock_engine = MagicMock()
        mock_engine.get_calc_cycle_minutes = AsyncMock(return_value=60)

        with (
            patch("app.core.db.AsyncSessionLocal") as mock_factory,
            patch(
                "app.services.engine_rule_loader.get_engine_rule_loader",
                return_value=mock_engine,
            ),
            patch("app.tasks.kpi_calc._calculate_loop_kpi", new_callable=AsyncMock) as mock_calc,
            patch(
                "app.services.loop_config.get_loop_type_weights_map", new_callable=AsyncMock
            ) as mock_weights,
        ):
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_calc.return_value = {"status": "SUCCESS", "loopId": str(loop.id)}
            mock_weights.return_value = {}

            from app.tasks.kpi_calc import _do_calculate_custom_loop

            await _do_calculate_custom_loop(
                "task-001",
                str(loop.id),
                "2026-06-22T08:00:00Z",
                None,  # 不传 ts_end
            )

        # 验证 ts_end = ts_start + 60min
        call_kwargs = mock_calc.call_args.kwargs
        expected_ts_start = datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC)
        expected_ts_end = expected_ts_start + timedelta(minutes=60)
        assert call_kwargs["ts_start"] == expected_ts_start
        assert call_kwargs["ts_end"] == expected_ts_end


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


class TestBuildConfigBundleIdealSettlingTime:
    """回路级手动理想稳态时间注入（loop_ledger.ideal_settling_time → CONFIG signals）。"""

    def test_manual_value_injected_into_signals(self) -> None:
        """ideal_settling_time > 0 时注入 signals['ideal_settling_time']。"""
        bundle = _build_config_bundle("loop-1", ControlType.FLOW, 90.0)
        assert bundle.data_block.signals["ideal_settling_time"] == [90.0]

    def test_manual_value_drives_calculator_manual_branch(self) -> None:
        """注入手动值时 IdealSettlingTimeCalculator 走 manual 分支（最高优先级）。"""
        from app.services.metric_calculator.ideal_settling_time import (
            IdealSettlingTimeCalculator,
        )

        bundle = _build_config_bundle("loop-1", ControlType.FLOW, 90.0)
        result = IdealSettlingTimeCalculator().calculate(bundle)
        assert result.value == 90.0
        assert result.details["source"] == "manual"

    def test_no_manual_value_falls_back_to_type_default(self) -> None:
        """未传手动值时不注入信号，calculator 走控制类型默认值分支（FC=30）。"""
        from app.services.metric_calculator.ideal_settling_time import (
            IdealSettlingTimeCalculator,
        )

        bundle = _build_config_bundle("loop-1", ControlType.FLOW)
        assert "ideal_settling_time" not in bundle.data_block.signals
        result = IdealSettlingTimeCalculator().calculate(bundle)
        assert result.value == 30.0
        assert result.details["source"] == "default"

    def test_non_positive_manual_value_not_injected(self) -> None:
        """ideal_settling_time <= 0 时不注入信号（回退默认值分支）。"""
        for bad in (0.0, -10.0):
            bundle = _build_config_bundle("loop-1", ControlType.FLOW, bad)
            assert "ideal_settling_time" not in bundle.data_block.signals


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
# P2 #27 R3: MetricConfig.weight 全局优先级测试
# 设计意图：管理员通过 PUT /configs/metrics 设置的 MetricConfig.weight
#           应作为全局权重覆盖 LoopTypeWeight 模板
# ===========================================================================


class TestBuildWeightsMapMetricConfigPriority:
    """测试 _build_weights_map() 优先级链：MetricConfig.weight > LoopTypeWeight > None。

    P2 #27 R3：MetricConfig.weight 应实际参与计算，而非仅作元数据。
    """

    def test_metric_config_overrides_loop_type_weight(self) -> None:
        """MetricConfig.weight 全配置时覆盖 LoopTypeWeight 模板。"""
        # MetricConfig: a=50, f=30, s=20 (sum=100)
        metric_configs = {
            "accuracy_rate": _make_metric_config("accuracy_rate", weight=Decimal("50")),
            "fast_rate": _make_metric_config("fast_rate", weight=Decimal("30")),
            "steady_rate": _make_metric_config("steady_rate", weight=Decimal("20")),
        }
        # LoopTypeWeight STABLE: a=0.2, f=0.3, s=0.5（应被覆盖）
        type_weights = {
            "STABLE": {
                "weight_a": Decimal("0.2"),
                "weight_f": Decimal("0.3"),
                "weight_s": Decimal("0.5"),
            }
        }
        result = _build_weights_map(type_weights, "STABLE", metric_configs)
        # 归一化后：50/100=0.5, 30/100=0.3, 20/100=0.2
        assert result == {
            "accuracy_rate": 0.5,
            "fast_rate": 0.3,
            "stability_rate": 0.2,
        }

    def test_metric_config_normalized_when_sum_not_100(self) -> None:
        """MetricConfig.weight 总和不为 100 时也按比例归一化（容错）。"""
        # sum=200（异常输入但容错）
        metric_configs = {
            "accuracy_rate": _make_metric_config("accuracy_rate", weight=Decimal("100")),
            "fast_rate": _make_metric_config("fast_rate", weight=Decimal("60")),
            "steady_rate": _make_metric_config("steady_rate", weight=Decimal("40")),
        }
        result = _build_weights_map(None, "STABLE", metric_configs)
        # 归一化：100/200=0.5, 60/200=0.3, 40/200=0.2
        assert result == {
            "accuracy_rate": 0.5,
            "fast_rate": 0.3,
            "stability_rate": 0.2,
        }

    def test_metric_config_with_partial_null_falls_back_to_loop_type(self) -> None:
        """MetricConfig.weight 部分为 null 时回退到 LoopTypeWeight。"""
        # accuracy_rate.weight=null, fast/steady 已配置（应回退）
        metric_configs = {
            "accuracy_rate": _make_metric_config("accuracy_rate", weight=None),
            "fast_rate": _make_metric_config("fast_rate", weight=Decimal("30")),
            "steady_rate": _make_metric_config("steady_rate", weight=Decimal("20")),
        }
        type_weights = {
            "SLOW": {
                "weight_a": Decimal("0.3"),
                "weight_f": Decimal("0.1"),
                "weight_s": Decimal("0.6"),
            }
        }
        result = _build_weights_map(type_weights, "SLOW", metric_configs)
        # 回退到 LoopTypeWeight SLOW 模板
        assert result == {
            "accuracy_rate": 0.3,
            "fast_rate": 0.1,
            "stability_rate": 0.6,
        }

    def test_metric_config_with_zero_weight_falls_back_to_loop_type(self) -> None:
        """MetricConfig.weight 含 0 时回退到 LoopTypeWeight（视作未配置）。"""
        metric_configs = {
            "accuracy_rate": _make_metric_config("accuracy_rate", weight=Decimal("0")),
            "fast_rate": _make_metric_config("fast_rate", weight=Decimal("50")),
            "steady_rate": _make_metric_config("steady_rate", weight=Decimal("50")),
        }
        type_weights = {
            "FAST": {
                "weight_a": Decimal("0.2"),
                "weight_f": Decimal("0.4"),
                "weight_s": Decimal("0.4"),
            }
        }
        result = _build_weights_map(type_weights, "FAST", metric_configs)
        assert result == {
            "accuracy_rate": 0.2,
            "fast_rate": 0.4,
            "stability_rate": 0.4,
        }

    def test_metric_config_partial_missing_falls_back_to_loop_type(self) -> None:
        """metric_configs 缺失某核心指标时回退到 LoopTypeWeight。"""
        # 缺 steady_rate
        metric_configs = {
            "accuracy_rate": _make_metric_config("accuracy_rate", weight=Decimal("50")),
            "fast_rate": _make_metric_config("fast_rate", weight=Decimal("30")),
        }
        type_weights = {
            "STABLE": {
                "weight_a": Decimal("0.25"),
                "weight_f": Decimal("0.20"),
                "weight_s": Decimal("0.55"),
            }
        }
        result = _build_weights_map(type_weights, "STABLE", metric_configs)
        assert result == {
            "accuracy_rate": 0.25,
            "fast_rate": 0.20,
            "stability_rate": 0.55,
        }

    def test_metric_config_only_metric_config_returns_normalized(self) -> None:
        """仅有 MetricConfig.weight（type_weights=None）时仍能解析。"""
        metric_configs = {
            "accuracy_rate": _make_metric_config("accuracy_rate", weight=Decimal("40")),
            "fast_rate": _make_metric_config("fast_rate", weight=Decimal("35")),
            "steady_rate": _make_metric_config("steady_rate", weight=Decimal("25")),
        }
        result = _build_weights_map(None, "STABLE", metric_configs)
        assert result == {
            "accuracy_rate": 0.4,
            "fast_rate": 0.35,
            "stability_rate": 0.25,
        }

    def test_metric_configs_none_falls_back_to_loop_type(self) -> None:
        """metric_configs=None 时直接走 LoopTypeWeight（保持兼容）。"""
        type_weights = {
            "STABLE": {
                "weight_a": Decimal("0.25"),
                "weight_f": Decimal("0.20"),
                "weight_s": Decimal("0.55"),
            }
        }
        result = _build_weights_map(type_weights, "STABLE", None)
        assert result == {
            "accuracy_rate": 0.25,
            "fast_rate": 0.20,
            "stability_rate": 0.55,
        }

    def test_both_none_returns_none(self) -> None:
        """metric_configs=None 且 type_weights=None 返回 None（用默认权重）。"""
        result = _build_weights_map(None, "STABLE", None)
        assert result is None


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
            "stiction_index",
            "output_trip_index",
            "auto_mode_rate",
            "settling_time",
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
        """数据库列名正确映射为计算器代码（如 fast_rate → fast_rate）。"""
        bundles = [_make_bundle("settling_time"), _make_bundle("fast_rate")]
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

        # get_calculator 被调用时传入的是计算器代码 "fast_rate" 而非 "fast_rate"
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
            _make_bundle("settling_time"),  # DB code for settling_time
            _make_bundle("fast_rate"),  # DB code for fast_rate
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

    def test_layer2_fast_rate_skipped_when_dependency_is_missing(self) -> None:
        """fast_rate 缺少任一声明依赖时不允许按不完整公式计算。"""
        bundles = [_make_bundle("fast_rate")]
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
            results, _ = _compute_kpis_three_layer(bundles, config_bundle, None)

        assert "fast_rate" not in results
        assert mock_calc.with_dependencies.call_count == 0

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
        assert kpi_values["fast_rate"] == Decimal("80.0")  # fast_rate → fast_rate
        assert kpi_values["steady_rate"] == Decimal("70.0")  # stability_rate → steady_rate
        assert kpi_values["effective_auto_rate"] == Decimal("60.0")
        assert kpi_values["stiction_index"] == Decimal("0.5")  # stiction_index → stiction_index
        assert kpi_values["settling_time"] == Decimal("45.0")  # settling_time → settling_time
        assert kpi_values["output_trip_index"] == Decimal(
            "12.0"
        )  # output_trip_index → output_trip_index
        assert kpi_values["ideal_settling_time"] == Decimal("30.0")

    def test_none_values_preserved(self) -> None:
        """value=None 的指标保持 None。"""
        metric_results = _make_full_metric_results(accuracy=None, fast=None)
        kpi_values = _extract_kpi_values(metric_results)

        assert kpi_values["accuracy_rate"] is None
        assert kpi_values["fast_rate"] is None
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
        assert _DB_TO_CALCULATOR_METRIC_CODE["fast_rate"] == "fast_rate"
        assert _DB_TO_CALCULATOR_METRIC_CODE["steady_rate"] == "stability_rate"
        assert _DB_TO_CALCULATOR_METRIC_CODE["stiction_index"] == "stiction_index"
        assert _DB_TO_CALCULATOR_METRIC_CODE["settling_time"] == "settling_time"
        assert _DB_TO_CALCULATOR_METRIC_CODE["output_trip_index"] == "output_trip_index"


# ===========================================================================
# P2 #29 B6: _save_custom_snapshot 数据血缘字段写入测试
# ===========================================================================


class TestSaveCustomSnapshotLineage:
    """P2 #29 B6: 自定义任务快照表数据血缘字段写入测试。

    验证 sampling_freq/quality_policy 与 kpi_snapshot_hourly 对齐，
    自定义任务具备完整数据血缘追溯能力。
    """

    @pytest.mark.asyncio
    async def test_new_custom_snapshot_writes_lineage_fields(self) -> None:
        """新增 custom 快照时 sampling_freq/quality_policy 被写入。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)

        result = await _save_custom_snapshot(
            db=db,
            task_id="task-p229-001",
            loop_id="loop-p229",
            ts_start=ts_start,
            ts_end=ts_end,
            status="SUCCESS",
            score=Decimal("82.50"),
            sampling_freq="1s",
            quality_policy="TDengine",
            algorithm_version=ALGORITHM_VERSION,
            valid_rate=Decimal("0.9876"),
            confidence_level="A",
            data_lineage={"algorithm_version": ALGORITHM_VERSION},
        )

        db.add.assert_called_once()
        added_obj = db.add.call_args.args[0]
        assert added_obj.sampling_freq == "1s"
        assert added_obj.quality_policy == "TDengine"
        assert added_obj.valid_rate == Decimal("0.9876")
        assert added_obj.confidence_level == "A"
        assert added_obj.algorithm_version == ALGORITHM_VERSION

        assert result["taskId"] == "task-p229-001"
        assert result["loopId"] == "loop-p229"
        assert result["status"] == "SUCCESS"
        assert result["score"] == 82.5

    @pytest.mark.asyncio
    async def test_update_existing_custom_snapshot_writes_lineage_fields(self) -> None:
        """更新已有 custom 快照时 sampling_freq/quality_policy 被写入。"""
        existing = MagicMock()
        existing.id = "existing-custom-id"
        existing.sampling_freq = None
        existing.quality_policy = None
        existing.algorithm_version = None
        existing.valid_rate = None
        existing.confidence_level = None
        existing.data_lineage = None
        existing.score = None
        existing.status = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(existing))
        db.add = MagicMock()

        ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)

        await _save_custom_snapshot(
            db=db,
            task_id="task-p229-002",
            loop_id="loop-p229",
            ts_start=ts_start,
            ts_end=ts_end,
            status="SUCCESS",
            score=Decimal("88.00"),
            sampling_freq="1min",
            quality_policy="OPC_DA",
            algorithm_version=ALGORITHM_VERSION,
            valid_rate=Decimal("0.95"),
            confidence_level="B",
            data_lineage={"algorithm_version": ALGORITHM_VERSION},
        )

        db.add.assert_not_called()
        assert existing.sampling_freq == "1min"
        assert existing.quality_policy == "OPC_DA"
        assert existing.valid_rate == Decimal("0.95")
        assert existing.confidence_level == "B"
        assert existing.algorithm_version == ALGORITHM_VERSION

    @pytest.mark.asyncio
    async def test_custom_snapshot_lineage_none_when_not_provided(self) -> None:
        """未提供 sampling_freq/quality_policy 时写入 None（向后兼容）。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)

        await _save_custom_snapshot(
            db=db,
            task_id="task-p229-003",
            loop_id="loop-p229",
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )

        db.add.assert_called_once()
        added_obj = db.add.call_args.args[0]
        assert added_obj.sampling_freq is None
        assert added_obj.quality_policy is None


class TestPersistSnapshotLineagePassThrough:
    """P2 #29 B6: _persist_snapshot 不再剔除 sampling_freq/quality_policy。

    验证自定义任务路径将完整 kwargs 透传给 _save_custom_snapshot。
    """

    @pytest.mark.asyncio
    async def test_persist_custom_snapshot_passes_lineage_fields(self) -> None:
        """_persist_snapshot 在 custom_task_id 模式下透传 sampling_freq/quality_policy。"""
        with patch(
            "app.tasks.kpi_calc._save_custom_snapshot", new_callable=AsyncMock
        ) as mock_save_custom:
            mock_save_custom.return_value = {"taskId": "task-p229", "loopId": "loop-1"}

            ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC)
            ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)

            await _persist_snapshot(
                db=AsyncMock(),
                custom_task_id="task-p229",
                loop_id="loop-1",
                ts_start=ts_start,
                ts_end=ts_end,
                status="SUCCESS",
                score=Decimal("80.00"),
                sampling_freq="1s",
                quality_policy="TDengine",
                algorithm_version=ALGORITHM_VERSION,
                valid_rate=Decimal("0.95"),
                confidence_level="A",
                data_lineage={"algorithm_version": ALGORITHM_VERSION},
            )

            call_kwargs = mock_save_custom.call_args.kwargs
            assert call_kwargs["sampling_freq"] == "1s"
            assert call_kwargs["quality_policy"] == "TDengine"
            assert call_kwargs["task_id"] == "task-p229"
            assert call_kwargs["loop_id"] == "loop-1"

    @pytest.mark.asyncio
    async def test_persist_standard_snapshot_still_writes_lineage_fields(self) -> None:
        """_persist_snapshot 标准任务路径仍透传 sampling_freq/quality_policy 到 _save_snapshot。"""
        with patch("app.tasks.kpi_calc._save_snapshot", new_callable=AsyncMock) as mock_save:
            mock_save.return_value = {"loopId": "loop-1"}

            ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC)
            ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)

            await _persist_snapshot(
                db=AsyncMock(),
                custom_task_id=None,
                loop_id="loop-1",
                ts_start=ts_start,
                ts_end=ts_end,
                status="SUCCESS",
                score=Decimal("80.00"),
                sampling_freq="1s",
                quality_policy="TDengine",
                algorithm_version=ALGORITHM_VERSION,
                valid_rate=Decimal("0.95"),
                confidence_level="A",
                data_lineage={"algorithm_version": ALGORITHM_VERSION},
            )

            call_kwargs = mock_save.call_args.kwargs
            assert call_kwargs["sampling_freq"] == "1s"
            assert call_kwargs["quality_policy"] == "TDengine"
            assert "task_id" not in call_kwargs


# ===========================================================================
# 任务取消逻辑测试（_is_task_cancelled + _do_backfill 提前终止）
# ===========================================================================


class TestIsTaskCancelled:
    """_is_task_cancelled — 检测任务取消标志."""

    @pytest.mark.asyncio
    async def test_returns_true_when_status_is_cancelled(self) -> None:
        """Redis 中 status=CANCELLED → 返回 True."""
        from app.tasks.kpi_calc import _is_task_cancelled

        fake_redis = MagicMock()
        fake_redis.hget = AsyncMock(return_value="CANCELLED")
        with patch("app.core.redis.redis_client", fake_redis):
            result = await _is_task_cancelled("task-001")
        assert result is True
        fake_redis.hget.assert_called_once_with("task:task-001", "status")

    @pytest.mark.asyncio
    async def test_returns_false_when_status_is_running(self) -> None:
        """Redis 中 status=RUNNING → 返回 False."""
        from app.tasks.kpi_calc import _is_task_cancelled

        fake_redis = MagicMock()
        fake_redis.hget = AsyncMock(return_value="RUNNING")
        with patch("app.core.redis.redis_client", fake_redis):
            result = await _is_task_cancelled("task-002")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_task_not_in_redis(self) -> None:
        """任务不存在（hget 返回 None）→ 返回 False."""
        from app.tasks.kpi_calc import _is_task_cancelled

        fake_redis = MagicMock()
        fake_redis.hget = AsyncMock(return_value=None)
        with patch("app.core.redis.redis_client", fake_redis):
            result = await _is_task_cancelled("task-nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_for_lowercase_cancelled(self) -> None:
        """status=cancelled（小写）→ 应识别为已取消."""
        from app.tasks.kpi_calc import _is_task_cancelled

        fake_redis = MagicMock()
        fake_redis.hget = AsyncMock(return_value="cancelled")
        with patch("app.core.redis.redis_client", fake_redis):
            result = await _is_task_cancelled("task-003")
        assert result is True


class TestBackfillWindowBatchCancellation:
    """_backfill_window_batch 在任务被取消时提前终止.

    设计依据：当用户 POST /tasks/{task_id}/cancel 后，
    回填子任务应在下个窗口开始前检测到 CANCELLED 状态并主动返回。
    """

    @staticmethod
    def _run_child(
        *,
        redis_status: str,
        task_id: str | None,
        windows: list[str],
    ) -> tuple[dict, AsyncMock, MagicMock]:
        from app.tasks.kpi_calc import _backfill_window_batch

        loop = _make_loop()
        loop_result = _make_scalars_mock([loop])
        metric_result = _make_scalars_mock([])
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[loop_result, metric_result])
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=db)
        session.__aexit__ = AsyncMock(return_value=False)

        fake_redis = MagicMock()
        fake_redis.hget = AsyncMock(return_value=redis_status)
        task = MagicMock()
        task.run_async.side_effect = asyncio.run
        calculate = AsyncMock(return_value=[{"status": "SUCCESS"}])

        with (
            patch("app.core.db.AsyncSessionLocal", return_value=session),
            patch("app.core.redis.redis_client", fake_redis),
            patch(
                "app.services.loop_config.get_loop_type_weights_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "app.tasks.kpi_calc._batch_load_loop_configs",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch("app.tasks.kpi_calc._run_batch_loop_calculations", calculate),
            patch(
                "app.tasks.kpi_calc._do_backfill_node_aggregation",
                new_callable=AsyncMock,
                return_value=0,
            ),
        ):
            result = _backfill_window_batch.run.__func__(
                task,
                windows,
                task_id=task_id,
            )

        return result, calculate, fake_redis

    def test_backfill_returns_cancelled_when_status_is_cancelled(self) -> None:
        """第一个窗口开始前检测到取消 → 立即返回 cancelled=True."""
        result, calculate, _ = self._run_child(
            redis_status="CANCELLED",
            task_id="task-cancel-test",
            windows=["2026-07-06T08:00:00", "2026-07-06T09:00:00"],
        )

        assert result["cancelled"] is True
        calculate.assert_not_awaited()

    def test_backfill_completes_when_not_cancelled(self) -> None:
        """任务未取消 → 正常执行所有窗口，返回 cancelled 字段不出现或为 False."""
        result, calculate, _ = self._run_child(
            redis_status="RUNNING",
            task_id="task-running-test",
            windows=["2026-07-06T08:00:00", "2026-07-06T09:00:00"],
        )

        assert calculate.await_count == 2
        assert result["success"] == 2
        assert "cancelled" not in result or result["cancelled"] is False

    def test_backfill_no_task_id_skips_cancel_check(self) -> None:
        """task_id=None → 不查询 Redis 取消标志，正常执行."""
        result, calculate, fake_redis = self._run_child(
            redis_status="CANCELLED",
            task_id=None,
            windows=["2026-07-06T08:00:00", "2026-07-06T09:00:00"],
        )

        fake_redis.hget.assert_not_called()
        assert calculate.await_count == 2
        assert result["success"] == 2


# ===========================================================================
# loop_confidence_latest 同步写入测试
# ===========================================================================


class TestExtractMetricsDetail:
    """_extract_metrics_detail — 12 子指标值+可信度提取（metrics JSONB 产物）."""

    def test_extracts_values_and_confidence(self) -> None:
        from app.tasks.kpi_calc import _extract_metrics_detail

        metric_results = {
            "accuracy_rate": _make_metric_result("accuracy_rate", 93.35, "A"),
            "stability_rate": _make_metric_result("stability_rate", 88.0, "B"),
            "fast_rate": _make_metric_result("fast_rate", None, "E"),
            "composite_score": _make_metric_result("composite_score", 90.0, "A"),
        }

        detail = _extract_metrics_detail(metric_results)

        # composite_score 不进入子指标 JSONB
        assert "composite_score" not in detail
        # Calculator 代码 stability_rate → DB 列名 steady_rate
        assert detail["steady_rate"] == {"value": 88.0, "confidence": "B"}
        assert detail["accuracy_rate"] == {"value": 93.35, "confidence": "A"}
        # None 值保留 None（INCONCLUSIVE 指标）
        assert detail["fast_rate"] == {"value": None, "confidence": "E"}

    def test_empty_metric_results_returns_empty_dict(self) -> None:
        from app.tasks.kpi_calc import _extract_metrics_detail

        assert _extract_metrics_detail({}) == {}


class TestPersistSnapshotConfidenceLatest:
    """_persist_snapshot 小时路径同步 UPSERT loop_confidence_latest."""

    @staticmethod
    def _confidence_stmt(db: AsyncMock, call_index: int):
        return db.execute.await_args_list[call_index].args[0]

    @pytest.mark.asyncio
    async def test_hourly_path_upserts_confidence_latest(self) -> None:
        """写 kpi_snapshot_hourly 后同步 UPSERT loop_confidence_latest（按 loop_id 冲突覆盖）。"""
        from sqlalchemy.dialects import postgresql

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("snap-1"))

        ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC)
        ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)
        metrics_detail = {"accuracy_rate": {"value": 93.35, "confidence": "A"}}

        result = await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=ts_start,
            ts_end=ts_end,
            status="SUCCESS",
            score=Decimal("90.00"),
            valid_rate=Decimal("0.9500"),
            confidence_level="A",
            algorithm_version=ALGORITHM_VERSION,
            metrics_detail=metrics_detail,
        )

        assert result["status"] == "SUCCESS"
        # 两次 execute：kpi_snapshot_hourly UPSERT + loop_confidence_latest UPSERT
        assert db.execute.await_count == 2

        stmt = self._confidence_stmt(db, 1)
        assert stmt.table.name == "loop_confidence_latest"
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "ON CONFLICT (loop_id) DO UPDATE" in compiled

        set_values = _extract_upsert_set_values(stmt)
        assert set_values["status"] == "SUCCESS"
        assert set_values["score"] == Decimal("90.00")
        assert set_values["valid_rate"] == 0.95  # Decimal → float
        assert set_values["confidence_level"] == "A"
        assert set_values["metrics"] == metrics_detail
        assert set_values["data_ts_start"] == ts_start
        assert set_values["data_ts_end"] == ts_end
        assert set_values["algorithm_version"] == ALGORITHM_VERSION
        assert "eval_time" in set_values
        assert "updated_at" in set_values
        # id / loop_id 不参与冲突更新
        assert "id" not in set_values
        assert "loop_id" not in set_values

    @pytest.mark.asyncio
    async def test_second_write_carries_latest_values(self) -> None:
        """两次写同回路：第二次 UPSERT 的 set_ 为最新值（冲突即覆盖为最新记录）。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("snap-1"))

        kwargs = {
            "db": db,
            "loop_id": "loop-1",
            "ts_start": datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC),
            "ts_end": datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC),
            "status": "SUCCESS",
            "score": Decimal("80.00"),
            "confidence_level": "B",
            "metrics_detail": {"accuracy_rate": {"value": 80.0, "confidence": "B"}},
        }
        await _persist_snapshot(**kwargs)
        kwargs["score"] = Decimal("95.00")
        kwargs["confidence_level"] = "A"
        kwargs["metrics_detail"] = {"accuracy_rate": {"value": 95.0, "confidence": "A"}}
        await _persist_snapshot(**kwargs)

        # 每次 2 条 execute（主快照 + 最新表），共 4 条；最后一次为最新值
        assert db.execute.await_count == 4
        stmt = self._confidence_stmt(db, 3)
        assert stmt.table.name == "loop_confidence_latest"
        set_values = _extract_upsert_set_values(stmt)
        assert set_values["score"] == Decimal("95.00")
        assert set_values["confidence_level"] == "A"
        assert set_values["metrics"] == {"accuracy_rate": {"value": 95.0, "confidence": "A"}}

    @pytest.mark.asyncio
    async def test_confidence_latest_failure_does_not_affect_snapshot(self) -> None:
        """loop_confidence_latest 写库失败仅记日志，主快照结果正常返回。"""
        ok_result = _make_returning_id_result_mock("snap-1")
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[ok_result, RuntimeError("boom")])

        result = await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC),
            status="SUCCESS",
            score=Decimal("90.00"),
        )

        assert result["snapshotId"] == "snap-1"
        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_custom_path_skips_confidence_latest(self) -> None:
        """自定义任务快照（custom_task_id 非 None）不写 loop_confidence_latest。"""
        db = AsyncMock()
        # _save_custom_snapshot 走 select-then-add：select 返回 None → db.add 新对象
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))

        await _persist_snapshot(
            db=db,
            custom_task_id="task-1",
            loop_id="loop-1",
            ts_start=datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC),
            status="SUCCESS",
            metrics_detail={"accuracy_rate": {"value": 1.0, "confidence": "A"}},
        )

        # 仅 _save_custom_snapshot 的一次 select，无 loop_confidence_latest UPSERT
        assert db.execute.await_count == 1


class TestPersistSnapshotInconclusiveConfidenceE:
    """P0 #3: INCONCLUSIVE 快照 confidence_level 缺省落 'E'（§7.15 E↔INCONCLUSIVE）。"""

    @pytest.mark.asyncio
    async def test_hourly_inconclusive_defaults_confidence_level_e(self) -> None:
        """小时路径 INCONCLUSIVE 未传等级 → 快照与最新表均落 'E'。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("snap-1"))

        await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC),
            status="INCONCLUSIVE",
        )

        assert db.execute.await_count == 2
        snapshot_stmt = db.execute.await_args_list[0].args[0]
        assert snapshot_stmt.table.name == "kpi_snapshot_hourly"
        assert _extract_upsert_set_values(snapshot_stmt)["confidence_level"] == "E"
        confidence_stmt = db.execute.await_args_list[1].args[0]
        assert confidence_stmt.table.name == "loop_confidence_latest"
        assert _extract_upsert_set_values(confidence_stmt)["confidence_level"] == "E"

    @pytest.mark.asyncio
    async def test_inconclusive_keeps_lineage_confidence_level(self) -> None:
        """INCONCLUSIVE 已传血缘等级 → 沿用传入值，不被兜底覆盖。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("snap-1"))

        await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC),
            status="INCONCLUSIVE",
            confidence_level="D",
        )

        snapshot_stmt = db.execute.await_args_list[0].args[0]
        assert _extract_upsert_set_values(snapshot_stmt)["confidence_level"] == "D"

    @pytest.mark.asyncio
    async def test_custom_inconclusive_defaults_confidence_level_e(self) -> None:
        """自定义任务路径 INCONCLUSIVE 未传等级 → 新增对象落 'E'。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        db.add = MagicMock()

        await _persist_snapshot(
            db=db,
            custom_task_id="task-1",
            loop_id="loop-1",
            ts_start=datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC),
            status="INCONCLUSIVE",
        )

        db.add.assert_called_once()
        added_obj = db.add.call_args.args[0]
        assert added_obj.status == "INCONCLUSIVE"
        assert added_obj.confidence_level == "E"

    @pytest.mark.asyncio
    async def test_success_status_confidence_level_not_forced(self) -> None:
        """非 INCONCLUSIVE 状态不触发兜底：未传等级保持 None。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_returning_id_result_mock("snap-1"))

        await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC),
            ts_end=datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC),
            status="PARTIAL",
            score=Decimal("50.00"),
        )

        snapshot_stmt = db.execute.await_args_list[0].args[0]
        assert _extract_upsert_set_values(snapshot_stmt)["confidence_level"] is None

    @pytest.mark.asyncio
    async def test_composite_none_passes_lineage_confidence_to_persist(self) -> None:
        """_calculate_loop_kpi 综合评分 None 路径沿用 composite 血缘等级（E）。"""
        loop = _make_loop()
        db = AsyncMock()

        mock_planner = AsyncMock()
        mock_planner.request_bundles = AsyncMock(return_value=[_make_bundle("accuracy_rate")])

        metric_results = _make_full_metric_results(effective_auto=None)
        composite_result = MetricResult(
            metric_code="composite_score",
            value=None,
            confidence_level="E",
            lineage=_make_data_lineage(),
        )

        with (
            patch(
                "app.tasks.kpi_calc._compute_kpis_three_layer",
                return_value=(metric_results, composite_result),
            ),
            patch("app.tasks.kpi_calc._persist_snapshot", new_callable=AsyncMock) as mock_persist,
        ):
            mock_persist.return_value = {"status": "INCONCLUSIVE", "score": None}
            result = await _calculate_loop_kpi(
                db=db,
                loop=loop,
                metric_configs={},
                ts_start=datetime(2026, 6, 22, 8, 0, 0, tzinfo=UTC),
                ts_end=datetime(2026, 6, 22, 9, 0, 0, tzinfo=UTC),
                data_planner=mock_planner,
            )

        assert result["status"] == "INCONCLUSIVE"
        assert mock_persist.await_args.kwargs["confidence_level"] == "E"


class TestCheckImportIdempotency:
    """_check_import_idempotency 幂等预检查测试.

    覆盖 5 个逻辑分支：
    - task_id=None → 返回 None
    - _get_task 返回 None（记录不存在）→ 返回 None
    - PENDING → 返回 None（正常执行）
    - SUCCESS/FAILED/CANCELLED → 返回 _build_cached_result（含 skipped_redelivery）
    - RUNNING 未超时 → 返回跳过结果；RUNNING 超时 / started_at 缺失 → 返回 None
    """

    @pytest.mark.asyncio
    async def test_none_task_id_returns_none(self) -> None:
        """task_id=None 时直接返回 None，不查 Redis。"""
        result = await _check_import_idempotency(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_task_record_missing_returns_none(self) -> None:
        """Redis 中无任务记录时返回 None（防御性放行）。"""
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=None)):
            result = await _check_import_idempotency("nonexistent-task-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_pending_status_returns_none(self) -> None:
        """PENDING 状态返回 None，允许正常执行。"""
        task_data = {"status": "PENDING", "loop_count": "5"}
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=task_data)):
            result = await _check_import_idempotency("pending-task-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_success_status_returns_cached_result(self) -> None:
        """SUCCESS 终态返回缓存结果，含 skipped_redelivery=True。"""
        task_data = {
            "status": "SUCCESS",
            "loop_count": "5",
            "imported_count": "5",
            "error_count": "0",
            "result": '{"total": 5, "succeeded": 5, "failed": 0, "errors": []}',
        }
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=task_data)):
            result = await _check_import_idempotency("success-task-id")
        assert result is not None
        assert result["skipped_redelivery"] is True
        assert result["total"] == 5
        assert result["succeeded"] == 5

    @pytest.mark.asyncio
    async def test_failed_status_returns_cached_result(self) -> None:
        """FAILED 终态返回缓存结果。"""
        task_data = {
            "status": "FAILED",
            "loop_count": "5",
            "imported_count": "3",
            "error_count": "2",
            "error_message": "连接超时",
        }
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=task_data)):
            result = await _check_import_idempotency("failed-task-id")
        assert result is not None
        assert result["skipped_redelivery"] is True
        assert result["failed"] == 2

    @pytest.mark.asyncio
    async def test_cancelled_status_returns_cached_result(self) -> None:
        """CANCELLED 终态返回缓存结果。"""
        task_data = {
            "status": "CANCELLED",
            "loop_count": "5",
            "imported_count": "0",
            "error_count": "0",
        }
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=task_data)):
            result = await _check_import_idempotency("cancelled-task-id")
        assert result is not None
        assert result["skipped_redelivery"] is True

    @pytest.mark.asyncio
    async def test_running_not_expired_returns_skip(self) -> None:
        """RUNNING 且 started_at 未超时（60s < 7200s 阈值）→ 返回跳过结果。"""
        started_at = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        task_data = {
            "status": "RUNNING",
            "loop_count": "5",
            "started_at": started_at,
        }
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=task_data)):
            result = await _check_import_idempotency("running-task-id")
        assert result is not None
        assert result["skipped_redelivery"] is True
        assert "concurrent redelivery skipped" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_running_expired_returns_none(self) -> None:
        """RUNNING 且 started_at 已超时（8000s > 7200s 阈值）→ 返回 None（接续执行）。"""
        started_at = (datetime.now(UTC) - timedelta(seconds=8000)).isoformat()
        task_data = {
            "status": "RUNNING",
            "loop_count": "5",
            "started_at": started_at,
        }
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=task_data)):
            result = await _check_import_idempotency("expired-task-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_running_missing_started_at_returns_none(self) -> None:
        """RUNNING 但 started_at 缺失 → 返回 None（交 _do_import CAS 兜底）。"""
        task_data = {
            "status": "RUNNING",
            "loop_count": "5",
            "started_at": "",
        }
        with patch("app.services.data_import._get_task", new=AsyncMock(return_value=task_data)):
            result = await _check_import_idempotency("no-started-at-task-id")
        assert result is None
