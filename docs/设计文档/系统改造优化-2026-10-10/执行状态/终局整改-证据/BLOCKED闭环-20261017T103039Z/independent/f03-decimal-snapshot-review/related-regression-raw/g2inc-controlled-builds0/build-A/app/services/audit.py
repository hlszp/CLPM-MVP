"""Audit log query service (S5-SYS-002).

Provides paginated audit log queries with filters (operator / operation_type /
time range). Audit logs are immutable — no delete or update operations.
"""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeparse import parse_iso_datetime, to_naive_utc
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


#: 单次全量导出上限（审计取证场景；超出时在首行口径注释中注明截断）
AUDIT_EXPORT_MAX_ROWS = 20_000


async def export_audit_logs(
    db: AsyncSession,
    *,
    operator: str | None = None,
    operation_type: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_rows: int = AUDIT_EXPORT_MAX_ROWS,
) -> tuple[list[dict], int]:
    """按筛选条件**全量**导出审计日志（2026-09-24 新增）。

    背景：页面内导出只导当前页（20 条）却提示"已导出 N 条"，审计取证时会被
    误当全量。本函数与 list_audit_logs 共用完全相同的过滤语义，但不分页，
    仅受 max_rows 上限保护（返回 (rows, total) 供调用方注明是否截断）。

    过滤语义与列表接口一致：operator 精确匹配、operation_type 精确匹配、
    时间按 operated_at 闭区间。
    """
    stmt = select(SysAuditLog)
    if operator:
        stmt = stmt.where(SysAuditLog.operator == operator)
    if operation_type:
        stmt = stmt.where(SysAuditLog.operation_type == operation_type)
    if start_time:
        start_dt = parse_iso_datetime(start_time, field="startTime")
        stmt = stmt.where(SysAuditLog.operated_at >= to_naive_utc(start_dt))
    if end_time:
        end_dt = parse_iso_datetime(end_time, field="endTime")
        stmt = stmt.where(SysAuditLog.operated_at <= to_naive_utc(end_dt))

    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0)
    stmt = stmt.order_by(SysAuditLog.operated_at.desc()).limit(max_rows)
    rows = (await db.execute(stmt)).scalars().all()
    return [_audit_log_to_dict(row) for row in rows], total


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
