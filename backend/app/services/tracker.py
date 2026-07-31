"""Action Tracker service (IDS v3.2 §2.4.6~2.4.7 — S4-DIAG-005).

业务逻辑：
- 状态管理（PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED）
- 状态变更记录审计日志
- IMPLEMENTED 状态时自动生成 A/B 对比视图
- A/B 对比：以实施时刻为界对比前后窗口 KPI 均值（kpi_snapshot_hourly）
- PDF 导出为同步生成（复用 SVC-12 报告生成器，直接返回 PDF 文件）
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisResult
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.models.sys_config import SysConfig
from app.models.tracker import ActionTracker
from app.services.diagnosis import get_diagnosis_detail
from app.services.diagnosis_recommendation import get_recommendations_for_loop
from app.services.diagnosis_report import generate_diagnosis_report

logger = logging.getLogger(__name__)

# 有效状态枚举
VALID_STATUSES = ("PENDING", "IN_PROGRESS", "IMPLEMENTED", "IGNORED")

# A/B 对比 KPI 字段（kpi_snapshot_hourly 实际可用指标）：
# (字段名, 中文名, 单位, 是否正向指标)
AB_COMPARE_KPIS: tuple[tuple[str, str, str, bool], ...] = (
    ("score", "综合评分", "分", True),
    ("steady_rate", "平稳率", "%", True),
    ("accuracy_rate", "控制精度", "%", True),
    ("effective_auto_rate", "有效自控率", "%", True),
    ("good_value_rate", "好值率", "%", True),
    ("oscillation_rate", "振荡率", "%", False),
    ("saturation_rate", "饱和率", "%", False),
    ("stiction_index", "粘滞指数", "", False),
)

# 实施后窗口最少快照数（小时级快照 <24 即数据不足 24h）
AB_MIN_AFTER_SNAPSHOTS = 24


async def update_tracker_status(
    db: AsyncSession,
    loop_id: str,
    operator: str,
    *,
    status: str,
    evidence_url: str | None = None,
    remark: str | None = None,
    comment: str | None = None,
    moc_ref: str | None = None,
    moc_not_applicable: bool | None = None,
    moc_reason: str | None = None,
    assignee: str | None = None,
    planned_at: datetime | None = None,
) -> dict:
    """更新 Action Tracker 状态。

    - 仅 IC_ENGINEER 可操作（在 endpoint 层鉴权）
    - 标记 IMPLEMENTED 后自动生成 A/B 对比视图
    - D3: 标记 IMPLEMENTED 时必须提供 moc_ref 或
      (moc_not_applicable=True + moc_reason)，对齐危化企业变更管理要求

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TRACKER_NOT_FOUND / ERR_MOC_REQUIRED
    """
    if status not in VALID_STATUSES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"无效的状态值，必须为 {', '.join(VALID_STATUSES)} 之一",
            status_code=400,
        )

    # D3: MOC 必填校验（仅 IMPLEMENTED 状态）
    if status == "IMPLEMENTED":
        if moc_not_applicable is True:
            if not moc_reason or not moc_reason.strip():
                raise BizError(
                    code="ERR_MOC_REQUIRED",
                    message="标记已实施且 MOC 不适用时，必须填写依据说明",
                    status_code=422,
                )
        elif not moc_ref or not moc_ref.strip():
            raise BizError(
                code="ERR_MOC_REQUIRED",
                message="标记已实施时必须提供 MOC 变更管理关联编号，或勾选'不适用'并填写依据说明",
                status_code=422,
            )

    logger.info(
        "Tracker 状态变更请求: loop_id=%s, operator=%s, target_status=%s, "
        "moc_ref=%s, moc_not_applicable=%s",
        loop_id,
        operator,
        status,
        moc_ref,
        moc_not_applicable,
    )

    # 校验回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 查询该回路最新的开放态 tracker（PENDING/IN_PROGRESS）
    # D1 整改：仅操作开放态记录；若最新一条已闭环（IMPLEMENTED/IGNORED），
    # 不再覆盖历史，而是新建一条 tracker（保留闭环历史）。
    tracker_result = await db.execute(
        select(ActionTracker)
        .where(ActionTracker.loop_id == loop_id)
        .where(ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"]))
        .order_by(ActionTracker.created_at.desc().nulls_last())
        .limit(1)
    )
    tracker = tracker_result.scalar_one_or_none()

    if tracker is None:
        # 无开放态 tracker（全部已闭环或从未建单）：新建一条手工 tracker
        # 取该回路最新的诊断标签作为建单依据
        diag_result = await db.execute(
            select(DiagnosisResult)
            .where(DiagnosisResult.loop_id == loop_id)
            .order_by(DiagnosisResult.diagnosed_at.desc())
            .limit(1)
        )
        diag = diag_result.scalar_one_or_none()
        diagnosis_label = diag.diag_label if diag else None

        tracker = ActionTracker(
            id=str(uuid4()),
            loop_id=loop_id,
            diagnosis_label=diagnosis_label,
            action_status="PENDING",
            trigger_type="manual",
            triggered_by=operator,
        )
        db.add(tracker)

    before_status = tracker.action_status
    before_evidence_url = tracker.evidence_url
    before_json = json.dumps(
        {
            "loopId": loop_id,
            "actionStatus": before_status,
            "evidenceUrl": before_evidence_url,
        },
        ensure_ascii=False,
        default=str,
    )

    tracker.action_status = status
    if evidence_url is not None:
        tracker.evidence_url = evidence_url
    if comment is not None:
        tracker.comment = comment
    if moc_ref is not None:
        tracker.moc_ref = moc_ref
    if moc_not_applicable is not None:
        tracker.moc_not_applicable = moc_not_applicable
    if moc_reason is not None:
        tracker.moc_reason = moc_reason
    # V62-P3-008：负责人与计划执行时间
    if assignee is not None:
        tracker.assignee = assignee
    if planned_at is not None:
        tracker.planned_at = planned_at
    tracker.updated_by = operator
    tracker.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after_json = json.dumps(
        {
            "loopId": loop_id,
            "actionStatus": tracker.action_status,
            "evidenceUrl": tracker.evidence_url,
            "remark": remark,
            "mocRef": tracker.moc_ref,
            "mocNotApplicable": tracker.moc_not_applicable,
        },
        ensure_ascii=False,
        default=str,
    )

    # 写入审计日志
    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=operator,
        operation_type="TRACKER_STATUS_UPDATE",
        target_type="action_tracker",
        target_id=str(tracker.id),
        before_value=before_json,
        after_value=after_json,
        operated_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(audit_log)

    await db.commit()

    logger.info(
        "Tracker 状态变更完成: tracker_id=%s, loop_id=%s, %s → %s, operator=%s, trigger_type=%s",
        tracker.id,
        loop_id,
        before_status,
        tracker.action_status,
        operator,
        tracker.trigger_type,
    )

    # IMPLEMENTED 状态时自动生成 A/B 对比视图
    ab_comparison = None
    if status == "IMPLEMENTED":
        logger.info(
            "Tracker 标记 IMPLEMENTED，开始生成 A/B 对比视图: tracker_id=%s, loop_id=%s",
            tracker.id,
            loop_id,
        )
        ab_comparison = await _generate_ab_comparison(db, loop_id, tracker)

    return {
        "loopId": loop_id,
        "diagnosisLabel": tracker.diagnosis_label,
        "actionStatus": tracker.action_status,
        "evidenceUrl": tracker.evidence_url,
        "updatedBy": tracker.updated_by,
        "updatedAt": tracker.updated_at.isoformat() if tracker.updated_at else None,
        "createdAt": tracker.created_at.isoformat() if tracker.created_at else None,
        "comment": tracker.comment,
        "mocRef": tracker.moc_ref,
        "mocNotApplicable": tracker.moc_not_applicable,
        "mocReason": tracker.moc_reason,
        "triggerType": tracker.trigger_type,
        "triggeredBy": tracker.triggered_by,
        "severity": tracker.severity,
        "abComparison": ab_comparison,
        # D4: 整改效果验证（初始为 None，T+7d 由周期任务回写）
        "effectVerified": tracker.effect_verified,
        "effectVerifiedAt": tracker.effect_verified_at.isoformat()
        if tracker.effect_verified_at
        else None,
        "abCompareSummary": tracker.ab_compare_summary,
        # V62-P3-008：负责人与计划执行时间
        "assignee": tracker.assignee,
        "plannedAt": tracker.planned_at.isoformat() if tracker.planned_at else None,
    }


async def _generate_ab_comparison(
    db: AsyncSession,
    loop_id: str,
    tracker: ActionTracker,
) -> dict[str, Any]:
    """生成 A/B 对比视图（实施前后数据窗口）。

    以 tracker.updated_at 为分界点，前 24 小时为 Before，后 24 小时为 After。
    """
    # 取该回路最新的诊断结果作为基准时间
    diag_result = await db.execute(
        select(DiagnosisResult)
        .where(DiagnosisResult.loop_id == loop_id)
        .order_by(DiagnosisResult.diagnosed_at.desc())
        .limit(1)
    )
    diag = diag_result.scalar_one_or_none()

    resolved_at = tracker.updated_at or datetime.now(UTC).replace(tzinfo=None)
    if resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=UTC)

    before_start = (resolved_at - timedelta(hours=24)).isoformat()
    before_end = resolved_at.isoformat()
    after_start = resolved_at.isoformat()
    after_end = (resolved_at + timedelta(hours=24)).isoformat()

    return {
        "resolvedAt": resolved_at.isoformat(),
        "beforeWindow": {
            "startTime": before_start,
            "endTime": before_end,
            "waveformUrl": (
                f"/api/v1/timeseries/{loop_id}/waveform"
                f"?startTime={before_start}&endTime={before_end}"
            ),
        },
        "afterWindow": {
            "startTime": after_start,
            "endTime": after_end,
            "waveformUrl": (
                f"/api/v1/timeseries/{loop_id}/waveform?startTime={after_start}&endTime={after_end}"
            ),
        },
        "diagnosisLabel": tracker.diagnosis_label,
        "diagnosedAt": diag.diagnosed_at.isoformat() if diag else None,
    }


# ---------------------------------------------------------------------------
# A/B 对比（GET /api/v1/diagnosis/ab-compare）
# ---------------------------------------------------------------------------


def _parse_iso_dt(value: str) -> datetime:
    """解析 ISO 8601 时间字符串为 naive datetime（kpi 快照列为 TIMESTAMP WITHOUT TIME ZONE）。

    Raises:
        BizError: ERR_VALIDATION — 时间格式无效
    """
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"时间格式无效: {value}",
            status_code=422,
        ) from exc
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _build_waveform_url(loop_id: str, start: datetime, end: datetime) -> str:
    """拼装波形 URL（与 _generate_ab_comparison 口径一致）。"""
    return (
        f"/api/v1/timeseries/{loop_id}/waveform"
        f"?startTime={start.isoformat()}&endTime={end.isoformat()}"
    )


async def _aggregate_kpi_window(
    db: AsyncSession,
    loop_id: str,
    start: datetime,
    end: datetime,
    *,
    start_exclusive: bool = False,
    end_exclusive: bool = False,
) -> tuple[int, list[Any]]:
    """聚合 kpi_snapshot_hourly 窗口内各 KPI 均值与快照数。

    Returns:
        (快照条数, 各 KPI 均值序列（与 AB_COMPARE_KPIS 同序，None 表示窗口无数据）)
    """
    conds: list[Any] = [KpiSnapshotHourly.loop_id == loop_id]
    ts_start = KpiSnapshotHourly.ts_start
    conds.append(ts_start > start if start_exclusive else ts_start >= start)
    conds.append(ts_start < end if end_exclusive else ts_start <= end)
    stmt = select(
        func.count(KpiSnapshotHourly.id),
        *[func.avg(getattr(KpiSnapshotHourly, field)) for field, *_ in AB_COMPARE_KPIS],
    ).where(*conds)
    row = (await db.execute(stmt)).one()
    return int(row[0] or 0), list(row[1:])


async def _collect_window_labels(
    db: AsyncSession,
    loop_id: str,
    start: datetime,
    end: datetime,
    *,
    start_exclusive: bool = False,
    end_exclusive: bool = False,
) -> list[dict[str, Any]]:
    """收集窗口内的诊断标签（每个标签取最新一条，Batch 4 A/B 对比增强）。

    confidence 在 DB 中以 0-100 存储，此处归一化为 0-1。

    Returns:
        [{"label": str, "confidence": float|None, "diagnosedAt": str|None}, ...]
    """
    conds: list[Any] = [
        DiagnosisResult.loop_id == loop_id,
        DiagnosisResult.diag_label.isnot(None),
    ]
    ts = DiagnosisResult.diagnosed_at
    conds.append(ts > start if start_exclusive else ts >= start)
    conds.append(ts < end if end_exclusive else ts <= end)
    # 每个标签取最新一条（按标签分组后取最新时间）
    stmt = (
        select(DiagnosisResult)
        .where(*conds)
        .order_by(DiagnosisResult.diag_label, DiagnosisResult.diagnosed_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r.diag_label or r.diag_label in seen:
            continue
        conf = None
        if r.confidence is not None:
            conf = round(float(r.confidence) / 100.0, 4)
        seen[r.diag_label] = {
            "label": r.diag_label,
            "confidence": conf,
            "diagnosedAt": r.diagnosed_at.isoformat() if r.diagnosed_at else None,
        }
    return list(seen.values())


def _diff_label_changes(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """计算前后窗口诊断标签差异（新增/消失/置信度变化）。"""
    before_map = {item["label"]: item for item in before}
    after_map = {item["label"]: item for item in after}
    all_labels = set(before_map) | set(after_map)
    changes: list[dict[str, Any]] = []
    for label in sorted(all_labels):
        b = before_map.get(label)
        a = after_map.get(label)
        if b is None and a is not None:
            changes.append({"label": label, "change": "added", "afterConfidence": a["confidence"]})
        elif b is not None and a is None:
            changes.append(
                {"label": label, "change": "removed", "beforeConfidence": b["confidence"]}
            )
        elif b is not None and a is not None:
            b_conf = b["confidence"]
            a_conf = a["confidence"]
            if b_conf is not None and a_conf is not None and abs(a_conf - b_conf) > 0.01:
                changes.append(
                    {
                        "label": label,
                        "change": "confidence_changed",
                        "beforeConfidence": b_conf,
                        "afterConfidence": a_conf,
                    }
                )
    return changes


async def get_ab_compare(
    db: AsyncSession,
    loop_id: str,
    *,
    implemented_at: str | None = None,
    before_start: str | None = None,
    before_end: str | None = None,
    after_start: str | None = None,
    after_end: str | None = None,
    include_diagnosis: bool = False,
) -> dict[str, Any]:
    """A/B 对比：实施前后两窗口 KPI 均值对比（kpi_snapshot_hourly）。

    窗口确定方式（二选一）：
    - implemented_at：以 T 为界自动截取 [T-7d,T) 与 (T,T+7d]（FDS §5.4.4）
    - before_start/before_end/after_start/after_end：显式窗口（闭区间）

    实施后窗口快照数 < 24（不足 24h 数据）时 dataInsufficient=true。

    include_diagnosis=True 时额外返回 beforeDiagnosisLabels/afterDiagnosisLabels/
    labelChanges（标签新增/消失/置信度变化），用于 Batch 4 回路分析页 A/B 对比增强。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_VALIDATION
    """
    # 校验回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    logger.info(
        "A/B 对比开始: loop_id=%s, tag=%s, implemented_at=%s, "
        "explicit_window=%s, include_diagnosis=%s",
        loop_id,
        loop.tag_name,
        implemented_at,
        bool(before_start and before_end and after_start and after_end),
        include_diagnosis,
    )

    resolved_at: datetime | None = None
    # 窗口互斥标志（[T-7d,T) 与 (T,T+7d]，显式窗口为闭区间）
    b_end_excl = a_start_excl = False
    if implemented_at:
        resolved_at = _parse_iso_dt(implemented_at)
        b_start, b_end = resolved_at - timedelta(days=7), resolved_at
        a_start, a_end = resolved_at, resolved_at + timedelta(days=7)
        b_end_excl = True
        a_start_excl = True
        before_count, before_avgs = await _aggregate_kpi_window(
            db, loop_id, b_start, b_end, end_exclusive=True
        )
        after_count, after_avgs = await _aggregate_kpi_window(
            db, loop_id, a_start, a_end, start_exclusive=True
        )
    elif before_start and before_end and after_start and after_end:
        b_start, b_end = _parse_iso_dt(before_start), _parse_iso_dt(before_end)
        a_start, a_end = _parse_iso_dt(after_start), _parse_iso_dt(after_end)
        before_count, before_avgs = await _aggregate_kpi_window(db, loop_id, b_start, b_end)
        after_count, after_avgs = await _aggregate_kpi_window(db, loop_id, a_start, a_end)
    else:
        raise BizError(
            code="ERR_VALIDATION",
            message="缺少 implementedAt 或完整的前后窗口参数",
            status_code=422,
        )

    data_insufficient = after_count < AB_MIN_AFTER_SNAPSHOTS
    logger.info(
        "A/B 对比窗口数据: loop_id=%s, before_count=%d, after_count=%d, data_insufficient=%s",
        loop_id,
        before_count,
        after_count,
        data_insufficient,
    )

    kpi_items: list[dict[str, Any]] = []
    for (field, name, unit, higher_better), b_raw, a_raw in zip(
        AB_COMPARE_KPIS, before_avgs, after_avgs, strict=True
    ):
        before_val = round(float(b_raw), 4) if b_raw is not None else None
        after_val = round(float(a_raw), 4) if a_raw is not None else None
        change: float | None = None
        change_pct: float | None = None
        improved: bool | None = None
        if before_val is not None and after_val is not None:
            change = round(after_val - before_val, 4)
            if before_val != 0:
                change_pct = round(change / abs(before_val) * 100, 2)
            if change > 0:
                improved = higher_better
            elif change < 0:
                improved = not higher_better
        kpi_items.append(
            {
                "metricKey": field,
                "metricName": name,
                "unit": unit,
                "before": before_val,
                "after": after_val,
                "change": change,
                "changePct": change_pct,
                "improved": improved,
            }
        )

    # Batch 4 F1d：诊断标签对比（可选，include_diagnosis=True 时返回）
    before_diagnosis_labels: list[dict[str, Any]] | None = None
    after_diagnosis_labels: list[dict[str, Any]] | None = None
    label_changes: list[dict[str, Any]] | None = None
    if include_diagnosis:
        before_diagnosis_labels = await _collect_window_labels(
            db, loop_id, b_start, b_end, end_exclusive=b_end_excl
        )
        after_diagnosis_labels = await _collect_window_labels(
            db, loop_id, a_start, a_end, start_exclusive=a_start_excl
        )
        label_changes = _diff_label_changes(before_diagnosis_labels, after_diagnosis_labels)

    return {
        "loopId": loop_id,
        "tagName": loop.tag_name,
        "implementedAt": resolved_at.isoformat() if resolved_at else None,
        "dataInsufficient": after_count < AB_MIN_AFTER_SNAPSHOTS,
        "beforeWindow": {
            "startTime": b_start.isoformat(),
            "endTime": b_end.isoformat(),
            "waveformUrl": _build_waveform_url(loop_id, b_start, b_end),
        },
        "afterWindow": {
            "startTime": a_start.isoformat(),
            "endTime": a_end.isoformat(),
            "waveformUrl": _build_waveform_url(loop_id, a_start, a_end),
        },
        "kpiComparison": kpi_items,
        "beforeDiagnosisLabels": before_diagnosis_labels,
        "afterDiagnosisLabels": after_diagnosis_labels,
        "labelChanges": label_changes,
    }


async def export_tracker_pdf(
    db: AsyncSession,
    loop_id: str,
) -> tuple[bytes, str]:
    """同步生成诊断建议书 PDF，返回 (pdf_bytes, filename)。

    复用 SVC-12 报告生成器（app.services.diagnosis_report），
    文件名格式：CLPM-诊断建议书-[位号]-[日期].pdf

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_DIAG_RESULT_NOT_FOUND
    """
    # 校验回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 诊断快照 + 推荐方案（与 POST /diagnosis/{loopId}/report 相同数据源）
    snapshot_data = await get_diagnosis_detail(db=db, loop_id=loop_id)
    recommendations = await get_recommendations_for_loop(db=db, loop_id=loop_id)

    pdf_bytes = generate_diagnosis_report(
        loop_id=loop_id,
        snapshot_data=snapshot_data,
        recommendations=recommendations,
    )

    tag_name = loop.tag_name or loop_id
    date_str = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d")
    filename = f"CLPM-诊断建议书-{tag_name}-{date_str}.pdf"
    return pdf_bytes, filename


# ---------------------------------------------------------------------------
# D4-2 整改效果验证周期配置（GET/PATCH /api/v1/tracker/verification-config）
# ---------------------------------------------------------------------------

# sys_config key：整改效果验证周期（小时），默认 24
TRACKER_VERIFICATION_INTERVAL_KEY = "tracker.verification_interval_hours"
TRACKER_VERIFICATION_INTERVAL_DEFAULT = 24


async def get_verification_config(db: AsyncSession) -> dict[str, Any]:
    """读取整改效果验证周期配置。

    sys_config 无此 key 时返回默认值 24。
    """
    result = await db.execute(
        select(SysConfig).where(SysConfig.key == TRACKER_VERIFICATION_INTERVAL_KEY)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None or not cfg.value:
        return {
            "intervalHours": TRACKER_VERIFICATION_INTERVAL_DEFAULT,
            "updatedBy": None,
            "updatedAt": None,
        }
    try:
        hours = int(cfg.value)
    except (ValueError, TypeError):
        hours = TRACKER_VERIFICATION_INTERVAL_DEFAULT
    return {
        "intervalHours": hours,
        "updatedBy": cfg.updated_by,
        "updatedAt": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


async def update_verification_config(
    db: AsyncSession, *, interval_hours: int, operator: str
) -> dict[str, Any]:
    """更新整改效果验证周期配置（upsert sys_config）。

    Args:
        interval_hours: 验证周期（小时），范围 1~720
        operator: 操作人 username
    """
    if not 1 <= interval_hours <= 720:
        raise BizError(
            code="ERR_VALIDATION",
            message="验证周期需在 1~720 小时之间",
            status_code=422,
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    result = await db.execute(
        select(SysConfig).where(SysConfig.key == TRACKER_VERIFICATION_INTERVAL_KEY)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = SysConfig(
            key=TRACKER_VERIFICATION_INTERVAL_KEY,
            value=str(interval_hours),
            description="D4-2 整改效果验证周期（小时），IMPLEMENTED 后等待 N 小时触发验证",
            updated_by=operator,
            updated_at=now,
        )
        db.add(cfg)
    else:
        cfg.value = str(interval_hours)
        cfg.updated_by = operator
        cfg.updated_at = now
    await db.commit()

    logger.info("整改效果验证周期已更新: %d 小时, operator=%s", interval_hours, operator)
    return {
        "intervalHours": interval_hours,
        "updatedBy": operator,
        "updatedAt": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# D4-3 整改有效率统计（GET /api/v1/tracker/effectiveness）
# ---------------------------------------------------------------------------

# 时间窗口 → 天数映射（last_90_days 扩展支持）
_EFFECTIVENESS_WINDOW_DAYS: dict[str, int] = {
    "last_7_days": 7,
    "last_30_days": 30,
    "last_90_days": 90,
}


async def get_tracker_effectiveness(
    db: AsyncSession,
    *,
    time_window: str = "last_30_days",
    plant_node_id: str | None = None,
) -> dict[str, Any]:
    """计算整改有效率统计。

    Args:
        time_window: last_7_days / last_30_days / last_90_days
        plant_node_id: 装置/单元筛选（对应 LoopLedger.unit_id）

    Returns:
        TrackerEffectivenessData dict
    """
    days = _EFFECTIVENESS_WINDOW_DAYS.get(time_window, 30)
    now = datetime.now(UTC).replace(tzinfo=None)
    window_start = now - timedelta(days=days)

    # 构建 plantNodeId 筛选 JOIN（如需）
    plant_filter = None
    if plant_node_id:
        plant_filter = LoopLedger.unit_id == plant_node_id

    # 1. 时间窗口内已实施数（IMPLEMENTED 且 updated_at 在窗口内）
    impl_stmt = select(func.count(ActionTracker.id)).where(
        ActionTracker.action_status == "IMPLEMENTED",
        ActionTracker.updated_at >= window_start,
    )
    if plant_filter is not None:
        impl_stmt = impl_stmt.join(LoopLedger, ActionTracker.loop_id == LoopLedger.id).where(
            plant_filter
        )
    total_implemented = (await db.execute(impl_stmt)).scalar() or 0

    # 2. 已验证数 / 改善数 / 恶化数（effect_verified_at 在窗口内）
    verified_base = select(ActionTracker).where(
        ActionTracker.effect_verified.is_not(None),
        ActionTracker.effect_verified_at >= window_start,
    )
    if plant_filter is not None:
        verified_base = verified_base.join(
            LoopLedger, ActionTracker.loop_id == LoopLedger.id
        ).where(plant_filter)

    verified_count_stmt = select(func.count()).select_from(verified_base.subquery())
    verified_count = (await db.execute(verified_count_stmt)).scalar() or 0

    improved_count_stmt = select(func.count()).select_from(
        verified_base.where(ActionTracker.effect_verified.is_(True)).subquery()
    )
    improved_count = (await db.execute(improved_count_stmt)).scalar() or 0

    deteriorated_count_stmt = select(func.count()).select_from(
        verified_base.where(ActionTracker.effect_verified.is_(False)).subquery()
    )
    deteriorated_count = (await db.execute(deteriorated_count_stmt)).scalar() or 0

    # 3. 当前待验证数（IMPLEMENTED 且 effect_verified IS NULL，不限窗口）
    pending_stmt = select(func.count(ActionTracker.id)).where(
        ActionTracker.action_status == "IMPLEMENTED",
        ActionTracker.effect_verified.is_(None),
    )
    if plant_filter is not None:
        pending_stmt = pending_stmt.join(LoopLedger, ActionTracker.loop_id == LoopLedger.id).where(
            plant_filter
        )
    pending_count = (await db.execute(pending_stmt)).scalar() or 0

    # 4. 整改有效率
    effective_rate = round(improved_count / verified_count, 4) if verified_count > 0 else None

    # 5. 每日有效率趋势（按 effect_verified_at 日期分组）
    trend_stmt = (
        select(
            func.date_trunc("day", ActionTracker.effect_verified_at).label("day"),
            func.count(ActionTracker.id).label("verified"),
            func.count(case((ActionTracker.effect_verified.is_(True), 1))).label("improved"),
        )
        .where(
            ActionTracker.effect_verified.is_not(None),
            ActionTracker.effect_verified_at >= window_start,
        )
        .group_by("day")
        .order_by("day")
    )
    if plant_filter is not None:
        trend_stmt = trend_stmt.join(LoopLedger, ActionTracker.loop_id == LoopLedger.id).where(
            plant_filter
        )
    trend_rows = (await db.execute(trend_stmt)).all()

    trend: list[dict[str, Any]] = []
    for row in trend_rows:
        day_dt = row.day
        if day_dt.tzinfo is not None:
            day_dt = day_dt.replace(tzinfo=None)
        verified = row.verified or 0
        improved = row.improved or 0
        day_rate = round(improved / verified, 4) if verified > 0 else None
        trend.append(
            {
                "date": day_dt.strftime("%Y-%m-%d"),
                "verifiedCount": verified,
                "improvedCount": improved,
                "effectiveRate": day_rate,
            }
        )

    logger.info(
        "整改有效率统计: window=%s, days=%d, plant_node_id=%s, "
        "implemented=%d, verified=%d, improved=%d, deteriorated=%d, "
        "pending=%d, rate=%s, trend_points=%d",
        time_window,
        days,
        plant_node_id,
        total_implemented,
        verified_count,
        improved_count,
        deteriorated_count,
        pending_count,
        effective_rate,
        len(trend),
    )

    return {
        "totalImplemented": total_implemented,
        "verifiedCount": verified_count,
        "improvedCount": improved_count,
        "deterioratedCount": deteriorated_count,
        "effectiveRate": effective_rate,
        "pendingVerificationCount": pending_count,
        "trend": trend,
    }


__all__ = [
    "export_tracker_pdf",
    "get_ab_compare",
    "get_verification_config",
    "get_tracker_effectiveness",
    "update_tracker_status",
    "update_verification_config",
]
