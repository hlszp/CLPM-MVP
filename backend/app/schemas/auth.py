"""Authentication-related Pydantic schemas (IDS v3.2 §5)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """POST /api/v1/auth/login request body."""

    username: str = Field(..., min_length=1, max_length=50, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码（明文）")
    rememberMe: bool = Field(False, description="记住登录（延长 Refresh Token 至 30 天）")


class UserInfo(BaseModel):
    """User info block returned in login / me responses."""

    id: str
    username: str
    displayName: str
    email: str | None = None
    role: str
    permissions: list[str]
    defaultHome: str = "/dashboard"
    lastLoginAt: str | None = None


class LoginData(BaseModel):
    """Login response ``data`` block."""

    accessToken: str
    refreshToken: str
    tokenType: str = "Bearer"
    expiresIn: int
    user: UserInfo


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class RefreshRequest(BaseModel):
    """POST /api/v1/auth/refresh request body."""

    refreshToken: str = Field(..., description="有效的 Refresh Token")


class RefreshData(BaseModel):
    """Refresh response ``data`` block."""

    accessToken: str
    refreshToken: str
    tokenType: str = "Bearer"
    expiresIn: int


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


class ChangePasswordRequest(BaseModel):
    """PUT /api/v1/auth/password request body."""

    oldPassword: str = Field(..., min_length=1, max_length=128)
    newPassword: str = Field(..., min_length=6, max_length=64)

    @field_validator("newPassword")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        """新密码需包含字母+数字（IDS §5.5）。"""
        has_letter = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_letter and has_digit):
            raise ValueError("新密码需包含字母和数字")
        return v


# ---------------------------------------------------------------------------
# Auth service result types (internal)
# ---------------------------------------------------------------------------


class AuthTokens(BaseModel):
    """Token pair issued by the auth service."""

    access_token: str
    refresh_token: str
    access_jti: str
    refresh_jti: str
    expires_in: int


__all__ = [
    "AuthTokens",
    "ChangePasswordRequest",
    "LoginData",
    "LoginRequest",
    "RefreshData",
    "RefreshRequest",
    "UserInfo",
]
