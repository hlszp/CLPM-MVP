"""振荡族元算子：IAE 零交叉相似率 + FFT 频域分析。

内核等价复制自 app/tasks/diagnosis_engine.py（MVP v2 迁移，带等价性回归测试）：
- _fft_kernel   ← _detect_oscillation_fft（L1854-1968）
- _iae_kernel   ← _detect_oscillation_iae（L3797-3931）
无状态纪律见 base.py 模块 docstring。

D3（2026-10-11，08§3.2/§5 振荡两行）：
- 逐段评估：算子消费 D1 段目录（meta.consecutive_segments），FFT/IAE 逐段
  运行、段级结果落库（segment_results），窗口结论按"任一段阳性→阳性；
  无阳性且有阴性段→范围内阴性；无可评估段→数据不足"归并——不跨 gap
  拼接、不同段频率/相似率不平均（C14-D01/C14-D04）；
- FFT 区分"周期存在"与"工程显著异常"：谱条件命中但幅度低于工程门槛
  （fft_min_amplitude_ratio，默认量程 1%）只保留波动特征不判振荡；
- IAE 增加"幅度/完整半周期数/周期稳定性"元信息（相似率公式不动——
  08§5"新稳健判据先影子对比，不默改现相似率公式"）；段内按 SP 阶跃
  事件再切分（SP 瞬态不混入半周期相似率）；
- 内核异常 `_error` 透传 → ERROR 态（08§4.1"错误不能被空结果吞掉"，
  不再被空结果渲染成 NEGATIVE）。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.services.diagnosis_operators.base import (
    ASSESSMENT_ERROR,
    ASSESSMENT_INSUFFICIENT_DATA,
    REASON_TOO_FEW_POINTS,
    EvidenceItem,
    OperatorInput,
    OperatorMeta,
    OperatorResult,
    merge_segment_states,
    operator,
    segment_bounds,
)
from app.services.metric_calculator.oscillation import (
    _DEFAULT_MAX_RATIO,
    _DEFAULT_MIN_RATIO,
    MIN_ZERO_CROSSINGS,
    OscillationRateCalculator,
)

logger = logging.getLogger(__name__)

# _detect_oscillation_iae 引用的半周期门控常量（与引擎 L3844 读取键一致）
from app.services.metric_calculator.stiction import MIN_HALF_PERIOD_SAMPLES  # noqa: E402

#: FFT 工程显著异常的最低幅度占比（主峰幅值 / PV 量程；量程缺失用段内极差）
DEFAULT_MIN_AMPLITUDE_RATIO = 0.01


def _empty_osc_result() -> dict[str, Any]:
    """空振荡检测结果（复制自引擎 L1797-1807）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "amplitude": 0.0,
        "frequency": 0.0,
        "index": 0.0,
        "frequencies": [],
        "amplitudes": [],
    }


def _fft_kernel(
    pv_values: np.ndarray,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """FFT 频域分析检测振荡（等价复制自引擎 _detect_oscillation_fft L1854-1968）。

    判定四条件：振荡指数 / 零交叉数 / 窗口完整周期数 / 频谱信噪比均达阈值。
    （单窗纯函数——逐段循环与幅度工程门槛在算子层，见 detect_fft。）
    """
    if not isinstance(threshold, dict):
        threshold = {}
    osc_index_threshold = float(threshold.get("fft_osc_index_threshold", 0.3))
    min_zero_crossings = int(threshold.get("fft_min_zero_crossings", 5))
    min_cycles = float(threshold.get("fft_min_cycles", 2.0))
    min_snr = float(threshold.get("fft_min_snr", 6.0))

    if len(pv_values) < 8:
        return _empty_osc_result()

    try:
        n = len(pv_values)
        fs = 1.0 / sample_interval if sample_interval > 0 else 1.0  # 采样频率 (Hz)
        # 去均值
        pv_centered = pv_values - np.mean(pv_values)
        # Hann 窗抑制频谱泄漏；幅值归一化分母 Σw 同时补偿窗的相干增益
        window = np.hanning(n)
        window_sum = float(np.sum(window))
        if window_sum <= 0:
            return _empty_osc_result()
        fft_vals = np.fft.rfft(pv_centered * window)
        fft_magnitude = np.abs(fft_vals)
        if len(fft_magnitude) <= 1:
            return _empty_osc_result()
        peak_idx = int(np.argmax(fft_magnitude[1:])) + 1
        # 单边谱幅值：2·|X(k)|/Σw
        amplitude = float(2.0 * fft_magnitude[peak_idx] / window_sum)
        frequency = float(peak_idx * fs / n)

        # 振荡指数：主频能量占比
        total_energy = float(np.sum(fft_magnitude[1:] ** 2))
        if total_energy <= 0:
            return _empty_osc_result()
        peak_energy = float(fft_magnitude[peak_idx] ** 2)
        osc_index = peak_energy / total_energy

        # IAE 零交叉检测
        zero_crossings = int(np.sum(np.diff(np.sign(pv_centered)) != 0))

        # 主峰完整性：窗口内完整周期数 = peak_idx
        cycles = float(peak_idx)
        # 频谱信噪比：主峰幅值 / 噪声底（除主峰外其余频点幅值中位数）
        noise_floor = float(np.median(np.delete(fft_magnitude[1:], peak_idx - 1)))
        spectral_snr = float(fft_magnitude[peak_idx]) / (noise_floor + 1e-12)

        detected = bool(
            osc_index > osc_index_threshold
            and zero_crossings > min_zero_crossings
            and cycles >= min_cycles
            and spectral_snr >= min_snr
        )
        confidence = min(1.0, osc_index * 1.5) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "amplitude": amplitude,
            "frequency": frequency,
            "index": osc_index,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("FFT 振荡检测失败: %s", exc)
        # D2（2026-09-25）：异常必须随结果落库，否则"已执行但失败"在前端与
        # 落库结果里完全不可见（原来只写日志，operator_results.error 为 null）。
        # D3（2026-10-11）：算子层消费该标记 → ASSESSMENT_ERROR（不渲染成 NEGATIVE）。
        failed = _empty_osc_result()
        failed["_error"] = f"{type(exc).__name__}: {exc}"
        return failed


def _iae_kernel(
    pv: np.ndarray,
    sp: np.ndarray,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """IAE 零交叉相似率法振荡检测（等价复制自引擎 _detect_oscillation_iae L3797-3931）。

    复用 KPI 侧 OscillationRateCalculator 静态方法，保证与 KPI 振荡率口径一致；
    附半周期门控抗白噪声伪穿越。

    D3（08§5 IAE 行）：相似率公式**不动**；新增幅度/完整半周期数/周期稳定性
    元信息键（mean_abs_error / complete_half_periods / half_period_cv）与
    周期不足原因码（too_few_crossings，仅 2≤穿越数<门限的"半周期数不足
    不可判"窗；整窗无符号翻转=未检测到本方法定义的符号交替振荡，仍为阴性）
    ——数值键行为与引擎等价保持。
    """
    if not isinstance(threshold, dict):
        threshold = {}
    similarity_threshold = float(threshold.get("similarity_threshold", 0.4))
    min_zero_crossings = int(threshold.get("min_zero_crossings", MIN_ZERO_CROSSINGS))
    min_half_period = float(threshold.get("min_half_period_samples", MIN_HALF_PERIOD_SAMPLES))

    min_len = min(len(pv), len(sp))
    if min_len < 8:
        return {
            "detected": False,
            "confidence": 0.0,
            "similarity": 0.0,
            "zero_crossing_count": 0,
            "mean_period": 0.0,
        }

    try:
        # 1. 计算控制偏差
        error = pv[:min_len].astype(float) - sp[:min_len].astype(float)
        mean_abs_error = float(np.mean(np.abs(error)))

        # 2. 识别零交叉点（复用 KPI 侧向量化实现）
        zero_crossings = OscillationRateCalculator._find_zero_crossings(error)
        n_crossings = len(zero_crossings)
        if n_crossings < max(min_zero_crossings, 2):
            return {
                "detected": False,
                "confidence": 0.0,
                "similarity": 0.0,
                "zero_crossing_count": n_crossings,
                "mean_period": 0.0,
                "mean_abs_error": mean_abs_error,
                "complete_half_periods": 0,
                # 2≤穿越<门限：半周期数不足以评估规律性 → "周期不足"不可判
                # （穿越=0/1：整窗无符号交替，未检测到本方法定义的振荡=阴性）
                **({"reason": "too_few_crossings"} if n_crossings >= 2 else {}),
            }

        # 3. 相邻零交叉间完整半周期的 IAE
        segments = OscillationRateCalculator._compute_iae_segments(error, zero_crossings)
        pos_iae = [s[0] for s in segments if s[2] > 0]
        neg_iae = [s[0] for s in segments if s[2] < 0]
        if not pos_iae or not neg_iae:
            return {
                "detected": False,
                "confidence": 0.0,
                "similarity": 0.0,
                "zero_crossing_count": n_crossings,
                "mean_period": 0.0,
                "mean_abs_error": mean_abs_error,
                "complete_half_periods": 0,
                "reason": "too_few_crossings",
            }

        # 4. IAE 相似率 S_A/S_B（最小距离法）
        s_a = OscillationRateCalculator._similarity_rate(
            pos_iae, _DEFAULT_MIN_RATIO, _DEFAULT_MAX_RATIO
        )
        s_b = OscillationRateCalculator._similarity_rate(
            neg_iae, _DEFAULT_MIN_RATIO, _DEFAULT_MAX_RATIO
        )
        similarity = min(s_a, s_b)

        # 5. 振荡判定：双侧相似率均达阈值且平均半周期达标
        durations = [s[1] for s in segments]
        mean_half_period = float(np.mean(durations)) if durations else 0.0
        detected = bool(
            s_a >= similarity_threshold
            and s_b >= similarity_threshold
            and mean_half_period >= min_half_period
        )

        # 6. 置信度
        confidence = min(1.0, similarity * 1.5) if detected else 0.0

        mean_period_samples = mean_half_period * 2.0
        mean_period = (
            mean_period_samples * sample_interval if sample_interval > 0 else mean_period_samples
        )

        # D3 元信息（08§5）：完整半周期数与周期稳定性（半周期时长的变异系数）
        dur_arr = np.asarray(durations, dtype=float)
        half_period_cv = (
            float(np.std(dur_arr) / np.mean(dur_arr)) if float(np.mean(dur_arr)) > 0 else None
        )

        return {
            "detected": detected,
            "confidence": confidence,
            "similarity": similarity,
            "zero_crossing_count": n_crossings,
            "mean_period": float(mean_period),
            "mean_abs_error": mean_abs_error,
            "complete_half_periods": len(segments),
            "half_period_cv": round(half_period_cv, 4) if half_period_cv is not None else None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("IAE 零交叉振荡检测失败: %s", exc)
        # D3：异常透传（错误不能被空结果吞掉——原先空结果会被渲染成 NEGATIVE）
        return {
            "detected": False,
            "confidence": 0.0,
            "similarity": 0.0,
            "zero_crossing_count": 0,
            "mean_period": 0.0,
            "_error": f"{type(exc).__name__}: {exc}",
        }


def _segment_sample_interval(ts: np.ndarray, start: int, end: int, default: float) -> float:
    """段内实际采样间隔（秒）：按真实时间轴估计，失败回退 default。"""
    if end - start >= 2 and len(ts) >= end:
        seg_ts = np.asarray(ts[start:end], dtype=float)
        span = float(seg_ts[-1] - seg_ts[0])
        if span > 0:
            return span / (len(seg_ts) - 1)
    return default if default > 0 else 1.0


def _range_span(meta: dict[str, Any], key: str) -> float | None:
    """meta 量程（pv_range/op_range）跨度；缺失/非法返回 None。"""
    rng = meta.get(key)
    try:
        lo, hi = float(rng[0]), float(rng[1])  # type: ignore[index]
        span = hi - lo
        return span if span > 0 else None
    except (TypeError, ValueError, IndexError):
        return None


@operator(
    OperatorMeta(
        name="oscillation_fft",
        display_name="FFT 频域振荡检测",
        family="oscillation",
        diag_code="OSCILLATION",
        description=(
            "对 PV 逐连续段做 FFT（Hann 窗），主峰能量占比+完整周期数+信噪比"
            "+幅度工程门槛判定振荡；段级结果独立落库不跨 gap"
        ),
        required_signals=("pv",),
        min_sample_rate=0.0,
        outputs_schema={
            "frequency": "主峰频率(Hz)",
            "period": "主峰周期(秒)",
            "amplitude": "主峰幅值(工程单位)",
            "amplitude_range_ratio": "主峰幅值/量程占比",
            "index": "振荡指数",
        },
        threshold_schema={
            "fft_osc_index_threshold": 0.3,
            "fft_min_zero_crossings": 5,
            "fft_min_cycles": 2.0,
            "fft_min_snr": 6.0,
            "fft_min_amplitude_ratio": DEFAULT_MIN_AMPLITUDE_RATIO,
        },
        symptom_tags=("OSCILLATION",),
        fast_group=False,
        confidence_basis=(
            "命中（能量占比/完整周期数/信噪比/幅度工程门槛四条件同时满足）时"
            " min(1.0, 主峰能量占比 × 1.5)"
        ),
    )
)
def detect_fft(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    if pv is None or len(pv) < 16:
        return OperatorResult(
            "oscillation_fft",
            executed=False,
            skip_reason="pv 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    # D1 兜底（2026-09-25）：点表同轴输入历史上可能仍是 object dtype，
    # 数值 ufunc（rfft 等）遇 object 直接抛错；此处强制转 float64 并让异常可见。
    try:
        pv = np.asarray(pv, dtype=float)
    except (TypeError, ValueError) as exc:
        logger.warning("FFT 振荡检测输入非数值: %s", exc)
        return OperatorResult(
            "oscillation_fft",
            executed=False,
            error=f"{type(exc).__name__}: {exc}",
            assessment_state=ASSESSMENT_ERROR,
            reason_code=f"{type(exc).__name__}: {exc}"[:200],
        )
    # D3（08§3.2/C14-D01/C14-D04，逐段评估）：消费 D1 段目录，每段独立 FFT——
    # 段级结果保留（不同段不同频率不平均），窗口结论按段归并；缺口两侧
    # 永不拼接（拼接会在缺口处制造虚假低频分量）。单段（无缺口）与原行为一致。
    segments = segment_bounds(input.meta, len(pv))
    ts_arr = np.asarray(input.timestamps, dtype=float)
    min_amp_ratio = float(threshold.get("fft_min_amplitude_ratio", DEFAULT_MIN_AMPLITUDE_RATIO))
    pv_span = _range_span(input.meta, "pv_range")

    segment_records: list[dict[str, Any]] = []
    for start, end in segments:
        n_seg = end - start
        if n_seg < 16:
            if n_seg >= 2:
                segment_records.append(
                    {
                        "start": start,
                        "end": end,
                        "points": n_seg,
                        "state": "INSUFFICIENT_DATA",
                        "reason": "TOO_FEW_POINTS",
                    }
                )
            continue
        seg_pv = pv[start:end]
        fs_interval = _segment_sample_interval(
            ts_arr, start, end, float(input.meta.get("sample_interval", 1.0))
        )
        res = _fft_kernel(seg_pv, fs_interval, threshold)
        if res.get("_error"):
            # D3：内核异常 → ERROR（错误不能被空结果吞掉）
            return OperatorResult(
                "oscillation_fft",
                executed=False,
                error=str(res["_error"]),
                assessment_state=ASSESSMENT_ERROR,
                reason_code=str(res["_error"])[:200],
            )
        seg_span = pv_span if pv_span is not None else float(np.max(seg_pv) - np.min(seg_pv))
        amp_ratio = float(res["amplitude"]) / seg_span if seg_span > 0 else None
        spectral_detected = bool(res["detected"])
        # D3（08§5 FFT 行）：区分"周期存在"与"工程显著异常"——谱条件命中但
        # 幅度低于工程门槛（量程占比 < fft_min_amplitude_ratio）只保留波动特征
        significant = amp_ratio is None or amp_ratio >= min_amp_ratio
        seg_detected = spectral_detected and significant
        segment_records.append(
            {
                "start": start,
                "end": end,
                "points": n_seg,
                "state": "POSITIVE" if seg_detected else "NEGATIVE",
                "frequency": round(float(res["frequency"]), 8),
                "period": (
                    round(1.0 / float(res["frequency"]), 3) if res["frequency"] > 0 else None
                ),
                "amplitude": round(float(res["amplitude"]), 4),
                "amplitude_range_ratio": (round(amp_ratio, 6) if amp_ratio is not None else None),
                "index": round(float(res["index"]), 6),
                "sample_interval": round(fs_interval, 4),
                **(
                    {"spectral_hit_without_significance": True}
                    if spectral_detected and not significant
                    else {}
                ),
            }
        )

    evaluated = [r for r in segment_records if r.get("state") in ("POSITIVE", "NEGATIVE")]
    if not evaluated:
        # 全部段 <16 点（缺口分割后无可分析窗）——数据不足，不伪称阴性
        return OperatorResult(
            "oscillation_fft",
            executed=False,
            skip_reason="最长连续有效段不足（缺口分割后 <16 点）",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )

    # 代表段：首个阳性段；无阳性取振荡指数最大者（tie → 点数多 → 更早）
    detected_any = any(r["state"] == "POSITIVE" for r in evaluated)
    if detected_any:
        rep = next(r for r in evaluated if r["state"] == "POSITIVE")
        rep_confidence = min(1.0, rep["index"] * 1.5)
    else:
        rep = max(evaluated, key=lambda r: (r["index"], r["points"], -r["start"]))
        rep_confidence = 0.0

    nonsignificant = [r for r in evaluated if r.get("spectral_hit_without_significance")]
    merged_state = merge_segment_states([r.get("state") or "" for r in segment_records])
    return OperatorResult(
        "oscillation_fft",
        executed=True,
        detected=detected_any,
        confidence=float(rep_confidence),
        features={
            "frequency": rep["frequency"],
            "period": rep["period"],
            "amplitude": rep["amplitude"],
            "amplitude_range_ratio": rep["amplitude_range_ratio"],
            "index": rep["index"],
            # D1：连续段消费透明化（段数/选用段点数/段内实际采样间隔）
            "consecutive_segment_count": len(segments),
            "evaluated_segment_count": len(evaluated),
            "analyzed_points": rep["points"],
            "analyzed_sample_interval": rep["sample_interval"],
            # D3：段级结果（08§3.2 每段单独特征与状态；不同段频率不平均）
            "segment_results": segment_records,
            # D3：实际生效参数随结果落库（支持度/参数保存，D1 载体承接）
            "applied_parameters": {
                "fft_osc_index_threshold": float(threshold.get("fft_osc_index_threshold", 0.3)),
                "fft_min_zero_crossings": int(threshold.get("fft_min_zero_crossings", 5)),
                "fft_min_cycles": float(threshold.get("fft_min_cycles", 2.0)),
                "fft_min_snr": float(threshold.get("fft_min_snr", 6.0)),
                "fft_min_amplitude_ratio": min_amp_ratio,
            },
        },
        evidence=[
            EvidenceItem(
                # 特征名与 outputs_schema 键保持一致（"index"=振荡指数），
                # 否则前端按注册表中文名映射不到，证据表显示英文原名
                "index",
                rep["index"],
                threshold.get("fft_osc_index_threshold"),
                "主峰能量占比"
                + ("达标" if detected_any else "未达标")
                + (
                    f"（共 {len(segments)} 连续段，逐段评估 {len(evaluated)} 段）"
                    if len(segments) > 1 or len(evaluated) < len(segments)
                    else ""
                )
                + (
                    f"；另 {len(nonsignificant)} 段谱峰幅度低于工程门槛"
                    f"（量程占比<{min_amp_ratio:.2%}），仅保留波动特征"
                    if nonsignificant
                    else ""
                ),
            ),
        ],
        assessment_state=merged_state,
    )


@operator(
    OperatorMeta(
        name="oscillation_iae",
        display_name="IAE 零交叉振荡检测",
        family="oscillation",
        diag_code="OSCILLATION",
        description=(
            "控制偏差 IAE 半周期相似率法检测振荡（与 KPI 振荡率同口径）；"
            "逐连续段评估且段内按 SP 阶跃再切分，附幅度/完整半周期/周期稳定性元信息"
        ),
        required_signals=("pv", "sp"),
        min_sample_rate=0.0,
        outputs_schema={
            "similarity": "半周期相似率",
            "zero_crossing_count": "零交叉数",
            "mean_period": "平均周期(秒)",
            "mean_abs_error": "平均绝对偏差(工程单位)",
            "complete_half_periods": "完整半周期数",
            "half_period_cv": "半周期时长变异系数",
        },
        threshold_schema={
            "similarity_threshold": 0.4,
            "min_zero_crossings": 4,
            "min_half_period_samples": 8,
        },
        symptom_tags=("OSCILLATION",),
        fast_group=True,
        confidence_basis="命中（IAE 相似率与过零密度双门）时 min(1.0, 正负偏差积分相似率 × 1.5)",
    )
)
def detect_iae(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or len(pv) < 16 or len(sp) < 16:
        return OperatorResult(
            "oscillation_iae",
            executed=False,
            skip_reason="pv/sp 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    try:
        pv = np.asarray(pv, dtype=float)
        sp = np.asarray(sp, dtype=float)
    except (TypeError, ValueError) as exc:
        logger.warning("IAE 振荡检测输入非数值: %s", exc)
        return OperatorResult(
            "oscillation_iae",
            executed=False,
            error=f"{type(exc).__name__}: {exc}",
            assessment_state=ASSESSMENT_ERROR,
            reason_code=f"{type(exc).__name__}: {exc}"[:200],
        )

    # D3 逐段评估（08§3.2）：连续段目录 ∩ 段内 SP 阶跃切分——缺口两侧不拼接，
    # SP 阶跃瞬态（单一半周期的过渡偏差）不混入半周期相似率
    n = min(len(pv), len(sp))
    segments = segment_bounds(input.meta, n)
    ts_arr = np.asarray(input.timestamps, dtype=float)
    sp_min, sp_max = float(np.min(sp[:n])), float(np.max(sp[:n]))
    sp_span = sp_max - sp_min

    eval_windows: list[tuple[int, int]] = []
    for start, end in segments:
        if sp_span < 1e-9:
            eval_windows.append((start, end))
            continue
        # 段内 SP 阶跃（>5% SP 极差，与响应算子同判据）作为附加切分边界
        sub = sp[start:end]
        step_threshold = sp_span * 0.05
        step_local = np.where(np.abs(np.diff(sub)) > step_threshold)[0]
        cuts = [start] + [start + int(i) + 1 for i in step_local] + [end]
        for a, b in zip(cuts[:-1], cuts[1:], strict=True):
            eval_windows.append((a, b))

    segment_records: list[dict[str, Any]] = []
    for start, end in eval_windows:
        n_win = end - start
        if n_win < 16:
            if n_win >= 2:
                segment_records.append(
                    {
                        "start": start,
                        "end": end,
                        "points": n_win,
                        "state": "INSUFFICIENT_DATA",
                        "reason": "TOO_FEW_POINTS",
                    }
                )
            continue
        win_interval = _segment_sample_interval(
            ts_arr, start, end, float(input.meta.get("sample_interval", 1.0))
        )
        res = _iae_kernel(pv[start:end], sp[start:end], win_interval, threshold)
        if res.get("_error"):
            return OperatorResult(
                "oscillation_iae",
                executed=False,
                error=str(res["_error"]),
                assessment_state=ASSESSMENT_ERROR,
                reason_code=str(res["_error"])[:200],
            )
        if res.get("reason") == "too_few_crossings":
            # D3（08§5 FFT 行同口径："周期不足为不足"）——过零/完整半周期不足
            # 的窗不评估相似率，不冒充范围内阴性
            state = "INSUFFICIENT_DATA"
        else:
            state = "POSITIVE" if res["detected"] else "NEGATIVE"
        segment_records.append(
            {
                "start": start,
                "end": end,
                "points": n_win,
                "state": state,
                **({"reason": "TOO_FEW_CROSSINGS"} if state == "INSUFFICIENT_DATA" else {}),
                "similarity": round(float(res["similarity"]), 4),
                "zero_crossing_count": int(res["zero_crossing_count"]),
                "mean_period": round(float(res["mean_period"]), 3),
                "mean_abs_error": round(float(res.get("mean_abs_error", 0.0)), 4),
                "complete_half_periods": int(res.get("complete_half_periods", 0)),
                "half_period_cv": res.get("half_period_cv"),
                "sample_interval": round(win_interval, 4),
            }
        )

    evaluated = [r for r in segment_records if r.get("state") in ("POSITIVE", "NEGATIVE")]
    if not evaluated:
        # 全部窗周期不足/太短——数据不足（范围内无可判窗），不伪称阴性
        return OperatorResult(
            "oscillation_iae",
            executed=False,
            skip_reason="连续段/SP 事件切分后无合格评估窗（周期不足或 <16 点）",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )

    detected_any = any(r["state"] == "POSITIVE" for r in evaluated)
    if detected_any:
        rep = next(r for r in evaluated if r["state"] == "POSITIVE")
        rep_confidence = min(1.0, rep["similarity"] * 1.5)
    else:
        rep = max(evaluated, key=lambda r: (r["similarity"], r["points"], -r["start"]))
        rep_confidence = 0.0

    insufficient_count = sum(1 for r in segment_records if r.get("state") == "INSUFFICIENT_DATA")
    merged_state = merge_segment_states([r.get("state") or "" for r in segment_records])
    return OperatorResult(
        "oscillation_iae",
        executed=True,
        detected=detected_any,
        confidence=float(rep_confidence),
        features={
            "similarity": rep["similarity"],
            "zero_crossing_count": rep["zero_crossing_count"],
            "mean_period": rep["mean_period"],
            # D3 元信息（08§5 IAE 行）：幅度/完整半周期/周期稳定性（代表窗）
            "mean_abs_error": rep["mean_abs_error"],
            "complete_half_periods": rep["complete_half_periods"],
            "half_period_cv": rep["half_period_cv"],
            "evaluated_window_count": len(evaluated),
            "window_total": len(segment_records),
            # 段级结果（每段单独特征与状态；不同段相似率/周期不平均）
            "segment_results": segment_records,
            "applied_parameters": {
                "similarity_threshold": float(threshold.get("similarity_threshold", 0.4)),
                "min_zero_crossings": int(threshold.get("min_zero_crossings", 4)),
                "min_half_period_samples": float(threshold.get("min_half_period_samples", 8)),
            },
        },
        evidence=[
            EvidenceItem(
                "similarity",
                rep["similarity"],
                threshold.get("similarity_threshold"),
                "半周期相似率"
                + ("达标" if detected_any else "未达标")
                + (
                    f"（{len(evaluated)}/{len(segment_records)} 窗完成评估"
                    + (
                        f"，另 {insufficient_count} 窗周期不足不计阴性"
                        if insufficient_count
                        else ""
                    )
                    + "）"
                    if len(segment_records) > 1
                    else ""
                ),
            ),
        ],
        assessment_state=merged_state,
    )
