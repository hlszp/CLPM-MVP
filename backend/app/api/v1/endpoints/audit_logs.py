"""Audit log endpoints (S5-SYS-002).

Routes:
- GET /api/v1/audit-logs — Paginated audit log query (ADMIN only)

Audit logs are immutable — no create/update/delete endpoints are exposed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.common import success
from app.services.audit import list_audit_logs

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


@router.get("")
async def list_audit_logs_endpoint(
    operator: str | None = Query(None, description="按操作人筛选"),
    operationType: str | None = Query(None, description="按操作类型筛选"),
    startTime: str | None = Query(None, description="开始时间（ISO 8601）"),
    endTime: str | None = Query(None, description="结束时间（ISO 8601）"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """分页查询审计日志（仅 ADMIN）。审计日志不可删除。"""
    data = await list_audit_logs(
        db=db,
        operator=operator,
        operation_type=operationType,
        start_time=startTime,
        end_time=endTime,
        page=page,
        page_size=pageSize,
    )
    return success(data=data)


__all__ = ["router"]
