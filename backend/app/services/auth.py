"""Authentication business logic.

Handles login, token refresh, logout, password change, and role-permission
mapping. Uses Redis for login-failure counting, token blacklist, and
user-token tracking.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.core.redis import redis_client
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_token_remaining_ttl,
    hash_password,
    verify_password,
    verify_refresh_token,
)
from app.models.audit import SysAuditLog
from app.models.sys_user import SysUser
from app.schemas.auth import AuthTokens

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOGIN_FAIL_MAX_ATTEMPTS = 5
LOGIN_FAIL_WINDOW_MINUTES = 15

# Redis key templates.
KEY_LOGIN_FAIL = "login_fail:{username}"
KEY_TOKEN_BLACKLIST = "token_blacklist:{jti}"
KEY_USER_TOKENS = "user_tokens:{user_id}"
KEY_TOKEN_PAIR = "token_pair:{access_jti}"

# Blacklist TTL upper bound: the longest possible token lifetime
# (remember-me refresh token = 30 days).
MAX_BLACKLIST_TTL = 30 * 24 * 3600

# ---------------------------------------------------------------------------
# Role → permissions mapping (aligned with PRD §3 and IDS §5.4).
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "ADMIN": ["*"],
    "IC_ENGINEER": [
        "loop:*",
        "metric:*",
        "diagnosis:*",
        "tuning:*",
        "portal:view",
    ],
    "PE_ENGINEER": [
        # WS-D 性能#7 R1：放开回路配置入口（create/edit/export），对齐后端 require_roles
        # 不含 loop:delete（ADMIN 专属）、loop:import（IC_ENGINEER 专属）
        "loop:view",
        "loop:create",
        "loop:edit",
        "loop:export",
        "metric:view",
        "diagnosis:view",
        "portal:view",
        "tracker:*",
    ],
    "SPONSOR": [
        "portal:view",
        "metric:view",
        "diagnosis:view",
    ],
    "EXPERT": [
        "portal:view",
        "metric:view",
        "diagnosis:view",
        "tracker:review",
        # 实现契约 §5：EXPERT 可查看整定相关页面（整定写端点本就对 EXPERT 开放）
        "tuning:view",
    ],
}

ROLE_DEFAULT_HOME: dict[str, str] = {
    "ADMIN": "/dashboard",
    "IC_ENGINEER": "/dashboard",
    "PE_ENGINEER": "/dashboard",
    "SPONSOR": "/dashboard",
    "EXPERT": "/dashboard",
}


def get_permissions(role: str) -> list[str]:
    """Return the permission list for a role."""
    return ROLE_PERMISSIONS.get(role, [])


def get_default_home(role: str) -> str:
    """Return the default home path for a role."""
    return ROLE_DEFAULT_HOME.get(role, "/dashboard")


# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------


async def _check_login_lock(username: str) -> int:
    """Return current failure count. Raises BizError if locked."""
    key = KEY_LOGIN_FAIL.format(username=username)
    count_str = await redis_client.get(key)
    count = int(count_str) if count_str else 0
    if count >= LOGIN_FAIL_MAX_ATTEMPTS:
        ttl = await redis_client.ttl(key)
        raise BizError(
            code="ERR_TOO_MANY_ATTEMPTS",
            message=f"登录失败次数过多，请 {max(ttl, 0) // 60 + 1} 分钟后再试",
            status_code=429,
        )
    return count


async def _record_login_fail(username: str) -> int:
    """Increment failure counter. Returns new count."""
    key = KEY_LOGIN_FAIL.format(username=username)
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, LOGIN_FAIL_WINDOW_MINUTES * 60)
    return count


async def _clear_login_fails(username: str) -> None:
    """Clear failure counter on successful login."""
    key = KEY_LOGIN_FAIL.format(username=username)
    await redis_client.delete(key)


async def _is_token_blacklisted(jti: str) -> bool:
    """Check if a token jti is in the blacklist."""
    key = KEY_TOKEN_BLACKLIST.format(jti=jti)
    return bool(await redis_client.exists(key))


async def _blacklist_token(jti: str, ttl: int) -> None:
    """Add a token jti to the blacklist with the given TTL (seconds)."""
    key = KEY_TOKEN_BLACKLIST.format(jti=jti)
    await redis_client.setex(key, ttl, "1")


async def _track_user_token(user_id: str, jti: str, ttl: int) -> None:
    """Track a jti under the user's token set (for batch revocation).

    Each member is stored as ``"{jti}:{exp_epoch}"`` so batch revocation can
    blacklist every token for exactly its remaining lifetime (remember-me
    refresh tokens live 30 days, well beyond a plain 7-day blacklist TTL).
    """
    key = KEY_USER_TOKENS.format(user_id=user_id)
    exp_epoch = int(datetime.now(UTC).timestamp()) + ttl
    await redis_client.sadd(key, f"{jti}:{exp_epoch}")
    await redis_client.expire(key, ttl)


async def _track_token_pair(access_jti: str, refresh_jti: str, refresh_ttl: int) -> None:
    """Record which refresh token was issued together with an access token.

    The pair key expires together with the refresh token, so its remaining
    TTL mirrors the refresh token's remaining lifetime — used by ``logout``
    to blacklist the paired refresh token for exactly as long as needed.
    """
    key = KEY_TOKEN_PAIR.format(access_jti=access_jti)
    await redis_client.setex(key, refresh_ttl, refresh_jti)


async def _revoke_all_user_tokens(user_id: str) -> None:
    """Blacklist all tracked jtis for a user (used on password change).

    Each blacklist entry lives for the token's actual remaining lifetime
    (capped at 30 days) so a remember-me refresh token cannot resurrect
    after a short fixed TTL expires.
    """
    key = KEY_USER_TOKENS.format(user_id=user_id)
    members = await redis_client.smembers(key)
    now = int(datetime.now(UTC).timestamp())
    for member in members:
        member_str = member if isinstance(member, str) else member.decode()
        jti, sep, exp_str = member_str.rpartition(":")
        if not sep or not exp_str.isdigit():
            # Legacy member without expiry info — fall back to the max lifetime.
            jti, ttl = member_str, MAX_BLACKLIST_TTL
        else:
            ttl = min(max(int(exp_str) - now, 1), MAX_BLACKLIST_TTL)
        await _blacklist_token(jti, ttl)
    await redis_client.delete(key)


# ---------------------------------------------------------------------------
# Core auth operations
# ---------------------------------------------------------------------------


async def authenticate(
    db: AsyncSession,
    username: str,
    password: str,
    remember_me: bool = False,
    device_ip: str | None = None,
) -> tuple[SysUser, AuthTokens]:
    """Authenticate a user and return ``(user, tokens)``.

    Raises ``BizError`` with appropriate error codes on failure.

    Args:
        device_ip: 客户端 IP 地址（用于 Refresh Token 设备绑定 S4-C2）
    """
    # Check lock before querying DB.
    await _check_login_lock(username)

    # Query user.
    result = await db.execute(select(SysUser).where(SysUser.username == username))
    user = result.scalar_one_or_none()

    if user is None:
        await _record_login_fail(username)
        # 登录失败审计日志（S1-B8）
        db.add(
            SysAuditLog(
                id=str(uuid4()),
                operator=username,
                operation_type="LOGIN_FAILED",
                target_type="User",
                target_id=None,
                after_value="reason=user_not_found",
                operated_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db.commit()
        # 统一错误信息，防止用户名枚举攻击（S1-B6）
        raise BizError(
            code="ERR_INVALID_CREDENTIALS",
            message="用户名或密码错误",
            status_code=400,
        )

    # Verify password.
    if not verify_password(password, user.password_hash):
        await _record_login_fail(username)
        # 登录失败审计日志（S1-B8）
        db.add(
            SysAuditLog(
                id=str(uuid4()),
                operator=username,
                operation_type="LOGIN_FAILED",
                target_type="User",
                target_id=str(user.id),
                after_value="reason=wrong_password",
                operated_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        await db.commit()
        raise BizError(
            code="ERR_INVALID_CREDENTIALS",
            message="用户名或密码错误",
            status_code=400,
        )

    # Check if account is active.
    if not user.is_active:
        raise BizError(
            code="ERR_ACCOUNT_DISABLED",
            message="账户已禁用，请联系管理员",
            status_code=403,
        )

    # Success — clear failure counter.
    await _clear_login_fails(username)

    # Issue tokens.
    tokens = await _issue_tokens(user, remember_me, device_ip=device_ip)

    # Update last_login_at (DB column is TIMESTAMP WITHOUT TIME ZONE).
    await db.execute(
        update(SysUser)
        .where(SysUser.id == str(user.id))
        .values(last_login_at=datetime.now(UTC).replace(tzinfo=None))
    )
    # 登录成功审计日志（S1-B8）
    db.add(
        SysAuditLog(
            id=str(uuid4()),
            operator=user.username,
            operation_type="LOGIN",
            target_type="User",
            target_id=str(user.id),
            after_value=f"role={user.role}",
            operated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await db.commit()

    return user, tokens


async def _issue_tokens(
    user: SysUser, remember_me: bool = False, device_ip: str | None = None
) -> AuthTokens:
    """Issue access + refresh tokens and track them in Redis.

    Args:
        device_ip: 客户端 IP 地址（绑定到 Refresh Token，S4-C2）
    """
    user_id = str(user.id)
    access_token, access_jti, expires_in = create_access_token(
        subject=user_id, username=user.username, role=user.role
    )
    refresh_token, refresh_jti, _ = create_refresh_token(
        subject=user_id, remember_me=remember_me, device_ip=device_ip
    )

    # Track jtis for batch revocation.
    refresh_ttl = 30 * 24 * 3600 if remember_me else 7 * 24 * 3600
    await _track_user_token(user_id, access_jti, expires_in)
    await _track_user_token(user_id, refresh_jti, refresh_ttl)
    # Pair access → refresh so logout can revoke both (P1 token lifecycle).
    await _track_token_pair(access_jti, refresh_jti, refresh_ttl)

    return AuthTokens(
        accessToken=access_token,
        refreshToken=refresh_token,
        accessJti=access_jti,
        refreshJti=refresh_jti,
        expiresIn=expires_in,
    )


async def refresh_tokens(
    refresh_token_str: str,
    device_ip: str | None = None,
) -> AuthTokens:
    """Validate a refresh token and issue a new token pair.

    Raises ``BizError`` with ``ERR_TOKEN_EXPIRED`` or ``ERR_TOKEN_INVALID``.

    Args:
        device_ip: 当前请求的客户端 IP（用于设备绑定校验 S4-C2，
            为 None 时跳过校验，向后兼容）
    """
    from app.core.security import JWTError

    try:
        payload = verify_refresh_token(refresh_token_str, device_ip=device_ip)
    except JWTError as exc:
        # pyjwt raises ExpiredSignatureError (subclass of PyJWTError) for expired.
        err_code = "ERR_TOKEN_EXPIRED" if "expired" in str(exc).lower() else "ERR_TOKEN_INVALID"
        msg = (
            "Refresh Token 已过期，请重新登录" if err_code == "ERR_TOKEN_EXPIRED" else "Token 无效"
        )
        raise BizError(
            code=err_code,
            message=msg,
            status_code=401,
        ) from exc
    except ValueError:
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="Token 类型错误，非 Refresh Token",
            status_code=401,
        ) from None

    jti = payload.get("jti", "")
    if not jti:
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="Token 缺少 jti",
            status_code=401,
        )

    if await _is_token_blacklisted(jti):
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="Token 已被吊销",
            status_code=401,
        )

    user_id = payload.get("sub", "")
    if not user_id:
        raise BizError(
            code="ERR_TOKEN_INVALID",
            message="Token 缺少用户信息",
            status_code=401,
        )

    # We need the user's role/username for the new access token.
    # Query the DB to get fresh user info.
    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SysUser).where(SysUser.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise BizError(
                code="ERR_USER_NOT_FOUND",
                message="用户不存在",
                status_code=404,
            )
        if not user.is_active:
            raise BizError(
                code="ERR_ACCOUNT_DISABLED",
                message="账户已禁用",
                status_code=403,
            )

        # Blacklist old refresh token.
        old_ttl = get_token_remaining_ttl(payload)
        await _blacklist_token(jti, old_ttl)

        # Issue new tokens（继承设备绑定）.
        tokens = await _issue_tokens(user, remember_me=False, device_ip=device_ip)
        return tokens


async def logout(access_token_str: str) -> None:
    """Blacklist the current access token and its paired refresh token.

    The paired refresh token is located via the ``token_pair:{access_jti}``
    key recorded at issuance; without this, a logged-out session could keep
    minting new access tokens for up to 7/30 days.

    Raises ``BizError`` if the token is invalid.
    """
    from app.core.security import JWTError

    try:
        payload = decode_token(access_token_str)
    except JWTError as exc:
        err_code = "ERR_TOKEN_EXPIRED" if "expired" in str(exc).lower() else "ERR_TOKEN_INVALID"
        raise BizError(
            code=err_code,
            message="Token 已过期" if err_code == "ERR_TOKEN_EXPIRED" else "Token 无效",
            status_code=401,
        ) from exc

    jti = payload.get("jti", "")
    if not jti:
        return

    ttl = get_token_remaining_ttl(payload)
    await _blacklist_token(jti, ttl)

    # Revoke the refresh token issued together with this access token.
    pair_key = KEY_TOKEN_PAIR.format(access_jti=jti)
    refresh_jti = await redis_client.get(pair_key)
    if refresh_jti:
        refresh_jti_str = refresh_jti if isinstance(refresh_jti, str) else refresh_jti.decode()
        # The pair key expires with the refresh token, so its remaining TTL
        # mirrors the refresh token's remaining lifetime.
        refresh_ttl = max(await redis_client.ttl(pair_key), 1)
        await _blacklist_token(refresh_jti_str, refresh_ttl)
        await redis_client.delete(pair_key)


async def change_password(
    db: AsyncSession,
    user: SysUser,
    old_password: str,
    new_password: str,
) -> None:
    """Change the current user's password and revoke all existing tokens.

    同时清除 must_change_password 首次登录强制改密标志（S5-AUTH P1）。

    Raises ``BizError`` with ``ERR_INVALID_CREDENTIALS`` or ``ERR_PASSWORD_SAME``.
    """
    # Verify old password.
    if not verify_password(old_password, user.password_hash):
        raise BizError(
            code="ERR_INVALID_CREDENTIALS",
            message="当前密码错误",
            status_code=400,
        )

    # Check new != old.
    if old_password == new_password:
        raise BizError(
            code="ERR_PASSWORD_SAME",
            message="新密码不能与旧密码相同",
            status_code=400,
        )

    # Update password（同时清除首次登录强制改密标志 S5-AUTH P1）.
    new_hash = hash_password(new_password)
    user_id_str = str(user.id)
    await db.execute(
        update(SysUser)
        .where(SysUser.id == user_id_str)
        .values(password_hash=new_hash, must_change_password=False)
    )
    await db.commit()

    # Revoke all existing tokens for this user.
    await _revoke_all_user_tokens(user_id_str)


async def is_token_blacklisted(jti: str) -> bool:
    """Public accessor for the blacklist check (used by deps)."""
    return await _is_token_blacklisted(jti)


__all__ = [
    "KEY_LOGIN_FAIL",
    "KEY_TOKEN_BLACKLIST",
    "KEY_USER_TOKENS",
    "LOGIN_FAIL_MAX_ATTEMPTS",
    "ROLE_DEFAULT_HOME",
    "ROLE_PERMISSIONS",
    "authenticate",
    "change_password",
    "get_default_home",
    "get_permissions",
    "is_token_blacklisted",
    "logout",
    "refresh_tokens",
]
