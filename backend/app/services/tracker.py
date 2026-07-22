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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisResult
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
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

    # 校验回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        raise BizError(
            code="ERR_LOOP_NOT_FOUND",
            message="回路不存在",
            status_code=404,
        )

    # 查询该回路最新的 tracker 记录
    tracker_result = await db.execute(
        select(ActionTracker)
        .where(ActionTracker.loop_id == loop_id)
        .order_by(ActionTracker.updated_at.desc().nulls_last())
        .limit(1)
    )
    tracker = tracker_result.scalar_one_or_none()

    if tracker is None:
        # 自动创建一条 tracker 记录
        # 取该回路最新的诊断标签
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

    # IMPLEMENTED 状态时自动生成 A/B 对比视图
    ab_comparison = None
    if status == "IMPLEMENTED":
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
        "abComparison": ab_comparison,
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


async def get_ab_compare(
    db: AsyncSession,
    loop_id: str,
    *,
    implemented_at: str | None = None,
    before_start: str | None = None,
    before_end: str | None = None,
    after_start: str | None = None,
    after_end: str | None = None,
) -> dict[str, Any]:
    """A/B 对比：实施前后两窗口 KPI 均值对比（kpi_snapshot_hourly）。

    窗口确定方式（二选一）：
    - implemented_at：以 T 为界自动截取 [T-7d,T) 与 (T,T+7d]（FDS §5.4.4）
    - before_start/before_end/after_start/after_end：显式窗口（闭区间）

    实施后窗口快照数 < 24（不足 24h 数据）时 dataInsufficient=true。

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

    resolved_at: datetime | None = None
    if implemented_at:
        resolved_at = _parse_iso_dt(implemented_at)
        b_start, b_end = resolved_at - timedelta(days=7), resolved_at
        a_start, a_end = resolved_at, resolved_at + timedelta(days=7)
        # [T-7d,T) 与 (T,T+7d]
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


__all__ = ["export_tracker_pdf", "get_ab_compare", "update_tracker_status"]
