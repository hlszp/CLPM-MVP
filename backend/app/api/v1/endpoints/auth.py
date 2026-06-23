"""Authentication endpoints (IDS v3.2 §5).

- POST   /api/v1/auth/login    — Login (public)
- POST   /api/v1/auth/refresh  — Refresh access token (public, needs refresh token)
- POST   /api/v1/auth/logout   — Logout (authenticated)
- GET    /api/v1/auth/me       — Current user info (authenticated)
- PUT    /api/v1/auth/password — Change password (authenticated)
- GET    /api/v1/auth/rbac-test — RBAC test endpoint (ADMIN only)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, oauth2_scheme, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
)
from app.schemas.common import success
from app.services.auth import (
    authenticate,
    change_password,
    get_default_home,
    get_permissions,
    logout,
    refresh_tokens,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_user_info(user: SysUser) -> dict:
    """Build the user info dict for login / me responses."""
    last_login = user.last_login_at.isoformat() if user.last_login_at else None
    return {
        "id": str(user.id),
        "username": user.username,
        "displayName": user.display_name,
        "email": user.email,
        "role": user.role,
        "permissions": get_permissions(user.role),
        "defaultHome": get_default_home(user.role),
        "lastLoginAt": last_login,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """User login — returns access + refresh tokens and user info."""
    user, tokens = await authenticate(
        db=db,
        username=body.username,
        password=body.password,
        remember_me=body.rememberMe,
    )
    return success(
        data={
            "accessToken": tokens.access_token,
            "refreshToken": tokens.refresh_token,
            "tokenType": "Bearer",
            "expiresIn": tokens.expires_in,
            "user": _build_user_info(user),
        }
    )


@router.post("/refresh")
async def refresh(body: RefreshRequest) -> dict:
    """Refresh access token using a valid refresh token."""
    tokens = await refresh_tokens(body.refreshToken)
    return success(
        data={
            "accessToken": tokens.access_token,
            "refreshToken": tokens.refresh_token,
            "tokenType": "Bearer",
            "expiresIn": tokens.expires_in,
        }
    )


@router.post("/logout")
async def logout_endpoint(
    token: str | None = Depends(oauth2_scheme),
    user: SysUser = Depends(get_current_user),
) -> dict:
    """Logout — blacklist the current access token (requires authentication)."""
    if token:
        await logout(token)
    return success(data=None)


@router.get("/me")
async def me(user: SysUser = Depends(get_current_user)) -> dict:
    """Get current user info including permissions and default home."""
    return success(data=_build_user_info(user))


@router.put("/password")
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


@router.get("/rbac-test")
async def rbac_test(
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """RBAC test endpoint — only ADMIN can access.

    Used by tests to verify the role-based access control guard.
    """
    return success(data={"role": user.role, "message": "RBAC check passed"})
