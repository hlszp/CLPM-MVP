"""``action_tracker`` model — lightweight anomaly tracking records."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ActionTracker(Base):
    """Lightweight anomaly tracking record (DDL §10).

    整改计划 D2：补全 created_at / comment / moc_ref / diagnosis_result_id
    列与 (loop_id, diagnosis_label) 开放态唯一约束。同一回路同一标签在
    PENDING/IN_PROGRESS 状态下不可重复建单（D1 自动建单依赖此约束），
    闭环后新诊断可再建新单（历史记录保留）。
    """

    __tablename__ = "action_tracker"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=True,
    )
    diagnosis_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    evidence_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # D2: 建单时间（闭环时长统计 = updated_at - created_at）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("now()")
    )
    # D2: 处理意见/审查备注
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # D3: MOC（变更管理）关联
    moc_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    moc_not_applicable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    moc_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # D2: 诊断结果外键（软删除不级联，保留历史）
    diagnosis_result_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("diagnosis_result.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'IMPLEMENTED')",
            name="ck_action_tracker_status",
        ),
        # D2: 同一回路同一标签在开放态（PENDING/IN_PROGRESS）下唯一，
        # 闭环后允许新建（历史保留）。D1 自动建单依赖此约束防重复。
        Index(
            "uk_action_tracker_open",
            "loop_id",
            "diagnosis_label",
            unique=True,
            postgresql_where=text(
                "action_status IN ('PENDING', 'IN_PROGRESS') "
                "AND loop_id IS NOT NULL AND diagnosis_label IS NOT NULL"
            ),
        ),
        Index("idx_action_tracker_loop_id", "loop_id"),
        Index("idx_action_tracker_action_status", "action_status"),
    )
