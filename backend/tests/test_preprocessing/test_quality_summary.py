"""QualitySummary 和连续段单元测试.

测试 compute_quality_summary 的 valid_rate/bad_rate/missing_rate 计算，
以及 compute_consecutive_segments 的连续有效段切分逻辑。

设计依据：算法说明 §3.4.2 步骤⑥⑧, §3.7.2
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.preprocessing.quality_summary import (
    compute_consecutive_segments,
    compute_quality_summary,
)


# ---------------------------------------------------------------------------
# compute_quality_summary
# ---------------------------------------------------------------------------


class TestComputeQualitySummary:
    """QualitySummary 质量摘要计算测试。"""

    def test_all_valid(self):
        """全部有效 → valid_rate=1.0, bad_rate=0.0。"""
        validity = {"pv_valid": [True, True, True, True]}
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(4)]
        summary = compute_quality_summary(validity, timestamps, point_count=4)
        assert summary.total_count == 4
        assert summary.valid_count == 4
        assert summary.bad_count == 0
        assert summary.valid_rate == 1.0
        assert summary.bad_rate == 0.0

    def test_all_invalid(self):
        """全部无效 → valid_rate=0.0, bad_rate=1.0。"""
        validity = {"pv_valid": [False, False, False]}
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(3)]
        summary = compute_quality_summary(validity, timestamps, point_count=3)
        assert summary.valid_count == 0
        assert summary.bad_count == 3
        assert summary.valid_rate == 0.0
        assert summary.bad_rate == 1.0

    def test_partial_valid(self):
        """部分有效 → valid_rate 正确计算。"""
        # all_valid = [True, False, True, False] (交集)
        validity = {
            "pv_valid": [True, False, True, True],
            "sp_valid": [True, True, True, False],
        }
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(4)]
        summary = compute_quality_summary(validity, timestamps, point_count=4)
        assert summary.valid_count == 2
        assert summary.bad_count == 2
        assert summary.valid_rate == 0.5
        assert summary.bad_rate == 0.5

    def test_valid_rate_calculation(self):
        """valid_rate = valid_count / total_count。"""
        validity = {"pv_valid": [True] * 7 + [False] * 3}
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(10)]
        summary = compute_quality_summary(validity, timestamps, point_count=10)
        assert summary.valid_count == 7
        assert summary.total_count == 10
        assert summary.valid_rate == 0.7

    def test_missing_count_zero(self):
        """无缺失时 missing_count=0。"""
        validity = {"pv_valid": [True, True, True]}
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(3)]
        summary = compute_quality_summary(
            validity, timestamps, point_count=3, expected_interval_s=1.0
        )
        assert summary.missing_count == 0
        assert summary.missing_rate == 0.0

    def test_missing_count_with_gap(self):
        """时间跨度大于实际点数时检测缺失。"""
        validity = {"pv_valid": [True, True, True]}
        # 3 个点跨 10 秒，期望间隔 1s → expected=11
        timestamps = [
            datetime(2024, 1, 1),
            datetime(2024, 1, 1) + timedelta(seconds=5),
            datetime(2024, 1, 1) + timedelta(seconds=10),
        ]
        summary = compute_quality_summary(
            validity, timestamps, point_count=3, expected_interval_s=1.0
        )
        assert summary.missing_count == 8  # 11 - 3

    def test_good_value_rate(self):
        """有质量码时计算 good_value_rate。"""
        validity = {"pv_valid": [True, True, True, True]}
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(4)]
        quality_codes = [1, 0, 1, 1]  # 3 Good, 1 Bad
        summary = compute_quality_summary(
            validity, timestamps, point_count=4, quality_codes=quality_codes
        )
        assert summary.good_value_rate == 0.75

    def test_good_value_rate_none_when_no_qc(self):
        """无质量码时 good_value_rate=None。"""
        validity = {"pv_valid": [True, True]}
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(2)]
        summary = compute_quality_summary(
            validity, timestamps, point_count=2, quality_codes=None
        )
        assert summary.good_value_rate is None

    def test_empty_point_count(self):
        """点数为 0 → 空摘要。"""
        summary = compute_quality_summary({}, [], point_count=0)
        assert summary.total_count == 0
        assert summary.valid_count == 0
        assert summary.valid_rate == 0.0

    def test_multi_tag_intersection(self):
        """多 tag valid 的交集决定有效点。"""
        validity = {
            "pv_valid": [True, True, True, True],
            "sp_valid": [True, True, False, True],
            "op_valid": [True, False, False, True],
        }
        timestamps = [datetime(2024, 1, 1) + timedelta(seconds=i) for i in range(4)]
        summary = compute_quality_summary(validity, timestamps, point_count=4)
        # all_valid = [T, F, F, T]
        assert summary.valid_count == 2
        assert summary.bad_count == 2


# ---------------------------------------------------------------------------
# compute_consecutive_segments
# ---------------------------------------------------------------------------


class TestComputeConsecutiveSegments:
    """compute_consecutive_segments 连续有效段测试。"""

    def test_single_segment(self):
        """单段连续有效。"""
        all_valid = [True, True, True, True, True]
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=3)
        assert segments == [(0, 4)]

    def test_segment_below_min_discarded(self):
        """长度不足 min_consecutive_points 的段被丢弃。"""
        all_valid = [True, True, False, True, True, True, True]
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=3)
        # [0,1] 长度 2 < 3 → 丢弃
        # [3,6] 长度 4 >= 3 → 保留
        assert segments == [(3, 6)]

    def test_multiple_segments(self):
        """多个连续有效段。"""
        all_valid = [True, True, True, False, True, True, True, True, True, False, True]
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=3)
        # [0,2] 长度 3 >= 3 → 保留
        # [4,8] 长度 5 >= 3 → 保留
        # [10,10] 长度 1 < 3 → 丢弃
        assert segments == [(0, 2), (4, 8)]

    def test_all_false(self):
        """全部无效 → 空列表。"""
        all_valid = [False, False, False]
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=2)
        assert segments == []

    def test_all_true(self):
        """全部有效 → 单段。"""
        all_valid = [True] * 10
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=5)
        assert segments == [(0, 9)]

    def test_empty_list(self):
        """空列表 → 空结果。"""
        segments = compute_consecutive_segments([], min_consecutive_points=3)
        assert segments == []

    def test_min_consecutive_at_boundary(self):
        """段长度恰好等于 min_consecutive_points → 保留。"""
        all_valid = [True, True, True]
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=3)
        assert segments == [(0, 2)]

    def test_segment_with_gap_at_start(self):
        """开头有无效点。"""
        all_valid = [False, False, True, True, True, True]
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=3)
        assert segments == [(2, 5)]

    def test_segment_with_gap_at_end(self):
        """末尾有无效点。"""
        all_valid = [True, True, True, True, False, False]
        segments = compute_consecutive_segments(all_valid, min_consecutive_points=3)
        assert segments == [(0, 3)]
