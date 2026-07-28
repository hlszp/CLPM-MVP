"""时间戳 UTC 口径与时区无关性测试（Phase 1 整改：热路径时间戳修复）.

覆盖：
- kpi_calc._ts_to_float / _build_ts_index：naive datetime/ISO 字符串按 UTC 处理，
  _build_ts_index datetime 快速路径（首末点换算 + 差分向量化）结果与逐点换算一致
- trend_service._ts_to_millis / monitor._ts_to_ms：naive 输入补 Z 视为 UTC，
  返回前端的毫秒时间戳与进程 TZ 无关

设计依据：项目红线（热路径禁止 naive .timestamp() 本地时区慢路径）；
返回前端的时间戳不得随后端部署时区偏移。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from app.services.monitor import _ts_to_ms
from app.services.trend_service import _ts_to_millis
from app.tasks.kpi_calc import _build_ts_index, _ts_to_float

NAIVE_ISO = "2026-07-01T12:00:00"
AWARE_ISO = "2026-07-01T12:00:00Z"
NAIVE_DT = datetime(2026, 7, 1, 12, 0, 0)
AWARE_DT = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


@contextmanager
def process_tz(tz: str) -> Iterator[None]:
    """临时切换进程时区（验证返回值与部署 TZ 无关）。"""
    orig = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if orig is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = orig
        time.tzset()


# ---------------------------------------------------------------------------
# kpi_calc._ts_to_float
# ---------------------------------------------------------------------------


class TestTsToFloatUtc:
    """naive 输入按 UTC 处理（补 Z 口径）。"""

    def test_naive_datetime_treated_as_utc(self) -> None:
        """naive datetime 与 UTC aware datetime 结果一致。"""
        assert _ts_to_float(NAIVE_DT) == _ts_to_float(AWARE_DT)

    def test_naive_iso_string_treated_as_utc(self) -> None:
        """无时区 ISO 字符串与带 Z 字符串结果一致。"""
        assert _ts_to_float(NAIVE_ISO) == _ts_to_float(AWARE_ISO)

    def test_naive_datetime_tz_independent(self) -> None:
        """naive datetime 换算结果与进程 TZ 无关。"""
        results = []
        for tz in ("UTC", "Asia/Shanghai", "America/New_York"):
            with process_tz(tz):
                results.append(_ts_to_float(NAIVE_DT))
        assert len(set(results)) == 1


# ---------------------------------------------------------------------------
# kpi_calc._build_ts_index
# ---------------------------------------------------------------------------


class TestBuildTsIndexVectorized:
    """datetime 快速路径（首点换算 + 差分向量化）。"""

    def test_datetime_fast_path_sorted(self) -> None:
        """全 datetime 输入：排序正确且数值与逐点 _ts_to_float 一致。"""
        dts = [
            datetime(2026, 7, 1, 12, 0, 2, tzinfo=UTC),
            datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC),
            datetime(2026, 7, 1, 12, 0, 1, tzinfo=UTC),
        ]
        data = [{"ts": dt, "value": float(i)} for i, dt in enumerate(dts)]
        ts_floats, ts_orig = _build_ts_index(data)
        assert ts_floats == sorted(ts_floats)
        assert ts_orig == sorted(dts)
        # 快速路径结果与逐点换算一致
        assert ts_floats == [_ts_to_float(dt) for dt in sorted(dts)]

    def test_naive_datetime_fast_path_tz_independent(self) -> None:
        """naive datetime 快速路径结果与进程 TZ 无关。"""
        dts = [datetime(2026, 7, 1, 12, 0, i) for i in range(3)]
        data = [{"ts": dt, "value": 1.0} for dt in dts]
        results = []
        for tz in ("UTC", "Asia/Shanghai"):
            with process_tz(tz):
                ts_floats, _ = _build_ts_index(data)
                results.append(tuple(ts_floats))
        assert results[0] == results[1]

    def test_mixed_types_fallback(self) -> None:
        """混合类型（datetime + 数值字符串）走通用路径，结果正确。"""
        data = [
            {"ts": datetime(2026, 7, 1, 12, 0, 1, tzinfo=UTC), "value": 1.0},
            {"ts": "1700000000", "value": 2.0},
        ]
        ts_floats, ts_orig = _build_ts_index(data)
        assert len(ts_floats) == 2
        assert 1700000000.0 in ts_floats

    def test_unconvertible_still_degrades(self) -> None:
        """任意 ts 无法转数值时返回空列表（退化为精确匹配）。"""
        data = [{"ts": "t1", "value": 1.0}, {"ts": "t2", "value": 2.0}]
        ts_floats, ts_orig = _build_ts_index(data)
        assert ts_floats == []
        assert ts_orig == []


# ---------------------------------------------------------------------------
# trend_service._ts_to_millis
# ---------------------------------------------------------------------------


class TestTrendTsToMillisUtc:
    """趋势接口毫秒时间戳与部署时区无关。"""

    def test_naive_string_equals_z_string(self) -> None:
        """无时区 ISO 字符串按 UTC 处理，与带 Z 字符串一致。"""
        assert _ts_to_millis(NAIVE_ISO) == _ts_to_millis(AWARE_ISO)

    def test_naive_datetime_equals_utc_aware(self) -> None:
        """naive datetime 按 UTC 处理，与 UTC aware datetime 一致。"""
        assert _ts_to_millis(NAIVE_DT) == _ts_to_millis(AWARE_DT)

    def test_millis_tz_independent(self) -> None:
        """同一 naive 输入在不同进程 TZ 下返回相同毫秒值。"""
        results = []
        for tz in ("UTC", "Asia/Shanghai", "America/New_York"):
            with process_tz(tz):
                results.append(_ts_to_millis(NAIVE_ISO))
                results.append(_ts_to_millis(NAIVE_DT))
        assert len(set(results)) == 1


# ---------------------------------------------------------------------------
# monitor._ts_to_ms
# ---------------------------------------------------------------------------


class TestMonitorTsToMsUtc:
    """监控接口毫秒时间戳与部署时区无关。"""

    def test_naive_string_equals_z_string(self) -> None:
        """无时区 ISO 字符串按 UTC 处理，与带 Z 字符串一致。"""
        assert _ts_to_ms(NAIVE_ISO) == _ts_to_ms(AWARE_ISO)

    def test_naive_datetime_equals_utc_aware(self) -> None:
        """naive datetime 按 UTC 处理，与 UTC aware datetime 一致。"""
        assert _ts_to_ms(NAIVE_DT) == _ts_to_ms(AWARE_DT)

    def test_ms_tz_independent(self) -> None:
        """同一 naive 输入在不同进程 TZ 下返回相同毫秒值。"""
        results = []
        for tz in ("UTC", "Asia/Shanghai", "America/New_York"):
            with process_tz(tz):
                results.append(_ts_to_ms(NAIVE_ISO))
                results.append(_ts_to_ms(NAIVE_DT))
        assert len(set(results)) == 1
