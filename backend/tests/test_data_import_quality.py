"""数据导入质量码映射与行构造单测。

回归背景（2026-08-17 数据链路审查）：
- _map_quality 原实现把「远端未携带质量码」臆断为 Bad(0)，与实时写入口径
 （NULL）不一致——Bad 行会被诊断门禁剔除，导致"导入后反而缺数"；
- 覆盖率反馈：任务 result.loopCoverage 量化每回路 导入点数/预期点数。
"""

from __future__ import annotations

from app.services.data_import import (
    _build_wide_row,
    _convert_to_wide_rows,
    _map_quality,
    _task_to_response,
)


class TestMapQuality:
    def test_known_good_codes(self) -> None:
        assert _map_quality(1) == 1
        assert _map_quality(192) == 1
        assert _map_quality("1") == 1

    def test_bad_codes(self) -> None:
        assert _map_quality(0) == 0
        assert _map_quality(2) == 0
        assert _map_quality(999) == 0

    def test_missing_quality_maps_to_none_not_bad(self) -> None:
        """回归：缺失/不可解析质量码 → None（NULL），不得臆断为 Bad(0)。"""
        assert _map_quality(None) is None
        assert _map_quality("") is None
        assert _map_quality("abc") is None


class TestBuildWideRow:
    def test_quality_shorter_than_values_yields_null(self) -> None:
        """远端 qualities 比 values 短 → 越界索引取 None → pv_quality=None。"""
        role_series = {
            "PV": {"values": [1.0, 2.0, 3.0], "qualities": [1]},  # 只有第 1 点有质量码
        }
        row = _build_wide_row(2, role_series)
        # 列序: pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality
        assert row[0] == 3.0
        assert row[7] is None  # 质量码缺失 → NULL（不再误标 Bad）

    def test_quality_good_mapped(self) -> None:
        role_series = {"PV": {"values": [1.0], "qualities": [1]}}
        assert _build_wide_row(0, role_series)[7] == 1

    def test_no_pv_series_quality_none(self) -> None:
        """无 PV series（远端未返回该 tag）→ pv=None, pv_quality=None。"""
        row = _build_wide_row(0, {})
        assert row[0] is None
        assert row[7] is None


class TestConvertToWideRows:
    def test_ts_parse_and_row_count(self) -> None:
        raw = (
            ["2026-08-17T02:00:00Z", "2026-08-17T02:00:01Z"],
            {"T1.PV": {"values": [10.0, 11.0], "qualities": [1, 1]}},
        )
        rows = _convert_to_wide_rows(raw, {"PV": "T1.PV"})
        assert len(rows) == 2
        # naive 北京墙钟存储（TDengine 服务器按 +8 解释）
        assert rows[0][0] == "2026-08-17 10:00:00.000"
        assert rows[0][1] == 10.0
        assert rows[0][8] == 1


class TestTaskResponse:
    def test_result_json_parsed(self) -> None:
        """任务 result JSON（含 loopCoverage）应解析后透出。"""
        import json

        payload = {
            "total": 2,
            "succeeded": 2,
            "loopCoverage": [{"loopId": "a", "coverage": 0.5}],
        }
        data = {"task_id": "t1", "status": "SUCCESS", "result": json.dumps(payload)}
        resp = _task_to_response(data)
        assert resp["result"]["loopCoverage"][0]["coverage"] == 0.5

    def test_result_invalid_json_falls_back_none(self) -> None:
        resp = _task_to_response({"task_id": "t1", "result": "not-json"})
        assert resp["result"] is None
