"""数据导入质量码映射与行构造单测。

回归背景（2026-08-17 数据链路审查）：
- _map_quality 原实现把「远端未携带质量码」臆断为 Bad(0)，与实时写入口径
 （NULL）不一致——Bad 行会被诊断门禁剔除，导致"导入后反而缺数"；
- 导入结果收敛为 total/succeeded/failed/errors，不再量化覆盖率（远端 COV
  稀疏，按理想网格点数做分母会系统性误报缺口）；
- 算法 v2（2026-09-09）：角色频率分层——COV 去重、StepFill 阶跃前向填充、
  宽表低频列填充口径。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.data_import import (
    _LOW_FREQ_ANCHOR_SECONDS,
    _build_wide_row,
    _convert_to_wide_rows,
    _cov_dedup_points,
    _map_quality,
    _split_role_frequencies,
    _StepFill,
    _task_to_response,
)


def _utc(minutes: float) -> datetime:
    return datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=minutes)


class TestRoleFrequencySplit:
    def test_split(self) -> None:
        role_map = {"PV": "a_PV", "OP": "a_OP", "SP": "a_SP", "MODE": "a_MODE"}
        high, low = _split_role_frequencies(role_map)
        assert high == {"PV": "a_PV", "OP": "a_OP"}
        assert low == {"SP": "a_SP", "MODE": "a_MODE"}


class TestCovDedup:
    def test_keeps_changes_and_anchors(self) -> None:
        """稳定段只留锚点；变化点全留；重复值不落。"""
        # 0-30min 稳定值 1.0（60s 步进采样）→ 仅首点 + 10min 锚点
        samples = [(_utc(m), 1.0, 1, 1) for m in range(0, 31)]
        # 31min 起变为 2.0（两个采样点）
        samples += [(_utc(31), 2.0, 1, 1), (_utc(32), 2.0, 1, 1)]
        out = _cov_dedup_points(samples, anchor_seconds=_LOW_FREQ_ANCHOR_SECONDS)
        ts_list = [t for t, *_ in out]
        # 首点 + 10/20/30min 锚点 + 31min 变化点（32min 重复值不落）
        assert ts_list == [_utc(0), _utc(10), _utc(20), _utc(30), _utc(31)]

    def test_quality_change_counts_as_change(self) -> None:
        """值不变但质量退化（Good→Bad）也落点（读取侧质量曲线需要）。"""
        samples = [(_utc(0), 1.0, 1, 1), (_utc(1), 1.0, 0, 0), (_utc(2), 1.0, 0, 0)]
        out = _cov_dedup_points(samples)
        assert len(out) == 2

    def test_none_value_is_change(self) -> None:
        """无效值（None）↔ 数值互转视为变化。"""
        samples = [(_utc(0), 1.0, 1, 1), (_utc(1), None, 1, 1)]
        out = _cov_dedup_points(samples)
        assert len(out) == 2


class TestStepFill:
    def test_forward_fill_and_pre_window_none(self) -> None:
        fill = _StepFill([(_utc(0), 1.0, 1, 1), (_utc(10), 2.0, 1, 1)])
        assert fill.value_at(_utc(-1)) is None  # 窗口前无样本
        assert fill.value_at(_utc(0)) == 1.0
        assert fill.value_at(_utc(9)) == 1.0  # 前向保持
        assert fill.value_at(_utc(10)) == 2.0
        assert fill.value_at(_utc(99)) == 2.0  # 超出末样本保持末值

    def test_monotonic_cursor_amortized(self) -> None:
        """升序查询游标推进，重复查询同值。"""
        fill = _StepFill([(_utc(0), 1.0, 1, 1), (_utc(5), 2.0, 1, 1)])
        assert [fill.value_at(_utc(m)) for m in range(0, 10)] == [
            1.0,
            1.0,
            1.0,
            1.0,
            1.0,
            2.0,
            2.0,
            2.0,
            2.0,
            2.0,
        ]


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

    def test_low_fills_forward_fill(self) -> None:
        """v2：低频列（SP/MODE/PID）由 StepFill 按 ts 前向填充，高频列取 series。

        fill 样本 01:59(Z) sp=5.0、02:00:01(Z) sp=6.0 →
        第 1 行（02:00:00Z）前向取 5.0，第 2 行（02:00:01Z）取 6.0。
        """
        raw = (
            ["2026-08-17T02:00:00Z", "2026-08-17T02:00:01Z"],
            {"T1.PV": {"values": [10.0, 11.0], "qualities": [1, 1]}},
        )
        fill = _StepFill(
            [
                (datetime(2026, 8, 17, 1, 59, 0, tzinfo=UTC), 5.0, 1, 1),
                (datetime(2026, 8, 17, 2, 0, 1, tzinfo=UTC), 6.0, 1, 1),
            ]
        )
        rows = _convert_to_wide_rows(raw, {"PV": "T1.PV"}, {"SP": fill})
        assert rows[0][2] == 5.0  # sp 前向取 01:59 样本
        assert rows[1][2] == 6.0  # sp 命中 02:00:01 变化点
        assert rows[0][1] == 10.0  # pv 仍取高频 series
        assert rows[1][1] == 11.0


class TestTaskResponse:
    def test_result_json_parsed(self) -> None:
        """任务 result JSON 应解析后透出。"""
        import json

        payload = {
            "total": 2,
            "succeeded": 2,
            "failed": 0,
            "errors": ["loop x: 无有效 tag 映射"],
        }
        data = {"task_id": "t1", "status": "SUCCESS", "result": json.dumps(payload)}
        resp = _task_to_response(data)
        assert resp["result"]["succeeded"] == 2
        assert resp["result"]["errors"] == ["loop x: 无有效 tag 映射"]

    def test_result_invalid_json_falls_back_none(self) -> None:
        resp = _task_to_response({"task_id": "t1", "result": "not-json"})
        assert resp["result"] is None
