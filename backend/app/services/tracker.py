"""Action Tracker service (IDS v3.2 §2.4.6~2.4.7 — S4-DIAG-005).

业务逻辑：
- 状态管理（PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED）
- 状态变更记录审计日志
- IMPLEMENTED 状态时自动生成 A/B 对比视图
- PDF 导出为异步任务（Phase 1 返回模拟任务 ID）
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import DiagnosisResult
from app.models.loop import LoopLedger
from app.models.tracker import ActionTracker

logger = logging.getLogger(__name__)

# 有效状态枚举
VALID_STATUSES = ("PENDING", "IN_PROGRESS", "IMPLEMENTED", "IGNORED")


async def update_tracker_status(
    db: AsyncSession,
    loop_id: str,
    operator: str,
    *,
    status: str,
    evidence_url: str | None = None,
    remark: str | None = None,
) -> dict:
    """更新 Action Tracker 状态。

    - 仅 IC_ENGINEER 可操作（在 endpoint 层鉴权）
    - 标记 IMPLEMENTED 后自动生成 A/B 对比视图

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TRACKER_NOT_FOUND
    """
    if status not in VALID_STATUSES:
        raise BizError(
            code="ERR_VALIDATION",
            message=f"无效的状态值，必须为 {', '.join(VALID_STATUSES)} 之一",
            status_code=400,
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
    tracker.updated_by = operator
    tracker.updated_at = datetime.now(UTC).replace(tzinfo=None)

    after_json = json.dumps(
        {
            "loopId": loop_id,
            "actionStatus": tracker.action_status,
            "evidenceUrl": tracker.evidence_url,
            "remark": remark,
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


async def export_tracker_pdf(
    db: AsyncSession,
    loop_id: str,
) -> dict:
    """导出诊断建议书 PDF（异步任务）。

    Phase 1: 返回模拟任务 ID。

    Raises:
        BizError: ERR_LOOP_NOT_FOUND
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

    # Phase 1: 返回模拟任务 ID
    task_id = str(uuid4())
    return {
        "taskId": task_id,
        "status": "PENDING",
    }


__all__ = ["export_tracker_pdf", "update_tracker_status"]
