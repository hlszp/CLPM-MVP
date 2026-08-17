"""整定族元算子：阶跃响应过激分析 + 响应迟缓检测。

内核等价复制自 app/tasks/diagnosis_engine.py：
- _step_kernel ← _analyze_step_response（L2916-3045）+ _compute_decay_ratio（L3048-3083）
- _slow_kernel ← _detect_slow_response（L3086-3255）+ _expected_time_constant（L3258-3282）
- _DEFAULT_EXPECTED_TAU_SECONDS ← 引擎 L113-120
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

#: 响应迟缓期望时间常数默认表（真实秒，复制自引擎 L113-120）
_DEFAULT_EXPECTED_TAU_SECONDS: dict[str, float] = {
    "FLOW": 10.0,
    "PRESSURE": 30.0,
    "LEVEL": 120.0,
    "TEMPERATURE": 600.0,
    "ANALYSIS": 900.0,
    "OTHER": 60.0,
}


def _empty_step_response_result() -> dict[str, Any]:
    """空阶跃响应分析结果（复制自引擎 L2601-2614，去除可视化大数组）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "overshoot": 0.0,
        "decay_ratio": 0.0,
        "steady_state_error": 0.0,
        "step_count": 0,
    }


def _empty_slow_response_result() -> dict[str, Any]:
    """空响应迟缓检测结果（复制自引擎 L2617-2625）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "time_constant": 0.0,
        "expected_time_constant": 0.0,
        "ratio": 0.0,
    }


def _compute_decay_ratio(pv_response: np.ndarray, new_sp: float, step_size: float) -> float:
    """计算衰减比 A2/A1（等价复制自引擎 L3048-3083）。"""
    if len(pv_response) < 8:
        return 0.0

    try:
        deviation = pv_response - new_sp
        if step_size < 0:
            deviation = -deviation

        from scipy.signal import find_peaks

        peaks, _ = find_peaks(deviation, prominence=np.std(deviation) * 0.1)
        if len(peaks) < 2:
            return 0.0

        a1 = float(deviation[peaks[0]])
        a2 = float(deviation[peaks[1]])
        if a1 < 1e-9:
            return 0.0
        return min(1.0, a2 / a1)
    except Exception:  # noqa: BLE001
        return 0.0


def _step_kernel(
    pv: np.ndarray,
    sp: np.ndarray,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """完整阶跃响应分析（等价复制自引擎 L2916-3045；可视化数组不入返回值）。"""
    if not isinstance(threshold, dict):
        threshold = {}
    overshoot_threshold = float(threshold.get("step_overshoot_threshold", 0.25))
    decay_ratio_threshold = float(threshold.get("step_decay_ratio_threshold", 0.4))
    sse_threshold = float(threshold.get("step_sse_threshold", 0.05))

    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_step_response_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        sp_range = float(np.max(sp_arr) - np.min(sp_arr))
        if sp_range < 1e-9:
            return _empty_step_response_result()

        # 检测 SP 阶跃点（变化超过 SP 量程的 5%）
        sp_diff = np.diff(sp_arr)
        step_threshold = sp_range * 0.05
        step_indices = np.where(np.abs(sp_diff) > step_threshold)[0]

        if len(step_indices) == 0:
            return _empty_step_response_result()

        # 遍历所有阶跃，取最严重（满足指标数最多）的作为结果
        best: dict[str, Any] | None = None
        best_satisfied = -1
        for s_i in range(len(step_indices)):
            step_idx = int(step_indices[s_i])
            step_size = float(sp_arr[step_idx + 1] - sp_arr[step_idx])
            if abs(step_size) < 1e-9:
                continue
            new_sp = float(sp_arr[step_idx + 1])

            next_step = int(step_indices[s_i + 1]) + 1 if s_i + 1 < len(step_indices) else min_len
            response_end = min(next_step, min_len)
            pv_response = pv_arr[step_idx + 1 : response_end]
            if len(pv_response) < 4:
                continue

            # 指标1：过冲
            if step_size > 0:
                pv_peak = float(np.max(pv_response))
                overshoot = max(0.0, (pv_peak - new_sp) / step_size)
            else:
                pv_trough = float(np.min(pv_response))
                overshoot = max(0.0, (new_sp - pv_trough) / abs(step_size))

            # 指标2：衰减比
            decay_ratio = _compute_decay_ratio(pv_response, new_sp, step_size)

            # 指标3：稳态误差（最后 20% 数据均值与 SP 的偏差）
            tail_len = max(1, len(pv_response) // 5)
            pv_tail = pv_response[-tail_len:]
            steady_state_error = abs(float(np.mean(pv_tail)) - new_sp) / sp_range

            flags = [
                overshoot > overshoot_threshold,
                decay_ratio > decay_ratio_threshold,
                steady_state_error > sse_threshold,
            ]
            satisfied = sum(flags)

            if satisfied > best_satisfied:
                best_satisfied = satisfied
                detected = bool(satisfied >= 2)
                confidence = min(0.95, satisfied / 3.0) if detected else 0.0
                best = {
                    "detected": detected,
                    "confidence": confidence,
                    "overshoot": overshoot,
                    "decay_ratio": decay_ratio,
                    "steady_state_error": steady_state_error,
                    "step_count": len(step_indices),
                }

        if best is None:
            return _empty_step_response_result()
        return best
    except Exception as exc:  # noqa: BLE001
        logger.warning("阶跃响应分析失败: %s", exc)
        return _empty_step_response_result()


def _expected_time_constant(loop_type: str | None, tau_map: dict | None = None) -> float:
    """按回路类型返回期望响应时间常数（等价复制自引擎 L3258-3282）。"""
    mapping = tau_map if isinstance(tau_map, dict) and tau_map else _DEFAULT_EXPECTED_TAU_SECONDS
    key = (loop_type or "OTHER").upper()
    try:
        return float(mapping.get(key, mapping.get("OTHER", 60.0)))
    except (TypeError, ValueError):
        return 60.0


def _slow_kernel(
    pv: np.ndarray,
    sp: np.ndarray,
    loop_type: str | None,
    ts: np.ndarray | list[float] | None,
    *,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """响应迟缓检测（等价复制自引擎 L3086-3255，真实秒时间轴拟合）。"""
    if not isinstance(threshold, dict):
        threshold = {}
    ratio_threshold = float(threshold.get("slow_response_ratio_threshold", 2.0))
    no_step_bias_ratio = float(threshold.get("slow_no_step_bias_ratio", 0.2))
    tau_map = threshold.get("slow_expected_tau_seconds")

    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_slow_response_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        # 时间轴（真实秒）
        if ts is not None and len(ts) >= min_len:
            t_seconds = np.asarray(ts[:min_len], dtype=float)
            t_seconds = t_seconds - t_seconds[0]
            if t_seconds[-1] < 1e-9:
                interval = sample_interval if sample_interval > 0 else 1.0
                t_seconds = np.arange(min_len, dtype=float) * interval
        else:
            interval = sample_interval if sample_interval > 0 else 1.0
            t_seconds = np.arange(min_len, dtype=float) * interval

        sp_range = float(np.max(sp_arr) - np.min(sp_arr))
        if sp_range < 1e-9:
            return _empty_slow_response_result()

        sp_diff = np.diff(sp_arr)
        step_threshold = sp_range * 0.05
        step_indices = np.where(np.abs(sp_diff) > step_threshold)[0]

        if len(step_indices) == 0:
            # 无阶跃：基于稳态偏差判断
            bias = pv_arr - sp_arr
            bias_std = float(np.std(bias))
            ratio = bias_std / sp_range
            detected = bool(ratio > no_step_bias_ratio)
            expected_tau = _expected_time_constant(loop_type, tau_map)
            confidence = min(0.8, ratio * 3) if detected else 0.0
            return {
                "detected": detected,
                "confidence": confidence,
                "time_constant": 0.0,
                "expected_time_constant": expected_tau,
                "ratio": ratio,
            }

        # 分析第一个阶跃后的响应
        step_idx = int(step_indices[0])
        step_size = float(sp_arr[step_idx + 1] - sp_arr[step_idx])
        if abs(step_size) < 1e-9:
            return _empty_slow_response_result()

        old_sp = float(sp_arr[step_idx])

        response_end = min(step_idx + 1 + min_len // 2, min_len)
        pv_response = pv_arr[step_idx + 1 : response_end]
        t_response = t_seconds[step_idx + 1 : response_end]
        if len(pv_response) < 8:
            return _empty_slow_response_result()

        # 一阶滞后拟合：PV(t) = old_sp + step_size * (1 - exp(-t/τ))
        t_fit = t_response - t_response[0]
        window_seconds = float(t_fit[-1]) if len(t_fit) > 0 else 0.0
        if window_seconds < 1e-9:
            return _empty_slow_response_result()

        from scipy.optimize import curve_fit

        def _first_order_lag(t: np.ndarray, tau: float) -> np.ndarray:
            return old_sp + step_size * (1.0 - np.exp(-t / max(tau, 1e-6)))

        try:
            popt, _ = curve_fit(
                _first_order_lag,
                t_fit,
                pv_response,
                p0=[max(window_seconds / 3.0, 1e-3)],
                bounds=([1e-3], [max(window_seconds * 10.0, 1.0)]),
                maxfev=1000,
            )
            time_constant = float(popt[0])
        except Exception:  # noqa: BLE001
            # 拟合失败：63.2% 响应时间近似
            target = old_sp + step_size * 0.632
            if step_size > 0:
                reach_idx = np.where(pv_response >= target)[0]
            else:
                reach_idx = np.where(pv_response <= target)[0]
            if len(reach_idx) > 0:
                time_constant = float(t_fit[reach_idx[0]])
            else:
                time_constant = window_seconds

        expected_tau = _expected_time_constant(loop_type, tau_map)

        # 拟合防护（优化增强）：一阶系统时间常数定义为到达 63.2% 目标的时刻，
        # 若响应窗结束时 PV 到达率不足 63.2%，τ 是窗外的外推值不可靠
        # （典型场景：SP 阶跃后 PV 几乎不动，拟合出 τ=数小时的病态值）。
        # 此时判"数据不支持"而非输出误导性 ratio。
        reached_fraction = float(np.clip((pv_response[-1] - old_sp) / step_size, -1.0, 1.0))
        fit_degenerate = bool(reached_fraction < 0.632)

        ratio = time_constant / expected_tau if expected_tau > 0 else 0.0

        if fit_degenerate:
            detected = False
            confidence = 0.0
        else:
            detected = bool(ratio > ratio_threshold)
            confidence = min(0.9, ratio / 10.0) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "time_constant": time_constant,
            "expected_time_constant": expected_tau,
            "ratio": ratio,
            "reached_fraction": round(reached_fraction, 4),
            "fit_degenerate": fit_degenerate,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("响应迟缓检测失败: %s", exc)
        return _empty_slow_response_result()


@operator(
    OperatorMeta(
        name="step_response_overshoot",
        display_name="阶跃响应过激分析",
        family="tuning",
        diag_code="OVERAGGRESSIVE",
        description="SP 阶跃后过冲/衰减比/稳态误差三项指标满足 2 项即判过激",
        required_signals=("pv", "sp"),
        min_sample_rate=0.0,
        outputs_schema={
            "overshoot": "过冲量",
            "decay_ratio": "衰减比 A2/A1",
            "steady_state_error": "稳态误差",
            "step_count": "阶跃次数",
        },
        threshold_schema={
            "step_overshoot_threshold": 0.25,
            "step_decay_ratio_threshold": 0.4,
            "step_sse_threshold": 0.05,
        },
        symptom_tags=("OVERAGGRESSIVE",),
        fast_group=True,
    )
)
def detect_step(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or min(len(pv), len(sp)) < 16:
        return OperatorResult(
            "step_response_overshoot", executed=False, skip_reason="pv/sp 数据不足"
        )
    res = _step_kernel(pv, sp, threshold)
    return OperatorResult(
        "step_response_overshoot",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "overshoot": res["overshoot"],
            "decay_ratio": res["decay_ratio"],
            "steady_state_error": res["steady_state_error"],
            "step_count": res["step_count"],
        },
        evidence=[
            EvidenceItem(
                "overshoot",
                round(float(res["overshoot"]), 4),
                threshold.get("step_overshoot_threshold"),
                "过冲量" + ("超阈" if res["detected"] else "未超阈"),
            ),
        ],
    )


@operator(
    OperatorMeta(
        name="slow_response",
        display_name="响应迟缓检测",
        family="tuning",
        diag_code="OVERCONSERVATIVE",
        description="一阶滞后拟合时间常数与回路类型期望值比较，慢 2 倍以上判过保守",
        required_signals=("pv", "sp"),
        min_sample_rate=0.0,
        outputs_schema={
            "time_constant": "拟合时间常数(秒)",
            "expected_time_constant": "期望时间常数(秒)",
            "ratio": "实际/期望比值",
        },
        threshold_schema={
            "slow_response_ratio_threshold": 2.0,
            "slow_no_step_bias_ratio": 0.2,
            "slow_expected_tau_seconds": dict(_DEFAULT_EXPECTED_TAU_SECONDS),
        },
        symptom_tags=("OVERCONSERVATIVE",),
        fast_group=True,
    )
)
def detect_slow(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or min(len(pv), len(sp)) < 16:
        return OperatorResult("slow_response", executed=False, skip_reason="pv/sp 数据不足")
    res = _slow_kernel(
        pv,
        sp,
        input.meta.get("loop_type"),
        input.timestamps,
        sample_interval=float(input.meta.get("sample_interval", 1.0)),
        threshold=threshold,
    )
    return OperatorResult(
        "slow_response",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "time_constant": res["time_constant"],
            "expected_time_constant": res["expected_time_constant"],
            "ratio": res["ratio"],
            "reached_fraction": res.get("reached_fraction", 1.0),
            "fit_degenerate": res.get("fit_degenerate", False),
        },
        evidence=[
            EvidenceItem(
                "ratio",
                round(float(res["ratio"]), 3),
                threshold.get("slow_response_ratio_threshold"),
                (
                    "响应窗内 PV 到达率不足 63.2%，τ 为外推值不可靠（不判迟缓）"
                    if res.get("fit_degenerate")
                    else "实际/期望时间常数比" + ("超阈" if res["detected"] else "未超阈")
                ),
            ),
        ],
    )
