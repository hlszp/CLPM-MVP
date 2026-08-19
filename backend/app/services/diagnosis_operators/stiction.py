"""粘滞族元算子：椭圆拟合 / Choudhury NGI-NLI / Kano 统计法。

内核源自 app/tasks/diagnosis_engine.py（复制后独立演进）：
- _ellipse_kernel   ← _detect_valve_stiction（复用 KPI 侧内核）
- _choudhury_kernel ← _detect_choudhury_nonlinearity + _compute_max_bicoherence
- _kano_kernel      ← _detect_kano_stiction

2026-08-19 P1/P3 修复（与旧引擎有意分叉；旧引擎未挂载仅测试引用）：
- P1：_compute_max_bicoherence 分段数 4→64 + 白噪声底校正 ln(M/α)/K，
      修复纯白噪声 NLI 饱和 1.0 导致的 NLI 门失效；
- P3：_choudhury_kernel/_kano_kernel 增加极限环前提门控（与椭圆法
      assess_stiction_features 同判据），修复非振荡段"工况跳变+静止
      微扰"拼接的 OP 增量重尾伪命中（41FIC40504 复核实例）。
"""

from __future__ import annotations

import logging
import math
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
    MIN_HALF_PERIOD_SAMPLES,
    MIN_IAE_SIMILARITY,
    MIN_ZERO_CROSSINGS,
    StictionIndexCalculator,
    assess_stiction_features,
)

logger = logging.getLogger(__name__)

#: P1 噪声底校正族系显著性水平（白噪声 max 校正上界 (c/K)·ln(M/α) 中的 α，
#: 取 0.1% 保守值；实测 200 次白噪声试验逐对校正后 NLI>0.01 的比率 ≈ α）
_NLI_FALSE_ALARM_ALPHA = 0.001


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


def _compute_max_bicoherence(signal: np.ndarray, n_seg: int = 64, n_freq: int = 16) -> float:
    """计算信号的最大双相干性（NLI 近似，分段平均 + 逐对噪声底校正）。

    P1 修复（2026-08-19）：原 4 段无重叠估计的噪声底 ≈ 1/4——单段
    bic² ≡ 1（|x1·x2·x3*|² 与分母恒等），4 段平均压不下去，纯白噪声
    的 max bic² 也饱和到 1.0，NLI 门（>0.01）完全失效。

    修复口径（Hinich χ²₂ 渐近 + 逐对异方差校正）：
    1. 非重叠分段数提至默认 64（段长不足 8 点时降级，段数不足 4 时放弃）；
    2. K 段独立估计下单对白噪声 E[bic²] = c/K：对角对 (f1=f2) 因
       t = x(f)²·x*(2f) 的四阶矩 E|x|⁴=2S² → c=2（200 次白噪声试验
       实测：对角 0.0316 vs 2/64、非对角 0.0156 vs 1/64，精确吻合）；
    3. M 个频点对取 max 的族系显著性上界 ≈ (c/K)·ln(M/α)，
       NLI = max(0, max(bic² − bias))。

    注：重尾（非高斯）信号的 6 阶矩会推高真实噪声底，校正后仍可能
    偏高——由 P3 极限环前提门控兜底（非振荡段一律不予检出）。
    """
    n = len(signal)
    seg_count = max(1, min(n_seg, n // 8))
    seg_len = n // seg_count
    if seg_len < 8 or seg_count < 4:
        return 0.0

    try:
        # 非重叠分段（段间独立，噪声底 1/K 解析可算）
        segments = np.empty((seg_count, seg_len), dtype=float)
        for i in range(seg_count):
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

        # 逐对噪声底校正：白噪声单对 E[bic²] = c/K（对角对 c=2，见 docstring）；
        # M 对取 max 的族系显著性上界 ≈ (c/K)·ln(M/α)
        m_pairs = int(len(f1_valid))
        diag_scale = np.where(f1_valid == f2_valid, 2.0, 1.0)
        bias = (diag_scale / seg_count) * math.log(m_pairs / _NLI_FALSE_ALARM_ALPHA)
        return float(min(1.0, max(0.0, float(np.max(bic - bias)))))
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

        # P3 前提门控（2026-08-19）：Choudhury/Kano/椭圆三法均以回路处于
        # 极限环振荡为物理前提；非振荡段的 OP 增量重尾（工况跳变+静止
        # 微扰拼接）会伪命中 NGI 门（41FIC40504 复核实例）。门控判据与
        # 椭圆法 assess_stiction_features 同源（G1 增强），三算子口径统一。
        is_limit_cycle, gate_info = StictionIndexCalculator._detect_limit_cycle(pv_arr)
        if not is_limit_cycle:
            result = _empty_choudhury_result()
            result.update({"reason": "no_limit_cycle", **gate_info})
            return result

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

        # P3 前提门控（2026-08-19）：同 Choudhury——粘滞三法以极限环振荡
        # 为物理前提，非振荡段"OP 不动 PV 大动"更可能是外扰/传感器问题。
        is_limit_cycle, gate_info = StictionIndexCalculator._detect_limit_cycle(pv_arr)
        if not is_limit_cycle:
            result = _empty_kano_result()
            result.update({"reason": "no_limit_cycle", **gate_info})
            return result

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


def _limit_cycle_gate_evidence(gate_info: dict[str, Any]) -> EvidenceItem:
    """构造极限环前提门控证据项（P3）：明确拦截原因，避免误读为指标未超阈。"""
    zc = int(gate_info.get("zero_crossings", 0) or 0)
    half = float(gate_info.get("mean_half_period", 0.0) or 0.0)
    if zc < MIN_ZERO_CROSSINGS:
        detail = f"零交叉 {zc} < {MIN_ZERO_CROSSINGS}"
    elif half < MIN_HALF_PERIOD_SAMPLES:
        detail = f"平均半周期 {half:.1f} < {MIN_HALF_PERIOD_SAMPLES} 采样"
    else:
        s_a = float(gate_info.get("s_a", 0.0) or 0.0)
        s_b = float(gate_info.get("s_b", 0.0) or 0.0)
        detail = f"IAE 相似率 s_a={s_a:.2f}/s_b={s_b:.2f} < {MIN_IAE_SIMILARITY}"
    return EvidenceItem(
        "limit_cycle_gate",
        "no_limit_cycle",
        "limit_cycle",
        f"非极限环振荡段（{detail}）：粘滞检测物理前提不成立，指标不参与判定",
    )


@operator(
    OperatorMeta(
        name="stiction_choudhury",
        display_name="Choudhury NGI/NLI 检测",
        family="stiction",
        diag_code="VALVE_STICTION",
        description="OP 增量域非高斯指数（NGI）+ 双相干性非线性指数（NLI）检测粘滞（需极限环前提）",
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
    if res.get("reason") == "no_limit_cycle":
        # P3 门控命中：证据表报"前提不成立"，避免误导性的"指标未超阈"
        return OperatorResult(
            "stiction_choudhury",
            executed=True,
            detected=False,
            confidence=0.0,
            features={
                "ngi": res["ngi"],
                "nli": res["nli"],
                "fitting_score": res["fitting_score"],
                "zero_crossings": res.get("zero_crossings"),
                "mean_half_period": res.get("mean_half_period"),
                "s_a": res.get("s_a"),
                "s_b": res.get("s_b"),
            },
            evidence=[_limit_cycle_gate_evidence(res)],
        )
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
        description="OP 单调分段统计：OP 几乎不变但 PV 大幅变化的粘滞区间占比（需极限环振荡前提）",
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
    if res.get("reason") == "no_limit_cycle":
        # P3 门控命中：证据表报"前提不成立"，避免误导性的"指标未超阈"
        return OperatorResult(
            "stiction_kano",
            executed=True,
            detected=False,
            confidence=0.0,
            features={
                "stiction_ratio": res["stiction_ratio"],
                "correlation": res["correlation"],
                "std_ratio": res["std_ratio"],
                "zero_crossings": res.get("zero_crossings"),
                "mean_half_period": res.get("mean_half_period"),
                "s_a": res.get("s_a"),
                "s_b": res.get("s_b"),
            },
            evidence=[_limit_cycle_gate_evidence(res)],
        )
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
