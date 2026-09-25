"""Audit log endpoints (S5-SYS-002).

Routes:
- GET /api/v1/audit-logs          — Paginated audit log query (ADMIN only)
- GET /api/v1/audit-logs/export   — Full CSV export of the filtered logs (ADMIN only)

Audit logs are immutable — no create/update/delete endpoints are exposed.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.audit import AuditLogListData
from app.schemas.common import ApiResponse, success
from app.services.audit import (
    export_audit_logs,
    list_audit_logs,
)

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


@router.get("", response_model=ApiResponse[AuditLogListData])
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


@router.get("/export", response_class=PlainTextResponse)
async def export_audit_logs_endpoint(
    operator: str | None = Query(None, description="按操作人筛选"),
    operationType: str | None = Query(None, description="按操作类型筛选"),
    startTime: str | None = Query(None, description="开始时间（ISO 8601）"),
    endTime: str | None = Query(None, description="结束时间（ISO 8601）"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> PlainTextResponse:
    """审计日志全量导出（CSV，UTF-8 BOM；仅 ADMIN）。

    2026-09-24 新增：与列表接口共用同一筛选语义，但**不分页**（上限
    ``AUDIT_EXPORT_MAX_ROWS``，超出在首行注明），解决"页面导出只导当前页
    却提示已导出 N 条"的审计取证陷阱；首行以 ``#`` 注释写入口径
    （生成时间/生成人/筛选条件/条数），导出的材料可自证范围。
    """
    rows, total = await export_audit_logs(
        db,
        operator=operator,
        operation_type=operationType,
        start_time=startTime,
        end_time=endTime,
    )

    trunc_note = ""
    if total > len(rows):
        trunc_note = f"；已截断到前 {len(rows)} 条（共 {total} 条，请收窄筛选）"
    scope_line = (
        f"# 审计日志导出 生成时间={datetime.now(UTC).isoformat()} "
        f"生成人={user.username} 口径：操作人={operator or '全部'}；"
        f"操作类型={operationType or '全部'}；时间={startTime or '不限'}~{endTime or '不限'}；"
        f"共 {len(rows)} 条{trunc_note}"
    )

    buffer = io.StringIO()
    buffer.write("\ufeff")
    buffer.write(scope_line + "\n")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "时间",
            "操作人",
            "操作类型",
            "资源类型",
            "资源 ID",
            "变更前",
            "变更后",
            "客户端 IP",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.get("operatedAt") or "",
                r.get("operator") or "",
                r.get("operationType") or "",
                r.get("targetType") or "",
                r.get("targetId") or "",
                "" if r.get("beforeValue") is None else str(r.get("beforeValue")),
                "" if r.get("afterValue") is None else str(r.get("afterValue")),
                r.get("clientIp") or "",
            ]
        )

    filename = f"audit-log-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.csv"
    return PlainTextResponse(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
