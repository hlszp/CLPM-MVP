"""诊断洞察只读聚合服务（16 号文 Phase A：F1 回路诊断档案 + F2 复诊对比）。

设计文档：docs/MVP设计/16-诊断模块功能扩展方案.md §4 F1/F2、§5.1、§5.4
- 零迁移纯查询：不新增 ORM 模型，不修改 diagnosis_orchestrator.py 主链路
- import 纪律（§1.4 P3）：允许 import 处置/整定域 ORM 模型（物理表恒在），
  禁止 import 其他模块 service 层；跨模块查询段前必须先判
  ``is_module_enabled("handling"/"tuning")``，禁用时跳过查询并在响应标记
  能力字段（前端据此隐藏图层/入口，而非置灰）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.modules import is_module_enabled
from app.models.diagnosis_run import DiagnosisRun
from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger
from app.models.loop_action_item import LoopActionItem
from app.models.metric import KpiSnapshotHourly
from app.models.tuning import TuningRecord
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
