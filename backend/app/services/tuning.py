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
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.tuning import TuningRecord
from app.services.preprocessing.data_quality_assessor import DataQualityAssessor
from app.services.process_model_migration import get_effective_model_params
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
    # V62-P3-005：读路径切换——优先从 process_model_version 读取 model_params
    persisted_model_params = await get_effective_model_params(db, record)
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
    # P2-009：CLIVC（可证明闭环一致 IV）已升级为生产方法
    # （IV_CAPABILITY_STATUS="CLIVC_PRODUCTION_READY"），复用 HISTORICAL_IV 枚举。
    # 早期 identify_iv/identify_iv4 实验性原型 pipeline 不再调用，故 HISTORICAL_IV
    # 现仅代表 CLIVC，按正常可信度门禁（A/B 放行、C 需确认、D/E/INCONCLUSIVE 拒绝）放行。
    # 详见契约 v2.3 §6.1。

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
        if identify_method not in {"HISTORICAL_ARX", "HISTORICAL_ARMAX", "HISTORICAL_IV"}:
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
    from app.contracts.data_types import ControlType, DataBlock, TimeWindow

    try:
        control_type = ControlType(control_type_str)
    except ValueError:
        control_type = ControlType.TEMPERATURE  # 默认温度型

    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    time_window = TimeWindow(start=start_dt, end=end_dt)

    planner = await _build_data_planner(db)

    # 请求 PVOP_HF（PV+OP, 1s）、BASE（含 SP）和 MODE_HF（MODE，从 BASE 派生）。
    # V62-P1-002: 增加 auto_mode_rate 触发 MODE_HF 派生；DataPlanner 复用 BASE 时
    # 会把所有 HF 组的 tags 合并进 BASE 查询（见 data_planner._build_query_plan），
    # 因此 BASE 查询会 SELECT mode 列，派生出的 MODE_HF block 才能携带 mode 信号。
    bundles = await planner.request_bundles(
        loop_id=loop_id,
        metrics=["valve_linearity", "error_mean", "auto_mode_rate"],
        time_window=time_window,
        control_type=control_type,
    )

    # V62-P1-001: 按 tag_group 索引收集 block，消除 bundle 迭代顺序依赖
    pvop_block: DataBlock | None = None
    base_block: DataBlock | None = None
    mode_block: DataBlock | None = None
    for bundle in bundles:
        block = bundle.data_block
        if block.tag_group == "PVOP_HF" and pvop_block is None:
            pvop_block = block
        elif block.tag_group == "BASE" and base_block is None:
            base_block = block
        elif block.tag_group == "MODE_HF" and mode_block is None:
            mode_block = block

    pv: list[float] = []
    op: list[float] = []
    sp: list[float] = []
    mode: list[int] = []
    timestamps: list[float] = []
    valid_rate = 1.0
    sampling_freq = 1.0
    resample_quality: dict[str, int] = {}
    mode_resample_quality: dict[str, int] = {}

    if pvop_block is not None:
        pvop_signals = pvop_block.signals
        pv = list(pvop_signals.get("pv", []))
        op = list(pvop_signals.get("op", []))
        pvop_ts = list(pvop_block.timestamps)
        # V62-P1-003/006: 相对秒用 _to_rel_seconds（无 naive .timestamp() 慢路径，
        # 无数组索引退化）
        if pvop_ts:
            timestamps = _to_rel_seconds(pvop_ts, pvop_ts[0])
        # 可信度统一 Phase 1：valid_rate 改用回路级口径（核心 tag 交集 / point_count），
        # 与诊断/KPI 链路口径一致；替代 PVOP 块级全 tag 交集（含 pid 等非评估信号）
        valid_rate = DataQualityAssessor.compute_loop_valid_rate(
            pvop_block.validity, pvop_block.point_count
        )
        sampling_freq = _parse_sampling_freq_hz(pvop_block.sampling_freq)

    # V62-P1-001: SP 重采样到 PVOP 网格（修复：目标网格传 PVOP timestamps，
    # 不再误传 BASE 自身 timestamps）
    if base_block is not None and pvop_block is not None and timestamps:
        sp_raw = list(base_block.signals.get("sp", []))
        ts_sp = list(base_block.timestamps)
        if sp_raw:
            sp, resample_quality = _resample_to_grid(
                sp_raw, ts_sp, dst_timestamps=pvop_block.timestamps
            )

    # V62-P1-002: MODE 零阶保持重采样到 PVOP 网格。
    # MODE 是离散状态量（AUTO/MANUAL/CASCADE），禁止线性插值（会产出 1.5 等无意义
    # 中间值）；用零阶保持：每个 PVOP 时间点取 MODE_HF 中不超过该时间的最近值。
    if mode_block is not None and pvop_block is not None and timestamps:
        mode_raw = list(mode_block.signals.get("mode", []))
        ts_mode = list(mode_block.timestamps)
        if mode_raw:
            mode, mode_resample_quality = _resample_mode_to_grid(
                mode_raw, ts_mode, dst_timestamps=pvop_block.timestamps
            )

    return {
        "pv": pv,
        "op": op,
        "sp": sp,
        "mode": mode,
        "timestamps": timestamps,
        "valid_rate": valid_rate,
        "sampling_freq": sampling_freq,
        "resample_quality": resample_quality,
        "mode_resample_quality": mode_resample_quality,
    }


def _infer_ts_from_grid(timestamps: list[float], sampling_freq: float) -> float:
    """从实际时间戳网格推导采样周期（秒），标签仅作兜底.

    Args:
        timestamps: 相对秒时序（_to_rel_seconds 输出）
        sampling_freq: DataBlock 标签解析的频率（Hz），仅在网格不可用时兜底
    """
    if len(timestamps) >= 2:
        diffs = [b - a for a, b in zip(timestamps, timestamps[1:], strict=False)]
        positive = sorted(d for d in diffs if d > 0)
        if positive:
            return positive[len(positive) // 2]
    return 1.0 / sampling_freq if sampling_freq > 0 else 1.0


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


def _to_rel_seconds(ts_list: list, t0: object) -> list[float]:
    """时间戳列表 → 相对 t0 的秒数（V62-P1-006）.

    纯 ``timedelta`` 算术，不调 naive ``.timestamp()``（macOS fork 时区慢路径，
    项目红线）；naive datetime 视为 UTC（项目惯例），aware 与 naive 混用时
    统一补 UTC。非 datetime（int/float epoch）直接转 float，不退化为数组索引。
    """
    _utc = UTC

    def _aware(t: datetime) -> datetime:
        return t if t.tzinfo is not None else t.replace(tzinfo=_utc)

    t0_aware = _aware(t0) if isinstance(t0, datetime) else t0
    out: list[float] = []
    for t in ts_list:
        if isinstance(t, datetime):
            out.append((_aware(t) - t0_aware).total_seconds())
        else:
            out.append(float(t))
    return out


def _resample_to_grid(
    values: list[float],
    src_timestamps: list,
    dst_timestamps: list,
) -> tuple[list[float], dict[str, int]]:
    """将 values 从 src_timestamps 线性插值到 dst_timestamps 目标网格.

    V62-P1-001/003/004/006:
    - 目标网格为 ``dst_timestamps``（PVOP 时间戳），不再误传 src 自身时间戳；
    - datetime → 相对秒用 ``_to_rel_seconds``（无 naive ``.timestamp()``，无数组索引退化）；
    - src 乱序时先排序（``np.interp`` 要求单调递增，覆盖 V62-P1-005 乱序场景）；
    - 返回插值/外推/缺口/有效样本质量指标（V62-P1-004）。

    Args:
        values: src 信号值
        src_timestamps: src 时间戳（datetime 或数值）
        dst_timestamps: 目标网格时间戳（通常 PVOP_HF）

    Returns:
        (重采样值列表, 质量指标 dict)。质量指标：
        ``interpolated_count``（src 范围内）、``extrapolated_count``（src 范围外，
        ``np.interp`` 用边界值）、``gap_count``（src 中 NaN/inf 缺失）、
        ``effective_samples``（src 有效样本数）。
    """
    if not values or not src_timestamps or not dst_timestamps:
        return [], {
            "interpolated_count": 0,
            "extrapolated_count": 0,
            "gap_count": 0,
            "effective_samples": 0,
        }

    t0 = dst_timestamps[0]
    src_sec = np.array(_to_rel_seconds(src_timestamps, t0), dtype=float)
    dst_sec = np.array(_to_rel_seconds(dst_timestamps, t0), dtype=float)
    values_arr = np.array(values, dtype=float)

    # 缺口：src 中 NaN/inf 视为缺失
    finite_mask = np.isfinite(values_arr)
    gap_count = int((~finite_mask).sum())

    # src 需单调递增供 np.interp；处理乱序（V62-P1-005）
    sort_idx = np.argsort(src_sec)
    src_sec_sorted = src_sec[sort_idx]
    values_sorted = values_arr[sort_idx]

    src_lo = float(src_sec_sorted[0])
    src_hi = float(src_sec_sorted[-1])
    in_range = (dst_sec >= src_lo) & (dst_sec <= src_hi)
    interpolated_count = int(in_range.sum())
    extrapolated_count = int((~in_range).sum())

    result = np.interp(
        dst_sec,
        src_sec_sorted,
        values_sorted,
        left=float(values_sorted[0]),
        right=float(values_sorted[-1]),
    )
    return result.tolist(), {
        "interpolated_count": interpolated_count,
        "extrapolated_count": extrapolated_count,
        "gap_count": gap_count,
        "effective_samples": int(finite_mask.sum()),
    }


def _resample_mode_to_grid(
    values: list[float],
    src_timestamps: list,
    dst_timestamps: list,
) -> tuple[list[int], dict[str, int]]:
    """将离散 MODE 信号零阶保持重采样到 dst 网格（V62-P1-002）.

    MODE 是离散状态量（AUTO/MANUAL/CASCADE 等），禁止线性插值——线性插值
    会产出 1.5、2.3 等无意义中间状态码。采用零阶保持（前向填充）：每个 dst
    时间点取 src 中不超过该时间的最近有效值，符合 DCS 模式保持语义。

    - dst 早于 src 首点：取 src[0]（前向外推，记 extrapolated_count）
    - dst 晚于 src 末点：取 src[-1]（后向外推，记 extrapolated_count）
    - src 中 NaN/inf 视为缺失，跳过（记 gap_count）
    - 时间戳 → 相对秒用 ``_to_rel_seconds``（无 naive ``.timestamp()``，V62-P1-006）

    Args:
        values: src MODE 值（int/float，离散状态码）
        src_timestamps: src 时间戳
        dst_timestamps: 目标网格时间戳（通常 PVOP_HF）

    Returns:
        (重采样 MODE int 列表, 质量指标 dict)。质量指标字段与
        ``_resample_to_grid`` 对齐：``interpolated_count``（src 范围内）、
        ``extrapolated_count``（src 范围外）、``gap_count``（src 缺失）、
        ``effective_samples``（src 有效样本数）。
    """
    if not values or not src_timestamps or not dst_timestamps:
        return [], {
            "interpolated_count": 0,
            "extrapolated_count": 0,
            "gap_count": 0,
            "effective_samples": 0,
        }

    t0 = dst_timestamps[0]
    src_sec = np.array(_to_rel_seconds(src_timestamps, t0), dtype=float)
    dst_sec = np.array(_to_rel_seconds(dst_timestamps, t0), dtype=float)
    values_arr = np.array(values, dtype=float)

    finite_mask = np.isfinite(values_arr)
    gap_count = int((~finite_mask).sum())
    effective_samples = int(finite_mask.sum())

    if effective_samples == 0:
        # 全部缺失：填 0 并标记为全外推（无有效源可保持）
        return [0] * len(dst_timestamps), {
            "interpolated_count": 0,
            "extrapolated_count": len(dst_timestamps),
            "gap_count": gap_count,
            "effective_samples": 0,
        }

    # 仅用有效样本做零阶保持（缺失点不参与）
    valid_src = src_sec[finite_mask]
    valid_vals = values_arr[finite_mask]
    # searchsorted(side="right") - 1：对每个 dst_sec 找 <= 它的最大 src 索引
    idx = np.searchsorted(valid_src, dst_sec, side="right") - 1
    before_mask = idx < 0  # dst 早于 src 首点 → 前向外推
    idx = np.clip(idx, 0, len(valid_vals) - 1)
    result = valid_vals[idx]
    after_mask = dst_sec > valid_src[-1]  # dst 晚于 src 末点 → 后向外推
    extrapolated_count = int(before_mask.sum() + after_mask.sum())
    interpolated_count = len(dst_timestamps) - extrapolated_count

    return [int(v) for v in result], {
        "interpolated_count": interpolated_count,
        "extrapolated_count": extrapolated_count,
        "gap_count": gap_count,
        "effective_samples": effective_samples,
    }


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
    # ts 以实际时间戳网格为准（相邻点间隔中位数）；sampling_freq 标签仅作兜底——
    # DataPlanner 查询不回聚（query_trend_data 忽略 interval_s），标签与真实网格
    # 可能不一致（如 control_type=FAST 回落 TC 后标签 5s、实际 1s），直接信标签
    # 会把 tau/theta 放大 5 倍。
    ts = _infer_ts_from_grid(signals["timestamps"], signals["sampling_freq"])

    if len(pv) < 50 or len(op) < 50:
        raise BizError(
            code="ERR_TUNING_DATA_INSUFFICIENT",
            message=f"预处理后数据不足（PV={len(pv)}, OP={len(op)} 点），至少需要 50 个有效数据点",
            status_code=400,
        )

    # 候选模型类型
    candidates = [ModelType(mt) for mt in (candidate_model_types or ["FOPDT", "SOPDT"])]

    # 调用算法栈（V62-P1-002: 传入同轴后的 MODE，供后续片段切分使用）
    result = identify_from_history(
        op=op,
        pv=pv,
        sp=sp if sp else None,
        mode=signals.get("mode") or None,
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

    V62-P1-008: 基于 segment_signals 真实切分片段（按 MODE/缺口/饱和/太短），
    对可辨识片段（exclusion_reason is None）跑激励检测，被排除片段标注原因。
    不再把整窗硬编码成单个 AUTO 片段。

    Returns:
        dict with loopId/totalSegments/segments/sufficientCount
    """
    loop = await _get_loop(db, loop_id)
    control_type_str = loop.control_type or "TC"

    signals = await _fetch_preprocessed_signals(db, loop_id, start_time, end_time, control_type_str)

    pv = signals["pv"]
    op = signals["op"]
    mode = signals.get("mode") or None

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
    from app.services.tuning_identification.segmentation import segment_signals

    # V62-P1-007/008: 真实事件切片（MODE/缺口/饱和/太短）
    specs = segment_signals(pv, op, mode)

    segments: list[dict[str, Any]] = []
    sufficient_count = 0
    for spec in specs:
        # endIdx 保持 inclusive 语义（兼容前端），SegmentSpec.end_idx 是 exclusive
        end_idx_inclusive = spec.end_idx - 1
        base = {
            "startIdx": spec.start_idx,
            "endIdx": end_idx_inclusive,
            "mode": spec.mode_label,
            "exclusionReason": spec.exclusion_reason,
            "validSampleRatio": spec.valid_sample_ratio,
            "pointCount": spec.point_count,
        }
        if spec.exclusion_reason is not None or spec.point_count < 10:
            # 被排除片段：不跑激励检测
            base.update(
                {
                    "excitationScore": None,
                    "conditionNumber": None,
                    "isSufficient": False,
                }
            )
        else:
            # 可辨识片段：跑激励检测
            seg_pv = pv[spec.start_idx : spec.end_idx]
            seg_op = op[spec.start_idx : spec.end_idx]
            u = np.array(seg_op, dtype=float)
            y = np.array(seg_pv, dtype=float)
            d = 1  # 预览用默认滞后，正式辨识由 pipeline 延迟搜索确定
            exc = check_excitation(u, y, d)
            score = excitation_score(exc.condition_number, exc.significant_changes)
            if exc.is_sufficient:
                sufficient_count += 1
            base.update(
                {
                    "excitationScore": score,
                    "conditionNumber": exc.condition_number,
                    "isSufficient": exc.is_sufficient,
                }
            )
        segments.append(base)

    return {
        "loopId": loop_id,
        "totalSegments": len(segments),
        "segments": segments,
        "sufficientCount": sufficient_count,
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
    # 边界过滤：底层趋势查询 ts <= end 右闭区间，恰好落在 end 的首行属于下一段
    # 数据（值可能完全不同），会在窗口尾部引入虚假跳变，必须排除。
    end_epoch = datetime.fromisoformat(end_time.replace("Z", "+00:00")).timestamp()
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
        if ts_sec >= end_epoch:
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
        # V62-P3-006：纯辨识记录不再用 IMC 占位，改为 IDENTIFICATION_ONLY
        algorithm="IDENTIFICATION_ONLY",
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
        # V62-P3-007：回退值 = 当前值（实施失败时恢复原参数）
        result["rollbackPid"] = dict(current_pid)

    # V62-P3-007：风险评估
    result["risk"] = _assess_tuning_risk(
        recommended_pid={"kp": pid.kp, "ti": pid.ti, "td": pid.td},
        current_pid=current_pid,
        model_params=model_params,
        confidence_level=(source_context.confidence_level if source_context else None),
    )

    # V62-P3-007：单位转换说明（首版无转换，预留结构）
    result["unitConversion"] = {
        "timeUnit": "seconds",
        "note": "PID 参数时间单位为秒；DCS 若用分钟，ti/td 需除以 60",
    }

    return result


def _assess_tuning_risk(
    *,
    recommended_pid: dict[str, Any],
    current_pid: dict[str, Any] | None,
    model_params: dict[str, Any],
    confidence_level: str | None,
) -> dict[str, Any]:
    """V62-P3-007 评估整定风险等级与因素.

    风险等级判定：
    - HIGH：PID 参数变化 > 50%，或模型可信度 D/E，或纯滞后 θ/τ > 0.5
    - MEDIUM：PID 参数变化 20%-50%，或模型可信度 C
    - LOW：PID 参数变化 < 20%，且模型可信度 A/B
    """
    factors: list[str] = []
    risk_score = 0

    # 1. PID 参数变化幅度
    if current_pid and isinstance(current_pid, dict):
        max_delta = _compute_max_pid_delta(recommended_pid=recommended_pid, current_pid=current_pid)
        if max_delta > 0.5:
            factors.append(f"PID 参数变化幅度大（{max_delta:.0%}）")
            risk_score += 3
        elif max_delta > 0.2:
            factors.append(f"PID 参数变化中等（{max_delta:.0%}）")
            risk_score += 2
        else:
            factors.append(f"PID 参数变化小（{max_delta:.0%}）")
            risk_score += 0

    # 2. 模型可信度
    conf = (confidence_level or "").upper()
    if conf in {"D", "E", "INCONCLUSIVE"}:
        factors.append(f"模型可信度低（{conf}）")
        risk_score += 4
    elif conf == "C":
        factors.append("模型可信度一般（C）")
        risk_score += 1

    # 3. 纯滞后比 θ/τ
    theta = float(model_params.get("theta") or 0)
    tau = float(model_params.get("tau") or 0)
    if tau > 0 and theta / tau > 0.5:
        factors.append(f"大滞后系统（θ/τ={theta / tau:.2f}）")
        risk_score += 2

    # 4. 过程增益极端
    k = float(model_params.get("K") or 0)
    if abs(k) > 10 or (0 < abs(k) < 0.1):
        factors.append(f"过程增益极端（K={k:.3f}）")
        risk_score += 1

    if risk_score >= 4:
        risk_level = "HIGH"
    elif risk_score >= 2:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    description = f"风险等级：{risk_level}。建议在低负荷工况下逐步实施，密切监控闭环响应。"

    return {
        "riskLevel": risk_level,
        "factors": factors,
        "description": description,
    }


def _compute_max_pid_delta(
    *, recommended_pid: dict[str, Any], current_pid: dict[str, Any]
) -> float:
    """计算推荐 PID 与当前 PID 的最大相对变化幅度."""
    max_delta = 0.0
    for key in ("kp", "ti", "td"):
        rec_val = float(recommended_pid.get(key, 0) or 0)
        cur_val = float(current_pid.get(key, 0) or 0)
        if abs(cur_val) < 1e-12:
            # 当前值为 0 时，用绝对值衡量
            delta = 1.0 if abs(rec_val) > 1e-12 else 0.0
        else:
            delta = abs(rec_val - cur_val) / abs(cur_val)
        max_delta = max(max_delta, delta)
    return max_delta


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
    # V62-P3-005：详情页优先展示 process_model_version 的 model_params
    effective_params = await get_effective_model_params(db, row[0])
    return _record_to_dict(
        row[0], row[1], include_detail=True, model_params_override=effective_params
    )


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

    # V62-P2-22：风险等级统计（risk_assessment JSON -> riskLevel GROUP BY）
    # 基于 risk_assessment->>'riskLevel'；仅统计已生成风险评估的记录。
    risk_level_col = TuningRecord.risk_assessment["riskLevel"].as_string()
    risk_result = await db.execute(
        select(risk_level_col, func.count())
        .select_from(TuningRecord)
        .where(TuningRecord.risk_assessment.isnot(None))
        .group_by(risk_level_col)
    )
    by_risk_raw: dict[str, int] = {
        str(row[0]): int(row[1]) for row in risk_result.all() if row[0] is not None
    }
    risk_high = by_risk_raw.get("HIGH", 0)
    risk_medium = by_risk_raw.get("MEDIUM", 0)
    risk_low = by_risk_raw.get("LOW", 0)
    risk_total = risk_high + risk_medium + risk_low
    risk_summary = {
        "high": risk_high,
        "medium": risk_medium,
        "low": risk_low,
        "total": risk_total,
        "calculated": risk_total > 0,
    }

    # V62-P2-22：待整定数（DRAFT/RUNNING/PENDING/IDENTIFIED 汇总，后端统一口径）
    pending_count = (
        by_status.get("DRAFT", 0)
        + by_status.get("RUNNING", 0)
        + by_status.get("PENDING", 0)
        + by_status.get("IDENTIFIED", 0)
    )

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
        "riskSummary": risk_summary,
        "pendingCount": pending_count,
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
    *,
    model_params_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """TuningRecord → dict（camelCase）。

    Args:
        model_params_override: P3-005 读路径切换——调用方预加载的有效 model_params
            （优先来自 process_model_version）。为 None 时回退到 record.model_params。
    """
    effective_params = (
        model_params_override if model_params_override is not None else record.model_params
    )
    data: dict[str, Any] = {
        "id": str(record.id),
        "loopId": str(record.loop_id),
        "tagName": tag_name,
        "modelType": record.model_type,
        "modelParams": effective_params,
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
        # V62-P3-005：模型版本引用（新记录非空，遗留记录为 NULL）
        "processModelVersionId": (
            str(record.process_model_version_id) if record.process_model_version_id else None
        ),
        # V62-P3-007：人工实施清单
        "currentPid": record.current_pid,
        "riskAssessment": record.risk_assessment,
        "rollbackPid": record.rollback_pid,
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
