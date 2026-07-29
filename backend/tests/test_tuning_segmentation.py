"""V62-P1-007 片段切分单测：MODE/缺口/饱和/启停事件边界.

覆盖：
- MODE 切换切分（AUTO↔MANUAL）
- 数据缺口切分（NaN/inf）
- OP 饱和排除
- 片段太短排除
- 无 MODE 信息兼容
- select_best_segment 选择策略
"""

from __future__ import annotations

from app.services.tuning_identification.segmentation import (
    SegmentSpec,
    segment_signals,
    select_best_segment,
)


def _const(n: int, val: float) -> list[float]:
    return [val] * n


def _ramp(n: int, start: float, step: float) -> list[float]:
    return [start + step * i for i in range(n)]


class TestSegmentModeSplit:
    """MODE 切换切分."""

    def test_auto_to_manual_splits_into_two_segments(self):
        """AUTO(100点) → MANUAL(100点) 应切分为 2 段."""
        n = 200
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)
        mode = [1] * 100 + [0] * 100  # AUTO → MANUAL

        segs = segment_signals(pv, op, mode)
        assert len(segs) == 2
        # 第一段 AUTO，可辨识
        assert segs[0].mode_label == "AUTO"
        assert segs[0].is_auto is True
        assert segs[0].exclusion_reason is None
        assert segs[0].start_idx == 0
        assert segs[0].end_idx == 100
        # 第二段 MANUAL，排除
        assert segs[1].mode_label == "MANUAL"
        assert segs[1].is_auto is False
        assert segs[1].exclusion_reason == "MANUAL_MODE"
        assert segs[1].start_idx == 100
        assert segs[1].end_idx == 200

    def test_multiple_mode_switches(self):
        """AUTO→MANUAL→AUTO→CAS 应切分为 4 段."""
        mode = [1] * 60 + [0] * 40 + [1] * 60 + [2] * 40
        n = len(mode)
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)

        segs = segment_signals(pv, op, mode)
        assert len(segs) == 4
        assert segs[0].mode_label == "AUTO"
        assert segs[1].mode_label == "MANUAL"
        assert segs[1].exclusion_reason == "MANUAL_MODE"
        assert segs[2].mode_label == "AUTO"
        assert segs[3].mode_label == "CAS"
        assert segs[3].is_auto is True  # CAS 计入自控率

    def test_no_mode_info_assumes_auto(self):
        """无 MODE 信息时不切分，假设全 AUTO，不排除."""
        n = 100
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)

        segs = segment_signals(pv, op, mode=None)
        assert len(segs) == 1
        assert segs[0].mode_label == "UNKNOWN"
        assert segs[0].is_auto is True
        assert segs[0].exclusion_reason is None


class TestSegmentDataGap:
    """数据缺口切分."""

    def test_nan_gap_splits_segment(self):
        """中间 NaN 缺口应切分为 2 段."""
        n = 120
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)
        # 中间 20 点 NaN
        for i in range(50, 70):
            pv[i] = float("nan")
            op[i] = float("nan")
        mode = [1] * n

        segs = segment_signals(pv, op, mode)
        assert len(segs) == 3  # 前段 + 缺口段 + 后段
        # 缺口段有效样本比例低，排除
        gap_seg = segs[1]
        assert gap_seg.exclusion_reason == "DATA_GAP"
        assert gap_seg.valid_sample_ratio < 0.5

    def test_inf_treated_as_gap(self):
        """inf 值视为缺口."""
        n = 100
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)
        for i in range(40, 50):
            pv[i] = float("inf")
        mode = [1] * n

        segs = segment_signals(pv, op, mode)
        assert len(segs) >= 2
        gap_seg = next(s for s in segs if s.exclusion_reason == "DATA_GAP")
        assert gap_seg.valid_sample_ratio < 0.5


class TestSegmentSaturation:
    """OP 饱和排除."""

    def test_op_saturated_segment_excluded(self):
        """OP 长时间全开（贴近 op_max）应排除."""
        n = 100
        pv = _ramp(n, 450.0, 0.01)
        # 95% 的点 OP=100（饱和），少数 OP=99
        op = [100.0] * 95 + [99.0] * 5
        mode = [1] * n

        segs = segment_signals(pv, op, mode, op_min=0.0, op_max=100.0)
        assert len(segs) == 1
        assert segs[0].exclusion_reason == "OP_SATURATION"

    def test_op_not_saturated_when_normal(self):
        """OP 在量程中段不饱和."""
        n = 100
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 40.0, 0.2)  # 40..59.8，远离上下限
        mode = [1] * n

        segs = segment_signals(pv, op, mode, op_min=0.0, op_max=100.0)
        assert segs[0].exclusion_reason is None

    def test_no_saturation_check_without_range(self):
        """未提供 op_min/op_max 时不做饱和检测."""
        n = 100
        pv = _ramp(n, 450.0, 0.01)
        op = [100.0] * n  # 全饱和但无量程信息
        mode = [1] * n

        segs = segment_signals(pv, op, mode)  # op_min/op_max=None
        assert segs[0].exclusion_reason is None


class TestSegmentTooShort:
    """片段太短排除."""

    def test_short_segment_excluded(self):
        """点数 < min_segment_points 的片段排除."""
        n = 30
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)
        mode = [1] * n

        segs = segment_signals(pv, op, mode, min_segment_points=50)
        assert len(segs) == 1
        assert segs[0].exclusion_reason == "TOO_SHORT"

    def test_custom_min_points(self):
        """自定义 min_segment_points."""
        n = 80
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)
        mode = [1] * n

        # min=50 → 通过
        segs = segment_signals(pv, op, mode, min_segment_points=50)
        assert segs[0].exclusion_reason is None
        # min=100 → 太短
        segs = segment_signals(pv, op, mode, min_segment_points=100)
        assert segs[0].exclusion_reason == "TOO_SHORT"


class TestSelectBestSegment:
    """最佳片段选择."""

    def test_selects_largest_valid_segment(self):
        """多个可辨识片段中选点数最多的."""
        mode = [1] * 100 + [0] * 50 + [1] * 200  # AUTO(100) + MANUAL(50) + AUTO(200)
        n = len(mode)
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)

        segs = segment_signals(pv, op, mode)
        best = select_best_segment(segs)
        assert best is not None
        assert best.point_count == 200
        assert best.mode_label == "AUTO"

    def test_returns_none_when_all_excluded(self):
        """全部片段被排除时返回 None."""
        n = 30  # 太短
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)
        mode = [0] * n  # 全 MANUAL

        segs = segment_signals(pv, op, mode, min_segment_points=50)
        best = select_best_segment(segs)
        assert best is None

    def test_ties_break_by_valid_ratio(self):
        """点数相同时取 valid_sample_ratio 最高的."""
        n = 100
        pv_a = _ramp(n, 450.0, 0.01)
        op_a = _ramp(n, 60.0, 0.005)
        # 第二段有少量 NaN（valid_ratio 略低）
        pv_b = _ramp(n, 450.0, 0.01)
        op_b = _ramp(n, 60.0, 0.005)
        pv_b[10] = float("nan")

        mode = [1] * n + [2] * n  # AUTO(100) + CAS(100)，点数相同
        pv = pv_a + pv_b
        op = op_a + op_b

        segs = segment_signals(pv, op, mode)
        best = select_best_segment(segs)
        assert best is not None
        assert best.mode_label == "AUTO"  # 第一段 valid_ratio 更高


class TestSegmentEdgeCases:
    """边界与容错."""

    def test_empty_input_returns_empty(self):
        assert segment_signals([], [], None) == []

    def test_length_mismatch_returns_empty(self):
        assert segment_signals([1.0, 2.0], [1.0], None) == []

    def test_all_nan_pv(self):
        """PV 全 NaN 的片段排除为 DATA_GAP."""
        n = 100
        pv = [float("nan")] * n
        op = _ramp(n, 60.0, 0.005)
        mode = [1] * n

        segs = segment_signals(pv, op, mode)
        assert len(segs) == 1
        assert segs[0].exclusion_reason == "DATA_GAP"
        assert segs[0].valid_sample_ratio == 0.0

    def test_segment_spec_fields_complete(self):
        """SegmentSpec 所有字段正确填充."""
        n = 100
        pv = _ramp(n, 450.0, 0.01)
        op = _ramp(n, 60.0, 0.005)
        mode = [1] * n

        segs = segment_signals(pv, op, mode)
        assert len(segs) == 1
        s = segs[0]
        assert isinstance(s, SegmentSpec)
        assert s.start_idx == 0
        assert s.end_idx == n
        assert s.point_count == n
        assert s.valid_sample_ratio == 1.0
        assert s.mode_label == "AUTO"
