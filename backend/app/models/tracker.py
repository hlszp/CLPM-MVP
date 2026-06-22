"""``action_tracker`` model — lightweight anomaly tracking records."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ActionTracker(Base):
    """Lightweight anomaly tracking record (DDL §10)."""

    __tablename__ = "action_tracker"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    loop_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=True,
    )
    diagnosis_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    evidence_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'RESOLVED')",
            name="ck_action_tracker_status",
        ),
        Index("idx_action_tracker_loop_id", "loop_id"),
        Index("idx_action_tracker_action_status", "action_status"),
    )
