"""``sys_user`` model — login/authentication user table."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SysUser(Base):
    """User table for login/authentication (DDL §1)."""

    __tablename__ = "sys_user"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    # Workbench v2.0: 泳道容量上限（看板拖拽时 UI 过载提示）
    lane_capacity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("6"), default=6
    )
    # 首次登录强制改密标志（S5-AUTH P1）：种子用户由迁移置 True，
    # 改密成功后在 change_password 中清除
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'IC_ENGINEER', 'PE_ENGINEER', 'SPONSOR', 'EXPERT')",
            name="ck_sys_user_role",
        ),
        UniqueConstraint("username", name="uk_sys_user_username"),
        UniqueConstraint("email", name="uk_sys_user_email"),
        Index("idx_sys_user_is_active", "is_active"),
    )
