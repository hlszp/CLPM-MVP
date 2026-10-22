"""向量化检测与标量规则等价，含非法值和检测开关。"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from app.contracts.data_types import ControlType, OutlierReason
from app.services.preprocessing import outlier_detection as detector
from app.services.preprocessing.thresholds import get_threshold


def test_disabled_numeric_detectors_do_not_convert_values(monkeypatch):
    monkeypatch.setattr(detector, "is_detector_enabled", lambda code: False)
    result = detector.OutlierDetector(get_threshold(ControlType.FLOW)).detect_all(
        "pv", [10**1000], [datetime(2026, 10, 18)], 0, 100
    )
    assert result == {}


@pytest.mark.parametrize("seed", range(5))
def test_numeric_detectors_match_scalar_rules(seed):
    rng = np.random.default_rng(seed)
    values = rng.normal(50, 25, 300).tolist()
    values[3:11] = [None, "bad", float("nan"), float("inf"), "-inf", "51", 0, 100]
    original = list(values)
    numeric = []
    for value in values:
        try:
            number = float(value)
            numeric.append(number if math.isfinite(number) else None)
        except (TypeError, ValueError):
            numeric.append(None)
    threshold = get_threshold(ControlType.FLOW)
    expected_nan = [(i, OutlierReason.NAN) for i, v in enumerate(numeric) if v is None]
    expected_range = [
        (i, OutlierReason.OUT_OF_RANGE)
        for i, v in enumerate(numeric)
        if v is not None and (v < 0 or v > 100)
    ]
    expected_jump, expected_spike = [], []
    for i in range(1, len(numeric)):
        a, b = numeric[i - 1 : i + 1]
        if a is not None and b is not None and abs(b - a) > threshold.jump_threshold_pct * 100:
            expected_jump.append((i, OutlierReason.JUMP))
    for i in range(1, len(numeric) - 1):
        a, b, c = numeric[i - 1 : i + 2]
        if all(v is not None for v in (a, b, c)):
            limit = threshold.spike_threshold_pct * 100
            if abs(b - a) > limit and abs(b - c) > limit:
                expected_spike.append((i, OutlierReason.SPIKE))
    assert detector.detect_nan(values) == expected_nan
    assert detector.detect_out_of_range(values, 0, 100) == expected_range
    assert detector.detect_jump(values, threshold, 0, 100) == expected_jump
    assert detector.detect_spike(values, threshold, 0, 100) == expected_spike
    assert all(a is b for a, b in zip(values, original, strict=True))
