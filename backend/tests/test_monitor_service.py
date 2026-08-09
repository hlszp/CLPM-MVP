"""回路监控服务 monitor.py 单元测试。

测试覆盖：
- _mode_value_to_label：MODE 值 → 控制模式标签映射
- _get_loop_tag_values：回路 Tag 关联查询（有/无 Tag）
- list_loop_monitor：回路监控列表（空列表、过滤、分页、Tag 值填充）
- get_loop_monitor_detail：回路运行详情（不存在、正常、无 Tag、趋势异常、时间窗）
- lttb_downsample：降采样算法边界场景（字符串 ts、target_points 边界）
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.contracts.data_types import RawTimeSeries
from app.core.exceptions import BizError
from app.services.monitor import (
    _get_loop_tag_values,
    _mode_value_to_label,
    get_loop_monitor_detail,
    list_loop_monitor,
    lttb_downsample,
)

# ===========================================================================
# 辅助函数：构造 mock 对象
# ===========================================================================


def _make_scalars_mock(items: list) -> MagicMock:
    """构造 .scalars().all() 返回 items 的 mock 结果。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value: object) -> MagicMock:
    """构造 .scalar_one_or_none() 返回 value 的 mock 结果。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_count_mock(count: int) -> MagicMock:
    """构造 .scalar() 返回 count 的 mock 结果（用于 count 查询）。"""
    result = MagicMock()
    result.scalar.return_value = count
    return result


def _make_loop(
    loop_id: str = "loop-001",
    tag_name: str = "LIC-101",
    status: str = "READY",
    is_active: bool = True,
    unit_id: str | None = "unit-001",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = "液位控制"
    loop.unit_id = unit_id
    loop.status = status
    loop.is_active = is_active
    loop.score_weight = Decimal("85.50")
    loop.created_at = datetime.now(UTC)
    return loop


def _make_tag(
    tag_id: str = "tag-001",
    tag_name: str = "LIC-101.PV",
    current_value: float = 50.0,
    quality: str = "GOOD",
) -> MagicMock:
    """构造 TagRegistry mock。"""
    tag = MagicMock()
    tag.id = tag_id
    tag.tag_name = tag_name
    tag.current_value = current_value
    tag.quality = quality
    tag.last_sync_at = datetime.now(UTC)
    return tag


def _make_mapping(
    loop_id: str = "loop-001",
    tag_role: str = "PV",
    tag_id: str = "tag-001",
) -> MagicMock:
    """构造 LoopTagMapping mock。"""
    m = MagicMock()
    m.loop_id = loop_id
    m.tag_role = tag_role
    m.tag_id = tag_id
    return m


def _make_plant_node(node_id: str = "unit-001", name: str = "常减压装置") -> MagicMock:
    """构造 PlantNode mock。"""
    node = MagicMock()
    node.id = node_id
    node.name = name
    return node


def _make_mode_mapping_row(
    loop_id: str = "loop-001",
    mode_value: int = 5,
    mode_label: str = "AUTO",
) -> MagicMock:
    """构造 LoopModeMapping 行 mock（用于 _load_mode_mappings 直接迭代）。"""
    row = MagicMock()
    row.loop_id = loop_id
    row.mode_value = mode_value
    row.mode_label = mode_label
    return row


def _make_rows_iterable_mock(rows: list) -> MagicMock:
    """构造可直接迭代返回 rows 的 mock（用于 _load_mode_mappings 查询）。

    _load_mode_mappings 使用 ``for row in result:`` 直接迭代 result，
    而非 ``result.scalars().all()``，需要此辅助函数。
    """
    result = MagicMock()
    result.__iter__ = MagicMock(return_value=iter(rows))
    return result


def _make_raw_series(
    *,
    timestamps: list[str],
    pv: list[float] | None = None,
    sp: list[float] | None = None,
    op: list[float] | None = None,
    mode: list[float] | None = None,
    pv_quality: list[str] | None = None,
) -> RawTimeSeries:
    """构造宽表查询结果，与 DataProvider.make_query_fn 契约一致。"""
    signals = {
        role: values
        for role, values in {"pv": pv, "sp": sp, "op": op, "mode": mode}.items()
        if values is not None
    }
    return RawTimeSeries(
        timestamps=timestamps,
        signals=signals,
        quality_codes={"pv_quality": pv_quality or ["GOOD"] * len(timestamps)},
    )


# ===========================================================================
# _mode_value_to_label 单元测试
# ===========================================================================


class TestModeValueToLabel:
    """_mode_value_to_label：MODE tag 值 → 控制模式标签。"""

    def test_none_returns_none(self) -> None:
        """None 输入返回 None。"""
        assert _mode_value_to_label(None) is None

    def test_manual(self) -> None:
        """0 → Manual。"""
        assert _mode_value_to_label(0) == "Manual"

    def test_auto(self) -> None:
        """1 → Auto。"""
        assert _mode_value_to_label(1) == "Auto"

    def test_cascade_value_2(self) -> None:
        """2 → Cascade。"""
        assert _mode_value_to_label(2) == "Cascade"

    def test_cascade_value_3(self) -> None:
        """3 → Cascade。"""
        assert _mode_value_to_label(3) == "Cascade"

    def test_unknown(self) -> None:
        """99 → Unknown。"""
        assert _mode_value_to_label(99) == "Unknown"

    def test_float_value_truncates_to_int(self) -> None:
        """浮点数 1.0 → Auto（int 转换）。"""
        assert _mode_value_to_label(1.0) == "Auto"

    def test_custom_mapping_overrides_default(self) -> None:
        """用户自定义 mapping 优先于默认映射。

        场景：DCS 中 MODE=5 表示 AUTO，MODE=8 表示 CAS（非标准编码），
        用户在 loop_mode_mapping 表配置后应生效。
        """
        custom_mapping = {5: "Auto", 8: "Cascade", 0: "Manual"}
        assert _mode_value_to_label(5, custom_mapping) == "Auto"
        assert _mode_value_to_label(8, custom_mapping) == "Cascade"
        assert _mode_value_to_label(0, custom_mapping) == "Manual"
        # 默认映射中 1=Auto，但自定义 mapping 中 1 未定义 → Unknown
        assert _mode_value_to_label(1, custom_mapping) == "Unknown"

    def test_none_mapping_falls_back_to_default(self) -> None:
        """mapping=None 回退到默认映射（向后兼容）。"""
        assert _mode_value_to_label(1, None) == "Auto"
        assert _mode_value_to_label(0, None) == "Manual"
        assert _mode_value_to_label(2, None) == "Cascade"

    def test_empty_mapping_returns_unknown_for_known_default_value(self) -> None:
        """空 mapping 字典 {} 不回退默认，已知值返回 Unknown。

        区分 None（未提供配置）与 {}（显式空配置）：
        - None → 使用默认映射（向后兼容）
        - {} → 视为有效但空的配置，所有值返回 Unknown
        """
        assert _mode_value_to_label(1, {}) == "Unknown"

    def test_custom_mapping_unknown_value(self) -> None:
        """自定义 mapping 中未定义的值返回 Unknown。"""
        custom_mapping = {5: "Auto"}
        assert _mode_value_to_label(99, custom_mapping) == "Unknown"


# ===========================================================================
# _get_loop_tag_values 单元测试
# ===========================================================================


class TestGetLoopTagValues:
    """_get_loop_tag_values：获取回路的 Tag 关联和 Tag 详情。"""

    async def test_with_tags(self) -> None:
        """有 Tag 关联时返回正确的 tags_map 和 mappings。"""
        mappings = [
            _make_mapping(tag_role="PV", tag_id="tag-001"),
            _make_mapping(tag_role="SP", tag_id="tag-002"),
        ]
        tags = [
            _make_tag(tag_id="tag-001", tag_name="LIC-101.PV"),
            _make_tag(tag_id="tag-002", tag_name="LIC-101.SP"),
        ]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock(mappings),
                _make_scalars_mock(tags),
            ]
        )
        tags_map, result_mappings = await _get_loop_tag_values(db, "loop-001")
        assert len(tags_map) == 2
        assert "tag-001" in tags_map
        assert "tag-002" in tags_map
        assert "PV" in result_mappings
        assert "SP" in result_mappings
        assert db.execute.await_count == 2

    async def test_without_tags(self) -> None:
        """无 Tag 关联时返回空 dict，且不查询 TagRegistry。"""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_make_scalars_mock([])])
        tags_map, result_mappings = await _get_loop_tag_values(db, "loop-001")
        assert tags_map == {}
        assert result_mappings == {}
        # 无 tag_id 时只执行 1 次（mappings 查询），跳过 TagRegistry 查询
        assert db.execute.await_count == 1


# ===========================================================================
# list_loop_monitor 单元测试
# ===========================================================================


class TestListLoopMonitor:
    """list_loop_monitor：回路监控列表查询。"""

    async def test_empty_list(self) -> None:
        """无回路时返回 total=0, items=[]。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(0),
                _make_scalars_mock([]),
            ]
        )
        result = await list_loop_monitor(db)
        assert result["total"] == 0
        assert result["items"] == []
        assert result["page"] == 1
        assert result["pageSize"] == 20
        assert result["view"] == "list"
        assert db.execute.await_count == 2

    async def test_with_loops_no_tags(self) -> None:
        """有回路但无 Tag 关联时，current_values 全为 None；无 KPI 快照时 score/status 为 None。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # KPI 快照查询（空）
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        result = await list_loop_monitor(db)
        assert result["total"] == 1
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["loopId"] == "loop-001"
        assert item["tagName"] == "LIC-101"
        assert item["description"] == "液位控制"
        assert item["unitName"] == "常减压装置"
        assert item["currentValues"]["pv"] is None
        assert item["currentValues"]["sp"] is None
        assert item["currentValues"]["op"] is None
        assert item["currentValues"]["mode"] is None
        assert item["currentValues"]["modeLabel"] is None
        assert item["currentValues"]["pvQuality"] is None
        assert item["readAt"] is None
        # 无 KPI 快照时 score/kpiStatus/confidenceLevel 均为 None
        assert item["score"] is None
        assert item["kpiStatus"] is None
        assert item["confidenceLevel"] is None
        assert item["kpiSummary"] is None
        assert item["isActive"] is True
        assert item["controlMode"] is None
        # 数据健康度块存在（无快照时各字段为 None）
        assert item["dataHealth"]["validRate"] is None
        assert item["dataHealth"]["confidenceLevel"] is None
        assert item["dataHealth"]["pvCompleteness"] is None
        assert item["dataHealth"]["integrityStatus"] is None

    def _make_snap(self, score: str) -> MagicMock:
        """C1-1 测试用 KPI 快照（仅 score/ts_end/status 有效，其余速率字段 None）。"""
        snap = MagicMock()
        snap.loop_id = "loop-001"
        snap.score = Decimal(score)
        snap.status = "GOOD"
        snap.confidence_level = "A"
        snap.ts_end = datetime(2026, 8, 8, 6, 0, 0)
        for f in (
            "good_value_rate",
            "auto_mode_rate",
            "steady_rate",
            "accuracy_rate",
            "fast_rate",
            "oscillation_rate",
            "saturation_rate",
            "valid_rate",
            "effective_auto_rate",
        ):
            setattr(snap, f, None)
        return snap

    async def test_day_trend_worsened(self) -> None:
        """C1-1：当前评分较昨日基线下降 ≥2 → dayTrend=WORSENED + scoreDelta 差值。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),  # Tag 关联（空）
                _make_scalars_mock([self._make_snap("78.00")]),  # 最新 KPI 快照
                _make_scalars_mock([self._make_snap("85.00")]),  # 昨日基线快照
                _make_scalars_mock([]),  # mode mapping
                _make_scalars_mock([]),  # 完整性巡检快照
            ]
        )
        result = await list_loop_monitor(db)
        item = result["items"][0]
        assert item["score"] == 78.0
        assert item["scoreDelta"] == -7.0
        assert item["dayTrend"] == "WORSENED"

    async def test_day_trend_improved_and_flat(self) -> None:
        """C1-1：上升 ≥2 → IMPROVED；|delta| < 2 → FLAT。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),
                _make_scalars_mock([self._make_snap("86.50")]),
                _make_scalars_mock([self._make_snap("83.00")]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),
            ]
        )
        item = (await list_loop_monitor(db))["items"][0]
        assert item["scoreDelta"] == 3.5
        assert item["dayTrend"] == "IMPROVED"

    async def test_day_trend_new_without_baseline(self) -> None:
        """C1-1：有当前快照但无昨日基线 → dayTrend=NEW、scoreDelta=None。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),
                _make_scalars_mock([self._make_snap("80.00")]),
                _make_scalars_mock([]),  # 昨日基线快照（空）
                _make_scalars_mock([]),
                _make_scalars_mock([]),
            ]
        )
        item = (await list_loop_monitor(db))["items"][0]
        assert item["dayTrend"] == "NEW"
        assert item["scoreDelta"] is None

    async def test_day_trend_none_without_snapshot(self) -> None:
        """C1-1：无当前快照 → dayTrend/scoreDelta 均为 None。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # 最新 KPI 快照（空）
                _make_scalars_mock([]),  # 昨日基线快照（空）
                _make_scalars_mock([]),
                _make_scalars_mock([]),
            ]
        )
        item = (await list_loop_monitor(db))["items"][0]
        assert item["dayTrend"] is None
        assert item["scoreDelta"] is None

    async def test_is_active_filter_applied(self) -> None:
        """WS-D 阶段5：list 与 stats 口径统一，仅返回 is_active=True 的回路。

        通过断言生成的 SQL where 子句包含 is_active IS true，确保过滤条件已注入。
        """
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        await list_loop_monitor(db)
        # 第一次 db.execute 调用为 count_stmt，应包含 is_active 过滤
        count_call = db.execute.await_args_list[0]
        count_stmt = count_call.args[0]
        compiled = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "is_active IS true" in compiled
        # 第二次 db.execute 调用为 list_stmt，同样应包含 is_active 过滤
        list_call = db.execute.await_args_list[1]
        list_stmt = list_call.args[0]
        list_compiled = str(list_stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "is_active IS true" in list_compiled

    async def test_with_plant_node_filter(self) -> None:
        """带 plant_node_id 过滤时正确返回。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalars_mock([]),  # _get_descendant_node_ids 查询
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # KPI 快照查询（空）
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        result = await list_loop_monitor(db, plant_node_id="unit-001")
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert db.execute.await_count == 9

    async def test_with_keyword_filter(self) -> None:
        """带 keyword 过滤时正确返回空列表。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(0),
                _make_scalars_mock([]),
            ]
        )
        result = await list_loop_monitor(db, keyword="液位")
        assert result["total"] == 0
        assert result["items"] == []

    async def test_pagination(self) -> None:
        """分页参数正确传递。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(15),
                _make_scalars_mock([]),
            ]
        )
        result = await list_loop_monitor(db, page=2, page_size=10)
        assert result["page"] == 2
        assert result["pageSize"] == 10
        assert result["total"] == 15

    async def test_with_tag_values(self) -> None:
        """回路有 Tag 关联（PV/SP/OP/MODE）时 current_values 正确填充。"""
        loop = _make_loop()
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC-101.PV", current_value=50.0)
        sp_tag = _make_tag(tag_id="tag-sp", tag_name="LIC-101.SP", current_value=52.0)
        op_tag = _make_tag(tag_id="tag-op", tag_name="LIC-101.OP", current_value=55.0)
        mode_tag = _make_tag(tag_id="tag-mode", tag_name="LIC-101.MODE", current_value=1)
        mappings = [
            _make_mapping(tag_role="PV", tag_id="tag-pv"),
            _make_mapping(tag_role="SP", tag_id="tag-sp"),
            _make_mapping(tag_role="OP", tag_id="tag-op"),
            _make_mapping(tag_role="MODE", tag_id="tag-mode"),
        ]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock(mappings),
                _make_scalars_mock([pv_tag, sp_tag, op_tag, mode_tag]),
                _make_scalars_mock([]),  # KPI 快照查询（空）
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        result = await list_loop_monitor(db)
        item = result["items"][0]
        assert item["currentValues"]["pv"] == 50.0
        assert item["currentValues"]["sp"] == 52.0
        assert item["currentValues"]["op"] == 55.0
        assert item["currentValues"]["mode"] == 1
        assert item["currentValues"]["modeLabel"] == "Auto"
        assert item["currentValues"]["pvQuality"] == "GOOD"
        assert item["controlMode"] == "Auto"
        assert item["readAt"] is not None

    async def test_no_unit_id_skips_plant_query(self) -> None:
        """回路无 unit_id 时跳过 PlantNode 查询，unitName 为 None。"""
        loop = _make_loop(unit_id=None)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # KPI 快照查询（空）
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        result = await list_loop_monitor(db)
        item = result["items"][0]
        assert item["unitName"] is None
        # count + loops + mappings + kpi_snapshot + prev_snapshot + mode_mapping + integrity = 7 次
        # （跳过 plant node 查询；无 tags 因 mappings 为空）
        assert db.execute.await_count == 7

    async def test_no_score_weight(self) -> None:
        """无 KPI 快照时 score 为 None（score 来自 KpiSnapshotHourly，非 loop.score_weight）。"""
        loop = _make_loop()
        loop.score_weight = None
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # KPI 快照查询（空）
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        result = await list_loop_monitor(db)
        item = result["items"][0]
        assert item["score"] is None

    async def test_string_last_sync_at(self) -> None:
        """Tag 的 last_sync_at 为字符串时正确处理 readAt。"""
        loop = _make_loop()
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC-101.PV")
        pv_tag.last_sync_at = "2026-06-22T10:00:00"
        mappings = [_make_mapping(tag_role="PV", tag_id="tag-pv")]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock([_make_plant_node()]),
                _make_scalars_mock(mappings),
                _make_scalars_mock([pv_tag]),
                _make_scalars_mock([]),  # KPI 快照查询（空）
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        result = await list_loop_monitor(db)
        item = result["items"][0]
        assert item["readAt"] == "2026-06-22T10:00:00"

    async def test_custom_mode_mapping_overrides_default(self) -> None:
        """用户配置 loop_mode_mapping 后，modeLabel 使用自定义映射而非默认。

        场景：DCS 中 MODE=5 表示 AUTO（非标准编码），
        用户在 loop_mode_mapping 表配置 mode_value=5, mode_label='AUTO'，
        列表展示时应返回 "Auto" 而非默认映射的 "Unknown"。
        """
        loop = _make_loop(unit_id=None)  # 跳过 plant node 查询简化 mock
        mode_tag = _make_tag(tag_id="tag-mode", tag_name="LIC-101.MODE", current_value=5)
        mappings = [_make_mapping(tag_role="MODE", tag_id="tag-mode")]
        # 用户配置：MODE=5 → AUTO
        mode_mapping_rows = [
            _make_mode_mapping_row(loop_id="loop-001", mode_value=5, mode_label="AUTO")
        ]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_count_mock(1),
                _make_scalars_mock([loop]),
                _make_scalars_mock(mappings),
                _make_scalars_mock([mode_tag]),
                _make_scalars_mock([]),  # KPI 快照查询（空）
                _make_scalars_mock([]),  # 昨日基线快照查询（空）
                _make_rows_iterable_mock(mode_mapping_rows),  # mode mapping 查询（有配置）
                _make_scalars_mock([]),  # 完整性巡检快照查询（空）
            ]
        )
        result = await list_loop_monitor(db)
        item = result["items"][0]
        # MODE=5 在默认映射中是 Unknown，但用户配置为 AUTO → 转换为 "Auto"
        assert item["currentValues"]["mode"] == 5
        assert item["currentValues"]["modeLabel"] == "Auto"
        assert item["controlMode"] == "Auto"


# ===========================================================================
# get_loop_monitor_detail 单元测试
# ===========================================================================


class TestGetLoopMonitorDetail:
    """get_loop_monitor_detail：回路运行详情查询。"""

    async def test_loop_not_found(self) -> None:
        """回路不存在时抛出 BizError(ERR_LOOP_NOT_FOUND, 404)。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with pytest.raises(BizError) as exc_info:
            await get_loop_monitor_detail(db, "nonexistent")
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"
        assert exc_info.value.status_code == 404

    async def test_normal_detail_with_tags_and_trend(self) -> None:
        """正常详情：有 Tag 关联（含 PID）和趋势数据。"""
        loop = _make_loop(status="READY")
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC-101.PV", current_value=50.0)
        sp_tag = _make_tag(tag_id="tag-sp", tag_name="LIC-101.SP", current_value=52.0)
        op_tag = _make_tag(tag_id="tag-op", tag_name="LIC-101.OP", current_value=55.0)
        mode_tag = _make_tag(tag_id="tag-mode", tag_name="LIC-101.MODE", current_value=1)
        pid_p_tag = _make_tag(tag_id="tag-pidp", tag_name="LIC-101.PID_P", current_value=0.5)
        pid_i_tag = _make_tag(tag_id="tag-pidi", tag_name="LIC-101.PID_I", current_value=0.1)
        pid_d_tag = _make_tag(tag_id="tag-pidd", tag_name="LIC-101.PID_D", current_value=0.01)
        mappings = [
            _make_mapping(tag_role="PV", tag_id="tag-pv"),
            _make_mapping(tag_role="SP", tag_id="tag-sp"),
            _make_mapping(tag_role="OP", tag_id="tag-op"),
            _make_mapping(tag_role="MODE", tag_id="tag-mode"),
            _make_mapping(tag_role="PID_P", tag_id="tag-pidp"),
            _make_mapping(tag_role="PID_I", tag_id="tag-pidi"),
            _make_mapping(tag_role="PID_D", tag_id="tag-pidd"),
        ]
        # KPI 快照 mock
        snap = MagicMock()
        snap.score = Decimal("85.50")
        snap.status = "GOOD"
        snap.algorithm_version = "KPI_CALC_v2.0"
        snap.good_value_rate = Decimal("96.80")
        snap.auto_mode_rate = Decimal("90.00")
        snap.steady_rate = Decimal("85.00")
        snap.accuracy_rate = Decimal("80.00")
        snap.oscillation_rate = Decimal("15.00")
        snap.saturation_rate = Decimal("8.00")
        snap.effective_auto_rate = Decimal("85.00")
        snap.fast_rate = Decimal("75.00")
        snap.valid_rate = Decimal("0.95")
        snap.ts_end = datetime.now(UTC)
        snap.ts_start = datetime.now(UTC)
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock(mappings),
                _make_scalars_mock(
                    [pv_tag, sp_tag, op_tag, mode_tag, pid_p_tag, pid_i_tag, pid_d_tag]
                ),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([snap]),  # KPI 快照查询
            ]
        )
        raw_series = _make_raw_series(
            timestamps=["2026-06-22T08:00:00Z", "2026-06-22T08:00:01Z"],
            pv=[50.0, 50.5],
        )
        with patch("app.services.data_source.factory.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)
            mock_get_provider.return_value = mock_provider
            result = await get_loop_monitor_detail(db, "loop-001")
        assert result["loopId"] == "loop-001"
        assert result["tagName"] == "LIC-101"
        # WS-D 阶段5：status 拆分为 loopStatus（回路态）+ kpiStatus（评估态）
        assert result["loopStatus"] == "READY"
        assert result["kpiStatus"] == "GOOD"
        assert result["currentValues"]["pv"] == 50.0
        assert result["currentValues"]["sp"] == 52.0
        assert result["currentValues"]["op"] == 55.0
        assert result["currentValues"]["mode"] == 1
        assert result["currentValues"]["modeLabel"] == "Auto"
        assert result["currentValues"]["pvQuality"] == "GOOD"
        assert result["currentValues"]["readAt"] is not None
        assert result["runtimeParams"]["controlMode"] == "Auto"
        assert result["runtimeParams"]["pidP"] == 0.5
        assert result["runtimeParams"]["pidI"] == 0.1
        assert result["runtimeParams"]["pidD"] == 0.01
        assert result["trendStatus"] == "OK"
        assert len(result["trend"]["timestamps"]) == 2
        assert result["trend"]["pv"] == [50.0, 50.5]
        assert result["trend"]["pvQuality"] == ["GOOD", "GOOD"]
        assert result["kpiSummary"]["composite_score"] == 85.5
        assert result["kpiSummary"]["status"] == "GOOD"
        assert result["kpiSummary"]["algorithm_version"] == "KPI_CALC_v2.0"

    async def test_no_tags(self) -> None:
        """回路无 Tag 关联时 current_values 全为 None，trendStatus=EMPTY。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        result = await get_loop_monitor_detail(db, "loop-001")
        assert result["currentValues"]["pv"] is None
        assert result["currentValues"]["sp"] is None
        assert result["currentValues"]["op"] is None
        assert result["currentValues"]["mode"] is None
        assert result["currentValues"]["modeLabel"] is None
        assert result["currentValues"]["pvQuality"] is None
        assert result["currentValues"]["readAt"] is None
        assert result["trendStatus"] == "EMPTY"
        assert result["runtimeParams"]["controlMode"] is None
        assert result["runtimeParams"]["pidP"] is None

    async def test_trend_query_failure(self) -> None:
        """趋势数据查询失败时 trendStatus=EMPTY（异常被捕获，返回空数组）。"""
        loop = _make_loop()
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC-101.PV")
        mappings = [_make_mapping(tag_role="PV", tag_id="tag-pv")]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock(mappings),
                _make_scalars_mock([pv_tag]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        with patch("app.services.data_source.factory.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.make_query_fn.return_value = AsyncMock(
                side_effect=RuntimeError("TDengine 连接失败")
            )
            mock_get_provider.return_value = mock_provider
            result = await get_loop_monitor_detail(db, "loop-001")
        assert result["trendStatus"] == "EMPTY"
        # current_values 仍然从 Tag 当前值填充
        assert result["currentValues"]["pv"] == 50.0

    async def test_different_trend_windows(self) -> None:
        """不同 trend_window 参数均能正常处理。

        WS-D 阶段5：last_7_days 已从 TREND_WINDOWS 移除（后端不支持，仅诊断/看板维度使用 7 天窗）。
        """
        for window in ("last_1_hour", "last_24_hours", "last_72_hours"):
            loop = _make_loop()
            db = AsyncMock()
            db.execute = AsyncMock(
                side_effect=[
                    _make_scalar_one_or_none_mock(loop),
                    _make_scalars_mock([]),
                    _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                    _make_scalars_mock([]),  # KPI 快照查询
                ]
            )
            result = await get_loop_monitor_detail(db, "loop-001", trend_window=window)
            assert result["trendStatus"] == "EMPTY"

    async def test_invalid_trend_window_returns_400(self) -> None:
        """WS-D 阶段5：非法 trend_window（如 last_7_days）返回 400 BizError。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),
                _make_scalars_mock([]),
            ]
        )
        with pytest.raises(BizError) as exc_info:
            await get_loop_monitor_detail(db, "loop-001", trend_window="last_7_days")
        # BizError status_code=400，code=ERR_VALIDATION
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "ERR_VALIDATION"
        assert "last_7_days" in exc_info.value.message

    async def test_non_ready_status(self) -> None:
        """回路状态非 READY 时 KPI 状态为 INCONCLUSIVE。"""
        loop = _make_loop(status="PARTIAL")
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        result = await get_loop_monitor_detail(db, "loop-001")
        assert result["kpiSummary"]["status"] == "INCONCLUSIVE"
        # WS-D 阶段5：loopStatus 字段为回路状态（PARTIAL/INACTIVE/READY），
        # 供前端区分 KPI 缺失原因（Tag 关联不完整 vs 数据不足）
        assert result["loopStatus"] == "PARTIAL"
        assert result["kpiStatus"] == "INCONCLUSIVE"

    async def test_inactive_status_returned(self) -> None:
        """WS-D 阶段5: 回路 INACTIVE 时返回 loopStatus='INACTIVE'。

        前端依据 loopStatus 提示「回路未激活，不参与 KPI 计算」，
        与 PARTIAL（Tag 关联不完整）和 READY+INCONCLUSIVE（数据不足）区分。
        """
        loop = _make_loop(status="INACTIVE")
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        result = await get_loop_monitor_detail(db, "loop-001")
        assert result["loopStatus"] == "INACTIVE"
        assert result["kpiStatus"] == "INCONCLUSIVE"
        assert result["kpiSummary"]["status"] == "INCONCLUSIVE"

    async def test_algorithm_version_passthrough_from_snapshot(self) -> None:
        """P3 #55: monitor 应透传快照实际记录的 algorithm_version。

        场景：v1.0 旧快照保留在数据库中（升级前生成），monitor 应返回 v1.0 而非
        当前常量 v2.0，确保审计/排查时能识别旧快照。
        """
        loop = _make_loop()
        # 模拟 v1.0 旧快照（升级前生成，保留在数据库）
        snap = MagicMock()
        snap.score = Decimal("75.00")
        snap.status = "GOOD"
        snap.algorithm_version = "KPI_CALC_v1.0"  # 旧版本号
        snap.good_value_rate = Decimal("92.00")
        snap.auto_mode_rate = Decimal("88.00")
        snap.steady_rate = Decimal("80.00")
        snap.accuracy_rate = Decimal("75.00")
        snap.fast_rate = Decimal("70.00")
        snap.oscillation_rate = Decimal("20.00")
        snap.saturation_rate = Decimal("15.00")
        snap.effective_auto_rate = Decimal("82.00")
        snap.ts_end = datetime.now(UTC)

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # mode mapping
                _make_scalars_mock([snap]),  # KPI 快照
            ]
        )
        result = await get_loop_monitor_detail(db, "loop-001")
        # 透传快照实际版本号 v1.0（而非当前常量 v2.0）
        assert result["kpiSummary"]["algorithm_version"] == "KPI_CALC_v1.0"

    async def test_algorithm_version_fallback_when_no_snapshot(self) -> None:
        """P3 #55: 无快照时用统一常量 v2.0（不再硬编码 v1.0）。"""
        loop = _make_loop()
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # mode mapping
                _make_scalars_mock([]),  # 无快照
            ]
        )
        result = await get_loop_monitor_detail(db, "loop-001")
        # 无快照时 fallback 到当前 ALGORITHM_VERSION（v2.0）
        assert result["kpiSummary"]["algorithm_version"] == "KPI_CALC_v2.0"

    async def test_algorithm_version_fallback_when_snap_none(self) -> None:
        """P3 #55: 快照 algorithm_version 字段为 None 时 fallback 到统一常量 v2.0。"""
        loop = _make_loop()
        snap = MagicMock()
        snap.score = Decimal("75.00")
        snap.status = "GOOD"
        snap.algorithm_version = None  # 字段为空（兼容旧数据）
        snap.good_value_rate = Decimal("92.00")
        snap.auto_mode_rate = Decimal("88.00")
        snap.steady_rate = Decimal("80.00")
        snap.accuracy_rate = Decimal("75.00")
        snap.fast_rate = Decimal("70.00")
        snap.oscillation_rate = Decimal("20.00")
        snap.saturation_rate = Decimal("15.00")
        snap.effective_auto_rate = Decimal("82.00")
        snap.ts_end = datetime.now(UTC)

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),
                _make_scalars_mock([snap]),
            ]
        )
        result = await get_loop_monitor_detail(db, "loop-001")
        assert result["kpiSummary"]["algorithm_version"] == "KPI_CALC_v2.0"

    async def test_no_score_weight(self) -> None:
        """回路无 score_weight 时 composite_score 为 None。"""
        loop = _make_loop()
        loop.score_weight = None
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        result = await get_loop_monitor_detail(db, "loop-001")
        assert result["kpiSummary"]["composite_score"] is None

    async def test_trend_sp_only(self) -> None:
        """PV 无趋势数据但 SP 有时，以 SP 为基准对齐。"""
        loop = _make_loop()
        sp_tag = _make_tag(tag_id="tag-sp", tag_name="LIC-101.SP", current_value=52.0)
        mappings = [_make_mapping(tag_role="SP", tag_id="tag-sp")]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock(mappings),
                _make_scalars_mock([sp_tag]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        raw_series = _make_raw_series(
            timestamps=["2026-06-22T08:00:00Z"],
            sp=[52.0],
        )
        with patch("app.services.data_source.factory.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)
            mock_get_provider.return_value = mock_provider
            result = await get_loop_monitor_detail(db, "loop-001")
        assert result["trendStatus"] == "OK"
        assert result["trend"]["sp"] == [52.0]
        # PV 无趋势数据但对齐到 SP 时间戳，长度一致（值为 None）
        assert result["trend"]["pv"] == [None]

    async def test_trend_op_only(self) -> None:
        """仅 OP 有趋势数据时以 OP 为基准对齐。"""
        loop = _make_loop()
        op_tag = _make_tag(tag_id="tag-op", tag_name="LIC-101.OP", current_value=55.0)
        mappings = [_make_mapping(tag_role="OP", tag_id="tag-op")]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock(mappings),
                _make_scalars_mock([op_tag]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        raw_series = _make_raw_series(
            timestamps=["2026-06-22T08:00:00Z"],
            op=[55.0],
        )
        with patch("app.services.data_source.factory.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)
            mock_get_provider.return_value = mock_provider
            result = await get_loop_monitor_detail(db, "loop-001")
        assert result["trendStatus"] == "OK"
        assert result["trend"]["op"] == [55.0]

    async def test_string_last_sync_at(self) -> None:
        """Tag 的 last_sync_at 为字符串时正确处理 readAt。"""
        loop = _make_loop()
        pv_tag = _make_tag(tag_id="tag-pv", tag_name="LIC-101.PV")
        pv_tag.last_sync_at = "2026-06-22T10:00:00"
        mappings = [_make_mapping(tag_role="PV", tag_id="tag-pv")]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock(mappings),
                _make_scalars_mock([pv_tag]),
                _make_scalars_mock([]),  # mode mapping 查询（空，回退默认）
                _make_scalars_mock([]),  # KPI 快照查询
            ]
        )
        with patch("app.services.data_source.factory.get_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.make_query_fn.return_value = AsyncMock(
                return_value=RawTimeSeries(timestamps=[], signals={})
            )
            mock_get_provider.return_value = mock_provider
            result = await get_loop_monitor_detail(db, "loop-001")
        assert result["currentValues"]["readAt"] == "2026-06-22T10:00:00"


# ===========================================================================
# lttb_downsample 边界场景测试
# ===========================================================================


class TestLTTBDownsampleEdgeCases:
    """lttb_downsample 边界场景（覆盖字符串 ts 解析、target_points 边界）。"""

    def test_string_iso_timestamps(self) -> None:
        """字符串 ISO 时间戳能正确降采样。"""
        data = [
            {"ts": f"2026-06-22T08:00:{i:02d}Z", "value": float(i), "quality": "GOOD"}
            for i in range(15)
        ]
        result = lttb_downsample(data, threshold=10, target_points=5)
        assert len(result) == 5
        assert result[0]["ts"] == "2026-06-22T08:00:00Z"
        assert result[-1]["ts"] == "2026-06-22T08:00:14Z"

    def test_string_numeric_timestamps(self) -> None:
        """字符串数值时间戳（非 ISO 格式）回退为 float 解析。"""
        data = [{"ts": str(i), "value": float(i), "quality": "GOOD"} for i in range(15)]
        result = lttb_downsample(data, threshold=10, target_points=5)
        assert len(result) == 5

    def test_invalid_string_timestamps(self) -> None:
        """无效字符串时间戳回退为 0.0。"""
        data = [{"ts": "invalid", "value": float(i), "quality": "GOOD"} for i in range(15)]
        result = lttb_downsample(data, threshold=10, target_points=5)
        assert len(result) == 5

    def test_none_timestamps(self) -> None:
        """None 时间戳回退为 0.0。"""
        data = [{"ts": None, "value": float(i), "quality": "GOOD"} for i in range(15)]
        result = lttb_downsample(data, threshold=10, target_points=5)
        assert len(result) == 5

    def test_none_value_uses_zero(self) -> None:
        """value 为 None 时使用 0.0 替代。"""
        data = [{"ts": i, "value": None, "quality": "GOOD"} for i in range(15)]
        result = lttb_downsample(data, threshold=10, target_points=5)
        assert len(result) == 5

    def test_target_points_le_2(self) -> None:
        """target_points <= 2 时返回首尾两点。"""
        data = [{"ts": i, "value": float(i), "quality": "GOOD"} for i in range(15)]
        result = lttb_downsample(data, threshold=10, target_points=2)
        assert len(result) == 2
        assert result[0]["ts"] == 0
        assert result[-1]["ts"] == 14
