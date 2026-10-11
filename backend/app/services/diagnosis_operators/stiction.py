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

D3（2026-10-11，08§3.2/§4.1/§5 粘滞三行）：
- 逐段评估：三法消费 D1 段目录（meta.consecutive_segments）逐连续段运行，
  段级结果落库（segment_results）——缺口两侧不拼接，窗口按
  "任一段阳性→阳性；无阳性且有阴性段→范围内阴性；全前提不成立→不适用；
  无有效评估段→数据不足"归并（C14-D01/C14-D04）；
- 证据不足不确诊（C15-D 语义）：椭圆法低相关（R²<0.5，assess_stiction_features
  insufficient_fit）→ INSUFFICIENT_DATA，不得渲染成"未检出"阴性；
- 内核异常 `_error` 透传 → ERROR 态（08§4.1"错误不能被空结果吞掉"）；
- 实际生效参数与段级结果随 features 落库（支持度/参数保存，D1 载体承接）；
- Choudhury 内部椭圆调用统一真实采样间隔与量程（08§5 行内要求）；
- Kano 输出段边界统计与两轴变化、分母（OP 单调段数）显式可核。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from app.services.diagnosis_operators.base import (
    ASSESSMENT_ERROR,
    ASSESSMENT_INSUFFICIENT_DATA,
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_NOT_APPLICABLE,
    ASSESSMENT_POSITIVE,
    REASON_INSUFFICIENT_FIT,
    REASON_NO_LIMIT_CYCLE,
    REASON_TOO_FEW_POINTS,
    EvidenceItem,
    OperatorInput,
    OperatorMeta,
    OperatorResult,
    merge_segment_states,
    operator,
    segment_bounds,
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

#: 椭圆法短长轴比确诊门（内部常数，与 KPI SEVERE 等级对齐；随
#: applied_parameters 落库可查，未开放为配置项——08§3.3 内部常数口径）
_ELLIPSE_STICTION_INDEX_THRESHOLD = 0.3

#: Kano 粘滞区间判据（内部常数：OP 段变化 <5% 量程 且 PV 段变化 >20% 量程）
_KANO_OP_MOVE_MAX = 0.05
_KANO_PV_MOVE_MIN = 0.20
#: Kano 粘滞区间占比确诊门（内部常数）
_KANO_RATIO_THRESHOLD = 0.6


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


def _seg_sample_interval(ts: np.ndarray, start: int, end: int, default: float) -> float:
    """段内实际采样间隔（秒）：按真实时间轴估计，失败回退 default。"""
    if end - start >= 2 and len(ts) >= end:
        seg_ts = np.asarray(ts[start:end], dtype=float)
        span = float(seg_ts[-1] - seg_ts[0])
        if span > 0:
            return span / (len(seg_ts) - 1)
    return default if default > 0 else 1.0


def _ellipse_kernel(
    pv_values: np.ndarray,
    op_values: np.ndarray,
    sample_interval: float = 1.0,
    *,
    pv_range: tuple[float, float] | None = None,
    op_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """PV-OP 散点椭圆拟合检测阀门粘滞（等价复制自引擎 L1970-2022）。

    复用 KPI 侧 assess_stiction_features（GB/T 44693.2-2024 F.2 口径，
    含互相关 θ 补偿 + 极限环门控 + R² 拟合度）。

    D1（DIA-05，08§3.3）：PV/OP 各自**真实量程**显式传入——椭圆短长轴比
    b/a 并非尺度不变量，双轴归一化必须用各自满量程；量程缺失时
    assess_stiction_features 内部回退 DEFAULT 0~100 并随 range_source
    记录（"双轴默认 100"仅为缺省兜底，不再冒充已核实量程）。
    """
    min_len = min(len(pv_values), len(op_values))
    if min_len < 8:
        return _empty_stiction_result()

    try:
        pv_span = (pv_range[1] - pv_range[0]) if pv_range else None
        op_span = (op_range[1] - op_range[0]) if op_range else None
        feat = assess_stiction_features(
            pv_values,
            op_values,
            sample_interval=sample_interval,
            pv_range=pv_span if pv_span and pv_span > 0 else None,
            op_range=op_span if op_span and op_span > 0 else None,
        )
        fitting_score = float(feat.get("fitting_score", 0.0))
        stiction_index = float(feat.get("stiction_index", 0.0))
        is_limit_cycle = bool(feat.get("is_limit_cycle", False))

        # 判定：极限环 + R²≥0.5 + 短长轴比>0.3（对齐 KPI SEVERE 等级）
        detected = (
            is_limit_cycle
            and fitting_score >= MIN_FITTING_SCORE
            and stiction_index > _ELLIPSE_STICTION_INDEX_THRESHOLD
        )
        confidence = min(1.0, (fitting_score + stiction_index) / 2) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "stiction_index": stiction_index,
            "fitting_score": fitting_score,
            # S3 修复（G26）：传播底层前提状态，供调用方区分"数据不足"与"未检出"。
            # assess_stiction_features 要求 >=100 点（MIN_POINTS）；本内核门槛为 8，
            # 而编排层数据门禁门槛为 32，故 32~99 点窗口会走到这里并得到
            # reason="insufficient_data"。原实现丢弃该字段，算子一律上报
            # detected=False，把"数据不足"渲染成"未检出"。
            # D3：insufficient_fit（低相关 R²<0.5）同样透传——证据不足不确诊，
            # 也不得作为"未检出"有效阴性（08§4.1 拟合信息不足 → INSUFFICIENT_DATA）。
            "reason": feat.get("reason"),
            # D1（C16-D01）：量程来源随特征落库（explicit=回路真实量程 /
            # normalized_default=缺省 0~100 兜底），换单位/量程结论等价可核
            "range_source": feat.get("range_source"),
            # 极限环门控细节（no_limit_cycle 时构造 NOT_APPLICABLE 证据用）
            "zero_crossings": feat.get("zero_crossings"),
            "mean_half_period": feat.get("mean_half_period"),
            "s_a": feat.get("s_a"),
            "s_b": feat.get("s_b"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("阀门粘滞检测失败: %s", exc)
        # D3：异常透传（错误不能被空结果吞掉——原先空结果会被渲染成 NEGATIVE）
        failed = _empty_stiction_result()
        failed["_error"] = f"{type(exc).__name__}: {exc}"
        return failed


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
    sample_interval: float = 1.0,
    pv_range: tuple[float, float] | None = None,
    op_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Choudhury NGI/NLI 非线性检测（等价复制自引擎 L2710-2806，OP 增量域统计量）。

    D3（08§5 行内要求）：内部椭圆调用统一真实采样间隔与量程——原硬编码
    1 秒采样/无量程会在非 1s 采样回路上得到错误的 θ 补偿与归一化。
    """
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

        # PV-OP 椭圆拟合（D3：透传真实采样间隔与量程，双相干元信息随结果落库）
        stiction_fit = _ellipse_kernel(
            pv_arr,
            op_arr,
            sample_interval if sample_interval > 0 else 1.0,
            pv_range=pv_range,
            op_range=op_range,
        )
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
            # S3 修复（G26）：同 _ellipse_kernel，传播底层前提状态。
            # 注：insufficient_fit 属内部椭圆辅证低相关——NGI/NLI 主判据不受
            # 影响，不因此把 Choudhury 整段判 INSUFFICIENT（仅记低拟合事实）。
            "reason": (
                stiction_fit.get("reason")
                if stiction_fit.get("reason") != "insufficient_fit"
                else None
            ),
            "bicoherence_segments": 64,
            "bicoherence_freq_pairs": 105,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Choudhury 非线性检测失败: %s", exc)
        # D3：异常透传（错误不能被空结果吞掉）
        failed = _empty_choudhury_result()
        failed["_error"] = f"{type(exc).__name__}: {exc}"
        return failed


def _kano_kernel(pv: np.ndarray, op: np.ndarray) -> dict[str, Any]:
    """Kano 统计法阀门粘滞检测（等价复制自引擎 L2809-2913）。

    D3（08§5 Kano 行）：输出段边界统计与两轴变化、分母（OP 单调段数）
    ——手算段数与占比可核，外扰/量程退化可由 op_span/pv_span 追因。
    """
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
            if delta_op < _KANO_OP_MOVE_MAX and delta_pv > _KANO_PV_MOVE_MIN:
                stiction_segments += 1

        stiction_ratio = stiction_segments / total_segments

        detected = bool(stiction_ratio > _KANO_RATIO_THRESHOLD)
        confidence = min(1.0, stiction_ratio) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "stiction_ratio": stiction_ratio,
            "correlation": correlation,
            "std_ratio": std_ratio,
            # D3：分母/分子/两轴变化显式可核（08§5 Kano 行）
            "segments_total": total_segments,
            "stiction_segments": stiction_segments,
            "op_span": float(np.max(op_arr) - np.min(op_arr)),
            "pv_span": float(np.max(pv_arr) - np.min(pv_arr)),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kano 粘滞检测失败: %s", exc)
        # D3：异常透传（错误不能被空结果吞掉）
        failed = _empty_kano_result()
        failed["_error"] = f"{type(exc).__name__}: {exc}"
        return failed


def _ellipse_seg_state(res: dict[str, Any]) -> tuple[str, str | None]:
    """椭圆法段态映射（D3：证据不足不确诊——insufficient_fit 不冒充阴性）。"""
    reason = res.get("reason")
    if reason == "insufficient_data":
        return ASSESSMENT_INSUFFICIENT_DATA, REASON_TOO_FEW_POINTS
    if reason == "insufficient_fit":
        return ASSESSMENT_INSUFFICIENT_DATA, REASON_INSUFFICIENT_FIT
    if reason == "no_limit_cycle":
        return ASSESSMENT_NOT_APPLICABLE, REASON_NO_LIMIT_CYCLE
    return (ASSESSMENT_POSITIVE if res.get("detected") else ASSESSMENT_NEGATIVE), None


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


def _pick_representative(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """窗口代表段：首个 POSITIVE → 首个 NEGATIVE → 首个 NOT_APPLICABLE → 最早段。"""
    for state in (ASSESSMENT_POSITIVE, ASSESSMENT_NEGATIVE, ASSESSMENT_NOT_APPLICABLE):
        for r in records:
            if r.get("state") == state:
                return r
    return records[0]


def _all_reason(records: list[dict[str, Any]], reason: str) -> bool:
    return all(r.get("reasonCode") == reason for r in records if r.get("reasonCode"))


@operator(
    OperatorMeta(
        name="stiction_ellipse",
        display_name="椭圆拟合粘滞检测",
        family="stiction",
        diag_code="VALVE_STICTION",
        description=(
            "PV-OP 散点椭圆拟合（含 θ 补偿与极限环门控，与 KPI 粘滞指数同口径）；"
            "逐连续段评估，段级结果独立落库"
        ),
        required_signals=("pv", "op"),
        min_sample_rate=0.0,
        outputs_schema={"stiction_index": "粘滞指数", "fitting_score": "椭圆拟合度 R²"},
        threshold_schema={},
        symptom_tags=("VALVE_STICTION",),
        fast_group=True,
        confidence_basis=(
            "命中（PV-OP 相平面椭圆回环）时 min(1.0, (椭圆拟合分 + 粘滞指数) / 2)；"
            "低相关（R²<0.5）与 <100 点段为证据不足不确诊，不冒充阴性"
        ),
    )
)
def detect_ellipse(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:  # noqa: ARG001
    pv = input.signals.get("pv")
    op = input.signals.get("op")
    if pv is None or op is None or min(len(pv), len(op)) < 16:
        return OperatorResult(
            "stiction_ellipse",
            executed=False,
            skip_reason="pv/op 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    # D3 逐段评估（08§3.2）：段目录内逐段运行，缺口两侧不拼接；段级结果
    # 保留（不同段的指数/拟合度不平均），窗口按段态归并
    segments = segment_bounds(input.meta, min(len(pv), len(op)))
    ts_arr = np.asarray(input.timestamps, dtype=float)
    pv_range = input.meta.get("pv_range")
    op_range = input.meta.get("op_range")

    records: list[dict[str, Any]] = []
    for start, end in segments:
        n_seg = end - start
        if n_seg < 16:
            if n_seg >= 2:
                records.append(
                    {
                        "start": start,
                        "end": end,
                        "points": n_seg,
                        "state": ASSESSMENT_INSUFFICIENT_DATA,
                        "reasonCode": REASON_TOO_FEW_POINTS,
                    }
                )
            continue
        interval = _seg_sample_interval(
            ts_arr, start, end, float(input.meta.get("sample_interval", 1.0))
        )
        res = _ellipse_kernel(
            pv[start:end],
            op[start:end],
            interval,
            pv_range=pv_range,
            op_range=op_range,
        )
        if res.get("_error"):
            # D3：内核异常 → ERROR（错误不能被空结果吞掉，不渲染成 NEGATIVE）
            return OperatorResult(
                "stiction_ellipse",
                executed=False,
                error=str(res["_error"]),
                assessment_state=ASSESSMENT_ERROR,
                reason_code=str(res["_error"])[:200],
            )
        state, code = _ellipse_seg_state(res)
        records.append(
            {
                "start": start,
                "end": end,
                "points": n_seg,
                "state": state,
                **({"reasonCode": code} if code else {}),
                "detected": bool(res["detected"]),
                "stiction_index": round(float(res["stiction_index"]), 4),
                "fitting_score": round(float(res["fitting_score"]), 4),
                "theta_hat_seconds": round(float(res.get("theta_hat_seconds") or 0.0), 2),
                "theta_compensated": bool(res.get("theta_compensated")),
                "range_source": res.get("range_source"),
                **(
                    {"zero_crossings": res.get("zero_crossings")}
                    if state == ASSESSMENT_NOT_APPLICABLE
                    else {}
                ),
            }
        )

    if not records:
        return OperatorResult(
            "stiction_ellipse",
            executed=False,
            skip_reason="pv/op 数据不足（连续段切分后无有效段）",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    merged = merge_segment_states([r.get("state") or "" for r in records])
    rep = _pick_representative(records)
    if merged == ASSESSMENT_INSUFFICIENT_DATA and _all_reason(records, REASON_TOO_FEW_POINTS):
        # 与原单窗行为同处置：全部段 <100 点 → 输入不足（executed=False）
        return OperatorResult(
            "stiction_ellipse",
            executed=False,
            skip_reason="pv/op 数据不足（椭圆拟合要求 >=100 点）",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )

    evidence: list[EvidenceItem]
    if rep.get("state") == ASSESSMENT_NOT_APPLICABLE:
        evidence = [_limit_cycle_gate_evidence(rep)]
    else:
        evidence = [
            EvidenceItem(
                "stiction_index",
                rep["stiction_index"],
                _ELLIPSE_STICTION_INDEX_THRESHOLD,
                "椭圆短长轴比"
                + ("超阈" if merged == ASSESSMENT_POSITIVE else "未超阈")
                + (f"（共 {len(records)} 段逐段评估）" if len(records) > 1 else ""),
            ),
        ]
    return OperatorResult(
        "stiction_ellipse",
        executed=True,
        detected=merged == ASSESSMENT_POSITIVE,
        confidence=float(min(1.0, (rep["fitting_score"] + rep["stiction_index"]) / 2))
        if merged == ASSESSMENT_POSITIVE
        else 0.0,
        features={
            "stiction_index": rep["stiction_index"],
            "fitting_score": rep["fitting_score"],
            "range_source": rep.get("range_source"),
            "segment_results": records,
            "applied_parameters": {
                "min_fitting_score": MIN_FITTING_SCORE,
                "stiction_index_threshold": _ELLIPSE_STICTION_INDEX_THRESHOLD,
            },
        },
        evidence=evidence,
        assessment_state=merged,
        reason_code=(
            rep.get("reasonCode")
            if merged in (ASSESSMENT_NOT_APPLICABLE, ASSESSMENT_INSUFFICIENT_DATA)
            else None
        ),
    )


@operator(
    OperatorMeta(
        name="stiction_choudhury",
        display_name="Choudhury NGI/NLI 检测",
        family="stiction",
        diag_code="VALVE_STICTION",
        description=(
            "OP 增量域非高斯指数（NGI）+ 双相干性非线性指数（NLI）检测粘滞"
            "（需极限环前提）；逐连续段评估，双相干分段/频带/有效样本随结果落库"
        ),
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
        return OperatorResult(
            "stiction_choudhury",
            executed=False,
            skip_reason="pv/op 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    segments = segment_bounds(input.meta, min(len(pv), len(op)))
    ts_arr = np.asarray(input.timestamps, dtype=float)
    pv_range = input.meta.get("pv_range")
    op_range = input.meta.get("op_range")

    records: list[dict[str, Any]] = []
    for start, end in segments:
        n_seg = end - start
        if n_seg < 32:
            if n_seg >= 2:
                records.append(
                    {
                        "start": start,
                        "end": end,
                        "points": n_seg,
                        "state": ASSESSMENT_INSUFFICIENT_DATA,
                        "reasonCode": REASON_TOO_FEW_POINTS,
                    }
                )
            continue
        interval = _seg_sample_interval(
            ts_arr, start, end, float(input.meta.get("sample_interval", 1.0))
        )
        res = _choudhury_kernel(
            pv[start:end],
            op[start:end],
            threshold,
            sample_interval=interval,
            pv_range=pv_range,
            op_range=op_range,
        )
        if res.get("_error"):
            return OperatorResult(
                "stiction_choudhury",
                executed=False,
                error=str(res["_error"]),
                assessment_state=ASSESSMENT_ERROR,
                reason_code=str(res["_error"])[:200],
            )
        if res.get("reason") == "insufficient_data":
            state, code = ASSESSMENT_INSUFFICIENT_DATA, REASON_TOO_FEW_POINTS
        elif res.get("reason") == "no_limit_cycle":
            state, code = ASSESSMENT_NOT_APPLICABLE, REASON_NO_LIMIT_CYCLE
        else:
            state, code = (ASSESSMENT_POSITIVE if res["detected"] else ASSESSMENT_NEGATIVE), None
        records.append(
            {
                "start": start,
                "end": end,
                "points": n_seg,
                "state": state,
                **({"reasonCode": code} if code else {}),
                "ngi": round(float(res["ngi"]), 4),
                "nli": round(float(res["nli"]), 4),
                "fitting_score": round(float(res["fitting_score"]), 4),
                "bicoherence_segments": res.get("bicoherence_segments"),
                "bicoherence_freq_pairs": res.get("bicoherence_freq_pairs"),
                **(
                    {"zero_crossings": res.get("zero_crossings")}
                    if state == ASSESSMENT_NOT_APPLICABLE
                    else {}
                ),
            }
        )

    if not records:
        return OperatorResult(
            "stiction_choudhury",
            executed=False,
            skip_reason="pv/op 数据不足（连续段切分后无有效段）",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    merged = merge_segment_states([r.get("state") or "" for r in records])
    rep = _pick_representative(records)
    if merged == ASSESSMENT_INSUFFICIENT_DATA and _all_reason(records, REASON_TOO_FEW_POINTS):
        return OperatorResult(
            "stiction_choudhury",
            executed=False,
            skip_reason="pv/op 数据不足（椭圆拟合要求 >=100 点）",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )

    evidence: list[EvidenceItem]
    if rep.get("state") == ASSESSMENT_NOT_APPLICABLE:
        evidence = [_limit_cycle_gate_evidence(rep)]
    else:
        evidence = [
            EvidenceItem(
                "ngi",
                rep["ngi"],
                threshold.get("choudhury_ngi_threshold"),
                "增量域非高斯指数" + ("超阈" if merged == ASSESSMENT_POSITIVE else "未超阈"),
            ),
            EvidenceItem(
                "nli",
                rep["nli"],
                threshold.get("choudhury_nli_threshold"),
                # NLI 为 NGI 辅证门；补充超阈措辞以便证据表"是否命中"列可解析
                "非线性指数"
                + (
                    "超阈"
                    if float(rep["nli"]) >= float(threshold.get("choudhury_nli_threshold", 0))
                    else "未超阈"
                ),
            ),
        ]
    return OperatorResult(
        "stiction_choudhury",
        executed=True,
        detected=merged == ASSESSMENT_POSITIVE,
        confidence=float(
            min(
                1.0,
                rep["ngi"] * 0.5 + rep["nli"] * 0.3 + rep["fitting_score"] * 0.2,
            )
        )
        if merged == ASSESSMENT_POSITIVE
        else 0.0,
        features={
            "ngi": rep["ngi"],
            "nli": rep["nli"],
            "fitting_score": rep["fitting_score"],
            "bicoherence_segments": rep.get("bicoherence_segments"),
            "bicoherence_freq_pairs": rep.get("bicoherence_freq_pairs"),
            "segment_results": records,
            "applied_parameters": {
                "choudhury_ngi_threshold": float(threshold.get("choudhury_ngi_threshold", 1.0)),
                "choudhury_nli_threshold": float(threshold.get("choudhury_nli_threshold", 0.01)),
            },
        },
        evidence=evidence,
        assessment_state=merged,
        reason_code=(
            rep.get("reasonCode")
            if merged in (ASSESSMENT_NOT_APPLICABLE, ASSESSMENT_INSUFFICIENT_DATA)
            else None
        ),
    )


@operator(
    OperatorMeta(
        name="stiction_kano",
        display_name="Kano 统计法粘滞检测",
        family="stiction",
        diag_code="VALVE_STICTION",
        description=(
            "OP 单调分段统计：OP 几乎不变但 PV 大幅变化的粘滞区间占比（需极限环"
            "振荡前提）；逐连续段评估，段边界/两轴变化/分母显式可核"
        ),
        required_signals=("pv", "op"),
        min_sample_rate=0.0,
        outputs_schema={
            "stiction_ratio": "粘滞区间占比",
            "correlation": "PV-OP 相关系数",
            "segments_total": "OP 单调分段数（分母）",
            "stiction_segments": "粘滞区间数（分子）",
        },
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
        return OperatorResult(
            "stiction_kano",
            executed=False,
            skip_reason="pv/op 数据不足",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    segments = segment_bounds(input.meta, min(len(pv), len(op)))

    records: list[dict[str, Any]] = []
    for start, end in segments:
        n_seg = end - start
        if n_seg < 16:
            if n_seg >= 2:
                records.append(
                    {
                        "start": start,
                        "end": end,
                        "points": n_seg,
                        "state": ASSESSMENT_INSUFFICIENT_DATA,
                        "reasonCode": REASON_TOO_FEW_POINTS,
                    }
                )
            continue
        res = _kano_kernel(pv[start:end], op[start:end])
        if res.get("_error"):
            return OperatorResult(
                "stiction_kano",
                executed=False,
                error=str(res["_error"]),
                assessment_state=ASSESSMENT_ERROR,
                reason_code=str(res["_error"])[:200],
            )
        if res.get("reason") == "no_limit_cycle":
            state, code = ASSESSMENT_NOT_APPLICABLE, REASON_NO_LIMIT_CYCLE
        else:
            state, code = (ASSESSMENT_POSITIVE if res["detected"] else ASSESSMENT_NEGATIVE), None
        records.append(
            {
                "start": start,
                "end": end,
                "points": n_seg,
                "state": state,
                **({"reasonCode": code} if code else {}),
                "stiction_ratio": round(float(res["stiction_ratio"]), 4),
                "correlation": round(float(res["correlation"]), 4),
                "std_ratio": round(float(res["std_ratio"]), 4),
                "segments_total": res.get("segments_total"),
                "stiction_segments": res.get("stiction_segments"),
                "op_span": round(float(res.get("op_span", 0.0)), 4),
                "pv_span": round(float(res.get("pv_span", 0.0)), 4),
                **(
                    {"zero_crossings": res.get("zero_crossings")}
                    if state == ASSESSMENT_NOT_APPLICABLE
                    else {}
                ),
            }
        )

    if not records:
        return OperatorResult(
            "stiction_kano",
            executed=False,
            skip_reason="pv/op 数据不足（连续段切分后无有效段）",
            assessment_state=ASSESSMENT_INSUFFICIENT_DATA,
            reason_code=REASON_TOO_FEW_POINTS,
        )
    merged = merge_segment_states([r.get("state") or "" for r in records])
    rep = _pick_representative(records)

    evidence: list[EvidenceItem]
    if rep.get("state") == ASSESSMENT_NOT_APPLICABLE:
        evidence = [_limit_cycle_gate_evidence(rep)]
    else:
        evidence = [
            EvidenceItem(
                "stiction_ratio",
                rep["stiction_ratio"],
                _KANO_RATIO_THRESHOLD,
                "粘滞区间占比"
                + ("超阈" if merged == ASSESSMENT_POSITIVE else "未超阈")
                + (
                    f"（{rep.get('stiction_segments')}/{rep.get('segments_total')} 段"
                    + (f"，共 {len(records)} 连续段逐段评估）" if len(records) > 1 else "")
                    + "）"
                ),
            ),
        ]
    return OperatorResult(
        "stiction_kano",
        executed=True,
        detected=merged == ASSESSMENT_POSITIVE,
        confidence=float(rep["stiction_ratio"]) if merged == ASSESSMENT_POSITIVE else 0.0,
        features={
            "stiction_ratio": rep["stiction_ratio"],
            "correlation": rep["correlation"],
            "std_ratio": rep["std_ratio"],
            "segments_total": rep.get("segments_total"),
            "stiction_segments": rep.get("stiction_segments"),
            "op_span": rep.get("op_span"),
            "pv_span": rep.get("pv_span"),
            "segment_results": records,
            "applied_parameters": {
                "kano_op_move_max": _KANO_OP_MOVE_MAX,
                "kano_pv_move_min": _KANO_PV_MOVE_MIN,
                "kano_ratio_threshold": _KANO_RATIO_THRESHOLD,
            },
        },
        evidence=evidence,
        assessment_state=merged,
        reason_code=(
            rep.get("reasonCode")
            if merged in (ASSESSMENT_NOT_APPLICABLE, ASSESSMENT_INSUFFICIENT_DATA)
            else None
        ),
    )
