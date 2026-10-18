"""Pure CPU changes preserve the existing time/coverage contract."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.contracts import series_context as sc
from app.services.metric_calculator import time_axis as axis

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ts(seconds, zone=UTC):
    value = T0 + timedelta(seconds=seconds)
    return value.replace(tzinfo=None) if zone is None else value.astimezone(zone)


def bundle(
    offsets,
    *,
    zone=UTC,
    signals=None,
    validity=None,
    coverage=None,
    boundaries=(),
    window=(0, 3),
    period=1,
):
    signals = signals if signals is not None else {"pv": [1.0] * len(offsets)}
    validity = (
        validity
        if validity is not None
        else {f"{role}_valid": [True] * len(values) for role, values in signals.items()}
    )
    context = sc.SeriesContext(
        layout="point",
        grid_start=ts(0),
        grid_end=ts(3),
        grid_period_s=period,
        role_coverage=coverage or {},
        segment_boundaries=[sc.SegmentBoundary(ts(at), "rebind") for at in boundaries],
    )
    block = SimpleNamespace(
        timestamps=[ts(i, zone) for i in offsets],
        signals=signals,
        validity=validity,
        series_context=context,
        query_window_start=ts(window[0], zone),
        query_window_end=ts(window[1], zone),
    )
    return SimpleNamespace(data_block=block)


@pytest.mark.parametrize("period", [1, 2, 5, 10])
@pytest.mark.parametrize("zone", [None, UTC, timezone(timedelta(hours=8))])
def test_complete_view_and_ratio_across_grids_and_timezones(period, zone):
    cov = sc.RoleCoverage(
        known=[sc.IntervalRun(ts(0), ts(4 * period))],
        observed=[sc.IntervalRun(ts(0), ts(period))],
        unknown=[sc.IntervalRun(ts(2 * period), ts(3 * period), "gap")],
    )
    value = bundle(
        [i * period for i in range(4)],
        zone=zone,
        period=period,
        window=(0, 4 * period - 1),
        coverage={"pv": cov, "sp": cov},
        signals={"pv": [1, None, 3, 4], "sp": [1, 2, 3, 4]},
        validity={"pv_valid": [True, True, True, False], "sp_valid": [True] * 4},
    )
    view = axis.build_metric_time_view(value, required_roles=("pv", "sp"))
    assert view is not None
    assert asdict(view) == {
        "window_start": ts(0),
        "window_end": ts(4 * period),
        "grid_period_s": float(period),
        "indices": [0, 1, 2, 3],
        "slot_starts": [ts(i * period) for i in range(4)],
        "slot_seconds": [float(period)] * 4,
        "uncovered_window_seconds": 0.0,
        "role_states": {
            "pv": [
                axis.SlotState.OBSERVED,
                axis.SlotState.UNKNOWN,
                axis.SlotState.UNKNOWN,
                axis.SlotState.HELD,
            ],
            "sp": [
                axis.SlotState.OBSERVED,
                axis.SlotState.HELD,
                axis.SlotState.UNKNOWN,
                axis.SlotState.HELD,
            ],
        },
        "role_unknown_reasons": {
            "pv": [None, "no_value", "gap", None],
            "sp": [None, None, "gap", None],
        },
        "role_numerically_valid": {"pv": [True, True, True, False], "sp": [True] * 4},
        "required_roles": ("pv", "sp"),
        "segments": [(0, 0)],
    }
    acc = view.ratio_accounting(lambda _: True, roles=("pv", "sp"))
    assert asdict(acc) == {
        "denominator_seconds": float(4 * period),
        "numerator_seconds": float(2 * period),
        "unknown_seconds": float(2 * period),
        "known_seconds": float(2 * period),
        "denominator_basis": "FULL_REQUEST_WINDOW",
    }


@pytest.mark.parametrize(
    "offsets,indices,starts,seconds,uncovered",
    [
        ([0, 0, 2], [1, 2], [0, 2], [2, 2], 0),
        ([2, 1, 3], [1, 2], [1, 3], [2, 1], 1),
        ([0, -1, 1], [1, 2], [0, 1], [1, 3], 0),
        ([0, 2, 1, 3], [0, 2, 3], [0, 1, 3], [2, 2, 1], 0),
    ],
)
def test_zero_negative_duplicate_and_overlapping_slots_are_not_reordered(
    offsets, indices, starts, seconds, uncovered
):
    view = axis.build_metric_time_view(bundle(offsets))
    assert view is not None
    assert (view.indices, view.slot_starts, view.slot_seconds, view.uncovered_window_seconds) == (
        indices,
        [ts(s) for s in starts],
        seconds,
        uncovered,
    )
    assert view.segments == [(0, len(indices) - 1)]


def test_clipping_keeps_original_timestamp_for_coverage_classification():
    cov = sc.RoleCoverage(
        known=[sc.IntervalRun(ts(0), ts(3))],
        unknown=[sc.IntervalRun(ts(-1), ts(0), "no_initial")],
    )
    view = axis.build_metric_time_view(bundle([-1, 1, 3], window=(0, 2), coverage={"pv": cov}))
    assert view is not None
    assert view.indices == [0, 1]
    assert view.slot_starts == [ts(0), ts(1)]
    assert view.slot_seconds == [1, 2]
    assert view.role_states["pv"] == [axis.SlotState.UNKNOWN, axis.SlotState.HELD]
    assert view.role_unknown_reasons["pv"] == ["no_initial", None]


@pytest.mark.parametrize(
    "boundaries,segments",
    [
        ([0], [(0, 3)]),
        ([1], [(0, 0), (2, 3)]),
        ([2], [(0, 1), (3, 3)]),
        ([3], [(0, 2)]),
        ([4], [(0, 3)]),
        ([3, 1, 1], [(0, 0), (2, 2)]),
        ([0.5, 1.5, 2.5], [(0, 0)]),
    ],
)
def test_boundary_prev_exclusive_current_inclusive_keeps_segment_state_machine(
    boundaries, segments
):
    view = axis.build_metric_time_view(bundle([0, 1, 2, 3], boundaries=boundaries))
    assert view is not None
    assert view.segments == segments


@pytest.mark.parametrize(
    "at,expected", [(-1, None), (0, 0), (5, 2), (6, None), (9, None), (10, None)]
)
def test_run_lookup_preserves_rightmost_duplicate_and_half_open(at, expected):
    runs = [
        sc.IntervalRun(ts(0), ts(10)),
        sc.IntervalRun(ts(5), ts(9), "older"),
        sc.IntervalRun(ts(5), ts(6), "rightmost"),
    ]
    found = axis._runs_contain(runs, ts(at))
    assert found is (None if expected is None else runs[expected])


def test_empty_single_run_and_unknown_precedence():
    run = sc.IntervalRun(ts(0), ts(2), "conflict")
    assert axis._runs_contain([], ts(1)) is None
    assert axis._runs_contain([run], ts(0)) is run
    assert axis._runs_contain([run], ts(2)) is None
    cov = sc.RoleCoverage(known=[run], observed=[run], unknown=[run])
    assert axis.classify_coverage(cov, ts(1)) == (False, False, "conflict")
    assert axis.classify_coverage(None, ts(1)) == (True, False, None)


@pytest.mark.parametrize("start,end", [(1, 1), (2, 1), (3, 0)])
def test_zero_or_negative_run_does_not_cover_timestamp(start, end):
    assert axis._runs_contain([sc.IntervalRun(ts(start), ts(end))], ts(start)) is None


@pytest.mark.parametrize("start,end", [(0, 0), (2, 1), (3, -1)])
def test_zero_or_negative_request_keeps_inclusive_clamp(start, end):
    assert axis.to_half_open(ts(start), ts(end)) == (ts(start), ts(start + 1))


def test_mixed_timezones_and_changed_coverage_are_refreshed():
    cov = sc.RoleCoverage(
        known=[sc.IntervalRun(ts(0), ts(3))], unknown=[sc.IntervalRun(ts(0), ts(1), "gap")]
    )
    value = bundle([0, 1, 2], coverage={"pv": cov})
    value.data_block.timestamps = [ts(0, None), ts(1, timezone(timedelta(hours=8))), ts(2)]
    first = axis.build_metric_time_view(value)
    assert first is not None
    assert first.role_states["pv"] == [
        axis.SlotState.UNKNOWN,
        axis.SlotState.HELD,
        axis.SlotState.HELD,
    ]
    cov.unknown[0].start = ts(1)
    cov.unknown[0].end = ts(2)
    second = axis.build_metric_time_view(value)
    assert second is not None
    assert second.role_states["pv"] == [
        axis.SlotState.HELD,
        axis.SlotState.UNKNOWN,
        axis.SlotState.HELD,
    ]


def test_short_validity_missing_value_and_mutation_do_not_reuse_data():
    value = bundle([0, 1, 2], signals={"pv": [1, 2]}, validity={"pv_valid": [True]})
    first = axis.build_metric_time_view(value)
    assert first is not None
    assert first.role_numerically_valid["pv"] == [True, False, False]
    assert first.role_unknown_reasons["pv"] == [None, None, "no_value"]
    value.data_block.signals["pv"][0] = None
    value.data_block.validity["pv_valid"][0] = False
    value.data_block.timestamps[0] = ts(0.5)
    second = axis.build_metric_time_view(value)
    assert second is not None
    assert second.role_states["pv"][0] is axis.SlotState.UNKNOWN
    assert second.role_numerically_valid["pv"][0] is False
    assert second.slot_starts[0] == ts(0.5)
    assert first.slot_starts[0] == ts(0)


def test_empty_legacy_and_invalid_timestamp_behavior():
    assert axis.build_metric_time_view(bundle([])) is None
    value = bundle([0, 1])
    value.data_block.query_window_start = value.data_block.query_window_end = None
    value.data_block.series_context = None
    assert axis.build_metric_time_view(value) is None
    value = bundle([0, 1])
    value.data_block.timestamps[1] = object()
    with pytest.raises(AttributeError):
        axis.build_metric_time_view(value)


def test_each_raw_timestamp_is_normalized_once_per_build(monkeypatch):
    value = bundle(
        range(6),
        signals={r: [1] * 6 for r in ("pv", "sp", "op", "mode")},
        window=(-1, 6),
        boundaries=(2.5, 4.5),
    )
    original = axis._utc
    calls = []

    def counted(dt):
        calls.append(dt)
        return original(dt)

    monkeypatch.setattr(axis, "_utc", counted)
    axis.build_metric_time_view(value)
    assert len(calls) == 6 + 2 + 2  # raw values + request endpoints + boundaries


def test_membership_search_does_not_read_every_run_start():
    reads = Counter()

    class Run:
        def __init__(self, i):
            self.i, self.end = i, ts(i + 1)

        @property
        def start(self):
            reads[self.i] += 1
            return ts(self.i)

    runs = [Run(i) for i in range(128)]
    assert axis._runs_contain(runs, ts(127.5)) is runs[-1]
    assert sum(reads.values()) <= math.ceil(math.log2(len(runs))) + 2
