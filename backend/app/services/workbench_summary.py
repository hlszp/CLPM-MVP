"""工作台摘要聚合服务（整改方案 §8.2 / §7.2 / §7.3）。

``GET /api/v1/monitor/loops/{loopId}/summary`` 的后端实现，一次返回工作台首屏
所需的全部摘要：回路基本信息、运行态、数据新鲜度、数据健康度、评分趋势、
当前回路活跃关注项、最新评估/诊断/整定摘要、Tracker/实施/验证时间线、
五阶段生命周期、推荐下一步 ``nextAction``。

设计约束（MW-P3-01 ~ MW-P3-04）：
- 复用现有服务查询，不复制算法计算。
- 摘要禁止返回趋势数组、FFT 点、仿真曲线等大数据。
- 单个来源失败时返回 ``partial=true`` 和 ``unavailableSections``，不让整页 500。
- 权限与当前工作台一致：ADMIN/IC/PE/EXPERT 可读，PE 返回同结构但所有写动作 disabled；
  Sponsor 固定返回 403（前端不发起该请求）。
- ``dataFreshness`` 由服务端计算，复用实时链路停滞配置
  （``SIGNALR_STALL_TIMEOUT_SECONDS``），前端不复制常量。
- 生命周期五阶段状态构建（方案 §7.2）。
- ``nextAction`` 按方案 §7.3 顺序输出唯一主动作，按角色过滤 enabled。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.diagnosis import DiagnosisResult, DiagnosisTask
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import (
    KpiSnapshotHourly,
    LoopConfidenceLatest,
    LoopIntegritySnapshot,
)
from app.models.plant_node import PlantNode
from app.models.tag import TagRegistry
from app.models.tracker import ActionTracker
from app.models.tuning import TuningRecord
from app.services.data_source.realtime_subscriber import get_subscriber
from app.services.monitor_attention import VERIFICATION_PERIOD_HOURS

logger = logging.getLogger(__name__)

#: 验证周期（与 monitor_attention 对齐）
_VERIFY_HOURS = VERIFICATION_PERIOD_HOURS

#: 评分恶化阈值（与 monitor_attention 对齐，用于 nextAction 判定）
_SCORE_DELTA_DEGRADATION = -2


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _to_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _iso(val: datetime | None) -> str | None:
    if val is None:
        return None
    if val.tzinfo is None:
        return val.replace(tzinfo=UTC).isoformat()
    return val.isoformat()


def _quality_code_to_label(q: Any) -> str | None:
    """质量码 → 标签（与 monitor.py 对齐，简化版）。"""
    if q is None:
        return None
    if isinstance(q, str):
        if q in ("GOOD", "BAD", "UNCERTAIN"):
            return q
        try:
            q = int(q)
        except (ValueError, TypeError):
            return "UNCERTAIN"
    if isinstance(q, (int, float)):
        if q in (1, 2, 3, 192):
            return "GOOD"
        if q == 0:
            return "BAD"
        return "UNCERTAIN"
    return "UNCERTAIN"


# ---------------------------------------------------------------------------
# 运行态 & 数据新鲜度
# ---------------------------------------------------------------------------


async def _build_runtime(
    db: AsyncSession, loop: LoopLedger
) -> tuple[dict, dict[str, TagRegistry], dict[str, LoopTagMapping]]:
    """构建运行态（PV/SP/OP/MODE + readAt + 质量码）。

    复用 Redis 实时缓存 + Tag 注册表，与 monitor.py 口径一致。
    返回 (runtime_dict, tags_map, mappings) 供后续阶段复用。
    """
    # 查 Tag 关联
    m_result = await db.execute(
        select(LoopTagMapping).where(LoopTagMapping.loop_id == str(loop.id))
    )
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    # 从 Redis 读实时值
    redis_cache: dict[str, dict] = {}
    try:
        subscriber = get_subscriber()
        all_tag_names = [tag.tag_name for tag in tags_map.values() if tag.tag_name]
        if all_tag_names:
            cached_list = await subscriber.get_cached_values(all_tag_names)
            for item in cached_list:
                tc = item.get("tagCode")
                if tc:
                    redis_cache[tc] = item
    except Exception as exc:  # noqa: BLE001
        logger.warning("summary: 从 Redis 读取实时值失败: %s", exc)

    runtime: dict[str, Any] = {
        "pv": None,
        "sp": None,
        "op": None,
        "mode": None,
        "modeLabel": None,
        "pvQuality": None,
        "pvUnit": None,
        "pvRange": None,
        "opRange": None,
        "readAt": None,
        "controlMode": None,
    }
    read_at: str | None = None

    for role in ("PV", "SP", "OP", "MODE"):
        mapping = mappings.get(role)
        if not mapping or str(mapping.tag_id) not in tags_map:
            continue
        tag = tags_map[str(mapping.tag_id)]
        field = role.lower()
        cached = redis_cache.get(tag.tag_name)
        if cached:
            try:
                runtime[field] = float(cached.get("value"))
            except (TypeError, ValueError):
                runtime[field] = tag.current_value
            if role == "PV":
                runtime["pvQuality"] = _quality_code_to_label(cached.get("quality", tag.quality))
            if cached.get("collectTime"):
                ct = cached["collectTime"]
                if read_at is None or ct > read_at:
                    read_at = ct
        else:
            runtime[field] = tag.current_value
            if role == "PV":
                runtime["pvQuality"] = _quality_code_to_label(tag.quality)
            if tag.last_sync_at:
                ts = (
                    tag.last_sync_at.isoformat()
                    if hasattr(tag.last_sync_at, "isoformat")
                    else str(tag.last_sync_at)
                )
                if read_at is None or ts > read_at:
                    read_at = ts
        if role == "PV":
            if tag.unit:
                runtime["pvUnit"] = tag.unit
            runtime["pvRange"] = {
                "min": float(tag.range_min) if tag.range_min is not None else None,
                "max": float(tag.range_max) if tag.range_max is not None else None,
            }
        elif role == "OP":
            runtime["opRange"] = {
                "min": float(tag.range_min) if tag.range_min is not None else None,
                "max": float(tag.range_max) if tag.range_max is not None else None,
            }

    runtime["modeLabel"] = _mode_value_to_label(runtime["mode"])
    runtime["controlMode"] = runtime["modeLabel"]
    runtime["readAt"] = read_at

    return runtime, tags_map, mappings


def _mode_value_to_label(value: Any) -> str | None:
    """MODE 值 → 标签（默认映射，与 monitor.py 一致；权威映射走 REST 配置）。"""
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "Unknown"
    return {0: "Manual", 1: "Auto", 2: "Cascade", 3: "Cascade"}.get(v, "Unknown")


def _build_data_freshness(read_at: str | None) -> dict:
    """数据新鲜度（服务端计算，复用实时链路停滞配置）。

    ``thresholdSeconds`` = ``SIGNALR_STALL_TIMEOUT_SECONDS``；
    readAt 为空或解析失败 → UNKNOWN；超出阈值 → DELAYED；否则 FRESH。
    """
    threshold = settings.SIGNALR_STALL_TIMEOUT_SECONDS
    if not read_at:
        return {
            "status": "UNKNOWN",
            "thresholdSeconds": threshold,
            "reason": "无实时采样数据",
        }
    try:
        if isinstance(read_at, str):
            dt = datetime.fromisoformat(read_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        elif isinstance(read_at, datetime):
            dt = read_at if read_at.tzinfo else read_at.replace(tzinfo=UTC)
        else:
            return {
                "status": "UNKNOWN",
                "thresholdSeconds": threshold,
                "reason": "采样时间格式异常",
            }
        now = datetime.now(UTC)
        age = (now - dt).total_seconds()
        if age > threshold:
            return {
                "status": "DELAYED",
                "thresholdSeconds": threshold,
                "reason": f"数据已停滞 {int(age)} 秒（阈值 {threshold} 秒）",
            }
        return {
            "status": "FRESH",
            "thresholdSeconds": threshold,
            "reason": None,
        }
    except (ValueError, TypeError):
        return {
            "status": "UNKNOWN",
            "thresholdSeconds": threshold,
            "reason": "采样时间解析失败",
        }


# ---------------------------------------------------------------------------
# 配置完整性（MONITOR 阶段判定）
# ---------------------------------------------------------------------------


def _check_config_completeness(
    mappings: dict[str, LoopTagMapping], loop: LoopLedger
) -> tuple[bool, str | None]:
    """检查回路配置完整性（必填 7 Tag 是否齐全）。

    Returns:
        (is_complete, reason) — 不完整时 reason 说明缺失角色。
    """
    required_roles = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
    missing = required_roles - set(mappings.keys())
    if missing:
        return False, f"缺失必填 Tag：{', '.join(sorted(missing))}"
    if not loop.is_active:
        return False, "回路已停用"
    return True, None


# ---------------------------------------------------------------------------
# 评估 / 诊断 / 整定 摘要
# ---------------------------------------------------------------------------


async def _build_assessment_summary(db: AsyncSession, loop_id: str) -> dict | None:
    """最新评估摘要（不含趋势数组）。"""
    snap_result = await db.execute(
        select(KpiSnapshotHourly)
        .where(KpiSnapshotHourly.loop_id == loop_id)
        .order_by(KpiSnapshotHourly.ts_end.desc())
        .limit(1)
    )
    snap = snap_result.scalar_one_or_none()
    if not snap:
        return None

    # 昨日基线用于 scoreDelta（与 monitor.py 口径一致）
    prev_result = await db.execute(
        select(KpiSnapshotHourly)
        .where(
            KpiSnapshotHourly.loop_id == loop_id,
            KpiSnapshotHourly.ts_end < func.date_trunc("day", func.now()),
        )
        .order_by(KpiSnapshotHourly.ts_end.desc())
        .limit(1)
    )
    prev = prev_result.scalar_one_or_none()

    score = _to_float(snap.score)
    score_delta: float | None = None
    day_trend: str | None = None
    if score is not None:
        if prev is None or prev.score is None:
            day_trend = "NEW"
        else:
            score_delta = round(score - float(prev.score), 1)
            if score_delta <= -2:
                day_trend = "WORSENED"
            elif score_delta >= 2:
                day_trend = "IMPROVED"
            else:
                day_trend = "FLAT"

    # 一句话结论
    summary_text = f"综合评分 {score:.1f}" if score is not None else "无有效评分"
    if day_trend == "WORSENED":
        summary_text += f"，较昨日下降 {abs(score_delta):.1f}"
    elif day_trend == "IMPROVED":
        summary_text += f"，较昨日上升 {score_delta:.1f}"

    return {
        "score": score,
        "confidenceLevel": snap.confidence_level,
        "status": snap.status,
        "resultAt": _iso(snap.ts_end),
        "timeWindow": "latest_hourly",
        "summary": summary_text,
        # scoreDelta/dayTrend 同时进 scoreTrend
        "_scoreDelta": score_delta,
        "_dayTrend": day_trend,
    }


async def _build_diagnosis_summary(db: AsyncSession, loop_id: str) -> dict | None:
    """最新诊断摘要（不含证据链大对象）。"""
    # 最新诊断结果
    diag_result = await db.execute(
        select(DiagnosisResult)
        .where(DiagnosisResult.loop_id == loop_id)
        .order_by(DiagnosisResult.diagnosed_at.desc())
        .limit(1)
    )
    diag = diag_result.scalar_one_or_none()
    if not diag:
        return None

    # 最新诊断任务状态
    task_status: str | None = None
    task_id = diag.task_id
    if task_id:
        task_result = await db.execute(
            select(DiagnosisTask.status).where(DiagnosisTask.id == task_id)
        )
        task_status = task_result.scalar_one_or_none()

    labels: list[str] = []
    if task_id:
        labels_result = await db.execute(
            select(DiagnosisResult.diag_label).where(
                DiagnosisResult.loop_id == loop_id, DiagnosisResult.task_id == task_id
            )
        )
        labels = [r[0] for r in labels_result.all() if r[0]]
    elif diag.diag_label:
        labels = [diag.diag_label]

    confidence = _to_float(diag.confidence)
    label_text = diag.diag_label or "需人工复核"
    summary_text = f"诊断标签：{label_text}"
    if confidence is not None:
        summary_text += f"（可信度 {confidence:.0f}%）"

    return {
        "diagLabel": diag.diag_label,
        "confidence": confidence,
        "status": task_status or "SUCCESS",
        "resultAt": _iso(diag.diagnosed_at),
        "taskId": str(task_id) if task_id else None,
        "labels": labels,
        "summary": summary_text,
    }


async def _build_tuning_summary(db: AsyncSession, loop_id: str) -> dict | None:
    """最新整定摘要（不含仿真曲线点）。"""
    tune_result = await db.execute(
        select(TuningRecord)
        .where(TuningRecord.loop_id == loop_id)
        .order_by(TuningRecord.created_at.desc())
        .limit(1)
    )
    tune = tune_result.scalar_one_or_none()
    if not tune:
        return None

    risk_level: str | None = None
    if tune.risk_assessment and isinstance(tune.risk_assessment, dict):
        risk_level = tune.risk_assessment.get("risk_level")

    fitting = _to_float(tune.fitting_score)
    summary_text = f"整定状态：{tune.status}"
    if tune.model_type:
        summary_text += f"，模型 {tune.model_type}"
    if fitting is not None:
        summary_text += f"，拟合度 {fitting:.1f}"

    return {
        "status": tune.status,
        "modelType": tune.model_type,
        "algorithm": tune.algorithm,
        "confidenceLevel": tune.confidence_level,
        "resultAt": _iso(tune.completed_at or tune.created_at),
        "currentPid": tune.current_pid,
        "recommendedPid": tune.recommended_pid,
        "fittingScore": fitting,
        "riskLevel": risk_level,
        "summary": summary_text,
    }


# ---------------------------------------------------------------------------
# Tracker / 实施 / 验证 时间线
# ---------------------------------------------------------------------------


async def _build_tracker_timeline(
    db: AsyncSession,
    loop_id: str,
    *,
    tuning_current_pid: dict | None = None,
) -> dict | None:
    """最新开放 Tracker 及其实施/验证状态。

    优先返回开放态（PENDING/IN_PROGRESS/VERIFYING）的最新一条；
    无开放态则返回最近一次闭环（CLOSED/REOPENED）用于展示验证结论。

    ``tuning_current_pid`` 为实施前 PID 基线（来自最新整定记录的 current_pid），
    传入后用于 MW-P3-09 实施前后对比。
    """
    # 开放态优先
    open_result = await db.execute(
        select(ActionTracker)
        .where(
            ActionTracker.loop_id == loop_id,
            ActionTracker.action_status.in_(("PENDING", "IN_PROGRESS", "VERIFYING")),
        )
        .order_by(ActionTracker.created_at.desc())
        .limit(1)
    )
    tracker = open_result.scalar_one_or_none()

    if tracker is None:
        # 回退到最近一次闭环/重开
        closed_result = await db.execute(
            select(ActionTracker)
            .where(
                ActionTracker.loop_id == loop_id,
                ActionTracker.action_status.in_(("CLOSED", "REOPENED")),
            )
            .order_by(ActionTracker.updated_at.desc().nulls_last())
            .limit(1)
        )
        tracker = closed_result.scalar_one_or_none()

    if tracker is None:
        return None

    is_overdue = False
    overdue_hours: float | None = None
    if tracker.action_status == "VERIFYING":
        updated = tracker.updated_at or tracker.created_at
        if updated:
            now = datetime.now(UTC).replace(tzinfo=None)
            if (now - updated) > timedelta(hours=_VERIFY_HOURS):
                is_overdue = True
                overdue_hours = (now - updated).total_seconds() / 3600

    new_pid: dict | None = None
    if any(v is not None for v in (tracker.new_pid_p, tracker.new_pid_i, tracker.new_pid_d)):
        new_pid = {
            "p": tracker.new_pid_p,
            "i": tracker.new_pid_i,
            "d": tracker.new_pid_d,
        }

    return {
        "trackerId": str(tracker.id),
        "diagnosisLabel": tracker.diagnosis_label,
        "actionStatus": tracker.action_status,
        "severity": tracker.severity,
        "triggerType": tracker.trigger_type,
        "assignee": tracker.assignee,
        "createdAt": _iso(tracker.created_at),
        "updatedAt": _iso(tracker.updated_at),
        "implementedAt": _iso(tracker.implemented_at),
        "implementedBy": tracker.implemented_by,
        "newPid": new_pid,
        "mocRef": tracker.moc_ref,
        "mocNotApplicable": tracker.moc_not_applicable,
        "plannedAt": _iso(tracker.planned_at),
        "closedAt": _iso(tracker.closed_at),
        "effectVerified": tracker.effect_verified,
        "effectVerifiedAt": _iso(tracker.effect_verified_at),
        "abCompareSummary": tracker.ab_compare_summary,
        "effectCompare": _build_effect_compare(
            tracker=tracker,
            ab_compare_summary=tracker.ab_compare_summary,
            effect_verified=tracker.effect_verified,
            effect_verified_at=tracker.effect_verified_at,
            tuning_current_pid=tuning_current_pid,
        ),
        "reopenReason": tracker.reopen_reason,
        "isOverdue": is_overdue,
        "overdueHours": overdue_hours,
    }


# ---------------------------------------------------------------------------
# 实施前后对比（MW-P3-09，方案 §7.1 闭环时间线增强）
# ---------------------------------------------------------------------------


#: 实施前后对比中排除的核心 KPI（综合评分单独提取为 scoreChange）
_SCORE_METRIC_KEY = "score"

#: 核心 KPI 展示上限（排除综合评分后最多展示 4 项）
_MAX_CORE_KPI_ITEMS = 4


def _build_effect_compare(
    *,
    tracker: ActionTracker | None,
    ab_compare_summary: dict | None,
    effect_verified: bool | None,
    effect_verified_at: datetime | None,
    tuning_current_pid: dict | None,
) -> dict | None:
    """构建实施前后对比（MW-P3-09）。

    复用 tracker.ab_compare_summary 存储快照，不重复实现
    ``/tracker/effectiveness`` 计算逻辑。

    状态判定：
    - 无 Tracker 或无 implemented_at → None（不展示对比区）
    - 有 Tracker 但无 ab_compare_summary → PENDING（未到验证周期）
    - 有 ab_compare_summary 但 dataInsufficient=true → INCONCLUSIVE
    - 有 ab_compare_summary 且 dataInsufficient=false → COMPLETED

    无基线、窗口不足、可信度不足时返回 INCONCLUSIVE，不显示伪 0。
    """
    if tracker is None or not tracker.implemented_at:
        return None

    implemented_at = tracker.implemented_at
    if implemented_at.tzinfo is None:
        implemented_at = implemented_at.replace(tzinfo=UTC)

    # 时间窗：[T-7d, T) 与 (T, T+7d]（与 get_ab_compare 口径一致）
    before_start = (implemented_at - timedelta(days=7)).isoformat()
    before_end = implemented_at.isoformat()
    after_start = implemented_at.isoformat()
    after_end = (implemented_at + timedelta(days=7)).isoformat()

    pid_after: dict | None = None
    if any(v is not None for v in (tracker.new_pid_p, tracker.new_pid_i, tracker.new_pid_d)):
        pid_after = {
            "p": tracker.new_pid_p,
            "i": tracker.new_pid_i,
            "d": tracker.new_pid_d,
        }

    # 无 ab_compare_summary → PENDING
    if not ab_compare_summary:
        return {
            "status": "PENDING",
            "conclusion": None,
            "conclusionLabel": "待验证",
            "implementedAt": implemented_at.isoformat(),
            "verifiedAt": _iso(effect_verified_at),
            "timeWindow": {
                "beforeStart": before_start,
                "beforeEnd": before_end,
                "afterStart": after_start,
                "afterEnd": after_end,
            },
            "scoreChange": None,
            "coreKpiChanges": [],
            "pidBefore": tuning_current_pid,
            "pidAfter": pid_after,
            "dataInsufficient": False,
            "confidence": None,
            "reason": "实施后未到验证周期（T+7d），暂无对比数据",
        }

    data_insufficient = bool(ab_compare_summary.get("dataInsufficient", False))
    kpi_comparison: list[dict] = ab_compare_summary.get("kpiComparison", [])
    improved_count = int(ab_compare_summary.get("improvedCount", 0))
    deteriorated_count = int(ab_compare_summary.get("deterioratedCount", 0))

    # 提取评分变化（metricKey == "score"）
    score_item = next(
        (k for k in kpi_comparison if k.get("metricKey") == _SCORE_METRIC_KEY),
        None,
    )
    score_change: dict | None = None
    if score_item:
        score_change = {
            "before": score_item.get("before"),
            "after": score_item.get("after"),
            "change": score_item.get("change"),
            "improved": score_item.get("improved"),
        }

    # 提取核心 KPI 变化（排除综合评分，最多 4 项）
    core_items: list[dict] = []
    for kpi in kpi_comparison:
        if kpi.get("metricKey") == _SCORE_METRIC_KEY:
            continue
        core_items.append(
            {
                "metricKey": kpi.get("metricKey"),
                "metricName": kpi.get("metricName"),
                "before": kpi.get("before"),
                "after": kpi.get("after"),
                "change": kpi.get("change"),
                "improved": kpi.get("improved"),
            }
        )
        if len(core_items) >= _MAX_CORE_KPI_ITEMS:
            break

    # INCONCLUSIVE：数据不足
    if data_insufficient:
        return {
            "status": "INCONCLUSIVE",
            "conclusion": None,
            "conclusionLabel": "证据不足",
            "implementedAt": implemented_at.isoformat(),
            "verifiedAt": _iso(effect_verified_at),
            "timeWindow": {
                "beforeStart": before_start,
                "beforeEnd": before_end,
                "afterStart": after_start,
                "afterEnd": after_end,
            },
            "scoreChange": score_change,
            "coreKpiChanges": core_items,
            "pidBefore": tuning_current_pid,
            "pidAfter": pid_after,
            "dataInsufficient": True,
            "confidence": "INSUFFICIENT",
            "reason": "实施后窗口数据不足 24 小时，无法判定效果",
        }

    # COMPLETED：判定结论
    if improved_count > deteriorated_count:
        conclusion = "IMPROVED"
        conclusion_label = "改善"
        confidence = "HIGH" if improved_count >= 3 else "MEDIUM"
    elif deteriorated_count > improved_count:
        conclusion = "DETERIORATED"
        conclusion_label = "恶化"
        confidence = "HIGH" if deteriorated_count >= 3 else "MEDIUM"
    else:
        conclusion = "NO_CHANGE"
        conclusion_label = "无明显变化"
        confidence = "MEDIUM"

    return {
        "status": "COMPLETED",
        "conclusion": conclusion,
        "conclusionLabel": conclusion_label,
        "implementedAt": implemented_at.isoformat(),
        "verifiedAt": _iso(effect_verified_at),
        "timeWindow": {
            "beforeStart": before_start,
            "beforeEnd": before_end,
            "afterStart": after_start,
            "afterEnd": after_end,
        },
        "scoreChange": score_change,
        "coreKpiChanges": core_items,
        "pidBefore": tuning_current_pid,
        "pidAfter": pid_after,
        "dataInsufficient": False,
        "confidence": confidence,
        "reason": f"改善 {improved_count} 项 / 恶化 {deteriorated_count} 项",
    }


# ---------------------------------------------------------------------------
# 活跃关注项汇总（当前回路，最多 3 条）
# ---------------------------------------------------------------------------


async def _build_active_attention(db: AsyncSession, loop_id: str, role: str) -> dict:
    """当前回路活跃关注项汇总（复用 monitor_attention 聚合）。

    延迟导入避免循环依赖。复用 list_attention 按 loopId 精确筛选，
    取 total + highestPriority + 最多 3 条明细。
    """
    from app.services.monitor_attention import (
        _PRIORITY_ORDER,
        list_attention,
    )

    data = await list_attention(
        db=db,
        loop_id=loop_id,
        page=1,
        page_size=3,
        role=role,
    )
    items = data.get("items", [])
    total = data.get("total", 0)
    highest_priority: str | None = None
    if items:
        highest_priority = min(
            (i["priority"] for i in items),
            key=lambda p: _PRIORITY_ORDER.get(p, 9),
        )
    return {
        "total": total,
        "highestPriority": highest_priority,
        "items": items,
    }


# ---------------------------------------------------------------------------
# 生命周期构建器（MW-P3-02）
# ---------------------------------------------------------------------------


def _build_lifecycle(
    *,
    loop: LoopLedger,
    config_complete: bool,
    config_reason: str | None,
    runtime: dict,
    data_freshness: dict,
    data_health: dict,
    assessment: dict | None,
    diagnosis: dict | None,
    tuning: dict | None,
    tracker: dict | None,
) -> dict:
    """构建五阶段生命周期（方案 §7.2）。

    | 阶段 | 状态来源 | 完成判定 |
    | MONITOR | 回路配置态、运行值、数据健康度 | 必填 Tag 完整且存在运行态数据 |
    | ASSESS | 评估任务 + 最新 KPI 快照 | 最新任务成功且快照落在当前时间上下文内 |
    | DIAGNOSE | 诊断任务 + 最新诊断结果 | 诊断时间不早于当前评估结果 |
    | TUNE | 整定任务状态机 | IDENTIFIED/SIMULATED/COMPLETED；INCONCLUSIVE 单独表达 |
    | VERIFY | Action Tracker | CLOSED 完成；VERIFYING 超期 OVERDUE；无整改项 NOT_REQUIRED |
    """
    stages: list[dict] = []

    # --- MONITOR ---
    if not config_complete:
        monitor_status = "BLOCKED"
        monitor_reason = config_reason or "回路配置不完整"
    elif not runtime.get("readAt"):
        monitor_status = "NOT_STARTED"
        monitor_reason = "无运行态数据"
    elif data_freshness["status"] == "DELAYED":
        monitor_status = "OVERDUE"
        monitor_reason = data_freshness.get("reason") or "数据停滞"
    else:
        monitor_status = "READY"
        monitor_reason = "配置完整且存在运行态数据"
    stages.append(
        {
            "stage": "MONITOR",
            "status": monitor_status,
            "resultAt": runtime.get("readAt"),
            "reason": monitor_reason,
        }
    )

    # --- ASSESS ---
    if assessment is None:
        assess_status = "NOT_STARTED"
        assess_reason = "无评估快照"
    elif assessment.get("status") == "INCONCLUSIVE":
        assess_status = "INCONCLUSIVE"
        assess_reason = "评估数据不足"
    elif assessment.get("status") == "PARTIAL":
        assess_status = "INCONCLUSIVE"
        assess_reason = "评估部分指标缺失"
    else:
        assess_status = "COMPLETED"
        assess_reason = "评估完成"
    stages.append(
        {
            "stage": "ASSESS",
            "status": assess_status,
            "resultAt": assessment.get("resultAt") if assessment else None,
            "reason": assess_reason,
        }
    )

    # --- DIAGNOSE ---
    if diagnosis is None:
        diagnose_status = "NOT_STARTED"
        diagnose_reason = "无诊断结果"
    elif diagnosis.get("status") in ("RUNNING", "PENDING"):
        diagnose_status = "RUNNING"
        diagnose_reason = "诊断进行中"
    elif diagnosis.get("status") == "FAILED":
        diagnose_status = "BLOCKED"
        diagnose_reason = "诊断失败"
    elif diagnosis.get("status") == "CANCELLED":
        diagnose_status = "NOT_STARTED"
        diagnose_reason = "诊断已取消"
    else:
        # 同轴判定：诊断时间不早于当前评估结果
        diag_at = diagnosis.get("resultAt")
        assess_at = assessment.get("resultAt") if assessment else None
        if diag_at and assess_at and diag_at < assess_at:
            diagnose_status = "NOT_STARTED"
            diagnose_reason = "诊断早于最新评估，需重新诊断"
        else:
            diagnose_status = "COMPLETED"
            diagnose_reason = "诊断完成"
    stages.append(
        {
            "stage": "DIAGNOSE",
            "status": diagnose_status,
            "resultAt": diagnosis.get("resultAt") if diagnosis else None,
            "reason": diagnose_reason,
        }
    )

    # --- TUNE ---
    if tuning is None:
        tune_status = "NOT_REQUIRED"
        tune_reason = "无整定记录"
    else:
        ts = tuning.get("status")
        if ts in ("RUNNING",):
            tune_status = "RUNNING"
            tune_reason = "整定进行中"
        elif ts in ("IDENTIFIED", "SIMULATED"):
            tune_status = "RUNNING" if ts == "IDENTIFIED" else "COMPLETED"
            tune_reason = "已辨识" if ts == "IDENTIFIED" else "已完成仿真"
        elif ts == "COMPLETED":
            tune_status = "COMPLETED"
            tune_reason = "整定完成"
        elif ts == "INCONCLUSIVE":
            tune_status = "INCONCLUSIVE"
            tune_reason = "整定数据不足"
        elif ts == "ROLLED_BACK":
            tune_status = "BLOCKED"
            tune_reason = "整定已回退"
        else:
            tune_status = "NOT_STARTED"
            tune_reason = f"整定状态 {ts}"
    stages.append(
        {
            "stage": "TUNE",
            "status": tune_status,
            "resultAt": tuning.get("resultAt") if tuning else None,
            "reason": tune_reason,
        }
    )

    # --- VERIFY ---
    if tracker is None:
        verify_status = "NOT_REQUIRED"
        verify_reason = "无整改工单"
    else:
        ta = tracker.get("actionStatus")
        if ta == "CLOSED":
            verify_status = "COMPLETED"
            verify_reason = "已闭环"
        elif ta == "REOPENED":
            verify_status = "BLOCKED"
            verify_reason = tracker.get("reopenReason") or "验证失败已重开"
        elif ta == "VERIFYING":
            if tracker.get("isOverdue"):
                verify_status = "OVERDUE"
                hours = tracker.get("overdueHours")
                verify_reason = f"验证已超期 {hours:.0f} 小时" if hours else "验证超期"
            else:
                verify_status = "RUNNING"
                verify_reason = "等待效果验证"
        elif ta in ("PENDING", "IN_PROGRESS"):
            verify_status = "NOT_STARTED"
            verify_reason = "尚未实施"
        else:
            verify_status = "NOT_REQUIRED"
            verify_reason = f"工单状态 {ta}"
    stages.append(
        {
            "stage": "VERIFY",
            "status": verify_status,
            "resultAt": tracker.get("updatedAt") if tracker else None,
            "reason": verify_reason,
        }
    )

    # 当前推荐关注阶段：第一个未完成/阻塞/超期的阶段
    current_stage: str | None = None
    for s in stages:
        if s["status"] in ("NOT_STARTED", "BLOCKED", "OVERDUE", "INCONCLUSIVE", "RUNNING"):
            current_stage = s["stage"]
            break

    return {"stages": stages, "currentStage": current_stage}


# ---------------------------------------------------------------------------
# 推荐下一步（MW-P3-03，方案 §7.3）
# ---------------------------------------------------------------------------


def _build_next_action(
    *,
    loop_id: str,
    role: str,
    config_complete: bool,
    config_reason: str | None,
    has_runtime: bool,
    assessment: dict | None,
    diagnosis: dict | None,
    tuning: dict | None,
    tracker: dict | None,
    active_attention: dict,
    lifecycle: dict,
) -> dict:
    """按方案 §7.3 顺序输出唯一主动作，按角色过滤 enabled。

    优先级（从上到下）：
    1. 回路配置不完整或无本地数据 → 修复 Tag 关联/导入历史数据。
    2. 评估缺失、过期或评分明显恶化 → 发起评估。
    3. 评估异常且无同轴诊断结果 → 发起诊断。
    4. 诊断存在可执行问题且无开放 Tracker → 创建 Tracker。
    5. 诊断指向可整定问题且无有效整定记录 → 回路辨识/参数整定。
    6. 已生成建议但未实施 → 记录人工实施和 MOC。
    7. Tracker 为 VERIFYING → 进入效果验证；超期时提升关注优先级。
    8. 无开放问题 → 持续监控。
    """
    workbench_target = {"route": "/monitor/loop-workbench", "query": {"loopId": loop_id}}

    # 写权限判定
    can_write_assess_diag = role in ("ADMIN", "IC_ENGINEER")
    can_write_tuning = role in ("ADMIN", "IC_ENGINEER", "EXPERT")
    can_write_tracker = role in ("ADMIN", "IC_ENGINEER")

    def _disabled(action_type: str) -> tuple[bool, str | None]:
        """按角色判定动作是否禁用。"""
        if action_type in ("RUN_ASSESSMENT", "RUN_DIAGNOSIS", "RECORD_IMPLEMENTATION"):
            if can_write_assess_diag:
                return (False, None)
            return (True, "当前角色无写权限")
        if action_type in ("CREATE_TRACKER",):
            if can_write_tracker:
                return (False, None)
            return (True, "当前角色无建单权限")
        if action_type in ("RUN_TUNING",):
            if can_write_tuning:
                return (False, None)
            return (True, "当前角色无整定权限")
        return (False, None)

    # 1. 配置不完整或无数据
    if not config_complete:
        disabled, reason = _disabled("FIX_TAG_CONFIG")
        return {
            "actionType": "FIX_TAG_CONFIG",
            "label": "修复 Tag 关联",
            "reason": config_reason or "回路配置不完整",
            "enabled": not disabled,
            "disabledReason": reason,
            "target": workbench_target,
        }
    if not has_runtime:
        return {
            "actionType": "IMPORT_DATA",
            "label": "导入历史数据",
            "reason": "无本地运行态数据，需导入历史数据",
            "enabled": True,
            "disabledReason": None,
            "target": workbench_target,
        }

    # 2. 评估缺失/过期/恶化
    assess_status = lifecycle["stages"][1]["status"]
    if assess_status in ("NOT_STARTED", "INCONCLUSIVE"):
        disabled, reason = _disabled("RUN_ASSESSMENT")
        return {
            "actionType": "RUN_ASSESSMENT",
            "label": "发起评估",
            "reason": "评估缺失或数据不足",
            "enabled": not disabled,
            "disabledReason": reason,
            "target": workbench_target,
        }

    # 评分明显恶化（scoreDelta <= -2）
    if assessment and assessment.get("_dayTrend") == "WORSENED":
        disabled, reason = _disabled("RUN_ASSESSMENT")
        return {
            "actionType": "RUN_ASSESSMENT",
            "label": "重新评估",
            "reason": f"评分较昨日下降 {abs(assessment.get('_scoreDelta') or 0):.1f} 分",
            "enabled": not disabled,
            "disabledReason": reason,
            "target": workbench_target,
        }

    # 3. 评估异常且无同轴诊断
    diag_status = lifecycle["stages"][2]["status"]
    if diag_status in ("NOT_STARTED",):
        disabled, reason = _disabled("RUN_DIAGNOSIS")
        return {
            "actionType": "RUN_DIAGNOSIS",
            "label": "发起诊断",
            "reason": "无诊断结果或诊断早于最新评估",
            "enabled": not disabled,
            "disabledReason": reason,
            "target": workbench_target,
        }

    # 可整定标签集合（振荡/阀门粘滞/输出饱和/整定不当）——走规则 5 RUN_TUNING
    tunable_labels = {
        "OSCILLATION",
        "VALVE_STICTION",
        "OUTPUT_SATURATION",
        "POOR_TUNING",
    }
    diag_labels = set(diagnosis.get("labels", [])) if diagnosis else set()
    is_tunable = bool(diag_labels & tunable_labels)

    # 4. 诊断存在可执行问题且无 Tracker → 创建 Tracker
    #    仅对非可整定标签（可整定走规则 5）；已有任何 tracker（含 CLOSED）说明已处置
    open_tracker = tracker and tracker.get("actionStatus") in (
        "PENDING",
        "IN_PROGRESS",
        "VERIFYING",
    )
    has_any_tracker = tracker is not None
    if (
        diagnosis
        and diagnosis.get("diagLabel")
        and not open_tracker
        and not has_any_tracker
        and not is_tunable
    ):
        disabled, reason = _disabled("CREATE_TRACKER")
        return {
            "actionType": "CREATE_TRACKER",
            "label": "创建工单",
            "reason": f"诊断标签 {diagnosis.get('diagLabel')} 待建单跟踪",
            "enabled": not disabled,
            "disabledReason": reason,
            "target": workbench_target,
        }

    # 5. 诊断指向可整定问题且无有效整定记录 → 回路辨识/参数整定
    tune_status = lifecycle["stages"][3]["status"]
    if tune_status in ("NOT_REQUIRED", "NOT_STARTED", "INCONCLUSIVE", "BLOCKED"):
        if is_tunable or tune_status == "INCONCLUSIVE":
            disabled, reason = _disabled("RUN_TUNING")
            return {
                "actionType": "RUN_TUNING",
                "label": "回路整定",
                "reason": "诊断指向可整定问题",
                "enabled": not disabled,
                "disabledReason": reason,
                "target": workbench_target,
            }

    # 6. 已生成建议但未实施（整定完成但无 VERIFYING/CLOSED Tracker）
    if (
        tuning
        and tuning.get("status") in ("COMPLETED", "SIMULATED")
        and tracker
        and tracker.get("actionStatus") in ("PENDING", "IN_PROGRESS")
    ):
        disabled, reason = _disabled("RECORD_IMPLEMENTATION")
        return {
            "actionType": "RECORD_IMPLEMENTATION",
            "label": "记录实施",
            "reason": "整定建议已生成，待记录人工实施与 MOC",
            "enabled": not disabled,
            "disabledReason": reason,
            "target": workbench_target,
        }

    # 7. Tracker VERIFYING → 进入效果验证
    if tracker and tracker.get("actionStatus") == "VERIFYING":
        if tracker.get("isOverdue"):
            return {
                "actionType": "VERIFY_EFFECT",
                "label": "立即验证",
                "reason": f"验证已超期 {tracker.get('overdueHours', 0):.0f} 小时",
                "enabled": can_write_tracker,
                "disabledReason": None if can_write_tracker else "当前角色无验证权限",
                "target": workbench_target,
            }
        return {
            "actionType": "VERIFY_EFFECT",
            "label": "进入验证",
            "reason": "实施后等待效果验证",
            "enabled": can_write_tracker,
            "disabledReason": None if can_write_tracker else "当前角色无验证权限",
            "target": workbench_target,
        }

    # 8. 无开放问题 → 持续监控
    return {
        "actionType": "CONTINUE_MONITORING",
        "label": "持续监控",
        "reason": "回路当前无开放问题",
        "enabled": True,
        "disabledReason": None,
        "target": None,
    }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def get_workbench_summary(
    db: AsyncSession,
    *,
    loop_id: str,
    role: str = "ADMIN",
) -> dict:
    """工作台首屏摘要聚合。

    单个来源失败时返回 ``partial=true`` 且该来源在 ``unavailableSections`` 中，
    其他来源正常返回，不让整页 500。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND（回路不存在）/ 403（Sponsor 无权限）
    """
    from app.core.exceptions import BizError

    if not _is_valid_uuid(loop_id):
        raise BizError(
            code="ERR_VALIDATION",
            message="无效的回路 ID",
            status_code=400,
        )

    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 查 unit_name
    unit_name: str | None = None
    if loop.unit_id:
        u_result = await db.execute(select(PlantNode).where(PlantNode.id == loop.unit_id))
        node = u_result.scalar_one_or_none()
        if node:
            unit_name = node.name

    unavailable: list[str] = []

    # --- 运行态 ---
    try:
        runtime, _tags_map, mappings = await _build_runtime(db, loop)
    except Exception:  # noqa: BLE001
        logger.warning("summary: 运行态构建失败", exc_info=True)
        runtime = {
            "pv": None,
            "sp": None,
            "op": None,
            "mode": None,
            "modeLabel": None,
            "pvQuality": None,
            "pvUnit": None,
            "pvRange": None,
            "opRange": None,
            "readAt": None,
            "controlMode": None,
        }
        mappings = {}
        unavailable.append("runtime")

    data_freshness = _build_data_freshness(runtime.get("readAt"))

    # --- 配置完整性 ---
    config_complete, config_reason = _check_config_completeness(mappings, loop)

    # --- 数据健康度 ---
    try:
        data_health = await _build_data_health(db, loop_id)
    except Exception:  # noqa: BLE001
        logger.warning("summary: 数据健康度构建失败", exc_info=True)
        data_health = {}
        unavailable.append("dataHealth")

    # --- 评估摘要 ---
    try:
        assessment = await _build_assessment_summary(db, loop_id)
    except Exception:  # noqa: BLE001
        logger.warning("summary: 评估摘要构建失败", exc_info=True)
        assessment = None
        unavailable.append("assessment")

    # --- 诊断摘要 ---
    try:
        diagnosis = await _build_diagnosis_summary(db, loop_id)
    except Exception:  # noqa: BLE001
        logger.warning("summary: 诊断摘要构建失败", exc_info=True)
        diagnosis = None
        unavailable.append("diagnosis")

    # --- 整定摘要 ---
    try:
        tuning = await _build_tuning_summary(db, loop_id)
    except Exception:  # noqa: BLE001
        logger.warning("summary: 整定摘要构建失败", exc_info=True)
        tuning = None
        unavailable.append("tuning")

    # --- Tracker 时间线（MW-P3-09：传入整定 current_pid 作为实施前基线） ---
    try:
        tracker = await _build_tracker_timeline(
            db,
            loop_id,
            tuning_current_pid=tuning.get("currentPid") if tuning else None,
        )
    except Exception:  # noqa: BLE001
        logger.warning("summary: Tracker 时间线构建失败", exc_info=True)
        tracker = None
        unavailable.append("trackerTimeline")

    # --- 活跃关注项 ---
    try:
        active_attention = await _build_active_attention(db, loop_id, role)
    except Exception:  # noqa: BLE001
        logger.warning("summary: 活跃关注项构建失败", exc_info=True)
        active_attention = {"total": 0, "highestPriority": None, "items": []}
        unavailable.append("activeAttention")

    # --- 生命周期 ---
    lifecycle = _build_lifecycle(
        loop=loop,
        config_complete=config_complete,
        config_reason=config_reason,
        runtime=runtime,
        data_freshness=data_freshness,
        data_health=data_health,
        assessment=assessment,
        diagnosis=diagnosis,
        tuning=tuning,
        tracker=tracker,
    )

    # --- 推荐下一步 ---
    next_action = _build_next_action(
        loop_id=loop_id,
        role=role,
        config_complete=config_complete,
        config_reason=config_reason,
        has_runtime=bool(runtime.get("readAt")),
        assessment=assessment,
        diagnosis=diagnosis,
        tuning=tuning,
        tracker=tracker,
        active_attention=active_attention,
        lifecycle=lifecycle,
    )

    # --- 评分趋势（从评估摘要提取） ---
    score_trend = {
        "score": assessment.get("score") if assessment else None,
        "scoreDelta": assessment.get("_scoreDelta") if assessment else None,
        "dayTrend": assessment.get("_dayTrend") if assessment else None,
        "resultAt": assessment.get("resultAt") if assessment else None,
        "confidenceLevel": assessment.get("confidenceLevel") if assessment else None,
        "status": assessment.get("status") if assessment else None,
    }
    # 清理评估摘要内部字段
    if assessment:
        assessment = {k: v for k, v in assessment.items() if not k.startswith("_")}

    return {
        "loopId": str(loop.id),
        "tagName": loop.tag_name,
        "description": loop.description,
        "unitName": unit_name,
        "loopType": loop.loop_type,
        "controlType": loop.control_type,
        "loopStatus": loop.status,
        "isActive": bool(loop.is_active),
        "importanceLevel": loop.importance_level,
        "runtime": runtime,
        "dataFreshness": data_freshness,
        "dataHealth": data_health,
        "scoreTrend": score_trend,
        "activeAttention": active_attention,
        "assessment": assessment,
        "diagnosis": diagnosis,
        "tuning": tuning,
        "trackerTimeline": tracker,
        "lifecycle": lifecycle,
        "nextAction": next_action,
        "partial": len(unavailable) > 0,
        "unavailableSections": unavailable,
    }


async def _build_data_health(db: AsyncSession, loop_id: str) -> dict:
    """数据健康度（validRate + 可信度 + 完整度）。"""
    # 最新快照 validRate + confidenceLevel
    snap_result = await db.execute(
        select(
            KpiSnapshotHourly.valid_rate,
            KpiSnapshotHourly.confidence_level,
        )
        .where(KpiSnapshotHourly.loop_id == loop_id)
        .order_by(KpiSnapshotHourly.ts_end.desc())
        .limit(1)
    )
    snap_row = snap_result.first()

    # 最新可信度
    conf_result = await db.execute(
        select(LoopConfidenceLatest.confidence_level).where(LoopConfidenceLatest.loop_id == loop_id)
    )
    conf_level = conf_result.scalar_one_or_none()

    # 最新完整性
    integrity_result = await db.execute(
        select(LoopIntegritySnapshot)
        .where(LoopIntegritySnapshot.loop_id == loop_id)
        .order_by(LoopIntegritySnapshot.check_date.desc())
        .limit(1)
    )
    integrity = integrity_result.scalar_one_or_none()

    return {
        "validRate": _to_float(snap_row[0]) if snap_row else None,
        "confidenceLevel": (snap_row[1] if snap_row and snap_row[1] else conf_level),
        "pvCompleteness": integrity.pv_completeness if integrity else None,
        "overallCompleteness": integrity.overall_completeness if integrity else None,
        "integrityStatus": integrity.status if integrity else None,
    }
