"""``tuning_batch`` + ``tuning_batch_records`` — 整定批次 + 前置工单依赖。

原型 W11 BATCHES：批次包含多条 TuningRecord；`prereq_order_ids` 中任一前置
handling_order 未 CLOSED/CANCELLED → batch.status = BLOCKED，UI 按钮禁用。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

BATCH_STATUSES = (
    "BLOCKED",
    "PENDING",
    "READY",
    "RUNNING",
    "COMPLETED",
    "CANCELLED",
)
SCOPE_TYPES = ("FACTORY", "AREA", "UNIT", "LOOP")


class TuningBatch(Base):
    """A tuning batch grouping multiple TuningRecords with pre-req order guards."""

    __tablename__ = "tuning_batch"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")

    # handling_order.id 列表：所有前置工单必须 ∈ {CLOSED, CANCELLED} 才 READY
    prereq_order_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    block_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # scatters_before/after: [{loop_id, score, steady_rate, accuracy_rate, ...}]
    scatters_before: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    scatters_after: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)

    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    records = relationship(
        "TuningRecord",
        secondary="tuning_batch_records",
        order_by="TuningBatchRecords.sort_order",
        lazy="select",
    )

    __table_args__ = (
        UniqueConstraint("batch_no", name="uniq_tuning_batch_no"),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in BATCH_STATUSES)})",
            name="ck_tuning_batch_status",
        ),
        CheckConstraint(
            f"scope_type IN ({', '.join(repr(s) for s in SCOPE_TYPES)})",
            name="ck_tuning_batch_scope_type",
        ),
        Index("idx_tuning_batch_scope", "scope_type", "scope_id"),
        Index("idx_tuning_batch_status", "status", "created_at"),
    )


class TuningBatchRecords(Base):
    """N:M association (batch_id, tuning_record_id) + sort order in batch."""

    __tablename__ = "tuning_batch_records"

    batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tuning_batch.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tuning_record_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("tuning_record.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("idx_tbr_record_batch", "tuning_record_id", "batch_id"),)
