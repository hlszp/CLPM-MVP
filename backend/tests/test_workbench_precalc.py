"""workbench_precalc M2 纯函数单测（无 DB）。

覆盖：floor_grid 网格对齐、score_to_status 分档、aggregate_rows 加权聚合
（含 NULL 指标独立分母）、build_trend_points 桶聚合、build_metric_slopes
方向判定、shape_level_dist 分桶映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.workbench_precalc import (
    aggregate_rows,
    build_metric_slopes,
    build_trend_points,
    floor_grid,
    score_to_status,
    shape_level_dist,
)


@dataclass
class FakeRow:
    """UnitKpiSummary 测试替身（率值 0-100 标度）。"""

    avg_score: float | None
    evaluated_loops: int
    snapshot_time: datetime
    auto_mode_rate: float | None = None
    effective_auto_rate: float | None = None
    stability_rate: float | None = None
    accuracy_rate: float | None = None
    fast_rate: float | None = None
    good_value_rate: float | None = None
    oscillation_rate: float | None = None
    saturation_rate: float | None = None
    instrument_fault_rate: float | None = None


class TestFloorGrid:
    def test_aligns_to_5min(self) -> None:
        # 输出恒为 naive UTC（DB 存储口径），aware 输入也被归一
        dt = datetime(2026, 9, 4, 12, 7, 33, tzinfo=UTC)
        assert floor_grid(dt) == datetime(2026, 9, 4, 12, 5, 0)

    def test_naive_utc_input(self) -> None:
        # naive 时间按 UTC 解释（DB 口径），不受机器时区影响
        dt = datetime(2026, 9, 4, 23, 59, 59)
        assert floor_grid(dt) == datetime(2026, 9, 4, 23, 55, 0)

    def test_idempotent(self) -> None:
        dt = datetime(2026, 9, 4, 12, 10, 0)
        assert floor_grid(floor_grid(dt)) == floor_grid(dt)


class TestScoreToStatus:
    def test_none_is_inconclusive(self) -> None:
        assert score_to_status(None) == "INCONCLUSIVE"

    def test_tiers(self) -> None:
        assert score_to_status(95) == "EXCELLENT"
        assert score_to_status(90) == "EXCELLENT"
        assert score_to_status(75) == "GOOD"
        assert score_to_status(60) == "FAIR"
        assert score_to_status(40) == "POOR"
        assert score_to_status(0) == "CRITICAL"


_T0 = datetime(2026, 9, 4, 12)


def _row(
    score: float | None,
    loops: int,
    hour: int = 12,
    auto: float | None = None,
    stability: float | None = None,
) -> FakeRow:
    return FakeRow(
        avg_score=score,
        evaluated_loops=loops,
        snapshot_time=datetime(2026, 9, 4, hour),
        auto_mode_rate=auto,
        stability_rate=stability,
    )


class TestAggregateRows:
    def test_empty_rows(self) -> None:
        agg = aggregate_rows([])
        assert agg["score"] is None
        assert agg["loop_count"] == 0
        assert all(v is None for v in agg["rates"].values())

    def test_weighted_average(self) -> None:
        rows = [_row(80, 3, auto=90), _row(90, 1, hour=13, auto=70)]
        agg = aggregate_rows(rows)
        # (80*3 + 90*1) / 4 = 82.5
        assert agg["score"] == 82.5
        assert agg["loop_count"] == 4
        # 自控率：(90*3 + 70*1)/4 = 85 → 0.85
        assert agg["rates"]["auto_mode_rate"] == 0.85

    def test_null_metric_independent_denominator(self) -> None:
        # 指标 NULL 的行不进入该指标分母，但 score 分母仍计入
        rows = [_row(80, 2, auto=None), _row(90, 2, auto=60)]
        agg = aggregate_rows(rows)
        assert agg["score"] == 85.0
        assert agg["loop_count"] == 4
        # 自控率仅第二行参与：60 → 0.6
        assert agg["rates"]["auto_mode_rate"] == 0.6

    def test_stability_maps_to_steady(self) -> None:
        agg = aggregate_rows([_row(80, 1, stability=88.8)])
        assert agg["rates"]["steady_rate"] == 0.888


class TestBuildTrendPoints:
    def test_hourly_buckets_24h(self) -> None:
        base = datetime(2026, 9, 4, 12, 0)
        rows = [
            FakeRow(avg_score=80, evaluated_loops=1, snapshot_time=base),
            FakeRow(avg_score=82, evaluated_loops=1, snapshot_time=base + timedelta(minutes=30)),
            FakeRow(avg_score=84, evaluated_loops=1, snapshot_time=base + timedelta(hours=1)),
        ]
        pts = build_trend_points(rows, "24h")
        assert len(pts) == 2  # 两个小时桶
        assert pts[0]["v"] == 81.0  # (80+82)/2
        assert pts[1]["v"] == 84.0

    def test_skips_null_score(self) -> None:
        rows = [
            FakeRow(avg_score=None, evaluated_loops=1, snapshot_time=datetime(2026, 9, 4, 12)),
        ]
        assert build_trend_points(rows, "24h") == []


class TestBuildMetricSlopes:
    def _agg(self, good: float, fault: float) -> dict[str, Any]:
        return {
            "score": 80,
            "loop_count": 1,
            "rates": {
                "good_value_rate": good / 100,
                "instrument_fault_rate": fault / 100,
            },
        }

    def test_direction(self) -> None:
        slopes = build_metric_slopes(self._agg(95, 3), self._agg(90, 5))
        by_metric = {s["metric"]: s for s in slopes}
        # 好值率上升 → good
        assert by_metric["好值率"]["delta"] == 5.0
        assert by_metric["好值率"]["direction"] == "good"
        # 故障率下降（反向指标）→ good
        assert by_metric["仪表故障率"]["delta"] == -2.0
        assert by_metric["仪表故障率"]["direction"] == "good"

    def test_missing_metric_skipped(self) -> None:
        cur = {"score": 80, "loop_count": 1, "rates": {"good_value_rate": 0.9}}
        prev = {"score": 80, "loop_count": 1, "rates": {}}
        assert build_metric_slopes(cur, prev) == []


class TestShapeLevelDist:
    def test_bucket_mapping(self) -> None:
        dist = {"EXCELLENT": 5, "GOOD": 4, "FAIR": 3, "WARNING": 2, "POOR": 1, "INCONCLUSIVE": 2}
        out = shape_level_dist(dist)
        by_label = {d["label"]: d["count"] for d in out}
        assert by_label["优（≥90）"] == 5
        assert by_label["良（75–90）"] == 4
        assert by_label["中（60–75）"] == 3
        assert by_label["差（<60）"] == 3  # WARNING + POOR
        assert by_label["不可评"] == 2

    def test_none_input(self) -> None:
        out = shape_level_dist(None)
        assert all(d["count"] == 0 for d in out)
        assert len(out) == 5
