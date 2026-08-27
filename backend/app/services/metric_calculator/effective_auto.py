"""有效自控率计算器（算法说明 §4.2）.

公式：R = T_auto_effective / T_total × 100%

其中 T_auto_effective 需同时满足：
    1. MODE 为 Auto/Cascade/Remote（自动模式）
    2. OP 未饱和（OP_low+ε < OP < OP_high-ε）
    3. 控制偏差在合理范围（|E| < |E|_max）

设计依据：算法说明 §4.2；GB/T 44693.2-2024 附录 B.2

定位：投用指标，作为综合评分的整体折扣因子（乘数 R）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.constants.mode import AUTO_MODES
from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 默认偏差最大允许基准占量程比例
DEFAULT_E_MAX_RATIO = 0.05

#: 归一化信号量程
NORMALIZED_RANGE = 100.0

#: 默认饱和容差
DEFAULT_EPSILON = 2.0

#: 默认 OP 上下限
DEFAULT_OP_LOW = 0.0
DEFAULT_OP_HIGH = 100.0


class EffectiveAutoRateCalculator(MetricCalculatorBase):
    """有效自控率计算器（算法说明 §4.2）.

    统计评估时段内控制器处于自动模式且控制有效的时长占比。
    作为综合评分的整体折扣因子（乘数 R）。
    """

    @property
    def metric_code(self) -> str:
        return "effective_auto_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算有效自控率.

        Args:
            bundle: 指标数据包（需含 mode/op/pv/sp 信号）

        Returns:
            MetricResult：value 为有效自控率 0~100；
            details 含 auto_duration_s / effective_duration_s / total_duration_s
            等时长上下文。

        Note:
            自控率指标（auto_mode_rate）由 AutoModeRateCalculator 单独计算，
            本计算器不再副产出该值——历史副产出口径为 ``mode_valid && op_valid``
            掩码下的自控时长占比，与自控率指标（``mode_valid`` 掩码）不一致，
            同名不同值易造成口径混淆（2026-08-27 移除重复实现）。
        """
        masked_mode = self._get_masked_values(bundle, "mode")
        masked_op = self._get_masked_values(bundle, "op")
        masked_pv = self._get_masked_values(bundle, "pv")
        masked_sp = self._get_masked_values(bundle, "sp")
        masked_ts = self._get_masked_timestamps(bundle)
        n = len(masked_mode)

        logger.debug("[有效自控率] 输入: masked_points=%d", n)

        if n < 2 or len(masked_ts) < 2:
            return self._make_inconclusive(bundle, "insufficient_data")

        op_low, op_high, epsilon = self._read_bounds(bundle)
        # 整改 F2：默认偏差带比例从配置链读取
        params = get_algorithm_params("effective_auto_rate", bundle.data_block.control_type)
        default_e_max_ratio = float(params.get("default_e_max_ratio", DEFAULT_E_MAX_RATIO))
        e_max = self._read_e_max(bundle, default_e_max_ratio)

        # 采用零阶保持模型：每个采样点代表一个时间间隔（最后一个点沿用前段时长）
        # 循环上界取 mode/时间戳最小长度防 IndexError。注意 op/pv/sp 是可选信号
        # （MODE_HF tagGroup 只含 mode+op），缺失时不得把上界截断为 0——
        # 否则 total_duration=0 误报 zero_total_duration（2026-07-28 回归教训：
        # 上一版对全部 5 个数组取 min，导致缺 pv/sp 时全回路 INCONCLUSIVE）。
        durations = self._point_durations(masked_ts)
        bound = min(n, len(durations))
        durations = durations[:bound]
        total_duration = sum(durations)
        if total_duration <= 0:
            return self._make_inconclusive(bundle, "zero_total_duration")

        has_op = len(masked_op) > 0
        has_pv_sp = len(masked_pv) > 0 and len(masked_sp) > 0

        auto_duration = 0.0
        effective_duration = 0.0

        for i in range(bound):
            segment = durations[i]
            mode_val = _to_int(masked_mode[i])
            if mode_val not in AUTO_MODES:
                continue
            auto_duration += segment

            # OP 饱和检查（op 信号缺失时不判饱和）
            is_saturated = False
            if has_op and i < len(masked_op):
                op_val = _to_float(masked_op[i])
                is_saturated = (op_val <= op_low + epsilon) or (op_val >= op_high - epsilon)

            # 偏差检查（pv/sp 信号缺失时视为偏差合理）
            is_deviation_ok = True
            if e_max > 0 and has_pv_sp and i < len(masked_pv) and i < len(masked_sp):
                deviation = abs(_to_float(masked_pv[i]) - _to_float(masked_sp[i]))
                is_deviation_ok = deviation < e_max

            # 有效自控：模式自控 AND OP 未饱和 AND 偏差合理
            if not is_saturated and is_deviation_ok:
                effective_duration += segment

        effective_rate = (effective_duration / total_duration) * 100.0
        effective_rate = self._clamp(effective_rate)

        logger.debug(
            "[有效自控率] auto=%.1f, effective=%.1f, total=%.1f, R=%.2f%%",
            auto_duration,
            effective_duration,
            total_duration,
            effective_rate,
        )

        return self._make_result(
            bundle,
            effective_rate,
            {
                "auto_duration_s": round(auto_duration, 2),
                "effective_duration_s": round(effective_duration, 2),
                "total_duration_s": round(total_duration, 2),
                "op_low": op_low,
                "op_high": op_high,
                "epsilon": epsilon,
                "e_max": e_max,
            },
        )

    @staticmethod
    def _read_bounds(bundle: MetricDataBundle) -> tuple[float, float, float]:
        """读取 OP 上下限与饱和容差."""
        signals = bundle.data_block.signals
        op_low = _read_float(signals, "op_low", DEFAULT_OP_LOW)
        op_high = _read_float(signals, "op_high", DEFAULT_OP_HIGH)
        epsilon = _read_float(signals, "saturation_epsilon", DEFAULT_EPSILON)
        return op_low, op_high, epsilon

    @staticmethod
    def _read_e_max(bundle: MetricDataBundle, default_ratio: float) -> float:
        """读取偏差最大允许基准 |E|_max."""
        signals = bundle.data_block.signals
        for key in ("e_max", "accuracy_e_max", "error_max"):
            val = MetricCalculatorBase._read_config_scalar(signals, key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return NORMALIZED_RANGE * default_ratio


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


__all__ = ["EffectiveAutoRateCalculator"]
