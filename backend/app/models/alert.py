"""Alert rule engine ORM models (智能预警规则引擎 §5).

新增 5 张表：
- alert_rule              规则定义
- alert_rule_subscription 回路-规则订阅
- alert_event             预警事件
- alert_rule_audit_log    规则变更审计
- alert_suppression       手动抑制记录

对齐 tracker.py 的 SQLAlchemy 2.0 Mapped 风格。
"""

from __future__ import annotations

from datetime import datetime
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AlertRule(Base):
    """预警规则定义（§5.1）。

    存储规则代码、类型、DSL、优先级、启停状态与版本号。
    DSL 完整结构见方案 §3.2（ruleType/scope/condition/durationSeconds/
    cooldownSeconds/severity/confidencePolicy/timeWindow/actions/priority/dedupKey）。
    """

    __tablename__ = "alert_rule"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    rule_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    dsl: Mapped[dict] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "rule_type IN ('THRESHOLD', 'DRIFT', 'COMPOSITE', 'CONFIDENCE', 'METRIC_THRESHOLD')",
            name="ck_alert_rule_type",
        ),
        Index("idx_alert_rule_type", "rule_type"),
        Index("idx_alert_rule_enabled_priority", "is_enabled", "priority"),
    )


class AlertRuleSubscription(Base):
    """回路-规则订阅关系（§5.2）。

    scope_type=PLANT/CONTROL_TYPE 时由后台批量展开为多行订阅记录（物化展开）。
    scope_type=ALL 时不展开，求值时按 loop_ledger.is_active=true 全量扫描。
    """

    __tablename__ = "alert_rule_subscription"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    rule_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alert_rule.id", ondelete="CASCADE"),
        nullable=False,
    )
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('ALL', 'LOOP', 'PLANT', 'CONTROL_TYPE')",
            name="ck_alert_subscription_scope",
        ),
        # 同一规则同一回路仅保留一条活跃订阅
        Index(
            "uk_alert_subscription_rule_loop",
            "rule_id",
            "loop_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index("idx_alert_subscription_loop", "loop_id"),
        Index("idx_alert_subscription_scope", "scope_type", "scope_value"),
    )


class AlertEvent(Base):
    """预警事件（§5.3）。

    状态机：ACTIVE → ACKNOWLEDGED → RESOLVED → ARCHIVED；
    分支：ACTIVE/ACKNOWLEDGED → SUPPRESSED（到期回原状态）。
    非状态变更动作：标记误报 / 转工单 / 转诊断任务。
    """

    __tablename__ = "alert_event"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    rule_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alert_rule.id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_code: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    loop_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'ACTIVE'"))
    trigger_condition_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    data_window: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    triggered_value: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(1), nullable=True)
    rule_dsl_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tracker_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("action_tracker.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_false_positive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("now()")
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')",
            name="ck_alert_event_severity",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED', 'SUPPRESSED', 'ARCHIVED')",
            name="ck_alert_event_status",
        ),
        CheckConstraint(
            "confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')",
            name="ck_alert_event_confidence",
        ),
        Index("idx_alert_event_loop_time", "loop_id", text("triggered_at DESC")),
        Index("idx_alert_event_severity_status", "severity", "status"),
        Index("idx_alert_event_rule", "rule_id", text("triggered_at DESC")),
        Index("idx_alert_event_status", "status"),
        Index("idx_alert_event_tracker", "tracker_id"),
    )


class AlertRuleAuditLog(Base):
    """规则变更审计日志（§5.4）。

    所有规则 CRUD（CREATE/UPDATE/ENABLE/DISABLE/DELETE）写入此表，
    含 before/after JSON 快照。不写 sys_audit_log（避免污染系统级审计）。
    """

    __tablename__ = "alert_rule_audit_log"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    rule_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alert_rule.id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_code: Mapped[str] = mapped_column(String(50), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator: Mapped[str] = mapped_column(String(50), nullable=False)
    operated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('CREATE', 'UPDATE', 'ENABLE', 'DISABLE', 'DELETE')",
            name="ck_alert_audit_operation",
        ),
        Index("idx_alert_audit_rule", "rule_id", text("operated_at DESC")),
        Index("idx_alert_audit_operator", "operator", text("operated_at DESC")),
        Index("idx_alert_audit_type", "operation_type", text("operated_at DESC")),
    )


class AlertSuppression(Base):
    """手动抑制记录（§5.5）。

    对指定回路 × 规则在指定时段内抑制告警。到期自动失效（is_active=false）。
    rule_id 或 loop_id 为 NULL 表示全规则/全回路抑制。
    """

    __tablename__ = "alert_suppression"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    rule_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("alert_rule.id", ondelete="CASCADE"),
        nullable=True,
    )
    loop_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("loop_ledger.id", ondelete="CASCADE"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    suppressed_by: Mapped[str] = mapped_column(String(50), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("idx_alert_suppression_loop", "loop_id"),
        Index("idx_alert_suppression_expiry", "end_at", "is_active"),
        Index("idx_alert_suppression_rule", "rule_id", "is_active"),
    )
