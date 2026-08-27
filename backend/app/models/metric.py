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
    Float,
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
        UniqueConstraint("metric_code", name="uk_metric_config_code"),
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
    # --- Phase 1 新增指标（HiaMonitor 借鉴，2026-07-23）---
    # 仪表故障率复用既有 outlier_detection 结果，AGGREGATABLE（参与节点聚合）
    instrument_fault_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # PV/SP/OP/偏差 统计指标，DISPLAY_ONLY（不参与节点聚合，避免均值再平均失真）
    pv_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    pv_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    sp_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    sp_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    op_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    op_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    error_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    error_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    # 阀门诊断指标，DISPLAY_ONLY
    valve_linearity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    valve_nonlinearity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    valve_op_min: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    valve_op_max: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    oscillation_amplitude: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    # Numeric(10,0) 对齐全 Decimal 管道（_extract_kpi_values），非 Integer
    setpoint_crossing_count: Mapped[Decimal | None] = mapped_column(Numeric(10, 0), nullable=True)
    time_constant: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    # --- P2 IA优化：回路适用性分层（L0~L4），KPI聚合完成后同频写入 ---
    fitness_level: Mapped[str | None] = mapped_column(String(2), nullable=True)
    fitness_tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fitness_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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
        CheckConstraint(
            "fitness_level IS NULL OR fitness_level IN ('L0', 'L1', 'L2', 'L3', 'L4')",
            name="ck_kpi_snapshot_fitness_level",
        ),
        Index("idx_kpi_snapshot_loop_id", "loop_id"),
        Index("idx_kpi_snapshot_ts_start", "ts_start"),
        Index("idx_kpi_snapshot_status", "status"),
        Index("idx_kpi_snapshot_fitness_level", "fitness_level"),
        # 库中已有（x4c5d6e7f8a9 迁移创建），补入元数据避免 autogen 误 DROP
        Index("idx_kpi_snapshot_ts_loop", "ts_start", "loop_id"),
        # UNIQUE 约束：每个回路每小时仅允许一条快照（q1a2b3c4d5e6 迁移）
        UniqueConstraint("loop_id", "ts_start", name="uq_kpi_snapshot_hourly_loop_ts"),
        {"comment": "每小时性能评估快照（好值率基于 PV 质量码统计，含P2适用性分层字段）"},
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
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=True
    )
    # --- Phase 1 新增指标（与 kpi_snapshot_hourly 对齐，2026-07-23）---
    instrument_fault_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    pv_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    pv_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    sp_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    sp_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    op_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    op_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    error_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    error_std: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    valve_linearity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    valve_nonlinearity: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    valve_op_min: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    valve_op_max: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    oscillation_amplitude: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    setpoint_crossing_count: Mapped[Decimal | None] = mapped_column(Numeric(10, 0), nullable=True)
    time_constant: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    # --- P2 IA优化：回路适用性分层（L0~L4），与hourly同频字段 ---
    fitness_level: Mapped[str | None] = mapped_column(String(2), nullable=True)
    fitness_tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fitness_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')",
            name="ck_kpi_custom_status",
        ),
        CheckConstraint("ts_end > ts_start", name="ck_kpi_custom_window"),
        CheckConstraint(
            "fitness_level IS NULL OR fitness_level IN ('L0', 'L1', 'L2', 'L3', 'L4')",
            name="ck_kpi_custom_fitness_level",
        ),
        UniqueConstraint("task_id", "loop_id", name="uq_kpi_custom_task_loop"),
        Index("ix_kpi_snapshot_custom_task", "task_id"),
        Index("ix_kpi_snapshot_custom_loop_ts", "loop_id", "ts_start"),
        Index("ix_kpi_snapshot_custom_fitness_level", "fitness_level"),
        {"comment": "自定义评估任务快照（按需触发，不参与装置级聚合，含P2适用性分层字段）"},
    )


class LoopConfidenceLatest(Base):
    """回路最新一次可信度评估结果（每回路仅一条，随小时快照 UPSERT 覆盖更新）。

    与 ``kpi_snapshot_hourly``（历史序列）互补：本表只保留"最新一次评估"，
    供回路性能页可信度抽屉快速查询单回路当前可信度详情。

    ``metrics`` JSONB 存储 12 个 KPI 子指标（3+1+8 体系）的计算值与各自可信度，
    键为 DB 列名（snake_case），形如::

        {"accuracy_rate": {"value": 93.35, "confidence": "A"}, ...}
    """

    __tablename__ = "loop_confidence_latest"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 评估时间（快照写入时刻，naive UTC）
    eval_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 数据源时间区间
    data_ts_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    data_ts_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(1), nullable=True)
    valid_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    algorithm_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')",
            name="ck_loop_confidence_latest_status",
        ),
        CheckConstraint(
            "confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')",
            name="ck_loop_confidence_latest_confidence",
        ),
        Index("idx_loop_confidence_latest_loop_id", "loop_id", unique=True),
        {"comment": "回路最新一次可信度评估结果（每回路一条，随小时快照覆盖更新）"},
    )


class LoopIntegritySnapshot(Base):
    """回路数据完整性巡检快照（每回路每天一条，随每日巡检 UPSERT 覆盖）。

    由每日 02:00 定时巡检任务写入，供回路监控列表/测点配置页快速展示
    PV 完整度，无需实时查 TDengine（27 回路 × 7 列 COUNT 需 ~3s，列表页不可接受）。

    设计依据：data-quality-enhancement-plan-2026-08-05.md §2.2 方案 A
    """

    __tablename__ = "loop_integrity_snapshot"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 巡检日期（Asia/Shanghai 时区，naive date），用于 UPSERT 去重
    check_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 巡检时间窗口（naive UTC）
    ts_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ts_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # 整体完整度 0.0~1.0
    overall_completeness: Mapped[float | None] = mapped_column(Float, nullable=True)
    # PV 列完整度 0.0~1.0（核心指标，<0.95 触发告警）
    pv_completeness: Mapped[float | None] = mapped_column(Float, nullable=True)
    # OP 列完整度 0.0~1.0
    op_completeness: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 列级明细 JSONB: {"pv": {"completeness": 0.97, "count": 86400, "expected": 86400}, ...}
    col_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 缺失列列表 ["sp", "mode"]（空列表表示无缺失）
    missing_columns: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 巡检状态：OK / WARNING / CRITICAL / DATA_UNAVAILABLE
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.timezone("UTC", func.now()), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('OK', 'WARNING', 'CRITICAL', 'DATA_UNAVAILABLE')",
            name="ck_loop_integrity_status",
        ),
        # 每回路每天仅一条快照（UPSERT 去重）
        UniqueConstraint("loop_id", "check_date", name="uq_loop_integrity_loop_date"),
        Index("idx_loop_integrity_check_date", "check_date"),
        Index("idx_loop_integrity_loop_id", "loop_id"),
        {"comment": "回路数据完整性每日巡检快照（每回路每天一条）"},
    )
