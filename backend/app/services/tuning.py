"""Tuning center service (IDS v3.2 §2.5 — S7-TUNE-006).

业务逻辑：
- 模型辨识：
  - 历史数据路径（Phase 2）：DataPlanner → 8 步预处理 → tuning_identification 算法栈
  - 阶跃实验路径（保留）：get_waveform → identify_fopdt/sopdt/ipdt（兜底）
- PID 整定：基于模型参数 → 调用整定算法 → 返回推荐 PID 参数
- 闭环仿真：基于模型 + 当前/推荐 PID → 仿真对比
- 整定任务管理：CRUD + 历史统计
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.tuning import TuningRecord
from app.services.tuning_algorithms import (
    TUNING_ALGORITHM_VERSION,
    TUNING_METHODS_INFO,
    PIDParams,
    identify_fopdt,
    identify_ipdt,
    identify_sopdt,
    simulate_closed_loop,
    tune_cohen_coon,
    tune_imc,
    tune_lambda,
    tune_simc,
    tune_zn,
)
from app.services.tuning_identification import identify_from_history
from app.services.tuning_identification.types import ModelType
from app.services.waveform import get_waveform

logger = logging.getLogger(__name__)

_STEP_MIN_POINTS = 20


@dataclass(frozen=True)
class TuningModelAuthorization:
    """服务端验证后的推荐链模型上下文。

    该对象不从 HTTP 请求直接反序列化；调用方必须通过
    :func:`authorize_tuning_model` 构造，避免裸模型参数绕过来源门禁。
    """

    model_type: str
    model_params: dict[str, Any]
    loop_id: str | None
    model_source: str
    source_record_id: str | None
    risk_confirmed: bool
    confidence_level: str | None = None
    confidence_reason: str | None = None
    identify_method: str | None = None
    data_source: str | None = None


def _model_params_match(
    model_type: str,
    requested: dict[str, Any],
    persisted: dict[str, Any],
) -> bool:
    """按模型必需参数比较请求与持久化值，阻止替换辨识结果。"""
    required = {
        "FOPDT": ("K", "tau", "theta"),
        "SOPDT": ("K", "T1", "T2", "theta"),
        "IPDT": ("K", "theta"),
    }.get(model_type, ())
    if not required:
        return False

    for key in required:
        left = requested.get(key)
        right = persisted.get(key)
        if isinstance(left, bool) or isinstance(right, bool):
            return False
        try:
            left_number = float(left)
            right_number = float(right)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(left_number) and math.isfinite(right_number)):
            return False
        if not math.isclose(left_number, right_number, rel_tol=1e-9, abs_tol=1e-12):
            return False
    return True


async def authorize_tuning_model(
    *,
    db: AsyncSession,
    requested_model_type: str,
    requested_model_params: dict[str, Any],
    loop_id: str | None,
    source_record_id: str | None,
    model_source: str | None,
    risk_confirmed: bool,
    trusted_step_validation: bool = False,
) -> TuningModelAuthorization:
    """校验整定/仿真推荐链的模型来源与可信度。

    ``trusted_step_validation`` 仅供同一服务进程内、已经执行真实单阶跃
    校验的编排链使用；该字段不会暴露到 HTTP schema。
    """
    if model_source is None:
        raise BizError(
            code="ERR_TUNING_SOURCE_REQUIRED",
            message="必须明确模型来源并提供可验证凭据；旧版裸模型请求已停止放行",
            status_code=400,
        )

    if model_source == "MANUAL":
        if source_record_id is not None:
            raise BizError(
                code="ERR_TUNING_SOURCE_INVALID",
                message="人工模型不得绑定或伪装成辨识记录",
                status_code=400,
            )
        if not risk_confirmed:
            raise BizError(
                code="ERR_TUNING_RISK_CONFIRMATION_REQUIRED",
                message="人工模型必须显式确认模型与整定风险",
                status_code=400,
            )
        return TuningModelAuthorization(
            model_type=requested_model_type,
            model_params=dict(requested_model_params),
            loop_id=loop_id,
            model_source="MANUAL",
            source_record_id=None,
            risk_confirmed=True,
        )

    if model_source not in {"IDENTIFICATION_RECORD", "STEP_EXPERIMENT"}:
        raise BizError(
            code="ERR_TUNING_SOURCE_INVALID",
            message=f"不支持的模型来源: {model_source}",
            status_code=400,
        )

    if model_source == "STEP_EXPERIMENT" and trusted_step_validation:
        return TuningModelAuthorization(
            model_type=requested_model_type,
            model_params=dict(requested_model_params),
            loop_id=loop_id,
            model_source="STEP_EXPERIMENT",
            source_record_id=source_record_id,
            risk_confirmed=risk_confirmed,
        )

    if source_record_id is None:
        source_name = "阶跃实验" if model_source == "STEP_EXPERIMENT" else "历史辨识"
        raise BizError(
            code="ERR_TUNING_SOURCE_REQUIRED",
            message=f"{source_name}模型必须提供服务端可验证的 sourceRecordId",
            status_code=400,
        )

    record = await db.get(TuningRecord, source_record_id)
    if record is None:
        raise BizError(
            code="ERR_TUNING_SOURCE_NOT_FOUND",
            message="模型来源记录不存在",
            status_code=404,
        )

    if not getattr(record, "task_id", None):
        raise BizError(
            code="ERR_TUNING_SOURCE_UNVERIFIED",
            message="模型记录不是由服务端辨识链生成，不能作为推荐依据",
            status_code=422,
        )
    if str(record.status or "") not in {"IDENTIFIED", "SIMULATED", "COMPLETED"}:
        raise BizError(
            code="ERR_TUNING_SOURCE_UNVERIFIED",
            message=f"模型记录状态 {record.status or '空'} 尚未完成辨识验证",
            status_code=422,
        )

    persisted_loop_id = str(record.loop_id)
    if loop_id is not None and str(loop_id) != persisted_loop_id:
        raise BizError(
            code="ERR_TUNING_LOOP_MISMATCH",
            message="请求回路与模型来源记录的回路不一致",
            status_code=409,
        )

    persisted_model_type = str(record.model_type)
    persisted_model_params = record.model_params
    if (
        requested_model_type != persisted_model_type
        or not isinstance(persisted_model_params, dict)
        or not _model_params_match(
            persisted_model_type,
            requested_model_params,
            persisted_model_params,
        )
    ):
        raise BizError(
            code="ERR_TUNING_MODEL_MISMATCH",
            message="请求模型参数与服务端辨识记录不一致",
            status_code=409,
        )

    confidence_reason = str(record.confidence_reason or "")
    if "THETA_SOURCE=HEURISTIC_2TS" in confidence_reason.upper():
        raise BizError(
            code="ERR_TUNING_THETA_HEURISTIC_BLOCKED",
            message="纯滞后参数来自 2Ts 启发估计，不得进入推荐整定/仿真链",
            status_code=422,
        )

    identify_method = str(record.identify_method or "")
    if identify_method == "HISTORICAL_IV":
        raise BizError(
            code="ERR_TUNING_EXPERIMENTAL_METHOD",
            message="HISTORICAL_IV 仍属实验性方法，不得进入生产推荐链",
            status_code=422,
        )

    if model_source == "STEP_EXPERIMENT":
        if not (
            identify_method.startswith("STEP_")
            and str(record.data_source or "") in {"STEP_EXPERIMENT", "fallback_step"}
            and "STEP_VALIDATION_PASSED=TRUE" in confidence_reason.upper()
        ):
            raise BizError(
                code="ERR_TUNING_STEP_EVIDENCE_REQUIRED",
                message="阶跃实验缺少服务端已验证的单阶跃证据",
                status_code=422,
            )
    else:
        if identify_method.startswith("STEP_"):
            raise BizError(
                code="ERR_TUNING_SOURCE_INVALID",
                message="阶跃辨识记录必须声明 STEP_EXPERIMENT 来源",
                status_code=400,
            )
        if identify_method not in {"HISTORICAL_ARX", "HISTORICAL_ARMAX"}:
            raise BizError(
                code="ERR_TUNING_SOURCE_UNVERIFIED",
                message="历史辨识记录缺少可放行的服务端算法凭据",
                status_code=422,
            )
        if str(record.data_source or "") != "HISTORY":
            raise BizError(
                code="ERR_TUNING_SOURCE_INVALID",
                message="历史辨识记录的数据来源标记不一致",
                status_code=400,
            )
        confidence_level = str(record.confidence_level or "").upper()
        if confidence_level in {"A", "B"}:
            pass
        elif confidence_level == "C":
            if not risk_confirmed:
                raise BizError(
                    code="ERR_TUNING_RISK_CONFIRMATION_REQUIRED",
                    message="C 级辨识结果必须显式确认风险后方可进入推荐链",
                    status_code=400,
                )
        else:
            raise BizError(
                code="ERR_TUNING_CONFIDENCE_BLOCKED",
                message=(
                    f"可信度 {confidence_level or '空'} 不满足推荐链要求；"
                    "仅 A/B 或经确认的 C 级结果可用"
                ),
                status_code=422,
            )

    return TuningModelAuthorization(
        model_type=persisted_model_type,
        model_params=dict(persisted_model_params),
        loop_id=persisted_loop_id,
        model_source=model_source,
        source_record_id=str(record.id),
        risk_confirmed=risk_confirmed,
        confidence_level=str(record.confidence_level) if record.confidence_level else None,
        confidence_reason=str(record.confidence_reason) if record.confidence_reason else None,
        identify_method=str(record.identify_method) if record.identify_method else None,
        data_source=str(record.data_source) if record.data_source else None,
    )


# ---------------------------------------------------------------------------
# DataPlanner 集成（Phase 2 历史数据辨识路径）
# ---------------------------------------------------------------------------


async def _build_data_planner(db: AsyncSession):
    """构造 DataPlanner 实例（复用 kpi_calc 的工厂模式）."""
    from app.core.redis import redis_client
    from app.services.cache.l1_datablock import L1DataBlockCache
    from app.services.data_planner import DataPlanner
    from app.services.data_source.factory import get_provider
    from app.services.metric_data_bundle import MetricDataBundleAssembler

    provider = get_provider()
    query_fn = provider.make_query_fn(db)
    cache = L1DataBlockCache(redis_client)
    assembler = MetricDataBundleAssembler()
    return DataPlanner(
        cache=cache,
        tdengine_query_fn=query_fn,
        assembler=assembler,
        db=db,
    )


async def _fetch_preprocessed_signals(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    control_type_str: str,
) -> dict[str, list[float]]:
    """通过 DataPlanner 获取 8 步预处理后的 PV/OP/SP 时序.

    策略：
    - 请求 valve_linearity（PVOP_HF, 1s 采样）→ 获取 PV+OP 高频时序
    - 请求 error_mean（BASE, 按控制类型采样）→ 获取 SP 时序
    - SP 按 PVOP 时间戳线性插值对齐

    Returns:
        dict with keys: "pv", "op", "sp"（sp 可能为空 list）, "timestamps"（秒）,
        "valid_rate", "sampling_freq"（数值 Hz，已从 DataBlock 标签解析）
    """
    from app.contracts.data_types import ControlType, TimeWindow

    try:
        control_type = ControlType(control_type_str)
    except ValueError:
        control_type = ControlType.TEMPERATURE  # 默认温度型

    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    time_window = TimeWindow(start=start_dt, end=end_dt)

    planner = await _build_data_planner(db)

    # 请求 PVOP_HF（PV+OP, 1s）和 BASE（含 SP）
    bundles = await planner.request_bundles(
        loop_id=loop_id,
        metrics=["valve_linearity", "error_mean"],
        time_window=time_window,
        control_type=control_type,
    )

    pv: list[float] = []
    op: list[float] = []
    sp: list[float] = []
    timestamps: list[float] = []
    valid_rate = 1.0
    sampling_freq = 1.0

    for bundle in bundles:
        block = bundle.data_block
        signals = block.signals
        ts_list = list(block.timestamps)

        if block.tag_group == "PVOP_HF":
            # PV+OP 高频（1s）作为主时间轴
            pv = list(signals.get("pv", []))
            op = list(signals.get("op", []))
            # timestamps 是 datetime，转为相对秒
            if ts_list:
                t0 = ts_list[0]
                timestamps = [
                    (t - t0).total_seconds() if hasattr(t, "total_seconds") else float(i)
                    for i, t in enumerate(ts_list)
                ]
            valid_rate = block.quality_summary.valid_rate if block.quality_summary else 1.0
            sampling_freq = _parse_sampling_freq_hz(block.sampling_freq)
        elif block.tag_group == "BASE":
            # SP 在 BASE 中，需对齐到 PVOP 时间轴
            sp_raw = list(signals.get("sp", []))
            ts_sp = list(ts_list)
            if sp_raw and timestamps:
                sp = _resample_to_grid(sp_raw, ts_sp, ts_list_pvop=block.timestamps)
            elif sp_raw:
                sp = sp_raw  # 降级：不对齐

    return {
        "pv": pv,
        "op": op,
        "sp": sp,
        "timestamps": timestamps,
        "valid_rate": valid_rate,
        "sampling_freq": sampling_freq,
    }


def _parse_sampling_freq_hz(label: object) -> float:
    """解析 DataBlock.sampling_freq 标签为采样频率（Hz，数值）.

    DataBlock.sampling_freq 是字符串标签（如 ``"1s"`` / ``"10s"``，实际语义为
    采样周期秒数，见 data_planner ``f"{interval_s}s"``），不是数值；
    直接参与 ``> 0`` 比较或除法会抛 TypeError（P0-1 修复）。
    解析方式对齐 metric_calculator/settling_time.py 的 ``_read_sample_interval``：
    标签去 ``s`` 后转 float 得采样周期（秒），频率 = 1 / 周期；
    空标签或解析失败回退 1.0 Hz（即 1s 周期，PVOP_HF 默认）。

    Args:
        label: DataBlock.sampling_freq 原始值（通常是 str，容错任意类型）

    Returns:
        采样频率 Hz（> 0 的有限浮点数）
    """
    if not label:
        return 1.0
    s = str(label).strip().lower().replace("s", "")
    try:
        interval_s = float(s) if s else 1.0
    except ValueError:
        return 1.0
    if interval_s <= 0:
        return 1.0
    return 1.0 / interval_s


def _resample_to_grid(
    values: list[float],
    src_timestamps: list,
    ts_list_pvop: list,
) -> list[float]:
    """将 SP 从 BASE 采样率线性插值到 PVOP_HF（1s）时间轴.

    Args:
        values: SP 原始值
        src_timestamps: SP 原始时间戳（datetime）
        ts_list_pvop: PVOP_HF 时间戳（datetime），目标网格
    """
    if not values or not src_timestamps or not ts_list_pvop:
        return []

    # 转为 epoch 秒
    src_sec = np.array(
        [
            t.timestamp() if hasattr(t, "timestamp") else float(i)
            for i, t in enumerate(src_timestamps)
        ]
    )
    dst_sec = np.array(
        [t.timestamp() if hasattr(t, "timestamp") else float(i) for i, t in enumerate(ts_list_pvop)]
    )
    values_arr = np.array(values, dtype=float)

    # 用 numpy 线性插值（外推用边界值）
    result = np.interp(dst_sec, src_sec, values_arr, left=values_arr[0], right=values_arr[-1])
    return result.tolist()


async def identify_model_from_history(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    candidate_model_types: list[str] | None = None,
    theta_estimate: float | None = None,
) -> dict[str, Any]:
    """基于历史数据辨识过程对象 G_plant = PV/OP（Phase 2 主路径）.

    通过 DataPlanner 获取 8 步预处理后的 PV/OP/SP 时序，
    调用 tuning_identification 算法栈完成辨识。

    Args:
        db: 数据库会话
        loop_id: 回路 ID
        start_time: 起始时间（ISO 8601）
        end_time: 结束时间（ISO 8601）
        candidate_model_types: 候选模型类型列表，默认 ["FOPDT","SOPDT"]
        theta_estimate: 纯滞后预估值（秒），None 自动估计

    Returns:
        辨识结果 dict（含 modelType/params/fittingScore/confidenceLevel 等）

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TUNING_DATA_INSUFFICIENT
    """
    loop = await _get_loop(db, loop_id)
    control_type_str = loop.control_type or "TC"

    # 通过 DataPlanner 获取预处理后时序
    signals = await _fetch_preprocessed_signals(db, loop_id, start_time, end_time, control_type_str)

    pv = signals["pv"]
    op = signals["op"]
    sp = signals["sp"]
    ts = 1.0 / signals["sampling_freq"] if signals["sampling_freq"] > 0 else 1.0

    if len(pv) < 50 or len(op) < 50:
        raise BizError(
            code="ERR_TUNING_DATA_INSUFFICIENT",
            message=f"预处理后数据不足（PV={len(pv)}, OP={len(op)} 点），至少需要 50 个有效数据点",
            status_code=400,
        )

    # 候选模型类型
    candidates = [ModelType(mt) for mt in (candidate_model_types or ["FOPDT", "SOPDT"])]

    # 调用算法栈
    result = identify_from_history(
        op=op,
        pv=pv,
        sp=sp if sp else None,
        ts=ts,
        theta_estimate=theta_estimate,
        candidate_models=candidates,
    )

    if not result.success:
        return {
            "success": False,
            "reason": result.reason,
            "tagName": loop.tag_name,
            "algorithmVersion": result.algorithm_version,
            "dataPoints": len(pv),
            "validRate": signals["valid_rate"],
        }

    # 转换为 API 响应格式
    d = result.to_dict()
    d["tagName"] = loop.tag_name
    d["dataPoints"] = len(pv)
    d["validRate"] = signals["valid_rate"]
    d["samplingFreq"] = signals["sampling_freq"]

    # ConfidenceEvaluator 接入：数据质量可信度（基于 valid_rate）
    # 与算法内部可信度（R²+残差+激励）取较低者，确保保守评级
    data_confidence = _evaluate_data_confidence(signals["valid_rate"])
    algo_confidence = result.best_model.confidence.value if result.best_model else "E"
    final_confidence = _min_confidence(data_confidence, algo_confidence)
    d["confidenceLevel"] = final_confidence
    d["dataConfidenceLevel"] = data_confidence
    d["confidenceReason"] = (
        f"data_quality={data_confidence}(valid_rate={signals['valid_rate']:.3f}), "
        f"algorithm={algo_confidence}(R²={d.get('fittingScore', 0):.1f}%), "
        f"final={final_confidence}"
    )
    return d


def _evaluate_data_confidence(valid_rate: float) -> str:
    """通过 ConfidenceEvaluator 评估数据质量可信度.

    复用平台统一的 valid_rate → A/B/C/D/E 口径（算法说明 §3.7.2）。
    """
    from app.services.confidence_evaluator import ConfidenceEvaluator

    level = ConfidenceEvaluator.evaluate(valid_rate)
    return level.value


def _min_confidence(level_a: str, level_b: str) -> str:
    """取两个可信度等级的较低者（保守评级）.

    A > B > C > D > E > INCONCLUSIVE
    """
    order = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1, "INCONCLUSIVE": 0}
    a_rank = order.get(level_a, 0)
    b_rank = order.get(level_b, 0)
    return level_a if a_rank <= b_rank else level_b


# ---------------------------------------------------------------------------
# 可辨识片段预览（Phase 2.2）
# ---------------------------------------------------------------------------


async def preview_identify_segments(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    """预览数据窗口内的可辨识片段（只做激励检测，不执行辨识）.

    Returns:
        dict with loopId/totalSegments/segments/sufficientCount
    """
    loop = await _get_loop(db, loop_id)
    control_type_str = loop.control_type or "TC"

    signals = await _fetch_preprocessed_signals(db, loop_id, start_time, end_time, control_type_str)

    pv = signals["pv"]
    op = signals["op"]

    if len(pv) < 10 or len(op) < 10:
        return {
            "loopId": loop_id,
            "totalSegments": 0,
            "segments": [],
            "sufficientCount": 0,
        }

    import numpy as np

    from app.services.tuning_identification.excitation import (
        check_excitation,
        excitation_score,
    )

    # Phase 2 初版：将整个数据窗口作为单个片段评估
    # 后续可按 MODE 变化点切分为多片段
    u = np.array(op, dtype=float)
    y = np.array(pv, dtype=float)
    d = 1  # 默认滞后 1 步
    exc_result = check_excitation(u, y, d)

    score = excitation_score(exc_result.condition_number, exc_result.significant_changes)

    segment = {
        "startIdx": 0,
        "endIdx": len(pv) - 1,
        "mode": "AUTO",
        "excitationScore": score,
        "conditionNumber": exc_result.condition_number,
        "isSufficient": exc_result.is_sufficient,
    }

    return {
        "loopId": loop_id,
        "totalSegments": 1,
        "segments": [segment],
        "sufficientCount": 1 if exc_result.is_sufficient else 0,
    }


# ---------------------------------------------------------------------------
# 模型辨识
# ---------------------------------------------------------------------------


async def identify_model(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    model_type: str = "FOPDT",
    method: str | None = None,
) -> dict[str, Any]:
    """模型辨识。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TUNING_DATA_INSUFFICIENT
    """
    # 校验回路
    loop = await _get_loop(db, loop_id)

    # 拉取波形数据
    waveform = await get_waveform(
        db, loop_id, start_time=start_time, end_time=end_time, max_points=10000
    )

    pv_values_raw = waveform.get("pv", [])
    op_values_raw = waveform.get("op", [])
    timestamps_raw = waveform.get("timestamps", [])

    # 按同一索引过滤 PV/OP/时间，避免分别过滤后信号错位。
    pv_values: list[float] = []
    op_values: list[float] = []
    timestamps: list[float] = []  # 绝对 Unix 时间戳（秒），用于响应绘图
    point_count = min(len(pv_values_raw), len(op_values_raw), len(timestamps_raw))
    for i in range(point_count):
        try:
            pv = float(pv_values_raw[i])
            op = float(op_values_raw[i])
            ts_sec = float(timestamps_raw[i]) / 1000.0
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(pv) and np.isfinite(op) and np.isfinite(ts_sec)):
            continue
        pv_values.append(pv)
        op_values.append(op)
        timestamps.append(ts_sec)

    if len(pv_values) < _STEP_MIN_POINTS:
        raise BizError(
            code="ERR_TUNING_DATA_INSUFFICIENT",
            message=(
                f"有效 PV/OP 对齐数据不足（{len(pv_values)} 点），"
                f"至少需要 {_STEP_MIN_POINTS} 个有效数据点"
            ),
            status_code=400,
        )

    step_validation = _detect_valid_step(op_values, pv_values)
    if not step_validation["valid"]:
        raise BizError(
            code="ERR_TUNING_STEP_INVALID",
            message=step_validation["reason"],
            status_code=400,
        )

    # 以 MV 阶跃时刻为时间零点；阶跃前稳定基线保留为负时间，
    # 既供算法估算初值，又避免把窗口前置基线时长误算进 theta。
    if timestamps:
        t0 = timestamps[step_validation["step_index"]]
        timestamps_rel = [t - t0 for t in timestamps]
    else:
        timestamps_rel = []

    # 只使用验证通过的真实 MV 单阶跃；禁止以 PV 变化冒充 MV 输入。
    mv_step = step_validation["mv_step"]

    # 调用辨识算法（传入相对时间戳，算法内部期望从 0 开始）
    if model_type == "FOPDT":
        result = identify_fopdt(pv_values, timestamps_rel, mv_step, method or "TWO_POINT")
        params = {"K": result["K"], "tau": result["tau"], "theta": result["theta"]}
    elif model_type == "SOPDT":
        result = identify_sopdt(pv_values, timestamps_rel, mv_step)
        params = {
            "K": result["K"],
            "T1": result["T1"],
            "T2": result["T2"],
            "theta": result["theta"],
        }
    elif model_type == "IPDT":
        result = identify_ipdt(pv_values, timestamps_rel, mv_step)
        params = {"K": result["K"], "theta": result["theta"]}
    else:
        raise BizError(
            code="ERR_INVALID_MODEL_TYPE",
            message=f"不支持的模型类型: {model_type}",
            status_code=400,
        )

    # 构建拟合曲线响应
    fitted_curve = None
    if result.get("fitted_pv"):
        fitted_curve = {
            "timestamps": [int(t * 1000) for t in timestamps],  # 转回毫秒
            "pv": pv_values,
            "fitted": result["fitted_pv"],
        }

    response = {
        "modelType": model_type,
        "params": params,
        "fittingScore": result["fitting_score"],
        "stepValidationPassed": True,
        "stepIndex": step_validation["step_index"],
        "algorithmVersion": TUNING_ALGORITHM_VERSION,
        "dataPoints": len(pv_values),
        "fittedCurve": fitted_curve,
        "tagName": loop.tag_name,
        "mvStep": mv_step,
    }
    validation_error = validate_step_identification_result(response)
    if validation_error:
        raise BizError(
            code="ERR_TUNING_IDENTIFICATION_FAILED",
            message=validation_error,
            status_code=400,
        )
    return response


def _detect_valid_step(
    op_values: list[float | None],
    pv_values: list[float | None],
) -> dict[str, Any]:
    """验证窗口是否包含稳定基线、唯一 MV 阶跃、保持段和显著 PV 响应。"""

    aligned: list[tuple[float, float]] = []
    for op_raw, pv_raw in zip(op_values, pv_values, strict=False):
        try:
            op = float(op_raw)
            pv = float(pv_raw)
        except (TypeError, ValueError):
            continue
        if np.isfinite(op) and np.isfinite(pv):
            aligned.append((op, pv))

    if len(aligned) < _STEP_MIN_POINTS:
        return {
            "valid": False,
            "reason": f"有效 MV/PV 对齐数据不足，至少需要 {_STEP_MIN_POINTS} 点",
        }

    op = np.asarray([pair[0] for pair in aligned], dtype=float)
    pv = np.asarray([pair[1] for pair in aligned], dtype=float)
    op_diff = np.abs(np.diff(op))
    primary_diff_index = int(np.argmax(op_diff))
    step_index = primary_diff_index + 1
    primary_jump = float(op_diff[primary_diff_index])

    margin = max(5, len(op) // 10)
    if step_index < margin or len(op) - step_index < margin:
        return {"valid": False, "reason": "MV 阶跃位置过近窗口边界，缺少稳定基线或保持段"}
    if primary_jump <= np.finfo(float).eps:
        return {"valid": False, "reason": "未检测到真实 MV 阶跃"}

    pre_op = op[:step_index]
    post_op = op[step_index:]
    pre_level = float(np.median(pre_op))
    post_level = float(np.median(post_op))
    mv_step = post_level - pre_level
    abs_step = abs(mv_step)
    op_scale = max(abs(pre_level), abs(post_level), 1.0)
    if abs_step <= max(op_scale * 1e-6, np.finfo(float).eps):
        return {"valid": False, "reason": "MV 阶跃幅值不可辨识"}

    # 瞬时跳变必须解释前后平台差，排除缓慢漂移。
    if primary_jump < 0.8 * abs_step:
        return {"valid": False, "reason": "MV 仅缓慢漂移，不构成单阶跃"}

    other_jumps = np.delete(op_diff, primary_diff_index)
    second_jump = float(np.max(other_jumps)) if other_jumps.size else 0.0
    if second_jump >= 0.2 * primary_jump:
        return {"valid": False, "reason": "检测到多个显著 MV 变化，不是单阶跃窗口"}

    plateau_tolerance = max(0.1 * abs_step, op_scale * 1e-6)
    pre_spread = float(np.percentile(pre_op, 95) - np.percentile(pre_op, 5))
    post_spread = float(np.percentile(post_op, 95) - np.percentile(post_op, 5))
    if pre_spread > plateau_tolerance or post_spread > plateau_tolerance:
        return {"valid": False, "reason": "MV 阶跃前基线或阶跃后保持段不稳定"}

    pre_pv = pv[:step_index]
    post_pv = pv[step_index:]
    pre_pv_level = float(np.median(pre_pv))
    tail_count = max(5, min(len(post_pv) // 4, 20))
    post_pv_level = float(np.median(post_pv[-tail_count:]))
    pv_response = abs(post_pv_level - pre_pv_level)
    pv_scale = max(abs(pre_pv_level), abs(post_pv_level), 1.0)
    pv_floor = pv_scale * 1e-4
    pre_pv_spread = float(np.percentile(pre_pv, 95) - np.percentile(pre_pv, 5))
    if pv_response <= max(5.0 * pre_pv_spread, pv_floor):
        return {"valid": False, "reason": "MV 阶跃后未检测到显著 PV 响应"}
    if pre_pv_spread > max(0.1 * pv_response, pv_floor):
        return {"valid": False, "reason": "PV 基线不稳定，无法归因于单次 MV 阶跃"}

    return {
        "valid": True,
        "reason": None,
        "mv_step": mv_step,
        "step_index": step_index,
    }


def validate_step_identification_result(result: dict[str, Any]) -> str | None:
    """返回阶跃辨识结果的拒绝原因；验证通过返回 ``None``。"""
    if result.get("stepValidationPassed") is not True:
        return "缺少真实单阶跃验证凭据"

    model_type = result.get("modelType")
    params = result.get("params")
    if not isinstance(params, dict):
        return "阶跃辨识参数无效：params 不是对象"

    required = {
        "FOPDT": ("K", "tau", "theta"),
        "SOPDT": ("K", "T1", "T2", "theta"),
        "IPDT": ("K", "theta"),
    }.get(model_type)
    if required is None:
        return f"阶跃辨识参数无效：不支持模型 {model_type}"

    numbers: dict[str, float] = {}
    for name in required:
        value = params.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"阶跃辨识参数无效：{name} 为空或非数值"
        number = float(value)
        if not np.isfinite(number):
            return f"阶跃辨识参数无效：{name} 不是有限值"
        numbers[name] = number

    if numbers["K"] == 0:
        return "阶跃辨识参数无效：K 必须非零"
    for time_constant in ("tau", "T1", "T2"):
        if time_constant in numbers and numbers[time_constant] <= 0:
            return f"阶跃辨识参数无效：{time_constant} 必须大于 0"
    if numbers["theta"] < 0:
        return "阶跃辨识参数无效：theta 不得小于 0"

    fitting_score = result.get("fittingScore")
    if isinstance(fitting_score, bool) or not isinstance(fitting_score, (int, float)):
        return "阶跃辨识参数无效：拟合分数为空或非数值"
    if not np.isfinite(float(fitting_score)) or float(fitting_score) <= 0:
        return "阶跃辨识参数无效：拟合分数必须为正有限值"
    return None


async def persist_step_identification_record(
    *,
    db: AsyncSession,
    loop_id: str,
    result: dict[str, Any],
    created_by: str,
    requested_method: str | None = None,
) -> str:
    """持久化服务端已验证的同步阶跃辨识证据，并返回记录 ID。"""
    validation_error = validate_step_identification_result(result)
    if validation_error:
        raise BizError(
            code="ERR_TUNING_IDENTIFICATION_FAILED",
            message=validation_error,
            status_code=400,
        )

    model_type = str(result["modelType"])
    method = str(requested_method or "").upper()
    if model_type == "FOPDT":
        identify_method = "STEP_AREA" if method == "AREA" else "STEP_TWO_POINT"
    else:
        identify_method = "STEP_NLS"

    record_id = str(uuid4())
    record = TuningRecord(
        id=record_id,
        loop_id=loop_id,
        model_type=model_type,
        model_params=dict(result["params"]),
        # 技术债：algorithm 当前为 NOT NULL；辨识记录尚无独立 task kind，暂用 IMC 占位。
        algorithm="IMC",
        fitting_score=result.get("fittingScore"),
        status="IDENTIFIED",
        created_by=created_by,
        identify_method=identify_method,
        data_source="STEP_EXPERIMENT",
        confidence_reason="step_validation_passed=true",
        task_id=f"step-sync:{uuid4()}",
        completed_at=datetime.now(),
    )
    db.add(record)
    await db.commit()
    return record_id


def _estimate_mv_step(op_values: list[float | None]) -> float:
    """从 OP 数据估算阶跃幅值。"""
    valid_ops = [float(v) for v in op_values if v is not None]
    if len(valid_ops) < 2:
        return 0.0
    # 找最大变化段
    max_change = 0.0
    for i in range(1, len(valid_ops)):
        change = abs(valid_ops[i] - valid_ops[i - 1])
        if change > max_change:
            max_change = change
    # 如果整体变化范围更大，用整体范围
    total_range = abs(valid_ops[-1] - valid_ops[0])
    return max(max_change, total_range)


# ---------------------------------------------------------------------------
# PID 整定
# ---------------------------------------------------------------------------


async def tune_pid(
    model_type: str,
    model_params: dict[str, Any],
    algorithm: str,
    algorithm_params: dict[str, Any] | None = None,
    current_pid: dict[str, Any] | None = None,
    loop_id: str | None = None,
    source_context: TuningModelAuthorization | None = None,
) -> dict[str, Any]:
    """PID 整定。

    Raises:
        BizError: ERR_INVALID_ALGORITHM / ERR_MODEL_PARAMS_MISSING
    """
    if source_context is None:
        raise BizError(
            code="ERR_TUNING_SOURCE_REQUIRED",
            message="PID 整定必须使用服务端已验证的模型来源上下文",
            status_code=400,
        )

    # 防御性地只使用门禁解析后的模型参数，忽略调用方重复传入的裸参数。
    model_params = source_context.model_params

    K = float(model_params.get("K") or 0)
    tau = float(model_params.get("tau") or 0)
    theta = float(model_params.get("theta") or 0)

    if K == 0:
        raise BizError(
            code="ERR_MODEL_PARAMS_MISSING",
            message="模型参数 K（过程增益）缺失或为零",
            status_code=400,
        )

    params = algorithm_params or {}

    if algorithm == "IMC":
        pid = tune_imc(K, tau, theta, lambda_ratio=float(params.get("lambdaRatio", 1.0)))
        notes = f"IMC 整定：λ = {params.get('lambdaRatio', 1.0)} × θ"
    elif algorithm == "LAMBDA":
        pid = tune_lambda(K, tau, theta, lambda_ratio=float(params.get("lambdaRatio", 1.0)))
        notes = f"Lambda 整定：λ = {params.get('lambdaRatio', 1.0)} × τ"
    elif algorithm == "ZN":
        controller_type = str(params.get("controllerType", "PID"))
        pid = tune_zn(K, tau, theta, controller_type=controller_type)
        notes = f"Z-N 开环法：控制器类型 = {controller_type}"
    elif algorithm == "COHEN_COON":
        controller_type = str(params.get("controllerType", "PID"))
        pid = tune_cohen_coon(K, tau, theta, controller_type=controller_type)
        notes = f"Cohen-Coon 整定：控制器类型 = {controller_type}"
    elif algorithm == "SIMC":
        tau_c_ratio = float(params.get("tauCRatio", 1.0))
        pid = tune_simc(K, tau, theta, tau_c_ratio=tau_c_ratio)
        notes = f"SIMC 整定：τc = {tau_c_ratio} × θ"
    else:
        raise BizError(
            code="ERR_INVALID_ALGORITHM",
            message=f"不支持的整定算法: {algorithm}",
            status_code=400,
        )

    result = {
        "algorithm": algorithm,
        "recommendedPid": {"kp": pid.kp, "ti": pid.ti, "td": pid.td},
        "algorithmParams": params,
        "algorithmVersion": TUNING_ALGORITHM_VERSION,
        "notes": notes,
    }

    if current_pid:
        result["currentPid"] = current_pid

    return result


# ---------------------------------------------------------------------------
# 闭环仿真
# ---------------------------------------------------------------------------


async def run_simulation(
    model_type: str,
    model_params: dict[str, Any],
    current_pid: dict[str, Any],
    recommended_pid: dict[str, Any],
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
    disturbance_type: str = "step",
    pid_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """闭环仿真（Phase 2 扩展多 PID 对比）。

    Args:
        pid_candidates: 多组候选 PID（每项含 label/kp/ti/td），向后兼容。
    """
    current = PIDParams(
        kp=float(current_pid.get("kp", 0)),
        ti=float(current_pid.get("ti", 0)),
        td=float(current_pid.get("td", 0)),
    )
    recommended = PIDParams(
        kp=float(recommended_pid.get("kp", 0)),
        ti=float(recommended_pid.get("ti", 0)),
        td=float(recommended_pid.get("td", 0)),
    )

    # Phase 2：转换候选 PID 列表
    candidates_tuples: list[tuple[str, PIDParams]] | None = None
    if pid_candidates:
        candidates_tuples = []
        for c in pid_candidates:
            label = c.get("label", "candidate")
            pid = PIDParams(
                kp=float(c.get("kp", 0)),
                ti=float(c.get("ti", 0)),
                td=float(c.get("td", 0)),
            )
            candidates_tuples.append((label, pid))

    result = simulate_closed_loop(
        model_type=model_type,
        model_params=model_params,
        current_pid=current,
        recommended_pid=recommended,
        sim_duration=sim_duration,
        sim_step=sim_step,
        setpoint_step=setpoint_step,
        disturbance_type=disturbance_type,
        pid_candidates=candidates_tuples,
    )

    return result


def _simulate_multi_pid(
    model_type: str,
    model_params: dict[str, Any],
    current_pid: dict[str, Any] | None,
    pid_candidates: list[dict[str, Any]],
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
) -> dict[str, Any]:
    """多 PID 闭环仿真对比（Phase 2.3，供 Celery 任务调用）.

    与 run_simulation 的区别：current_pid 可选，pid_candidates 为必传；
    返回完整仿真结果含 candidateResponses。

    Args:
        pid_candidates: 候选 PID 列表（每项含 label/kp/ti/td/algorithm）
    """
    # 若无 current_pid，用第一个候选作为"当前"基准
    if current_pid is None and pid_candidates:
        current_pid = pid_candidates[0].get("pid", pid_candidates[0])

    # recommended_pid 取第二个候选或第一个
    recommended_pid = (
        (
            pid_candidates[1].get("pid", pid_candidates[1])
            if len(pid_candidates) > 1
            else pid_candidates[0].get("pid", pid_candidates[0])
        )
        if pid_candidates
        else {"kp": 0, "ti": 0, "td": 0}
    )

    # 构造 candidates_tuples（排除已作为 current/recommended 的项）
    candidates_tuples: list[tuple[str, PIDParams]] = []
    for c in pid_candidates:
        label = c.get("label", c.get("algorithm", "candidate"))
        pid_dict = c.get("pid", c)
        pid = PIDParams(
            kp=float(pid_dict.get("kp", 0)),
            ti=float(pid_dict.get("ti", 0)),
            td=float(pid_dict.get("td", 0)),
        )
        candidates_tuples.append((label, pid))

    return simulate_closed_loop(
        model_type=model_type,
        model_params=model_params,
        current_pid=PIDParams(
            kp=float(current_pid.get("kp", 0)),
            ti=float(current_pid.get("ti", 0)),
            td=float(current_pid.get("td", 0)),
        ),
        recommended_pid=PIDParams(
            kp=float(recommended_pid.get("kp", 0)),
            ti=float(recommended_pid.get("ti", 0)),
            td=float(recommended_pid.get("td", 0)),
        ),
        sim_duration=sim_duration,
        sim_step=sim_step,
        setpoint_step=setpoint_step,
        pid_candidates=candidates_tuples or None,
    )


# ---------------------------------------------------------------------------
# 整定任务管理
# ---------------------------------------------------------------------------


async def create_tuning_task(
    db: AsyncSession,
    loop_id: str,
    model_type: str,
    model_params: dict[str, Any],
    algorithm: str,
    recommended_pid: dict[str, Any],
    current_pid: dict[str, Any] | None = None,
    fitting_score: float | None = None,
    simulation_result: dict[str, Any] | None = None,
    status: str = "SIMULATED",
    created_by: str | None = None,
    # Phase 2.2 新增字段
    identify_method: str | None = None,
    data_source: str | None = None,
    confidence_level: str | None = None,
    confidence_reason: str | None = None,
    excitation_score: float | None = None,
    residual_test_passed: bool | None = None,
    pid_candidates: dict[str, Any] | None = None,
    candidate_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """创建整定任务记录。"""
    # 校验回路
    loop = await _get_loop(db, loop_id)

    record = TuningRecord(
        id=str(uuid4()),
        loop_id=loop_id,
        model_type=model_type,
        model_params=model_params,
        algorithm=algorithm,
        recommended_pid=recommended_pid,
        simulation_result=simulation_result,
        fitting_score=fitting_score,
        status=status,
        created_by=created_by,
        # Phase 2.2 元数据
        identify_method=identify_method,
        data_source=data_source,
        confidence_level=confidence_level,
        confidence_reason=confidence_reason,
        excitation_score=excitation_score,
        residual_test_passed=residual_test_passed,
        pid_candidates=pid_candidates,
        candidate_results=candidate_results,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return _record_to_dict(record, loop.tag_name)


async def list_tuning_tasks(
    db: AsyncSession,
    *,
    loop_id: str | None = None,
    algorithm: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """查询整定任务列表（分页）。"""
    query = select(TuningRecord, LoopLedger.tag_name).outerjoin(
        LoopLedger, TuningRecord.loop_id == LoopLedger.id
    )

    if loop_id:
        query = query.where(TuningRecord.loop_id == loop_id)
    if algorithm:
        query = query.where(TuningRecord.algorithm == algorithm)
    if status:
        query = query.where(TuningRecord.status == status)

    # 总数
    count_query = select(func.count()).select_from(TuningRecord)
    if loop_id:
        count_query = count_query.where(TuningRecord.loop_id == loop_id)
    if algorithm:
        count_query = count_query.where(TuningRecord.algorithm == algorithm)
    if status:
        count_query = count_query.where(TuningRecord.status == status)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # 分页
    query = query.order_by(TuningRecord.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    items = [_record_to_dict(r[0], r[1]) for r in rows]

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


async def get_tuning_task_detail(db: AsyncSession, task_id: str) -> dict[str, Any]:
    """获取整定任务详情。"""
    result = await db.execute(
        select(TuningRecord, LoopLedger.tag_name)
        .outerjoin(LoopLedger, TuningRecord.loop_id == LoopLedger.id)
        .where(TuningRecord.id == task_id)
    )
    row = result.first()
    if row is None:
        raise BizError(
            code="ERR_TUNING_TASK_NOT_FOUND",
            message="整定任务不存在",
            status_code=404,
        )
    return _record_to_dict(row[0], row[1], include_detail=True)


async def get_tuning_history_stats(db: AsyncSession) -> dict[str, Any]:
    """整定历史统计。"""
    # 总数
    total_result = await db.execute(select(func.count()).select_from(TuningRecord))
    total = total_result.scalar() or 0

    # 按算法分组
    algo_result = await db.execute(
        select(TuningRecord.algorithm, func.count()).group_by(TuningRecord.algorithm)
    )
    by_algorithm = {row[0]: row[1] for row in algo_result.all()}

    # 按状态分组
    status_result = await db.execute(
        select(TuningRecord.status, func.count()).group_by(TuningRecord.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    # 平均拟合度
    avg_result = await db.execute(
        select(func.avg(TuningRecord.fitting_score)).where(TuningRecord.fitting_score.isnot(None))
    )
    avg_fitting = avg_result.scalar()
    avg_fitting_score = round(float(avg_fitting), 2) if avg_fitting else None

    # 最近 10 条任务
    recent_result = await db.execute(
        select(TuningRecord, LoopLedger.tag_name)
        .outerjoin(LoopLedger, TuningRecord.loop_id == LoopLedger.id)
        .order_by(TuningRecord.created_at.desc())
        .limit(10)
    )
    recent_tasks = [_record_to_dict(r[0], r[1]) for r in recent_result.all()]

    return {
        "totalTasks": total,
        "byAlgorithm": by_algorithm,
        "byStatus": by_status,
        "avgFittingScore": avg_fitting_score,
        "recentTasks": recent_tasks,
    }


def get_tuning_methods() -> list[dict[str, Any]]:
    """获取整定方法信息。"""
    return TUNING_METHODS_INFO


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


async def _get_loop(db: AsyncSession, loop_id: str) -> LoopLedger:
    """获取回路，不存在则抛错。"""
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )
    return loop


def _record_to_dict(
    record: TuningRecord,
    tag_name: str | None = None,
    include_detail: bool = False,
) -> dict[str, Any]:
    """TuningRecord → dict（camelCase）。"""
    data: dict[str, Any] = {
        "id": str(record.id),
        "loopId": str(record.loop_id),
        "tagName": tag_name,
        "modelType": record.model_type,
        "modelParams": record.model_params,
        "algorithm": record.algorithm,
        "recommendedPid": record.recommended_pid,
        "fittingScore": float(record.fitting_score) if record.fitting_score else None,
        "status": record.status,
        "createdBy": record.created_by,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
        # Phase 2.2 新增字段（可选）
        "identifyMethod": record.identify_method,
        "dataSource": record.data_source,
        "confidenceLevel": record.confidence_level,
        "confidenceReason": record.confidence_reason,
        "excitationScore": float(record.excitation_score) if record.excitation_score else None,
        "residualTestPassed": record.residual_test_passed,
        "taskId": record.task_id,
        "completedAt": record.completed_at.isoformat() if record.completed_at else None,
    }
    if include_detail:
        data["simulationResult"] = record.simulation_result
        data["pidCandidates"] = record.pid_candidates
        data["candidateResults"] = record.candidate_results
    return data


__all__ = [
    "identify_model",
    "identify_model_from_history",
    "persist_step_identification_record",
    "authorize_tuning_model",
    "preview_identify_segments",
    "tune_pid",
    "run_simulation",
    "_simulate_multi_pid",
    "create_tuning_task",
    "list_tuning_tasks",
    "get_tuning_task_detail",
    "get_tuning_history_stats",
    "get_tuning_methods",
]
