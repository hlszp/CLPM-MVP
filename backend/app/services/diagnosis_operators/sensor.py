"""仪表族元算子：传感器故障三子检测 + PV 质量码 Q001-Q005 规则。

内核等价复制自 app/tasks/diagnosis_engine.py：
- _sensor_fault_kernel ← _detect_sensor_faults（L2202-2373）+ _rolling_std（L2179-2199）
- _quality_kernel      ← _analyze_quality（L2025-2162）

差异说明（保持无状态）：引擎版 sensor_faults 读取 get_trigger_config().min_data_points
（默认 32）做点数门槛；元算子改为常量 MIN_DATA_POINTS=32（编排层数据门禁已先行校验，
口径一致）。
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

#: 传感器故障检测最小点数（与引擎 get_trigger_config().min_data_points 默认值一致）
MIN_DATA_POINTS = 32

#: 质量码数值编码 → 标准标签（编排层按 map_quality_code 产出 0/1/2）
_QUALITY_LABELS = {0: "GOOD", 1: "UNCERTAIN", 2: "BAD"}


def _empty_sensor_fault_result() -> dict[str, Any]:
    """空传感器故障检测结果（复制自引擎 L2165-2176）。"""
    return {
        "detected": False,
        "sensor_subtype": None,
        "confidence": 0.0,
        "frozen_max_segment": 0,
        "frozen_segment_ratio": 0.0,
        "noise_std_ratio": 1.0,
        "drift_magnitude": 0.0,
        "reasoning": "",
    }


def _empty_quality_result() -> dict[str, Any]:
    """空质量码分析结果（复制自引擎 L1830-1839）。"""
    return {
        "abnormal": False,
        "confidence": 0.0,
        "bad_rate": 0.0,
        "total": 0,
        "bad_count": 0,
        "quality_pattern": "NORMAL",
    }


def _rolling_std(x: np.ndarray, window: int) -> np.ndarray:
    """滚动标准差（等价复制自引擎 L2179-2199，O(n) 累积和实现）。"""
    n = len(x)
    if n < window or window <= 0:
        return np.array([], dtype=float)
    c = np.cumsum(np.insert(x, 0, 0.0))
    c2 = np.cumsum(np.insert(x * x, 0, 0.0))
    sums = c[window:] - c[:-window]
    sums2 = c2[window:] - c2[:-window]
    mean = sums / window
    var = sums2 / window - mean * mean
    return np.sqrt(np.maximum(var, 0.0))


def _sensor_fault_kernel(
    pv_values: np.ndarray,
    sp_values: np.ndarray | None = None,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """传感器故障检测（等价复制自引擎 L2202-2373：卡死/噪声突增/漂移三子检测）。"""
    if threshold is None:
        threshold = {}
    frozen_window = int(threshold.get("frozen_window", 300))
    frozen_eps = float(threshold.get("frozen_eps", 1e-4))
    frozen_ratio = float(threshold.get("frozen_ratio", 0.2))
    noise_ratio = float(threshold.get("noise_ratio", 3.0))
    noise_segment = float(threshold.get("noise_segment", 0.5))
    drift_k = float(threshold.get("drift_k", 2.0))
    drift_segments = int(threshold.get("drift_segments", 5))

    result = _empty_sensor_fault_result()

    n = len(pv_values)
    if n < MIN_DATA_POINTS:
        return result

    try:
        hits: list[tuple[str, float, str]] = []  # (subtype, confidence, reasoning)

        # --- 1. 卡死/冻结：滚动 std < eps 的最长持续段 ---
        frozen_max_segment = 0
        frozen_segment_ratio = 0.0
        if n >= frozen_window:
            rstd = _rolling_std(pv_values, frozen_window)
            below = rstd < frozen_eps
            max_run = 0
            run = 0
            for b in below:
                if b:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 0
            if max_run > 0:
                frozen_max_segment = max_run + frozen_window - 1
                frozen_segment_ratio = frozen_max_segment / n
            result["frozen_max_segment"] = frozen_max_segment
            result["frozen_segment_ratio"] = frozen_segment_ratio
            if frozen_segment_ratio > frozen_ratio:
                hits.append(
                    (
                        "frozen",
                        0.85,
                        (
                            f"传感器卡死：最长冻结段 {frozen_max_segment} 点"
                            f"（占比 {frozen_segment_ratio:.2f} > {frozen_ratio:.2f}），"
                            f"滚动 std < {frozen_eps}"
                        ),
                    )
                )

        # --- 2. 噪声突增：前段 vs 后段滚动 std 中位数比值 ---
        noise_std_ratio = 1.0
        split = int(n * noise_segment)
        if split >= 16 and n - split >= 16:
            win = max(10, min(30, split // 4, (n - split) // 4))
            std_first = _rolling_std(pv_values[:split], win)
            std_second = _rolling_std(pv_values[split:], win)
            if len(std_first) > 0 and len(std_second) > 0:
                med_first = float(np.median(std_first))
                med_second = float(np.median(std_second))
                denom = min(med_first, med_second)
                numer = max(med_first, med_second)
                if denom > 1e-12:
                    noise_std_ratio = numer / denom
                else:
                    noise_std_ratio = np.inf if numer > 1e-12 else 1.0
                result["noise_std_ratio"] = (
                    float(noise_std_ratio) if np.isfinite(noise_std_ratio) else 999.0
                )
                if noise_std_ratio > noise_ratio:
                    hits.append(
                        (
                            "noisy",
                            0.7,
                            (
                                f"传感器噪声突增：前后段滚动 std 中位数比值 "
                                f"{result['noise_std_ratio']:.2f} > {noise_ratio:.2f}"
                                f"（前段 {med_first:.4g} / 后段 {med_second:.4g}）"
                            ),
                        )
                    )

        # --- 3. 漂移：等长分段均值单调递进 + 幅度超阈值 ---
        drift_magnitude = 0.0
        seg_len = n // drift_segments
        if seg_len >= 4:
            means = np.array(
                [
                    float(np.mean(pv_values[i * seg_len : (i + 1) * seg_len]))
                    for i in range(drift_segments)
                ]
            )
            diffs = np.diff(means)
            drift_magnitude = float(means[-1] - means[0])
            result["drift_magnitude"] = drift_magnitude
            monotonic = bool(np.all(diffs > 0)) or bool(np.all(diffs < 0))
            global_std = float(np.std(pv_values))
            if monotonic and abs(drift_magnitude) > drift_k * global_std:
                # SP 同向同步变化 → 工艺真实变化而非传感器漂移
                sp_synced = False
                if sp_values is not None and len(sp_values) == n:
                    sp_means = np.array(
                        [
                            float(np.mean(sp_values[i * seg_len : (i + 1) * seg_len]))
                            for i in range(drift_segments)
                        ]
                    )
                    sp_magnitude = float(sp_means[-1] - sp_means[0])
                    if sp_magnitude * drift_magnitude > 0 and abs(sp_magnitude) >= 0.5 * abs(
                        drift_magnitude
                    ):
                        sp_synced = True
                if not sp_synced:
                    hits.append(
                        (
                            "drift",
                            0.65,
                            (
                                f"传感器漂移：分段均值单调递进，首尾均值差 "
                                f"{drift_magnitude:.4g}（{abs(drift_magnitude) / global_std:.2f}σ"
                                f" > {drift_k:.1f}σ）且 SP 未同向变化"
                            ),
                        )
                    )

        if hits:
            # 多子类型同时命中时按严重度取最高（frozen > noisy > drift）
            subtype, confidence, reasoning = max(hits, key=lambda h: h[1])
            result["detected"] = True
            result["sensor_subtype"] = subtype
            result["confidence"] = confidence
            result["reasoning"] = reasoning

        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("传感器故障检测失败: %s", exc)
        return _empty_sensor_fault_result()


def _quality_kernel(
    pv_quality: np.ndarray,
    threshold: dict | None = None,
    *,
    sample_interval: float = 1.0,
) -> dict[str, Any]:
    """PV 质量码 Q001-Q005 规则（等价复制自引擎 _analyze_quality L2024-2162）。

    输入为 0/1/2 数值质量码序列（GOOD/UNCERTAIN/BAD），内部翻译为标签后走原逻辑。

    优化增强（相对旧引擎）：Q001 置信度按最长连续 Bad 段时长分级
    （≤60s→0.6 / ≤600s→0.75 / >600s→0.9），并输出 bad 段起止索引。
    """
    if threshold is None:
        threshold = {}
    q001_consecutive_bad = int(threshold.get("q001_consecutive_bad", 10))
    q002_bad_rate = float(threshold.get("q002_bad_rate", 0.1))
    q003_uncertain_rate = float(threshold.get("q003_uncertain_rate", 0.2))
    q004_bad_duration = int(threshold.get("q004_bad_duration", 5))
    q005_min_bad = int(threshold.get("q005_min_bad", 3))
    q005_max_bad = int(threshold.get("q005_max_bad", 10))

    quality_seq = [_QUALITY_LABELS.get(int(q), "GOOD") for q in pv_quality]
    total = len(quality_seq)
    if total == 0:
        return _empty_quality_result()

    try:
        bad_count = 0
        uncertain_count = 0
        for q in quality_seq:
            if q == "BAD":
                bad_count += 1
            elif q == "UNCERTAIN":
                uncertain_count += 1

        bad_rate = bad_count / total
        uncertain_rate = uncertain_count / total

        # 计算 Bad 连续段（用于 Q001/Q004/Q005）；记录每段起止索引（闭区间）
        bad_segments: list[int] = []
        bad_runs: list[tuple[int, int, int]] = []  # (start_idx, end_idx, length)
        current_bad_run = 0
        max_consecutive_bad = 0
        for idx, q in enumerate(quality_seq):
            if q == "BAD":
                current_bad_run += 1
            else:
                if current_bad_run > 0:
                    bad_segments.append(current_bad_run)
                    bad_runs.append((idx - current_bad_run, idx - 1, current_bad_run))
                    max_consecutive_bad = max(max_consecutive_bad, current_bad_run)
                current_bad_run = 0
        if current_bad_run > 0:
            bad_segments.append(current_bad_run)
            bad_runs.append((total - current_bad_run, total - 1, current_bad_run))
            max_consecutive_bad = max(max_consecutive_bad, current_bad_run)

        q001_hit = max_consecutive_bad > q001_consecutive_bad
        q004_hit = any(seg > q004_bad_duration for seg in bad_segments) and not q001_hit
        q005_hit = any(q005_min_bad <= seg <= q005_max_bad for seg in bad_segments)
        q002_hit = bad_rate > q002_bad_rate and not q001_hit
        q003_hit = uncertain_rate > q003_uncertain_rate

        # 按优先级选择质量模式（Q001 > Q004 > Q002 > Q003 > Q005 > NORMAL）
        if q001_hit:
            quality_pattern = "Q001"
            # 置信度按最长 Bad 段时长分级（秒 = 点数 × 采样间隔）：
            # 瞬时断流(≤60s) 0.6 / 短时断流(≤600s) 0.75 / 持续断流(>600s) 0.9
            max_bad_seconds = max_consecutive_bad * max(sample_interval, 1e-3)
            confidence = 0.9 if max_bad_seconds > 600 else (0.75 if max_bad_seconds > 60 else 0.6)
            abnormal = True
        elif q004_hit:
            quality_pattern = "Q004"
            confidence = 0.8
            abnormal = True
        elif q002_hit:
            quality_pattern = "Q002"
            confidence = 0.6
            abnormal = True
        elif q003_hit:
            quality_pattern = "Q003"
            confidence = 0.6
            abnormal = True
        elif q005_hit:
            quality_pattern = "Q005"
            confidence = 0.4
            abnormal = True
        else:
            quality_pattern = "NORMAL"
            confidence = 0.0
            abnormal = False

        return {
            "abnormal": abnormal,
            "confidence": confidence,
            "bad_rate": bad_rate,
            "total": total,
            "bad_count": bad_count,
            "quality_pattern": quality_pattern,
            "max_consecutive_bad": max_consecutive_bad,
            "bad_runs": bad_runs,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("质量码分析失败: %s", exc)
        return _empty_quality_result()


@operator(
    OperatorMeta(
        name="sensor_fault",
        display_name="传感器故障检测",
        family="sensor",
        diag_code="QUALITY_ABNORMAL",
        description="波形侧卡死/噪声突增/漂移三子检测（SP 同向变化可排除工艺漂移）",
        required_signals=("pv",),
        min_sample_rate=0.0,
        outputs_schema={
            "sensor_subtype": "故障子类型(frozen/noisy/drift)",
            "frozen_max_segment": "最长冻结段点数",
            "noise_std_ratio": "前后段噪声比",
            "drift_magnitude": "漂移幅度",
        },
        threshold_schema={
            "frozen_window": 300,
            "frozen_eps": 1e-4,
            "frozen_ratio": 0.2,
            "noise_ratio": 3.0,
            "noise_segment": 0.5,
            "drift_k": 2.0,
            "drift_segments": 5,
        },
        symptom_tags=("QUALITY_ABNORMAL",),
        fast_group=True,
        confidence_basis=(
            "按故障子类型定档取最高：卡死 0.85 / 噪声突增 0.70 / 漂移 0.65；"
            "SP 同向同步变化可排除漂移（工艺真实变化不扣分）"
        ),
    )
)
def detect_sensor_fault(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv = input.signals.get("pv")
    if pv is None or len(pv) < MIN_DATA_POINTS:
        return OperatorResult("sensor_fault", executed=False, skip_reason="pv 数据不足")
    res = _sensor_fault_kernel(pv, input.signals.get("sp"), threshold)
    return OperatorResult(
        "sensor_fault",
        executed=True,
        detected=bool(res["detected"]),
        confidence=float(res["confidence"]),
        features={
            "sensor_subtype": res["sensor_subtype"],
            "frozen_max_segment": res["frozen_max_segment"],
            "frozen_segment_ratio": round(float(res["frozen_segment_ratio"]), 4),
            "noise_std_ratio": res["noise_std_ratio"],
            "drift_magnitude": res["drift_magnitude"],
        },
        evidence=[
            EvidenceItem(
                "sensor_subtype",
                res["sensor_subtype"],
                None,
                res["reasoning"] or "无命中",
            ),
        ],
    )


@operator(
    OperatorMeta(
        name="quality_code_rules",
        display_name="PV 质量码规则检测",
        family="link",
        diag_code="QUALITY_ABNORMAL",
        description=(
            "Q001-Q005 质量码时序模式规则（连续 Bad/间歇 Bad/Uncertain/突变/恢复）；"
            "Q001 连续断流段指向通信链路问题（独立 link 族）"
        ),
        required_signals=("pv_quality",),
        min_sample_rate=0.0,
        outputs_schema={
            "quality_pattern": "命中模式(Q001-Q005/NORMAL)",
            "bad_rate": "Bad 点占比",
            "bad_count": "Bad 点数",
            "bad_segments": "断流段定位（最长 3 段，窗口偏移秒）",
        },
        threshold_schema={
            "q001_consecutive_bad": 10,
            "q002_bad_rate": 0.1,
            "q003_uncertain_rate": 0.2,
            "q004_bad_duration": 5,
            "q005_min_bad": 3,
            "q005_max_bad": 10,
        },
        symptom_tags=("LINK_ABNORMAL",),
        fast_group=False,
        confidence_basis=(
            "按命中模式定档：Q001 按最长连续断流段时长分级"
            "（≤60s→0.60 / ≤600s→0.75 / >600s→0.90）；"
            "Q004 持续坏点 0.80；Q002 高坏点率 0.60 / Q003 高不确定率 0.60；"
            "Q005 零星坏点 0.40"
        ),
    )
)
def detect_quality(input: OperatorInput, threshold: dict[str, Any]) -> OperatorResult:
    pv_quality = input.signals.get("pv_quality")
    if pv_quality is None or len(pv_quality) == 0:
        return OperatorResult("quality_code_rules", executed=False, skip_reason="pv_quality 缺失")
    sample_interval = float(input.meta.get("sample_interval", 1.0))
    res = _quality_kernel(pv_quality, threshold, sample_interval=sample_interval)

    # 断流段详情（最多 3 段，按长度降序）：偏移秒 = 索引 × 采样间隔；
    # 若编排层提供原始时间轴 pv_quality_ts（与 pv_quality 同长度）则用精确值。
    # 前端结合 timeWindowStart + offset 展示本地钟点。
    pv_quality_ts = input.signals.get("pv_quality_ts")
    ts_arr = (
        np.asarray(pv_quality_ts, dtype=float)
        if pv_quality_ts is not None and len(pv_quality_ts) == len(pv_quality)
        else None
    )
    bad_segments: list[dict[str, Any]] = []
    for start, end, length in sorted(res.get("bad_runs") or [], key=lambda r: r[2], reverse=True)[
        :3
    ]:
        seg: dict[str, Any] = {"points": length}
        if ts_arr is not None:
            seg["start_offset_s"] = round(float(ts_arr[start]), 1)
            seg["end_offset_s"] = round(float(ts_arr[end]), 1)
        else:
            seg["start_offset_s"] = round(start * sample_interval, 1)
            seg["end_offset_s"] = round(end * sample_interval, 1)
        bad_segments.append(seg)

    top_seg = bad_segments[0] if bad_segments else None
    seg_judgment = "质量码模式 " + ("异常" if res["abnormal"] else "正常")
    if top_seg is not None and res["abnormal"]:
        seg_judgment += (
            f"；最长断流段 {top_seg['points']} 点"
            f"（窗口偏移 {top_seg['start_offset_s']}~{top_seg['end_offset_s']}s）"
        )
    return OperatorResult(
        "quality_code_rules",
        executed=True,
        detected=bool(res["abnormal"]),
        confidence=float(res["confidence"]),
        features={
            "quality_pattern": res["quality_pattern"],
            "bad_rate": round(float(res["bad_rate"]), 4),
            "bad_count": res["bad_count"],
            "total": res["total"],
            "max_consecutive_bad": res.get("max_consecutive_bad", 0),
            "bad_segments": bad_segments,
        },
        evidence=[
            EvidenceItem(
                "quality_pattern",
                res["quality_pattern"],
                "NORMAL",
                seg_judgment,
            ),
        ],
    )
