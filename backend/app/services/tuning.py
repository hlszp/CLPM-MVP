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
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.loop import LoopLedger
from app.models.tuning import TuningRecord
from app.services.preprocessing.data_quality_assessor import DataQualityAssessor
from app.services.process_model_migration import get_effective_model_params
from app.services.tuning_algorithms import (
    ROBUST_SET_MAX_SIM_DURATION,
    SIM_BUDGET_SYNC,
    SIMULATION_ENGINE_VERSION,
    TUNING_ALGORITHM_VERSION,
    TUNING_METHODS_INFO,
    PIDParams,
    SimulationBudgetError,
    SimulationError,
    SimulationNotSupportedError,
    SimWorkBudget,
    build_trustworthy_set,
    get_method_info,
    identify_fopdt,
    identify_ipdt,
    identify_sopdt,
    simulate_closed_loop,
    tune_cohen_coon,
    tune_imc,
    tune_ipdt_pi,
    tune_lambda,
    tune_simc,
    tune_sopdt_imc,
    tune_zn,
    validate_tuning_params,
    verify_robustness_on_set,
)
from app.services.tuning_identification import identify_from_history
from app.services.tuning_identification.types import ModelType
from app.services.waveform import get_waveform

logger = logging.getLogger(__name__)

_STEP_MIN_POINTS = 20

#: P3-07：可整定模型结构的必需参数表（与 _model_params_match 同口径）。
#: FOPDT=五法（§6.3~§6.7）；IPDT=IPDT_PI（R12）；SOPDT=SOPDT_IMC（R13）
_TUNING_MODEL_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "FOPDT": ("K", "tau", "theta"),
    "IPDT": ("K", "theta"),
    "SOPDT": ("K", "T1", "T2", "theta"),
}


@dataclass(frozen=True)
class TuningModelAuthorization:
    """服务端验证后的推荐链模型上下文。

    该对象不从 HTTP 请求直接反序列化；调用方必须通过
    :func:`authorize_tuning_model` 构造，避免裸模型参数绕过来源门禁。

    P3-07 加性字段：``model_version_id`` / ``snapshot_id`` /
    ``final_verification``（来源记录的证据快照摘要——模型级终验结论；
    读取失败或旧记录无终验装配时为 None，显式未知不伪补）。
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
    # P3-07：来源证据摘要（从来源辨识记录的输入包 manifest 读取）
    model_version_id: str | None = None
    snapshot_id: str | None = None
    final_verification: dict[str, Any] | None = None


def _read_identification_manifest(record: Any) -> dict[str, Any] | None:
    """读取来源辨识记录的输入包 manifest（只读证据，不触发重放契约）.

    P3-07：终验结论/重选不确定性从辨识输入包（P2-03 不可变包）的
    manifest.resultEvidence 读取。读包只取证据摘要，不走
    ``load_snapshot_for_replay``（重放契约含保留期/构建 digest 校验，
    属复现场景语义，证据读取不应因代码迭代拒绝）。文件缺失/损坏 →
    None（显式未知，不伪补）。
    """
    snap_id = getattr(record, "dataset_snapshot_id", None)
    if not snap_id:
        return None
    try:
        import json

        from app.services.calc_snapshot import snapshot_file_paths

        for path in snapshot_file_paths(str(snap_id)):
            if path.name.endswith(".manifest.json") and path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.debug("辨识输入包 manifest 读取失败（终验/不确定性证据按未知）", exc_info=True)
    return None


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

    # P3-07：来源证据摘要（终验结论等；读取失败按未知 None 不伪补）
    _manifest = _read_identification_manifest(record)
    _result_evidence = (_manifest or {}).get("resultEvidence") or {}
    _final_verification = _result_evidence.get("finalVerification")
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
        model_version_id=(
            str(_pmv) if (_pmv := getattr(record, "process_model_version_id", None)) else None
        ),
        snapshot_id=str(_dsn) if (_dsn := getattr(record, "dataset_snapshot_id", None)) else None,
        final_verification=_final_verification if isinstance(_final_verification, dict) else None,
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
        #
        # S3 修复（G23 续·整定链）：再折入**时间覆盖率**，与 KPI/诊断两链同口径。
        # R14-2（2026-09-06）为"有效点比例 != 时间覆盖率"给 Pipeline 增加了
        # time_coverage 因子，此前只落在 KPI 链；本轮起三链一致。
        # expected_interval_s 必须是**契约**采样间隔（compute_time_coverage 文档：
        # 用块级实际 sampling_freq 会把稀疏数据洗白成 coverage=100%），
        # 故用 control_type 反查阈值而非 pvop_block.sampling_freq。
        from app.services.preprocessing.quality_summary import compute_time_coverage
        from app.services.preprocessing.thresholds import get_threshold

        raw_loop_valid_rate = DataQualityAssessor.compute_loop_valid_rate(
            pvop_block.validity, pvop_block.point_count
        )
        time_coverage = compute_time_coverage(
            list(pvop_ts),
            expected_interval_s=float(get_threshold(control_type).base_sampling_freq),
        )
        valid_rate = raw_loop_valid_rate * time_coverage
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

    # ---- AD05（I01/I02）：point 逻辑宽表路径——同轴直通 + 有效连续段 ----
    # point 数据已是 1s 对齐网格：SP/MODE **不再二次重采样**（重采样会把
    # 未知保持/外推成合法值，I02）；辨识输入按逐点有效性选取实际连续片段，
    # PV/OP/SP/MODE/时间**同一索引范围**切片（I01），不拼接两段。
    point_ctx = getattr(pvop_block, "series_context", None) if pvop_block is not None else None
    if point_ctx is not None and getattr(point_ctx, "is_point", False):
        return _point_axis_signals(pvop_block, base_block, mode_block)

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


def _point_axis_signals(
    pvop_block,
    base_block,
    mode_block,
) -> dict[str, Any]:
    """point 路径取数桥接（AD05）：同轴直通 + 最长有效连续段选取.

    规则（补充方案 §4.3 整定链）：
    - PV/OP 取 PVOP_HF；SP/MODE 取 BASE/MODE_HF（point 各组共享同一 1s
      网格，直接复制，不插值/不零阶保持）；
    - 逐槽有效性 = 核心角色（pv∧op∧sp[有则]∧mode[有则]）validity 交集 ∧
      数值有限（无效有限值不进辨识，I01）；
    - 选**最长连续有效段**（沿用既有最长有效段规则；硬缺口/坏质量/未知
      切断段，不跨段拼接）；全窗有效率与选段有效率分开记录，对外可信度
      口径=全窗有效率（不以选段 100% 覆盖原窗口，I01）；
    - MODE 未知（None）切断段（不得当合法手动状态）；
    - 无可用段 → 返回空序列（上层既有 ERR_TUNING_DATA_INSUFFICIENT 路径）。
    """
    pv = list(pvop_block.signals.get("pv", []))
    op = list(pvop_block.signals.get("op", []))
    n = min(len(pv), len(op), len(pvop_block.timestamps))

    # 同轴判定：BASE 与 PVOP 时间戳一致（point 网格共享；不一致则该窗口
    # 不是纯 point 数据——保守按 legacy 已处理结果返回空由上层判定不足）
    sp: list[Any] = []
    mode: list[Any] = []
    if base_block is not None and len(base_block.timestamps) == n:
        sp = list(base_block.signals.get("sp", []))
    if mode_block is not None and len(mode_block.timestamps) == n:
        mode = list(mode_block.signals.get("mode", []))

    # 逐槽有效性：PVOP validity（pv/op）∧ BASE validity（sp/mode 有则）
    def _valid_arr(block, key):
        if block is None:
            return None
        arr = block.validity.get(key)
        return arr if arr is not None and len(arr) >= n else None

    pv_v = _valid_arr(pvop_block, "pv_valid")
    op_v = _valid_arr(pvop_block, "op_valid")
    sp_v = _valid_arr(base_block, "sp_valid") if sp else None
    mode_v = _valid_arr(mode_block, "mode_valid") if mode else None

    ok = [False] * n
    for i in range(n):
        pv_val, op_val = pv[i], op[i]
        if pv_val is None or op_val is None:
            continue
        if pv_v is not None and not pv_v[i]:
            continue
        if op_v is not None and not op_v[i]:
            continue
        if sp:
            sv = sp[i] if i < len(sp) else None
            if sv is None or (sp_v is not None and not sp_v[i]):
                continue
        if mode:
            mv = mode[i] if i < len(mode) else None
            if mv is None or (mode_v is not None and not mode_v[i]):
                continue
        ok[i] = True

    # 最长连续有效段（等间隔 1s；不拼接）
    best_start, best_len = 0, 0
    i = 0
    while i < n:
        if ok[i]:
            j = i
            while j < n and ok[j]:
                j += 1
            if j - i > best_len:
                best_start, best_len = i, j - i
            i = j
        else:
            i += 1

    full_valid_count = sum(1 for v in ok if v)
    full_valid_rate = (full_valid_count / n) if n else 0.0
    segment_rate = (best_len / n) if n else 0.0

    ts_full = _to_rel_seconds(list(pvop_block.timestamps), pvop_block.timestamps[0]) if n else []
    ctx = pvop_block.series_context
    seg = {
        "pv": pv[best_start : best_start + best_len],
        "op": op[best_start : best_start + best_len],
        "sp": ([float(v) for v in sp[best_start : best_start + best_len]] if sp else []),
        "mode": ([int(v) for v in mode[best_start : best_start + best_len]] if mode else []),
        "timestamps": ts_full[best_start : best_start + best_len],
        # 对外可信度口径=全窗有效率（选段比例另行记录，不覆盖原窗口）
        "valid_rate": full_valid_rate,
        "sampling_freq": 1.0,
        "resample_quality": {},
        "mode_resample_quality": {},
        # AD05 证据字段（响应/日志可审计；不改变既有消费键）
        "point_axis": {
            "full_window_valid_rate": full_valid_rate,
            "segment_valid_rate": segment_rate,
            "segment_index_start": best_start,
            "segment_length": best_len,
            "grid_slots": n,
            "unknown_reasons": dict(ctx.unknown_reasons) if ctx is not None else {},
            "source_coverage_ratio": (ctx.source_coverage_ratio("pv") if ctx is not None else None),
            "interpretation_consistent": (
                ctx.interpretation_consistent if ctx is not None else None
            ),
        },
    }
    return seg


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


def _dcs_template_revision(loop) -> str:
    """P2-03：DCS 模板 revision（冻结口径：绑定 dcs_model 时 "dcs_model:{id}"）.

    未绑定 DCS 型号 → 显式 ``UNKNOWN_DCS_TEMPLATE``（P2-04/P3-03 前不伪补
    标准 kp/ti/td 表示；07 号文 §3.3 红线）。
    """
    dcs_model_id = getattr(loop, "dcs_model_id", None)
    if dcs_model_id:
        return f"dcs_model:{dcs_model_id}"
    return "UNKNOWN_DCS_TEMPLATE"


async def identify_model_from_history(
    db: AsyncSession,
    loop_id: str,
    start_time: str,
    end_time: str,
    candidate_model_types: list[str] | None = None,
    theta_estimate: float | None = None,
    tuning_record_id: str | None = None,
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
        tuning_record_id: P2-03 输入包创建引用（tuning_record.id；任务层
            先建占位记录再辨识，包与记录互链；None=无宿主记录不落包）

    Returns:
        辨识结果 dict（含 modelType/params/fittingScore/confidenceLevel 等；
        P2-03 起附 ``inputSnapshot``={snapshotId,inputHash,status} 或显式
        SAVE_FAILED 状态——保存失败不阻断辨识，但不可主张完整复现）

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TUNING_DATA_INSUFFICIENT
    """
    loop = await _get_loop(db, loop_id)
    control_type_str = loop.control_type or "TC"

    # 通过 DataPlanner 获取预处理后时序
    signals = await _fetch_preprocessed_signals(db, loop_id, start_time, end_time, control_type_str)

    # AD06/I07：跨解释配置边界（改绑/量程单位候选变化）的窗口——无依据用
    # 当前量程解释旧值，动态辨识拒绝（沿用既有数据不足出口，原因显式）
    _pctx = signals.get("point_axis") or {}
    if _pctx and _pctx.get("interpretation_consistent") is False:
        raise BizError(
            code="ERR_TUNING_DATA_INSUFFICIENT",
            message=(
                "窗口跨改绑/解释配置边界（point 上下文 interpretation_consistent="
                "False），不能用当前量程/单位解释旧值；请按边界分段发起辨识"
            ),
            status_code=400,
        )

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
    # v1.2：IPDT 纳入默认候选（液位等积分过程；用户裁决 2026-10-10）
    candidates = [ModelType(mt) for mt in (candidate_model_types or ["FOPDT", "SOPDT", "IPDT"])]

    # 调用算法栈
    # P2-020（2026-10-04）：mode 重新传入——G24 裁决移除的是"文档称用实际
    # 未用"的死参数；现用于自动选段的切分依据（MODE/缺口/饱和边界 + 激励
    # 评分选优）。语义与 G24 兼容：不作辨识门控（手动段降级使用而非拒绝），
    # mode=None 时算法栈不切分、假设全 AUTO。op_limits：生产链 OP 已归一化
    # 0~100（V62-P1-009），饱和检测按此量程；算法栈自身不假设量纲。
    result = identify_from_history(
        op=op,
        pv=pv,
        sp=sp if sp else None,
        ts=ts,
        theta_estimate=theta_estimate,
        candidate_models=candidates,
        mode=signals.get("mode") or None,
        op_limits=(0.0, 100.0),
    )

    # P3-03 B1-2：回路工程事实（单位域换算与落包共用；解析失败不阻断主链，
    # unitBasis/工程换算按显式未知处理，不伪补）
    try:
        from app.services.engineering_facts import resolve_loop_engineering_facts

        _facts = await resolve_loop_engineering_facts(db, str(loop.id))
    except Exception:  # noqa: BLE001
        logger.warning("辨识工程事实解析失败（unitBasis 工程换算按未知，不伪补）", exc_info=True)
        _facts = None
    unit_basis = _history_unit_basis(_facts, result)

    # P2-03（DEC-10 类别②：整定/辨识任务落包）：保存完整分析输入不可变包
    # （不只胜出片段；预处理参数结构化随包）。失败不阻断辨识主链，结果显式
    # 标 SAVE_FAILED——不可主张完整复现（诚实化原则）。
    input_snapshot: dict[str, Any] | None = None
    if tuning_record_id is not None:
        try:
            from app.services import calc_snapshot as _cs

            # engineeringFacts 已在上方解析（_facts），此处直接复用
            snap = await _cs.save_identification_snapshot(
                db,
                loop_id=str(loop.id),
                ts_start=datetime.fromisoformat(start_time.replace("Z", "+00:00")).replace(
                    tzinfo=None
                ),
                ts_end=datetime.fromisoformat(end_time.replace("Z", "+00:00")).replace(tzinfo=None),
                timestamps_rel=signals["timestamps"],
                op=op,
                pv=pv,
                sp=sp if sp else None,
                mode=signals.get("mode") or None,
                sampling_period_seconds=ts,
                theta_estimate=theta_estimate,
                candidate_model_types=[mt.value for mt in candidates],
                result_dict=result.to_dict(),
                tuning_record_id=tuning_record_id,
                dcs_template_revision=_dcs_template_revision(loop),
                valid_rate=signals.get("valid_rate"),
                point_ctx=signals.get("point_axis"),
                engineering_facts=_facts,
            )
            if snap is not None:
                input_snapshot = {
                    "snapshotId": str(snap.id),
                    "inputHash": snap.input_hash,
                    "status": "SAVED",
                }
        except Exception:  # noqa: BLE001
            input_snapshot = {"status": "SAVE_FAILED"}
            logger.warning(
                "辨识输入包保存失败（loop=%s 窗口 %s~%s），结果不可主张完整复现",
                loop.tag_name,
                start_time,
                end_time,
                exc_info=True,
            )

    if not result.success:
        return {
            "success": False,
            "reason": result.reason,
            "tagName": loop.tag_name,
            "algorithmVersion": result.algorithm_version,
            "dataPoints": len(pv),
            "validRate": signals["valid_rate"],
            "inputSnapshot": input_snapshot,
            "dcsTemplateRevision": _dcs_template_revision(loop),
            "unitBasis": unit_basis,
        }

    # 转换为 API 响应格式
    d = result.to_dict()
    d["tagName"] = loop.tag_name
    d["dataPoints"] = len(pv)
    d["validRate"] = signals["valid_rate"]
    d["samplingFreq"] = signals["sampling_freq"]
    # T2（2026-10-01）：任务层版本表读 samplingPeriod（原恒 None）；
    # 周期 = 1/采样频率（Hz）
    _freq = signals["sampling_freq"]
    d["samplingPeriod"] = round(1.0 / _freq, 4) if _freq else None
    # P3-03 B1-2：K 单位域声明与工程域换算（显式层，见 tuning_units）
    d["unitBasis"] = unit_basis

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
    d["inputSnapshot"] = input_snapshot
    d["dcsTemplateRevision"] = _dcs_template_revision(loop)
    return d


def _history_unit_basis(
    facts: dict[str, Any] | None,
    result: Any,
) -> dict[str, Any]:
    """历史辨识链 K 单位域声明（P3-03 B1-2）.

    历史链输入经 planner 预处理 Step③ 量程归一化（PV/OP 各自 tag 量程
    → 0~100），K 量纲恒为 %PV/%OP；工程域换算只在 PV/OP tag 量程均已
    核实（TAG_RANGE）时给出，默认 0~100 假设不冒充工程换算（诚实未知）。
    """
    params = None
    k_value = None
    if getattr(result, "success", False) and result.best_model is not None:
        params = result.best_model.params.to_dict()
        k_value = params.get("K")
    return _normalized_k_basis(facts, k_value)


def _normalized_k_basis(facts: dict[str, Any] | None, k_value: Any) -> dict[str, Any]:
    """历史链 K（归一化域）单位域声明与工程域换算（``_history_unit_basis`` 的 K 级核心）.

    P3-B2 W5 抽出：保存链冻结 unitBasis 与辨识链共用同一换算口径
    （tuning_units 唯一仿射入口，不分叉第二套实现）。
    """
    from app.services import tuning_units as tu

    pv_span, op_span = tu.spans_from_engineering_facts(facts)
    block = tu.unit_basis_block(
        tu.BASIS_NORMALIZED,
        pv_span=pv_span,
        op_span=op_span,
        note=(
            "历史辨识链：预处理 Step③ 量程归一化域（PV/OP 各自 tag 量程→0~100），"
            "K 量纲为 %PV/%OP；与阶跃实验链（TAG_STORED 域）比较须经换算层"
        ),
        k_source=k_value,
    )
    if isinstance(k_value, (int, float)) and not isinstance(k_value, bool):
        if pv_span.source == "TAG_RANGE" and op_span.source == "TAG_RANGE":
            try:
                block["kConverted"] = tu.convert_gain(
                    float(k_value),
                    tu.normalized_span(),
                    tu.normalized_span(),
                    pv_span,
                    op_span,
                )
                block["convertedBasis"] = tu.BASIS_ENGINEERING
            except tu.UnknownConversion:
                block["kConvertedUnknownReason"] = "量程事实非法，工程域换算不可执行"
        else:
            block["kConvertedUnknownReason"] = (
                "PV/OP tag 量程未全部核实（默认 0~100 假设），工程域 K 换算不可执行"
            )
    return block


def _step_unit_basis(facts: dict[str, Any] | None, k_value: float | None) -> dict[str, Any]:
    """阶跃实验链 K 单位域声明（P3-03 B1-2）.

    阶跃链经波形 API 取 TDengine 点存储**原值域**（与 planner 归一化前
    同域），K 量纲随存储域；归一化域换算只在 tag 量程已核实时给出。
    """
    from app.services import tuning_units as tu

    pv_span, op_span = tu.spans_from_engineering_facts(facts)
    block = tu.unit_basis_block(
        tu.BASIS_TAG_STORED,
        pv_span=pv_span,
        op_span=op_span,
        note=(
            "阶跃实验链：波形 API 返回点存储原值域（未经 Step③ 归一化），"
            "K 量纲为存储域 PV/存储域 OP；与历史辨识链（NORMALIZED_0_100 域）"
            "比较须经换算层"
        ),
        k_source=k_value,
    )
    if isinstance(k_value, (int, float)) and not isinstance(k_value, bool):
        if pv_span.source == "TAG_RANGE" and op_span.source == "TAG_RANGE":
            try:
                block["kConverted"] = tu.convert_gain(
                    float(k_value),
                    pv_span,
                    op_span,
                    tu.normalized_span(),
                    tu.normalized_span(),
                )
                block["convertedBasis"] = tu.BASIS_NORMALIZED
            except tu.UnknownConversion:
                block["kConvertedUnknownReason"] = "量程事实非法，归一化域换算不可执行"
        else:
            block["kConvertedUnknownReason"] = (
                "PV/OP tag 量程未全部核实，归一化域 K 换算不可执行（存储域身份未验证）"
            )
    return block


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
            # V62-P1-009 接线（2026-10-01）：OP 已归一化 0~100，走量程归一化
            exc = check_excitation(u, y, d, op_span=100.0)
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

    # P3-03 B1-2：工程事实（单位域换算；解析失败不阻断，按未知处理）
    try:
        from app.services.engineering_facts import resolve_loop_engineering_facts

        _facts = await resolve_loop_engineering_facts(db, str(loop.id))
    except Exception:  # noqa: BLE001
        logger.warning("阶跃辨识工程事实解析失败（unitBasis 换算按未知）", exc_info=True)
        _facts = None

    # P3-03 B1-6：最佳解释模型与可整定模型显式区分（阶跃链辨识单一请求
    # 结构：解释模型=本模型；P3-07 起 IPDT/SOPDT 专用整定方法已注册
    # （IPDT_PI/SOPDT_IMC），但其整定前置=模型级终验 PASSED——阶跃链不
    # 运行终验装配，故可整定角色仍按 FOPDT 口径输出（放开归验收后裁决）
    tunable = model_type == "FOPDT"
    model_role = {
        "bestExplanatory": {"modelType": model_type, "fittingScore": result["fitting_score"]},
        "tunable": (
            {"modelType": model_type, "fittingScore": result["fitting_score"], "isBestModel": True}
            if tunable
            else None
        ),
        "note": (
            "FOPDT 为当前唯一可整定结构（可整定资格仍须经推荐链可信度门禁）"
            if tunable
            else (
                f"{model_type} 不可整定（可整定角色=终验 PASSED+结构支持；"
                "P3-07 已注册 IPDT_PI/SOPDT_IMC 专用方法，但其前置为模型级终验"
                " PASSED——阶跃链不运行终验装配，请走历史辨识链获取终验结论）"
            )
        ),
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
        # P3-03 B1-2/B1-6：K 单位域声明与模型角色（字段级，加性键）
        "unitBasis": _step_unit_basis(_facts, params.get("K")),
        "modelRole": model_role,
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
    # 同上：模型类型同样以门禁解析结果为准，避免调用方用 model_type 与
    # source_context 不一致来绕过下面的模型适用性校验。
    model_type = source_context.model_type

    # 模型适用性：注册表驱动（P3-07）。S3 修复（G21）的 fail-closed 语义
    # 保留——五法（IMC/Lambda/Z-N/Cohen-Coon/SIMC）仍仅适用于 FOPDT
    # （SOPDT 参数契约无 tau，静默套 FOPDT 公式会放大积分作用 ~27 倍）；
    # P3-07 新增 IPDT_PI/SOPDT_IMC 专用方法（R12/R13），未注册组合明确拒绝。
    model_type_u = str(model_type).upper()
    if model_type_u not in _TUNING_MODEL_REQUIRED_PARAMS:
        raise BizError(
            code="ERR_MODEL_TYPE_NOT_TUNABLE",
            message=(
                f"不支持的模型类型 {model_type}（可整定结构："
                f"{'/'.join(_TUNING_MODEL_REQUIRED_PARAMS)}，未支持结构明确拒绝）"
            ),
            status_code=400,
        )
    _method_info = get_method_info(algorithm)
    if _method_info:
        _applicable = {m["applicableModel"] for m in _method_info}
        if model_type_u not in _applicable:
            _hint = {
                "IPDT": "IPDT 请使用专用方法 IPDT_PI（R12 Skogestad IMC 积分规则族）",
                "SOPDT": "SOPDT 请使用专用方法 SOPDT_IMC（R13 二阶专用 IMC-PID 解析）",
            }.get(model_type_u, "")
            raise BizError(
                code="ERR_MODEL_TYPE_NOT_TUNABLE",
                message=(
                    f"整定算法 {algorithm} 仅适用于 {'/'.join(sorted(_applicable))} 模型"
                    f"（算法说明 §6.3~§6.7 以 FOPDT 为前提），当前模型类型 {model_type}"
                    + (f"；{_hint}" if _hint else "")
                ),
                status_code=400,
            )

    # P3-07：IPDT/SOPDT 专用整定的终验前置（03 号文验收链
    # "候选模型→独立终验→整定"；O-D6 未终验模型明确拒绝）。
    # FOPDT 五法不追加该前置（P3-03 终验收口为 modelRole 展示级语义，
    # 本批不改既有 FOPDT 推荐链行为）。
    if model_type_u != "FOPDT":
        _fv_status = (
            source_context.final_verification.get("status")
            if isinstance(source_context.final_verification, dict)
            else None
        )
        if _fv_status is None:
            raise BizError(
                code="ERR_TUNING_FINAL_VERIFICATION_REQUIRED",
                message=(
                    f"{model_type_u} 专用整定要求来源模型经模型级终验（finalVerification"
                    "=PASSED）；当前来源未携带终验结论（旧记录/未运行终验装配），拒绝整定"
                ),
                status_code=422,
            )
        if _fv_status != "PASSED":
            raise BizError(
                code="ERR_TUNING_FINAL_VERIFICATION_NOT_PASSED",
                message=(
                    f"{model_type_u} 专用整定要求模型级终验通过，当前终验结论为 "
                    f"{_fv_status}（终验差/未实际验证的模型不进整定链，07§2）"
                ),
                status_code=422,
            )

    K = float(model_params.get("K") or 0)
    theta = float(model_params.get("theta") or 0)

    if K == 0:
        raise BizError(
            code="ERR_MODEL_PARAMS_MISSING",
            message="模型参数 K（过程增益）缺失或为零",
            status_code=400,
        )

    tau = float(model_params.get("tau") or 0)
    if model_type_u == "FOPDT" and not (tau > 0 or math.isinf(tau)):
        raise BizError(
            code="ERR_MODEL_PARAMS_MISSING",
            message="模型参数 tau（时间常数）缺失或非正",
            status_code=400,
        )
    t1 = float(model_params.get("T1") or 0)
    t2 = float(model_params.get("T2") or 0)
    if model_type_u == "SOPDT" and not (t1 > 0 and t2 > 0 and theta >= 0):
        raise BizError(
            code="ERR_MODEL_PARAMS_MISSING",
            message="SOPDT 模型参数 T1/T2（时间常数）缺失或非正，或 theta 非法",
            status_code=400,
        )
    if model_type_u == "IPDT" and theta < 0:
        raise BizError(
            code="ERR_MODEL_PARAMS_MISSING",
            message="IPDT 模型参数 theta（纯滞后）为负",
            status_code=400,
        )

    params = algorithm_params or {}

    # P2-01 TUN-05：算法参数按注册表校验（服务端统一入口，/tune 与 /tune/matrix
    # 共用；schema 层已拒绝未知键，此处对**本算法消费的键**做值级校验，
    # 防止内部调用方/未来通道绕过 schema 直写非法值——矩阵行仅校验自己
    # 消费的键，跨算法共用键不视为未知。未注册算法（IDENTIFICATION_ONLY/
    # MANUAL_TUNING 等）不在本入口报参数错，留给下方 ERR_INVALID_ALGORITHM
    # 分支维持既有错误语义）
    method = get_method_info(algorithm)
    if method:
        consumed = {
            k: v for k, v in params.items() if k in {p["name"] for p in method[0].get("params", [])}
        }
        param_errors = validate_tuning_params(algorithm, consumed)
        if param_errors:
            raise BizError(
                code="ERR_TUNING_PARAM_INVALID",
                message="；".join(param_errors),
                status_code=400,
            )

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
    elif algorithm == "IPDT_PI":
        # P3-07 R12：IPDT 专用 PI 规则（Skogestad IMC 积分规则族）
        # λ 停点终裁（2026-10-12）：默认 7.0 与注册表 default 单源一致
        _lambda_ratio = float(params.get("lambdaRatio", 7.0))
        pid = tune_ipdt_pi(K, theta, lambda_ratio=_lambda_ratio)
        notes = (
            f"IPDT-PI 整定（R12 Skogestad IMC 积分规则族）：λ = {_lambda_ratio} × θ，"
            f"Ti = 2λ+θ，Kc = (2λ+θ)/(K·(λ+θ)²)；默认 λ=7.0 在宽 CI 可信集合"
            "（K±5%/θ±10%）下满足 R15 冻结鲁棒指标（GM≥2.0/PM≥30°/超调≤20%），"
            "调小 λ 超调可能超限，验证器将如实标记不通过"
        )
    elif algorithm == "SOPDT_IMC":
        # P3-07 R13：SOPDT 专用 IMC-PID 解析（一阶 Padé，非降阶）
        _lambda_ratio = float(params.get("lambdaRatio", 1.0))
        pid = tune_sopdt_imc(K, t1, t2, theta, lambda_ratio=_lambda_ratio)
        notes = (
            f"SOPDT-IMC 整定（R13 二阶专用解析，一阶 Padé）：λ = {_lambda_ratio} × θ，"
            "Ti = T1+T2+θ/2，Kc = (T1+T2+θ/2)/(K·(λ+θ/2))，"
            "Td = (T1·T2+θ·(T1+T2)/2)/(T1+T2+θ/2)"
        )
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

    # P3-07（P-35 来源保存）：建议来源绑定——模型版本/终验结论/整定方法与
    # 参数版本随建议携带（B2 冻结链兼容：加性键；保存链/展示层按需消费）
    result["recommendationSource"] = {
        "modelSource": source_context.model_source,
        "sourceRecordId": source_context.source_record_id,
        "modelVersionId": source_context.model_version_id,
        "datasetSnapshotId": source_context.snapshot_id,
        "finalVerification": (
            source_context.final_verification.get("status")
            if isinstance(source_context.final_verification, dict)
            else None
        ),
        "algorithm": algorithm,
        "algorithmParams": dict(params),
        "algorithmVersion": TUNING_ALGORITHM_VERSION,
        "simulationEngineVersion": SIMULATION_ENGINE_VERSION,
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
# 鲁棒性验证链（P3-07 R15：整定→可信集合→冻结指标→DCS 转换→来源）
# ---------------------------------------------------------------------------


async def verify_tuning_robustness(
    db: AsyncSession,
    *,
    loop_id: str,
    model_type: str,
    model_params: dict[str, Any],
    algorithm: str,
    algorithm_params: dict[str, Any] | None = None,
    recommended_pid: dict[str, Any] | None = None,
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
    noise_std: float | None = None,
    source_record_id: str | None = None,
    model_source: str | None = None,
    risk_confirmed: bool = False,
    source_context: TuningModelAuthorization | None = None,
) -> dict[str, Any]:
    """R15 冻结指标的鲁棒验证链（P3-07；03 号文验收链的完整走通入口）.

    链路：候选模型（服务端授权门禁）→ 独立终验（finalVerification=PASSED
    前置）→ 整定（tune_pid 注册表驱动分派）→ 可信模型集合（P-32：终验
    通过模型 ± 95% CI 采样 ≤64）→ 正确模型结构反馈仿真（复用 P3-04 分派
    内核，噪声/负载真实进入反馈，C20-ID/C18-ID）→ DCS 转换（B1 工程事实
    驱动，未知表示明确拒绝）→ 来源绑定（P-35）。

    明确拒绝（O-D6/P-34，不静默降级）：
    - 终验结论缺失/未通过 → ERR_TUNING_FINAL_VERIFICATION_*；
    - OP 限幅未核实（默认 0~100 假设不得冒充实际限位）→
      ERR_TUNING_OP_LIMITS_UNVERIFIED；
    - DCS PID 表示未知 → ERR_TUNING_DCS_REPRESENTATION_UNKNOWN。

    Returns:
        验证报告 dict（frozenThresholds/indicators/overallPass/setBasis/
        dcsRepresentation/disturbanceResponse/sources/lowSnr 等）。
    """
    if source_context is None:
        source_context = await authorize_tuning_model(
            db=db,
            requested_model_type=model_type,
            requested_model_params=dict(model_params),
            loop_id=loop_id,
            source_record_id=source_record_id,
            model_source=model_source,
            risk_confirmed=risk_confirmed,
        )
    model_type = source_context.model_type
    model_params = source_context.model_params
    loop_id = source_context.loop_id or loop_id

    # 链路第 2~3 步：终验前置（对全部结构统一；FOPDT 五法在 tune_pid 内
    # 不改既有行为，本验证链自身要求终验通过——07§2 验收原文）
    _fv = source_context.final_verification
    _fv_status = _fv.get("status") if isinstance(_fv, dict) else None
    if _fv_status is None:
        raise BizError(
            code="ERR_TUNING_FINAL_VERIFICATION_REQUIRED",
            message=(
                "鲁棒验证要求来源模型经模型级终验（finalVerification=PASSED）；"
                "当前来源未携带终验结论（旧记录/未运行终验装配）"
            ),
            status_code=422,
        )
    if _fv_status != "PASSED":
        raise BizError(
            code="ERR_TUNING_FINAL_VERIFICATION_NOT_PASSED",
            message=f"鲁棒验证要求模型级终验通过，当前终验结论为 {_fv_status}",
            status_code=422,
        )

    # 链路第 3 步：整定（注册表驱动分派；IPDT/SOPDT 专用方法 + 终验前置
    # 在 tune_pid 内生效）。recommended_pid 提供时验证该建议，否则验证
    # 服务端现算建议。
    tune_result = await tune_pid(
        model_type=model_type,
        model_params=model_params,
        algorithm=algorithm,
        algorithm_params=algorithm_params,
        current_pid=None,
        loop_id=loop_id,
        source_context=source_context,
    )
    if recommended_pid is not None:
        pid = PIDParams(
            kp=float(recommended_pid.get("kp") or 0.0),
            ti=float(recommended_pid.get("ti") or 0.0),
            td=float(recommended_pid.get("td") or 0.0),
        )
        verified_pid_source = "CALLER_PROVIDED"
    else:
        rec = tune_result["recommendedPid"]
        pid = PIDParams(kp=float(rec["kp"]), ti=float(rec["ti"]), td=float(rec["td"]))
        verified_pid_source = "SERVER_TUNED"

    # 可信集合来源（P-32）：优先 06c 重选不确定性（覆盖全部方法），其次
    # P2-015 参数解析 CI；均缺失 → 名义单点集合（显式 setBasis，不伪补宽度）
    _manifest = None
    if source_context.source_record_id is not None:
        record = await db.get(TuningRecord, source_context.source_record_id)
        _manifest = _read_identification_manifest(record) if record is not None else None
    _evidence = (_manifest or {}).get("resultEvidence") or {}
    _reselect = _evidence.get("reselectUncertainty")
    _param_uq = _evidence.get("uncertainty")
    ci_bounds: dict[str, Any] | None = None
    set_basis_source = "NOMINAL_ONLY"
    set_basis_note = (
        "来源无参数不确定性证据（重选/解析 CI 均未记录），"
        "集合退化为名义单点（显式标注，不伪补宽度）"
    )
    if isinstance(_reselect, dict) and _reselect.get("status") not in {"NOT_RUN", None}:
        ci_bounds = {
            k: v.get("ci95")
            for k, v in _reselect.items()
            if isinstance(v, dict) and isinstance(v.get("ci95"), list)
        }
        set_basis_source = "RESELECT_UNCERTAINTY"
        set_basis_note = "06c P-26/R10 重选时滞/结构不确定性（覆盖全部方法）95% 分位区间"
    elif isinstance(_param_uq, dict) and _param_uq:
        ci_bounds = {
            k: v.get("ci95")
            for k, v in _param_uq.items()
            if isinstance(v, dict) and isinstance(v.get("ci95"), list)
        }
        if ci_bounds:
            set_basis_source = "PARAMETER_UNCERTAINTY"
            set_basis_note = "P2-015 解析协方差 95% CI（仅 ARX/CLIVC 方法产出）"

    try:
        set_build = build_trustworthy_set(model_type, model_params, ci_bounds)
    except SimulationError as exc:
        raise _map_simulation_kernel_error(exc) from exc

    # 工程事实：OP 实际限幅（P-34 未核实明确拒绝）+ DCS PID 表示（O-D6）
    try:
        from app.services.engineering_facts import resolve_loop_engineering_facts

        facts = await resolve_loop_engineering_facts(db, str(loop_id))
    except Exception as exc:  # noqa: BLE001
        raise BizError(
            code="ERR_TUNING_OP_LIMITS_UNVERIFIED",
            message=f"回路工程事实解析失败，无法核实 OP 实际限幅：{exc}",
            status_code=422,
        ) from exc

    op_limits_info = facts.get("opLimits") or {}
    if str(op_limits_info.get("provenance") or "") != "ACTUAL":
        raise BizError(
            code="ERR_TUNING_OP_LIMITS_UNVERIFIED",
            message=(
                "OP 限幅未核实（"
                f"source={op_limits_info.get('source')}, "
                f"provenance={op_limits_info.get('provenance')}）——默认 0~100 假设"
                "不得冒充实际限位；请先在回路工程属性或 OP 位号量程中核实限幅"
            ),
            status_code=422,
        )
    op_limits = (float(op_limits_info["lower"]), float(op_limits_info["upper"]))

    pid_repr = facts.get("pidRepresentation") or {}
    from app.services.pid_conversion import (
        PidRepresentationUnknownError,
        StandardPid,
        dcs_pid_from_standard_facts,
    )

    try:
        dcs_pid = dcs_pid_from_standard_facts(
            StandardPid(kp=pid.kp, ti=pid.ti, td=pid.td), pid_repr
        )
        dcs_section = {
            "status": "KNOWN",
            "modelCode": pid_repr.get("modelCode"),
            "pType": pid_repr.get("pType"),
            "iUnit": pid_repr.get("iUnit"),
            "dUnit": pid_repr.get("dUnit"),
            "dcsPid": dcs_pid.to_dict(),
            "dcsTemplateRevision": pid_repr.get("dcsTemplateRevision"),
        }
    except PidRepresentationUnknownError as exc:
        raise BizError(
            code="ERR_TUNING_DCS_REPRESENTATION_UNKNOWN",
            message=f"DCS PID 表示未知，链路 DCS 转换步骤不可执行（明确拒绝）：{exc}",
            status_code=422,
        ) from exc

    # 集体验证（R15 冻结指标；正确模型结构反馈仿真内置于内核）
    try:
        report = verify_robustness_on_set(
            model_type,
            set_build["members"],
            pid,
            op_limits=op_limits,
            sim_duration=sim_duration,
            sim_step=sim_step,
            setpoint_step=setpoint_step,
        )
    except SimulationError as exc:
        raise _map_simulation_kernel_error(exc) from exc

    # 扰动响应证据（C20-ID/C18-ID：噪声与负载实际进入反馈使 OP 变化）——
    # 名义成员上一次含噪+负载仿真与无扰动仿真对照（确定性种子）
    _noise = 0.05 if noise_std is None else float(noise_std)
    try:
        _clean = simulate_closed_loop(
            model_type,
            model_params,
            pid,
            pid,
            sim_duration=sim_duration,
            sim_step=sim_step,
            setpoint_step=setpoint_step,
            disturbance_type="none",
            op_limits=op_limits,
            budget=SimWorkBudget(
                max_duration_seconds=ROBUST_SET_MAX_SIM_DURATION,
                max_steps=SIM_BUDGET_SYNC.max_steps,
                max_candidates=1,
                max_substeps_per_step=SIM_BUDGET_SYNC.max_substeps_per_step,
                max_total_integration_steps=SIM_BUDGET_SYNC.max_total_integration_steps,
            ),
        )
        _noisy = simulate_closed_loop(
            model_type,
            model_params,
            pid,
            pid,
            sim_duration=sim_duration,
            sim_step=sim_step,
            setpoint_step=setpoint_step,
            disturbance_type="step",
            disturbance_amplitude=0.5,
            noise_std=_noise,
            op_limits=op_limits,
            budget=SimWorkBudget(
                max_duration_seconds=ROBUST_SET_MAX_SIM_DURATION,
                max_steps=SIM_BUDGET_SYNC.max_steps,
                max_candidates=1,
                max_substeps_per_step=SIM_BUDGET_SYNC.max_substeps_per_step,
                max_total_integration_steps=SIM_BUDGET_SYNC.max_total_integration_steps,
            ),
        )
        _op_clean = _clean["recommendedResponse"]["op"]
        _op_noisy = _noisy["recommendedResponse"]["op"]
        _op_std = float(np.std(_op_noisy[1:]))
        _max_delta = float(np.max(np.abs(np.array(_op_noisy[1:]) - np.array(_op_clean[1:]))))
        disturbance_response = {
            "noiseStd": _noise,
            "noiseSeed": 42,
            "loadStepAmplitude": 0.5,
            "opStdWithNoise": round(_op_std, 6),
            "maxOpDifferenceVsClean": round(_max_delta, 6),
            "opVariesWithNoiseOrLoad": bool(_max_delta > 0.0),
            "note": "噪声进入反馈通道、负载阶跃进入对象输入（P3-04 扰动通道）",
        }
    except SimulationError as exc:
        disturbance_response = {"status": "FAILED", "reason": str(exc)}

    # S1-b 知悉传递：低 SNR 档显式"不承诺参数级精度"标注如实带入建议层
    low_snr = None
    _pp = (_manifest or {}).get("preprocess")
    if not isinstance(_pp, dict) or not _pp.get("lowSnr"):
        _pp = ((_manifest or {}).get("searchAudit") or {}).get("preprocessing")
    _low = _pp.get("lowSnr") if isinstance(_pp, dict) else None
    if isinstance(_low, dict):
        low_snr = {
            "mode": _low.get("mode"),
            "snrDb": _low.get("snrDb"),
            "thresholdDb": _low.get("thresholdDb"),
            "disclaimer": (
                "来源辨识数据信噪比低于最低承诺档下界：模型参数带不承诺参数级精度，"
                "建议结合工程判断谨慎实施（S1-b 知悉传递，如实标注不承诺）"
            ),
        }

    report.update(
        {
            "loopId": str(loop_id),
            "modelType": model_type,
            "algorithm": algorithm,
            "verifiedPid": {"kp": pid.kp, "ti": pid.ti, "td": pid.td},
            "verifiedPidSource": verified_pid_source,
            "setBasis": {
                "source": set_basis_source,
                "note": set_basis_note,
                "bounds": set_build["bounds"],
                "freeKeys": set_build["freeKeys"],
                "fixedKeys": set_build["fixedKeys"],
                "memberCount": set_build["memberCount"],
            },
            "opLimits": {
                "lower": op_limits[0],
                "upper": op_limits[1],
                "source": op_limits_info.get("source"),
                "provenance": op_limits_info.get("provenance"),
                "unit": op_limits_info.get("unit"),
            },
            "dcsRepresentation": dcs_section,
            "disturbanceResponse": disturbance_response,
            "lowSnrDisclaimer": low_snr,
            "finalVerification": _fv,
            "sources": {
                "modelSource": source_context.model_source,
                "sourceRecordId": source_context.source_record_id,
                "modelVersionId": source_context.model_version_id,
                "datasetSnapshotId": source_context.snapshot_id,
                "algorithmVersion": TUNING_ALGORITHM_VERSION,
                "simulationEngineVersion": SIMULATION_ENGINE_VERSION,
                "algorithmParams": dict(algorithm_params or {}),
            },
        }
    )
    return report


# ---------------------------------------------------------------------------
# 闭环仿真
# ---------------------------------------------------------------------------


def _map_simulation_kernel_error(exc: Exception) -> Exception:
    """仿真内核异常 → API 语义异常（P3-04：预算/不支持明确拒绝，不静默降级）。"""
    if isinstance(exc, SimulationBudgetError):
        return BizError(
            code="ERR_TUNING_SIMULATION_BUDGET_EXCEEDED",
            message=str(exc),
            status_code=400,
            data={
                "requested": exc.requested,
                "limits": exc.limits,
                "suggestion": (
                    "降低仿真时长/增大步长/减少候选 PID，或改用异步整定任务"
                    "（POST /tuning/identify/history 或工作台异步链，预算更宽且可取消）"
                ),
            },
        )
    if isinstance(exc, SimulationNotSupportedError):
        return BizError(
            code="ERR_TUNING_SIMULATION_MODEL_NOT_SUPPORTED",
            message=str(exc),
            status_code=400,
        )
    if isinstance(exc, SimulationError):
        return BizError(
            code="ERR_TUNING_SIMULATION_INVALID_PARAMS",
            message=str(exc),
            status_code=400,
        )
    return exc


async def run_simulation(
    model_type: str,
    model_params: dict[str, Any],
    current_pid: dict[str, Any],
    recommended_pid: dict[str, Any],
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
    disturbance_type: str = "none",
    pid_candidates: list[dict[str, Any]] | None = None,
    *,
    disturbance_amplitude: float | None = None,
    disturbance_time: float | None = None,
) -> dict[str, Any]:
    """闭环仿真（Phase 2 扩展多 PID 对比；P3-04 扰动真实施加 + 同步预算）。

    Args:
        pid_candidates: 多组候选 PID（每项含 label/kp/ti/td），向后兼容；
            参数相对差 <1%（B-12）的候选去重标记、不重复仿真。
        disturbance_type: none/step（step=负载阶跃扰动真实进入对象输入）。
        disturbance_amplitude/disturbance_time: step 扰动幅值/施加时刻（秒）。

    Raises:
        BizError: ERR_TUNING_SIMULATION_BUDGET_EXCEEDED（超同步预算，引导异步）/
            ERR_TUNING_SIMULATION_MODEL_NOT_SUPPORTED（未知模型/IPDT 缺 K）/
            ERR_TUNING_SIMULATION_INVALID_PARAMS（非法扰动参数等）。
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

    try:
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
            disturbance_amplitude=disturbance_amplitude,
            disturbance_time=disturbance_time,
            budget=SIM_BUDGET_SYNC,
        )
    except SimulationError as exc:
        raise _map_simulation_kernel_error(exc) from exc

    return result


def _simulate_multi_pid(
    model_type: str,
    model_params: dict[str, Any],
    current_pid: dict[str, Any] | None,
    pid_candidates: list[dict[str, Any]],
    sim_duration: float = 600.0,
    sim_step: float = 1.0,
    setpoint_step: float = 1.0,
    disturbance_type: str = "none",
    *,
    disturbance_amplitude: float | None = None,
    disturbance_time: float | None = None,
    budget: SimWorkBudget | None = None,
) -> dict[str, Any]:
    """多 PID 闭环仿真对比（Phase 2.3，供 Celery 任务与 /compare 调用）.

    与 run_simulation 的区别：current_pid 可选，pid_candidates 为必传；
    返回完整仿真结果含 candidateResponses（P3-04 起含 simulationContext
    与每候选 deduplicated/duplicateOf 去重标记）。

    Args:
        pid_candidates: 候选 PID 列表（每项含 label/kp/ti/td/algorithm）
        budget: 工作量预算（缺省 SIM_BUDGET_SYNC 同步口径；异步任务链
            传 SIM_BUDGET_ASYNC，墙钟由 Celery time_limit 兜底）

    Raises:
        BizError: 同 run_simulation（预算/不支持/参数明确拒绝）。
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

    try:
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
            disturbance_type=disturbance_type,
            pid_candidates=candidates_tuples or None,
            disturbance_amplitude=disturbance_amplitude,
            disturbance_time=disturbance_time,
            budget=budget or SIM_BUDGET_SYNC,
        )
    except SimulationError as exc:
        raise _map_simulation_kernel_error(exc) from exc


# ---------------------------------------------------------------------------
# 整定任务管理
# ---------------------------------------------------------------------------

#: W5：currentPid 三元组读取涉及的 PID tag 角色
_PID_RUNTIME_ROLES = ("PID_P", "PID_I", "PID_D")

#: W5：标准 PID 转换依据的引擎版本标识（B1 pid_conversion 工程事实驱动入口）
_PID_CONVERSION_ENGINE = "PID_CONVERSION_FACTS_V1"


def _frozen_unit_basis(
    facts: dict[str, Any] | None,
    k_value: Any,
    identify_method: str | None,
) -> dict[str, Any]:
    """W5：保存链冻结模型 K 单位域（tuning_units 换算层接入）.

    按来源辨识记录的链路声明单位域并给出跨域换算（kConverted）或诚实未知：
    - HISTORICAL_* → 归一化域（与历史辨识链 ``_normalized_k_basis`` 同源）；
    - STEP_* → 存储原值域（与阶跃辨识链 ``_step_unit_basis`` 同源）；
    - 无来源方法（MANUAL 人工模型）→ 单位域未声明，诚实 UNKNOWN
      （工程师自证，平台不冒充辨识链 provenance）。
    """
    from app.services import tuning_units as tu

    method = str(identify_method or "")
    if method.startswith("STEP_"):
        return _step_unit_basis(facts, k_value)
    if method.startswith("HISTORICAL_"):
        return _normalized_k_basis(facts, k_value)
    pv_span, op_span = tu.spans_from_engineering_facts(facts)
    return tu.unit_basis_block(
        "UNKNOWN",
        pv_span=pv_span,
        op_span=op_span,
        note="人工模型：单位域未声明（无辨识链 provenance，K 量纲由工程师自证）",
        k_source=k_value,
    )


async def _current_pid_snapshot(
    db: AsyncSession,
    loop: LoopLedger,
    facts: dict[str, Any] | None,
) -> dict[str, Any]:
    """W5：保存链 currentPid 三元组快照（标准形式 / 原始表示 / 转换依据）.

    服务端读取回路 PID_P/PID_I/PID_D 实时值（Redis 优先、tag.current_value
    兜底——与回路详情 runtimeParams 同口径），经工程事实 ``pidRepresentation``
    驱动的 ``pid_conversion.standard_pid_from_facts``（B1 入口，单源复用）
    转换为标准形式（Kp 无量纲 / Ti、Td 秒）。

    诚实化原则：未知 DCS 表示不默认标准 kp/ti/td（显式 REPRESENTATION_UNKNOWN）；
    转换数值异常（如 PB=0 除零）显式 CONVERSION_ERROR；实时值缺失显式
    UNAVAILABLE——一律不伪造标准形式。

    Returns:
        {"status": KNOWN|REPRESENTATION_UNKNOWN|CONVERSION_ERROR|UNAVAILABLE,
         "standard": {kp,ti,td}|None, "raw": {p,i,d}|None,
         "representation": {...}, "conversion": {...}|None,
         "readAt": str|None, "reason": str|None}
    """
    from app.models.loop import LoopTagMapping
    from app.models.tag import TagRegistry
    from app.services.pid_conversion import (
        DcsPid,
        PidRepresentationUnknownError,
        standard_pid_from_facts,
    )

    pid_repr = (facts or {}).get("pidRepresentation") or {}

    result = await db.execute(
        select(LoopTagMapping, TagRegistry)
        .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
        .where(
            LoopTagMapping.loop_id == str(loop.id),
            LoopTagMapping.tag_role.in_(_PID_RUNTIME_ROLES),
        )
    )
    role_tag: dict[str, TagRegistry] = {m.tag_role: t for m, t in result.all()}

    # Redis 实时缓存（失败/无缓存回退 tag.current_value，与回路详情同口径）
    cached: dict[str, dict] = {}
    tag_names = [t.tag_name for t in role_tag.values() if t.tag_name]
    if tag_names:
        try:
            from app.services.data_source.realtime_subscriber import get_subscriber

            for item in await get_subscriber().get_cached_values(tag_names):
                tc = item.get("tagCode")
                if tc:
                    cached[tc] = item
        except Exception:  # noqa: BLE001
            logger.warning("保存链读取 PID 实时缓存失败（回退 tag.current_value）")

    def _role_value(role: str) -> tuple[float | None, Any]:
        tag = role_tag.get(role)
        if tag is None:
            return None, None
        item = cached.get(tag.tag_name) if tag.tag_name else None
        read_at = item.get("collectTime") if item else None
        if item is not None:
            try:
                return float(item.get("value")), read_at
            except (TypeError, ValueError):
                pass
        value = getattr(tag, "current_value", None)
        try:
            return (float(value) if value is not None else None), read_at
        except (TypeError, ValueError):
            return None, read_at

    p_val, read_p = _role_value("PID_P")
    i_val, read_i = _role_value("PID_I")
    d_val, _ = _role_value("PID_D")
    read_at = max((x for x in (read_p, read_i) if x is not None), default=None)

    snapshot: dict[str, Any] = {
        "status": "UNAVAILABLE",
        "standard": None,
        "raw": None,
        "representation": pid_repr or None,
        "conversion": None,
        "readAt": str(read_at) if read_at is not None else None,
        "reason": None,
    }
    if p_val is None or i_val is None:
        snapshot["reason"] = (
            "回路未绑定 PID_P/PID_I 位号或无实时值，无法形成当前 PID 快照（不伪造）"
        )
        return snapshot

    snapshot["raw"] = {"p": p_val, "i": i_val, "d": d_val}
    try:
        standard = standard_pid_from_facts(
            DcsPid(p=p_val, i=i_val, d=d_val if d_val is not None else 0.0), pid_repr
        )
    except PidRepresentationUnknownError as exc:
        snapshot["status"] = "REPRESENTATION_UNKNOWN"
        snapshot["reason"] = str(exc)
        return snapshot
    except ValueError as exc:
        snapshot["status"] = "CONVERSION_ERROR"
        snapshot["reason"] = f"DCS 私有表示转标准 PID 数值异常：{exc}"
        return snapshot
    snapshot["status"] = "KNOWN"
    snapshot["standard"] = standard.to_dict()
    snapshot["conversion"] = {
        "engine": _PID_CONVERSION_ENGINE,
        "modelId": pid_repr.get("modelId"),
        "modelCode": pid_repr.get("modelCode"),
        "pType": pid_repr.get("pType"),
        "iUnit": pid_repr.get("iUnit"),
        "dUnit": pid_repr.get("dUnit"),
        "dcsTemplateRevision": pid_repr.get("dcsTemplateRevision"),
    }
    return snapshot


async def create_tuning_task(
    db: AsyncSession,
    loop_id: str,
    model_type: str,
    model_params: dict[str, Any],
    algorithm: str,
    recommended_pid: dict[str, Any],
    *,
    model_source: str | None,
    source_record_id: str | None = None,
    risk_confirmed: bool = False,
    simulation_result: dict[str, Any] | None = None,
    status: str = "SIMULATED",
    created_by: str | None = None,
    risk_assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """保存最终选定整定方案（P3-B2 W4：服务端校验冻结，G2-INC-c 闭合）.

    与推荐链（/tune /simulate /compare）和异步任务链
    （``_do_tune_and_simulate``）共用同一授权门禁
    ``authorize_tuning_model``——同回路（跨回路引用拒绝）、来源可访问性
    （伪造/过期来源拒绝）、模型绑定一致（请求参数须与服务端持久化辨识
    记录逐项一致）。冻结语义（保存→落库→回读不漂移，与 G2-INC-a 同源）：

    - 模型类型/参数：授权后一律取服务端持久化值（process_model_version 优先），
      并随 ``process_model_version_id`` 绑定模型版本身份；
    - 来源元数据（identify_method/data_source/confidence*/fitting_score 等）：
      从来源辨识记录服务端派生；MANUAL 人工模型全部置空（诚实标记人工，
      不冒充算法输出）；
    - 辨识时间窗 / 输入包引用 / DCS 模板：随来源记录冻结入列与 calc_context；
    - currentPid（W5）：服务端读取回路实时 PID 原始值并经工程事实转换为
      标准形式（Kp/Ti/Td 秒），三元组（标准/原始/转换依据）冻结入
      calc_context；未知 DCS 表示不默认标准 kp/ti/td。

    旧协议（``model_source`` 缺省的裸模型保存）显式拒绝，与推荐链
    "旧版裸模型请求已停止放行"同口径。
    """
    if model_source is None:
        raise BizError(
            code="ERR_TUNING_SOURCE_REQUIRED",
            message="最终方案保存必须声明模型来源（modelSource）；旧版裸模型保存已停止放行",
            status_code=400,
        )

    loop = await _get_loop(db, loop_id)

    source_context = await authorize_tuning_model(
        db=db,
        requested_model_type=model_type,
        requested_model_params=dict(model_params),
        loop_id=str(loop.id),
        source_record_id=source_record_id,
        model_source=model_source,
        risk_confirmed=risk_confirmed,
    )
    # 授权后模型以服务端持久化值为准（客户端请求参数已通过逐项一致性校验）
    model_type = source_context.model_type
    model_params = dict(source_context.model_params)

    # SIMULATED 状态必须有仿真证据（服务端 /compare 输出形态），拒绝"声称已仿真"
    if str(status) == "SIMULATED":
        candidate_responses = (
            simulation_result.get("candidateResponses")
            if isinstance(simulation_result, dict)
            else None
        )
        if not (isinstance(candidate_responses, list) and candidate_responses):
            raise BizError(
                code="ERR_TUNING_SIMULATION_EVIDENCE_REQUIRED",
                message=(
                    "SIMULATED 状态的最终方案必须携带含 candidateResponses 的仿真证据"
                    "（服务端 /tuning/compare 输出）"
                ),
                status_code=400,
            )

    # 来源辨识记录（authorize 已验证存在；session 身份映射内复用）
    source_record = None
    if source_record_id is not None:
        source_record = await db.get(TuningRecord, source_record_id)

    if source_record is not None:
        identify_method = source_record.identify_method
        data_source = source_record.data_source
        confidence_level = source_record.confidence_level
        confidence_reason = source_record.confidence_reason
        excitation_score = source_record.excitation_score
        residual_test_passed = source_record.residual_test_passed
        fitting_score = source_record.fitting_score
        model_version_id = (
            str(source_record.process_model_version_id)
            if source_record.process_model_version_id
            else None
        )
        source_snapshot_id = (
            str(source_record.dataset_snapshot_id) if source_record.dataset_snapshot_id else None
        )
        source_window = {
            "tsStart": source_record.time_window_start.isoformat()
            if source_record.time_window_start
            else None,
            "tsEnd": source_record.time_window_end.isoformat()
            if source_record.time_window_end
            else None,
        }
        time_window_start = source_record.time_window_start
        time_window_end = source_record.time_window_end
    else:
        # MANUAL 人工模型：来源元数据全部置空（诚实标记，不冒充算法输出）
        identify_method = None
        data_source = None
        confidence_level = None
        confidence_reason = None
        excitation_score = None
        residual_test_passed = None
        fitting_score = None
        model_version_id = None
        source_snapshot_id = None
        source_window = None
        time_window_start = None
        time_window_end = None

    # 工程事实（单位域/DCS 模板/currentPid 转换依据共用一次解析；失败按未知）
    try:
        from app.services.engineering_facts import resolve_loop_engineering_facts

        facts = await resolve_loop_engineering_facts(db, str(loop.id))
    except Exception:  # noqa: BLE001
        logger.warning("保存链工程事实解析失败（unitBasis/currentPid 按显式未知，不伪补）")
        facts = None

    pid_repr = (facts or {}).get("pidRepresentation") or {}
    dcs_revision = pid_repr.get("dcsTemplateRevision") or _dcs_template_revision(loop)

    # W5：currentPid 三元组 + K 单位域冻结
    pid_snapshot = await _current_pid_snapshot(db, loop, facts)
    standard_current_pid = pid_snapshot.get("standard")
    unit_basis = _frozen_unit_basis(facts, model_params.get("K"), identify_method)

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
        identify_method=identify_method,
        data_source=data_source,
        confidence_level=confidence_level,
        confidence_reason=confidence_reason,
        excitation_score=excitation_score,
        residual_test_passed=residual_test_passed,
        # 人工实施清单：当前/回退 PID = 服务端标准形式快照（未知/缺失时 None，
        # 不伪造 naive kp/ti/td）
        current_pid=dict(standard_current_pid) if standard_current_pid else None,
        rollback_pid=dict(standard_current_pid) if standard_current_pid else None,
        risk_assessment=risk_assessment,
        # W4 冻结身份（与异步 TUNING_PROPOSAL 链同字段口径）
        source_record_id=source_record_id,
        process_model_version_id=model_version_id,
        dcs_template_revision=str(dcs_revision),
        time_window_start=time_window_start,
        time_window_end=time_window_end,
        calc_context={
            "schemaVersion": "1",
            "kind": "TUNING_PROPOSAL",
            "savedVia": "WORKBENCH_FINAL_SELECTION",
            "loopId": str(loop.id),
            "sourceRecordId": source_record_id,
            "modelSource": source_context.model_source,
            "modelVersionId": model_version_id,
            "frozenModel": {"modelType": model_type, "modelParams": dict(model_params)},
            "sourceWindow": source_window,
            "datasetSnapshotId": source_snapshot_id,
            "dcsTemplateRevision": str(dcs_revision),
            "unitBasis": unit_basis,
            "currentPid": pid_snapshot,
            "riskConfirmed": bool(risk_confirmed),
        },
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
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """查询整定任务列表（分页）。

    status 支持逗号分隔多值（单值向后兼容）；start_time/end_time 按
    created_at 闭区间过滤（naive UTC）。
    """
    # status 逗号分隔多值 → IN 查询；单值为 IN 的特例，行为不变
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else []

    query = select(TuningRecord, LoopLedger.tag_name).outerjoin(
        LoopLedger, TuningRecord.loop_id == LoopLedger.id
    )
    # 2026-10-10 停用回路裁决：列表页不显示停用回路（outerjoin 上加过滤，
    # 孤儿记录一并剔除）
    query = query.where(LoopLedger.is_active.is_(True))

    if loop_id:
        query = query.where(TuningRecord.loop_id == loop_id)
    if algorithm:
        query = query.where(TuningRecord.algorithm == algorithm)
    if statuses:
        query = query.where(TuningRecord.status.in_(statuses))
    if start_time:
        query = query.where(TuningRecord.created_at >= start_time)
    if end_time:
        query = query.where(TuningRecord.created_at <= end_time)

    # 总数（与列表同口径过滤停用回路：无 join，用活跃回路子查询）
    count_query = (
        select(func.count())
        .select_from(TuningRecord)
        .where(
            TuningRecord.loop_id.in_(select(LoopLedger.id).where(LoopLedger.is_active.is_(True)))
        )
    )
    if loop_id:
        count_query = count_query.where(TuningRecord.loop_id == loop_id)
    if algorithm:
        count_query = count_query.where(TuningRecord.algorithm == algorithm)
    if statuses:
        count_query = count_query.where(TuningRecord.status.in_(statuses))
    if start_time:
        count_query = count_query.where(TuningRecord.created_at >= start_time)
    if end_time:
        count_query = count_query.where(TuningRecord.created_at <= end_time)
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


async def cancel_tuning_task(task_id: str, *, db: AsyncSession | None = None) -> dict[str, Any]:
    """取消异步整定任务（P3-04：取消语义明确化——进度 CANCELLED + 记录终态落定）。

    - Celery 侧：PENDING/STARTED 状态 revoke(terminate=True, SIGTERM)；
    - 进度侧：tuning_progress/TaskTracker 同步 CANCELLED（V62-P1-013 链路）；
    - 记录侧：该 task_id 名下仍处 RUNNING 的 TuningRecord 落定 INCONCLUSIVE，
      confidence_reason 显式"用户主动取消"（状态机无 CANCELLED 枚举，不新增
      DB 约束/迁移，以原因字段区分取消与失败——实现裁量，交接登记）。

    Returns:
        {"taskId": ..., "status": "CANCELLED"|既有终态}。
    """
    from celery.result import AsyncResult

    from app.services.tuning_progress import update_progress
    from app.tasks.celery_app import celery_app

    result = AsyncResult(task_id, app=celery_app)
    if result.state not in ("PENDING", "RUNNING", "STARTED"):
        # 已终态（SUCCESS/FAILED/REVOKED）不可取消
        return {"taskId": task_id, "status": result.state}

    result.revoke(terminate=True, signal="SIGTERM")
    await update_progress(
        task_id,
        status="CANCELLED",
        message="用户主动取消",
    )

    # 记录终态落定：RUNNING → INCONCLUSIVE（原因显式"用户取消"）
    if db is not None:
        cancel_rows = (
            await db.execute(
                update(TuningRecord)
                .where(TuningRecord.task_id == task_id, TuningRecord.status == "RUNNING")
                .values(
                    status="INCONCLUSIVE",
                    confidence_reason="任务已取消（用户主动取消）",
                    completed_at=func.now(),
                )
            )
        ).rowcount
        if cancel_rows:
            await db.commit()
            logger.info(
                "取消整定任务 %s：落定 %d 条 RUNNING 记录为 INCONCLUSIVE", task_id, cancel_rows
            )

    return {"taskId": task_id, "status": "CANCELLED"}


async def get_tuning_history_stats(db: AsyncSession) -> dict[str, Any]:
    """整定历史统计。"""
    # 2026-10-10 停用回路裁决：统计一律排除停用回路（无 join 查询统一用
    # 活跃回路子查询过滤）
    active_loops = select(LoopLedger.id).where(LoopLedger.is_active.is_(True))

    # 总数
    total_result = await db.execute(
        select(func.count()).select_from(TuningRecord).where(TuningRecord.loop_id.in_(active_loops))
    )
    total = total_result.scalar() or 0

    # 按算法分组
    algo_result = await db.execute(
        select(TuningRecord.algorithm, func.count())
        .where(TuningRecord.loop_id.in_(active_loops))
        .group_by(TuningRecord.algorithm)
    )
    by_algorithm = {row[0]: row[1] for row in algo_result.all()}

    # 按状态分组
    status_result = await db.execute(
        select(TuningRecord.status, func.count())
        .where(TuningRecord.loop_id.in_(active_loops))
        .group_by(TuningRecord.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    # 平均拟合度
    avg_result = await db.execute(
        select(func.avg(TuningRecord.fitting_score))
        .where(TuningRecord.fitting_score.isnot(None))
        .where(TuningRecord.loop_id.in_(active_loops))
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
        .where(TuningRecord.loop_id.in_(active_loops))
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

    # 最近 10 条任务（同口径过滤停用回路）
    recent_result = await db.execute(
        select(TuningRecord, LoopLedger.tag_name)
        .outerjoin(LoopLedger, TuningRecord.loop_id == LoopLedger.id)
        .where(LoopLedger.is_active.is_(True))
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
    """获取回路，不存在则抛错。

    2026-10-10 停用裁决：停用回路拒绝发起整定（本函数的 4 个调用方
    identify/preview/create_tuning_task 均为计算触发口，无读路径）。
    """
    result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )
    if loop.is_active is False:
        raise BizError(
            code="ERR_LOOP_INACTIVE",
            message=f"回路已停用，不可发起整定: {loop.tag_name}",
            status_code=400,
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
    # P3-B2 W4/W5：冻结上下文（旧记录无 calc_context → None，诚实不伪补）
    calc_ctx = record.calc_context if isinstance(record.calc_context, dict) else {}
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
        # 辨识时间窗（异步历史辨识落库；同步阶跃/MANUAL 为 None）
        "timeWindowStart": record.time_window_start.isoformat()
        if record.time_window_start
        else None,
        "timeWindowEnd": record.time_window_end.isoformat() if record.time_window_end else None,
        # P3-B2 W4/W5：来源身份与冻结上下文（加性键；旧记录为 None）
        "sourceRecordId": str(record.source_record_id) if record.source_record_id else None,
        "dcsTemplateRevision": record.dcs_template_revision,
        "modelSource": calc_ctx.get("modelSource"),
        "unitBasis": calc_ctx.get("unitBasis"),
        "currentPidContext": calc_ctx.get("currentPid"),
        # P3-04：仿真验证状态（加性键）——无仿真结果显式"未验证"，
        # 不以空白/占位数据冒充仿真结论（诚实化原则）
        "simulationStatus": (
            "SIMULATED"
            if isinstance(record.simulation_result, dict) and record.simulation_result
            else "NOT_SIMULATED"
        ),
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
    "cancel_tuning_task",
    "list_tuning_tasks",
    "get_tuning_task_detail",
    "get_tuning_history_stats",
    "get_tuning_methods",
]
