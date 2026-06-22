"""``loop_ledger`` and ``loop_tag_mapping`` models."""

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
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LoopLedger(Base):
    """Loop ledger — core entity of the system (DDL §3)."""

    __tablename__ = "loop_ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plant_node.id", ondelete="RESTRICT"), nullable=True
    )
    score_weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    last_aas_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PARTIAL")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # S2-LOOP-004 新增字段
    score_weights: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('READY', 'PARTIAL', 'INACTIVE')",
            name="ck_loop_ledger_status",
        ),
        Index("uk_loop_ledger_tag_name", "tag_name", unique=True),
        Index("idx_loop_ledger_unit_id", "unit_id"),
        Index("idx_loop_ledger_status", "status"),
        Index("idx_loop_ledger_tag_name", "tag_name"),
    )


class LoopTagMapping(Base):
    """Loop ↔ Tag association — 7 OPC tag roles per loop (DDL §5)."""

    __tablename__ = "loop_tag_mapping"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    loop_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    tag_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tag_registry.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tag_role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "tag_role IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D')",
            name="ck_loop_tag_mapping_role",
        ),
        Index("uk_loop_tag_mapping_loop_role", "loop_id", "tag_role", unique=True),
        Index("idx_loop_tag_mapping_loop_id", "loop_id"),
        Index("idx_loop_tag_mapping_tag_id", "tag_id"),
    )
