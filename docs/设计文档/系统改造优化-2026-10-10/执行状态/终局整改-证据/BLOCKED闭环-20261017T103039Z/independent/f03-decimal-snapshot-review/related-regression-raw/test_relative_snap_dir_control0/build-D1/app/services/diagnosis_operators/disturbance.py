"""外扰族元算子：偏差突变（CUSUM + 突变确认）检测。

内核等价复制自 app/tasks/diagnosis_engine.py：
- _bias_shift_kernel ← _detect_bias_shift（L3285-3420）
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.services.diagnosis_operators.base import (
    ASSESSMENT_INSUFFICIENT_DATA,
    REASON_TOO_FEW_POINTS,
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
        # D1（DIA-06）：与 SP 阶跃时间重合被排除的候选突变数（SP 响应归因）
        "sp_coincident_shifts": 0,
    }


def _bias_shift_kernel(
    pv: np.ndarray,
    sp: np.ndarray,
    ts: np.ndarray | list[float] | None = None,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """偏差突变检测（等价复制自引擎 L3285-3420；CUSUM 触发后需突变确认）。

    D1（DIA-06，08§5 disturbance_burst 行 / C16-D02）：**SP 主动变更排除**——
    SP 阶跃本身会造成 PV-SP 偏差的真实跳变，属"SP 响应"而非独立外扰。
    确认窗内出现 SP 阶跃（变化 > SP 量程 5%，与阶跃响应算子同判据）的
    候选突变跳过，不计数、不称"与 SP 变更无关"。
    """
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

        # SP 阶跃索引（变化超过 SP 量程 5%——与 _step_kernel/_slow_kernel 同判据）
        sp_range = float(np.max(sp_arr) - np.min(sp_arr))
        sp_step_indices = (
            np.where(np.abs(np.diff(sp_arr)) > sp_range * 0.05)[0]
            if sp_range > 1e-9
            else np.array([], dtype=int)
        )

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

        # 突变确认：触发点前后确认窗内偏差均值的跳变幅度需 > amplitude_k×σ；
        # 且确认窗内无 SP 阶跃（SP 响应不算独立外扰——DIA-06）
        shift_points: list[int] = []
        shift_changes: list[float] = []
        sp_coincident = 0
        for idx, direction in raw_shifts:
            pre = bias_centered[max(0, idx - confirm_window) : idx]
            post = bias_centered[idx : min(min_len, idx + confirm_window)]
            if len(pre) < 5 or len(post) < 5:
                continue
            # SP 阶跃排除：确认窗 [idx-confirm_window, idx+confirm_window) 内
            # 出现 SP 阶跃 → 该偏差跳变归因 SP 响应，不计独立突变
            if len(sp_step_indices) and np.any(
                (sp_step_indices >= idx - confirm_window) & (sp_step_indices < idx + confirm_window)
            ):
                sp_coincident += 1
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
            "sp_coincident_shifts": sp_coincident,
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
        confidence_basis="命中（与 SP 变更无关的偏差确认突变）时 min(0.95, 突变频率 ÷ 20 次/h)",
    )
)
def detect_bias_shift(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or min(len(pv), len(sp)) < 16:
        return OperatorResult(
            "disturbance_burst",
            executed=False,
            skip_reason="pv/sp 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
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
            "sp_coincident_shifts": res.get("sp_coincident_shifts", 0),
        },
        evidence=[
            EvidenceItem(
                "shift_frequency",
                round(float(res["shift_frequency"]), 3),
                threshold.get("bias_shift_freq_threshold"),
                (
                    "确认突变频率达阈（已排除 SP 阶跃重合候选 "
                    f"{int(res.get('sp_coincident_shifts', 0))} 次）"
                    if res["detected"]
                    else "确认突变频率未达阈"
                ),
            ),
        ],
    )
