"""``diagnosis_config``、``diagnosis_result`` 与 ``diagnosis_tag`` models."""

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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DiagnosisConfig(Base):
    """Diagnosis metric configuration (DDL §7)."""

    __tablename__ = "diagnosis_config"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
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

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
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


class DiagnosisTag(Base):
    """Diagnosis tag — loop-level fault tag (DDS §2.16).

    承载回路级的诊断标签记录，用于故障定位和告警，包括振荡、阀门粘滞、
    输出饱和、PV 质量异常等标签。与 ``DiagnosisResult`` 互补：
    ``DiagnosisResult`` 存储完整诊断证据链，``DiagnosisTag`` 存储可枚举、
    可查询、可状态流转的标签实例，支撑告警面板与标签筛选。

    设计依据：DDS §2.16, PRD §5.6, IDS §2.4.10-2.4.12
    """

    __tablename__ = "diagnosis_tag"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 标签代码（如 OSCILLATION / VALVE_STICTION / OUTPUT_SATURATION / QUALITY_ABNORMAL）
    tag_code: Mapped[str] = mapped_column(String(50), nullable=False)
    tag_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 严重等级：INFO / WARN / ERROR / CRITICAL
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    # 触发该标签的来源指标代码（如 oscillation_rate）
    source_metric: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 触发条件，如 {"threshold": 0.4, "window_minutes": 60}
    trigger_condition: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 触发阈值数值
    trigger_value: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 标签状态：ACTIVE（生效中）/ RESOLVED（已解除）/ SUPPRESSED（已抑制）
    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'ACTIVE'"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')",
            name="ck_diag_tag_severity",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED')",
            name="ck_diag_tag_status",
        ),
        Index("ix_diagnosis_tag_loop_status", "loop_id", "status"),
        Index("ix_diagnosis_tag_severity", "severity", "triggered_at"),
        {"comment": "诊断标签表：用于故障定位和告警（振荡/阀门粘滞/输出饱和/PV质量异常等）"},
    )
