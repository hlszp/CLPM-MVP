"""``kpi_node_snapshot_hourly`` model — 节点级每小时性能评估快照。

对齐 GB/T 44693.2-2024 §6.4 综合评估：按 plant_node 递归收集下属回路，
以 score_weight 加权聚合回路级快照，支持企业级/装置级/单元级 KPI。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KpiNodeSnapshotHourly(Base):
    """节点级每小时性能评估快照（DDL §9.1）。"""

    __tablename__ = "kpi_node_snapshot_hourly"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    plant_node_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("plant_node.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ts_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    good_value_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    auto_mode_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    effective_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    steady_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    accuracy_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fast_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    oscillation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    saturation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Phase 1 新增：仪表故障率（AGGREGATABLE，参与节点级加权聚合）
    instrument_fault_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # P1 #14: 补全 4 个字段（与回路级 KpiSnapshotHourly 对齐）
    stiction_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    output_trip_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    ideal_settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    auto_loop_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    realtime_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    loop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    algorithm_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')",
            name="ck_kpi_node_snapshot_status",
        ),
        CheckConstraint("ts_end > ts_start", name="ck_kpi_node_snapshot_window"),
        Index("idx_kpi_node_snapshot_node_id", "plant_node_id"),
        Index("idx_kpi_node_snapshot_ts_start", "ts_start"),
        Index("idx_kpi_node_snapshot_status", "status"),
        Index("idx_kpi_node_snapshot_node_ts", "plant_node_id", "ts_start"),
        Index("idx_kpi_node_snapshot_ts_status", "ts_start", "status", "score"),
    )


class KpiNodeSnapshotDaily(Base):
    """节点级日性能评估快照（DDL §9.2）。

    按 loop_count 加权聚合当天 24 条小时快照；
    realtime_auto_rate 取当天最后一次小时快照的值（非聚合）。
    """

    __tablename__ = "kpi_node_snapshot_daily"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    plant_node_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("plant_node.id", ondelete="CASCADE"),
        nullable=False,
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    good_value_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    auto_mode_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    effective_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    steady_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    accuracy_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fast_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    oscillation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    saturation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Phase 1 新增：仪表故障率（AGGREGATABLE）
    instrument_fault_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # P1 #14: 补全 4 个字段（与回路级 KpiSnapshotHourly 对齐）
    stiction_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    output_trip_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    ideal_settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    auto_loop_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    realtime_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    loop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    algorithm_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')",
            name="ck_kpi_node_snapshot_daily_status",
        ),
        UniqueConstraint(
            "plant_node_id",
            "stat_date",
            name="uk_kpi_node_snapshot_daily_node_date",
        ),
        Index("idx_kpi_node_snapshot_daily_node_id", "plant_node_id"),
        Index("idx_kpi_node_snapshot_daily_stat_date", "stat_date"),
        Index("idx_kpi_node_snapshot_daily_status", "status"),
        Index("idx_kpi_node_snapshot_daily_node_date", "plant_node_id", "stat_date"),
        # Workbench v2.0: PLANTS 排名 / UNITS 热力高频查询加速 (方案 M-F)
        Index(
            "idx_kpi_daily_scope_date_desc",
            "plant_node_id",
            text("stat_date DESC"),
        ),
    )


class KpiNodeSnapshotMonthly(Base):
    """节点级月性能评估快照（DDL §9.3）。

    按 loop_count 加权聚合当月所有日快照；
    realtime_auto_rate 取当月最后一次小时快照的值（非聚合）。
    """

    __tablename__ = "kpi_node_snapshot_monthly"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    plant_node_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("plant_node.id", ondelete="CASCADE"),
        nullable=False,
    )
    stat_month: Mapped[date] = mapped_column(Date, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    good_value_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    auto_mode_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    effective_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    steady_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    accuracy_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fast_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    oscillation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    saturation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Phase 1 新增：仪表故障率（AGGREGATABLE）
    instrument_fault_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # P1 #14: 补全 4 个字段（与回路级 KpiSnapshotHourly 对齐）
    stiction_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    output_trip_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    ideal_settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    auto_loop_ratio: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    realtime_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    loop_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    algorithm_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')",
            name="ck_kpi_node_snapshot_monthly_status",
        ),
        UniqueConstraint(
            "plant_node_id",
            "stat_month",
            name="uk_kpi_node_snapshot_monthly_node_month",
        ),
        Index("idx_kpi_node_snapshot_monthly_node_id", "plant_node_id"),
        Index("idx_kpi_node_snapshot_monthly_stat_month", "stat_month"),
        Index("idx_kpi_node_snapshot_monthly_status", "status"),
        Index("idx_kpi_node_snapshot_monthly_node_month", "plant_node_id", "stat_month"),
    )


__all__ = [
    "KpiNodeSnapshotDaily",
    "KpiNodeSnapshotHourly",
    "KpiNodeSnapshotMonthly",
]
