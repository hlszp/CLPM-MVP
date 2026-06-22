"""Shared FastAPI dependencies: JWT extraction, user lookup, RBAC guard.

Usage in endpoints::

    @router.get("/me", dependencies=[Depends(get_current_user)])
    async def me(user: SysUser = Depends(get_current_user)): ...

    @router.delete("/{id}", dependencies=[Depends(require_roles("ADMIN"))])
    async def delete_loop(...): ...
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.security import JWTError, decode_token
from app.models.sys_user import SysUser
from app.services.auth import is_token_blacklisted

# Token extraction — auto-populates Swagger UI "Authorize" button.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> SysUser:
    """Decode the JWT, check the blacklist, and return the active ``SysUser``.

    Raises ``BizError`` with ``ERR_TOKEN_INVALID`` / ``ERR_TOKEN_EXPIRED`` /
    ``ERR_ACCOUNT_DISABLED`` on failure.
    """
    if not token:
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="未携带认证 Token",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        payload: dict[str, Any] = decode_token(token)
    except JWTError as exc:
        err_code = "ERR_TOKEN_EXPIRED" if "expired" in str(exc).lower() else "ERR_TOKEN_INVALID"
        raise BizError(
            code=err_code,
            message="Token 已过期，请重新登录" if err_code == "ERR_TOKEN_EXPIRED" else "Token 无效",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    # Must be an access token.
    if payload.get("type") != "access":
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="Token 类型错误，非 Access Token",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    jti = payload.get("jti", "")
    if jti and await is_token_blacklisted(jti):
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="Token 已被吊销，请重新登录",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user_id = payload.get("sub", "")
    if not user_id:
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="Token 缺少用户信息",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    result = await db.execute(select(SysUser).where(SysUser.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise BizError(
            code="ERR_USER_NOT_FOUND",
            message="用户不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not user.is_active:
        raise BizError(
            code="ERR_ACCOUNT_DISABLED",
            message="账户已禁用",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return user


def require_roles(*roles: str) -> Callable[..., Awaitable[SysUser]]:
    """Return a dependency that enforces the user's role is in ``roles``.

    Usage::

        @router.post("/users", dependencies=[Depends(require_roles("ADMIN"))])
        async def create_user(...): ...
    """

    async def _check(user: SysUser = Depends(get_current_user)) -> SysUser:
        if user.role not in roles:
            raise BizError(
                code="ERR_PERMISSION_DENIED",
                message=f"权限不足，需要以下角色之一: {', '.join(roles)}",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return user

    return _check


__all__ = [
    "get_current_user",
    "oauth2_scheme",
    "require_roles",
]
