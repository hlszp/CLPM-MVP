"""数据完整性检查服务单元测试（P0-3 时区修复）.

覆盖：
- ``_parse_dt`` 带时区输入 astimezone(UTC) 后再去 tzinfo（不直接丢弃时区）
- ``_to_utc_z`` SQL 查询边界统一输出带 Z 的 UTC 串
- 桶键 epoch 对齐：REST 返回的 UTC 桶起点与期望枚举桶键一致
- ``_query_loop_bucket`` SQL 边界归一化为 Z 串
- ``_aggregate`` 端到端桶对齐（+8 偏移输入窗口 × UTC 桶键，回归用例）
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from app.services.data_integrity import (
    _DATA_COLUMNS,
    _aggregate,
    _enumerate_hour_buckets_with_expected,
    _normalize_bucket_key,
    _parse_bucket_str,
    _parse_dt,
    _query_loop_bucket,
    _to_utc_z,
)


class TestParseDt:
    """_parse_dt：显式时区对齐，naive 视为 UTC."""

    def test_naive_treated_as_utc(self) -> None:
        assert _parse_dt("2026-07-28T02:00:00") == datetime(2026, 7, 28, 2, 0, 0)

    def test_z_suffix_keeps_utc_wallclock(self) -> None:
        assert _parse_dt("2026-07-28T02:00:00Z") == datetime(2026, 7, 28, 2, 0, 0)

    def test_plus8_offset_converts_to_utc(self) -> None:
        """+8 墙钟 10:00 应转为 UTC 02:00，而非丢弃时区保留 10:00."""
        assert _parse_dt("2026-07-28T10:00:00+08:00") == datetime(2026, 7, 28, 2, 0, 0)

    def test_zero_offset_equivalent_to_z(self) -> None:
        assert _parse_dt("2026-07-28T02:00:00+00:00") == datetime(2026, 7, 28, 2, 0, 0)


class TestToUtcZ:
    """_to_utc_z：SQL 查询边界归一化."""

    def test_naive_input(self) -> None:
        assert _to_utc_z("2026-07-28 02:00:00") == "2026-07-28T02:00:00.000Z"

    def test_plus8_input(self) -> None:
        assert _to_utc_z("2026-07-28T10:00:00+08:00") == "2026-07-28T02:00:00.000Z"

    def test_z_input_passthrough(self) -> None:
        assert _to_utc_z("2026-07-28T02:00:00.000Z") == "2026-07-28T02:00:00.000Z"


class TestBucketKeyAlignment:
    """桶键 epoch 对齐：REST 返回 UTC 桶起点 × 期望枚举桶键."""

    def test_parse_bucket_str_z(self) -> None:
        assert _parse_bucket_str("2026-07-28T02:00:00.000Z") == datetime(2026, 7, 28, 2, 0, 0)

    def test_parse_bucket_str_offset(self) -> None:
        assert _parse_bucket_str("2026-07-28T10:00:00+08:00") == datetime(2026, 7, 28, 2, 0, 0)

    def test_parse_bucket_str_naive(self) -> None:
        assert _parse_bucket_str("2026-07-28 02:00:00") == datetime(2026, 7, 28, 2, 0, 0)

    def test_rest_bucket_key_matches_enumerated_key(self) -> None:
        """REST 返回的 UTC 桶起点归一化后必须命中期望枚举桶键.

        回归用例：修复前 _parse_dt 直接丢弃 tz，+8 偏移窗口枚举出的
        期望键（'10:00:00'）与 REST 返回的 UTC 桶键（'02:00:00'）错位
        8 小时，所有桶被误判缺失。
        """
        start_dt = _parse_dt("2026-07-28T10:00:00+08:00")
        end_dt = _parse_dt("2026-07-28T11:00:00+08:00")
        expected_buckets = _enumerate_hour_buckets_with_expected(start_dt, end_dt, 1)
        assert [b[0] for b in expected_buckets] == ["2026-07-28 02:00:00"]
        assert expected_buckets[0][1] == 3600

        rest_bucket_key = _normalize_bucket_key("2026-07-28T02:00:00.000Z")
        assert rest_bucket_key == expected_buckets[0][0]


@pytest.mark.asyncio
async def test_query_loop_bucket_sql_uses_utc_z_bounds() -> None:
    """_query_loop_bucket 的 SQL 边界必须归一化为带 Z 的 UTC 串.

    修复前直接拼接 naive 输入，服务器按 +8 解释导致过滤窗口偏移 8 小时。
    """
    import asyncio

    captured: list[str] = []

    async def fake_execute_sql(sql: str, *, raise_on_error: bool = False) -> list[dict]:
        captured.append(sql)
        return []

    async def fake_seed(subtable: str, start_time: str) -> dict:
        return {}

    with (
        patch("app.services.data_integrity.execute_sql", new=fake_execute_sql),
        patch("app.services.data_integrity.query_last_values_before", new=fake_seed),
    ):
        await _query_loop_bucket(
            asyncio.Semaphore(1),
            "loop-1",
            "d_loop_x",
            "2026-07-28T10:00:00+08:00",
            "2026-07-28T11:00:00+08:00",
        )

    assert len(captured) == 1
    assert "ts >= '2026-07-28T02:00:00.000Z'" in captured[0]
    assert "ts <= '2026-07-28T03:00:00.000Z'" in captured[0]


@pytest.mark.asyncio
async def test_query_loop_bucket_raise_on_error_and_cov_seed() -> None:
    """_query_loop_bucket 必须以 raise_on_error=True 调用，并带回 COV 初始值.

    TDengine 故障时抛 TDengineError（携带 loop_id），不降级为空结果误判缺失。
    """
    import asyncio

    from app.core.tdengine import TDengineError

    seen_kwargs: list[dict] = []

    async def fake_execute_sql(sql: str, *, raise_on_error: bool = False) -> list[dict]:
        seen_kwargs.append({"raise_on_error": raise_on_error})
        return []

    async def fake_seed(subtable: str, start_time: str) -> dict:
        assert start_time == "2026-07-28T02:00:00.000Z"
        return {"sp": 50.0, "mode": 1}

    with (
        patch("app.services.data_integrity.execute_sql", new=fake_execute_sql),
        patch("app.services.data_integrity.query_last_values_before", new=fake_seed),
    ):
        result = await _query_loop_bucket(
            asyncio.Semaphore(1),
            "loop-1",
            "d_loop_x",
            "2026-07-28T10:00:00+08:00",
            "2026-07-28T11:00:00+08:00",
        )

    assert seen_kwargs == [{"raise_on_error": True}]
    assert result["cov_seed"] == {"sp": 50.0, "mode": 1}

    # TDengine 故障：异常上抛且携带 loop_id（供聚合层报告）
    async def failing_execute_sql(sql: str, *, raise_on_error: bool = False) -> list[dict]:
        raise TDengineError("connection refused")

    with patch("app.services.data_integrity.execute_sql", new=failing_execute_sql):
        with pytest.raises(TDengineError) as exc_info:
            await _query_loop_bucket(
                asyncio.Semaphore(1),
                "loop-1",
                "d_loop_x",
                "2026-07-28T10:00:00+08:00",
                "2026-07-28T11:00:00+08:00",
            )
    assert exc_info.value.loop_id == "loop-1"


class TestCovColumnJudgement:
    """P1：COV 列（sp/mode/pid_*）稀疏存储口径.

    窗口起点前有值或窗口内有变化点即视为连续有值，不按点数判定；
    仅 PV/OP 按点数判定缺失。
    """

    @staticmethod
    def _make_result(
        pv_cnt: int,
        op_cnt: int,
        cov_cnt: int,
        cov_seed: dict,
    ) -> dict:
        col_totals = {"pv": pv_cnt, "op": op_cnt} | dict.fromkeys(
            ["sp", "mode", "pid_p", "pid_i", "pid_d"], cov_cnt
        )
        bucket_row = {"bucket_start": "2026-07-28T02:00:00.000Z"} | {
            f"cnt_{c}": v for c, v in col_totals.items()
        }
        return {
            "loop_id": "loop-1",
            "subtable": "d_loop_x",
            "buckets": [bucket_row],
            "col_totals": col_totals,
            "cov_seed": cov_seed,
            "first_ts": "2026-07-28T02:00:00.000Z",
            "last_ts": "2026-07-28T02:00:00.000Z",
        }

    _WINDOW = {
        "ts_start": "2026-07-28T02:00:00Z",
        "ts_end": "2026-07-28T03:00:00Z",
        "expected_interval_s": 1,
    }

    def _run(self, result: dict) -> dict:
        return _aggregate(
            results=[result],
            loop_ids=["loop-1"],
            tag_name_map={"loop-1": "TIC-101"},
            **self._WINDOW,
        )

    def test_cov_seed_present_not_reported_missing(self) -> None:
        """COV 列窗口内 0 变化点但窗口前有初始值 → 视为连续有值，不报缺失."""
        seed = dict.fromkeys(["sp", "mode", "pid_p", "pid_i", "pid_d"], 1.0)
        report = self._run(self._make_result(3600, 3600, 0, seed))

        detail = report["loopDetails"][0]
        assert detail["completeness"] == 1.0
        assert detail["status"] == "COMPLETE"
        assert detail["missingHourCount"] == 0
        assert detail["missingColumns"] == []
        for col in ("sp", "mode", "pid_p", "pid_i", "pid_d"):
            assert detail["colDetails"][col]["completeness"] == 1.0
        assert report["overallCompleteness"] == 1.0
        assert report["dataSourceUnavailable"] is False

    def test_cov_change_points_in_window_not_missing(self) -> None:
        """COV 列窗口内有变化点（>0）即使无初始值 → 视为有值，不报缺失."""
        report = self._run(self._make_result(3600, 3600, 5, {}))

        detail = report["loopDetails"][0]
        assert detail["completeness"] == 1.0
        assert detail["missingColumns"] == []

    def test_cov_no_seed_no_changes_reported_missing(self) -> None:
        """COV 列窗口前无初始值且窗口内 0 变化点 → 判缺失."""
        report = self._run(self._make_result(3600, 3600, 0, {}))

        detail = report["loopDetails"][0]
        cov_cols = ("sp", "mode", "pid_p", "pid_i", "pid_d")
        for col in cov_cols:
            assert detail["colDetails"][col]["completeness"] == 0.0
        assert sorted(detail["missingColumns"]) == sorted(cov_cols)
        # 回路完整度 = PV+OP 实际 / 7 列预期 = 7200 / 25200
        assert detail["completeness"] == pytest.approx(round(7200 / 25200, 4))
        # PV/OP 点数满 → 无小时级缺口（COV 不参与桶级判定）
        assert detail["missingHourCount"] == 0
        assert report["timeGaps"] == []

    def test_pv_op_still_judged_by_point_count(self) -> None:
        """PV/OP 高频连续量仍按点数判定：半数点 → 完整度 0.5 + 小时缺口."""
        seed = dict.fromkeys(["sp", "mode", "pid_p", "pid_i", "pid_d"], 1.0)
        report = self._run(self._make_result(1800, 3600, 0, seed))

        detail = report["loopDetails"][0]
        assert detail["colDetails"]["pv"]["completeness"] == 0.5
        assert detail["colDetails"]["op"]["completeness"] == 1.0
        assert "pv" in detail["missingColumns"]
        assert detail["missingHourCount"] == 1
        assert len(report["timeGaps"]) == 1


class TestDataSourceUnavailable:
    """P2：TDengine 故障明确报告'数据源不可用'，不误判为全量缺失."""

    def test_tdengine_error_not_reported_as_missing(self) -> None:
        from app.core.tdengine import TDengineError

        err = TDengineError("connection refused")
        err.loop_id = "loop-1"

        report = _aggregate(
            results=[err],
            loop_ids=["loop-1"],
            tag_name_map={"loop-1": "TIC-101"},
            ts_start="2026-07-28T02:00:00Z",
            ts_end="2026-07-28T03:00:00Z",
            expected_interval_s=1,
        )

        assert report["dataSourceUnavailable"] is True
        assert report["failedLoopIds"] == ["loop-1"]
        # 故障回路不进入缺失统计
        assert report["loopDetails"] == []
        assert report["missingLoopCount"] == 0
        assert report["timeGaps"] == []

    def test_mixed_success_and_failure(self) -> None:
        """部分回路故障：正常回路照常判定，故障回路仅记入 failedLoopIds."""
        from app.core.tdengine import TDengineError

        err = TDengineError("timeout")
        err.loop_id = "loop-bad"
        full = dict.fromkeys(_DATA_COLUMNS, 3600)
        ok = {
            "loop_id": "loop-ok",
            "subtable": "d_loop_ok",
            "buckets": [
                {"bucket_start": "2026-07-28T02:00:00.000Z"}
                | {f"cnt_{c}": 3600 for c in _DATA_COLUMNS}
            ],
            "col_totals": full,
            "cov_seed": {},
            "first_ts": "2026-07-28T02:00:00.000Z",
            "last_ts": "2026-07-28T02:00:00.000Z",
        }

        report = _aggregate(
            results=[err, ok],
            loop_ids=["loop-bad", "loop-ok"],
            tag_name_map={"loop-bad": "TIC-102", "loop-ok": "TIC-101"},
            ts_start="2026-07-28T02:00:00Z",
            ts_end="2026-07-28T03:00:00Z",
            expected_interval_s=1,
        )

        assert report["dataSourceUnavailable"] is True
        assert report["failedLoopIds"] == ["loop-bad"]
        assert report["loopCount"] == 1
        assert report["overallCompleteness"] == 1.0


def test_aggregate_aligns_plus8_window_with_utc_buckets() -> None:
    """_aggregate 端到端：+8 偏移输入窗口 × REST UTC 桶键，数据完整时不应误报缺失."""
    full = dict.fromkeys(_DATA_COLUMNS, 3600)
    bucket_row = {"bucket_start": "2026-07-28T02:00:00.000Z"} | {
        f"cnt_{c}": 3600 for c in _DATA_COLUMNS
    }
    results = [
        {
            "loop_id": "loop-1",
            "subtable": "d_loop_x",
            "buckets": [bucket_row],
            "col_totals": full,
            "first_ts": "2026-07-28T02:00:00.000Z",
            "last_ts": "2026-07-28T02:00:00.000Z",
        }
    ]
    report = _aggregate(
        results=results,
        loop_ids=["loop-1"],
        tag_name_map={"loop-1": "TIC-101"},
        ts_start="2026-07-28T10:00:00+08:00",
        ts_end="2026-07-28T11:00:00+08:00",
        expected_interval_s=1,
    )

    detail = report["loopDetails"][0]
    assert detail["missingHourCount"] == 0
    assert detail["completeness"] == 1.0
    assert detail["status"] == "COMPLETE"
    assert report["timeGaps"] == []
    assert report["overallCompleteness"] == 1.0
