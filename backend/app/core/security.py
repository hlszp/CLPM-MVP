"""Security utilities — JWT issuance/verification + bcrypt password hashing.

JWT payload fields (aligned with IDS v3.2 §5.6):
- ``sub``: user ID (UUID string)
- ``username``: username (access token only)
- ``role``: role enum (access token only)
- ``type``: ``access`` or ``refresh``
- ``iat``: issued-at timestamp
- ``exp``: expiry timestamp
- ``jti``: unique token ID (for blacklist revocation)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

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
) -> tuple[str, str, int]:
    """Issue a long-lived refresh JWT.

    Returns ``(token, jti, expires_in_seconds)``.
    """
    days = settings.REFRESH_TOKEN_EXPIRE_DAYS if not remember_me else 30
    delta = timedelta(days=days)
    token, jti = _create_token(
        subject=subject,
        token_type="refresh",
        expires_delta=delta,
    )
    return token, jti, int(delta.total_seconds())


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT. Raises ``JWTError`` on failure."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


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
    "get_token_remaining_ttl",
    "hash_password",
    "verify_password",
]
