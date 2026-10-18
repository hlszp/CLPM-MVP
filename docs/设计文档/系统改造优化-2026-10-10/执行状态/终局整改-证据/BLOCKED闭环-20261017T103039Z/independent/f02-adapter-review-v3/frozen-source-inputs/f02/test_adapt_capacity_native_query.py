"""Native ps query-window regression; no host commands or business execution."""

import pytest

from adapt_capacity_artifacts import MissingEvidence, native_span
from test_adapt_capacity_native import native_fixture


def test_overlapping_end_query_uses_the_next_real_started_after_observation(tmp_path):
    _, runtime, _, bundle, span = native_fixture(tmp_path)
    bundle["observations"][11]["psCommand"]["startedAt"] = (
        "2026-10-17T08:00:20.500000+00:00"
    )
    proof = native_span(runtime, bundle, span)
    assert proof["selectedLastSequence"] == 12
    assert proof["afterQueryStartedAt"] == "2026-10-17T08:00:23.990000+00:00"


def test_overlapping_last_query_with_no_later_original_is_blocked(tmp_path):
    _, runtime, _, bundle, span = native_fixture(tmp_path)
    bundle["observations"] = bundle["observations"][:12]
    bundle["observations"][-1]["psCommand"]["startedAt"] = (
        "2026-10-17T08:00:20.500000+00:00"
    )
    with pytest.raises(MissingEvidence, match="enclose the actual business span"):
        native_span(runtime, bundle, span)
