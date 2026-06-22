"""``engine_rule`` model — engine rule configuration."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EngineRule(Base):
    """Engine rule configuration — calc cycle / data fetch / schedule (DDL §8)."""

    __tablename__ = "engine_rule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    rule_code: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_enabled: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "rule_type IN ('CALC_CYCLE', 'DATA_FETCH', 'SCHEDULE')",
            name="ck_engine_rule_type",
        ),
        Index("uk_engine_rule_code", "rule_code", unique=True),
    )
