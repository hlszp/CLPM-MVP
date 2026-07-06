"""``metric_config``、``kpi_snapshot_hourly`` 与 ``kpi_snapshot_custom`` models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
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
    # DEPRECATED: 对齐 FDS v5.1 §5.3.1.2，12 项指标算法已固化为独立函数模块，不再支持自定义公式覆盖
    formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    threshold: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # MIGRATED: 已迁移至 loop_ledger.control_type，本字段仅保留兼容历史数据
    control_type: Mapped[str | None] = mapped_column(String(20), default="STABLE", nullable=True)
    # v5.3 新增：5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR），JSONB 存储
    grading_thresholds: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
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
    fast_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    effective_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # 故障诊断扩展指标（nullable，向后兼容；诊断中心与性能评估共享表）
    stiction_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    output_trip_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # v4.0 扩展字段（DDS §2.8）：理想稳态时间 + 算法版本 + 5 个数据血缘字段
    ideal_settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sampling_freq: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quality_policy: Mapped[str | None] = mapped_column(String(30), nullable=True)
    valid_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(CHAR(1), nullable=True)
    data_lineage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')",
            name="ck_kpi_snapshot_status",
        ),
        CheckConstraint("ts_end > ts_start", name="ck_kpi_snapshot_window"),
        CheckConstraint(
            "confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')",
            name="ck_kpi_snapshot_confidence",
        ),
        Index("idx_kpi_snapshot_loop_id", "loop_id"),
        Index("idx_kpi_snapshot_ts_start", "ts_start"),
        Index("idx_kpi_snapshot_status", "status"),
        # UNIQUE 约束：每个回路每小时仅允许一条快照（q1a2b3c4d5e6 迁移）
        UniqueConstraint("loop_id", "ts_start", name="uq_kpi_snapshot_hourly_loop_ts"),
        {"comment": "每小时性能评估快照（好值率基于 PV 质量码统计）"},
    )


class KpiSnapshotCustom(Base):
    """Custom evaluation task snapshot (DDS §2.14).

    自定义评估任务快照，由用户按需触发（指定时间窗/回路集合），
    通过 ``task_id`` 区分独立任务，**不参与装置级聚合**
    （装置级汇总仅基于 ``kpi_snapshot_hourly``）。

    设计依据：DDS §2.14, PRD §4.3.7B, FDS §5.3.11
    """

    __tablename__ = "kpi_snapshot_custom"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    task_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ts_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    accuracy_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fast_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    steady_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    effective_auto_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    good_value_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    oscillation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    saturation_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    stiction_index: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    output_trip_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    ideal_settling_time: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    auto_mode_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # P2 #29 B6: 补齐数据血缘字段（与 kpi_snapshot_hourly 对齐）
    sampling_freq: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quality_policy: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence_level: Mapped[str | None] = mapped_column(CHAR(1), nullable=True)
    valid_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    data_lineage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')",
            name="ck_kpi_custom_status",
        ),
        CheckConstraint("ts_end > ts_start", name="ck_kpi_custom_window"),
        UniqueConstraint("task_id", "loop_id", name="uq_kpi_custom_task_loop"),
        Index("ix_kpi_snapshot_custom_task", "task_id"),
        Index("ix_kpi_snapshot_custom_loop_ts", "loop_id", "ts_start"),
        {"comment": "自定义评估任务快照（按需触发，不参与装置级聚合）"},
    )
