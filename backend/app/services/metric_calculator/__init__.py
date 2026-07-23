"""指标计算器注册表（Phase 3 任务 3.2 + Phase 1 HiaMonitor 借鉴）.

集中注册 26 个指标计算器（12 原有 + 14 Phase 1 新增），提供按
metric_code 查找计算器实例的能力。编排层（Phase 4）通过本模块
获取计算器实例并组装依赖关系。

设计依据：算法说明 §4.0, §3.6；数据流程图 §7.5；
CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §3-§4
"""

from __future__ import annotations

from app.contracts.metric_calculator import MetricCalculator
from app.services.metric_calculator.accuracy import AccuracyRateCalculator
from app.services.metric_calculator.auto_mode import AutoModeRateCalculator
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.effective_auto import EffectiveAutoRateCalculator
from app.services.metric_calculator.fast_rate import FastRateCalculator
from app.services.metric_calculator.good_value import GoodValueRateCalculator
from app.services.metric_calculator.ideal_settling_time import IdealSettlingTimeCalculator
from app.services.metric_calculator.instrument_fault import InstrumentFaultRateCalculator
from app.services.metric_calculator.oscillation import OscillationRateCalculator
from app.services.metric_calculator.output_trip import OutputTripIndexCalculator
from app.services.metric_calculator.saturation import SaturationRateCalculator
from app.services.metric_calculator.setpoint_crossing import (
    OscillationAmplitudeCalculator,
    SetpointCrossingCountCalculator,
)
from app.services.metric_calculator.settling_time import SettlingTimeCalculator
from app.services.metric_calculator.stability import StabilityRateCalculator
from app.services.metric_calculator.statistics import (
    ErrorMeanCalculator,
    ErrorStdCalculator,
    OpMeanCalculator,
    OpStdCalculator,
    PvMeanCalculator,
    PvStdCalculator,
    SpMeanCalculator,
    SpStdCalculator,
)
from app.services.metric_calculator.stiction import StictionIndexCalculator
from app.services.metric_calculator.valve_diagnosis import (
    ValveLinearityCalculator,
    ValveNonlinearityCalculator,
    ValveOperatingRangeCalculator,
)

#: 计算器注册表 {metric_code: calculator_class}
CALCULATOR_REGISTRY: dict[str, type[MetricCalculator]] = {
    "accuracy_rate": AccuracyRateCalculator,
    "fast_rate": FastRateCalculator,
    "stability_rate": StabilityRateCalculator,
    "effective_auto_rate": EffectiveAutoRateCalculator,
    "good_value_rate": GoodValueRateCalculator,
    "oscillation_rate": OscillationRateCalculator,
    "saturation_rate": SaturationRateCalculator,
    "stiction_index": StictionIndexCalculator,
    "output_trip_index": OutputTripIndexCalculator,
    "auto_mode_rate": AutoModeRateCalculator,
    "settling_time": SettlingTimeCalculator,
    "ideal_settling_time": IdealSettlingTimeCalculator,
    # Phase 1 新增（HiaMonitor 借鉴，2026-07-23）
    "instrument_fault_rate": InstrumentFaultRateCalculator,
    "pv_mean": PvMeanCalculator,
    "pv_std": PvStdCalculator,
    "sp_mean": SpMeanCalculator,
    "sp_std": SpStdCalculator,
    "op_mean": OpMeanCalculator,
    "op_std": OpStdCalculator,
    "error_mean": ErrorMeanCalculator,
    "error_std": ErrorStdCalculator,
    "valve_linearity": ValveLinearityCalculator,
    "valve_nonlinearity": ValveNonlinearityCalculator,
    "valve_operating_range": ValveOperatingRangeCalculator,
    "setpoint_crossing_count": SetpointCrossingCountCalculator,
    "oscillation_amplitude": OscillationAmplitudeCalculator,
}

#: 核心质量指标代码（参与综合评分加权）
CORE_METRIC_CODES: tuple[str, ...] = (
    "accuracy_rate",
    "fast_rate",
    "stability_rate",
)

#: 折扣因子指标代码
DISCOUNT_METRIC_CODE = "effective_auto_rate"

#: 辅助诊断指标代码
AUXILIARY_METRIC_CODES: tuple[str, ...] = (
    "good_value_rate",
    "oscillation_rate",
    "saturation_rate",
    "stiction_index",
    "output_trip_index",
    "auto_mode_rate",
    "settling_time",
    "ideal_settling_time",
    # Phase 1 新增（DISPLAY_ONLY + 1 AGGREGATABLE）
    "instrument_fault_rate",
    "pv_mean",
    "pv_std",
    "sp_mean",
    "sp_std",
    "op_mean",
    "op_std",
    "error_mean",
    "error_std",
    "valve_linearity",
    "valve_nonlinearity",
    "valve_operating_range",
    "setpoint_crossing_count",
    "oscillation_amplitude",
)


def get_calculator(metric_code: str) -> MetricCalculator | None:
    """按 metric_code 获取计算器实例.

    Args:
        metric_code: 指标代码

    Returns:
        计算器实例；未知代码返回 None
    """
    cls = CALCULATOR_REGISTRY.get(metric_code)
    if cls is None:
        return None
    return cls()


def get_all_calculators() -> dict[str, MetricCalculator]:
    """获取所有已注册计算器实例.

    Returns:
        {metric_code: calculator_instance}
    """
    return {code: cls() for code, cls in CALCULATOR_REGISTRY.items()}


__all__ = [
    "AUXILIARY_METRIC_CODES",
    "CALCULATOR_REGISTRY",
    "CORE_METRIC_CODES",
    "DISCOUNT_METRIC_CODE",
    "AccuracyRateCalculator",
    "AutoModeRateCalculator",
    "EffectiveAutoRateCalculator",
    "ErrorMeanCalculator",
    "ErrorStdCalculator",
    "FastRateCalculator",
    "GoodValueRateCalculator",
    "IdealSettlingTimeCalculator",
    "InstrumentFaultRateCalculator",
    "MetricCalculatorBase",
    "OpMeanCalculator",
    "OpStdCalculator",
    "OscillationAmplitudeCalculator",
    "OscillationRateCalculator",
    "OutputTripIndexCalculator",
    "PvMeanCalculator",
    "PvStdCalculator",
    "SaturationRateCalculator",
    "SetpointCrossingCountCalculator",
    "SettlingTimeCalculator",
    "SpMeanCalculator",
    "SpStdCalculator",
    "StabilityRateCalculator",
    "StictionIndexCalculator",
    "ValveLinearityCalculator",
    "ValveNonlinearityCalculator",
    "ValveOperatingRangeCalculator",
    "get_all_calculators",
    "get_calculator",
]
