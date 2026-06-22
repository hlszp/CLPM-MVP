"""Audit log schemas (S5-SYS-002)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AuditLogItem(BaseModel):
    """Audit log item in list responses."""

    logId: str
    operator: str
    operationType: str
    targetType: str | None = None
    targetId: str | None = None
    beforeValue: Any | None = None
    afterValue: Any | None = None
    operatedAt: str | None = None
    clientIp: str | None = None


class AuditLogListData(BaseModel):
    """Paginated audit log list response data."""

    items: list[AuditLogItem]
    total: int
    page: int
    pageSize: int


__all__ = [
    "AuditLogItem",
    "AuditLogListData",
]
