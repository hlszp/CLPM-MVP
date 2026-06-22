"""User management schemas (S5-SYS-001)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class UserCreateRequest(BaseModel):
    """POST /api/v1/users request body."""

    username: str = Field(..., min_length=1, max_length=50, description="用户名")
    password: str = Field(..., min_length=6, max_length=64, description="密码（明文）")
    displayName: str = Field(..., min_length=1, max_length=100, description="姓名")
    email: str | None = Field(None, max_length=255, description="邮箱")
    role: str = Field(..., description="角色：ADMIN/IC_ENGINEER/PE_ENGINEER/SPONSOR/EXPERT")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"ADMIN", "IC_ENGINEER", "PE_ENGINEER", "SPONSOR", "EXPERT"}
        if v not in allowed:
            raise ValueError(f"角色必须是 {allowed} 之一")
        return v

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        has_letter = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_letter and has_digit):
            raise ValueError("密码需包含字母和数字")
        return v


class UserUpdateRequest(BaseModel):
    """PUT /api/v1/users/{id} request body (partial update)."""

    displayName: str | None = Field(None, min_length=1, max_length=100)
    email: str | None = Field(None, max_length=255)
    role: str | None = None
    isActive: bool | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"ADMIN", "IC_ENGINEER", "PE_ENGINEER", "SPONSOR", "EXPERT"}
        if v not in allowed:
            raise ValueError(f"角色必须是 {allowed} 之一")
        return v


class ResetPasswordRequest(BaseModel):
    """PUT /api/v1/users/{id}/reset-password request body."""

    newPassword: str = Field(..., min_length=6, max_length=64)

    @field_validator("newPassword")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        has_letter = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_letter and has_digit):
            raise ValueError("密码需包含字母和数字")
        return v


class UserItem(BaseModel):
    """User item in list / detail responses."""

    id: str
    username: str
    displayName: str
    email: str | None = None
    role: str
    isActive: bool | None = True
    lastLoginAt: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class UserListData(BaseModel):
    """Paginated user list response data."""

    items: list[UserItem]
    total: int
    page: int
    pageSize: int


__all__ = [
    "ResetPasswordRequest",
    "UserCreateRequest",
    "UserItem",
    "UserListData",
    "UserUpdateRequest",
]
