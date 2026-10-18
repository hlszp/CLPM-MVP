"""诊断关键配置变更审批服务（C5 审批流，ADS §1）。

职责：
- 变更请求 CRUD（PENDING → APPROVED/REJECTED）
- "双人确认"：审批人不能与申请人相同
- 审批通过后自动应用变更（原子切换）
- 全程审计日志

设计依据：整改计划 C5 / ADS §1
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.audit import SysAuditLog
from app.models.diagnosis import (
    DiagnosisConfig,
    DiagnosisConfigChange,
    DiagnosisRule,
)

logger = logging.getLogger(__name__)


async def create_change_request(
    db: AsyncSession,
    operator: str,
    *,
    target_type: str,
    target_id: str,
    change_type: str,
    before_value: dict | None = None,
    after_value: dict | None = None,
) -> dict:
    """创建关键配置变更请求（状态 PENDING）。

    Raises:
        BizError: ERR_INVALID_TARGET / ERR_INVALID_CHANGE_TYPE
    """
    valid_targets = {"config", "rule", "trigger"}
    if target_type not in valid_targets:
        raise BizError(
            code="ERR_INVALID_TARGET",
            message=f"target_type 必须为 {valid_targets} 之一",
            status_code=422,
        )
    valid_types = {"update", "enable", "disable"}
    if change_type not in valid_types:
        raise BizError(
            code="ERR_INVALID_CHANGE_TYPE",
            message=f"change_type 必须为 {valid_types} 之一",
            status_code=422,
        )

    before_json = (
        json.dumps(before_value, ensure_ascii=False, default=str) if before_value else None
    )
    after_json = json.dumps(after_value, ensure_ascii=False, default=str) if after_value else None

    change = DiagnosisConfigChange(
        id=str(uuid4()),
        target_type=target_type,
        target_id=target_id,
        change_type=change_type,
        before_value=before_json,
        after_value=after_json,
        status="PENDING",
        requested_by=operator,
    )
    db.add(change)
    await db.commit()

    logger.info(
        "诊断配置变更请求已创建: id=%s, target=%s/%s, type=%s, by=%s",
        change.id,
        target_type,
        target_id,
        change_type,
        operator,
    )
    return _change_to_dict(change)


async def list_change_requests(
    db: AsyncSession,
    status: str | None = None,
    target_type: str | None = None,
) -> list[dict]:
    """列出变更请求（可按状态/目标类型筛选）。"""
    stmt = select(DiagnosisConfigChange).order_by(DiagnosisConfigChange.requested_at.desc())
    if status:
        stmt = stmt.where(DiagnosisConfigChange.status == status)
    if target_type:
        stmt = stmt.where(DiagnosisConfigChange.target_type == target_type)
    result = await db.execute(stmt)
    return [_change_to_dict(c) for c in result.scalars().all()]


async def approve_change_request(
    db: AsyncSession,
    change_id: str,
    reviewer: str,
    review_note: str | None = None,
) -> dict:
    """审批通过变更请求并自动应用变更。

    "双人确认"：审批人不能与申请人相同。

    Raises:
        BizError: ERR_CHANGE_NOT_FOUND / ERR_CHANGE_NOT_PENDING /
                  ERR_SELF_APPROVAL / ERR_AFTER_VALUE_PARSE
    """
    result = await db.execute(
        select(DiagnosisConfigChange).where(DiagnosisConfigChange.id == change_id)
    )
    change = result.scalar_one_or_none()
    if change is None:
        raise BizError(
            code="ERR_CHANGE_NOT_FOUND",
            message="变更请求不存在",
            status_code=404,
        )

    if change.status != "PENDING":
        raise BizError(
            code="ERR_CHANGE_NOT_PENDING",
            message=f"变更请求状态为 {change.status}，无法审批",
            status_code=422,
        )

    # 双人确认：审批人不能与申请人相同
    if change.requested_by == reviewer:
        raise BizError(
            code="ERR_SELF_APPROVAL",
            message="审批人不能与申请人相同（双人确认原则）",
            status_code=422,
        )

    # 解析 after_value
    after_dict = None
    if change.after_value:
        try:
            after_dict = json.loads(change.after_value)
        except (json.JSONDecodeError, TypeError) as exc:
            raise BizError(
                code="ERR_AFTER_VALUE_PARSE",
                message=f"after_value 解析失败: {exc}",
                status_code=422,
            ) from exc

    # 应用变更到目标
    await _apply_change(db, change, after_dict, reviewer)

    # 更新审批状态
    now = datetime.now(UTC).replace(tzinfo=None)
    change.status = "APPROVED"
    change.reviewed_by = reviewer
    change.reviewed_at = now
    change.review_note = review_note
    change.effective_from = now

    # 审计日志
    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=reviewer,
        operation_type="DIAG_CONFIG_APPROVE",
        target_type="diagnosis_config_change",
        target_id=str(change.id),
        before_value=change.before_value,
        after_value=change.after_value,
        operated_at=now,
    )
    db.add(audit_log)
    await db.commit()

    logger.info("诊断配置变更已审批通过: id=%s, reviewer=%s", change.id, reviewer)
    return _change_to_dict(change)


async def reject_change_request(
    db: AsyncSession,
    change_id: str,
    reviewer: str,
    review_note: str | None = None,
) -> dict:
    """拒绝变更请求。

    Raises:
        BizError: ERR_CHANGE_NOT_FOUND / ERR_CHANGE_NOT_PENDING /
                  ERR_SELF_APPROVAL
    """
    result = await db.execute(
        select(DiagnosisConfigChange).where(DiagnosisConfigChange.id == change_id)
    )
    change = result.scalar_one_or_none()
    if change is None:
        raise BizError(
            code="ERR_CHANGE_NOT_FOUND",
            message="变更请求不存在",
            status_code=404,
        )

    if change.status != "PENDING":
        raise BizError(
            code="ERR_CHANGE_NOT_PENDING",
            message=f"变更请求状态为 {change.status}，无法拒绝",
            status_code=422,
        )

    if change.requested_by == reviewer:
        raise BizError(
            code="ERR_SELF_APPROVAL",
            message="审批人不能与申请人相同（双人确认原则）",
            status_code=422,
        )

    now = datetime.now(UTC).replace(tzinfo=None)
    change.status = "REJECTED"
    change.reviewed_by = reviewer
    change.reviewed_at = now
    change.review_note = review_note

    audit_log = SysAuditLog(
        id=str(uuid4()),
        operator=reviewer,
        operation_type="DIAG_CONFIG_REJECT",
        target_type="diagnosis_config_change",
        target_id=str(change.id),
        before_value=change.before_value,
        after_value=change.after_value,
        operated_at=now,
    )
    db.add(audit_log)
    await db.commit()

    logger.info("诊断配置变更已拒绝: id=%s, reviewer=%s", change.id, reviewer)
    return _change_to_dict(change)


async def _apply_change(
    db: AsyncSession,
    change: DiagnosisConfigChange,
    after_dict: dict | None,
    reviewer: str,
) -> None:
    """审批通过后自动应用变更到目标对象。"""
    if after_dict is None:
        return

    if change.target_type == "config":
        result = await db.execute(
            select(DiagnosisConfig).where(DiagnosisConfig.id == change.target_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            raise BizError(
                code="ERR_DIAG_CONFIG_NOT_FOUND",
                message="诊断指标配置不存在",
                status_code=404,
            )
        if "threshold" in after_dict:
            config.threshold = after_dict["threshold"]
        if "diagName" in after_dict:
            config.diag_name = after_dict["diagName"]
        if "isEnabled" in after_dict:
            config.is_enabled = after_dict["isEnabled"]
        if "params" in after_dict:
            config.params = after_dict["params"]
        config.version = (config.version or 1) + 1
        config.updated_by = reviewer
        config.updated_at = datetime.now(UTC).replace(tzinfo=None)

    elif change.target_type == "rule":
        result = await db.execute(select(DiagnosisRule).where(DiagnosisRule.id == change.target_id))
        rule = result.scalar_one_or_none()
        if rule is None:
            raise BizError(
                code="ERR_RULE_NOT_FOUND",
                message="专家规则不存在",
                status_code=404,
            )
        if "conditionExpr" in after_dict:
            rule.condition_expr = after_dict["conditionExpr"]
        if "isEnabled" in after_dict:
            rule.is_enabled = after_dict["isEnabled"]
        if "priority" in after_dict:
            rule.priority = after_dict["priority"]
        if "actionType" in after_dict:
            rule.action_type = after_dict["actionType"]
        if "actionParams" in after_dict:
            rule.action_params = after_dict["actionParams"]
        rule.version = (rule.version or 1) + 1
        rule.updated_by = reviewer
        rule.updated_at = datetime.now(UTC).replace(tzinfo=None)
        # 失效规则缓存
        from app.services.diagnosis_rule import invalidate_rule_cache

        await invalidate_rule_cache()

    elif change.target_type == "trigger":
        # 触发条件变更：写入 sys_config 并刷新进程内缓存
        from app.services import diagnosis_trigger_config as svc

        # 补充审批人信息后写入
        after_dict["updatedBy"] = reviewer
        after_dict["updatedAt"] = datetime.now(UTC).replace(tzinfo=None).isoformat()
        payload_json = json.dumps(after_dict, ensure_ascii=False)
        await svc.set_config_value(
            db, svc.SYS_CONFIG_KEY, payload_json, svc.SYS_CONFIG_DESC, reviewer
        )
        svc.apply_runtime(after_dict)


def _change_to_dict(c: DiagnosisConfigChange) -> dict:
    """变更请求转字典。"""
    before_dict = None
    if c.before_value:
        try:
            before_dict = json.loads(c.before_value)
        except (json.JSONDecodeError, TypeError):
            before_dict = None
    after_dict = None
    if c.after_value:
        try:
            after_dict = json.loads(c.after_value)
        except (json.JSONDecodeError, TypeError):
            after_dict = None
    return {
        "changeId": str(c.id),
        "targetType": c.target_type,
        "targetId": c.target_id,
        "changeType": c.change_type,
        "beforeValue": before_dict,
        "afterValue": after_dict,
        "status": c.status,
        "requestedBy": c.requested_by,
        "requestedAt": c.requested_at.isoformat() if c.requested_at else None,
        "reviewedBy": c.reviewed_by,
        "reviewedAt": c.reviewed_at.isoformat() if c.reviewed_at else None,
        "reviewNote": c.review_note,
        "effectiveFrom": c.effective_from.isoformat() if c.effective_from else None,
    }
