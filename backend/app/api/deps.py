"""Shared FastAPI dependencies: JWT extraction, user lookup, RBAC guard.

Usage in endpoints::

    @router.get("/me", dependencies=[Depends(get_current_user)])
    async def me(user: SysUser = Depends(get_current_user)): ...

    @router.delete("/{id}", dependencies=[Depends(require_roles("ADMIN"))])
    async def delete_loop(...): ...

    @router.get("", dependencies=[Depends(require_perms("loop:view"))])
    async def list_loops(...): ...
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.exceptions import BizError
from app.core.security import JWTError, decode_token
from app.models.sys_user import SysUser
from app.services.auth import ROLE_PERMISSIONS, is_token_blacklisted

# Token extraction — auto-populates Swagger UI "Authorize" button.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

# 强制改密豁免路径（S5-AUTH P1）：must_change_password=True 时仅放行
# 改密与登出写端点（登出放行避免用户无法重新登录）；读端点（GET/HEAD/OPTIONS）
# 一律放行，前端可正常加载改密页所需数据，避免死锁
_FORCE_PASSWORD_CHANGE_EXEMPT_PATHS = frozenset(
    {
        "/api/v1/auth/password",
        "/api/v1/auth/logout",
    }
)
_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def get_current_user(
    request: Request,
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

    # S5-AUTH P1：首次登录强制改密——标志为 True 时拒绝所有写操作
    # （改密/登出豁免，读端点放行避免前端死锁）。
    # ``is True`` 严格判断：真实列为 bool；测试 mock 用户未设置该属性时放行。
    if (
        getattr(user, "must_change_password", False) is True
        and request.method not in _READ_ONLY_METHODS
        and request.url.path not in _FORCE_PASSWORD_CHANGE_EXEMPT_PATHS
    ):
        raise BizError(
            code="ERR_PASSWORD_CHANGE_REQUIRED",
            message="首次登录须先修改密码后再执行此操作",
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


# ---------------------------------------------------------------------------
# Permission-code guard（"模块:操作" 码，与前端 v-permission 口径一致）
# ---------------------------------------------------------------------------


def _perm_matches(granted: str, required: str) -> bool:
    """Match one granted permission code against a required code.

    通配规则（与前端 v-permission 一致）：
    - ``*`` 全通（ADMIN）；
    - ``模块:*`` 匹配该模块下任意操作码（如 ``loop:*`` 匹配 ``loop:view``）；
    - 其余按精确匹配。
    """
    if granted == "*" or granted == required:
        return True
    return granted.endswith(":*") and required.startswith(granted[:-1])


def has_perms(role: str, *codes: str) -> bool:
    """Return True if ``role``'s granted codes cover all of ``codes``."""
    granted = ROLE_PERMISSIONS.get(role, [])
    return all(any(_perm_matches(g, code) for g in granted) for code in codes)


def require_perms(*codes: str) -> Callable[..., Awaitable[SysUser]]:
    """Return a dependency that enforces the user's role holds all ``codes``.

    基于 ``ROLE_PERMISSIONS`` 映射做服务端权限码校验（P2 D5：权限码只下发
    不执行的问题修复，先覆盖敏感读端点）。用法::

        @router.get("", dependencies=[Depends(require_perms("loop:view"))])
        async def list_loops(...): ...
    """

    async def _check(user: SysUser = Depends(get_current_user)) -> SysUser:
        if not has_perms(user.role, *codes):
            raise BizError(
                code="ERR_PERMISSION_DENIED",
                message=f"权限不足，需要权限码: {', '.join(codes)}",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return user

    return _check


__all__ = [
    "get_current_user",
    "has_perms",
    "oauth2_scheme",
    "require_perms",
    "require_roles",
]
