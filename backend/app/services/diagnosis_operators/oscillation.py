"""振荡族元算子：IAE 零交叉相似率 + FFT 频域分析。

内核等价复制自 app/tasks/diagnosis_engine.py（MVP v2 迁移，带等价性回归测试）：
- _fft_kernel   ← _detect_oscillation_fft（L1854-1968）
- _iae_kernel   ← _detect_oscillation_iae（L3797-3931）
无状态纪律见 base.py 模块 docstring。
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
from app.services.metric_calculator.oscillation import (
    _DEFAULT_MAX_RATIO,
    _DEFAULT_MIN_RATIO,
    MIN_ZERO_CROSSINGS,
    OscillationRateCalculator,
)

logger = logging.getLogger(__name__)

# _detect_oscillation_iae 引用的半周期门控常量（与引擎 L3844 读取键一致）
from app.services.metric_calculator.stiction import MIN_HALF_PERIOD_SAMPLES  # noqa: E402


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
        return _empty_osc_result()


def _iae_kernel(
    pv: np.ndarray,
    sp: np.ndarray,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """IAE 零交叉相似率法振荡检测（等价复制自引擎 _detect_oscillation_iae L3797-3931）。

    复用 KPI 侧 OscillationRateCalculator 静态方法，保证与 KPI 振荡率口径一致；
    附半周期门控抗白噪声伪穿越。
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

        return {
            "detected": detected,
            "confidence": confidence,
            "similarity": similarity,
            "zero_crossing_count": n_crossings,
            "mean_period": float(mean_period),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("IAE 零交叉振荡检测失败: %s", exc)
        return {
            "detected": False,
            "confidence": 0.0,
            "similarity": 0.0,
            "zero_crossing_count": 0,
            "mean_period": 0.0,
        }


@operator(
    OperatorMeta(
        name="oscillation_fft",
        display_name="FFT 频域振荡检测",
        family="oscillation",
        diag_code="OSCILLATION",
        description="对 PV 做 FFT（Hann 窗），主峰能量占比+完整周期数+信噪比判定振荡",
        required_signals=("pv",),
        min_sample_rate=0.0,
        outputs_schema={"frequency": "主峰频率(Hz)", "amplitude": "主峰幅值", "index": "振荡指数"},
        threshold_schema={
            "fft_osc_index_threshold": 0.3,
            "fft_min_zero_crossings": 5,
            "fft_min_cycles": 2.0,
            "fft_min_snr": 6.0,
        },
        symptom_tags=("OSCILLATION",),
        fast_group=False,
    )
)
def detect_fft(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    if pv is None or len(pv) < 16:
        return OperatorResult("oscillation_fft", executed=False, skip_reason="pv 数据不足")
    res = _fft_kernel(pv, float(input.meta.get("sample_interval", 1.0)), threshold)
    return OperatorResult(
        "oscillation_fft",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "frequency": res["frequency"],
            "amplitude": res["amplitude"],
            "index": res["index"],
        },
        evidence=[
            EvidenceItem(
                "osc_index",
                round(float(res["index"]), 4),
                threshold.get("fft_osc_index_threshold"),
                "主峰能量占比" + ("达标" if res["detected"] else "未达标"),
            ),
        ],
    )


@operator(
    OperatorMeta(
        name="oscillation_iae",
        display_name="IAE 零交叉振荡检测",
        family="oscillation",
        diag_code="OSCILLATION",
        description="控制偏差 IAE 半周期相似率法检测振荡（与 KPI 振荡率同口径）",
        required_signals=("pv", "sp"),
        min_sample_rate=0.0,
        outputs_schema={
            "similarity": "半周期相似率",
            "zero_crossing_count": "零交叉数",
            "mean_period": "平均周期(秒)",
        },
        threshold_schema={
            "similarity_threshold": 0.4,
            "min_zero_crossings": 4,
            "min_half_period_samples": 8,
        },
        symptom_tags=("OSCILLATION",),
        fast_group=True,
    )
)
def detect_iae(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    sp = input.signals.get("sp")
    if pv is None or sp is None or len(pv) < 16:
        return OperatorResult("oscillation_iae", executed=False, skip_reason="pv/sp 数据不足")
    res = _iae_kernel(pv, sp, float(input.meta.get("sample_interval", 1.0)), threshold)
    return OperatorResult(
        "oscillation_iae",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "similarity": res["similarity"],
            "zero_crossing_count": res["zero_crossing_count"],
            "mean_period": res["mean_period"],
        },
        evidence=[
            EvidenceItem(
                "similarity",
                round(float(res["similarity"]), 4),
                threshold.get("similarity_threshold"),
                "半周期相似率" + ("达标" if res["detected"] else "未达标"),
            ),
        ],
    )
