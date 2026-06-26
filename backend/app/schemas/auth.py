"""Authentication-related Pydantic schemas (IDS v3.2 §5)."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# Password policy (S1-B4)
# ---------------------------------------------------------------------------

# 弱密码字典（常见易猜密码）
_WEAK_PASSWORDS = frozenset(
    {
        "password", "password1", "password123",
        "12345678", "123456789", "1234567890",
        "admin123", "admin1234", "administrator",
        "qwerty123", "qwertyui",
        "abc12345", "abcd1234",
        "welcome1", "welcome123",
        "letmein1", "letmein123",
        "changeme1", "changeme123",
    }
)

_SPECIAL_CHAR_RE = re.compile(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]")


def validate_password_strength(v: str) -> str:
    """密码复杂度校验：最少 8 字符，需包含大小写字母、数字和特殊字符。

    开发环境（DEBUG=True）放宽为仅校验长度 ≥ 4，便于使用 admin123 等简单密码。

    Args:
        v: 明文密码

    Returns:
        校验通过的密码

    Raises:
        ValueError: 密码不符合复杂度要求
    """
    from app.core.config import settings

    # 开发环境：仅校验最小长度
    if settings.DEBUG:
        if len(v) < 4:
            raise ValueError("密码长度不得少于 4 字符")
        return v

    # 生产环境：完整复杂度校验
    if len(v) < 8:
        raise ValueError("密码长度不得少于 8 字符")

    if v.lower() in _WEAK_PASSWORDS:
        raise ValueError("密码过于简单，请使用更复杂的密码")

    has_upper = any(c.isupper() for c in v)
    has_lower = any(c.islower() for c in v)
    has_digit = any(c.isdigit() for c in v)
    has_special = bool(_SPECIAL_CHAR_RE.search(v))

    missing = []
    if not has_upper:
        missing.append("大写字母")
    if not has_lower:
        missing.append("小写字母")
    if not has_digit:
        missing.append("数字")
    if not has_special:
        missing.append("特殊字符")

    if missing:
        raise ValueError(f"密码需包含 {'、'.join(missing)}")

    return v

# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class LoginRequest(CamelModel):
    """POST /api/v1/auth/login request body."""

    username: str = Field(..., min_length=1, max_length=50, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码（明文）")
    rememberMe: bool = Field(False, description="记住登录（延长 Refresh Token 至 30 天）")


class UserInfo(CamelModel):
    """User info block returned in login / me responses."""

    id: str
    username: str
    displayName: str
    email: str | None = None
    role: str
    permissions: list[str]
    defaultHome: str = "/dashboard"
    lastLoginAt: str | None = None


class LoginData(CamelModel):
    """Login response ``data`` block."""

    accessToken: str
    refreshToken: str
    tokenType: str = "Bearer"
    expiresIn: int
    user: UserInfo


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class RefreshRequest(CamelModel):
    """POST /api/v1/auth/refresh request body."""

    refreshToken: str = Field(..., description="有效的 Refresh Token")


class RefreshData(CamelModel):
    """Refresh response ``data`` block."""

    accessToken: str
    refreshToken: str
    tokenType: str = "Bearer"
    expiresIn: int


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


class ChangePasswordRequest(CamelModel):
    """PUT /api/v1/auth/password request body."""

    oldPassword: str = Field(..., min_length=1, max_length=128)
    newPassword: str = Field(..., min_length=8, max_length=64)

    @field_validator("newPassword")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        """新密码需包含大小写字母、数字和特殊字符（S1-B4 增强）。"""
        return validate_password_strength(v)


# ---------------------------------------------------------------------------
# Auth service result types (internal)
# ---------------------------------------------------------------------------


class AuthTokens(CamelModel):
    """Token pair issued by the auth service."""

    accessToken: str
    refreshToken: str
    accessJti: str
    refreshJti: str
    expiresIn: int


__all__ = [
    "AuthTokens",
    "ChangePasswordRequest",
    "LoginData",
    "LoginRequest",
    "RefreshData",
    "RefreshRequest",
    "UserInfo",
    "validate_password_strength",
]
