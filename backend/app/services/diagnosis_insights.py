"""诊断洞察只读聚合服务（16 号文：F1 回路诊断档案 + F2 复诊对比 + F3 覆盖台账 + F4 回路组对比
+ F5 发起前数据预检 + F6 复核反馈统计）。

设计文档：docs/MVP设计/16-诊断模块功能扩展方案.md §4、§5.1、§5.4
- 零迁移纯查询：不新增 ORM 模型，不修改 diagnosis_orchestrator.py 主链路
- import 纪律（§1.4 P3）：允许 import 处置/整定域 ORM 模型（物理表恒在），
  禁止 import 其他模块 service 层；跨模块查询段前必须先判
  ``is_module_enabled("handling"/"tuning"/"assess")``，禁用时跳过查询并在响应标记
  能力字段（前端据此隐藏图层/入口，而非置灰）
- F3/F4/F5/F6 数据全部来自 PG 表（diagnosis_run/loop_ledger/kpi_snapshot_hourly），
  不触 TDengine（F5 为 D1 裁决的廉价代理口径：快照行数密度，零 TDengine 查询）
- 安全边界（§3.2）：F6 只输出"建议复核阈值"提示，不提供任何自动调参入口
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.modules import is_module_enabled
from app.models.diagnosis_run import DiagnosisRun
from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger
from app.models.loop_action_item import LoopActionItem
from app.models.metric import KpiSnapshotHourly
from app.models.tuning import TuningRecord
from app.services.diagnosis_operators import list_operators
from app.services.waveform import lttb_downsample_multi_series

#: F1 时间窗预设 → 天数（"all" 截断 90d，§5.3 性能边界）
_WINDOW_DAYS: dict[str, int] = {"30d": 30, "90d": 90, "all": 90}

#: KPI 趋势 LTTB 降采样目标点数（90d≈2160 点 → ≤2000）
_TREND_MAX_POINTS = 2000

_EPOCH = datetime(1970, 1, 1)

#: 反向指标（下降=改善）算子特征名集合：均为故障/劣化指示量，升=恶化。
#: 未列出的特征按正向（升=改善）处理（fitting_score/similarity 等拟合质量量）。
_REVERSE_FEATURES = frozenset(
    {
        # 质量码
        "bad_rate",
        "bad_count",
        "max_consecutive_bad",
        # 饱和
        "saturation_rate",
        # 粘滞
        "stiction_index",
        "stiction_ratio",
        "ngi",
        "nli",
        # 振荡（index=振荡指数 / amplitude=振荡幅值）
        "index",
        "amplitude",
        "oscillation_rate",
        # 传感器
        "frozen_max_segment",
        "frozen_segment_ratio",
        "noise_std_ratio",
        "drift_magnitude",
        # 扰动
        "shift_frequency",
        "shift_magnitude",
        "shift_count",
        # 整定（ratio=时间常数比/超调/衰减比/稳态误差）
        "overshoot",
        "decay_ratio",
        "steady_state_error",
        "ratio",
        # 时长/行程类
        "settling_time",
        "output_trip_index",
    }
)

#: metric_summary 键 → 方向（negative 全部反向、positive 全部正向，
#: 与 diagnosis_orchestrator._build_metric_summary 的分组口径一致）
_METRIC_REVERSE_KEYS = frozenset(
    {
        "badValueRate",
        "saturationRate",
        "oscillationRate",
        "stictionIndex",
        "settlingTime",
        "outputTravelIndex",
    }
)


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _num(v: Any) -> float | None:
    """数值宽松转换（Decimal/str/bool → float；不可数值化返回 None）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _dt_to_epoch_ms(dt: datetime) -> int:
    """naive UTC datetime → epoch 毫秒（纯算术，禁用 .timestamp() 热路径红线）。"""
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return int((dt - _EPOCH).total_seconds() * 1000)


def _direction(delta: float | None, reverse: bool) -> str | None:
    """delta → improved/worsened/flat（反向指标方向翻转；不可比较返回 None）。"""
    if delta is None:
        return None
    if delta == 0:
        return "flat"
    worsened = delta > 0 if reverse else delta < 0
    return "worsened" if worsened else "improved"


async def _load_loop(db: AsyncSession, loop_id: str) -> LoopLedger:
    loop = (
        await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    ).scalar_one_or_none()
    if loop is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"回路不存在: {loop_id}", status_code=404)
    return loop


def _diagnosed_at(run: DiagnosisRun) -> datetime | None:
    """诊断时间口径：与 runs/latest 一致（COALESCE(finished_at, created_at)）。"""
    return run.finished_at or run.created_at


# ---------------------------------------------------------------------------
# F1 回路诊断档案
# ---------------------------------------------------------------------------


def _run_timeline_item(run: DiagnosisRun) -> dict[str, Any]:
    return {
        "runId": run.id,
        "diagnosedAt": _iso(_diagnosed_at(run)),
        "primaryCategory": run.primary_category,
        "secondaryCategories": run.secondary_categories or [],
        "severity": run.severity,
        "confidence": float(run.primary_confidence) if run.primary_confidence is not None else None,
        "triggerType": run.trigger_type,
        "status": run.status,
        "reviewStatus": run.review_status,
    }


def _build_kpi_trend(
    snapshots: list[Any],
) -> dict[str, Any]:
    """kpi_snapshot_hourly 行 → score/oscillationRate 双序列（LTTB ≤2000 点）。

    snapshots 为 (ts_start, score, oscillation_rate) 元组列表（升序）。
    """
    empty: dict[str, Any] = {"available": False, "series": {"score": [], "oscillationRate": []}}
    if not snapshots:
        return empty

    ts_ms = [_dt_to_epoch_ms(r[0]) for r in snapshots]
    series_map = {
        "score": [None if r[1] is None else float(r[1]) for r in snapshots],
        "oscillationRate": [None if r[2] is None else float(r[2]) for r in snapshots],
    }
    if len(ts_ms) > _TREND_MAX_POINTS:
        ts_ms, series_map = lttb_downsample_multi_series(ts_ms, series_map, _TREND_MAX_POINTS)
    return {
        "available": True,
        "series": {
            "score": [{"t": t, "v": v} for t, v in zip(ts_ms, series_map["score"], strict=True)],
            "oscillationRate": [
                {"t": t, "v": v} for t, v in zip(ts_ms, series_map["oscillationRate"], strict=True)
            ],
        },
    }


async def _load_handling_events(
    db: AsyncSession, loop_id: str, window_start: datetime, window_end: datetime
) -> list[dict[str, Any]]:
    """处置工单事件（创建/关闭竖线）。调用前须确认 handling 模块启用。"""
    orders = (
        await db.execute(
            select(
                HandlingOrder.id,
                HandlingOrder.order_no,
                HandlingOrder.title,
                HandlingOrder.status,
                HandlingOrder.created_at,
                HandlingOrder.verified_at,
            ).where(
                HandlingOrder.loop_id == loop_id,
                HandlingOrder.created_at >= window_start,
                HandlingOrder.created_at <= window_end,
            )
        )
    ).all()
    items: list[dict[str, Any]] = []
    for oid, order_no, title, status, created_at, verified_at in orders:
        items.append(
            {
                "type": "handling",
                "subtype": "created",
                "at": _iso(created_at),
                "title": f"{order_no} {title}",
                "refId": oid,
            }
        )
        if (
            status == "CLOSED"
            and verified_at is not None
            and window_start <= verified_at <= window_end
        ):
            items.append(
                {
                    "type": "handling",
                    "subtype": "closed",
                    "at": _iso(verified_at),
                    "title": f"{order_no} 关闭验证",
                    "refId": oid,
                }
            )
    return items


async def _load_tuning_events(
    db: AsyncSession, loop_id: str, window_start: datetime, window_end: datetime
) -> list[dict[str, Any]]:
    """整定记录事件（开始/完成竖线）。调用前须确认 tuning 模块启用。"""
    records = (
        await db.execute(
            select(
                TuningRecord.id,
                TuningRecord.algorithm,
                TuningRecord.created_at,
                TuningRecord.completed_at,
            ).where(
                TuningRecord.loop_id == loop_id,
                TuningRecord.created_at >= window_start,
                TuningRecord.created_at <= window_end,
            )
        )
    ).all()
    items: list[dict[str, Any]] = []
    for rid, algorithm, created_at, completed_at in records:
        items.append(
            {
                "type": "tuning",
                "subtype": "created",
                "at": _iso(created_at),
                "title": f"整定记录（{algorithm}）",
                "refId": rid,
            }
        )
        if completed_at is not None and window_start <= completed_at <= window_end:
            items.append(
                {
                    "type": "tuning",
                    "subtype": "completed",
                    "at": _iso(completed_at),
                    "title": f"整定完成（{algorithm}）",
                    "refId": rid,
                }
            )
    return items


async def loop_archive(
    db: AsyncSession,
    loop_id: str,
    window: str = "90d",
) -> dict[str, Any]:
    """F1 回路诊断档案聚合（16 号文 §4 F1 / §5.1）。

    结构：回路静态信息 + 全量摘要 + 窗口内 run 时间轴（升序）+
    KPI 趋势（LTTB）+ 处置/整定事件竖线（模块启用时才查询）。
    """
    days = _WINDOW_DAYS.get(window)
    if days is None:
        raise BizError(
            code="ERR_PARAM", message=f"window 仅支持 30d/90d/all: {window}", status_code=400
        )
    loop = await _load_loop(db, loop_id)

    now = _utcnow_naive()
    window_start = now - timedelta(days=days)

    # 全量摘要（累计次数/首末诊断时间，不受窗口影响）
    total, first_at, last_at = (
        await db.execute(
            select(
                func.count(DiagnosisRun.id),
                func.min(DiagnosisRun.created_at),
                func.max(DiagnosisRun.created_at),
            ).where(DiagnosisRun.loop_id == loop_id)
        )
    ).one()
    latest_run = (
        (
            await db.execute(
                select(DiagnosisRun)
                .where(DiagnosisRun.loop_id == loop_id)
                .order_by(DiagnosisRun.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    # 窗口内 run 时间轴（升序，供泳道渲染）
    runs = (
        (
            await db.execute(
                select(DiagnosisRun)
                .where(
                    DiagnosisRun.loop_id == loop_id,
                    DiagnosisRun.created_at >= window_start,
                )
                .order_by(DiagnosisRun.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    # KPI 小时快照趋势（评估域产出；无快照 → available=false，不误报）
    snapshots = (
        await db.execute(
            select(
                KpiSnapshotHourly.ts_start,
                KpiSnapshotHourly.score,
                KpiSnapshotHourly.oscillation_rate,
            )
            .where(
                KpiSnapshotHourly.loop_id == loop_id,
                KpiSnapshotHourly.ts_start >= window_start,
            )
            .order_by(KpiSnapshotHourly.ts_start.asc())
        )
    ).all()

    # 事件竖线（P3 门控：模块禁用时跳过查询段，仅标记能力字段）
    handling_enabled = is_module_enabled("handling")
    tuning_enabled = is_module_enabled("tuning")
    events: list[dict[str, Any]] = []
    if handling_enabled:
        events.extend(await _load_handling_events(db, loop_id, window_start, now))
    if tuning_enabled:
        events.extend(await _load_tuning_events(db, loop_id, window_start, now))
    events.sort(key=lambda e: e["at"] or "")

    return {
        "loop": {
            "loopId": loop.id,
            "loopName": loop.tag_name,
            "loopType": loop.loop_type,
            "level": loop.importance_level,
        },
        "summary": {
            "totalRuns": int(total or 0),
            "firstDiagnosedAt": _iso(first_at),
            "lastDiagnosedAt": _iso(last_at),
            "latestCategory": latest_run.primary_category if latest_run else None,
            "latestConfidence": (
                float(latest_run.primary_confidence)
                if latest_run is not None and latest_run.primary_confidence is not None
                else None
            ),
        },
        "runs": [_run_timeline_item(r) for r in runs],
        "kpiTrend": _build_kpi_trend(list(snapshots)),
        "events": {
            "handlingEnabled": handling_enabled,
            "tuningEnabled": tuning_enabled,
            "items": events,
        },
        "window": window,
        "windowStart": _iso(window_start),
    }


# ---------------------------------------------------------------------------
# F2 复诊对比
# ---------------------------------------------------------------------------


async def _load_run_or_404(db: AsyncSession, run_id: str) -> DiagnosisRun:
    run = (
        await db.execute(select(DiagnosisRun).where(DiagnosisRun.id == run_id))
    ).scalar_one_or_none()
    if run is None:
        raise BizError(code="ERR_NOT_FOUND", message=f"诊断记录不存在: {run_id}", status_code=404)
    return run


async def _adjacent_previous_run(db: AsyncSession, run: DiagnosisRun) -> DiagnosisRun | None:
    """同回路该 run 之前最近一条 SUCCESS/PARTIAL run（相邻对比基准）。"""
    return (
        (
            await db.execute(
                select(DiagnosisRun)
                .where(
                    DiagnosisRun.loop_id == run.loop_id,
                    DiagnosisRun.created_at < run.created_at,
                    DiagnosisRun.status.in_(("SUCCESS", "PARTIAL")),
                )
                .order_by(DiagnosisRun.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def _compare_run_brief(run: DiagnosisRun) -> dict[str, Any]:
    return {
        "runId": run.id,
        "diagnosedAt": _iso(_diagnosed_at(run)),
        "primaryCategory": run.primary_category,
        "severity": run.severity,
        "confidence": float(run.primary_confidence) if run.primary_confidence is not None else None,
        "windowStart": _iso(run.time_window_start),
        "windowEnd": _iso(run.time_window_end),
    }


def _build_feature_rows(base: DiagnosisRun, target: DiagnosisRun) -> list[dict[str, Any]]:
    """operator_results 同算子同特征名逐行对照（任一侧 executed 的算子）。"""
    base_ops: dict[str, Any] = base.operator_results or {}
    target_ops: dict[str, Any] = target.operator_results or {}

    rows: list[dict[str, Any]] = []
    for op_name in sorted(set(base_ops) | set(target_ops)):
        b = base_ops.get(op_name) or {}
        t = target_ops.get(op_name) or {}
        if not (b.get("executed") or t.get("executed")):
            continue  # 两侧都未执行（fast 组过滤/信号缺失）无对照意义
        b_feats = b.get("features") or {}
        t_feats = t.get("features") or {}
        for feat_name in sorted(set(b_feats) | set(t_feats)):
            b_val = b_feats.get(feat_name)
            t_val = t_feats.get(feat_name)
            b_num, t_num = _num(b_val), _num(t_val)
            delta = round(t_num - b_num, 6) if b_num is not None and t_num is not None else None
            rows.append(
                {
                    "operator": op_name,
                    "feature": feat_name,
                    "baseValue": b_num if b_num is not None else b_val,
                    "targetValue": t_num if t_num is not None else t_val,
                    "delta": delta,
                    "direction": _direction(delta, feat_name in _REVERSE_FEATURES),
                }
            )
    return rows


def _build_kpi_rows(base: DiagnosisRun, target: DiagnosisRun) -> list[dict[str, Any]]:
    """metric_summary 关键指标对照（negative 反向 / positive 正向）。"""
    base_ms: dict[str, Any] = base.metric_summary or {}
    target_ms: dict[str, Any] = target.metric_summary or {}
    base_metrics = {**base_ms.get("positive", {}), **base_ms.get("negative", {})}
    target_metrics = {**target_ms.get("positive", {}), **target_ms.get("negative", {})}

    rows: list[dict[str, Any]] = []
    for metric in sorted(set(base_metrics) | set(target_metrics)):
        b_num, t_num = _num(base_metrics.get(metric)), _num(target_metrics.get(metric))
        delta = round(t_num - b_num, 6) if b_num is not None and t_num is not None else None
        rows.append(
            {
                "metric": metric,
                "base": b_num if b_num is not None else base_metrics.get(metric),
                "target": t_num if t_num is not None else target_metrics.get(metric),
                "delta": delta,
                "direction": _direction(delta, metric in _METRIC_REVERSE_KEYS),
            }
        )
    return rows


def _build_conclusion(base: DiagnosisRun, target: DiagnosisRun) -> dict[str, Any]:
    b_conf = float(base.primary_confidence) if base.primary_confidence is not None else None
    t_conf = float(target.primary_confidence) if target.primary_confidence is not None else None
    conf_delta = round(t_conf - b_conf, 4) if b_conf is not None and t_conf is not None else None
    return {
        "primaryCategory": {
            "base": base.primary_category,
            "target": target.primary_category,
            "delta": (None if base.primary_category == target.primary_category else "changed"),
        },
        "severity": {"base": base.severity, "target": target.severity},
        "confidence": {"base": b_conf, "target": t_conf, "delta": conf_delta},
    }


async def compare_runs(
    db: AsyncSession,
    run_id: str,
    mode: str = "adjacent",
) -> dict[str, Any]:
    """F2 复诊对比（16 号文 §4 F2 / D3 双模式）。

    - adjacent：target=当前 run，base=同回路之前最近一条 SUCCESS/PARTIAL run（纯诊断域恒可用）
    - verify：当前 run 须被 handling_order.verify_run_id 关联（处置启用才查）；
      base=工单来源建议对应的 run（无来源建议时回退相邻前序）
    """
    if mode not in ("adjacent", "verify"):
        raise BizError(
            code="ERR_PARAM", message=f"mode 仅支持 adjacent/verify: {mode}", status_code=400
        )
    target = await _load_run_or_404(db, run_id)

    # verifyPair：当前 run 是否为处置验证复诊 run（前端据此显隐"验证对比"入口）
    verify_pair = False
    if is_module_enabled("handling"):
        verify_pair = (
            await db.execute(
                select(HandlingOrder.id).where(HandlingOrder.verify_run_id == run_id).limit(1)
            )
        ).first() is not None

    base: DiagnosisRun | None = None
    if mode == "adjacent":
        base = await _adjacent_previous_run(db, target)
        if base is None:
            raise BizError(
                code="ERR_NOT_FOUND",
                message=f"该 run 之前无相邻 SUCCESS/PARTIAL 诊断记录: {run_id}",
                status_code=404,
            )
    else:
        if not is_module_enabled("handling"):
            raise BizError(
                code="ERR_NOT_FOUND",
                message=f"处置模块未启用，无验证对比数据: {run_id}",
                status_code=404,
            )
        order = (
            (
                await db.execute(
                    select(HandlingOrder).where(HandlingOrder.verify_run_id == run_id).limit(1)
                )
            )
            .scalars()
            .first()
        )
        if order is None:
            raise BizError(
                code="ERR_NOT_FOUND",
                message=f"该 run 未关联处置验证复诊记录: {run_id}",
                status_code=404,
            )
        # 处置前 run：来源建议（suggestion_ids）回溯其所属诊断 run，取最新一条；
        # MANUAL 工单/建议已删时回退相邻前序（同 adjacent 口径）
        suggestion_ids = [s for s in (order.suggestion_ids or []) if isinstance(s, str)]
        if suggestion_ids:
            base = (
                (
                    await db.execute(
                        select(DiagnosisRun)
                        .join(LoopActionItem, LoopActionItem.run_id == DiagnosisRun.id)
                        .where(LoopActionItem.id.in_(suggestion_ids))
                        .order_by(DiagnosisRun.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        if base is None:
            base = await _adjacent_previous_run(db, target)
        if base is None:
            raise BizError(
                code="ERR_NOT_FOUND",
                message=f"该 run 无可对比的前序诊断记录: {run_id}",
                status_code=404,
            )

    return {
        "mode": mode,
        "base": _compare_run_brief(base),
        "target": _compare_run_brief(target),
        "conclusion": _build_conclusion(base, target),
        "features": _build_feature_rows(base, target),
        "kpi": _build_kpi_rows(base, target),
        "verifyPair": verify_pair,
    }


# ---------------------------------------------------------------------------
# F3 诊断覆盖台账与新鲜度
# ---------------------------------------------------------------------------

#: 新鲜度分档（按回路最新 SUCCESS 诊断时间，口径 COALESCE(finished_at, created_at)，
#: 与 runs/latest 一致）。bucket 顺序即展示顺序；never=无任何 SUCCESS run。
_FRESHNESS_BUCKET_KEYS = ("within24h", "within7d", "within30d", "stale", "never")

#: 调度滞后阈值（小时）：与 tasks/diagnosis_schedule.py 排程节奏同一口径——
#: 1 级每日 01:10（24h+1h 宽限）；2 级每周日 02:10（7d+1h 宽限）；3 级不排程。
_SCHEDULE_LAG_THRESHOLD_HOURS: dict[int, int] = {1: 25, 2: 7 * 24 + 1}
_SCHEDULED_LEVELS = (1, 2)
_SCHEDULE_CADENCE = {
    1: ("daily", "每日"),
    2: ("weekly", "每周"),
    3: ("manual", "不排程，仅手动"),
}

#: 数据不足 TopN：近 30d DATA_INSUFFICIENT 占比最高的回路
_DI_WINDOW_DAYS = 30
_DI_TOP_N = 5

#: 8 类原因分类代码（F4 入参校验域）
_VALID_CATEGORIES = frozenset(
    {
        "TUNING",
        "VALVE",
        "INSTRUMENT",
        "COMMUNICATION",
        "PROCESS",
        "UTILIZATION",
        "DESIGN",
        "DATA_INSUFFICIENT",
    }
)


def _freshness_bucket(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "never"
    age = (now - ts).total_seconds()
    if age <= 24 * 3600:
        return "within24h"
    if age <= 7 * 24 * 3600:
        return "within7d"
    if age <= 30 * 24 * 3600:
        return "within30d"
    return "stale"


async def coverage(db: AsyncSession, *, include_schedule: bool) -> dict[str, Any]:
    """F3 诊断覆盖台账（16 号文 §4 F3 / §5.1）。

    - 新鲜度分档：活跃回路（is_active，与 runs/latest 台账口径一致）按最新
      SUCCESS 诊断时间分 5 档，附回路清单供悬浮/下钻
    - 调度执行（仅 ADMIN，include_schedule=False 时整段为 None，前端隐藏）：
      1/2 级应跑回路（importance_level + status=READY + is_active，与
      diagnosis_schedule._loops_by_importance 同一判定）vs 最近一次
      SCHEDULED run；超阈值或从未排程计入滞后；3 级标注"不排程，仅手动"
    - 数据不足 Top5：近 30d DATA_INSUFFICIENT 占比最高（提示先补数据）
    """
    now = _utcnow_naive()

    # 活跃回路全集（台账口径）
    loops = list(
        (
            await db.execute(
                select(
                    LoopLedger.id,
                    LoopLedger.tag_name,
                    LoopLedger.importance_level,
                    LoopLedger.status,
                ).where(LoopLedger.is_active.is_(True))
            )
        ).all()
    )
    tag_of = {row.id: row.tag_name for row in loops}

    # 每回路最新 SUCCESS 诊断时间
    latest_success = dict(
        (
            await db.execute(
                select(
                    DiagnosisRun.loop_id,
                    func.max(func.coalesce(DiagnosisRun.finished_at, DiagnosisRun.created_at)),
                )
                .where(DiagnosisRun.status == "SUCCESS")
                .group_by(DiagnosisRun.loop_id)
            )
        ).all()
    )

    bucket_loops: dict[str, list[dict[str, Any]]] = {k: [] for k in _FRESHNESS_BUCKET_KEYS}
    for row in loops:
        last = latest_success.get(row.id)
        bucket_loops[_freshness_bucket(last, now)].append(
            {"loopId": row.id, "loopTagName": row.tag_name, "lastDiagnosedAt": _iso(last)}
        )
    for items in bucket_loops.values():
        items.sort(key=lambda x: x["loopTagName"] or "")
    freshness = {
        "totalLoops": len(loops),
        "generatedAt": _iso(now),
        "buckets": [
            {"key": k, "count": len(bucket_loops[k]), "loops": bucket_loops[k]}
            for k in _FRESHNESS_BUCKET_KEYS
        ],
    }

    # 调度执行（仅 ADMIN；非管理员整段 None，前端隐藏而非置灰）
    schedule: dict[str, Any] | None = None
    if include_schedule:
        latest_scheduled = dict(
            (
                await db.execute(
                    select(DiagnosisRun.loop_id, func.max(DiagnosisRun.created_at))
                    .where(DiagnosisRun.trigger_type == "SCHEDULED")
                    .group_by(DiagnosisRun.loop_id)
                )
            ).all()
        )
        levels: list[dict[str, Any]] = []
        for level in (1, 2, 3):
            cadence, cadence_label = _SCHEDULE_CADENCE[level]
            # 与 diagnosis_schedule._loops_by_importance 同一判定：READY + active
            expected = [
                row for row in loops if row.importance_level == level and row.status == "READY"
            ]
            item: dict[str, Any] = {
                "level": level,
                "cadence": cadence,
                "cadenceLabel": cadence_label,
                "expectedLoops": len(expected),
            }
            if level in _SCHEDULED_LEVELS:
                threshold_h = _SCHEDULE_LAG_THRESHOLD_HOURS[level]
                lagging: list[dict[str, Any]] = []
                last_any: datetime | None = None
                for row in expected:
                    last = latest_scheduled.get(row.id)
                    if last is not None and (last_any is None or last > last_any):
                        last_any = last
                    if last is None or (now - last).total_seconds() > threshold_h * 3600:
                        lagging.append(
                            {
                                "loopId": row.id,
                                "loopTagName": row.tag_name,
                                "lastScheduledAt": _iso(last),
                            }
                        )
                lagging.sort(key=lambda x: x["loopTagName"] or "")
                item.update(
                    {
                        "lagThresholdHours": threshold_h,
                        "lastScheduledAt": _iso(last_any),
                        "laggingCount": len(lagging),
                        "lagging": lagging,
                    }
                )
            else:
                item["note"] = "3 级回路不排程，仅手动/事件触发"
            levels.append(item)
        schedule = {"levels": levels}

    # 数据不足 Top5（近 30d，DATA_INSUFFICIENT 占比降序）
    di_rows = list(
        (
            await db.execute(
                select(
                    DiagnosisRun.loop_id,
                    func.count(DiagnosisRun.id),
                    func.count(DiagnosisRun.id).filter(
                        DiagnosisRun.primary_category == "DATA_INSUFFICIENT"
                    ),
                )
                .where(DiagnosisRun.created_at >= now - timedelta(days=_DI_WINDOW_DAYS))
                .group_by(DiagnosisRun.loop_id)
            )
        ).all()
    )
    di_items = [
        {
            "loopId": loop_id,
            "loopTagName": tag_of.get(loop_id),
            "totalRuns": int(total),
            "insufficientRuns": int(di_count),
            "ratio": round(int(di_count) / int(total), 4) if total else 0.0,
        }
        for loop_id, total, di_count in di_rows
        if di_count and total
    ]
    di_items.sort(key=lambda x: (-x["ratio"], -x["insufficientRuns"], x["loopTagName"] or ""))

    return {
        "freshness": freshness,
        "schedule": schedule,
        "dataInsufficient": {
            "windowDays": _DI_WINDOW_DAYS,
            "top": di_items[:_DI_TOP_N],
        },
    }


# ---------------------------------------------------------------------------
# F4 共性问题回路组对比
# ---------------------------------------------------------------------------


async def category_cohort(
    db: AsyncSession,
    category: str,
    plant_node_id: str | None = None,
) -> dict[str, Any]:
    """F4 共性问题回路组（16 号文 §4 F4 / §5.1）。

    该分类×装置下"最新一条 run 主分类 = category"的活跃回路清单（每回路
    最新结论/置信度/严重度/metric_summary/最近诊断时间），供前端勾选
    2~3 回路并排雷达对比。
    latest-per-loop 查询泛化自 runs/latest（同一 LATERAL 取数、同一时间
    口径 COALESCE(finished_at, created_at)、同一 is_active 台账范围），
    避免两套"最新 run"口径。
    """
    if category not in _VALID_CATEGORIES:
        raise BizError(
            code="ERR_PARAM",
            message=f"未知原因分类: {category}（可用: {sorted(_VALID_CATEGORIES)}）",
            status_code=400,
        )
    if plant_node_id is not None:
        try:
            UUID(plant_node_id)
        except ValueError:
            raise BizError(
                code="ERR_PARAM",
                message="plantNodeId 格式非法（应为 UUID）",
                status_code=400,
            ) from None

    # 装置节点递归下钻到单元（与 runs/latest 同一 node_tree 口径）
    cte = (
        "WITH RECURSIVE node_tree AS ("
        "SELECT id FROM plant_node WHERE id = :root_id "
        "UNION ALL "
        "SELECT child.id FROM plant_node child "
        "JOIN node_tree nt ON child.parent_id = nt.id) "
        if plant_node_id
        else ""
    )
    conditions = ["ll.is_active = true", "r.primary_category = :category"]
    if plant_node_id:
        conditions.append("ll.unit_id IN (SELECT id FROM node_tree)")

    sql = text(
        f"""
        {cte}
        SELECT ll.id AS loop_id, ll.tag_name, ll.description AS loop_description,
               ll.importance_level,
               r.id AS run_id, r.primary_confidence, r.severity,
               COALESCE(r.finished_at, r.created_at) AS last_diagnosed_at,
               r.metric_summary
        FROM loop_ledger ll
        JOIN LATERAL (
                SELECT * FROM diagnosis_run dr
                WHERE dr.loop_id = ll.id
                ORDER BY dr.created_at DESC LIMIT 1
            ) r ON true
            WHERE {" AND ".join(conditions)}
            ORDER BY last_diagnosed_at DESC
            """
    )
    params: dict[str, str] = {"category": category}
    if plant_node_id:
        params["root_id"] = plant_node_id

    rows = list((await db.execute(sql, params)).all())
    items = [
        {
            "loopId": str(r.loop_id),
            "loopTagName": r.tag_name,
            "loopDescription": r.loop_description,
            "importanceLevel": int(r.importance_level) if r.importance_level else None,
            "runId": str(r.run_id),
            "primaryConfidence": float(r.primary_confidence)
            if r.primary_confidence is not None
            else None,
            "severity": r.severity,
            "lastDiagnosedAt": r.last_diagnosed_at.isoformat() if r.last_diagnosed_at else None,
            "metricSummary": r.metric_summary,
        }
        for r in rows
    ]
    return {
        "category": category,
        "plantNodeId": plant_node_id,
        "items": items,
        "total": len(items),
    }


# ---------------------------------------------------------------------------
# F5 发起前数据充足性预检徽标
# ---------------------------------------------------------------------------

#: 预检时间窗 → 预期快照行数（kpi_snapshot_hourly 每小时 1 行）
_PRECHECK_WINDOWS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}

#: 密度分级阈值（§4 F5 / D1=a 廉价代理）：
#: 红档下限与调度侧密度门禁 tasks/diagnosis_schedule._DENSITY_THRESHOLD=0.5 同口径
#: （低于 50% 预期的窗口跑诊断大概率 DATA_INSUFFICIENT）；
#: 琥珀档（50%~90%）为徽标特有的"刚过门禁但余量不足"提示带。
_PRECHECK_INSUFFICIENT_RATIO = 0.5
_PRECHECK_MARGINAL_RATIO = 0.9

#: 批量上限（§5.3：与工作台单次勾选对齐，单次 PostgreSQL 聚合查询）
_PRECHECK_MAX_LOOPS = 10


def _precheck_level(ratio: float) -> str:
    if ratio < _PRECHECK_INSUFFICIENT_RATIO:
        return "insufficient"
    if ratio < _PRECHECK_MARGINAL_RATIO:
        return "marginal"
    return "sufficient"


async def precheck(
    db: AsyncSession,
    loop_ids: list[str],
    window: str = "24h",
) -> dict[str, Any]:
    """F5 发起前数据充足性预检（16 号文 §4 F5 / §5.1，D1=a 廉价代理）。

    - 数据源：kpi_snapshot_hourly 行数密度（PostgreSQL 单次聚合，零 TDengine）
    - 分级：充足 sufficient / 疑似不足 marginal / 不足 insufficient / 无评估数据 unknown
    - 降级（§5.4）：评估模块禁用 → assessEnabled=false 且不发查询（前端整列隐藏徽标）；
      回路无任何快照 → unknown 中性态（数据源缺失，非数据质量结论，不得误报"不足"）
    - 事前提示定位：不替代发起时 fitness 门禁（L0/L1 阻止、L2 警告，行为不变）
    """
    hours = _PRECHECK_WINDOWS.get(window)
    if hours is None:
        raise BizError(
            code="ERR_PARAM",
            message=f"window 仅支持 {sorted(_PRECHECK_WINDOWS)}: {window}",
            status_code=400,
        )
    if not loop_ids:
        raise BizError(code="ERR_PARAM", message="loopIds 不能为空", status_code=400)
    if len(loop_ids) > _PRECHECK_MAX_LOOPS:
        raise BizError(
            code="ERR_PARAM",
            message=f"单次预检回路数不超过 {_PRECHECK_MAX_LOOPS}（与发起上限一致）",
            status_code=400,
        )
    for lid in loop_ids:
        try:
            UUID(lid)
        except ValueError:
            raise BizError(
                code="ERR_PARAM", message=f"loopId 格式非法（应为 UUID）: {lid}", status_code=400
            ) from None

    now = _utcnow_naive()
    resp: dict[str, Any] = {
        "window": window,
        "expectedRows": hours,
        "assessEnabled": is_module_enabled("assess"),
        "generatedAt": _iso(now),
        "items": [],
    }
    if not resp["assessEnabled"]:
        return resp  # 评估禁用：跳过查询段，前端整列隐藏徽标（隐藏而非置灰）

    window_start = now - timedelta(hours=hours)
    rows = list(
        (
            await db.execute(
                select(
                    KpiSnapshotHourly.loop_id,
                    func.count(KpiSnapshotHourly.ts_start).filter(
                        KpiSnapshotHourly.ts_start >= window_start
                    ),
                    func.count(KpiSnapshotHourly.ts_start),
                )
                .where(KpiSnapshotHourly.loop_id.in_(loop_ids))
                .group_by(KpiSnapshotHourly.loop_id)
            )
        ).all()
    )
    stats = {str(lid): (int(window_rows), int(total_rows)) for lid, window_rows, total_rows in rows}

    items: list[dict[str, Any]] = []
    for lid in loop_ids:
        window_rows, total_rows = stats.get(lid, (0, 0))
        if total_rows == 0:
            # 数据源缺失（评估从未产出该回路快照）→ 中性态，不误报"不足"
            items.append(
                {
                    "loopId": lid,
                    "level": "unknown",
                    "rowCount": 0,
                    "expectedRows": hours,
                    "ratio": None,
                }
            )
            continue
        ratio = round(window_rows / hours, 4)
        items.append(
            {
                "loopId": lid,
                "level": _precheck_level(ratio),
                "rowCount": window_rows,
                "expectedRows": hours,
                "ratio": ratio,
            }
        )
    resp["items"] = items
    return resp


# ---------------------------------------------------------------------------
# F6 复核反馈统计与阈值调优提示
# ---------------------------------------------------------------------------

#: D4：最小样本 ≥10 次（不足显示"样本不足"占位，不给出误导性比例）
_REVIEW_SAMPLE_MIN = 10

#: 阈值调优提示线（§4 F6.3：算子改判率 > 40% 标琥珀 + "建议复核阈值"入口；
#: 仅提示，调参走现有四级阈值覆盖人工操作——平台安全边界红线，不自动调参）
_REVIEW_OVERTURN_HINT = 0.4

#: 改判去向分布 TopN
_OVERTURN_TOP_N = 3

#: 症状标签 → 原因分类（与 classification 决策表同一映射口径；
#: OSCILLATION 在决策表中条件性归 PROCESS，此处取静态主映射）
_SYMPTOM_TO_CATEGORY: dict[str, str] = {
    "LINK_ABNORMAL": "COMMUNICATION",
    "QUALITY_ABNORMAL": "INSTRUMENT",
    "VALVE_STICTION": "VALVE",
    "OUTPUT_SATURATION": "VALVE",
    "EXTERNAL_DISTURBANCE": "PROCESS",
    "OSCILLATION": "PROCESS",
    "OVERAGGRESSIVE": "TUNING",
    "OVERCONSERVATIVE": "TUNING",
}

#: 分类展示顺序（与 ck_diagnosis_run_category 约束顺序一致）
_CATEGORY_ORDER = (
    "TUNING",
    "VALVE",
    "INSTRUMENT",
    "COMMUNICATION",
    "PROCESS",
    "UTILIZATION",
    "DESIGN",
    "DATA_INSUFFICIENT",
)


def _judgement_categories(judgements: Any) -> set[str]:
    """secondary_categories / pending_review（list[dict]）→ 分类代码集合。"""
    out: set[str] = set()
    for j in judgements or []:
        if isinstance(j, dict):
            c = j.get("category")
            if isinstance(c, str):
                out.add(c)
    return out


def _review_categories(review_results: Any) -> set[str]:
    """review_results（list[str] 分类代码）→ 集合。"""
    return {c for c in (review_results or []) if isinstance(c, str)}


def _overturn_top(counter: Counter) -> list[dict[str, Any]]:
    return [{"category": c, "count": int(n)} for c, n in counter.most_common(_OVERTURN_TOP_N)]


def _finalize_feedback_row(
    detected: int,
    reviewed: int,
    sample: int,
    confirmed: int,
    overturned: int,
    overturn_counter: Counter,
) -> dict[str, Any]:
    """D4 样本门槛收口：sample < 10 → insufficientSample=true，比例/去向置空不误导。"""
    insufficient = sample < _REVIEW_SAMPLE_MIN
    return {
        "detectedCount": detected,
        "reviewedCount": reviewed,
        "reviewRate": round(reviewed / detected, 4) if detected else None,
        "sampleSize": sample,
        "insufficientSample": insufficient,
        "confirmRate": None if insufficient else round(confirmed / sample, 4),
        "overturnRate": None if insufficient else round(overturned / sample, 4),
        "overturnTop": [] if insufficient else _overturn_top(overturn_counter),
        "tuningHint": (not insufficient) and (overturned / sample) > _REVIEW_OVERTURN_HINT,
    }


async def review_feedback(db: AsyncSession) -> dict[str, Any]:
    """F6 复核反馈统计（16 号文 §4 F6 / §5.1，D4 样本门槛 ≥10）。

    口径（§5.1 实现要点 + §4 F6.4 常驻小字）：
    - 统计范围：全量 diagnosis_run（MVP 量级可控，§5.3）；改判 = 复核结论不含机器分类
    - 分类维度：检出 = 机器主分类命中（primary_category == C）；样本 = 其中已复核数
    - 算子维度：只统计 executed=true 且 detected=true 的算子；按症状标签映射分类 C(O)
      归因——机器采纳 C(O)（主/次分类含 C(O)）且已复核才计入改判分母；
      pending_review 命中 C(O) 的 run 不计入改判分母（机器已主动降级为待复核，
      人工不确认不算误报）；机器未采纳 C(O) 的检出无从改判，同样不入分母
    - 阈值调优提示：tuningHint=true 仅表示"建议复核阈值"，不含任何自动调参动作
    """
    rows = list(
        (
            await db.execute(
                select(
                    DiagnosisRun.primary_category,
                    DiagnosisRun.secondary_categories,
                    DiagnosisRun.pending_review,
                    DiagnosisRun.review_status,
                    DiagnosisRun.review_results,
                    DiagnosisRun.operator_results,
                )
            )
        ).all()
    )

    operators = list_operators()
    op_meta: list[dict[str, Any]] = []
    for meta in operators:
        symptoms = meta.get("symptomTags") or []
        op_meta.append(
            {
                "operator": meta["name"],
                "displayName": meta["displayName"],
                "family": meta["family"],
                "diagCode": meta["diagCode"],
                "category": _SYMPTOM_TO_CATEGORY.get(symptoms[0]) if symptoms else None,
            }
        )

    cat_agg: dict[str, dict[str, Any]] = {
        c: {"detected": 0, "reviewed": 0, "confirmed": 0, "overturned": 0, "top": Counter()}
        for c in _CATEGORY_ORDER
    }
    op_agg: dict[str, dict[str, Any]] = {
        m["operator"]: {
            "detected": 0,
            "reviewed": 0,
            "pending_excluded": 0,
            "sample": 0,
            "confirmed": 0,
            "overturned": 0,
            "top": Counter(),
        }
        for m in op_meta
    }

    reviewed_runs = 0
    for primary, secondary, pending, review_status, review_results, operator_results in rows:
        reviewed = review_status == "REVIEWED"
        if reviewed:
            reviewed_runs += 1
        review_cats = _review_categories(review_results)
        secondary_cats = _judgement_categories(secondary)
        pending_cats = _judgement_categories(pending)

        # ---- 分类维度（机器主分类） ----
        if primary in cat_agg:
            agg = cat_agg[primary]
            agg["detected"] += 1
            if reviewed:
                agg["reviewed"] += 1
                if primary in review_cats:
                    agg["confirmed"] += 1
                else:
                    agg["overturned"] += 1
                    agg["top"].update(review_cats)

        # ---- 算子维度（executed + detected，按映射分类归因） ----
        op_results = operator_results or {}
        for m in op_meta:
            res = op_results.get(m["operator"])
            if not isinstance(res, dict) or not res.get("executed") or not res.get("detected"):
                continue
            agg = op_agg[m["operator"]]
            agg["detected"] += 1
            if not reviewed:
                continue
            agg["reviewed"] += 1
            category = m["category"]
            if category is None:
                continue
            if category in pending_cats:
                agg["pending_excluded"] += 1  # pending_review 命中不计入改判分母
                continue
            if category != primary and category not in secondary_cats:
                continue  # 机器未采纳该分类，无可改判的结论
            agg["sample"] += 1
            if category in review_cats:
                agg["confirmed"] += 1
            else:
                agg["overturned"] += 1
                agg["top"].update(review_cats)

    return {
        "generatedAt": _iso(_utcnow_naive()),
        "sampleMin": _REVIEW_SAMPLE_MIN,
        "overturnHintThreshold": _REVIEW_OVERTURN_HINT,
        "totalRuns": len(rows),
        "reviewedRuns": reviewed_runs,
        "operators": [
            {
                **{k: m[k] for k in ("operator", "displayName", "family", "diagCode", "category")},
                "pendingExcludedCount": op_agg[m["operator"]]["pending_excluded"],
                **_finalize_feedback_row(
                    op_agg[m["operator"]]["detected"],
                    op_agg[m["operator"]]["reviewed"],
                    op_agg[m["operator"]]["sample"],
                    op_agg[m["operator"]]["confirmed"],
                    op_agg[m["operator"]]["overturned"],
                    op_agg[m["operator"]]["top"],
                ),
            }
            for m in op_meta
        ],
        "categories": [
            {
                "category": c,
                **_finalize_feedback_row(
                    cat_agg[c]["detected"],
                    cat_agg[c]["reviewed"],
                    cat_agg[c]["reviewed"],
                    cat_agg[c]["confirmed"],
                    cat_agg[c]["overturned"],
                    cat_agg[c]["top"],
                ),
            }
            for c in _CATEGORY_ORDER
        ],
    }
