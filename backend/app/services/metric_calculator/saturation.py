"""饱和率计算器（算法说明 §4.7）.

公式：η_sat = T_saturated / T_total × 100%

其中：
    T_saturated = Σ Δt_i · 𝟙(OP_i ≤ OP_low+ε ∨ OP_i ≥ OP_high-ε)
    仅统计自控模式（Auto/Cascade/Remote）下的饱和时长

设计依据：算法说明 §4.7；GB/T 44693.2-2024 附录 F.3

定位：辅助诊断指标，用于有效自控率判定和输出能力诊断。
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.auto_mode import AUTO_MODES
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 默认输出上下限（归一化 0~100）
DEFAULT_OP_LOW = 0.0
DEFAULT_OP_HIGH = 100.0

#: 默认饱和容差（%）
DEFAULT_EPSILON = 2.0


class SaturationRateCalculator(MetricCalculatorBase):
    """饱和率计算器（算法说明 §4.7）.

    统计控制器输出 OP 处于限位（0% 或 100%）的时长占比。
    仅统计自控模式下的饱和时长（手动模式不计入）。
    """

    @property
    def metric_code(self) -> str:
        return "saturation_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算饱和率.

        Args:
            bundle: 指标数据包（需含 op/mode 信号，mask 为 op_valid && mode_valid）

        Returns:
            MetricResult：value 为饱和率 0~100，saturation_type 在 details 中
        """
        masked_op = self._get_masked_values(bundle, "op")
        masked_mode = self._get_masked_values(bundle, "mode")
        masked_ts = self._get_masked_timestamps(bundle)
        n = len(masked_op)

        logger.debug("[饱和率] 输入: masked_points=%d", n)

        if n < 2 or len(masked_ts) < 2:
            return self._make_inconclusive(bundle, "insufficient_op_mode_data")

        op_low, op_high, epsilon = self._read_op_bounds(bundle)

        # 采用零阶保持模型：每个采样点代表一个时间间隔（最后一个点沿用前段时长）
        durations = self._point_durations(masked_ts)

        total_duration = 0.0
        sat_high_duration = 0.0
        sat_low_duration = 0.0

        for i in range(n):
            segment = durations[i]
            mode_val = _to_int(masked_mode[i]) if i < len(masked_mode) else -1
            if mode_val not in AUTO_MODES:
                continue
            total_duration += segment
            op_val = _to_float(masked_op[i])
            if op_val >= op_high - epsilon:
                sat_high_duration += segment
            elif op_val <= op_low + epsilon:
                sat_low_duration += segment

        if total_duration <= 0:
            return self._make_inconclusive(bundle, "zero_auto_duration")

        sat_total = sat_high_duration + sat_low_duration
        rate = (sat_total / total_duration) * 100.0
        rate = self._clamp(rate)
        sat_type = _determine_type(sat_high_duration, sat_low_duration)

        logger.debug(
            "[饱和率] sat_high=%.1f, sat_low=%.1f, total_auto=%.1f, rate=%.2f%%, type=%s",
            sat_high_duration,
            sat_low_duration,
            total_duration,
            rate,
            sat_type,
        )

        return self._make_result(
            bundle,
            rate,
            {
                "saturation_type": sat_type,
                "sat_high_duration_s": round(sat_high_duration, 2),
                "sat_low_duration_s": round(sat_low_duration, 2),
                "auto_duration_s": round(total_duration, 2),
                "op_low": op_low,
                "op_high": op_high,
                "epsilon": epsilon,
            },
        )

    @staticmethod
    def _read_op_bounds(bundle: MetricDataBundle) -> tuple[float, float, float]:
        """读取 OP 上下限与饱和容差.

        优先从 CONFIG 信号读取，否则使用默认值（0/100/2）。
        """
        signals = bundle.data_block.signals
        op_low = _read_float(signals, "op_low", DEFAULT_OP_LOW)
        op_high = _read_float(signals, "op_high", DEFAULT_OP_HIGH)
        epsilon = _read_float(signals, "saturation_epsilon", DEFAULT_EPSILON)
        return op_low, op_high, epsilon


def _determine_type(sat_high: float, sat_low: float) -> str:
    """判定饱和类型 HIGH/LOW/BOTH/NONE."""
    has_high = sat_high > 0
    has_low = sat_low > 0
    if has_high and has_low:
        return "BOTH"
    if has_high:
        return "HIGH"
    if has_low:
        return "LOW"
    return "NONE"


def _to_int(val: Any) -> int:
    """安全转换为 int."""
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return -1


def _to_float(val: Any) -> float:
    """安全转换为 float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _read_float(signals: dict, key: str, default: float) -> float:
    """从信号字典读取 float 值（兼容列表存储）."""
    val = MetricCalculatorBase._read_config_scalar(signals, key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


__all__ = ["SaturationRateCalculator"]
