"""规则变更审计（方案 §4.4 + §5.4）。

所有规则 CRUD（CREATE/UPDATE/ENABLE/DISABLE/DELETE）写入 alert_rule_audit_log，
含 before/after JSON 快照。不写 sys_audit_log（避免污染系统级审计）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertRuleAuditLog

logger = logging.getLogger(__name__)


async def write_audit(
    db: AsyncSession,
    rule_id: str | None,
    rule_code: str,
    operation_type: str,
    operator: str,
    before_value: dict[str, Any] | None = None,
    after_value: dict[str, Any] | None = None,
) -> None:
    """写入规则变更审计日志（不提交，由调用方控制事务）。

    Args:
        db: 数据库会话
        rule_id: 规则 ID（DELETE 后规则已删，可为 None）
        rule_code: 规则代码（冗余，便于规则删除后仍可追溯）
        operation_type: 操作类型 CREATE/UPDATE/ENABLE/DISABLE/DELETE
        operator: 操作人用户名
        before_value: 变更前快照
        after_value: 变更后快照
    """
    log = AlertRuleAuditLog(
        rule_id=rule_id,
        rule_code=rule_code,
        operation_type=operation_type,
        before_value=_safe_serialize(before_value),
        after_value=_safe_serialize(after_value),
        operator=operator,
    )
    db.add(log)
    await db.flush()


def _safe_serialize(value: dict[str, Any] | None) -> str | None:
    """安全序列化为 JSON 字符串（ORM Text 字段存储）。"""
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


async def list_audit_logs(
    db: AsyncSession,
    rule_id: str | None = None,
    operator: str | None = None,
    operation_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AlertRuleAuditLog]:
    """查询规则变更审计日志列表。"""
    from sqlalchemy import select

    stmt = select(AlertRuleAuditLog).order_by(AlertRuleAuditLog.operated_at.desc())
    if rule_id:
        stmt = stmt.where(AlertRuleAuditLog.rule_id == rule_id)
    if operator:
        stmt = stmt.where(AlertRuleAuditLog.operator == operator)
    if operation_type:
        stmt = stmt.where(AlertRuleAuditLog.operation_type == operation_type)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars())
