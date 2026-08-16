"""外扰族元算子：偏差突变（CUSUM + 突变确认）检测。

内核等价复制自 app/tasks/diagnosis_engine.py：
- _bias_shift_kernel ← _detect_bias_shift（L3285-3420）
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.services.diagnosis_operators.base import (
    EvidenceItem,
    OperatorInput,
    OperatorMeta,
    OperatorResult,
    operator,
)

logger = logging.getLogger(__name__)


def _empty_bias_shift_result() -> dict[str, Any]:
    """空偏差突变检测结果（复制自引擎 L2628-2642，去除可视化大数组）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "shift_count": 0,
        "raw_shift_count": 0,
        "max_cusum": 0.0,
        "shift_magnitude": 0.0,
        "shift_frequency": 0.0,
    }


def _bias_shift_kernel(
    pv: np.ndarray,
    sp: np.ndarray,
    ts: np.ndarray | list[float] | None = None,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """偏差突变检测（等价复制自引擎 L3285-3420；CUSUM 触发后需突变确认）。"""
    if not isinstance(threshold, dict):
        threshold = {}
    freq_threshold = float(threshold.get("bias_shift_freq_threshold", 5.0))
    amplitude_k = float(threshold.get("bias_shift_amplitude_k", 1.0))
    confirm_window = int(threshold.get("bias_shift_confirm_window", 30))

    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_bias_shift_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        bias = pv_arr - sp_arr
        bias_mean = float(np.mean(bias))
        bias_std = float(np.std(bias))
        if bias_std < 1e-9:
            return _empty_bias_shift_result()

        # CUSUM 参数：k=0.5σ，h=5σ（引擎硬编码口径）
        k = 0.5 * bias_std
        h = 5.0 * bias_std

        bias_centered = bias - bias_mean
        cusum_pos = np.zeros(min_len)
        cusum_neg = np.zeros(min_len)
        raw_shifts: list[tuple[int, int]] = []

        for i in range(1, min_len):
            cusum_pos[i] = max(0.0, cusum_pos[i - 1] + bias_centered[i] - k)
            cusum_neg[i] = min(0.0, cusum_neg[i - 1] + bias_centered[i] + k)
            if cusum_pos[i] > h or abs(cusum_neg[i]) > h:
                direction = 1 if cusum_pos[i] > h else -1
                raw_shifts.append((i, direction))
                cusum_pos[i] = 0.0
                cusum_neg[i] = 0.0

        # 突变确认：触发点前后确认窗内偏差均值的跳变幅度需 > amplitude_k×σ
        shift_points: list[int] = []
        shift_changes: list[float] = []
        for idx, direction in raw_shifts:
            pre = bias_centered[max(0, idx - confirm_window) : idx]
            post = bias_centered[idx : min(min_len, idx + confirm_window)]
            if len(pre) < 5 or len(post) < 5:
                continue
            level_change = float(np.mean(post) - np.mean(pre))
            if level_change * direction > amplitude_k * bias_std:
                shift_points.append(idx)
                shift_changes.append(level_change)

        # 计算时间窗口（秒）
        if ts is not None and len(ts) >= min_len:
            ts_arr = np.asarray(ts[:min_len], dtype=float)
            total_time = float(ts_arr[-1] - ts_arr[0])
        else:
            total_time = float(min_len)

        total_hours = total_time / 3600.0 if total_time > 0 else 1.0
        shift_count = len(shift_points)
        shift_frequency = shift_count / total_hours if total_hours > 0 else 0.0

        max_cusum = float(max(np.max(cusum_pos), abs(np.min(cusum_neg))))
        shift_magnitude = float(np.mean(np.abs(shift_changes))) if shift_changes else 0.0

        detected = bool(shift_frequency >= freq_threshold)
        confidence = min(0.95, shift_frequency / 20.0) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "shift_count": shift_count,
            "raw_shift_count": len(raw_shifts),
            "max_cusum": max_cusum,
            "shift_magnitude": shift_magnitude,
            "shift_frequency": shift_frequency,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("偏差突变检测失败: %s", exc)
        return _empty_bias_shift_result()


@operator(
    OperatorMeta(
        name="disturbance_burst",
        display_name="偏差突变检测",
        family="disturbance",
        diag_code="EXTERNAL_DISTURBANCE",
        description="PV-SP 偏差 CUSUM 突变检测 + 确认窗校验，确认频率达阈值判外扰频繁",
        required_signals=("pv", "sp"),
        min_sample_rate=0.0,
        outputs_schema={
            "shift_count": "确认突变次数",
            "shift_frequency": "确认突变频率(次/小时)",
            "shift_magnitude": "突变平均幅度",
        },
        threshold_schema={
            "bias_shift_freq_threshold": 5.0,
            "bias_shift_amplitude_k": 1.0,
            "bias_shift_confirm_window": 30,
        },
        symptom_tags=("EXTERNAL_DISTURBANCE",),
        fast_group=True,
    )
)
def detect_bias_shift(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or min(len(pv), len(sp)) < 16:
        return OperatorResult("disturbance_burst", executed=False, skip_reason="pv/sp 数据不足")
    res = _bias_shift_kernel(pv, sp, input.timestamps, threshold)
    return OperatorResult(
        "disturbance_burst",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "shift_count": res["shift_count"],
            "shift_frequency": round(float(res["shift_frequency"]), 3),
            "shift_magnitude": res["shift_magnitude"],
        },
        evidence=[
            EvidenceItem(
                "shift_frequency",
                round(float(res["shift_frequency"]), 3),
                threshold.get("bias_shift_freq_threshold"),
                "确认突变频率" + ("达阈" if res["detected"] else "未达阈"),
            ),
        ],
    )
