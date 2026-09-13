"""诊断编排器（MVP v2）：取数 → 门禁 → 元算子 → 族内融合 → 原因分类 → 快照 → 落库。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §4.2 / §4.3
取数路径与旧引擎一致（D3 决策）：get_provider() 宽表查询（本地 TDengine 唯一数据源）
+ B4 异常点剔除（DataQualityAssessor）+ ConfidenceEvaluator 分级；
数据门禁＝消费日常监测层已有质量结论，不过关直接输出 DATA_INSUFFICIENT（正常完成）。
"""

from __future__ import annotations

import logging
import math
import warnings
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import func, select

from app.contracts.data_types import ControlType, LoopPreprocessConfig, QualityStatus, RawTimeSeries
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.services.confidence_evaluator import ConfidenceEvaluator
from app.services.data_source.factory import get_provider
from app.services.diagnosis_operators import OPERATOR_REGISTRY, OperatorInput, OperatorResult
from app.services.diagnosis_operators.classification import ClassificationResult, classify
from app.services.diagnosis_operators.fusion import FamilyFusion, fuse_family
from app.services.diagnosis_operators.gate import evaluate_gate
from app.services.diagnosis_system_actions import generate_system_actions_best_effort
from app.services.preprocessing.data_quality_assessor import DataQualityAssessor
from app.services.preprocessing.quality_code import map_quality_code
from app.services.waveform import lttb_downsample_multi_series

logger = logging.getLogger(__name__)

MVP_DIAG_VERSION = "MVP_DIAG_V2_v1.0"
MAX_CHART_POINTS = 2000

#: 进度回调：async (loop_fraction 0~1, stage_name) -> None
ProgressCb = Callable[[float, str], Awaitable[None]]

#: B4：loop_type → 预处理控制类型映射（复制自引擎 L102-108，与 kpi_calc 对齐）
_LOOP_TYPE_TO_CONTROL_TYPE: dict[str, ControlType] = {
    "FLOW": ControlType.FLOW,
    "PRESSURE": ControlType.PRESSURE,
    "TEMPERATURE": ControlType.TEMPERATURE,
    "LEVEL": ControlType.LEVEL,
    "ANALYSIS": ControlType.COMPOSITION,
}

#: 质量标签 → 数值编码（quality_code_rules 算子输入契约）
_QUALITY_CODE_MAP = {"GOOD": 0, "UNCERTAIN": 1, "BAD": 2}


# ---------------------------------------------------------------------------
# 等价复制的取数辅助（引擎 L3991-4003 / L4113-4203）
# ---------------------------------------------------------------------------


def _resolve_pv_range(
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
) -> tuple[float, float]:
    """解析 PV Tag 量程（复制自引擎 L3991-4003），缺省回退 0.0~100.0。"""
    mapping = mappings.get("PV")
    tag = tags_map.get(str(mapping.tag_id)) if mapping else None
    range_min = getattr(tag, "range_min", None)
    range_max = getattr(tag, "range_max", None)
    min_v = float(range_min) if isinstance(range_min, int | float) else 0.0
    max_v = float(range_max) if isinstance(range_max, int | float) else 100.0
    if max_v <= min_v:
        return 0.0, 100.0
    return min_v, max_v


def _resolve_op_range(
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
) -> tuple[float, float]:
    """解析 OP Tag 量程（趋势图右轴用），缺省回退 0.0~100.0。"""
    mapping = mappings.get("OP")
    tag = tags_map.get(str(mapping.tag_id)) if mapping else None
    range_min = getattr(tag, "range_min", None)
    range_max = getattr(tag, "range_max", None)
    min_v = float(range_min) if isinstance(range_min, int | float) else 0.0
    max_v = float(range_max) if isinstance(range_max, int | float) else 100.0
    if max_v <= min_v:
        return 0.0, 100.0
    return min_v, max_v


def _ts_list_to_seconds(ts_list: list[Any]) -> np.ndarray:
    """时间戳序列 → 浮点秒（向量化，复制自引擎 L4113-4173）。

    项目红线：禁止对 naive datetime 逐点调 .timestamp()。
    """
    if not ts_list:
        return np.array([], dtype=float)
    if all(isinstance(t, int | float) and not isinstance(t, bool) for t in ts_list):
        return np.asarray(ts_list, dtype=float)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            dt_arr = np.asarray(ts_list, dtype="datetime64[us]")
        return dt_arr.astype("int64").astype(float) / 1e6
    except (TypeError, ValueError):
        pass

    values = np.full(len(ts_list), np.nan, dtype=float)
    base: datetime | None = None
    for i, ts in enumerate(ts_list):
        if isinstance(ts, bool):
            continue
        if isinstance(ts, int | float):
            values[i] = float(ts)
            continue
        dt: datetime | None = None
        if isinstance(ts, datetime):
            dt = ts
        else:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
        if base is None:
            base = dt
            values[i] = 0.0
            continue
        try:
            values[i] = (dt - base).total_seconds()
        except TypeError:
            continue
    return values


def _compute_sample_interval(aligned: list[dict[str, Any]]) -> float:
    """平均采样间隔（秒，复制自引擎 L4176-4203，向量化差分）。"""
    raw_ts = [d.get("ts") for d in aligned if d.get("ts") is not None]
    if len(raw_ts) < 2:
        return 1.0
    ts_values = _ts_list_to_seconds(raw_ts)
    ts_values = ts_values[~np.isnan(ts_values)]
    if len(ts_values) < 2:
        return 1.0
    diffs = np.diff(ts_values)
    positive = diffs[diffs > 0]
    if len(positive) == 0:
        return 1.0
    return float(np.mean(positive))


def _mark_outlier_preprocessing_point(
    aligned: list[dict[str, Any]],
    src_indices: list[int],
    raw_series: RawTimeSeries,
    loop: LoopLedger,
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
) -> tuple[list[dict[str, Any]], float]:
    """point 路径 B4：异常点**标记**不删行（AD04/I03——网格对齐保持）.

    与 _apply_outlier_preprocessing 同一评估器/阈值；差异仅在处置：
    invalid 行置 row_valid=False（保留轴位），valid_rate 为全窗回路级口径。
    """
    n_raw = len(raw_series.timestamps)
    try:
        loop_type = loop.loop_type if isinstance(loop.loop_type, str) else ""
        control_type = _LOOP_TYPE_TO_CONTROL_TYPE.get(loop_type.upper(), ControlType.FLOW)
        range_min, range_max = _resolve_pv_range(mappings, tags_map)
        config = LoopPreprocessConfig(
            loop_id=loop.tag_name,
            control_type=control_type,
            range_min=range_min,
            range_max=range_max,
        )
        assessor = DataQualityAssessor(config)
        assessment = assessor.assess(raw_series)
        pv_valid_raw = assessment.validity.get("pv_valid", [True] * n_raw)
        op_valid_raw = assessment.validity.get("op_valid", [True] * n_raw)
        op_values_raw = raw_series.signals.get("op")
        marked = 0
        for aligned_i, src_i in enumerate(src_indices):
            if src_i >= n_raw or aligned_i >= len(aligned):
                continue
            invalid = False
            if not pv_valid_raw[src_i]:
                invalid = True
            elif op_values_raw and op_values_raw[src_i] is not None and not op_valid_raw[src_i]:
                invalid = True
            if invalid:
                aligned[aligned_i]["row_valid"] = False
                marked += 1
        if marked:
            logger.info(
                "回路 %s point 异常点标记 %d/%d（行保留，轴对齐）",
                loop.tag_name,
                marked,
                len(aligned),
            )
        return aligned, assessment.loop_valid_rate
    except Exception as exc:  # noqa: BLE001 — 评估失败按未标记继续（显式告警）
        logger.warning("回路 %s point B4 预处理失败，按未标记继续: %s", loop.tag_name, exc)
        fallback_rate = (
            sum(1 for d in aligned if d.get("row_valid")) / len(aligned) if aligned else 0.0
        )
        return aligned, round(fallback_rate, 4)


def _apply_outlier_preprocessing(
    aligned: list[dict[str, Any]],
    src_indices: list[int],
    raw_series: RawTimeSeries,
    loop: LoopLedger,
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
) -> tuple[list[dict[str, Any]], float]:
    """B4 异常点剔除 + 回路级 valid_rate（等价复制自引擎 L4005-4095）。"""
    n_raw = len(raw_series.timestamps)
    try:
        loop_type = loop.loop_type if isinstance(loop.loop_type, str) else ""
        control_type = _LOOP_TYPE_TO_CONTROL_TYPE.get(loop_type.upper(), ControlType.FLOW)
        range_min, range_max = _resolve_pv_range(mappings, tags_map)

        config = LoopPreprocessConfig(
            loop_id=loop.tag_name,
            control_type=control_type,
            range_min=range_min,
            range_max=range_max,
        )
        assessor = DataQualityAssessor(config)
        assessment = assessor.assess(raw_series)

        pv_valid_raw = assessment.validity.get("pv_valid", [True] * n_raw)
        op_valid_raw = assessment.validity.get("op_valid", [True] * n_raw)
        op_values_raw = raw_series.signals.get("op")

        invalid_idx: set[int] = set()
        for aligned_i, src_i in enumerate(src_indices):
            if src_i >= n_raw:
                continue
            if not pv_valid_raw[src_i]:
                invalid_idx.add(aligned_i)
            elif op_values_raw and op_values_raw[src_i] is not None and not op_valid_raw[src_i]:
                invalid_idx.add(aligned_i)

        removed = len(invalid_idx)
        if removed:
            ratio = removed / len(aligned) if aligned else 0.0
            logger.info(
                "回路 %s 异常点剔除 %d/%d（%.1f%%）",
                loop.tag_name,
                removed,
                len(aligned),
                ratio * 100,
            )

        filtered = [d for i, d in enumerate(aligned) if i not in invalid_idx]
        return filtered, assessment.loop_valid_rate
    except Exception as exc:  # noqa: BLE001
        logger.warning("回路 %s B4 异常点预处理失败，按未剔除继续: %s", loop.tag_name, exc)
        fallback_rate = len(aligned) / n_raw if n_raw else 0.0
        return aligned, round(fallback_rate, 4)


# ---------------------------------------------------------------------------
# KPI 上下文 / 阈值
# ---------------------------------------------------------------------------


async def _kpi_context(
    db: Any,
    loop_id: str,
    start: datetime,
    end: datetime,
    expected_dataset_ref: str | None = None,
) -> dict[str, Any]:
    """时间窗内 KPI 快照均值（投用率/评分），供分类映射层消费。

    附带 ``_window_averages``（camelCase 全指标均值）供 metricSummary 聚合复用
    （下划线键为编排器内部约定，分类层不消费）。
    """
    kpi_avgs = await _kpi_window_averages(
        db, loop_id, start, end, expected_dataset_ref=expected_dataset_ref
    )
    # 量纲归一：快照列 effective_auto_rate 为 0~100（metric_calculator/
    # effective_auto.py 以 *100 落库），而分类层的契约是 0~1
    # （classification.py 的 UTILIZATION_DETECT_RATE=0.5 / UTILIZATION_PRIORITY_RATE=0.3、
    #  min(0.95, 1.0-auto_rate)、f"{auto_rate:.0%}" 均按分数语义）。
    # 此前未归一导致 UTILIZATION 分支仅在投用率 <0.5% 时触发，实质永不生效。
    raw_auto_rate = kpi_avgs.get("effectiveAutoRate")
    return {
        "auto_rate_avg": (float(raw_auto_rate) / 100.0) if raw_auto_rate is not None else None,
        "score_avg": kpi_avgs.get("score"),
        "_window_averages": kpi_avgs,
    }


#: 窗口 KPI 均值查询列（metricSummary 聚合 + 分类上下文共用）
_KPI_AVG_COLUMNS = (
    "score",
    "effective_auto_rate",
    "auto_mode_rate",
    "good_value_rate",
    "steady_rate",
    "accuracy_rate",
    "fast_rate",
    "oscillation_rate",
    "saturation_rate",
    "stiction_index",
    "settling_time",
    "output_trip_index",
)


async def _kpi_window_averages(
    db: Any,
    loop_id: str,
    start: datetime,
    end: datetime,
    expected_dataset_ref: str | None = None,
) -> dict[str, Any]:
    """诊断时间窗内 KPI 快照各指标均值（camelCase 键；窗口无快照返回空 dict）。

    与 _kpi_context 同口径：ts_start 落在 [start, end] 的 SUCCESS 快照。
    AD07/I06：``expected_dataset_ref`` 非空（point 诊断）时按快照血缘的
    dataset_ref 过滤——不兼容版本（旧布局/无引用）不混入均值；全部不兼容
    返回空 dict（走既有"无可用 KPI 上下文"路径；旧结果保留不重算）。
    """
    from app.models.metric import KpiSnapshotHourly as _K

    _snapshot_filter = (
        _K.loop_id == loop_id,
        _K.ts_start >= start,
        _K.ts_start <= end,
        _K.status == "SUCCESS",
    )
    if expected_dataset_ref is not None:
        rows = (await db.execute(select(_K.id, _K.data_lineage).where(*_snapshot_filter))).all()
        compatible = []
        for _sid, _lineage in rows:
            ref = None
            if isinstance(_lineage, dict):
                ref = _lineage.get("dataset_ref")
            if ref == expected_dataset_ref:
                compatible.append(_sid)
        if not compatible:
            return {}
        cols = [func.avg(getattr(_K, c)) for c in _KPI_AVG_COLUMNS]
        row = (
            await db.execute(select(*cols).where(*_snapshot_filter, _K.id.in_(compatible)))
        ).one_or_none()
    else:
        cols = [func.avg(getattr(_K, c)) for c in _KPI_AVG_COLUMNS]
        row = (await db.execute(select(*cols).where(*_snapshot_filter))).one_or_none()
    if not row or all(v is None for v in row):
        return {}

    def _camel(snake: str) -> str:
        parts = snake.split("_")
        return parts[0] + "".join(p.title() for p in parts[1:])

    # row 可能是较短的 mock 元组（测试）或完整 12 列聚合行；按列名 zip 截齐
    values = tuple(row)[: len(_KPI_AVG_COLUMNS)]
    return {
        _camel(c): round(float(v), 2)
        for c, v in zip(_KPI_AVG_COLUMNS, values, strict=False)
        if v is not None
    }


def _build_metric_summary(
    kpi_avgs: dict[str, Any],
    op_results: dict[str, Any],
) -> dict[str, Any]:
    """诊断指标汇总（方案 A）：窗口 KPI 均值 + 算子特征，统一 0~100 口径。

    口径约定：
    - KPI 源指标（0~100）：饱和率/振荡率/粘滞系数/好值率等直接取窗口均值；
    - 算子源指标（0~1 小数）：saturation_rate/bad_rate/stiction_index 换算 ×100，
      仅在窗口无对应 KPI 快照时兜底（KPI 与诊断同一时间窗，优先级更高）；
    - 坏值率：KPI 好值率均值换算 100−goodValueRate（对齐原工作台诊断卡口径），
      无快照时用质量码算子 bad_rate×100 兜底；
    - 稳定时间（秒）/行程指数（0~100）：非率类指标原值透传，前端按各自单位展示。

    Returns:
        {
          "negative": {badValueRate, saturationRate, oscillationRate,
                       stictionIndex, settlingTime, outputTravelIndex},
          "positive": {score, effectiveAutoRate, autoModeRate, goodValueRate,
                       steadyRate, accuracyRate, fastRate},
          "source":   {各指标来源 kpi|operator|derived，供前端标注}
        }
    """
    ops: dict[str, Any] = op_results or {}

    def _op_feature(op_name: str, key: str) -> Any:
        feats = (ops.get(op_name) or {}).get("features") or {}
        return feats.get(key)

    def _pct_from_ratio(v: Any) -> Any:
        """0~1 小数 → 0~100 百分比（None/非法透传 None）。"""
        if v is None:
            return None
        try:
            return round(float(v) * 100.0, 2)
        except (TypeError, ValueError):
            return None

    # ---- 负向指标 ----
    bad_rate_ratio = _op_feature("quality_code_rules", "bad_rate")
    if kpi_avgs.get("goodValueRate") is not None:
        bad_value_rate = round(100.0 - float(kpi_avgs["goodValueRate"]), 2)
        bad_src = "kpi"
    elif bad_rate_ratio is not None:
        bad_value_rate = _pct_from_ratio(bad_rate_ratio)
        bad_src = "operator"
    else:
        bad_value_rate, bad_src = None, "none"

    if kpi_avgs.get("saturationRate") is not None:
        sat_rate, sat_src = kpi_avgs["saturationRate"], "kpi"
    else:
        sat_rate = _pct_from_ratio(_op_feature("output_saturation", "saturation_rate"))
        sat_src = "operator" if sat_rate is not None else "none"

    osc_rate = kpi_avgs.get("oscillationRate")
    stiction = kpi_avgs.get("stictionIndex")
    if stiction is None:
        stiction = _pct_from_ratio(_op_feature("stiction_ellipse", "stiction_index"))
    settling = kpi_avgs.get("settlingTime")
    travel = kpi_avgs.get("outputTripIndex")

    negative: dict[str, Any] = {
        "badValueRate": bad_value_rate,
        "saturationRate": sat_rate,
        "oscillationRate": osc_rate,
        "stictionIndex": stiction,
        "settlingTime": settling,
        "outputTravelIndex": travel,
    }
    source: dict[str, str] = {
        "badValueRate": bad_src,
        "saturationRate": sat_src,
        "oscillationRate": "kpi" if osc_rate is not None else "none",
        "stictionIndex": (
            "kpi"
            if kpi_avgs.get("stictionIndex") is not None
            else ("operator" if stiction is not None else "none")
        ),
        "settlingTime": "kpi" if settling is not None else "none",
        "outputTravelIndex": "kpi" if travel is not None else "none",
    }

    # ---- 正向指标（全部 KPI 窗口均值） ----
    positive = {
        k: kpi_avgs.get(k)
        for k in (
            "score",
            "effectiveAutoRate",
            "autoModeRate",
            "goodValueRate",
            "steadyRate",
            "accuracyRate",
            "fastRate",
        )
    }

    return {"negative": negative, "positive": positive, "source": source}


async def _effective_thresholds(db: Any, loop_id: str) -> dict[str, dict[str, Any]]:
    """四级阈值覆盖（复用 diagnosis_threshold.recommend_for_loop）；失败回退算子默认。"""
    try:
        from app.services.diagnosis_threshold import recommend_for_loop

        result = await recommend_for_loop(db, loop_id)
        effective = result.get("effectiveThreshold")
        return effective if isinstance(effective, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.info("阈值覆盖加载失败（回退算子默认值）: %s", exc)
        return {}


def _thresholds_for(
    defaults: dict[str, Any],
    effective: dict[str, Any],
    diag_code: str,
) -> dict[str, Any]:
    merged = dict(defaults)
    override = effective.get(diag_code)
    if isinstance(override, dict):
        merged.update(override)
    return merged


# ---------------------------------------------------------------------------
# 波形快照
# ---------------------------------------------------------------------------


def _build_chart_snapshots(
    aligned: list[dict[str, Any]],
    pv_range: tuple[float, float] | None = None,
    op_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """证据波形快照：趋势（LTTB ≤2000 点）+ PV-OP 散点（等步长抽稀 ≤2000）。

    趋势附带 pvRange/opRange 轴量程（前端双 Y 轴：左轴 PV/SP，右轴 OP）；
    量程解析失败时不带字段，前端回退数据自适应。
    """
    n = len(aligned)
    if n == 0:
        empty_trend: dict[str, Any] = {"ts": [], "pv": [], "sp": [], "op": []}
        if pv_range:
            empty_trend["pvRange"] = {"min": pv_range[0], "max": pv_range[1]}
        if op_range:
            empty_trend["opRange"] = {"min": op_range[0], "max": op_range[1]}
        return {"trend": empty_trend, "scatter": {"pv": [], "op": []}}

    ts_sec = _ts_list_to_seconds([d.get("ts") for d in aligned])
    base = float(np.nanmin(ts_sec)) if len(ts_sec) else 0.0
    ts_ms = [int((float(t) - base) * 1000) if math.isfinite(float(t)) else 0 for t in ts_sec]

    series_map = {
        "pv": [d.get("pv") for d in aligned],
        "sp": [d.get("sp") for d in aligned],
        "op": [d.get("op") for d in aligned],
    }
    if n > MAX_CHART_POINTS:
        down_ts, down_series = lttb_downsample_multi_series(ts_ms, series_map, MAX_CHART_POINTS)
        trend = {
            "ts": down_ts,
            "pv": down_series.get("pv", []),
            "sp": down_series.get("sp", []),
            "op": down_series.get("op", []),
        }
    else:
        trend = {"ts": ts_ms, **series_map}
    if pv_range:
        trend["pvRange"] = {"min": pv_range[0], "max": pv_range[1]}
    if op_range:
        trend["opRange"] = {"min": op_range[0], "max": op_range[1]}

    scatter_pv = [d["pv"] for d in aligned if d.get("pv") is not None and d.get("op") is not None]
    scatter_op = [d["op"] for d in aligned if d.get("pv") is not None and d.get("op") is not None]
    if len(scatter_pv) > MAX_CHART_POINTS:
        stride = math.ceil(len(scatter_pv) / MAX_CHART_POINTS)
        idx = np.arange(0, len(scatter_pv), stride)
        scatter_pv = [scatter_pv[i] for i in idx]
        scatter_op = [scatter_op[i] for i in idx]

    return {
        "trend": trend,
        "scatter": {"pv": scatter_pv, "op": scatter_op},
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _operator_result_to_dict(r: OperatorResult) -> dict[str, Any]:
    return {
        "operator": r.operator,
        "executed": r.executed,
        "skipReason": r.skip_reason,
        "detected": r.detected,
        "confidence": round(float(r.confidence), 4),
        "features": r.features,
        "evidence": [
            {
                "feature": e.feature,
                "value": e.value,
                "threshold": e.threshold,
                "judgment": e.judgment,
            }
            for e in r.evidence
        ],
        "error": r.error,
    }


def _scoped_operator_input(op_input: OperatorInput, meta: Any) -> tuple[OperatorInput, str | None]:
    """point 同轴算子输入（AD04/I03）：按 required_signals 公共有效掩码切片.

    - 参与切片信号：required_signals 中的数值信号（pv/sp/op/mode）；
    - 公共掩码 = 各信号"非 None 且（有掩码时）valid"交集——所有列与时间
      戳**同一索引集**切片，禁止各列独立 compact 或 min 截齐；
    - 质量轴（pv_quality/pv_quality_ts）不参与掩码：required 含质量的算子
      消费**完整质量轴**（I03：质量码算子全轴）；
    - 掩码后样本不足（<2）→ 返回 skip 原因（不虚构输入）。
    """
    numeric = [
        sig
        for sig in meta.required_signals
        if sig in ("pv", "sp", "op", "mode") and sig in op_input.signals
    ]
    signals_valid = op_input.meta.get("signals_valid") or {}
    n = len(op_input.timestamps)
    common = np.ones(n, dtype=bool)
    for sig in numeric:
        arr = op_input.signals[sig]
        present = np.array([arr[i] is not None for i in range(min(len(arr), n))])
        present = np.pad(present, (0, max(0, n - len(present))), constant_values=False)
        mask = present.copy()
        sv = signals_valid.get(sig)
        if sv is not None and len(sv) >= n:
            mask = mask & np.asarray(sv[:n], dtype=bool)
        common = common & mask
    idx = np.nonzero(common)[0]
    if len(idx) < 2:
        return op_input, "point 同轴切片后有效样本不足（<2）"
    sliced_signals = dict(op_input.signals)
    for sig in numeric:
        sliced_signals[sig] = np.asarray(op_input.signals[sig])[idx]
    sliced_signals["pv_quality"] = op_input.signals["pv_quality"]  # 全轴
    if "pv_quality_ts" in op_input.signals:
        sliced_signals["pv_quality_ts"] = op_input.signals["pv_quality_ts"]  # 全轴
    scoped = OperatorInput(
        loop_id=op_input.loop_id,
        signals=sliced_signals,
        timestamps=np.asarray(op_input.timestamps)[idx],
        meta={
            **{k: v for k, v in op_input.meta.items() if k != "signals_valid"},
            "sample_interval": op_input.meta.get("sample_interval", 1.0),
            "total_points": int(len(idx)),
        },
        kpi_context=op_input.kpi_context,
    )
    return scoped, None


def _run_operators(
    op_input: OperatorInput,
    effective_thresholds: dict[str, dict[str, Any]],
    operator_group: str,
    operators: list[str] | None = None,
) -> tuple[dict[str, OperatorResult], dict[str, FamilyFusion]]:
    """执行全部算子并按症状分组做族内融合。

    operators 为单算子细选白名单（None=不细选）：与 fast 组过滤叠加生效。
    """
    selected = set(operators) if operators else None
    results: dict[str, OperatorResult] = {}
    for name, (meta, fn) in OPERATOR_REGISTRY.items():
        if operator_group == "fast" and not meta.fast_group:
            results[name] = OperatorResult(name, executed=False, skip_reason="fast 组未包含该算子")
            continue
        if selected is not None and name not in selected:
            results[name] = OperatorResult(name, executed=False, skip_reason="未在本次细选算子内")
            continue
        missing = [
            s
            for s in meta.required_signals
            if s not in op_input.signals or len(op_input.signals[s]) == 0
        ]
        if missing:
            results[name] = OperatorResult(
                name, executed=False, skip_reason=f"输入信号缺失: {','.join(missing)}"
            )
            continue
        try:
            thresholds = _thresholds_for(
                dict(meta.threshold_schema), effective_thresholds, meta.diag_code
            )
            fn_input = op_input
            if op_input.meta.get("point_axis"):
                fn_input, scope_skip = _scoped_operator_input(op_input, meta)
                if scope_skip is not None:
                    results[name] = OperatorResult(name, executed=False, skip_reason=scope_skip)
                    continue
            results[name] = fn(fn_input, thresholds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("算子 %s 执行异常: %s", name, exc)
            results[name] = OperatorResult(name, executed=False, error=str(exc))

    # 按症状标签分组融合（每算子恰一个 symptom_tag）
    groups: dict[str, list[OperatorResult]] = {}
    family_of: dict[str, str] = {}
    for name, (meta, _fn) in OPERATOR_REGISTRY.items():
        tag = meta.symptom_tags[0]
        groups.setdefault(tag, []).append(results[name])
        family_of[tag] = meta.family

    fusions = {tag: fuse_family(family_of[tag], tag, group) for tag, group in groups.items()}
    return results, fusions


async def run_diagnosis_for_loop(
    db: Any,
    loop_id: str,
    *,
    start: datetime,
    end: datetime,
    task_id: str | None = None,
    triggered_by: str = "system",
    operator_group: str = "full",
    operators: list[str] | None = None,
    trigger_type: str = "MANUAL",
    progress_cb: ProgressCb | None = None,
) -> DiagnosisRun | None:
    """单回路一次诊断：返回落库后的 DiagnosisRun；回路不存在返回 None。

    operator_group: full/fast；operators 非空时按单算子细选白名单执行
    （落库 operator_group 记为 custom）。
    trigger_type: MANUAL/SCHEDULED/EVENT（§12 自动诊断三层触发）。
    """

    async def _report(frac: float, stage: str) -> None:
        if progress_cb is not None:
            await progress_cb(frac, stage)

    started_at = datetime.utcnow()
    loop = (
        await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    ).scalar_one_or_none()
    if loop is None:
        logger.warning("诊断回路 %s 不存在", loop_id)
        return None

    # Tag 关联与量程
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    mappings = {m.tag_role: m for m in m_result.scalars().all()}
    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 阈值（四级覆盖，失败回退默认）
    effective_thresholds = await _effective_thresholds(db, loop_id)

    # ---- 取数（宽表查询，本地 TDengine）----
    await _report(0.05, "读取历史数据")
    try:
        query_wide_fn = get_provider().make_query_fn(db)
        raw_series = await query_wide_fn(
            loop_id=loop_id,
            tag_roles=["pv", "sp", "op", "mode"],
            start=start,
            end=end,
            interval_s=1,
        )
        if not isinstance(raw_series, RawTimeSeries):
            raw_series = None
    except Exception as exc:  # noqa: BLE001
        logger.warning("诊断宽表查询失败（回路 %s）: %s", loop.tag_name, exc)
        raw_series = None

    raw_series = raw_series if isinstance(raw_series, RawTimeSeries) else None

    # ---- 对齐 + 质量码过滤（复制引擎 L1053-1092；point 分支 AD04/I03）----
    point_ctx = getattr(raw_series, "series_context", None) if raw_series is not None else None
    is_point = point_ctx is not None and getattr(point_ctx, "is_point", False)
    pv_quality_codes: list[int] = []
    aligned: list[dict[str, Any]] = []
    aligned_src_indices: list[int] = []
    if raw_series is not None:
        raw_pv_quality = raw_series.quality_codes.get("pv_quality", [])
        for i in range(len(raw_series.timestamps)):
            status = (
                map_quality_code(raw_pv_quality[i])
                if i < len(raw_pv_quality)
                else QualityStatus.GOOD
            )
            quality_label = "UNCERTAIN" if status == QualityStatus.UNKNOWN else status.value.upper()
            pv_quality_codes.append(_QUALITY_CODE_MAP.get(quality_label, 0))
            pv_list = raw_series.signals.get("pv")
            pv_val = pv_list[i] if pv_list and i < len(pv_list) else None
            sp_list = raw_series.signals.get("sp")
            op_list = raw_series.signals.get("op")
            mode_list = raw_series.signals.get("mode")
            row_bad = status == QualityStatus.BAD or pv_val is None
            if is_point:
                # AD04/I03：point 全网格保留（含未知/BAD 行），行有效性标记；
                # 各列不再独立删点——同轴由算子输入的公共掩码切片保证
                aligned.append(
                    {
                        "ts": raw_series.timestamps[i],
                        "pv": pv_val,
                        "sp": sp_list[i] if sp_list and i < len(sp_list) else None,
                        "op": op_list[i] if op_list and i < len(op_list) else None,
                        "mode": mode_list[i] if mode_list and i < len(mode_list) else None,
                        "row_valid": not row_bad,
                    }
                )
                aligned_src_indices.append(i)
                continue
            if status == QualityStatus.BAD:
                continue
            if pv_val is None:
                continue
            aligned.append(
                {
                    "ts": raw_series.timestamps[i],
                    "pv": pv_val,
                    "sp": sp_list[i] if sp_list and i < len(sp_list) else None,
                    "op": op_list[i] if op_list and i < len(op_list) else None,
                    "mode": mode_list[i] if mode_list and i < len(mode_list) else None,
                }
            )
            aligned_src_indices.append(i)

    # ---- B4 异常点剔除 + 可信度分级（point：标记不删行）----
    if raw_series is not None and aligned:
        await _report(0.15, "数据质量预处理")
        if is_point:
            aligned, valid_rate = _mark_outlier_preprocessing_point(
                aligned, aligned_src_indices, raw_series, loop, mappings, tags_map
            )
        else:
            aligned, valid_rate = _apply_outlier_preprocessing(
                aligned, aligned_src_indices, raw_series, loop, mappings, tags_map
            )
    else:
        valid_rate = 0.0
    confidence_level = ConfidenceEvaluator.evaluate(valid_rate).value

    # ---- 数据门禁（消费质量结论；point 口径 AD04/I03 §4.2）----
    await _report(0.2, "数据门禁")
    window_seconds = max(1.0, (end - start).total_seconds())
    if is_point:
        # point：expected=网格期望格点数 N（上下文，不从删点后的轴猜测）；
        # point_count=可用有效样本（行有效 ∩ 异常标记后），占位行不计
        expected_points = int(point_ctx.expected_slots)
        usable = sum(1 for d in aligned if d.get("row_valid"))
    else:
        # 应有点数按实测中位采样间隔推算（中位数对缺失段稳健）：
        # 数据源可能是 1s 或 1min 采样，按 1s 硬编码会把稀疏采样恒判为断点超限
        if len(aligned) >= 2:
            ts_sec = _ts_list_to_seconds([d["ts"] for d in aligned])
            diffs = np.diff(ts_sec)
            positive = diffs[diffs > 0]
            median_interval = float(np.median(positive)) if len(positive) else 1.0
        else:
            median_interval = 1.0
        expected_points = int(window_seconds / max(median_interval, 1e-3))
        usable = len(aligned)
    gate = evaluate_gate(
        point_count=usable,
        expected_points=expected_points,
        valid_rate=valid_rate,
        confidence_level=confidence_level,
    )
    if is_point and not point_ctx.interpretation_consistent:
        # AD06/I07：跨改绑/解释配置边界的窗口——无依据用当前量程/单位解释
        # 旧值，动态诊断拒绝（证据保留：gate 失败原因显式，不虚构正常）
        gate.passed = False
        gate.reason = (
            "窗口跨改绑/解释配置边界（interpretation_consistent=False），"
            "不能用当前配置解释旧值；请按边界分段诊断"
        )

    # AD07/I06：point 诊断的 KPI 上下文按数据版本兼容过滤（不兼容版本不混
    # 入均值——旧结果保留为历史事实，不自动重算）
    kpi_ctx = await _kpi_context(
        db,
        loop_id,
        start,
        end,
        expected_dataset_ref=(
            point_ctx.dataset_ref if is_point and point_ctx is not None else None
        ),
    )
    # 方案 A：窗口 KPI 均值随上下文带回（同一次查询），供 metricSummary 聚合
    kpi_avgs: dict[str, Any] = kpi_ctx.get("_window_averages") or {}
    if is_point:
        sample_interval = float(point_ctx.grid_period_s)
    else:
        sample_interval = _compute_sample_interval(aligned) if aligned else 1.0

    # ---- 算子执行 + 融合 + 分类 ----
    if gate.passed:
        if is_point:
            # AD04/I03：point 全轴组装——各信号等长（None 保位），逐信号有效
            # 掩码随行；同轴切片由 _run_operators 按算子 required_signals 公共
            # 掩码执行（禁止各列独立 compact/min 截齐）。质量码算子消费完整轴。
            signals = {
                "pv": np.array([d.get("pv") for d in aligned], dtype=object),
                "sp": np.array([d.get("sp") for d in aligned], dtype=object),
                "op": np.array([d.get("op") for d in aligned], dtype=object),
                "mode": np.array([d.get("mode") for d in aligned], dtype=object),
                "pv_quality": np.array(pv_quality_codes, dtype=int),
            }
            if raw_series is not None and len(raw_series.timestamps) > 0:
                raw_ts_sec = _ts_list_to_seconds(list(raw_series.timestamps))
                pv_quality_ts = raw_ts_sec - float(np.nanmin(raw_ts_sec))
            else:
                pv_quality_ts = np.array([], dtype=float)
            signals["pv_quality_ts"] = pv_quality_ts
            signals_valid = {
                "pv": np.array([d.get("pv") is not None and d.get("row_valid") for d in aligned]),
                "sp": np.array([d.get("sp") is not None for d in aligned]),
                "op": np.array([d.get("op") is not None for d in aligned]),
                "mode": np.array([d.get("mode") is not None for d in aligned]),
            }
        else:
            op_rows = [d for d in aligned if d.get("op") is not None]
            # 原始序列相对秒（与 pv_quality 同长度/同基准）：供质量码算子把
            # Bad 段索引映射为窗口内偏移秒（前端结合 timeWindowStart 展示
            # 本地钟点）；对齐轴 timestamps 长度不含 BAD 行，无法直接复用
            if raw_series is not None and len(raw_series.timestamps) > 0:
                raw_ts_sec = _ts_list_to_seconds(list(raw_series.timestamps))
                pv_quality_ts = raw_ts_sec - float(np.nanmin(raw_ts_sec))
            else:
                pv_quality_ts = np.array([], dtype=float)
            signals: dict[str, np.ndarray] = {
                "pv": np.array([d["pv"] for d in aligned if d.get("pv") is not None], dtype=float),
                "sp": np.array([d["sp"] for d in aligned if d.get("sp") is not None], dtype=float),
                "op": np.array([d["op"] for d in op_rows], dtype=float),
                # 与 op 同行取 mode（可为 None：_is_auto_mode(None)=False，
                # 与引擎"仅自控模式计分子"语义一致且保证索引对齐）
                "mode": np.array([d.get("mode") for d in op_rows], dtype=object),
                "pv_quality": np.array(pv_quality_codes, dtype=int),
                "pv_quality_ts": pv_quality_ts,
            }
            signals_valid = None  # legacy：无逐信号掩码（组装即已对齐）

        ts_seconds = _ts_list_to_seconds([d["ts"] for d in aligned])
        ts_seconds = ts_seconds - (np.nanmin(ts_seconds) if len(ts_seconds) else 0.0)
        op_input = OperatorInput(
            loop_id=str(loop_id),
            signals=signals,
            timestamps=np.nan_to_num(ts_seconds, nan=0.0),
            meta={
                "sample_interval": sample_interval,
                "total_points": len(aligned),
                "loop_type": loop.loop_type,
                "pv_range": _resolve_pv_range(mappings, tags_map),
                # AD04：point 同轴模式——_run_operators 按各算子 required_signals
                # 公共有效掩码切片（信号/时间同轴）；legacy 无此标记（原行为）
                **(
                    {"point_axis": True, "signals_valid": signals_valid}
                    if is_point and signals_valid is not None
                    else {}
                ),
            },
            kpi_context=kpi_ctx,
        )

        op_results, fusions = _run_operators(
            op_input, effective_thresholds, operator_group, operators
        )
        await _report(0.9, "融合与分类")
        classification: ClassificationResult = classify(fusions, op_results, kpi_ctx, gate)
    else:
        op_results = {}
        fusions = {}
        classification = classify({}, {}, kpi_ctx, gate)

    # ---- 波形快照 + 指标汇总 + 落库 ----
    await _report(0.95, "证据快照与落库")
    chart_rows = [d for d in aligned if d.get("pv") is not None] if is_point else aligned
    charts = _build_chart_snapshots(
        chart_rows,
        pv_range=_resolve_pv_range(mappings, tags_map),
        op_range=_resolve_op_range(mappings, tags_map),
    )
    # 方案 A：诊断指标汇总（窗口 KPI 均值 + 算子特征，0~100 统一口径）
    metric_summary = _build_metric_summary(
        kpi_avgs, {name: _operator_result_to_dict(r) for name, r in op_results.items()}
    )
    has_error = any(r.error for r in op_results.values())
    finished_at = datetime.utcnow()

    run = DiagnosisRun(
        id=str(uuid4()),
        task_id=task_id,
        loop_id=str(loop_id),
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        time_window_start=start,
        time_window_end=end,
        operator_group="custom" if operators else operator_group,
        status="PARTIAL" if has_error else "SUCCESS",
        data_gate=gate.to_dict(),
        operator_results={name: _operator_result_to_dict(r) for name, r in op_results.items()},
        fusion_results={tag: f.to_dict() for tag, f in fusions.items()},
        symptom_tags={
            tag: {"detected": f.detected, "confidence": round(f.confidence, 4)}
            for tag, f in fusions.items()
        },
        primary_category=classification.primary.category if classification.primary else None,
        primary_confidence=(
            Decimal(str(round(min(0.999, classification.primary.confidence), 3)))
            if classification.primary
            else None
        ),
        secondary_categories=[j.to_dict() for j in classification.secondary],
        pending_review=[j.to_dict() for j in classification.pending_review],
        severity=classification.severity,
        rationale=classification.rationale,
        recommendations=[r.to_dict() for r in classification.recommendations],
        evidence_charts=charts,
        metric_summary=metric_summary,
        threshold_version=(
            "default" if not effective_thresholds else f"override:{len(effective_thresholds)}"
        ),
        algorithm_version=MVP_DIAG_VERSION,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((finished_at - started_at).total_seconds() * 1000),
    )
    db.add(run)
    await db.commit()
    # A3：诊断落库即时生成 SYSTEM 处置建议（失败仅记日志，不阻塞主链路）
    await generate_system_actions_best_effort(db, run)

    await _report(1.0, "完成")
    logger.info(
        "诊断完成 回路=%s 主分类=%s 置信=%.2f 状态=%s",
        loop.tag_name,
        run.primary_category,
        float(run.primary_confidence or 0),
        run.status,
    )
    return run
