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

    async def fake_execute_sql(sql: str) -> list[dict]:
        captured.append(sql)
        return []

    with patch("app.services.data_integrity.execute_sql", new=fake_execute_sql):
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
