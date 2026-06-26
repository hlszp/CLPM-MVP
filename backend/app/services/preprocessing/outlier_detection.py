"""8 类异常值检测模块.

实现算法说明 §3.4.3 和 PRD §5.5.2 定义的 8 类异常值检测。
检测结果为 (index, OutlierReason) 列表，由 Pipeline 步骤④汇总。

关键规则（算法说明 §3.4.3 备注）：
    - TS_ANOMALY（时间戳异常）和 HF_NOISE（高频噪声）仅标记，不置 valid=False
    - 其余 6 类异常置 valid=False
    - KEEP_ALL_WITH_VALIDITY：不删除任何数据点

设计依据：算法说明 §3.4.3-3.4.4, PRD §5.5.2-5.5.3
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

import numpy as np

from app.contracts.data_types import (
    ControlType,
    OutlierReason,
    QualityStatus,
)
from app.services.preprocessing.quality_code import (
    is_nan_or_inf,
    map_quality_code,
)
from app.services.preprocessing.thresholds import ControlTypeThreshold

logger = logging.getLogger(__name__)

# 标记但不置 valid=False 的原因码
_MARK_ONLY: frozenset[OutlierReason] = frozenset(
    {OutlierReason.TS_ANOMALY, OutlierReason.HF_NOISE}
)


# ---------------------------------------------------------------------------
# 单项检测器
# ---------------------------------------------------------------------------


def detect_nan(values: list[Any]) -> list[tuple[int, OutlierReason]]:
    """检测 NaN/Inf/NULL 值（算法说明 §3.4.3 第5类）.

    Args:
        values: 信号值数组

    Returns:
        (index, OutlierReason.NAN) 列表
    """
    return [(i, OutlierReason.NAN) for i, v in enumerate(values) if is_nan_or_inf(v)]


def detect_out_of_range(
    values: list[Any], range_min: float, range_max: float
) -> list[tuple[int, OutlierReason]]:
    """检测超量程值（算法说明 §3.4.3 第1类）.

    PV/SP/OP 超出配置的量程上下限时标记为 OUT_OF_RANGE。
    NaN 值跳过（由 detect_nan 处理）。

    Args:
        values: 信号值数组
        range_min: 量程下限
        range_max: 量程上限

    Returns:
        (index, OutlierReason.OUT_OF_RANGE) 列表
    """
    results: list[tuple[int, OutlierReason]] = []
    for i, v in enumerate(values):
        if is_nan_or_inf(v):
            continue
        try:
            f = float(v)
        except (ValueError, TypeError):
            continue
        if f < range_min or f > range_max:
            results.append((i, OutlierReason.OUT_OF_RANGE))
    return results


def detect_frozen(
    values: list[Any], threshold: ControlTypeThreshold
) -> list[tuple[int, OutlierReason]]:
    """检测冻结值（算法说明 §3.4.3 第2类, §3.4.4 阈值）.

    在滑动窗口（frozen_window_points 点）内计算标准差，
    若 std < frozen_std_pct × range 则标记窗口内所有点为 FROZEN。

    Args:
        values: 信号值数组
        threshold: 控制类型阈值

    Returns:
        (index, OutlierReason.FROZEN) 列表
    """
    n = len(values)
    win = threshold.frozen_window_points
    if n < win:
        return []

    # 将值转为 float 数组，NaN 用前值填充以避免干扰 std
    float_vals: list[float] = []
    for v in values:
        if is_nan_or_inf(v):
            float_vals.append(float("nan"))
        else:
            try:
                float_vals.append(float(v))
            except (ValueError, TypeError):
                float_vals.append(float("nan"))

    arr = np.array(float_vals, dtype=float)
    range_span = 1.0  # 归一化后量程为 0~100，span=100；原始值用 range_max-range_min
    # frozen_std_pct 是占量程百分比，阈值 = pct × range_span
    # 此处 range_span 由调用方在归一化后为 100，原始值时由调用方传入
    # 为通用起见，使用绝对阈值：threshold.frozen_std_pct * 100（归一化场景）
    std_threshold = threshold.frozen_std_pct * 100.0

    results: list[tuple[int, OutlierReason]] = []
    frozen_flags = [False] * n
    for i in range(n - win + 1):
        window = arr[i : i + win]
        valid_mask = ~np.isnan(window)
        if valid_mask.sum() < 2:
            continue
        std = float(np.std(window[valid_mask]))
        if std < std_threshold:
            for j in range(i, i + win):
                frozen_flags[j] = True

    for i, flag in enumerate(frozen_flags):
        if flag:
            results.append((i, OutlierReason.FROZEN))
    return results


def detect_frozen_raw(
    values: list[Any],
    threshold: ControlTypeThreshold,
    range_min: float,
    range_max: float,
) -> list[tuple[int, OutlierReason]]:
    """检测冻结值（原始值版本，使用实际量程计算 std 阈值）.

    Args:
        values: 信号值数组（原始工程值）
        threshold: 控制类型阈值
        range_min: 量程下限
        range_max: 量程上限

    Returns:
        (index, OutlierReason.FROZEN) 列表
    """
    n = len(values)
    win = threshold.frozen_window_points
    if n < win:
        return []

    float_vals: list[float] = []
    for v in values:
        if is_nan_or_inf(v):
            float_vals.append(float("nan"))
        else:
            try:
                float_vals.append(float(v))
            except (ValueError, TypeError):
                float_vals.append(float("nan"))

    arr = np.array(float_vals, dtype=float)
    range_span = max(range_max - range_min, 1e-9)
    std_threshold = threshold.frozen_std_pct * range_span

    results: list[tuple[int, OutlierReason]] = []
    frozen_flags = [False] * n
    for i in range(n - win + 1):
        window = arr[i : i + win]
        valid_mask = ~np.isnan(window)
        if valid_mask.sum() < 2:
            continue
        std = float(np.std(window[valid_mask]))
        if std < std_threshold:
            for j in range(i, i + win):
                frozen_flags[j] = True

    for i, flag in enumerate(frozen_flags):
        if flag:
            results.append((i, OutlierReason.FROZEN))
    return results


def detect_jump(
    values: list[Any],
    threshold: ControlTypeThreshold,
    range_min: float,
    range_max: float,
) -> list[tuple[int, OutlierReason]]:
    """检测跳变（算法说明 §3.4.3 第3类, §3.4.4 阈值）.

    相邻采样点变化幅度超过 jump_threshold_pct × range 时标记为 JUMP。
    跳变点本身和前一个点都标记（变化是两点间的属性）。

    Args:
        values: 信号值数组
        threshold: 控制类型阈值
        range_min: 量程下限
        range_max: 量程上限

    Returns:
        (index, OutlierReason.JUMP) 列表
    """
    n = len(values)
    if n < 2:
        return []

    range_span = max(range_max - range_min, 1e-9)
    jump_threshold = threshold.jump_threshold_pct * range_span

    results: list[tuple[int, OutlierReason]] = []
    for i in range(1, n):
        if is_nan_or_inf(values[i]) or is_nan_or_inf(values[i - 1]):
            continue
        try:
            diff = abs(float(values[i]) - float(values[i - 1]))
        except (ValueError, TypeError):
            continue
        if diff > jump_threshold:
            results.append((i, OutlierReason.JUMP))
    return results


def detect_spike(
    values: list[Any],
    threshold: ControlTypeThreshold,
    range_min: float,
    range_max: float,
) -> list[tuple[int, OutlierReason]]:
    """检测尖峰（算法说明 §3.4.3 第4类）.

    单点突变且前后点回落：|v[i]-v[i-1]| > spike_threshold 且
    |v[i]-v[i+1]| > spike_threshold（突变后立即恢复）。

    Args:
        values: 信号值数组
        threshold: 控制类型阈值
        range_min: 量程下限
        range_max: 量程上限

    Returns:
        (index, OutlierReason.SPIKE) 列表
    """
    n = len(values)
    if n < 3:
        return []

    range_span = max(range_max - range_min, 1e-9)
    spike_threshold = threshold.spike_threshold_pct * range_span

    results: list[tuple[int, OutlierReason]] = []
    for i in range(1, n - 1):
        if is_nan_or_inf(values[i]) or is_nan_or_inf(values[i - 1]) or is_nan_or_inf(
            values[i + 1]
        ):
            continue
        try:
            prev_diff = abs(float(values[i]) - float(values[i - 1]))
            next_diff = abs(float(values[i]) - float(values[i + 1]))
        except (ValueError, TypeError):
            continue
        if prev_diff > spike_threshold and next_diff > spike_threshold:
            results.append((i, OutlierReason.SPIKE))
    return results


def detect_ts_anomaly(
    timestamps: list[datetime],
    expected_interval_s: float,
) -> list[tuple[int, OutlierReason]]:
    """检测时间戳异常（算法说明 §3.4.3 第6类，仅标记）.

    检测：重复时间戳、逆序时间戳、间隔异常（> 2× 期望间隔）。
    此检测仅标记，不置 valid=False。

    Args:
        timestamps: 时间戳序列
        expected_interval_s: 期望采样间隔（秒）

    Returns:
        (index, OutlierReason.TS_ANOMALY) 列表
    """
    n = len(timestamps)
    if n < 2:
        return []

    results: list[tuple[int, OutlierReason]] = []
    max_gap = expected_interval_s * 2.0
    seen_ts: set[float] = set()

    for i, ts in enumerate(timestamps):
        ts_epoch = ts.timestamp()
        if ts_epoch in seen_ts:
            results.append((i, OutlierReason.TS_ANOMALY))
        seen_ts.add(ts_epoch)

    for i in range(1, n):
        gap = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if gap <= 0:
            # 逆序或重复（重复已在上面处理，这里处理逆序）
            if gap < 0:
                results.append((i, OutlierReason.TS_ANOMALY))
        elif gap > max_gap:
            results.append((i, OutlierReason.TS_ANOMALY))

    return results


def detect_qc_bad(
    quality_codes: list[int] | None,
) -> list[tuple[int, OutlierReason]]:
    """检测质量码异常（算法说明 §3.4.3 第7类）.

    PV tag 携带的 OPC 质量码为 Bad/Uncertain 时标记为 QC_BAD。
    质量码为 None（缺省）时不标记（容错视为 Good）。

    Args:
        quality_codes: 质量码数组，None 表示无质量码数据

    Returns:
        (index, OutlierReason.QC_BAD) 列表
    """
    if not quality_codes:
        return []
    results: list[tuple[int, OutlierReason]] = []
    for i, qc in enumerate(quality_codes):
        status = map_quality_code(qc)
        if status != QualityStatus.GOOD:
            results.append((i, OutlierReason.QC_BAD))
    return results


def detect_hf_noise(
    values: list[Any],
    threshold: ControlTypeThreshold,
    sampling_freq_hz: float,
) -> list[tuple[int, OutlierReason]]:
    """检测高频噪声（算法说明 §3.4.3 第8类，仅标记不滤波）.

    通过 FFT 计算功率谱，若超过截止频率的成分能量占比超过 30%，
    则标记整个信号段为 HF_NOISE。此检测仅标记，不置 valid=False，
    也不做滤波（振荡率指标自行决定是否过滤）。

    Args:
        values: 信号值数组
        threshold: 控制类型阈值（取 noise_cutoff_hz）
        sampling_freq_hz: 实际采样频率（Hz）

    Returns:
        (index, OutlierReason.HF_NOISE) 列表（若检测到则标记所有点）
    """
    n = len(values)
    if n < 8:
        return []

    # 提取有效浮点值，NaN 用线性插值填充
    float_vals: list[float] = []
    for v in values:
        if is_nan_or_inf(v):
            float_vals.append(float("nan"))
        else:
            try:
                float_vals.append(float(v))
            except (ValueError, TypeError):
                float_vals.append(float("nan"))

    arr = np.array(float_vals, dtype=float)
    nan_mask = np.isnan(arr)
    if nan_mask.all():
        return []
    if nan_mask.any():
        # 线性插值填充 NaN
        valid_idx = np.where(~nan_mask)[0]
        arr[nan_mask] = np.interp(np.where(nan_mask)[0], valid_idx, arr[valid_idx])

    # 去均值后 FFT
    arr = arr - np.mean(arr)
    if np.max(np.abs(arr)) < 1e-9:
        return []

    spectrum = np.abs(np.fft.rfft(arr))
    freqs = np.fft.rfftfreq(n, d=1.0 / sampling_freq_hz)
    power = spectrum**2
    total_power = float(np.sum(power))
    if total_power < 1e-12:
        return []

    # 超过截止频率的能量占比
    hf_mask = freqs > threshold.noise_cutoff_hz
    hf_ratio = float(np.sum(power[hf_mask]) / total_power)

    if hf_ratio > 0.3:
        logger.debug(
            "HF_NOISE detected: hf_ratio=%.3f, cutoff=%.2fHz, freq=%.1fHz",
            hf_ratio,
            threshold.noise_cutoff_hz,
            sampling_freq_hz,
        )
        return [(i, OutlierReason.HF_NOISE) for i in range(n)]
    return []


# ---------------------------------------------------------------------------
# 异常值检测编排器
# ---------------------------------------------------------------------------


class OutlierDetector:
    """8 类异常值检测编排器.

    对单个信号执行全部 8 类检测，汇总异常原因码。
    返回每个点的异常原因码列表，由 Pipeline 步骤②④使用。

    设计依据：算法说明 §3.4.3-3.4.4
    """

    def __init__(self, threshold: ControlTypeThreshold) -> None:
        self.threshold = threshold

    def detect_all(
        self,
        tag_name: str,
        values: list[Any],
        timestamps: list[datetime],
        range_min: float,
        range_max: float,
        quality_codes: list[int] | None = None,
        is_normalized: bool = False,
        skip_frozen: bool = False,
    ) -> dict[int, list[OutlierReason]]:
        """对单个信号执行全部 8 类异常值检测.

        Args:
            tag_name: 信号名（如 "pv" / "sp" / "op"）
            values: 信号值数组
            timestamps: 时间戳序列
            range_min: 量程下限
            range_max: 量程上限
            quality_codes: 质量码数组（仅 PV 有）
            is_normalized: 是否已归一化（归一化后量程为 0~100）
            skip_frozen: 跳过冻结值检测（用于 SP/MODE/PID 等常态为常量的信号）

        Returns:
            dict[index, list[OutlierReason]]：每个异常点的异常原因码列表
        """
        n = len(values)
        if n == 0:
            return {}

        # 归一化后量程为 0~100，原始值量程为 range_min~range_max
        eff_min = 0.0 if is_normalized else range_min
        eff_max = 100.0 if is_normalized else range_max
        sampling_freq_hz = 1.0 / self.threshold.base_sampling_freq
        expected_interval = float(self.threshold.base_sampling_freq)

        all_reasons: dict[int, list[OutlierReason]] = {}

        def _add(reasons: list[tuple[int, OutlierReason]]) -> None:
            for idx, reason in reasons:
                all_reasons.setdefault(idx, []).append(reason)

        # 1. NaN/Inf/NULL
        _add(detect_nan(values))

        # 2. 超量程
        _add(detect_out_of_range(values, eff_min, eff_max))

        # 3. 冻结值（SP/MODE/PID 等常态为常量的信号跳过）
        if not skip_frozen:
            if is_normalized:
                _add(detect_frozen(values, self.threshold))
            else:
                _add(
                    detect_frozen_raw(
                        values, self.threshold, range_min, range_max
                    )
                )

        # 4. 跳变
        _add(detect_jump(values, self.threshold, range_min, range_max))

        # 5. 尖峰
        _add(detect_spike(values, self.threshold, range_min, range_max))

        # 6. 时间戳异常（仅标记）
        _add(detect_ts_anomaly(timestamps, expected_interval))

        # 7. 质量码异常（仅 PV 有质量码）
        if quality_codes is not None:
            _add(detect_qc_bad(quality_codes))

        # 8. 高频噪声（仅标记不滤波）
        _add(detect_hf_noise(values, self.threshold, sampling_freq_hz))

        logger.debug(
            "OutlierDetector: tag=%s, points=%d, anomalous=%d, reasons=%s",
            tag_name,
            n,
            len(all_reasons),
            {
                k: [r.value for r in v]
                for k, v in list(all_reasons.items())[:5]
            },
        )
        return all_reasons

    @staticmethod
    def should_invalidate(reasons: list[OutlierReason]) -> bool:
        """判断一组异常原因码是否应置 valid=False.

        TS_ANOMALY 和 HF_NOISE 仅标记不置 valid=False（算法说明 §3.4.3）。

        Args:
            reasons: 某个点的所有异常原因码

        Returns:
            True if any reason should invalidate the point
        """
        return any(r not in _MARK_ONLY for r in reasons)
