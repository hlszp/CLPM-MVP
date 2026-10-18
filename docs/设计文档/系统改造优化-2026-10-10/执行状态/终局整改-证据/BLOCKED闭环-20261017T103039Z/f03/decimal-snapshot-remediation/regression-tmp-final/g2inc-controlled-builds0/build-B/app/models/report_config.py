"""``report_config`` model — auto-report configuration (DDS §2.13 reference).

Stores report generation configurations: period, recipients, content template.
The actual generated report archives are stored in ``report_record``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReportConfig(Base):
    """Auto-report configuration (period / recipients / content template)."""

    __tablename__ = "report_config"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    report_period: Mapped[str] = mapped_column(String(20), nullable=False)
    recipients: Mapped[str] = mapped_column(Text, nullable=False)
    content_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "report_period IN ('SHIFT', 'DAILY', 'WEEKLY', 'MONTHLY')",
            name="ck_report_config_period",
        ),
        Index("idx_report_config_period", "report_period"),
        Index("idx_report_config_is_enabled", "is_enabled"),
    )
