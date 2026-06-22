"""``tag_registry`` model — AAS-synced OPC tag registry."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TagRegistry(Base):
    """AAS Tag registry — OPC tag metadata synced from AAS (DDL §4)."""

    __tablename__ = "tag_registry"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tag_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag_type: Mapped[str] = mapped_column(String(20), nullable=False)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_linked: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "tag_type IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D', 'OTHER')",
            name="ck_tag_registry_type",
        ),
        CheckConstraint(
            "quality IS NULL OR quality IN ('GOOD', 'BAD', 'UNCERTAIN')",
            name="ck_tag_registry_quality",
        ),
        Index("uk_tag_registry_tag_name", "tag_name", unique=True),
        Index("idx_tag_registry_tag_name", "tag_name"),
        Index("idx_tag_registry_tag_type", "tag_type"),
        Index("idx_tag_registry_is_linked", "is_linked"),
    )
