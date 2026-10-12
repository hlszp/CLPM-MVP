"""整定族元算子：阶跃响应过激分析 + 响应迟缓检测。

内核等价复制自 app/tasks/diagnosis_engine.py：
- _step_kernel ← _analyze_step_response（L2916-3045）+ _compute_decay_ratio（L3048-3083）
- _slow_kernel ← _detect_slow_response（L3086-3255）+ _expected_time_constant（L3258-3282）
- _DEFAULT_EXPECTED_TAU_SECONDS ← 引擎 L113-120

D4（2026-10-11，08§5 step/slow 两行 + C16-D03/D05）：
- 过激：事件去重（SP 斜坡多索引归并为一事件）；AUTO 前提检查（手动模式事件
  不直接判 PID 过激，NOT_APPLICABLE）；OP 限幅检查（输出受限期间不判过激）；
  末段未证稳定不称稳态误差（SSE 不参与命中计数并显式标注）；事件级结果
  （segment_results）与实际生效参数落库；
- 迟缓：**逐合格事件**评估（不再只取首个）；初值 y0 取事件前实测 PV（模型
  y(t)=y0+A(1−exp(−t/τ))，A 估计依据/单位随结果保存，不默认 y0=旧 SP、
  A=SP 差）；拟合质量（RMSE/|A|）与参数触边（τ/A 贴拟合边界）不发布有效
  τ 阴性；响应完成度判据（到达率 <63.2% = 响应未完成，INSUFFICIENT 有
  原因）；多事件 τ 不平均（代表事件=比值最差者）；
- 两算子内核异常 `_error` 透传 → ERROR 态；无合格事件 → INSUFFICIENT
  （NO_EVENTS）；expected τ 对照（actual/expected + 来源）逐事件保留。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.constants.mode import AUTO_MODES
from app.services.diagnosis_operators.base import (
    ASSESSMENT_ERROR,
    ASSESSMENT_INSUFFICIENT_DATA,
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_NOT_APPLICABLE,
    ASSESSMENT_POSITIVE,
    REASON_INSUFFICIENT_FIT,
    REASON_MANUAL_MODE,
    REASON_NO_EVENTS,
    REASON_NO_EXPECTED_TAU,
    REASON_OP_SATURATED,
    REASON_RESPONSE_INCOMPLETE,
    REASON_TOO_FEW_POINTS,
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

#: SP 阶跃事件去重间隔（采样点）：相邻 <4 点的多个阶跃索引归并为同一事件
#: （SP 斜坡在 2-3 个采样内走完会触发多索引，属同一物理事件）
_STEP_EVENT_DEDUP_GAP = 4

#: 过激算子：响应窗最小点数（不足 = 响应未完成，不硬凑）
_MIN_RESPONSE_POINTS = 8

#: 迟缓算子：事件前实测初值窗口（采样点）与最小长度
_PRE_EVENT_WINDOW = 120
_PRE_EVENT_MIN_POINTS = 8

#: 迟缓算子：A（响应幅度）拟合边界（|step| 的倍数；触边 = 增益假设缺依据）
_AMPLITUDE_BOUND_LOW = 0.05
_AMPLITUDE_BOUND_HIGH = 5.0

#: 迟缓算子：到达率门（一阶系统 τ 定义为 63.2% 到达时刻；低于此 τ 为窗外
#: 外推值不可靠——D1 fit_degenerate 判据保留为完成度判据）
_REACHED_FRACTION_MIN = 0.632


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


def _detect_sp_steps(sp_arr: np.ndarray, sp_range: float) -> list[int]:
    """SP 阶跃索引（变化 > 5% SP 极差，与引擎一致）。"""
    sp_diff = np.diff(sp_arr)
    step_threshold = sp_range * 0.05
    return [int(i) for i in np.where(np.abs(sp_diff) > step_threshold)[0]]


def _dedup_step_events(step_indices: list[int]) -> list[tuple[int, int]]:
    """SP 阶跃事件去重（D4，08§5"事件去重"）。

    相邻 < ``_STEP_EVENT_DEDUP_GAP`` 采样的多个阶跃索引（SP 斜坡）归并为
    同一事件：事件 = (last_stable_idx, settled_idx)——last_stable 为事件前
    最后一个稳定采样，settled 为斜坡走完后的首个采样。
    """
    if not step_indices:
        return []
    events: list[tuple[int, int]] = []
    group_start = step_indices[0]
    prev = step_indices[0]
    for idx in step_indices[1:]:
        if idx - prev <= _STEP_EVENT_DEDUP_GAP:
            prev = idx
            continue
        events.append((group_start, prev + 1))
        group_start = idx
        prev = idx
    events.append((group_start, prev + 1))
    return events


def _is_auto_mode_value(mode_val: Any) -> bool:
    """MODE 值是否自控（与 saturation._is_auto_mode 同口径）。"""
    if isinstance(mode_val, bool):
        return False
    if isinstance(mode_val, int | np.integer):
        return int(mode_val) in AUTO_MODES
    if isinstance(mode_val, float | np.floating):
        return float(mode_val).is_integer() and int(mode_val) in AUTO_MODES
    try:
        num = float(str(mode_val).strip())
    except (TypeError, ValueError):
        return False
    return num.is_integer() and int(num) in AUTO_MODES


def _optional_aligned(input: OperatorInput, key: str, n: int) -> np.ndarray | None:
    """机会性消费非 required 信号（mode/op）——仅在**与 pv 同轴对齐**时。

    legacy 路径各信号同一公共行切片（等长）；point 路径 mode/op 为全网格
    未切片（meta.point_axis 标记），索引与切片后 pv 不对齐——禁止消费
    （错位 MODE/OP 会把限幅/手动判到错误窗口），此时返回 None = 该检查
    显式记"未提供"而非假设成立（08§5"缺 MODE 不假设 AUTO"同口径）。
    """
    if input.meta.get("point_axis"):
        return None
    sig = input.signals.get(key)
    if sig is None:
        return None
    arr = np.asarray(sig)
    return arr if len(arr) >= n else None


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
    mode: np.ndarray | None = None,
    op: np.ndarray | None = None,
    op_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """完整阶跃响应分析（等价复制自引擎 L2916-3045；可视化数组不入返回值）。

    D4 深化（mode/op 缺省时与引擎数值行为一致——等价性回归前提）：
    1. 事件去重：SP 斜坡多索引归并单事件；
    2. AUTO 前提：mode 同轴提供且事件窗自控占比 <50% → 该事件不评
       （手动模式响应反映的是手操轨迹，不直接判 PID 过激）；
    3. 限幅前提：op 同轴提供且事件窗内贴限占比 >50% → 该事件不评
       （执行器受限期间的过冲不是控制器整定问题）；
    4. 末段未证稳定不称稳态误差：尾部波动 > step_tail_stability_ratio×|阶跃|
       时 SSE 不参与命中计数（记 tail_unstable）。
    """
    if not isinstance(threshold, dict):
        threshold = {}
    overshoot_threshold = float(threshold.get("step_overshoot_threshold", 0.25))
    decay_ratio_threshold = float(threshold.get("step_decay_ratio_threshold", 0.4))
    sse_threshold = float(threshold.get("step_sse_threshold", 0.05))
    tail_stability_ratio = float(threshold.get("step_tail_stability_ratio", 0.1))

    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_step_response_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)
        mode_arr = (
            np.asarray(mode[:min_len], dtype=object)
            if mode is not None and len(mode) >= min_len
            else None
        )
        op_arr = (
            np.asarray(op[:min_len], dtype=float) if op is not None and len(op) >= min_len else None
        )

        sp_range = float(np.max(sp_arr) - np.min(sp_arr))
        if sp_range < 1e-9:
            return {**_empty_step_response_result(), "events": []}

        events = _dedup_step_events(_detect_sp_steps(sp_arr, sp_range))
        if not events:
            return {**_empty_step_response_result(), "events": []}

        op_lo, op_hi = (
            (float(op_range[0]), float(op_range[1]))
            if op_range and float(op_range[1]) > float(op_range[0])
            else (None, None)
        )

        # 遍历所有事件，取最严重（满足指标数最多）的作为结果
        best: dict[str, Any] | None = None
        best_satisfied = -1
        event_records: list[dict[str, Any]] = []
        for e_i, (step_idx, settled_idx) in enumerate(events):
            step_size = float(sp_arr[settled_idx] - sp_arr[step_idx])
            new_sp = float(sp_arr[settled_idx])
            record: dict[str, Any] = {
                "event": e_i,
                "start": settled_idx,
                "step_size": round(step_size, 4),
            }
            if abs(step_size) < 1e-9:
                record["state"] = "SKIPPED"
                record["reason"] = "zero_step"
                event_records.append(record)
                continue

            next_start = events[e_i + 1][0] + 1 if e_i + 1 < len(events) else min_len
            response_end = min(next_start, min_len)
            pv_response = pv_arr[settled_idx:response_end]

            # AUTO 前提：事件窗（阶跃点→响应结束）自控占比
            if mode_arr is not None:
                win_modes = [m for m in mode_arr[step_idx:response_end] if m is not None]
                # 非 None 样本均可解析（数值/字符串枚举）；未提供样本不计入
                if win_modes:
                    auto_frac = sum(1 for m in win_modes if _is_auto_mode_value(m)) / len(win_modes)
                    record["mode_auto_fraction"] = round(auto_frac, 3)
                    if auto_frac < 0.5:
                        record["state"] = "SKIPPED"
                        record["reason"] = "manual_mode"
                        event_records.append(record)
                        continue
                else:
                    record["mode_auto_fraction"] = None  # MODE 全缺——不假设 AUTO

            # 限幅前提：事件窗内 OP 贴限占比（真实限幅，1% 量程裕度）
            if op_arr is not None and op_lo is not None:
                margin = (op_hi - op_lo) * 0.01
                win_op = op_arr[step_idx:response_end]
                if len(win_op) > 0:
                    limited = float(
                        np.mean((win_op >= op_hi - margin) | (win_op <= op_lo + margin))
                    )
                    record["op_limit_fraction"] = round(limited, 3)
                    if limited > 0.5:
                        record["state"] = "SKIPPED"
                        record["reason"] = "op_saturated"
                        event_records.append(record)
                        continue

            if len(pv_response) < _MIN_RESPONSE_POINTS:
                # D4：响应完成度——被下一事件截断后不足，不硬凑
                record["state"] = "SKIPPED"
                record["reason"] = "response_incomplete"
                record["response_points"] = len(pv_response)
                event_records.append(record)
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

            # 指标3：稳态误差（最后 20% 数据均值与 SP 的偏差）——
            # D4：末段未证稳定（尾部波动 > tail_stability_ratio×|阶跃|）时
            # 不称稳态误差，该指标不参与命中计数
            tail_len = max(1, len(pv_response) // 5)
            pv_tail = pv_response[-tail_len:]
            tail_std = float(np.std(pv_tail))
            tail_stable = tail_std <= tail_stability_ratio * abs(step_size)
            steady_state_error = abs(float(np.mean(pv_tail)) - new_sp) / sp_range

            flags = [
                overshoot > overshoot_threshold,
                decay_ratio > decay_ratio_threshold,
                (steady_state_error > sse_threshold) if tail_stable else False,
            ]
            satisfied = sum(flags)

            record.update(
                {
                    "state": "ASSESSED",
                    "end": response_end,
                    "response_points": len(pv_response),
                    "overshoot": round(overshoot, 4),
                    "decay_ratio": round(decay_ratio, 4),
                    "steady_state_error": round(steady_state_error, 4),
                    "tail_stable": bool(tail_stable),
                    "satisfied": satisfied,
                    "detected": bool(satisfied >= 2),
                    **({"sse_not_assessed": "tail_unstable"} if not tail_stable else {}),
                }
            )
            event_records.append(record)

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
                    "step_count": len(events),
                }

        if best is None:
            return {
                **_empty_step_response_result(),
                "step_count": len(events),
                "events": event_records,
            }
        return {**best, "events": event_records}
    except Exception as exc:  # noqa: BLE001
        logger.warning("阶跃响应分析失败: %s", exc)
        # D4：异常透传（错误不能被空结果吞掉——原先空结果会被渲染成 NEGATIVE）
        failed = _empty_step_response_result()
        failed["_error"] = f"{type(exc).__name__}: {exc}"
        return failed


def _expected_time_constant(
    loop_type: str | None,
    tau_map: dict | None,
    expected_tau_info: dict | None,
) -> tuple[float | None, str]:
    """期望响应时间常数（D1/DIA-08 改造：工程来源优先）.

    来源优先级（08§3.3"批准模板/设备工艺设计/已确认正常历史"；B1 供数
    ``resolve_assessment_supply_facts`` 的 T′=idealSettlingTime——回路字段
    > 工程模板 > 回路类型默认）：
    1. expected_tau_info（B1 工程供数，valueSeconds>0）——T′ 为 2% 稳态带
       稳态时间，一阶系统 T′≈4τ（换算基准随来源返回）；
    2. 阈值表 slow_expected_tau_seconds（配置覆盖链，兜底）；
    3. 回路类型默认表（旧行为兜底）。

    无任何来源返回 (None, 来源说明)——调用方**只报告测得动态特征，不判断
    "比应有速度慢"**（NOT_APPLICABLE，不伪称阴性）。
    """
    if isinstance(expected_tau_info, dict):
        value = expected_tau_info.get("valueSeconds")
        source = str(expected_tau_info.get("source") or "UNKNOWN")
        if isinstance(value, int | float) and not isinstance(value, bool) and float(value) > 0:
            # T′（2% 稳态带）→ τ：一阶系统 4τ 衰减到 2%（换算基准显式随结果落库）
            return float(value) / 4.0, f"t_prime:{source}/4"
        if source == "UNKNOWN":
            return None, "no_engineering_source"
    mapping = tau_map if isinstance(tau_map, dict) and tau_map else _DEFAULT_EXPECTED_TAU_SECONDS
    key = (loop_type or "OTHER").upper()
    try:
        return float(mapping.get(key, mapping.get("OTHER", 60.0))), "type_default"
    except (TypeError, ValueError):
        return 60.0, "type_default"


def _fit_first_order(
    t_fit: np.ndarray, pv_response: np.ndarray, y0: float, step_size: float
) -> tuple[float, float, float, str] | None:
    """一阶拟合 y(t)=y0+A(1−exp(−t/τ))——返回 (A, τ, rmse, method) 或 None.

    D4（08§5 slow 行）：y0 取事件前实测 PV（不默认旧 SP）；A 为自由参数
    （边界 |step| 的 0.05~5 倍，触边由调用方判"增益假设缺依据"）；
    method = curve_fit / crossing_fallback（拟合失败时 63.2% 穿越近似）。
    """
    window_seconds = float(t_fit[-1]) if len(t_fit) > 0 else 0.0
    tau_upper = max(window_seconds * 10.0, 1.0)

    def _model(t: np.ndarray, a: float, tau: float) -> np.ndarray:
        return y0 + a * (1.0 - np.exp(-t / np.maximum(tau, 1e-6)))

    try:
        from scipy.optimize import curve_fit

        a_lo, a_hi = sorted((step_size * _AMPLITUDE_BOUND_LOW, step_size * _AMPLITUDE_BOUND_HIGH))
        popt, _ = curve_fit(
            _model,
            t_fit,
            pv_response,
            p0=[step_size, max(window_seconds / 3.0, 1e-3)],
            bounds=([a_lo, 1e-3], [a_hi, tau_upper]),
            maxfev=2000,
        )
        a_fit, tau = float(popt[0]), float(popt[1])
        residual = pv_response - _model(t_fit, a_fit, tau)
        rmse = float(np.sqrt(np.mean(residual**2)))
        return a_fit, tau, rmse, "curve_fit"
    except Exception:  # noqa: BLE001
        # 拟合失败：尾部均值估 A + 63.2% 穿越近似 τ
        tail_len = max(1, len(pv_response) // 5)
        a_tail = float(np.mean(pv_response[-tail_len:])) - y0
        if abs(a_tail) < 1e-9:
            return None
        target = y0 + a_tail * _REACHED_FRACTION_MIN
        if a_tail > 0:
            reach_idx = np.where(pv_response >= target)[0]
        else:
            reach_idx = np.where(pv_response <= target)[0]
        tau = float(t_fit[reach_idx[0]]) if len(reach_idx) > 0 else window_seconds
        residual = pv_response - (y0 + a_tail * (1.0 - np.exp(-t_fit / max(tau, 1e-6))))
        rmse = float(np.sqrt(np.mean(residual**2)))
        return a_tail, tau, rmse, "crossing_fallback"


def _slow_kernel(
    pv: np.ndarray,
    sp: np.ndarray,
    loop_type: str | None,
    ts: np.ndarray | list[float] | None,
    *,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
    expected_tau_info: dict | None = None,
) -> dict[str, Any]:
    """响应迟缓检测（D1 截断 + D4 逐事件/实测初值/拟合质量深化）。

    D1（DIA-08，C16-D03）：响应窗截到**下一 SP 阶跃**（不混拟合）。
    D4（08§5 slow 行）：
    1. **逐合格事件**评估（不再只取首个）——窗口判定 = 任一合格事件判慢；
       代表特征取比值最差事件，多事件 τ **不平均**（不同事件的过渡过程
       属不同物理窗，平均会造出不存在的 τ）；
    2. 初值 y0 = 事件前实测 PV 均值（模型 y0+A(1−exp(−t/τ))），事件前
       未稳定（波动 > slow_pre_stability_ratio×|阶跃|）→ 该事件不评；
    3. 拟合质量（RMSE/|A| > slow_max_fit_rmse_ratio）或参数触边
       （τ/A 贴拟合边界）→ 不发布有效 τ（证据不足不确诊）；
    4. 响应完成度：窗末到达率 (PV_end−y0)/A < 63.2% → 响应未完成
       （INSUFFICIENT，τ 为窗外外推值不可靠——D1 fit_degenerate 判据语义化）。
    """
    if not isinstance(threshold, dict):
        threshold = {}
    ratio_threshold = float(threshold.get("slow_response_ratio_threshold", 2.0))
    no_step_bias_ratio = float(threshold.get("slow_no_step_bias_ratio", 0.2))
    pre_stability_ratio = float(threshold.get("slow_pre_stability_ratio", 0.2))
    max_fit_rmse_ratio = float(threshold.get("slow_max_fit_rmse_ratio", 0.15))
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

        expected_tau, expected_tau_source = _expected_time_constant(
            loop_type, tau_map, expected_tau_info
        )

        events = _dedup_step_events(_detect_sp_steps(sp_arr, sp_range))
        if not events:
            # 无阶跃：基于稳态偏差判断（不依赖 expected τ）
            bias = pv_arr - sp_arr
            bias_std = float(np.std(bias))
            ratio = bias_std / sp_range
            detected = bool(ratio > no_step_bias_ratio)
            confidence = min(0.8, ratio * 3) if detected else 0.0
            return {
                "detected": detected,
                "confidence": confidence,
                "time_constant": 0.0,
                "expected_time_constant": expected_tau,
                "expected_tau_source": expected_tau_source,
                "ratio": ratio,
            }

        event_records: list[dict[str, Any]] = []
        valid: list[dict[str, Any]] = []
        for e_i, (step_idx, settled_idx) in enumerate(events):
            step_size = float(sp_arr[settled_idx] - sp_arr[step_idx])
            record: dict[str, Any] = {
                "event": e_i,
                "start": settled_idx,
                "step_size": round(step_size, 4),
            }
            if abs(step_size) < 1e-9:
                record.update({"state": "SKIPPED", "reason": "zero_step"})
                event_records.append(record)
                continue

            next_start = events[e_i + 1][0] + 1 if e_i + 1 < len(events) else min_len
            response_end = min(next_start, min_len)
            pv_response = pv_arr[settled_idx:response_end]
            t_response = t_seconds[settled_idx:response_end]

            # D4：实测初值 y0——事件前窗口（≤120 采样，≥8 点）
            pre_lo = max(0, step_idx - _PRE_EVENT_WINDOW + 1)
            pre_pv = pv_arr[pre_lo : step_idx + 1]
            if len(pre_pv) < _PRE_EVENT_MIN_POINTS:
                record.update(
                    {
                        "state": "SKIPPED",
                        "reason": "pre_event_window_insufficient",
                        "pre_points": len(pre_pv),
                    }
                )
                event_records.append(record)
                continue
            y0 = float(np.mean(pre_pv))
            pre_std = float(np.std(pre_pv))
            if pre_std > pre_stability_ratio * abs(step_size):
                # 08§5"初始未稳…不发布有效 τ 阴性"——初值缺依据
                record.update(
                    {
                        "state": "SKIPPED",
                        "reason": "initial_not_steady",
                        "y0": round(y0, 4),
                        "pre_std": round(pre_std, 4),
                    }
                )
                event_records.append(record)
                continue
            record["y0"] = round(y0, 4)
            record["y0_source"] = "measured_pre_event_mean"

            if len(pv_response) < _MIN_RESPONSE_POINTS:
                record.update(
                    {
                        "state": "SKIPPED",
                        "reason": "response_incomplete",
                        "response_points": len(pv_response),
                    }
                )
                event_records.append(record)
                continue

            t_fit = t_response - t_response[0]
            window_seconds = float(t_fit[-1]) if len(t_fit) > 0 else 0.0
            if window_seconds < 1e-9:
                record.update({"state": "SKIPPED", "reason": "response_incomplete"})
                event_records.append(record)
                continue

            fit = _fit_first_order(t_fit, pv_response, y0, step_size)
            if fit is None:
                record.update({"state": "SKIPPED", "reason": "fit_failed"})
                event_records.append(record)
                continue
            a_fit, time_constant, rmse, fit_method = fit
            tau_upper = max(window_seconds * 10.0, 1.0)
            a_lo, a_hi = sorted(
                (step_size * _AMPLITUDE_BOUND_LOW, step_size * _AMPLITUDE_BOUND_HIGH)
            )
            # 触边判定按**数值方向**（a_lo < a_hi 恒成立；负阶跃时 a_lo 是大幅
            # 边界、a_hi 是小幅边界——不能按幅值绝对值比较）
            a_bound_span = a_hi - a_lo
            amplitude_boundary = bool(
                (a_fit <= a_lo + 0.01 * a_bound_span) or (a_fit >= a_hi - 0.01 * a_bound_span)
            )
            tau_boundary = time_constant >= 0.999 * tau_upper
            fit_rmse_ratio = rmse / max(abs(a_fit), 1e-9)
            fit_quality_ok = fit_rmse_ratio <= max_fit_rmse_ratio

            # D4：响应完成度——窗末到达率 <63.2% → 响应未完成（τ 外推不可靠）
            reached_fraction = float(
                np.clip((pv_response[-1] - y0) / a_fit if abs(a_fit) > 1e-9 else -1.0, -1.0, 1.0)
            )
            response_complete = reached_fraction >= _REACHED_FRACTION_MIN

            ratio = time_constant / expected_tau if expected_tau else 0.0
            event_valid = (
                response_complete and fit_quality_ok and not tau_boundary and not amplitude_boundary
            )
            event_detected = bool(event_valid and expected_tau and ratio > ratio_threshold)
            record.update(
                {
                    "state": "ASSESSED",
                    "end": response_end,
                    "response_points": len(pv_response),
                    "amplitude": round(a_fit, 4),
                    "amplitude_basis": (f"fitted(bounds {abs(a_lo):.3g}~{abs(a_hi):.3g} × |step|)"),
                    "time_constant": round(time_constant, 3),
                    "fit_rmse": round(rmse, 4),
                    "fit_rmse_ratio": round(fit_rmse_ratio, 4),
                    "fit_method": fit_method,
                    "reached_fraction": round(reached_fraction, 4),
                    "response_complete": bool(response_complete),
                    **({"tau_boundary": True} if tau_boundary else {}),
                    **({"amplitude_boundary": True} if amplitude_boundary else {}),
                    "expected_time_constant": expected_tau,
                    "expected_tau_source": expected_tau_source,
                    "ratio": round(ratio, 3),
                    "valid": bool(event_valid),
                    "detected": event_detected,
                    **(
                        {"reason": "response_incomplete"}
                        if not response_complete
                        else (
                            {"reason": "fit_quality"}
                            if not fit_quality_ok
                            else (
                                {"reason": "parameter_boundary"}
                                if (tau_boundary or amplitude_boundary)
                                else {}
                            )
                        )
                    ),
                }
            )
            event_records.append(record)
            if event_valid:
                valid.append(record)

        no_expected_tau = expected_tau is None

        if not valid:
            # 无任何合格事件（截断/初值未稳/拟合质量/触边）——响应未完成有原因；
            # 携带"最接近合格"事件的诊断特征（到达率/拟合值）供读端理解不足原因
            assessed = [e for e in event_records if e.get("state") == "ASSESSED"]
            diag = (
                max(assessed, key=lambda e: (e.get("reached_fraction", 0.0), e.get("ratio", 0.0)))
                if assessed
                else None
            )
            incomplete = any(e.get("reason") == "response_incomplete" for e in event_records)
            return {
                "detected": False,
                "confidence": 0.0,
                "time_constant": (diag or {}).get("time_constant", 0.0),
                "expected_time_constant": expected_tau,
                "expected_tau_source": expected_tau_source,
                "ratio": 0.0,
                **(
                    {
                        "y0": diag["y0"],
                        "amplitude": diag["amplitude"],
                        "amplitude_basis": diag["amplitude_basis"],
                        "fit_rmse_ratio": diag["fit_rmse_ratio"],
                        "reached_fraction": diag["reached_fraction"],
                    }
                    if diag
                    else {}
                ),
                "fit_degenerate": bool(incomplete),
                "reason": "no_valid_response_event",
                "events": event_records,
                "step_count": len(events),
                **({"no_expected_tau": True} if no_expected_tau else {}),
            }

        # 代表事件：判慢者中比值最差；无判慢取比值最大（最接近判慢证据）
        rep = max(valid, key=lambda r: (r["detected"], r["ratio"]))
        detected = bool(rep["detected"] and not no_expected_tau)
        confidence = min(0.9, rep["ratio"] / 10.0) if detected else 0.0
        return {
            "detected": detected,
            "confidence": confidence,
            "time_constant": rep["time_constant"],
            "expected_time_constant": expected_tau,
            "expected_tau_source": expected_tau_source,
            "ratio": rep["ratio"],
            "y0": rep["y0"],
            "amplitude": rep["amplitude"],
            "amplitude_basis": rep["amplitude_basis"],
            "fit_rmse_ratio": rep["fit_rmse_ratio"],
            "reached_fraction": rep["reached_fraction"],
            "fit_degenerate": not rep["response_complete"],
            "events": event_records,
            "step_count": len(events),
            "valid_event_count": len(valid),
            **({"no_expected_tau": True} if no_expected_tau else {}),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("响应迟缓检测失败: %s", exc)
        # D4：异常透传（错误不能被空结果吞掉）
        failed = _empty_slow_response_result()
        failed["_error"] = f"{type(exc).__name__}: {exc}"
        return failed


def _step_window_state(res: dict[str, Any]) -> tuple[str, str | None, str]:
    """过激算子窗口态：无事件→INSUFFICIENT；全手动/限幅→NOT_APPLICABLE；
    有评估事件→POSITIVE/NEGATIVE。返回 (state, reasonCode, 证据文案)。"""
    events = res.get("events") or []
    if not events:
        return (
            ASSESSMENT_INSUFFICIENT_DATA,
            REASON_NO_EVENTS,
            "窗口内无 SP 阶跃事件（变化 >5% SP 极差），无响应可评",
        )
    assessed = [e for e in events if e.get("state") == "ASSESSED"]
    if not assessed:
        reasons = [e.get("reason") for e in events if e.get("reason")]
        premise = [r for r in reasons if r in ("manual_mode", "op_saturated")]
        if premise and len(premise) == len(reasons):
            code = REASON_MANUAL_MODE if "manual_mode" in premise else REASON_OP_SATURATED
            text = (
                "事件均在手动模式（非 AUTO），阶跃响应反映手操轨迹——不判 PID 过激"
                if code == REASON_MANUAL_MODE
                else "事件窗口内输出持续贴限（执行器受限），过冲不归因控制器整定"
            )
            return ASSESSMENT_NOT_APPLICABLE, code, text
        return (
            ASSESSMENT_INSUFFICIENT_DATA,
            REASON_RESPONSE_INCOMPLETE,
            "SP 阶跃事件均被后续事件截断（响应未完成），无法评估响应指标",
        )
    state = ASSESSMENT_POSITIVE if res.get("detected") else ASSESSMENT_NEGATIVE
    return state, None, ""


@operator(
    OperatorMeta(
        name="step_response_overshoot",
        display_name="阶跃响应过激分析",
        family="tuning",
        diag_code="OVERAGGRESSIVE",
        description=(
            "SP 阶跃后过冲/衰减比/稳态误差三项指标满足 2 项即判过激；"
            "手动模式/输出限幅/响应未完成的事件不参与判定（AUTO 前提），"
            "末段未证稳定不称稳态误差"
        ),
        required_signals=("pv", "sp"),
        min_sample_rate=0.0,
        outputs_schema={
            "overshoot": "过冲量（阶跃幅度比例）",
            "decay_ratio": "衰减比 A2/A1",
            "steady_state_error": "稳态误差",
            "step_count": "阶跃事件数（去重后）",
        },
        threshold_schema={
            "step_overshoot_threshold": 0.25,
            "step_decay_ratio_threshold": 0.4,
            "step_sse_threshold": 0.05,
            "step_tail_stability_ratio": 0.1,
        },
        symptom_tags=("OVERAGGRESSIVE",),
        fast_group=True,
        confidence_basis=(
            "超调/衰减比/稳态误差 3 项指标满足 ≥2 项才判过激；置信度 = min(0.95, 满足项数 / 3)；"
            "手动模式/OP 贴限/响应未完成事件不评估（NOT_APPLICABLE/数据不足）"
        ),
    )
)
def detect_step(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or min(len(pv), len(sp)) < 16:
        return OperatorResult(
            "step_response_overshoot",
            executed=False,
            skip_reason="pv/sp 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    n = min(len(pv), len(sp))
    res = _step_kernel(
        pv,
        sp,
        threshold,
        # D4：机会性同轴消费（point 路径未对齐时不消费，见 _optional_aligned）
        mode=_optional_aligned(input, "mode", n),
        op=_optional_aligned(input, "op", n),
        op_range=input.meta.get("op_range"),
    )
    if res.get("_error"):
        # D4：内核异常 → ERROR（错误不能被空结果吞掉，不渲染成 NEGATIVE）
        return OperatorResult(
            "step_response_overshoot",
            executed=False,
            error=str(res["_error"]),
            assessment_state=ASSESSMENT_ERROR,
            reason_code=str(res["_error"])[:200],
        )
    state, reason_code, state_text = _step_window_state(res)
    # 无事件（INSUFFICIENT/NO_EVENTS）时内核已实际运行——executed=True，
    # 事件条件不满足由八态表达（与 slow 算子 no_event 同口径）
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
            # D4：事件级结果（去重/AUTO/限幅/末段稳定逐事件可核）
            "events": res.get("events") or [],
            "applied_parameters": {
                "step_overshoot_threshold": float(threshold.get("step_overshoot_threshold", 0.25)),
                "step_decay_ratio_threshold": float(
                    threshold.get("step_decay_ratio_threshold", 0.4)
                ),
                "step_sse_threshold": float(threshold.get("step_sse_threshold", 0.05)),
                "step_tail_stability_ratio": float(threshold.get("step_tail_stability_ratio", 0.1)),
            },
        },
        evidence=[
            EvidenceItem(
                "overshoot",
                round(float(res["overshoot"]), 4),
                threshold.get("step_overshoot_threshold"),
                (
                    state_text
                    if state_text
                    else "过冲量"
                    + ("超阈" if res["detected"] else "未超阈")
                    + (
                        "；稳态误差未证稳定（末段波动大）不参与命中计数"
                        if any(
                            e.get("sse_not_assessed") == "tail_unstable"
                            for e in (res.get("events") or [])
                            if e.get("state") == "ASSESSED"
                        )
                        else ""
                    )
                ),
            ),
        ],
        assessment_state=state,
        reason_code=reason_code,
    )


@operator(
    OperatorMeta(
        name="slow_response",
        display_name="响应迟缓检测",
        family="tuning",
        diag_code="OVERCONSERVATIVE",
        description=(
            "逐合格 SP 阶跃事件做一阶滞后拟合（实测初值 y0+A(1−exp(−t/τ))），"
            "时间常数与工程期望 τ（来源可查）比较，慢过阈值倍数判过保守；"
            "响应未完成/初值未稳/拟合质量差/参数触边不发布有效 τ"
        ),
        required_signals=("pv", "sp"),
        min_sample_rate=0.0,
        outputs_schema={
            "time_constant": "拟合时间常数(秒)",
            "expected_time_constant": "期望时间常数(秒)",
            "ratio": "实际/期望比值",
            "y0": "事件前实测初值",
            "amplitude": "拟合响应幅度 A",
            "reached_fraction": "响应窗末到达率",
        },
        threshold_schema={
            "slow_response_ratio_threshold": 2.0,
            "slow_no_step_bias_ratio": 0.2,
            "slow_pre_stability_ratio": 0.2,
            "slow_max_fit_rmse_ratio": 0.15,
            "slow_expected_tau_seconds": dict(_DEFAULT_EXPECTED_TAU_SECONDS),
        },
        symptom_tags=("OVERCONSERVATIVE",),
        fast_group=True,
        confidence_basis=(
            "有合格 SP 阶跃事件：min(0.9, 实际/期望时间常数比 ÷ 10)；"
            "无阶跃（稳态偏差）：min(0.8, 偏差比 × 3)；"
            "响应未完成（到达率 <63.2%）/初值未稳/拟合残差或参数触边不判（证据不足）"
        ),
    )
)
def detect_slow(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or min(len(pv), len(sp)) < 16:
        return OperatorResult(
            "slow_response",
            executed=False,
            skip_reason="pv/sp 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    res = _slow_kernel(
        pv,
        sp,
        input.meta.get("loop_type"),
        input.timestamps,
        sample_interval=float(input.meta.get("sample_interval", 1.0)),
        threshold=threshold,
        expected_tau_info=input.meta.get("expected_tau_info"),
    )
    if res.get("_error"):
        # D4：内核异常 → ERROR（错误不能被空结果吞掉，不渲染成 NEGATIVE）
        return OperatorResult(
            "slow_response",
            executed=False,
            error=str(res["_error"]),
            assessment_state=ASSESSMENT_ERROR,
            reason_code=str(res["_error"])[:200],
        )
    # D1（DIA-08，08§3.3）：expected τ 无工程来源——测得动态特征保留，
    # 但"比应有速度慢"的判定前提不成立（NOT_APPLICABLE，不伪称阴性）
    no_expected_tau = bool(res.get("no_expected_tau"))
    no_valid_event = res.get("reason") == "no_valid_response_event"
    events = res.get("events") or []
    incomplete_case = any(e.get("reason") == "response_incomplete" for e in events)
    if no_expected_tau:
        state, reason_code = ASSESSMENT_NOT_APPLICABLE, REASON_NO_EXPECTED_TAU
    elif no_valid_event:
        # D4：事件存在但全部不合格——响应未完成（截断/到达率）记
        # RESPONSE_INCOMPLETE；初值未稳/拟合质量/触边记 INSUFFICIENT_FIT
        # （证据不足不确诊，均不伪称阴性）
        state = ASSESSMENT_INSUFFICIENT_DATA
        reason_code = REASON_RESPONSE_INCOMPLETE if incomplete_case else REASON_INSUFFICIENT_FIT
    else:
        # 正常分支自赋 POSITIVE/NEGATIVE（直调算子不经编排层派生时状态仍完整）
        state = ASSESSMENT_POSITIVE if res["detected"] else ASSESSMENT_NEGATIVE
        reason_code = None
    return OperatorResult(
        "slow_response",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "time_constant": res["time_constant"],
            "expected_time_constant": res.get("expected_time_constant"),
            "expected_tau_source": res.get("expected_tau_source"),
            "ratio": res["ratio"],
            "y0": res.get("y0"),
            "amplitude": res.get("amplitude"),
            "amplitude_basis": res.get("amplitude_basis"),
            "fit_rmse_ratio": res.get("fit_rmse_ratio"),
            "reached_fraction": res.get("reached_fraction", 1.0),
            "fit_degenerate": res.get("fit_degenerate", False),
            "step_count": res.get("step_count", 0),
            "valid_event_count": res.get("valid_event_count", 0),
            # D4：事件级结果（y0/A/拟合质量/触边/完成度逐事件可核）
            "events": events,
            "applied_parameters": {
                "slow_response_ratio_threshold": float(
                    threshold.get("slow_response_ratio_threshold", 2.0)
                ),
                "slow_no_step_bias_ratio": float(threshold.get("slow_no_step_bias_ratio", 0.2)),
                "slow_pre_stability_ratio": float(threshold.get("slow_pre_stability_ratio", 0.2)),
                "slow_max_fit_rmse_ratio": float(threshold.get("slow_max_fit_rmse_ratio", 0.15)),
            },
            **({"no_expected_tau": True} if no_expected_tau else {}),
        },
        evidence=[
            EvidenceItem(
                "ratio",
                round(float(res["ratio"]), 3),
                threshold.get("slow_response_ratio_threshold"),
                (
                    "expected τ 无工程来源：只报告测得动态特征，不判断响应快慢"
                    if no_expected_tau
                    else (
                        "响应窗内 PV 到达率不足 63.2%，τ 为外推值不可靠（不判迟缓）"
                        if incomplete_case
                        else (
                            "响应事件不足（截断/初值未稳/拟合质量/触边），无法发布有效 τ"
                            if no_valid_event
                            else (
                                "响应窗内 PV 到达率不足 63.2%，τ 为外推值不可靠（不判迟缓）"
                                if res.get("fit_degenerate")
                                else "实际/期望时间常数比"
                                + ("超阈" if res["detected"] else "未超阈")
                                + (
                                    f"（{res.get('valid_event_count')} 个合格事件"
                                    "逐事件评估，代表最差比值）"
                                    if (res.get("valid_event_count") or 0) > 1
                                    else ""
                                )
                            )
                        )
                    )
                ),
            ),
        ],
        assessment_state=state,
        reason_code=reason_code,
    )
