"""按控制类型的异常值检测阈值表.

不同控制类型（流量/压力/温度/液位/成分）的物理特性不同，
异常值检测阈值差异显著。本模块提供阈值查询接口。

阈值支持运行时覆盖（sys_config ``outlier_params.current``，JSON）：
``set_threshold_overrides()`` 由配置接口保存后调用，进程内缓存合并结果，
热路径（Pipeline/DataPlanner/诊断引擎）经 ``get_threshold()`` 读取合并后配置，
不会每回路每窗口查库。8 类检测开关同理（``set_detector_switches()``）。

设计依据：算法说明 §3.4.4, PRD §5.5.3, 实施方案 Phase 1 阈值表
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import Any

from app.contracts.data_types import ControlType

logger = logging.getLogger(__name__)


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
        frozen_fault_min_minutes: 仪表故障复合判据的冻结持续最短时间（分钟）；
            PV 冻结持续 ≥ 该时长且同期 OP 有变化才计为仪表故障
            （instrument_fault_rate，FROZEN 仅标记不置 invalid 后的误报抑制）。
            注：暂不纳入 PARAM_FIELDS（sys_config 运行时覆盖不含此项，
            如需开放覆盖需同步 outlier_params 的 camelCase 映射与 schema）
    """

    control_type: ControlType
    base_sampling_freq: int
    frozen_window_points: int
    frozen_std_pct: float
    jump_threshold_pct: float
    spike_threshold_pct: float
    noise_cutoff_hz: float
    min_consecutive_points: int
    frozen_fault_min_minutes: float

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
        frozen_fault_min_minutes=5.0,
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
        frozen_fault_min_minutes=5.0,
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
        frozen_fault_min_minutes=5.0,
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
        frozen_fault_min_minutes=5.0,
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
        frozen_fault_min_minutes=5.0,
    ),
}


def get_threshold(control_type: ControlType) -> ControlTypeThreshold:
    """获取指定控制类型的异常值检测阈值（覆盖合并后的生效值）.

    读取进程内合并缓存：``_THRESHOLDS`` 默认值叠加 sys_config 中的
    参数覆盖（``set_threshold_overrides()`` 在配置保存/预载时刷新）。
    热路径不查库。

    Args:
        control_type: 控制类型枚举

    Returns:
        对应的 ControlTypeThreshold（合并覆盖后的生效配置）

    Raises:
        KeyError: 未知控制类型
    """
    return _merged_cache[control_type]


def get_sampling_freq(control_type: ControlType) -> str:
    """获取控制类型的基础采样率标签.

    Args:
        control_type: 控制类型

    Returns:
        采样率标签，如 ``"1s"`` / ``"5s"``
    """
    return _merged_cache[control_type].sampling_freq_label


def get_default_threshold(control_type: ControlType) -> ControlTypeThreshold:
    """获取指定控制类型的算法默认阈值（不含运行时覆盖）.

    供配置服务构建"默认值 + 覆盖标记"合并视图使用。
    """
    return _THRESHOLDS[control_type]


def get_threshold_by_sampling_freq(sampling_freq_label: str) -> ControlTypeThreshold | None:
    """按采样率标签反查生效阈值（覆盖合并后的值）.

    仪表故障率等计算器只持有 ``DataBlock.sampling_freq``（如 ``"1s"``），
    无法直接拿到控制类型枚举，用本 helper 反查阈值。
    ``"5s"`` 同时对应 TEMPERATURE/LEVEL 两类时返回首个匹配项
    （两者默认阈值一致；若运行时覆盖使两者分叉，以先匹配到的 TEMPERATURE 为准）。

    Args:
        sampling_freq_label: 采样率标签，如 ``"1s"`` / ``"5s"``

    Returns:
        匹配的 ControlTypeThreshold；无匹配时返回 None
    """
    for threshold in _merged_cache.values():
        if threshold.sampling_freq_label == sampling_freq_label:
            return threshold
    return None


# ---------------------------------------------------------------------------
# 运行时参数覆盖与检测开关（sys_config outlier_params.current，进程内缓存）
# ---------------------------------------------------------------------------

#: 可覆盖的阈值参数字段名（snake_case，对应 ControlTypeThreshold 字段）
PARAM_FIELDS: tuple[str, ...] = (
    "base_sampling_freq",
    "frozen_window_points",
    "frozen_std_pct",
    "jump_threshold_pct",
    "spike_threshold_pct",
    "noise_cutoff_hz",
    "min_consecutive_points",
)

#: 8 类异常值检测开关键（与 OutlierReason 对应的小写形式）
DETECTOR_KEYS: tuple[str, ...] = (
    "nan",
    "out_of_range",
    "frozen",
    "jump",
    "spike",
    "ts_anomaly",
    "qc_bad",
    "hf_noise",
)

#: 运行时参数覆盖（key=ControlType，value={参数字段: 覆盖值}）
_threshold_overrides: dict[ControlType, dict[str, Any]] = {}

#: 运行时检测开关（默认全部启用）
_detector_switches: dict[str, bool] = dict.fromkeys(DETECTOR_KEYS, True)


def _rebuild_merged() -> dict[ControlType, ControlTypeThreshold]:
    """默认值叠加覆盖项，重建合并缓存."""
    merged: dict[ControlType, ControlTypeThreshold] = {}
    for ct, base in _THRESHOLDS.items():
        override = _threshold_overrides.get(ct) or {}
        valid = {k: v for k, v in override.items() if k in PARAM_FIELDS and v is not None}
        merged[ct] = dataclasses.replace(base, **valid) if valid else base
    return merged


#: 合并后的生效阈值缓存（get_threshold/get_sampling_freq 读取）
_merged_cache: dict[ControlType, ControlTypeThreshold] = _rebuild_merged()


def set_threshold_overrides(
    overrides: dict[ControlType | str, dict[str, Any]] | None,
) -> None:
    """更新运行时阈值覆盖并重建合并缓存（保存配置/启动预载时调用）.

    Args:
        overrides: ``{控制类型: {参数字段: 覆盖值}}``，未列出的控制类型/参数
            回落到 ``_THRESHOLDS`` 默认值；None 或空字典重置为纯默认。
    """
    global _merged_cache
    _threshold_overrides.clear()
    if overrides:
        for ct_key, params in overrides.items():
            ct = ct_key if isinstance(ct_key, ControlType) else ControlType(str(ct_key))
            if params:
                _threshold_overrides[ct] = dict(params)
    _merged_cache = _rebuild_merged()
    logger.info(
        "异常值检测阈值覆盖已更新: overridden_types=%s",
        sorted(ct.value for ct in _threshold_overrides),
    )


def get_threshold_overrides() -> dict[ControlType, dict[str, Any]]:
    """获取当前运行时阈值覆盖（浅拷贝，未覆盖的控制类型不出现）."""
    return {ct: dict(params) for ct, params in _threshold_overrides.items()}


def set_detector_switches(switches: dict[str, bool] | None) -> None:
    """更新 8 类检测开关（保存配置/启动预载时调用）.

    Args:
        switches: ``{检测键: enabled}``，未列出的检测键回落默认 true；
            None 或空字典重置为全部启用。
    """
    global _detector_switches
    merged = dict.fromkeys(DETECTOR_KEYS, True)
    if switches:
        for key, enabled in switches.items():
            if key in merged:
                merged[key] = bool(enabled)
    _detector_switches = merged
    disabled = [k for k, v in _detector_switches.items() if not v]
    logger.info("异常值检测开关已更新: disabled=%s", disabled or "无（全部启用）")


def get_detector_switches() -> dict[str, bool]:
    """获取当前 8 类检测开关（副本，含全部 8 键的生效值）."""
    return dict(_detector_switches)


def is_detector_enabled(detector_key: str) -> bool:
    """判断某类异常值检测是否启用（热路径读取进程内缓存）."""
    return _detector_switches.get(detector_key, True)
