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
# P1a 闭环状态机扩展：
#   PENDING → IN_PROGRESS → VERIFYING → CLOSED
#                                  ↓→ REOPENED → IN_PROGRESS
#   任意开放态 → IGNORED
#   IMPLEMENTED 为历史兼容状态，存量数据自动映射为 VERIFYING
VALID_STATUSES = (
    "PENDING",
    "IN_PROGRESS",
    "VERIFYING",
    "CLOSED",
    "REOPENED",
    "IMPLEMENTED",
    "IGNORED",
)

# 允许的状态转换（from_status -> set(to_statuses)）
# None 表示新建 tracker 时的初始状态
# P1a闭环状态机：PENDING → IN_PROGRESS → VERIFYING → CLOSED
# VERIFYING 可→ REOPENED，REOPENED 可→ IN_PROGRESS
# 历史兼容：保留 PENDING/IN_PROGRESS → IMPLEMENTED 的旧路径
ALLOWED_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"PENDING"},  # 新建只能是PENDING
    "PENDING": {"IN_PROGRESS", "IMPLEMENTED", "IGNORED"},
    "IN_PROGRESS": {
        "VERIFYING",
        "IMPLEMENTED",
        "IGNORED",
        "IN_PROGRESS",
    },  # IN_PROGRESS→IN_PROGRESS 用于更新备注
    "VERIFYING": {"CLOSED", "REOPENED", "VERIFYING", "IGNORED"},
    "REOPENED": {"IN_PROGRESS", "IMPLEMENTED", "IGNORED"},
    "IMPLEMENTED": {"CLOSED", "REOPENED", "VERIFYING", "IGNORED"},  # 兼容存量IMPLEMENTED
    "CLOSED": set(),  # 终态不可转换
    "IGNORED": set(),  # 终态不可转换
}

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
    # P1a 新增字段
    implemented_at: datetime | None = None,
    new_pid_p: float | None = None,
    new_pid_i: float | None = None,
    new_pid_d: float | None = None,
    reopen_reason: str | None = None,
) -> dict:
    """更新 Action Tracker 状态（P1a 闭环状态机扩展）。

    - 仅 IC_ENGINEER 可操作（在 endpoint 层鉴权）
    - 状态转换校验：PENDING→IN_PROGRESS→VERIFYING→CLOSED，VERIFYING可→REOPENED
    - 标记 VERIFYING（原 IMPLEMENTED）时强制填写实施PID参数（Poka-Yoke）
    - VERIFYING 后自动触发 A/B 对比，等待周期任务验证
    - 标记 REOPENED 时必须填写重开原因

    Raises:
        BizError: ERR_LOOP_NOT_FOUND / ERR_TRACKER_NOT_FOUND / ERR_MOC_REQUIRED
                  / ERR_INVALID_TRANSITION / ERR_PID_PARAMS_REQUIRED / ERR_REOPEN_REASON_REQUIRED
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

    # 查询该回路最新的开放态 tracker（PENDING/IN_PROGRESS/VERIFYING）
    open_statuses = ["PENDING", "IN_PROGRESS", "VERIFYING"]
    tracker_result = await db.execute(
        select(ActionTracker)
        .where(ActionTracker.loop_id == loop_id)
        .where(ActionTracker.action_status.in_(open_statuses))
        .order_by(ActionTracker.created_at.desc().nulls_last())
        .limit(1)
    )
    tracker = tracker_result.scalar_one_or_none()

    is_new_tracker = False
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
        # 取严重等级
        severity = diag.severity if diag else None

        tracker = ActionTracker(
            id=str(uuid4()),
            loop_id=loop_id,
            diagnosis_label=diagnosis_label,
            action_status="PENDING",
            trigger_type="manual",
            triggered_by=operator,
            severity=severity,
        )
        db.add(tracker)
        is_new_tracker = True
        # 新建tracker初始为PENDING，从PENDING开始状态转换
        before_status = "PENDING"
    else:
        before_status = tracker.action_status

    # ---------- 状态转换校验 ----------
    # 新建tracker也需要校验（从PENDING转换到目标状态）
    allowed_targets = ALLOWED_TRANSITIONS.get(before_status, set())
    if status not in allowed_targets:
        raise BizError(
            code="ERR_INVALID_TRANSITION",
            message=(
                f"不允许从状态 {before_status} 转换到 {status}，"
                f"允许的目标状态: {sorted(allowed_targets)}"
            ),
            status_code=422,
        )

    # ---------- P1a: 必填字段校验（Poka-Yoke）----------
    # VERIFYING 状态：必须提供MOC+实施PID参数
    # （IMPLEMENTED为历史兼容状态，不强制PID参数）
    is_implementing = status == "VERIFYING"
    if is_implementing:
        # MOC 必填校验（原D3逻辑）
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
        # P1a 新增：PID参数必填校验
        if new_pid_p is None or new_pid_i is None or new_pid_d is None:
            raise BizError(
                code="ERR_PID_PARAMS_REQUIRED",
                message="标记已实施时必须填写新的 PID 参数值（P/I/D）",
                status_code=422,
            )
    elif status == "IMPLEMENTED":
        # 历史兼容：IMPLEMENTED 状态只校验 MOC（原D3逻辑），PID参数可选
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

    # REOPENED 状态：必须填写重开原因
    if status == "REOPENED":
        if not reopen_reason or not reopen_reason.strip():
            raise BizError(
                code="ERR_REOPEN_REASON_REQUIRED",
                message="重开跟踪记录时必须填写重开原因（如验证未通过、效果不佳等）",
                status_code=422,
            )

    # IN_PROGRESS 且 assignee 为空时自动认领（operator 作为 assignee）
    if status == "IN_PROGRESS" and not assignee and not tracker.assignee:
        assignee = operator

    logger.info(
        "Tracker 状态变更请求: loop_id=%s, operator=%s, %s → %s, "
        "is_new=%s, moc_ref=%s, moc_not_applicable=%s",
        loop_id,
        operator,
        before_status,
        status,
        is_new_tracker,
        moc_ref,
        moc_not_applicable,
    )

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

    # ---------- 更新字段 ----------
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
    if assignee is not None:
        tracker.assignee = assignee
    if planned_at is not None:
        tracker.planned_at = planned_at
    # P1a 新增：实施PID字段（VERIFYING 必填；IMPLEMENTED 可选更新）
    if is_implementing or (status == "IMPLEMENTED" and new_pid_p is not None):
        tracker.implemented_by = operator
        tracker.implemented_at = implemented_at or datetime.now(UTC).replace(tzinfo=None)
        if new_pid_p is not None:
            tracker.new_pid_p = new_pid_p
        if new_pid_i is not None:
            tracker.new_pid_i = new_pid_i
        if new_pid_d is not None:
            tracker.new_pid_d = new_pid_d
    if status == "CLOSED":
        tracker.closed_at = datetime.now(UTC).replace(tzinfo=None)
    if reopen_reason is not None:
        tracker.reopen_reason = reopen_reason
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
            "implementedBy": tracker.implemented_by,
            "newPid": (
                {"p": tracker.new_pid_p, "i": tracker.new_pid_i, "d": tracker.new_pid_d}
                if is_implementing
                else None
            ),
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

    # VERIFYING/IMPLEMENTED 状态时自动生成 A/B 对比视图（验证窗口元数据）
    ab_comparison = None
    if is_implementing or status == "IMPLEMENTED":
        logger.info(
            "Tracker 标记 %s，生成 A/B 对比计划: tracker_id=%s, loop_id=%s",
            status,
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
        # D4: 整改效果验证
        "effectVerified": tracker.effect_verified,
        "effectVerifiedAt": tracker.effect_verified_at.isoformat()
        if tracker.effect_verified_at
        else None,
        "abCompareSummary": tracker.ab_compare_summary,
        # V62-P3-008：负责人与计划执行时间
        "assignee": tracker.assignee,
        "plannedAt": tracker.planned_at.isoformat() if tracker.planned_at else None,
        # P1a 新增：实施详情
        "implementedAt": tracker.implemented_at.isoformat() if tracker.implemented_at else None,
        "implementedBy": tracker.implemented_by,
        "newPidP": tracker.new_pid_p,
        "newPidI": tracker.new_pid_i,
        "newPidD": tracker.new_pid_d,
        "closedAt": tracker.closed_at.isoformat() if tracker.closed_at else None,
        "reopenReason": tracker.reopen_reason,
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


# ---------------------------------------------------------------------------
# P1a: 单回路处置时间线查询
# ---------------------------------------------------------------------------

# 时间线事件类型 → 中文标题映射
_TIMELINE_EVENT_TITLES: dict[str, str] = {
    "diagnosis_detected": "系统发现异常",
    "claimed": "认领处理",
    "comment": "添加备注",
    "tuning_completed": "整定完成",
    "implemented": "现场实施",
    "verification_passed": "验证通过·闭环",
    "verification_failed": "验证未通过·重开",
    "ignored": "忽略此异常",
    "moc_recorded": "记录MOC变更",
}


def _severity_label(severity: str | None) -> str:
    mapping = {"CRITICAL": "紧急", "ERROR": "错误", "WARN": "警告", "INFO": "提示"}
    return mapping.get(severity or "", severity or "未知")


async def get_loop_timeline(
    db: AsyncSession,
    loop_id: str,
) -> dict[str, Any]:
    """获取单回路异常处置时间线。

    聚合来源：
    1. 诊断结果（diagnosis_result）→ diagnosis_detected 事件
    2. ActionTracker 审计日志（sys_audit_log，operation_type=TRACKER_STATUS_UPDATE）
       → claimed/implemented/ignored/verification_passed/verification_failed 事件
    3. 整定结果（tuning_result，如有）→ tuning_completed 事件
    4. 当前 tracker 状态元数据 → 展示预计验证时间

    Returns:
        TimelineData dict
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

    events: list[dict[str, Any]] = []

    # ---------- 1. 诊断事件：取该回路最近的诊断结果 ----------
    diag_stmt = (
        select(DiagnosisResult)
        .where(DiagnosisResult.loop_id == loop_id)
        .order_by(DiagnosisResult.diagnosed_at.desc())
        .limit(20)
    )
    diag_rows = (await db.execute(diag_stmt)).scalars().all()
    for diag in diag_rows:
        label_name = diag.diag_label or ""
        confidence_pct = round(float(diag.confidence) / 100.0, 2) if diag.confidence else None
        events.append(
            {
                "eventId": f"diag_{diag.id}",
                "eventType": "diagnosis_detected",
                "timestamp": diag.diagnosed_at.isoformat() if diag.diagnosed_at else "",
                "actor": "system",
                "title": f"系统发现异常：{label_name}",
                "description": (
                    f"严重度：{_severity_label(diag.severity)}，置信度：{confidence_pct or 'N/A'}"
                ),
                "meta": {
                    "label": diag.diag_label,
                    "labelName": label_name,
                    "confidence": confidence_pct,
                    "severity": diag.severity,
                    "compositeScore": diag.composite_score,
                },
            }
        )

    # ---------- 2. Tracker 状态变更事件（从 sys_audit_log 解析）----------
    # 先找到该回路关联的 tracker
    tracker_stmt = (
        select(ActionTracker)
        .where(ActionTracker.loop_id == loop_id)
        .order_by(ActionTracker.created_at.desc())
        .limit(5)
    )
    trackers = (await db.execute(tracker_stmt)).scalars().all()
    tracker_ids = [str(t.id) for t in trackers]
    current_tracker = trackers[0] if trackers else None

    if tracker_ids:
        audit_stmt = (
            select(SysAuditLog)
            .where(SysAuditLog.target_type == "action_tracker")
            .where(SysAuditLog.target_id.in_(tracker_ids))
            .where(SysAuditLog.operation_type == "TRACKER_STATUS_UPDATE")
            .order_by(SysAuditLog.operated_at.asc())
        )
        audit_rows = (await db.execute(audit_stmt)).scalars().all()

        for audit in audit_rows:
            try:
                before = json.loads(audit.before_value or "{}")
                after = json.loads(audit.after_value or "{}")
            except (json.JSONDecodeError, TypeError):
                continue

            before_status = before.get("actionStatus")
            after_status = after.get("actionStatus")
            if not after_status or after_status == before_status:
                # 备注更新等无状态变更的记录
                if after.get("remark") or after.get("comment"):
                    events.append(
                        {
                            "eventId": f"audit_{audit.id}_comment",
                            "eventType": "comment",
                            "timestamp": audit.operated_at.isoformat() if audit.operated_at else "",
                            "actor": audit.operator,
                            "title": "添加处理备注",
                            "description": after.get("comment") or after.get("remark"),
                            "meta": {},
                        }
                    )
                continue

            # 判断事件类型
            event_type: str | None = None
            title_suffix = ""
            desc = ""
            meta: dict[str, Any] = {}

            if before_status in (None, "PENDING") and after_status == "IN_PROGRESS":
                event_type = "claimed"
                title_suffix = "认领处理"
                desc = f"由 {audit.operator} 认领，开始处理"
                if after.get("assignee"):
                    desc += f"，指派给 {after['assignee']}"
            elif after_status in ("VERIFYING", "IMPLEMENTED"):
                event_type = "implemented"
                title_suffix = "现场实施完成"
                new_pid = after.get("newPid") or {}
                pid_str = ""
                if new_pid:
                    pid_str = (
                        f"新PID参数：P={new_pid.get('p')}, "
                        f"I={new_pid.get('i')}, D={new_pid.get('d')}。"
                    )
                moc_ref = after.get("mocRef")
                moc_str = f"MOC单号：{moc_ref}" if moc_ref else "MOC不适用"
                desc = f"由 {after.get('implementedBy') or audit.operator} 实施。{pid_str}{moc_str}"
                meta = {
                    "newPid": new_pid,
                    "mocRef": after.get("mocRef"),
                    "mocNotApplicable": after.get("mocNotApplicable"),
                }
            elif after_status == "CLOSED":
                event_type = "verification_passed"
                title_suffix = "验证通过，闭环完成"
                desc = "A/B对比验证显示指标改善，异常闭环"
            elif after_status == "REOPENED":
                event_type = "verification_failed"
                title_suffix = "验证未通过，重新打开"
                desc = "A/B对比验证未达预期，需重新诊断处理"
            elif after_status == "IGNORED":
                event_type = "ignored"
                title_suffix = "忽略此异常"
                desc = f"由 {audit.operator} 标记为忽略"

            if event_type:
                events.append(
                    {
                        "eventId": f"audit_{audit.id}",
                        "eventType": event_type,
                        "timestamp": audit.operated_at.isoformat() if audit.operated_at else "",
                        "actor": audit.operator,
                        "title": title_suffix or _TIMELINE_EVENT_TITLES.get(event_type, "状态变更"),
                        "description": desc,
                        "meta": meta,
                    }
                )

    # ---------- 3. 整定事件（如果有整定记录，通过 tuning_result 表查）----------
    # 简化处理：不引入新表依赖，整定事件由前端在进入整定页时独立标记
    # （后续 Phase 2 整定闭环接入后再完善）

    # ---------- 排序：按时间升序 ----------
    events.sort(key=lambda e: e["timestamp"])

    # ---------- 计算预计验证时间 ----------
    pending_verification_at: str | None = None
    if (
        current_tracker
        and current_tracker.action_status == "VERIFYING"
        and current_tracker.implemented_at
    ):
        # 从 sys_config 读取验证周期（默认24小时）
        cfg_result = await db.execute(
            select(SysConfig).where(SysConfig.key == TRACKER_VERIFICATION_INTERVAL_KEY)
        )
        cfg = cfg_result.scalar_one_or_none()
        try:
            interval_hours = (
                int(cfg.value) if cfg and cfg.value else TRACKER_VERIFICATION_INTERVAL_DEFAULT
            )
        except (ValueError, TypeError):
            interval_hours = TRACKER_VERIFICATION_INTERVAL_DEFAULT
        verify_dt = current_tracker.implemented_at + timedelta(hours=interval_hours)
        pending_verification_at = (
            verify_dt.isoformat()
            if verify_dt.tzinfo is None
            else verify_dt.replace(tzinfo=None).isoformat()
        )

    return {
        "loopId": loop_id,
        "tagName": loop.tag_name,
        "currentStatus": current_tracker.action_status if current_tracker else None,
        "events": events,
        "pendingVerificationAt": pending_verification_at,
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

    # 1. 时间窗口内已实施数（IMPLEMENTED/VERIFYING/CLOSED 状态均表示已完成实施）
    # P1a 兼容：VERIFYING=实施后等待验证，CLOSED=验证通过闭环，IMPLEMENTED=历史兼容
    impl_statuses = ("IMPLEMENTED", "VERIFYING", "CLOSED")
    impl_stmt = select(func.count(ActionTracker.id)).where(
        ActionTracker.action_status.in_(impl_statuses),
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

    # 3. 当前待验证数（VERIFYING 且 effect_verified IS NULL，不限窗口）
    # P1a: 原 IMPLEMENTED 待验证改为 VERIFYING 状态（历史 IMPLEMENTED 且未验证也纳入）
    pending_stmt = select(func.count(ActionTracker.id)).where(
        ActionTracker.action_status.in_(("VERIFYING", "IMPLEMENTED")),
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
    "get_loop_timeline",
    "get_verification_config",
    "get_tracker_effectiveness",
    "update_tracker_status",
    "update_verification_config",
]
