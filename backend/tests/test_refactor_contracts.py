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

import pytest

from app.services.data_source.base import HistoryDataProvider
from app.services.data_source.factory import get_provider
from app.services.data_source.tdengine_provider import (
    TDengineProvider,
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
# ---------------------------------------------------------------------------
# 3. 时间口径（查询边界 / 存储时区 / 区间语义）
# ---------------------------------------------------------------------------
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
