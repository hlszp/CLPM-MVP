"""Authentication endpoints (IDS v3.2 §5).

- POST   /api/v1/auth/login    — Login (public)
- POST   /api/v1/auth/refresh  — Refresh access token (public, needs refresh token)
- POST   /api/v1/auth/logout   — Logout (authenticated)
- GET    /api/v1/auth/me       — Current user info (authenticated)
- GET    /api/v1/auth/effective-access — 有效权限视图+策略 revision（P4-04 轮询锚）
- PUT    /api/v1/auth/password — Change password (authenticated)
- GET    /api/v1/auth/rbac-test — RBAC test endpoint (ADMIN only)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, oauth2_scheme, require_roles
from app.core.db import get_db
from app.core.modules import get_enabled_modules
from app.core.security import get_client_ip
from app.models.sys_user import SysUser
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginData,
    LoginRequest,
    RefreshData,
    RefreshRequest,
    UserInfo,
)
from app.schemas.authz import MyEffectiveAccess, UserScopeView
from app.schemas.common import ApiResponse, success
from app.services import authz_catalog as az
from app.services.auth import (
    authenticate,
    change_password,
    get_default_home,
    logout,
    refresh_tokens,
)
from app.services.authz_runtime import get_effective_codes, get_role_codes

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_user_info(user: SysUser, effective: dict[str, list[str]]) -> dict:
    """Build the user info dict for login / me responses.

    P4-02：permissions/pageCodes 改由 DB 持久授权矩阵（sys_role_permission，
    经 authz_runtime.get_effective_codes）下发——显式码集合（无通配），
    数据库保存驱动真实授权，不依赖前端常量。
    """
    last_login = user.last_login_at.isoformat() if user.last_login_at else None
    return {
        "id": str(user.id),
        "username": user.username,
        "displayName": user.display_name,
        "email": user.email,
        "role": user.role,
        "permissions": effective["actions"],
        "pageCodes": effective["pages"],
        "defaultHome": get_default_home(user.role),
        "lastLoginAt": last_login,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=ApiResponse[LoginData])
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """User login — returns access + refresh tokens and user info."""
    client_ip = get_client_ip(request)
    user, tokens = await authenticate(
        db=db,
        username=body.username,
        password=body.password,
        remember_me=body.rememberMe,
        device_ip=client_ip,
    )
    effective = await get_effective_codes(db, user.role)
    return success(
        data={
            "accessToken": tokens.accessToken,
            "refreshToken": tokens.refreshToken,
            "tokenType": "Bearer",
            "expiresIn": tokens.expiresIn,
            "user": _build_user_info(user, effective),
        }
    )


@router.post("/refresh", response_model=ApiResponse[RefreshData])
async def refresh(body: RefreshRequest, request: Request) -> dict:
    """Refresh access token using a valid refresh token."""
    client_ip = get_client_ip(request)
    tokens = await refresh_tokens(body.refreshToken, device_ip=client_ip)
    return success(
        data={
            "accessToken": tokens.accessToken,
            "refreshToken": tokens.refreshToken,
            "tokenType": "Bearer",
            "expiresIn": tokens.expiresIn,
        }
    )


@router.post("/logout", response_model=ApiResponse[dict])
async def logout_endpoint(
    token: str | None = Depends(oauth2_scheme),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """Logout — blacklist the current access token (requires authentication)."""
    if token:
        await logout(token)
    return success(data=None)


@router.get("/me", response_model=ApiResponse[UserInfo])
async def me(
    user: SysUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get current user info including permissions and default home."""
    effective = await get_effective_codes(db, user.role)
    return success(data=_build_user_info(user, effective))


@router.get("/effective-access", response_model=ApiResponse[MyEffectiveAccess])
async def effective_access(
    user: SysUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """当前登录主体的有效权限视图（01§3.5 建议接口；P4-04 在线生效轮询锚点）.

    - ``policyRevision``：前端 ≤30s 轮询比对锚——变更后前端刷新路由/菜单/按钮，
      满足"另一会话无重登在线生效"；服务端即时校验由 ``authz_runtime`` 每请求
      revision 比对独立保证（本端点仅服务于 UI 生效节奏）。
    - 解释与实际 403 同源：码集合来自 ``authz_runtime.get_role_codes``（与
      ``deps.require_perms`` 同一函数），不另立第二套解释器。
    - ``unavailableByModule``：模块禁用下不可用的已授权码（授权只收紧不放宽，
      模块开关与 fitness 等业务门禁在授权之外独立执行）。
    - ``scope``：当前用户数据范围（无显式行按 ALL，消费侧 P4-03 贯穿执行）。
    """
    revision = await az.get_current_revision(db)
    codes = await get_role_codes(db, user.role)
    effective = az.get_effective_access(
        user.role, set(codes), enabled_modules=get_enabled_modules()
    )
    scope_row = await az.get_user_scope(db, str(user.id))
    return success(
        data=MyEffectiveAccess(
            policyRevision=revision,
            role=user.role,
            pages=effective["pages"],
            actions=effective["actions"],
            unavailableByModule=effective["unavailableByModule"],
            scope=UserScopeView(
                scopeType=scope_row.scope_type if scope_row else "ALL",
                nodeId=str(scope_row.node_id) if scope_row and scope_row.node_id else None,
            ),
            note=effective["note"],
        ).model_dump()
    )


@router.put("/password", response_model=ApiResponse[dict])
async def change_password_endpoint(
    body: ChangePasswordRequest,
    user: SysUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Change the current user's password. Revokes all existing tokens."""
    await change_password(
        db=db,
        user=user,
        old_password=body.oldPassword,
        new_password=body.newPassword,
    )
    return success(data=None, message="密码修改成功，请重新登录")


@router.get("/rbac-test", response_model=ApiResponse[dict])
async def rbac_test(
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """RBAC test endpoint — only ADMIN can access.

    Used by tests to verify the role-based access control guard.
    """
    return success(data={"role": user.role, "message": "RBAC check passed"})
