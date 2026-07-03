"""理想稳态时间计算器（算法说明 §4.5）.

理想稳态时间 T' 是衡量回路响应速度的基准，支持三种配置方式，优先级从高到低：
    1. 回路级手动配置（loop_ledger.ideal_settling_time，存于 CONFIG 信号）
    2. 基于过程模型参数自动计算：T' = α·(τ+θ)
    3. 基于回路类型的默认值

默认值（按控制类型，单位：秒）：
    | FC | PC | TC | LC  | CC | 其他 |
    | 30 | 60 | 180| 600 | 300| 120  |

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 B.4

定位：辅助诊断指标，为快速率计算提供理想稳态时间基准。
"""

from __future__ import annotations

import logging

from app.contracts.data_types import ControlType, MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 按控制类型的默认理想稳态时间（秒，P1 #17 修正）
#: 旧值：TC=300/LC=300/CC=600（错误）；新值：TC=180/LC=600/CC=300（设计要求）
DEFAULT_IDEAL_SETTLING: dict[str, float] = {
    ControlType.FLOW.value: 30.0,
    ControlType.PRESSURE.value: 60.0,
    ControlType.TEMPERATURE.value: 180.0,
    ControlType.LEVEL.value: 600.0,
    ControlType.COMPOSITION.value: 300.0,
}

#: 默认值（未知控制类型）
FALLBACK_DEFAULT = 120.0

#: 按控制类型的经验系数 α（用于模型法 T' = α·(τ+θ)）
ALPHA_BY_TYPE: dict[str, float] = {
    ControlType.FLOW.value: 1.5,
    ControlType.PRESSURE.value: 2.0,
    ControlType.TEMPERATURE.value: 2.75,
    ControlType.LEVEL.value: 4.0,
    ControlType.COMPOSITION.value: 3.5,
}


class IdealSettlingTimeCalculator(MetricCalculatorBase):
    """理想稳态时间计算器（算法说明 §4.5）.

    按优先级读取/计算理想稳态时间：
    手动配置 > 模型辨识计算值 > 控制类型默认值。
    """

    @property
    def metric_code(self) -> str:
        return "ideal_settling_time"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算理想稳态时间.

        Args:
            bundle: 指标数据包（CONFIG tagGroup 的信号含配置参数）

        Returns:
            MetricResult：value 为理想稳态时间（秒）
        """
        signals = bundle.data_block.signals

        # 方式一：手动配置（最高优先级）
        manual = self._read_manual(signals)
        if manual is not None and manual > 0:
            logger.debug("[理想稳态时间] 手动配置: T'=%.1f 秒", manual)
            return self._make_result(
                bundle,
                manual,
                {"source": "manual", "ideal_settling_time": round(manual, 2)},
            )

        # 方式二：基于过程模型参数 T' = α·(τ+θ)
        model_t = self._compute_from_model(signals)
        if model_t is not None and model_t > 0:
            logger.debug("[理想稳态时间] 模型计算: T'=%.1f 秒", model_t)
            return self._make_result(
                bundle,
                model_t,
                {"source": "model", "ideal_settling_time": round(model_t, 2)},
            )

        # 方式三：控制类型默认值
        control_type = self._read_control_type(signals)
        default_t = DEFAULT_IDEAL_SETTLING.get(control_type, FALLBACK_DEFAULT)
        logger.debug("[理想稳态时间] 默认值: control_type=%s, T'=%.1f 秒", control_type, default_t)
        return self._make_result(
            bundle,
            default_t,
            {
                "source": "default",
                "control_type": control_type,
                "ideal_settling_time": round(default_t, 2),
            },
        )

    @staticmethod
    def _read_manual(signals: dict) -> float | None:
        """读取手动配置的理想稳态时间."""
        for key in ("ideal_settling_time", "manual_settling_time"):
            val = MetricCalculatorBase._read_config_scalar(signals, key)
            if val is not None:
                try:
                    v = float(val)
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _compute_from_model(signals: dict) -> float | None:
        """基于过程模型参数计算 T' = α·(τ+θ).

        需要 process_time_constant (τ) 和 process_dead_time (θ) 两个参数。
        """
        tau = _read_float_opt(signals, "process_time_constant", "tau")
        theta = _read_float_opt(signals, "process_dead_time", "dead_time", "theta")
        if tau is None or theta is None:
            return None

        control_type = IdealSettlingTimeCalculator._read_control_type(signals)
        alpha = ALPHA_BY_TYPE.get(control_type, 2.5)
        return alpha * (tau + theta)

    @staticmethod
    def _read_control_type(signals: dict) -> str:
        """读取控制类型."""
        val = MetricCalculatorBase._read_config_scalar(signals, "control_type")
        if val is None:
            return ""
        s = str(val).strip().upper()
        # 兼容枚举值和字符串
        for ct in ControlType:
            if s == ct.value or s == ct.name:
                return ct.value
        return s


def _read_float_opt(signals: dict, *keys: str) -> float | None:
    """从信号字典按多个 key 读取 float 值（兼容列表存储）."""
    for key in keys:
        val = MetricCalculatorBase._read_config_scalar(signals, key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


__all__ = ["IdealSettlingTimeCalculator"]
