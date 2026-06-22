"""Audit log query service (S5-SYS-002).

Provides paginated audit log queries with filters (operator / operation_type /
time range). Audit logs are immutable — no delete or update operations.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import SysAuditLog


async def list_audit_logs(
    db: AsyncSession,
    *,
    operator: str | None = None,
    operation_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """Paginated audit log list with optional filters.

    Returns ``{"items": [...], "total": N, "page": P, "pageSize": S}``.
    """
    stmt = select(SysAuditLog)

    if operator:
        stmt = stmt.where(SysAuditLog.operator == operator)
    if operation_type:
        stmt = stmt.where(SysAuditLog.operation_type == operation_type)
    if start_time:
        from datetime import datetime

        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            start_dt = datetime.fromisoformat(start_time)
        stmt = stmt.where(SysAuditLog.operated_at >= start_dt.replace(tzinfo=None))
    if end_time:
        from datetime import datetime

        try:
            end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            end_dt = datetime.fromisoformat(end_time)
        stmt = stmt.where(SysAuditLog.operated_at <= end_dt.replace(tzinfo=None))

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    stmt = stmt.order_by(SysAuditLog.operated_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    logs = result.scalars().all()

    return {
        "items": [_audit_log_to_dict(log) for log in logs],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


def _audit_log_to_dict(log: SysAuditLog) -> dict:
    """Convert an audit log record to a response dict."""
    before_value = _safe_json_loads(log.before_value)
    after_value = _safe_json_loads(log.after_value)
    return {
        "logId": str(log.id),
        "operator": log.operator,
        "operationType": log.operation_type,
        "targetType": log.target_type,
        "targetId": str(log.target_id) if log.target_id else None,
        "beforeValue": before_value,
        "afterValue": after_value,
        "operatedAt": log.operated_at.isoformat() if log.operated_at else None,
        "clientIp": None,  # sys_audit_log model does not track client IP
    }


def _safe_json_loads(value: str | None) -> dict | str | None:
    """Safely parse a JSON string, returning the raw value on failure."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


__all__ = ["list_audit_logs"]
