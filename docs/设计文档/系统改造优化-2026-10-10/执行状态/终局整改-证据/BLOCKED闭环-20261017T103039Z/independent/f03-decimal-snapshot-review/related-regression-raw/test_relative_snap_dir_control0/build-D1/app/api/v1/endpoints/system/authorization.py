"""授权管理端点（P4-01）— /api/v1/system/authorization/*（全部 ADMIN）.

- GET  ``/catalog``：页面码/动作码目录（含依赖/模块标注/保护标记）
- GET  ``/roles``：五角色当前矩阵 + revision
- GET  ``/roles/{role}``：单角色矩阵 + 有效授权视图（依赖闭包 + 模块启用标注）
- PUT  ``/roles/{role}``：更新角色矩阵（expectedRevision 乐观锁 409；
  未知码/漏依赖/管理员保护拒绝；SysAuditLog 审计 + 策略账本）
- GET  ``/users``：用户 scope 列表
- PUT  ``/users/{user_id}/scope``：更新用户数据范围（ALL/NODE_SUBTREE）
- GET  ``/revisions``：策略版本账本（回退支撑）
- POST ``/revisions/{revision}/rollback``：回退=发布逆操作

端点本身经 ``system:authorization-manage``（protected）语义由 ADMIN 角色守卫；
服务端 authorizer 的统一接线（require_perms 映射到新目录）属 P4-02。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.modules import get_enabled_modules
from app.models.authz import SysAuthzPolicy, SysUserScope
from app.models.sys_user import SysUser
from app.schemas.authz import (
    CatalogItem,
    CatalogResponse,
    EffectiveAccessResponse,
    PolicyRevisionInfo,
    PolicyRevisionsResponse,
    RoleGrants,
    RolesResponse,
    RoleUpdateRequest,
    RoleUpdateResponse,
    RollbackRequest,
    RollbackResponse,
    ScopeUpdateRequest,
    ScopeUpdateResponse,
    UserScopeInfo,
)
from app.schemas.common import ApiResponse, success
from app.services import authz_catalog as az

router = APIRouter(prefix="/system/authorization", tags=["system-authorization"])


def _catalog_item(code: str, d: az.PermDef) -> CatalogItem:
    return CatalogItem(
        code=code,
        kind=d.kind,
        parentCode=d.parent,
        requiresCodes=list(d.requires),
        moduleKey=d.module_key,
        isProtected=d.protected,
        description=d.description,
        sortOrder=d.order,
    )


@router.get("/catalog", response_model=ApiResponse[CatalogResponse])
async def get_catalog(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """页面码/动作码目录（无机械通配展开；码间依赖显式）。"""
    revision = await az.get_current_revision(db)
    pages = [_catalog_item(c, d) for c, d in az.CATALOG.items() if d.kind != "ACTION"]
    actions = [_catalog_item(c, d) for c, d in az.CATALOG.items() if d.kind == "ACTION"]
    return success(
        CatalogResponse(
            revision=revision,
            pages=pages,
            actions=actions,
            protectedCodes=sorted(az.PROTECTED_CODES),
            roles=list(az.VALID_ROLES),
            note=(
                "目录不接受通配码（loop:* 不自动获得 delete）；module enabled 与 "
                "fitness 门禁在授权之外独立执行（授权只收紧不放宽）"
            ),
        ).model_dump()
    )


@router.get("/roles", response_model=ApiResponse[RolesResponse])
async def get_roles(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """五角色当前授权矩阵。"""
    revision = await az.get_current_revision(db)
    roles = []
    for role in az.VALID_ROLES:
        grants = await az.get_role_grants(db, role)
        roles.append(RoleGrants(role=role, **grants).model_dump())
    return success(RolesResponse(revision=revision, roles=roles).model_dump())


@router.get("/roles/{role}", response_model=ApiResponse[EffectiveAccessResponse])
async def get_role_effective(
    role: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """单角色矩阵 + 有效授权视图（依赖闭包展开 + 模块启用标注）。"""
    if role not in az.VALID_ROLES:
        raise BizError(
            code="ERR_AUTHZ_ROLE_NOT_FOUND",
            message=f"未知角色: {role}",
            status_code=404,
        )
    grants = await az.get_role_grants(db, role)
    effective = az.get_effective_access(
        role,
        set(grants["pages"]) | set(grants["actions"]),
        enabled_modules=get_enabled_modules(),
    )
    effective["pages"] = grants["pages"]  # 页面按显式授权展示（不闭包展开）
    return success(EffectiveAccessResponse(**effective).model_dump())


@router.put("/roles/{role}", response_model=ApiResponse[RoleUpdateResponse])
async def put_role(
    role: str,
    body: RoleUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新角色授权矩阵（expectedRevision 409 + 完整校验链 + 审计）。"""
    result = await az.update_role_grants(
        db,
        role=role,
        expected_revision=body.expectedRevision,
        page_codes=body.pageCodes,
        action_codes=body.actionCodes,
        reason=body.reason,
        operator=user.username,
    )
    return success(RoleUpdateResponse(**result).model_dump())


@router.get("/users", response_model=ApiResponse[list[UserScopeInfo]])
async def list_user_scopes(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """全部用户 scope 列表（无 scope 行=未显式配置，消费侧按 ALL）。"""
    result = await db.execute(
        select(SysUser, SysUserScope)
        .outerjoin(SysUserScope, SysUserScope.user_id == SysUser.id)
        .order_by(SysUser.username)
    )
    items = []
    for u, s in result.fetchall():
        items.append(
            UserScopeInfo(
                userId=str(u.id),
                username=u.username,
                displayName=u.display_name,
                role=u.role,
                scopeType=s.scope_type if s else "ALL",
                nodeId=s.node_id if s else None,
            ).model_dump()
        )
    return success(items)


@router.put("/users/{user_id}/scope", response_model=ApiResponse[ScopeUpdateResponse])
async def put_user_scope(
    user_id: str,
    body: ScopeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新用户数据范围（ALL/NODE_SUBTREE + plant_node 绑定校验）。"""
    result = await az.update_user_scope(
        db,
        user_id=user_id,
        expected_revision=body.expectedRevision,
        scope_type=body.scopeType,
        node_id=body.nodeId,
        reason=body.reason,
        operator=user.username,
    )
    return success(ScopeUpdateResponse(**result).model_dump())


@router.get("/revisions", response_model=ApiResponse[PolicyRevisionsResponse])
async def get_revisions(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """策略版本账本（倒序；回退支撑视图）。"""
    revision = await az.get_current_revision(db)
    result = await db.execute(
        select(SysAuthzPolicy).order_by(SysAuthzPolicy.revision.desc()).limit(200)
    )
    items = []
    for row in result.scalars():
        items.append(
            PolicyRevisionInfo(
                revision=row.revision,
                operation=row.operation,
                role=row.role,
                userId=str(row.user_id) if row.user_id else None,
                reason=row.reason,
                operator=row.operator,
                beforeValue=row.before_value,
                afterValue=row.after_value,
                rollbackOf=row.rollback_of,
                createdAt=row.created_at.isoformat() if row.created_at else None,
            ).model_dump()
        )
    return success(PolicyRevisionsResponse(revision=revision, revisions=items).model_dump())


@router.post("/revisions/{revision}/rollback", response_model=ApiResponse[RollbackResponse])
async def post_rollback(
    revision: int,
    body: RollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """回退=发布逆操作（恢复目标版本 before 快照为新 revision；不删历史）。"""
    result = await az.rollback_revision(
        db,
        revision=revision,
        expected_revision=body.expectedRevision,
        reason=body.reason,
        operator=user.username,
    )
    return success(RollbackResponse(**result).model_dump())
