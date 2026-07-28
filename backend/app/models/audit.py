"""``sys_audit_log`` model — system audit log (immutable)."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SysAuditLog(Base):
    """System audit log — all config changes land here (DDL §14)."""

    __tablename__ = "sys_audit_log"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    operator: Mapped[str] = mapped_column(String(50), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # target_id 可能是 loop_id/user_id/报表 id/任务 id 等非 UUID 业务标识，
    # 库中为 VARCHAR(36)，模型以库为准用 String(36)（收敛迁移 d4e5f6a7b8c9）
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    operated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_sys_audit_log_operator", "operator"),
        Index("idx_sys_audit_log_operation_type", "operation_type"),
        Index("idx_sys_audit_log_operated_at", "operated_at"),
        Index("idx_sys_audit_log_target_type", "target_type"),
    )
