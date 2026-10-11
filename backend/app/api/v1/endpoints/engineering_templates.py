"""工程模板接口（P2-04：版本化模板 + TEMPLATE→回路映射）.

路由清单：
- GET  /api/v1/engineering-templates                       — 模板列表（含版本数/回路数）
- POST /api/v1/engineering-templates                       — 创建模板（ADMIN）
- GET  /api/v1/engineering-templates/{id}                  — 模板详情 + 当前版本
- GET  /api/v1/engineering-templates/{id}/versions         — 版本历史（追加式账本）
- POST /api/v1/engineering-templates/{id}/versions         — 发布不可变新版本（ADMIN）
- POST /api/v1/engineering-templates/{id}/versions/{v}/rollback — 指针回拨（ADMIN）
- GET  /api/v1/engineering-templates/{id}/loops            — 关联回路（影响范围可查）

权限（DEC-03 同口径）：查询/预览对 ADMIN/IC/PE 只读开放；创建/发布/回拨
为 ADMIN。版本行追加式不可变——发布新版不自动改已固定引用（loop pin 的
版本号 / 记录的 revisionRef 解析结果不变）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Path
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.base import CamelModel
from app.schemas.common import ApiResponse, success
from app.services import engineering_template as et

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/engineering-templates", tags=["engineering-template"])

_READ_ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER")


class TemplateCreateRequest(CamelModel):
    """创建工程模板."""

    template_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="RESPONSE", pattern="^(RESPONSE|CONTROL)$")
    description: str | None = Field(default=None, max_length=500)


class TemplateVersionPublishRequest(CamelModel):
    """发布不可变新版本（追加式；expectedRevision 乐观锁）."""

    profile: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


class TemplateVersionRollbackRequest(CamelModel):
    """current_version 指针回拨到既有版本（生成新 revision，版本历史不删改）."""

    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


@router.get("", response_model=ApiResponse[dict])
async def list_templates_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """模板列表（含版本数与关联回路数——TEMPLATE→回路映射可查）."""
    items = await et.list_templates(db)
    return success(data={"items": items, "total": len(items)})


@router.post("", status_code=201, response_model=ApiResponse[dict])
async def create_template_endpoint(
    body: TemplateCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建工程模板（ADMIN；不发布版本）."""
    data = await et.create_template(
        db,
        template_key=body.template_key,
        name=body.name,
        category=body.category,
        description=body.description,
        operator=user.username,
    )
    return success(data=data, message="模板已创建")


@router.get("/{template_id}", response_model=ApiResponse[dict])
async def get_template_endpoint(
    template_id: str = Path(min_length=1),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """模板详情 + 当前版本内容（无版本时 currentVersion=null）."""
    template = await et.get_template_by_id(db, template_id)
    if template is None:
        from fastapi import status

        from app.core.exceptions import BizError

        raise BizError(
            code="ERR_TEMPLATE_NOT_FOUND",
            message=f"工程模板不存在: {template_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    current = (
        await et.get_version(db, template_id, template.current_version)
        if template.current_version is not None
        else None
    )
    loops = await et.list_loops_for_template(db, template_id)
    data = {
        **et._template_to_dict(template, loop_count=len(loops)),
        "currentVersionDetail": current,
        "loops": loops,
    }
    return success(data=data)


@router.get("/{template_id}/versions", response_model=ApiResponse[dict])
async def list_versions_endpoint(
    template_id: str = Path(min_length=1),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """版本历史（追加式不可变账本，按版本倒序）."""
    items = await et.list_versions(db, template_id)
    return success(data={"items": items, "total": len(items)})


@router.post("/{template_id}/versions", response_model=ApiResponse[dict])
async def publish_version_endpoint(
    template_id: str = Path(min_length=1),
    body: TemplateVersionPublishRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """发布不可变新版本（ADMIN；追加版本行 + 前进 current_version 指针）.

    接 P2-02 统一发布链：expectedRevision 乐观锁（冲突 409）、
    config_publication 账本（operation=TEMPLATE_VERSION）、SysAuditLog 审计。
    既有版本行零改动——已固定引用解析结果不变。
    """
    if body is None:  # pragma: no cover - FastAPI 必填体兜底
        from fastapi import status

        from app.core.exceptions import BizError

        raise BizError(
            code="ERR_PARAM_INVALID",
            message="请求体不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    result = await et.publish_template_version(
        db,
        template_id=template_id,
        profile=body.profile,
        reason=body.reason,
        operator=user.username,
        expected_revision=body.expected_revision,
    )
    return success(data=result, message="模板版本已发布（不可变，固定引用不受影响）")


@router.post("/{template_id}/versions/{version}/rollback", response_model=ApiResponse[dict])
async def rollback_version_endpoint(
    template_id: str = Path(min_length=1),
    version: int = Path(ge=1),
    body: TemplateVersionRollbackRequest | None = None,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """current_version 指针回拨到既有版本（ADMIN；版本历史保持追加式）."""
    if body is None:  # pragma: no cover
        from fastapi import status

        from app.core.exceptions import BizError

        raise BizError(
            code="ERR_PARAM_INVALID",
            message="请求体不能为空",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    result = await et.rollback_template_version(
        db,
        template_id=template_id,
        version=version,
        reason=body.reason,
        operator=user.username,
        expected_revision=body.expected_revision,
    )
    return success(data=result, message="模板当前版本已回拨（版本历史不变）")


@router.get("/{template_id}/loops", response_model=ApiResponse[dict])
async def list_template_loops_endpoint(
    template_id: str = Path(min_length=1),
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles(*_READ_ROLES)),
) -> dict:
    """模板关联回路清单（发布影响范围可查；pin/跟随状态见回路详情）."""
    template = await et.get_template_by_id(db, template_id)
    if template is None:
        from fastapi import status

        from app.core.exceptions import BizError

        raise BizError(
            code="ERR_TEMPLATE_NOT_FOUND",
            message=f"工程模板不存在: {template_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    total = await et.count_loops_for_template(db, template_id)
    items = await et.list_loops_for_template(db, template_id, limit=min(max(limit, 1), 200))
    return success(data={"total": total, "items": items})


__all__ = ["router"]
