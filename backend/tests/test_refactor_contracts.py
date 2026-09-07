"""P0-3 接口契约基线测试（测点子表重构）.

冻结对象（设计 §2.2 / §5.1 / §5.3 / §5.4）：本重构期间以下契约**不得变化**，
任何差异须以"取数桥接 diff + 主审确认"流程处理，不允许静默修改：
1. HistoryDataProvider 协议形态（make_query_fn / query_trend_data / close）
2. QueryFn 签名与 RawTimeSeries 结构（字段/角色命名/质量键）
3. 时间口径（查询边界格式化、存储侧 +8 墙钟解析、闭区间端点）
4. 质量码全局映射（quality_code.map_quality_code）
5. COV 前向填充列集合与 RawTimeSeries 转换行为
6. 导入任务对外响应字段与"点数"单位（宽表行数=时间槽数）
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from app.contracts.data_types import RawTimeSeries, TimeWindow
from app.services.data_source.base import HistoryDataProvider
from app.services.data_source.factory import get_provider
from app.services.data_source.realtime_subscriber import RealtimeSubscriber
from app.services.data_source.tdengine_provider import (
    TDengineProvider,
    _format_ts,
    _parse_ts,
    _rows_to_raw_series,
    _stored_ts_to_utc_naive,
)

# ---------------------------------------------------------------------------
# 1. Provider 协议与工厂
# ---------------------------------------------------------------------------


class TestProviderContract:
    def test_factory_returns_local_tdengine_provider(self):
        """工厂恒返回本地 TDengineProvider（计算不降级远端）。"""
        provider = get_provider()
        assert isinstance(provider, TDengineProvider)

    def test_provider_satisfies_protocol(self):
        assert isinstance(TDengineProvider(), HistoryDataProvider)

    def test_make_query_fn_signature(self):
        """make_query_fn(db) 返回 (loop_id, tag_roles, start, end, interval_s) 闭包."""
        provider = TDengineProvider()
        fn = provider.make_query_fn(db=object())
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        assert [p.name for p in params] == ["loop_id", "tag_roles", "start", "end", "interval_s"]

    def test_query_trend_data_signature_and_default(self):
        sig = inspect.signature(TDengineProvider.query_trend_data)
        params = list(sig.parameters)
        assert params == ["self", "tag_name", "start_time", "end_time", "sample_interval"]
        assert sig.parameters["sample_interval"].default == 1

    def test_close_is_async(self):
        assert inspect.iscoroutinefunction(TDengineProvider.close)


# ---------------------------------------------------------------------------
# 2. RawTimeSeries / 角色与质量键
# ---------------------------------------------------------------------------


class TestRawTimeSeriesContract:
    def test_dataclass_fields(self):
        """必需字段冻结 + 可选上下文（AD01 校正：默认 None=legacy 兼容）."""
        fields = RawTimeSeries.__dataclass_fields__
        # 既有必需字段与默认行为不变（旧构造方式/旧消费者兼容）
        assert {"timestamps", "signals", "quality_codes"} <= set(fields)
        assert fields["quality_codes"].default_factory is not None  # 可缺省
        # AD01：可选 series_context，默认 None（不带上下文构造=旧行为）
        assert "series_context" in fields
        assert fields["series_context"].default is None
        legacy = RawTimeSeries(timestamps=[], signals={}, quality_codes={})
        assert legacy.series_context is None

    def test_role_names_lowercase_and_quality_key(self):
        """角色键为小写；当前契约仅输出 pv_quality 一个质量键."""
        rows = [
            {
                "ts": "2026-09-06 00:00:01.000",
                "pv": 1.0,
                "sp": 2.0,
                "op": 3.0,
                "mode": 1,
                "pid_p": 0.5,
                "pid_i": 0.1,
                "pid_d": 0.0,
                "pv_quality": 1,
            },
            {
                "ts": "2026-09-06 00:00:02.000",
                "pv": 1.1,
                "sp": 2.0,
                "op": 3.1,
                "mode": 1,
                "pid_p": 0.5,
                "pid_i": 0.1,
                "pid_d": 0.0,
                "pv_quality": 0,
            },
        ]
        raw = _rows_to_raw_series(rows, {}, ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"])
        assert set(raw.signals) == {"pv", "sp", "op", "mode", "pid_p", "pid_i", "pid_d"}
        assert set(raw.quality_codes) == {"pv_quality"}
        assert raw.quality_codes["pv_quality"] == [1, 0]
        for values in raw.signals.values():
            assert len(values) == len(raw.timestamps)

    def test_cov_fill_columns_frozen(self):
        """COV 前向填充列固定 sp/mode/pid_p/pid_i/pid_d（PV/OP 不填充）."""
        from app.core.tdengine_native import COV_FILL_COLUMNS

        assert set(COV_FILL_COLUMNS) == {"sp", "mode", "pid_p", "pid_i", "pid_d"}

    def test_cov_fill_initial_value_carried(self):
        """窗口前初值经 initial 参数进入首行（现行为，作为契约固定）."""
        rows = [
            {
                "ts": "2026-09-06 00:00:01.000",
                "pv": 1.0,
                "sp": None,
                "op": 3.0,
                "mode": None,
                "pid_p": None,
                "pid_i": None,
                "pid_d": None,
                "pv_quality": 1,
            },
        ]
        initial = {"sp": 9.0, "mode": 1, "pid_p": 0.5, "pid_i": 0.2, "pid_d": 0.1}
        raw = _rows_to_raw_series(rows, initial, ["SP", "MODE"])
        assert raw.signals["sp"] == [9.0]
        assert raw.signals["mode"] == [1]

    def test_subscriber_row_layout_frozen(self):
        """realtime_subscriber._build_row 9 列布局（ts+7角色+pv_quality）。"""
        roles = {
            "PV": {"value": "1.5", "quality": 1, "ts": "2026-09-06T08:00:00+08:00"},
            "SP": {"value": "2.0", "quality": 1, "ts": "2026-09-06T08:00:00+08:00"},
        }
        row = RealtimeSubscriber._build_row(None, roles)  # 静态语义（不引用 self）
        assert row == ("2026-09-06 08:00:00.000", 1.5, 2.0, None, None, None, None, None, 1)


# ---------------------------------------------------------------------------
# 3. 时间口径（查询边界 / 存储时区 / 区间语义）
# ---------------------------------------------------------------------------


class TestTimeContract:
    def test_format_ts_naive_as_utc(self):
        out = _format_ts(datetime(2026, 9, 6, 0, 0, 0))
        assert out == "2026-09-06T00:00:00.000Z"

    def test_format_ts_aware_converted_to_utc(self):
        from datetime import timedelta, timezone

        plus8 = timezone(timedelta(hours=8))
        out = _format_ts(datetime(2026, 9, 6, 8, 0, 0, tzinfo=plus8))
        assert out == "2026-09-06T00:00:00.000Z"

    def test_format_ts_string_passthrough(self):
        assert _format_ts("2026-09-06T00:00:00.000Z") == "2026-09-06T00:00:00.000Z"

    def test_stored_ts_to_utc_naive(self):
        """+8 墙钟字符串 → naive UTC（缓存行比较口径）。"""
        dt = _stored_ts_to_utc_naive("2026-09-06 08:00:00.000")
        assert dt == datetime(2026, 9, 6, 0, 0, 0)
        assert dt.tzinfo is None

    def test_stored_ts_with_tz_respected(self):
        dt = _stored_ts_to_utc_naive("2026-09-06T00:00:00Z")
        assert dt == datetime(2026, 9, 6, 0, 0, 0)

    def test_parse_ts_aware_to_naive_utc(self):
        dt = _parse_ts(datetime(2026, 9, 6, 2, 0, 0, tzinfo=UTC))
        assert dt == datetime(2026, 9, 6, 2, 0, 0)
        assert dt.tzinfo is None

    def test_wide_sql_inclusive_bounds(self):
        """宽表查询窗口两端均含（>= 与 <=）。"""
        from app.core.tdengine_native import _build_wide_sql

        sql = _build_wide_sql("d_loop_x", "A", "B")
        assert "ts >= 'A' AND ts <= 'B'" in sql
        sql_half_open = _build_wide_sql("d_loop_x", "A", "B", inclusive_end=False)
        assert "ts >= 'A' AND ts < 'B'" in sql_half_open

    def test_time_window_doc_semantics(self):
        """TimeWindow 起止均含（docstring 契约 + 逻辑宽表网格=全部整秒含两端）."""
        assert "含" in TimeWindow.__doc__ or "inclusive" in TimeWindow.__doc__.lower()


# ---------------------------------------------------------------------------
# 4. 质量码映射（全局函数，禁改）
# ---------------------------------------------------------------------------


class TestQualityCodeContract:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (None, "Good"),  # 缺省容错为 Good（既有行为，兼容层不得依赖此语义兜底新表）
            (1, "Good"),
            (2, "Good"),
            (3, "Good"),
            (192, "Good"),
            (0, "Bad"),
            (7, "Unknown"),
            ("abc", "Unknown"),
        ],
    )
    def test_map_quality_code(self, raw, expected):
        from app.services.preprocessing.quality_code import map_quality_code

        assert map_quality_code(raw).value == expected

    def test_import_quality_mapping_unit(self):
        """导入侧外部质量码 → 1/0/None（缺失不臆断 Bad；Good 集合={1,192}）."""
        from app.services.data_import import _map_quality

        assert _map_quality(1) == 1
        assert _map_quality(192) == 1  # 192 (OPC DA Good) 在导入侧 Good 集合内（现状登记）
        # OPC UA Good=2 在导入侧映射为 0（与预处理 Good 集合不同，登记差异）
        assert _map_quality(2) == 0
        assert _map_quality(0) == 0
        assert _map_quality(None) is None
        assert _map_quality("x") is None


# ---------------------------------------------------------------------------
# 5. 导入任务对外字段与计数单位
# ---------------------------------------------------------------------------


class TestImportTaskContract:
    def test_task_response_fields(self):
        """对外响应字段固定（frontend 依赖）。"""
        from app.services.data_import import _task_to_response

        data = {
            "task_id": "t1",
            "status": "RUNNING",
            "loop_count": "2",
            "ts_start": "2026-09-06T00:00:00",
            "ts_end": "2026-09-06T01:00:00",
            "interval": "1",
            "conflict_strategy": "skip",
            "imported_count": "3600",
            "error_count": "0",
            "progress": "0.5",
            "created_at": "2026-09-06T00:00:00",
            "updated_at": "2026-09-06T00:00:00",
        }
        resp = _task_to_response(data)
        for key in (
            "taskId",
            "status",
            "loopCount",
            "importedCount",
            "errorCount",
            "progress",
            "tsStart",
            "tsEnd",
            "conflictStrategy",
            "createdAt",
            "triggerBackfill",
            "result",
        ):
            assert key in resp, f"对外字段 {key} 缺失"
        assert resp["importedCount"] == 3600
        assert resp["loopCount"] == 2

    def test_point_count_unit_is_wide_rows(self):
        """『点数』单位=宽表行数（时间槽），不是 7×物理记录数.

        以 _convert_to_wide_rows 行为固定：N 个时间戳 → N 行。
        """
        from app.services.data_import import _convert_to_wide_rows

        raw = (
            ["2026-09-06T00:00:00Z", "2026-09-06T00:00:01Z", "2026-09-06T00:00:02Z"],
            {"TAG_PV": {"values": [1.0, 1.1, 1.2], "qualities": [1, 1, 1]}},
        )
        rows = _convert_to_wide_rows(raw, {"PV": "TAG_PV"})
        assert len(rows) == 3
        assert all(len(r) == 9 for r in rows)


# ---------------------------------------------------------------------------
# 6. DataPlanner 查询函数签名对齐
# ---------------------------------------------------------------------------


class TestDataPlannerContract:
    def test_query_fn_signature_aligns_planner(self):
        """base.QueryFn 与 DataPlanner.TDengineQueryFn 一致（两处独立定义，防漂移）."""
        from app.services.data_planner import TDengineQueryFn
        from app.services.data_source.base import QueryFn

        # 两个 typing 别名的字符串表示须一致（参数类型序列相同）
        assert str(QueryFn) == str(TDengineQueryFn)
