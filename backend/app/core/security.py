"""Security utilities — JWT issuance/verification + bcrypt password hashing.

JWT payload fields (aligned with IDS v3.2 §5.6):
- ``sub``: user ID (UUID string)
- ``username``: username (access token only)
- ``role``: role enum (access token only)
- ``type``: ``access`` or ``refresh``
- ``iat``: issued-at timestamp
- ``exp``: expiry timestamp
- ``jti``: unique token ID (for blacklist revocation)
- ``device``: 客户端 IP 地址（仅 Refresh Token，用于设备绑定校验 S4-C2）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import bcrypt
import jwt
from jwt import PyJWTError as JWTError
from starlette.requests import Request

from app.core.config import settings
from app.core.exceptions import BizError

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(raw: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# 客户端 IP 提取（S4-C2 设备绑定）
# ---------------------------------------------------------------------------


def get_client_ip(request: Request) -> str | None:
    """从请求中获取客户端真实 IP 地址（考虑代理转发场景）。

    优先从 ``X-Forwarded-For`` 请求头获取原始客户端 IP（代理转发场景），
    回退到直接连接的客户端 IP。如果均不可用则返回 None。

    Args:
        request: FastAPI/Starlette 请求对象

    Returns:
        客户端 IP 地址字符串，或 None（不可用时跳过设备绑定检查）
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For 可能包含多个 IP，取第一个（最原始的客户端 IP）
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


# ---------------------------------------------------------------------------
# Token creation
# ---------------------------------------------------------------------------


def _create_token(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Issue a JWT and return ``(token, jti)``."""
    now = datetime.now(UTC)
    jti = str(uuid4())
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": jti,
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti


def create_access_token(
    subject: str,
    username: str,
    role: str,
) -> tuple[str, str, int]:
    """Issue a short-lived access JWT.

    Returns ``(token, jti, expires_in_seconds)``.
    """
    delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    token, jti = _create_token(
        subject=subject,
        token_type="access",
        expires_delta=delta,
        extra={"username": username, "role": role},
    )
    return token, jti, int(delta.total_seconds())


def create_refresh_token(
    subject: str,
    remember_me: bool = False,
    device_ip: str | None = None,
) -> tuple[str, str, int]:
    """Issue a long-lived refresh JWT.

    Returns ``(token, jti, expires_in_seconds)``.

    Args:
        subject: 用户 ID
        remember_me: 是否延长有效期至 30 天
        device_ip: 客户端 IP 地址（用于设备绑定校验 S4-C2，为 None 时不绑定）
    """
    days = settings.REFRESH_TOKEN_EXPIRE_DAYS if not remember_me else 30
    delta = timedelta(days=days)
    extra: dict[str, Any] = {}
    if device_ip:
        extra["device"] = device_ip
    token, jti = _create_token(
        subject=subject,
        token_type="refresh",
        expires_delta=delta,
        extra=extra,
    )
    return token, jti, int(delta.total_seconds())


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises ``JWTError`` on failure."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def verify_refresh_token(
    token: str,
    device_ip: str | None = None,
) -> dict[str, Any]:
    """解码并验证 Refresh Token（S4-C2 设备绑定）。

    验证内容：
    - Token 签名和有效期（通过 ``decode_token``，失败时抛出 ``JWTError``）
    - Token 类型为 ``refresh``
    - 设备 IP 一致性：如果 Token 中绑定了 ``device`` 且当前请求提供了 ``device_ip``，
      则两者必须一致，否则抛出 ``BizError(ERR_TOKEN_DEVICE_MISMATCH)``。
      如果任一端缺失则跳过检查（向后兼容）。

    Args:
        token: Refresh Token 字符串
        device_ip: 当前请求的客户端 IP（可选，用于设备绑定校验）

    Returns:
        JWT payload dict

    Raises:
        JWTError: Token 无效或已过期
        ValueError: Token 类型错误（非 Refresh Token）
        BizError: 设备 IP 不匹配 (``ERR_TOKEN_DEVICE_MISMATCH``)
    """
    payload = decode_token(token)

    if payload.get("type") != "refresh":
        raise ValueError("Token 类型错误，非 Refresh Token")

    # 设备绑定检查：Token 和请求都包含设备信息时才校验
    token_device = payload.get("device")
    if token_device and device_ip and token_device != device_ip:
        raise BizError(
            code="ERR_TOKEN_DEVICE_MISMATCH",
            message="Token 设备信息不匹配，请重新登录",
            status_code=401,
        )

    return payload


def get_token_remaining_ttl(payload: dict[str, Any]) -> int:
    """Return remaining TTL (seconds) for a token payload, minimum 1."""
    exp = payload.get("exp")
    if exp is None:
        return 1
    now = datetime.now(UTC).timestamp()
    remaining = int(exp - now)
    return max(remaining, 1)


__all__ = [
    "JWTError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_client_ip",
    "get_token_remaining_ttl",
    "hash_password",
    "verify_password",
    "verify_refresh_token",
]
