"""8 类异常值检测器单元测试.

测试 8 类异常值检测器（算法说明 §3.4.3, PRD §5.5.2）：
    1. OUT_OF_RANGE — 超量程
    2. FROZEN — 冻结值
    3. JUMP — 跳变
    4. SPIKE — 尖峰
    5. NaN — NaN/Inf/NULL
    6. TS_ANOMALY — 时间戳异常（仅标记）
    7. QC_BAD — 质量码异常
    8. HF_NOISE — 高频噪声（仅标记）

以及 OutlierDetector 编排器和 should_invalidate 判定逻辑。

设计依据：算法说明 §3.4.3-3.4.4, PRD §5.5.2-5.5.3
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.contracts.data_types import ControlType, OutlierReason
from app.services.preprocessing.outlier_detection import (
    OutlierDetector,
    detect_frozen,
    detect_frozen_raw,
    detect_hf_noise,
    detect_jump,
    detect_nan,
    detect_out_of_range,
    detect_qc_bad,
    detect_spike,
    detect_ts_anomaly,
)
from app.services.preprocessing.thresholds import get_threshold

# ---------------------------------------------------------------------------
# 1. NaN 检测
# ---------------------------------------------------------------------------


class TestDetectNaN:
    """detect_nan：NaN/Inf/NULL 值检测。"""

    def test_detect_nan_values(self):
        """None/NaN/Inf 值应被检测为 NaN。"""
        values = [1.0, None, float("nan"), float("inf"), 5.0]
        results = detect_nan(values)
        indices = [i for i, _ in results]
        assert indices == [1, 2, 3]
        assert all(r == OutlierReason.NAN for _, r in results)

    def test_detect_nan_empty(self):
        """空数组返回空列表。"""
        assert detect_nan([]) == []

    def test_detect_nan_no_nan(self):
        """正常值不触发 NaN。"""
        assert detect_nan([1.0, 2.0, 3.0]) == []

    def test_detect_nan_all_nan(self):
        """全 NaN 数组所有点都标记。"""
        results = detect_nan([float("nan"), None, float("inf")])
        assert len(results) == 3


# ---------------------------------------------------------------------------
# 2. 超量程检测
# ---------------------------------------------------------------------------


class TestDetectOutOfRange:
    """detect_out_of_range：超量程值检测。"""

    def test_value_above_range(self):
        """值超出上限 → OUT_OF_RANGE。"""
        values = [50.0, 101.0, 50.0]
        results = detect_out_of_range(values, range_min=0.0, range_max=100.0)
        assert results == [(1, OutlierReason.OUT_OF_RANGE)]

    def test_value_below_range(self):
        """值低于下限 → OUT_OF_RANGE。"""
        values = [-1.0, 50.0, 50.0]
        results = detect_out_of_range(values, range_min=0.0, range_max=100.0)
        assert results == [(0, OutlierReason.OUT_OF_RANGE)]

    def test_value_at_boundary(self):
        """边界值（==range_min / ==range_max）不算超量程。"""
        values = [0.0, 100.0, 50.0]
        results = detect_out_of_range(values, range_min=0.0, range_max=100.0)
        assert results == []

    def test_nan_skipped(self):
        """NaN 值跳过（由 detect_nan 处理）。"""
        values = [float("nan"), 200.0, None]
        results = detect_out_of_range(values, range_min=0.0, range_max=100.0)
        # NaN 跳过，只有 200.0 超量程
        assert results == [(1, OutlierReason.OUT_OF_RANGE)]

    def test_multiple_out_of_range(self):
        """多个超量程值都应被检测。"""
        values = [-5.0, 50.0, 150.0, 50.0, -10.0]
        results = detect_out_of_range(values, range_min=0.0, range_max=100.0)
        indices = [i for i, _ in results]
        assert indices == [0, 2, 4]


# ---------------------------------------------------------------------------
# 3. 冻结值检测
# ---------------------------------------------------------------------------


class TestDetectFrozen:
    """detect_frozen / detect_frozen_raw：冻结值检测。"""

    def test_frozen_constant_values_normalized(self):
        """归一化后连续 N 点标准差 < 阈值 → FROZEN。"""
        # FC: frozen_window_points=5, frozen_std_pct=0.001 → std_threshold=0.1
        threshold = get_threshold(ControlType.FLOW)
        values = [50.0] * 7  # 恒定值，std=0 < 0.1
        results = detect_frozen(values, threshold)
        indices = [i for i, _ in results]
        # 所有 7 个点都应被标记（所有 5 点窗口 std=0）
        assert indices == list(range(7))
        assert all(r == OutlierReason.FROZEN for _, r in results)

    def test_frozen_varying_values_not_detected(self):
        """变化值标准差 > 阈值 → 不标记 FROZEN。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [50.0, 50.5, 51.0, 50.5, 50.0, 50.5, 51.0]
        results = detect_frozen(values, threshold)
        # std of any window > 0.1，不触发冻结
        assert results == []

    def test_frozen_raw_constant_values(self):
        """原始值版本：恒定值 → FROZEN。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [100.0] * 6
        results = detect_frozen_raw(values, threshold, range_min=0.0, range_max=200.0)
        # std_threshold = 0.001 * 200 = 0.2, std=0 < 0.2 → FROZEN
        indices = [i for i, _ in results]
        assert len(indices) == 6

    def test_frozen_window_too_short(self):
        """数据点数不足窗口大小 → 空列表。"""
        threshold = get_threshold(ControlType.FLOW)  # frozen_window_points=5
        values = [50.0, 50.0, 50.0]  # 只有 3 点 < 5
        results = detect_frozen(values, threshold)
        assert results == []

    def test_frozen_partial_segment(self):
        """部分窗口冻结：只有前几个恒定点被标记。"""
        threshold = get_threshold(ControlType.FLOW)  # window=5, std_threshold=0.1
        # 前 5 点恒定（std=0），后 5 点变化大
        values = [50.0, 50.0, 50.0, 50.0, 50.0, 80.0, 20.0, 80.0, 20.0, 80.0]
        results = detect_frozen(values, threshold)
        indices = {i for i, _ in results}
        # 窗口 [0:5] std=0 → 标记 0-4
        # 窗口 [1:6] 含 80.0，std 较大 → 不标记
        # 但窗口 [0:5] 标记了 0-4，后续窗口可能也标记部分
        assert 0 in indices
        assert 1 in indices
        assert 2 in indices
        assert 3 in indices
        assert 4 in indices

    def test_frozen_skips_nan_only_windows(self):
        """少于两个有效点的窗口不会被冻结检测标记。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [float("nan"), "invalid", 50.0, float("nan"), "invalid"]
        assert detect_frozen(values, threshold) == []

    def test_frozen_raw_marks_overlapping_windows_once(self):
        """重叠冻结窗口的索引去重且按原始顺序输出。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [10.0] * 8
        results = detect_frozen_raw(values, threshold, range_min=0.0, range_max=100.0)
        assert [index for index, _ in results] == list(range(8))


# ---------------------------------------------------------------------------
# 4. 跳变检测
# ---------------------------------------------------------------------------


class TestDetectJump:
    """detect_jump：跳变检测。"""

    def test_jump_detected(self):
        """相邻点变化 > 阈值 → JUMP。"""
        # FC: jump_threshold_pct=0.8, range 0-100 → threshold=80
        threshold = get_threshold(ControlType.FLOW)
        values = [10.0, 95.0, 10.0]  # diff=85 > 80
        results = detect_jump(values, threshold, range_min=0.0, range_max=100.0)
        indices = [i for i, _ in results]
        assert 1 in indices
        assert all(r == OutlierReason.JUMP for _, r in results)

    def test_small_change_not_jump(self):
        """小幅变化不触发 JUMP。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [50.0, 51.0, 52.0, 53.0]  # diff=1 < 80
        results = detect_jump(values, threshold, range_min=0.0, range_max=100.0)
        assert results == []

    def test_jump_at_boundary(self):
        """变化恰好等于阈值不触发（使用 > 严格大于）。"""
        threshold = get_threshold(ControlType.FLOW)
        # threshold = 0.8 * 100 = 80, diff = 80 不大于 80
        values = [10.0, 90.0]
        results = detect_jump(values, threshold, range_min=0.0, range_max=100.0)
        assert results == []

    def test_jump_with_nan_skipped(self):
        """NaN 值跳过跳变检测。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [50.0, float("nan"), 95.0]
        results = detect_jump(values, threshold, range_min=0.0, range_max=100.0)
        # NaN 跳过，50→NaN 和 NaN→95 都不检测
        assert results == []

    def test_jump_single_point(self):
        """单点数据不触发跳变检测。"""
        threshold = get_threshold(ControlType.FLOW)
        results = detect_jump([50.0], threshold, range_min=0.0, range_max=100.0)
        assert results == []


# ---------------------------------------------------------------------------
# 5. 尖峰检测
# ---------------------------------------------------------------------------


class TestDetectSpike:
    """detect_spike：尖峰检测（单点突变后恢复）。"""

    def test_spike_detected(self):
        """单点突变且前后点回落 → SPIKE。"""
        # FC: spike_threshold_pct=0.5, range 0-100 → threshold=50
        threshold = get_threshold(ControlType.FLOW)
        # 中间点 90 与前后 10 的差都 > 50
        values = [10.0, 90.0, 10.0]
        results = detect_spike(values, threshold, range_min=0.0, range_max=100.0)
        assert results == [(1, OutlierReason.SPIKE)]

    def test_spike_not_detected_when_not_recovered(self):
        """突变后不恢复 → 不是尖峰。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [10.0, 90.0, 90.0]  # 90→90 差=0 < 50
        results = detect_spike(values, threshold, range_min=0.0, range_max=100.0)
        assert results == []

    def test_small_spike_not_detected(self):
        """小幅突变不触发 SPIKE。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [50.0, 55.0, 50.0]  # diff=5 < 50
        results = detect_spike(values, threshold, range_min=0.0, range_max=100.0)
        assert results == []

    def test_spike_at_boundary(self):
        """变化恰好等于阈值不触发（使用 > 严格大于）。"""
        threshold = get_threshold(ControlType.FLOW)
        # threshold = 0.5 * 100 = 50, diff = 60 > 50 → SPIKE
        values = [10.0, 70.0, 10.0]
        results = detect_spike(values, threshold, range_min=0.0, range_max=100.0)
        assert results == [(1, OutlierReason.SPIKE)]

    def test_spike_too_short(self):
        """数据少于 3 点不检测尖峰。"""
        threshold = get_threshold(ControlType.FLOW)
        results = detect_spike([10.0, 90.0], threshold, range_min=0.0, range_max=100.0)
        assert results == []

    def test_spike_with_nan_skipped(self):
        """NaN 邻居跳过尖峰检测。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [10.0, 90.0, float("nan")]
        results = detect_spike(values, threshold, range_min=0.0, range_max=100.0)
        assert results == []


# ---------------------------------------------------------------------------
# 6. 时间戳异常检测（仅标记）
# ---------------------------------------------------------------------------


class TestDetectTsAnomaly:
    """detect_ts_anomaly：时间戳异常检测（仅标记不置 valid=False）。"""

    def test_duplicate_timestamp(self):
        """重复时间戳 → TS_ANOMALY。"""
        base = datetime(2024, 1, 1)
        timestamps = [base, base + timedelta(seconds=1), base + timedelta(seconds=1)]
        results = detect_ts_anomaly(timestamps, expected_interval_s=1.0)
        indices = [i for i, _ in results]
        # 第 2 个重复时间戳被标记
        assert 2 in indices
        assert all(r == OutlierReason.TS_ANOMALY for _, r in results)

    def test_reverse_timestamp(self):
        """逆序时间戳 → TS_ANOMALY。"""
        base = datetime(2024, 1, 1)
        timestamps = [base, base + timedelta(seconds=2), base + timedelta(seconds=1)]
        results = detect_ts_anomaly(timestamps, expected_interval_s=1.0)
        indices = [i for i, _ in results]
        # gap from t[1] to t[2] = -1 < 0 → 逆序
        assert 2 in indices

    def test_large_gap(self):
        """间隔过大（> 2×期望间隔）→ TS_ANOMALY。"""
        base = datetime(2024, 1, 1)
        timestamps = [
            base,
            base + timedelta(seconds=1),
            base + timedelta(seconds=10),  # gap=9 > 2
        ]
        results = detect_ts_anomaly(timestamps, expected_interval_s=1.0)
        indices = [i for i, _ in results]
        assert 2 in indices

    def test_normal_intervals_no_anomaly(self):
        """正常间隔不触发异常。"""
        base = datetime(2024, 1, 1)
        timestamps = [base + timedelta(seconds=i) for i in range(5)]
        results = detect_ts_anomaly(timestamps, expected_interval_s=1.0)
        assert results == []

    def test_single_timestamp_no_anomaly(self):
        """单时间戳不检测异常。"""
        results = detect_ts_anomaly([datetime(2024, 1, 1)], expected_interval_s=1.0)
        assert results == []

    def test_gap_at_boundary_not_anomaly(self):
        """间隔恰好等于 2×期望间隔不触发（使用 > 严格大于）。"""
        base = datetime(2024, 1, 1)
        timestamps = [base, base + timedelta(seconds=2)]  # gap=2 == 2*1, 不大于
        results = detect_ts_anomaly(timestamps, expected_interval_s=1.0)
        assert results == []


# ---------------------------------------------------------------------------
# 7. 质量码异常检测
# ---------------------------------------------------------------------------


class TestDetectQcBad:
    """detect_qc_bad：质量码异常检测。"""

    def test_bad_quality_code(self):
        """Bad 质量码 → QC_BAD。"""
        results = detect_qc_bad([1, 0, 1, 1])
        assert results == [(1, OutlierReason.QC_BAD)]

    def test_unknown_quality_code(self):
        """Unknown 质量码 → QC_BAD。"""
        results = detect_qc_bad([1, 999, 1])
        assert results == [(1, OutlierReason.QC_BAD)]

    def test_all_good_no_qc_bad(self):
        """全部 Good 质量码不触发。"""
        assert detect_qc_bad([1, 1, 1, 192, 2, 3]) == []

    def test_none_quality_codes(self):
        """None 质量码数组返回空（容错视为 Good）。"""
        assert detect_qc_bad(None) == []
        assert detect_qc_bad([]) == []

    def test_multiple_bad_codes(self):
        """多个 Bad/Uncertain 都被标记。"""
        results = detect_qc_bad([1, 0, 999, 1, 0])
        indices = [i for i, _ in results]
        assert indices == [1, 2, 4]


# ---------------------------------------------------------------------------
# 8. 高频噪声检测（仅标记）
# ---------------------------------------------------------------------------


class TestDetectHfNoise:
    """detect_hf_noise：高频噪声检测（仅标记不置 valid=False）。"""

    def test_high_frequency_signal_detected(self):
        """高频信号 → HF_NOISE（标记所有点）。"""
        threshold = get_threshold(ControlType.FLOW)  # noise_cutoff_hz=0.2
        # 交替信号 [0, 100, 0, 100, ...] 能量集中在 Nyquist 频率
        values = [0.0, 100.0, 0.0, 100.0, 0.0, 100.0, 0.0, 100.0]
        results = detect_hf_noise(values, threshold, sampling_freq_hz=1.0)
        # 高频能量占比 > 0.3 → 标记所有点
        assert len(results) == 8
        assert all(r == OutlierReason.HF_NOISE for _, r in results)

    def test_smooth_signal_not_detected(self):
        """平滑信号不触发 HF_NOISE。"""
        threshold = get_threshold(ControlType.FLOW)
        # 缓慢变化的信号，能量集中在低频
        values = [50.0 + i * 0.1 for i in range(16)]
        results = detect_hf_noise(values, threshold, sampling_freq_hz=1.0)
        assert results == []

    def test_constant_signal_not_detected(self):
        """恒定信号不触发 HF_NOISE（去均值后全零）。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [50.0] * 10
        results = detect_hf_noise(values, threshold, sampling_freq_hz=1.0)
        assert results == []

    def test_too_few_points(self):
        """数据点不足 8 → 空列表。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [0.0, 100.0, 0.0, 100.0, 0.0, 100.0, 0.0]  # 7 points < 8
        results = detect_hf_noise(values, threshold, sampling_freq_hz=1.0)
        assert results == []

    def test_all_nan_returns_empty(self):
        """全 NaN 数据返回空列表。"""
        threshold = get_threshold(ControlType.FLOW)
        values = [float("nan")] * 10
        results = detect_hf_noise(values, threshold, sampling_freq_hz=1.0)
        assert results == []


# ---------------------------------------------------------------------------
# OutlierDetector 编排器
# ---------------------------------------------------------------------------


class TestOutlierDetector:
    """OutlierDetector.detect_all 编排器测试。"""

    def test_detect_all_clean_signal(self):
        """干净信号不产生异常。"""
        threshold = get_threshold(ControlType.FLOW)
        detector = OutlierDetector(threshold)
        base = datetime(2024, 1, 1)
        timestamps = [base + timedelta(seconds=i) for i in range(7)]
        values = [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6]
        results = detector.detect_all(
            tag_name="pv",
            values=values,
            timestamps=timestamps,
            range_min=0.0,
            range_max=100.0,
            is_normalized=True,
        )
        assert results == {}

    def test_detect_all_multiple_reasons(self):
        """单个点可叠加多个异常原因码。"""
        threshold = get_threshold(ControlType.FLOW)
        detector = OutlierDetector(threshold)
        base = datetime(2024, 1, 1)
        timestamps = [base + timedelta(seconds=i) for i in range(7)]
        # 值 200 超量程 + 是一个跳变
        values = [50.0, 50.0, 200.0, 50.0, 50.0, 50.0, 50.0]
        results = detector.detect_all(
            tag_name="pv",
            values=values,
            timestamps=timestamps,
            range_min=0.0,
            range_max=100.0,
            is_normalized=True,
        )
        # index 2 应有 OUT_OF_RANGE + SPIKE + JUMP
        reasons_2 = results.get(2, [])
        reason_values = [r.value for r in reasons_2]
        assert OutlierReason.OUT_OF_RANGE.value in reason_values

    def test_detect_all_empty_values(self):
        """空值数组返回空字典。"""
        threshold = get_threshold(ControlType.FLOW)
        detector = OutlierDetector(threshold)
        results = detector.detect_all(
            tag_name="pv",
            values=[],
            timestamps=[],
            range_min=0.0,
            range_max=100.0,
        )
        assert results == {}

    def test_detect_all_with_quality_codes(self):
        """带质量码的信号应执行 QC_BAD 检测。"""
        threshold = get_threshold(ControlType.FLOW)
        detector = OutlierDetector(threshold)
        base = datetime(2024, 1, 1)
        timestamps = [base + timedelta(seconds=i) for i in range(7)]
        values = [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6]
        quality_codes = [1, 1, 0, 1, 1, 1, 1]  # index 2 = Bad
        results = detector.detect_all(
            tag_name="pv",
            values=values,
            timestamps=timestamps,
            range_min=0.0,
            range_max=100.0,
            quality_codes=quality_codes,
            is_normalized=True,
        )
        # index 2 应有 QC_BAD
        reasons_2 = results.get(2, [])
        assert OutlierReason.QC_BAD in reasons_2

    def test_detect_all_ts_anomaly_marked(self):
        """时间戳异常被标记为 TS_ANOMALY。"""
        threshold = get_threshold(ControlType.FLOW)
        detector = OutlierDetector(threshold)
        base = datetime(2024, 1, 1)
        # 包含重复时间戳
        timestamps = [
            base,
            base + timedelta(seconds=1),
            base + timedelta(seconds=1),  # 重复
            base + timedelta(seconds=2),
            base + timedelta(seconds=3),
            base + timedelta(seconds=4),
            base + timedelta(seconds=5),
        ]
        values = [50.0, 50.1, 50.2, 50.3, 50.4, 50.5, 50.6]
        results = detector.detect_all(
            tag_name="pv",
            values=values,
            timestamps=timestamps,
            range_min=0.0,
            range_max=100.0,
            is_normalized=True,
        )
        # index 2 应有 TS_ANOMALY
        reasons_2 = results.get(2, [])
        assert OutlierReason.TS_ANOMALY in reasons_2


# ---------------------------------------------------------------------------
# should_invalidate 判定
# ---------------------------------------------------------------------------


class TestShouldInvalidate:
    """OutlierDetector.should_invalidate：判断是否应置 valid=False。"""

    def test_mark_only_ts_anomaly(self):
        """TS_ANOMALY 仅标记不置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.TS_ANOMALY]) is False

    def test_mark_only_hf_noise(self):
        """HF_NOISE 仅标记不置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.HF_NOISE]) is False

    def test_out_of_range_invalidates(self):
        """OUT_OF_RANGE 应置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.OUT_OF_RANGE]) is True

    def test_frozen_invalidates(self):
        """FROZEN 应置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.FROZEN]) is True

    def test_jump_invalidates(self):
        """JUMP 应置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.JUMP]) is True

    def test_spike_invalidates(self):
        """SPIKE 应置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.SPIKE]) is True

    def test_nan_invalidates(self):
        """NaN 应置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.NAN]) is True

    def test_qc_bad_invalidates(self):
        """QC_BAD 应置 valid=False。"""
        assert OutlierDetector.should_invalidate([OutlierReason.QC_BAD]) is True

    def test_mixed_reasons_with_mark_only(self):
        """混合原因码：含有非 MARK_ONLY → 应置 valid=False。"""
        reasons = [OutlierReason.TS_ANOMALY, OutlierReason.JUMP]
        assert OutlierDetector.should_invalidate(reasons) is True

    def test_mixed_reasons_all_mark_only(self):
        """混合原因码：全是 MARK_ONLY → 不置 valid=False。"""
        reasons = [OutlierReason.TS_ANOMALY, OutlierReason.HF_NOISE]
        assert OutlierDetector.should_invalidate(reasons) is False

    def test_empty_reasons(self):
        """空原因码列表 → 不置 valid=False。"""
        assert OutlierDetector.should_invalidate([]) is False

    def test_all_six_invalidating_reasons(self):
        """6 类应置 valid=False 的原因码全部测试。"""
        invalidating = [
            OutlierReason.OUT_OF_RANGE,
            OutlierReason.FROZEN,
            OutlierReason.JUMP,
            OutlierReason.SPIKE,
            OutlierReason.NAN,
            OutlierReason.QC_BAD,
        ]
        for reason in invalidating:
            assert OutlierDetector.should_invalidate([reason]) is True

    def test_two_mark_only_reasons_not_invalidate(self):
        """2 类仅标记的原因码全部测试。"""
        mark_only = [OutlierReason.TS_ANOMALY, OutlierReason.HF_NOISE]
        for reason in mark_only:
            assert OutlierDetector.should_invalidate([reason]) is False
