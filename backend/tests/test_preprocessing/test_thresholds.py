"""控制类型阈值表单元测试.

测试 5 种控制类型（FC/PC/TC/LC/CC）的阈值配置，
验证不同控制类型的采样率、冻结窗口、跳变/尖峰阈值差异化。

设计依据：算法说明 §3.4.4, PRD §5.5.3
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import ControlType
from app.services.preprocessing.thresholds import (
    ControlTypeThreshold,
    get_sampling_freq,
    get_threshold,
)


# ---------------------------------------------------------------------------
# 5 种控制类型阈值查询
# ---------------------------------------------------------------------------


class TestControlTypeThresholds:
    """5 种控制类型（FC/PC/TC/LC/CC）阈值差异化测试。"""

    @pytest.mark.parametrize(
        "control_type,expected_freq",
        [
            (ControlType.FLOW, 1),
            (ControlType.PRESSURE, 2),
            (ControlType.TEMPERATURE, 5),
            (ControlType.LEVEL, 5),
            (ControlType.COMPOSITION, 10),
        ],
    )
    def test_base_sampling_freq(self, control_type, expected_freq):
        """不同控制类型的基础采样率应正确区分。"""
        threshold = get_threshold(control_type)
        assert threshold.base_sampling_freq == expected_freq

    @pytest.mark.parametrize(
        "control_type,expected_label",
        [
            (ControlType.FLOW, "1s"),
            (ControlType.PRESSURE, "2s"),
            (ControlType.TEMPERATURE, "5s"),
            (ControlType.LEVEL, "5s"),
            (ControlType.COMPOSITION, "10s"),
        ],
    )
    def test_sampling_freq_label(self, control_type, expected_label):
        """采样率标签格式应为 '{freq}s'。"""
        threshold = get_threshold(control_type)
        assert threshold.sampling_freq_label == expected_label

    def test_get_sampling_freq_function(self):
        """get_sampling_freq 返回采样率标签。"""
        assert get_sampling_freq(ControlType.FLOW) == "1s"
        assert get_sampling_freq(ControlType.COMPOSITION) == "10s"

    def test_flow_threshold_values(self):
        """FC（流量）阈值：高频采样、大跳变阈值。"""
        t = get_threshold(ControlType.FLOW)
        assert t.control_type == ControlType.FLOW
        assert t.frozen_window_points == 5
        assert t.frozen_std_pct == 0.001       # 0.1%
        assert t.jump_threshold_pct == 0.8     # 0.8×量程
        assert t.spike_threshold_pct == 0.5    # 0.5×量程
        assert t.noise_cutoff_hz == 0.2
        assert t.min_consecutive_points == 30

    def test_pressure_threshold_values(self):
        """PC（压力）阈值。"""
        t = get_threshold(ControlType.PRESSURE)
        assert t.frozen_window_points == 5
        assert t.frozen_std_pct == 0.001
        assert t.jump_threshold_pct == 0.5
        assert t.spike_threshold_pct == 0.3
        assert t.noise_cutoff_hz == 0.1
        assert t.min_consecutive_points == 20

    def test_temperature_threshold_values(self):
        """TC（温度）阈值：低频采样、小跳变阈值、更严格冻结标准差。"""
        t = get_threshold(ControlType.TEMPERATURE)
        assert t.frozen_window_points == 6
        assert t.frozen_std_pct == 0.0005      # 0.05%（更严格）
        assert t.jump_threshold_pct == 0.3
        assert t.spike_threshold_pct == 0.2
        assert t.noise_cutoff_hz == 0.05
        assert t.min_consecutive_points == 15

    def test_level_threshold_values(self):
        """LC（液位）阈值。"""
        t = get_threshold(ControlType.LEVEL)
        assert t.frozen_window_points == 6
        assert t.frozen_std_pct == 0.001
        assert t.jump_threshold_pct == 0.3
        assert t.spike_threshold_pct == 0.2
        assert t.noise_cutoff_hz == 0.05
        assert t.min_consecutive_points == 15

    def test_composition_threshold_values(self):
        """CC（成分）阈值：最低频采样、最严格阈值。"""
        t = get_threshold(ControlType.COMPOSITION)
        assert t.frozen_window_points == 6
        assert t.frozen_std_pct == 0.0005
        assert t.jump_threshold_pct == 0.2
        assert t.spike_threshold_pct == 0.1
        assert t.noise_cutoff_hz == 0.02
        assert t.min_consecutive_points == 10

    def test_thresholds_are_differentiated(self):
        """5 种控制类型的阈值应差异化（采样率各不相同）。"""
        freqs = {
            ct: get_threshold(ct).base_sampling_freq for ct in ControlType
        }
        # FC=1, PC=2, TC=5, LC=5, CC=10
        assert freqs[ControlType.FLOW] < freqs[ControlType.PRESSURE]
        assert freqs[ControlType.PRESSURE] < freqs[ControlType.TEMPERATURE]
        assert freqs[ControlType.COMPOSITION] > freqs[ControlType.TEMPERATURE]

    def test_threshold_is_frozen_dataclass(self):
        """ControlTypeThreshold 是 frozen dataclass，不可变。"""
        t = get_threshold(ControlType.FLOW)
        with pytest.raises(AttributeError):
            t.base_sampling_freq = 999  # type: ignore[misc]

    def test_all_control_types_covered(self):
        """所有 5 种控制类型都有阈值配置。"""
        for ct in ControlType:
            threshold = get_threshold(ct)
            assert isinstance(threshold, ControlTypeThreshold)
            assert threshold.control_type == ct
