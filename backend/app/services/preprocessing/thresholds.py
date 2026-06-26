"""按控制类型的异常值检测阈值表.

不同控制类型（流量/压力/温度/液位/成分）的物理特性不同，
异常值检测阈值差异显著。本模块提供阈值查询接口。

设计依据：算法说明 §3.4.4, PRD §5.5.3, 实施方案 Phase 1 阈值表
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts.data_types import ControlType


@dataclass(frozen=True)
class ControlTypeThreshold:
    """控制类型阈值配置（算法说明 §3.4.4）.

    Attributes:
        control_type: 控制类型枚举
        base_sampling_freq: 基础采样率（秒），如 1/2/5/10
        frozen_window_points: 冻结检测窗口点数
        frozen_std_pct: 冻结标准差阈值（占量程百分比，如 0.001=0.1%）
        jump_threshold_pct: 跳变阈值（占量程百分比，如 0.8=80%）
        spike_threshold_pct: 尖峰阈值（占量程百分比，如 0.5=50%）
        noise_cutoff_hz: 噪声截止频率（Hz）
        min_consecutive_points: 连续有效最短段点数
    """

    control_type: ControlType
    base_sampling_freq: int
    frozen_window_points: int
    frozen_std_pct: float
    jump_threshold_pct: float
    spike_threshold_pct: float
    noise_cutoff_hz: float
    min_consecutive_points: int

    @property
    def sampling_freq_label(self) -> str:
        """采样率标签，如 ``"1s"`` / ``"5s"``。"""
        return f"{self.base_sampling_freq}s"


# ---------------------------------------------------------------------------
# 阈值表（算法说明 §3.4.4 + PRD §5.5.3 冻结标准差）
# ---------------------------------------------------------------------------

_THRESHOLDS: dict[ControlType, ControlTypeThreshold] = {
    ControlType.FLOW: ControlTypeThreshold(
        control_type=ControlType.FLOW,
        base_sampling_freq=1,
        frozen_window_points=5,
        frozen_std_pct=0.001,  # 0.1%（PRD §5.5.3）
        jump_threshold_pct=0.8,  # 0.8×量程
        spike_threshold_pct=0.5,  # 0.5×量程
        noise_cutoff_hz=0.2,
        min_consecutive_points=30,
    ),
    ControlType.PRESSURE: ControlTypeThreshold(
        control_type=ControlType.PRESSURE,
        base_sampling_freq=2,
        frozen_window_points=5,
        frozen_std_pct=0.001,  # 0.1%
        jump_threshold_pct=0.5,
        spike_threshold_pct=0.3,
        noise_cutoff_hz=0.1,
        min_consecutive_points=20,
    ),
    ControlType.TEMPERATURE: ControlTypeThreshold(
        control_type=ControlType.TEMPERATURE,
        base_sampling_freq=5,
        frozen_window_points=6,
        frozen_std_pct=0.0005,  # 0.05%（PRD §5.5.3）
        jump_threshold_pct=0.3,
        spike_threshold_pct=0.2,
        noise_cutoff_hz=0.05,
        min_consecutive_points=15,
    ),
    ControlType.LEVEL: ControlTypeThreshold(
        control_type=ControlType.LEVEL,
        base_sampling_freq=5,
        frozen_window_points=6,
        frozen_std_pct=0.001,  # 0.1%
        jump_threshold_pct=0.3,
        spike_threshold_pct=0.2,
        noise_cutoff_hz=0.05,
        min_consecutive_points=15,
    ),
    ControlType.COMPOSITION: ControlTypeThreshold(
        control_type=ControlType.COMPOSITION,
        base_sampling_freq=10,
        frozen_window_points=6,
        frozen_std_pct=0.0005,  # 0.05%
        jump_threshold_pct=0.2,
        spike_threshold_pct=0.1,
        noise_cutoff_hz=0.02,
        min_consecutive_points=10,
    ),
}


def get_threshold(control_type: ControlType) -> ControlTypeThreshold:
    """获取指定控制类型的异常值检测阈值.

    Args:
        control_type: 控制类型枚举

    Returns:
        对应的 ControlTypeThreshold

    Raises:
        KeyError: 未知控制类型
    """
    return _THRESHOLDS[control_type]


def get_sampling_freq(control_type: ControlType) -> str:
    """获取控制类型的基础采样率标签.

    Args:
        control_type: 控制类型

    Returns:
        采样率标签，如 ``"1s"`` / ``"5s"``
    """
    return _THRESHOLDS[control_type].sampling_freq_label
