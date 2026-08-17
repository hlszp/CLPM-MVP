"""数据完整性检查服务单元测试（行级判定简化版）.

覆盖：
- ``_parse_dt`` 带时区输入 astimezone(UTC) 后再去 tzinfo（不直接丢弃时区）
- ``_to_utc_z`` SQL 查询边界统一输出带 Z 的 UTC 串
- 桶键 epoch 对齐：REST 返回的 UTC 桶起点与期望枚举桶键一致
- ``_query_loop_bucket`` SQL 边界归一化为 Z 串，使用 COUNT(*) 行级统计
- ``_aggregate`` 端到端桶对齐（+8 偏移输入窗口 × UTC 桶键，回归用例）
- TDengine 故障明确报告 dataSourceUnavailable，不误判缺失
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from app.services.data_integrity import (
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
async def test_query_loop_bucket_sql_uses_count_star_and_utc_bounds() -> None:
    """_query_loop_bucket 使用 COUNT(*) 行级统计，SQL 边界归一化为带 Z 的 UTC 串."""
    import asyncio

    captured: list[str] = []

    async def fake_execute_sql(sql: str, *, raise_on_error: bool = False) -> list[dict]:
        captured.append(sql)
        return [{"bucket_start": "2026-07-28T02:00:00.000Z", "cnt": 3600}]

    with patch("app.services.data_integrity.execute_sql", new=fake_execute_sql):
        result = await _query_loop_bucket(
            asyncio.Semaphore(1),
            "loop-1",
            "d_loop_x",
            "2026-07-28T10:00:00+08:00",
            "2026-07-28T11:00:00+08:00",
        )

    assert len(captured) == 1
    sql = captured[0]
    # 行级判定：使用 COUNT(*) 而非多个 COUNT(col)
    assert "COUNT(*) AS cnt" in sql
    assert "ts >= '2026-07-28T02:00:00.000Z'" in sql
    assert "ts <= '2026-07-28T03:00:00.000Z'" in sql
    assert "INTERVAL(1h)" in sql
    # 返回结构简化：total_rows 替代 col_totals，无 cov_seed
    assert result["total_rows"] == 3600
    assert "cov_seed" not in result
    assert "col_totals" not in result


@pytest.mark.asyncio
async def test_query_loop_bucket_raise_on_error() -> None:
    """_query_loop_bucket 必须以 raise_on_error=True 调用，TDengine故障上抛并携带loop_id."""
    import asyncio

    from app.core.tdengine import TDengineError

    seen_kwargs: list[dict] = []

    async def fake_execute_sql(sql: str, *, raise_on_error: bool = False) -> list[dict]:
        seen_kwargs.append({"raise_on_error": raise_on_error})
        return []

    with patch("app.services.data_integrity.execute_sql", new=fake_execute_sql):
        result = await _query_loop_bucket(
            asyncio.Semaphore(1),
            "loop-1",
            "d_loop_x",
            "2026-07-28T10:00:00+08:00",
            "2026-07-28T11:00:00+08:00",
        )

    assert seen_kwargs == [{"raise_on_error": True}]
    assert result["total_rows"] == 0

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


class TestDataSourceUnavailable:
    """TDengine 故障明确报告'数据源不可用'，不误判为全量缺失."""

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
        ok = {
            "loop_id": "loop-ok",
            "subtable": "d_loop_ok",
            "buckets": [{"bucket_start": "2026-07-28T02:00:00.000Z", "cnt": 3600}],
            "total_rows": 3600,
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


def test_aggregate_full_data_reports_complete() -> None:
    """_aggregate 端到端：+8 偏移输入窗口 × UTC 桶键，数据完整时不应误报缺失（行级COUNT(*)判定）."""
    results = [
        {
            "loop_id": "loop-1",
            "subtable": "d_loop_x",
            "buckets": [{"bucket_start": "2026-07-28T02:00:00.000Z", "cnt": 3600}],
            "total_rows": 3600,
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
    # 行级判定：无 colDetails/missingColumns 字段
    assert "colDetails" not in detail
    assert "missingColumns" not in detail
    assert report["timeGaps"] == []
    assert report["overallCompleteness"] == 1.0


def test_aggregate_partial_data_reports_missing_hours() -> None:
    """行级判定：半数点数 → 完整度 0.5，有小时缺口."""
    results = [
        {
            "loop_id": "loop-1",
            "subtable": "d_loop_x",
            "buckets": [{"bucket_start": "2026-07-28T02:00:00.000Z", "cnt": 1800}],
            "total_rows": 1800,
            "first_ts": "2026-07-28T02:00:00.000Z",
            "last_ts": "2026-07-28T02:00:00.000Z",
        }
    ]
    report = _aggregate(
        results=results,
        loop_ids=["loop-1"],
        tag_name_map={"loop-1": "TIC-101"},
        ts_start="2026-07-28T02:00:00Z",
        ts_end="2026-07-28T03:00:00Z",
        expected_interval_s=1,
    )

    detail = report["loopDetails"][0]
    assert detail["completeness"] == 0.5
    assert detail["status"] == "PARTIAL"
    assert detail["missingHourCount"] == 1
    assert len(report["timeGaps"]) == 1
    assert report["timeGaps"][0]["affectedLoopCount"] == 1


def test_aggregate_no_data_reports_missing() -> None:
    """无数据行 → 完整度0，状态MISSING."""
    results = [
        {
            "loop_id": "loop-1",
            "subtable": "d_loop_x",
            "buckets": [],
            "total_rows": 0,
            "first_ts": None,
            "last_ts": None,
        }
    ]
    report = _aggregate(
        results=results,
        loop_ids=["loop-1"],
        tag_name_map={"loop-1": "TIC-101"},
        ts_start="2026-07-28T02:00:00Z",
        ts_end="2026-07-28T03:00:00Z",
        expected_interval_s=1,
    )

    detail = report["loopDetails"][0]
    assert detail["completeness"] == 0.0
    assert detail["status"] == "MISSING"
    assert detail["missingHourCount"] == 1
