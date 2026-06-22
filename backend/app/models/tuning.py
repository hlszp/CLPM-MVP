"""``tuning_record`` model — loop tuning task records (Phase 2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TuningRecord(Base):
    """Loop tuning record (DDL §12, Phase 2)."""

    __tablename__ = "tuning_record"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_type: Mapped[str] = mapped_column(String(20), nullable=False)
    model_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    algorithm: Mapped[str] = mapped_column(String(50), nullable=False)
    recommended_pid: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    simulation_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fitting_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "model_type IN ('FOPDT', 'SOPDT', 'IPDT')",
            name="ck_tuning_record_model",
        ),
        CheckConstraint(
            "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON')",
            name="ck_tuning_record_algo",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'IDENTIFIED', 'SIMULATED', 'APPLIED', 'VERIFIED')",
            name="ck_tuning_record_status",
        ),
    )
