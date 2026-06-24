"""``metric_config`` and ``kpi_snapshot_hourly`` models."""

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
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetricConfig(Base):
    """Performance metric configuration (DDL §6)."""

    __tablename__ = "metric_config"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    metric_code: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    threshold: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    control_type: Mapped[str | None] = mapped_column(String(20), default="STABLE", nullable=True)
    is_enabled: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, default=1, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "control_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC')",
            name="ck_metric_config_control_type",
        ),
        Index("uk_metric_config_code", "metric_code", unique=True),
    )


class KpiSnapshotHourly(Base):
    """Hourly performance evaluation snapshot (DDL §9)."""

    __tablename__ = "kpi_snapshot_hourly"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=True,
    )
    ts_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ts_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    good_value_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    auto_mode_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    steady_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    accuracy_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    oscillation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    saturation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fast_response_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    effective_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # 故障诊断扩展指标（nullable，向后兼容；诊断中心与性能评估共享表）
    stiction_coeff: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    steady_state_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    output_travel_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')",
            name="ck_kpi_snapshot_status",
        ),
        CheckConstraint("ts_end > ts_start", name="ck_kpi_snapshot_window"),
        Index("idx_kpi_snapshot_loop_id", "loop_id"),
        Index("idx_kpi_snapshot_ts_start", "ts_start"),
        Index("idx_kpi_snapshot_status", "status"),
        # 复合索引（S1-C2）：优化常见查询模式
        Index("idx_kpi_snapshot_loop_ts", "loop_id", "ts_start"),
        Index("idx_kpi_snapshot_ts_status_score", "ts_start", "status", "score"),
    )
