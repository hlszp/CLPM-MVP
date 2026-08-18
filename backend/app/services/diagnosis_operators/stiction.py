"""粘滞族元算子：椭圆拟合 / Choudhury NGI-NLI / Kano 统计法。

内核等价复制自 app/tasks/diagnosis_engine.py：
- _ellipse_kernel   ← _detect_valve_stiction（L1971-2022，复用 KPI 侧内核）
- _choudhury_kernel ← _detect_choudhury_nonlinearity（L2710-2806）
                      + _compute_max_bicoherence（L2645-2707）
- _kano_kernel      ← _detect_kano_stiction（L2809-2913）
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
from app.services.metric_calculator.stiction import (
    MIN_FITTING_SCORE,
    assess_stiction_features,
)

logger = logging.getLogger(__name__)


def _empty_stiction_result() -> dict[str, Any]:
    """空粘滞检测结果（复制自引擎 L1810-1817）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "stiction_index": 0.0,
        "fitting_score": 0.0,
    }


def _empty_choudhury_result() -> dict[str, Any]:
    """空 Choudhury 非线性检测结果（复制自引擎 L2578-2587）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "ngi": 0.0,
        "nli": 0.0,
        "stiction_index": 0.0,
        "fitting_score": 0.0,
    }


def _empty_kano_result() -> dict[str, Any]:
    """空 Kano 粘滞检测结果（复制自引擎 L2590-2598）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "stiction_ratio": 0.0,
        "correlation": 0.0,
        "std_ratio": 0.0,
    }


def _ellipse_kernel(
    pv_values: np.ndarray,
    op_values: np.ndarray,
    sample_interval: float = 1.0,
) -> dict[str, Any]:
    """PV-OP 散点椭圆拟合检测阀门粘滞（等价复制自引擎 L1970-2022）。

    复用 KPI 侧 assess_stiction_features（GB/T 44693.2-2024 F.2 口径，
    含互相关 θ 补偿 + 极限环门控 + R² 拟合度）。
    """
    min_len = min(len(pv_values), len(op_values))
    if min_len < 8:
        return _empty_stiction_result()

    try:
        feat = assess_stiction_features(pv_values, op_values, sample_interval=sample_interval)
        fitting_score = float(feat.get("fitting_score", 0.0))
        stiction_index = float(feat.get("stiction_index", 0.0))
        is_limit_cycle = bool(feat.get("is_limit_cycle", False))

        # 判定：极限环 + R²≥0.5 + 短长轴比>0.3（对齐 KPI SEVERE 等级）
        detected = is_limit_cycle and fitting_score >= MIN_FITTING_SCORE and stiction_index > 0.3
        confidence = min(1.0, (fitting_score + stiction_index) / 2) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "stiction_index": stiction_index,
            "fitting_score": fitting_score,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("阀门粘滞检测失败: %s", exc)
        return _empty_stiction_result()


def _compute_max_bicoherence(signal: np.ndarray, n_seg: int = 4, n_freq: int = 16) -> float:
    """计算信号的最大双相干性（NLI 近似，等价复制自引擎 L2645-2707）。"""
    n = len(signal)
    seg_len = n // n_seg
    if seg_len < 8:
        return 0.0

    try:
        # 构建分段矩阵 (n_seg, seg_len) 并计算 FFT
        segments = np.empty((n_seg, seg_len), dtype=float)
        for i in range(n_seg):
            seg = signal[i * seg_len : (i + 1) * seg_len]
            segments[i] = seg - np.mean(seg)

        x = np.fft.rfft(segments, axis=1)
        n_bins = x.shape[1]
        if n_bins < 4:
            return 0.0

        max_f = min(n_freq, n_bins // 2)

        # 构建频率对网格（向量化）
        f1_arr = np.arange(1, max_f)
        f2_arr = np.arange(1, max_f)
        f1_grid, f2_grid = np.meshgrid(f1_arr, f2_arr, indexing="ij")
        mask = (f2_grid >= f1_grid) & ((f1_grid + f2_grid) < n_bins)
        f1_valid = f1_grid[mask]
        f2_valid = f2_grid[mask]

        if len(f1_valid) == 0:
            return 0.0

        x_f1 = x[:, f1_valid]
        x_f2 = x[:, f2_valid]
        x_f12 = x[:, f1_valid + f2_valid]

        # 双谱（分段平均）
        bis = np.mean(x_f1 * x_f2 * np.conj(x_f12), axis=0)

        # 归一化分母
        psd_f1 = np.mean(np.abs(x_f1) ** 2, axis=0)
        psd_f2 = np.mean(np.abs(x_f2) ** 2, axis=0)
        psd_f12 = np.mean(np.abs(x_f12) ** 2, axis=0)
        denom = np.sqrt(psd_f1 * psd_f2 * psd_f12) + 1e-12

        bic = (np.abs(bis) / denom) ** 2
        return float(min(1.0, np.max(bic)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("双相干性计算失败: %s", exc)
        return 0.0


def _choudhury_kernel(
    pv: np.ndarray,
    op: np.ndarray,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """Choudhury NGI/NLI 非线性检测（等价复制自引擎 L2710-2806，OP 增量域统计量）。"""
    if not isinstance(threshold, dict):
        threshold = {}
    ngi_threshold = float(threshold.get("choudhury_ngi_threshold", 1.0))
    nli_threshold = float(threshold.get("choudhury_nli_threshold", 0.01))

    min_len = min(len(pv), len(op))
    if min_len < 32:
        return _empty_choudhury_result()

    try:
        from scipy import stats as sp_stats

        op_arr = op[:min_len].astype(float)
        pv_arr = pv[:min_len].astype(float)

        op_centered = op_arr - np.mean(op_arr)
        op_std = float(np.std(op_centered))
        if op_std < 1e-9:
            return _empty_choudhury_result()

        # OP 增量（一阶差分）：粘滞"不动+跳变"特征在增量域呈重尾分布
        op_diff = np.diff(op_arr)
        op_diff_centered = op_diff - np.mean(op_diff)
        if float(np.std(op_diff_centered)) < 1e-9:
            return _empty_choudhury_result()

        skewness = float(sp_stats.skew(op_diff_centered))
        kurtosis_excess = float(sp_stats.kurtosis(op_diff_centered, fisher=True))

        # NGI: 非高斯指数
        ngi = abs(kurtosis_excess) / 6.0 + (skewness**2) / 24.0
        # NLI: 非线性指数（最大双相干性近似）
        nli = _compute_max_bicoherence(op_diff_centered)

        # PV-OP 椭圆拟合（引擎 L2783 调 _detect_valve_stiction，这里调本文件内核）
        stiction_fit = _ellipse_kernel(pv_arr, op_arr)
        fitting_score = float(stiction_fit.get("fitting_score", 0.0))
        stiction_index = float(stiction_fit.get("stiction_index", 0.0))

        detected = bool(ngi > ngi_threshold and nli > nli_threshold)

        if detected:
            confidence = min(1.0, ngi * 0.5 + nli * 0.3 + fitting_score * 0.2)
        else:
            confidence = 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "ngi": ngi,
            "nli": nli,
            "stiction_index": stiction_index,
            "fitting_score": fitting_score,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Choudhury 非线性检测失败: %s", exc)
        return _empty_choudhury_result()


def _kano_kernel(pv: np.ndarray, op: np.ndarray) -> dict[str, Any]:
    """Kano 统计法阀门粘滞检测（等价复制自引擎 L2809-2913）。"""
    min_len = min(len(pv), len(op))
    if min_len < 16:
        return _empty_kano_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        op_arr = op[:min_len].astype(float)

        pv_std = float(np.std(pv_arr))
        op_std = float(np.std(op_arr))
        std_ratio = pv_std / (op_std + 1e-9)

        if pv_std > 1e-9 and op_std > 1e-9:
            correlation = float(np.corrcoef(pv_arr, op_arr)[0, 1])
        else:
            correlation = 0.0

        # OP 单调分段：检测方向变化点
        op_diff = np.diff(op_arr)
        signs = np.sign(op_diff)
        nz_idx = np.flatnonzero(signs != 0)
        if len(nz_idx) < 2:
            return _empty_kano_result()

        sign_changes = np.flatnonzero(np.diff(signs[nz_idx]) != 0)
        boundaries = np.concatenate([[-1], sign_changes, [len(nz_idx) - 1]])

        total_segments = len(boundaries) - 1
        if total_segments == 0:
            return _empty_kano_result()

        # 统计粘滞区间：OP 变化小但 PV 变化大
        stiction_segments = 0
        op_range = float(np.max(op_arr) - np.min(op_arr)) + 1e-9
        pv_range = float(np.max(pv_arr) - np.min(pv_arr)) + 1e-9

        for i in range(total_segments):
            start_idx = int(nz_idx[boundaries[i] + 1])
            end_idx = int(nz_idx[boundaries[i + 1]]) + 2
            if end_idx <= start_idx:
                continue
            seg_op = op_arr[start_idx:end_idx]
            seg_pv = pv_arr[start_idx:end_idx]
            delta_op = float(np.max(seg_op) - np.min(seg_op)) / op_range
            delta_pv = float(np.max(seg_pv) - np.min(seg_pv)) / pv_range
            # 粘滞区间：OP 变化 < 5% 但 PV 变化 > 20%
            if delta_op < 0.05 and delta_pv > 0.20:
                stiction_segments += 1

        stiction_ratio = stiction_segments / total_segments

        detected = bool(stiction_ratio > 0.6)
        confidence = min(1.0, stiction_ratio) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "stiction_ratio": stiction_ratio,
            "correlation": correlation,
            "std_ratio": std_ratio,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kano 粘滞检测失败: %s", exc)
        return _empty_kano_result()


@operator(
    OperatorMeta(
        name="stiction_ellipse",
        display_name="椭圆拟合粘滞检测",
        family="stiction",
        diag_code="VALVE_STICTION",
        description="PV-OP 散点椭圆拟合（含 θ 补偿与极限环门控，与 KPI 粘滞指数同口径）",
        required_signals=("pv", "op"),
        min_sample_rate=0.0,
        outputs_schema={"stiction_index": "粘滞指数", "fitting_score": "椭圆拟合度 R²"},
        threshold_schema={},
        symptom_tags=("VALVE_STICTION",),
        fast_group=True,
        confidence_basis="命中（PV-OP 相平面椭圆回环）时 min(1.0, (椭圆拟合分 + 粘滞指数) / 2)",
    )
)
def detect_ellipse(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:  # noqa: ARG001
    pv = input.signals.get("pv")
    op = input.signals.get("op")
    if pv is None or op is None or min(len(pv), len(op)) < 16:
        return OperatorResult("stiction_ellipse", executed=False, skip_reason="pv/op 数据不足")
    res = _ellipse_kernel(pv, op, float(input.meta.get("sample_interval", 1.0)))
    return OperatorResult(
        "stiction_ellipse",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={"stiction_index": res["stiction_index"], "fitting_score": res["fitting_score"]},
        evidence=[
            EvidenceItem(
                "stiction_index",
                round(float(res["stiction_index"]), 4),
                0.3,
                "椭圆短长轴比" + ("超阈" if res["detected"] else "未超阈"),
            ),
        ],
    )


@operator(
    OperatorMeta(
        name="stiction_choudhury",
        display_name="Choudhury NGI/NLI 检测",
        family="stiction",
        diag_code="VALVE_STICTION",
        description="OP 增量域非高斯指数（NGI）+ 双相干性非线性指数（NLI）检测粘滞",
        required_signals=("pv", "op"),
        min_sample_rate=0.0,
        outputs_schema={"ngi": "非高斯指数", "nli": "非线性指数"},
        threshold_schema={"choudhury_ngi_threshold": 1.0, "choudhury_nli_threshold": 0.01},
        symptom_tags=("VALVE_STICTION",),
        fast_group=False,
        confidence_basis=(
            "命中（NGI 谐波间隙 + NLI 非线性双门）时 min(1.0, NGI×0.5 + NLI×0.3 + 椭圆拟合分×0.2)"
        ),
    )
)
def detect_choudhury(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    op = input.signals.get("op")
    if pv is None or op is None or min(len(pv), len(op)) < 32:
        return OperatorResult("stiction_choudhury", executed=False, skip_reason="pv/op 数据不足")
    res = _choudhury_kernel(pv, op, threshold)
    return OperatorResult(
        "stiction_choudhury",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={"ngi": res["ngi"], "nli": res["nli"], "fitting_score": res["fitting_score"]},
        evidence=[
            EvidenceItem(
                "ngi",
                round(float(res["ngi"]), 4),
                threshold.get("choudhury_ngi_threshold"),
                "增量域非高斯指数" + ("超阈" if res["detected"] else "未超阈"),
            ),
            EvidenceItem(
                "nli",
                round(float(res["nli"]), 4),
                threshold.get("choudhury_nli_threshold"),
                # NLI 为 NGI 辅证门；补充超阈措辞以便证据表"是否命中"列可解析
                "非线性指数"
                + (
                    "超阈"
                    if float(res["nli"]) >= float(threshold.get("choudhury_nli_threshold", 0))
                    else "未超阈"
                ),
            ),
        ],
    )


@operator(
    OperatorMeta(
        name="stiction_kano",
        display_name="Kano 统计法粘滞检测",
        family="stiction",
        diag_code="VALVE_STICTION",
        description="OP 单调分段统计：OP 几乎不变但 PV 大幅变化的粘滞区间占比",
        required_signals=("pv", "op"),
        min_sample_rate=0.0,
        outputs_schema={"stiction_ratio": "粘滞区间占比", "correlation": "PV-OP 相关系数"},
        threshold_schema={},
        symptom_tags=("VALVE_STICTION",),
        fast_group=False,
        confidence_basis="命中（OP 几乎不动而 PV 大幅变化）时 min(1.0, Kano 统计粘滞比)",
    )
)
def detect_kano(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:  # noqa: ARG001
    pv = input.signals.get("pv")
    op = input.signals.get("op")
    if pv is None or op is None or min(len(pv), len(op)) < 16:
        return OperatorResult("stiction_kano", executed=False, skip_reason="pv/op 数据不足")
    res = _kano_kernel(pv, op)
    return OperatorResult(
        "stiction_kano",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "stiction_ratio": res["stiction_ratio"],
            "correlation": res["correlation"],
            "std_ratio": res["std_ratio"],
        },
        evidence=[
            EvidenceItem(
                "stiction_ratio",
                round(float(res["stiction_ratio"]), 4),
                0.6,
                "粘滞区间占比" + ("超阈" if res["detected"] else "未超阈"),
            ),
        ],
    )
