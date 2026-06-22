"""``diagnosis_config`` and ``diagnosis_result`` models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DiagnosisConfig(Base):
    """Diagnosis metric configuration (DDL §7)."""

    __tablename__ = "diagnosis_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    diag_code: Mapped[str] = mapped_column(String(50), nullable=False)
    diag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    algorithm_type: Mapped[str] = mapped_column(String(50), nullable=False)
    calc_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    threshold: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_enabled: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, default=1, nullable=True)

    __table_args__ = (Index("uk_diagnosis_config_code", "diag_code", unique=True),)


class DiagnosisResult(Base):
    """Diagnosis engine result for a loop (DDL §11)."""

    __tablename__ = "diagnosis_result"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    loop_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=True,
    )
    diag_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    feature_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_chain: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    diagnosed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="ck_diagnosis_result_conf",
        ),
        Index("idx_diagnosis_result_loop_id", "loop_id"),
        Index("idx_diagnosis_result_diagnosed", "diagnosed_at"),
    )
