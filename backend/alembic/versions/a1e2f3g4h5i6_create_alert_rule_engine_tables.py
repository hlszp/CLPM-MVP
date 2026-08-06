"""create alert rule engine tables

Revision ID: a1e2f3g4h5i6
Revises: p302a1b2c3d4
Create Date: 2026-08-07

智能预警规则引擎 Phase 1（§5 数据模型）：
- alert_rule              规则定义
- alert_rule_subscription 回路-规则订阅
- alert_event             预警事件
- alert_rule_audit_log    规则变更审计
- alert_suppression       手动抑制记录

ORM 表数 38 → 43（实现契约 v2.6 §10）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1e2f3g4h5i6"
down_revision: str | None = "p302a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. alert_rule（规则定义）
    op.create_table(
        "alert_rule",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("rule_code", sa.String(50), nullable=False, unique=True),
        sa.Column("rule_name", sa.String(100), nullable=False),
        sa.Column("rule_type", sa.String(20), nullable=False),
        sa.Column("dsl", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "rule_type IN ('THRESHOLD', 'DRIFT', 'COMPOSITE', 'CONFIDENCE')",
            name="ck_alert_rule_type",
        ),
        comment="预警规则定义",
    )
    op.create_index("idx_alert_rule_type", "alert_rule", ["rule_type"])
    op.create_index("idx_alert_rule_enabled_priority", "alert_rule", ["is_enabled", "priority"])

    # 2. alert_rule_subscription（回路-规则订阅）
    op.create_table(
        "alert_rule_subscription",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("alert_rule.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_value", sa.String(100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "scope_type IN ('ALL', 'LOOP', 'PLANT', 'CONTROL_TYPE')",
            name="ck_alert_subscription_scope",
        ),
        comment="回路-规则订阅关系",
    )
    op.create_index(
        "uk_alert_subscription_rule_loop",
        "alert_rule_subscription",
        ["rule_id", "loop_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index("idx_alert_subscription_loop", "alert_rule_subscription", ["loop_id"])
    op.create_index(
        "idx_alert_subscription_scope",
        "alert_rule_subscription",
        ["scope_type", "scope_value"],
    )

    # 3. alert_event（预警事件）
    op.create_table(
        "alert_event",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("alert_rule.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rule_code", sa.String(50), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("trigger_condition_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("data_window", postgresql.JSONB(), nullable=True),
        sa.Column("triggered_value", sa.Numeric(10, 4), nullable=True),
        sa.Column("confidence_level", sa.String(1), nullable=True),
        sa.Column("rule_dsl_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "tracker_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("action_tracker.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_false_positive", sa.Boolean(), nullable=True),
        sa.Column("trigger_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("triggered_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("acknowledged_by", sa.String(50), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(50), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')",
            name="ck_alert_event_severity",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ACKNOWLEDGED', 'RESOLVED', 'SUPPRESSED', 'ARCHIVED')",
            name="ck_alert_event_status",
        ),
        sa.CheckConstraint(
            "confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')",
            name="ck_alert_event_confidence",
        ),
        comment="预警事件",
    )
    op.create_index(
        "idx_alert_event_loop_time",
        "alert_event",
        ["loop_id", sa.text("triggered_at DESC")],
    )
    op.create_index("idx_alert_event_severity_status", "alert_event", ["severity", "status"])
    op.create_index(
        "idx_alert_event_rule", "alert_event", ["rule_id", sa.text("triggered_at DESC")]
    )
    op.create_index("idx_alert_event_status", "alert_event", ["status"])
    op.create_index("idx_alert_event_tracker", "alert_event", ["tracker_id"])

    # 4. alert_rule_audit_log（规则变更审计）
    op.create_table(
        "alert_rule_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("alert_rule.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rule_code", sa.String(50), nullable=False),
        sa.Column("operation_type", sa.String(20), nullable=False),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("after_value", sa.Text(), nullable=True),
        sa.Column("operator", sa.String(50), nullable=False),
        sa.Column("operated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "operation_type IN ('CREATE', 'UPDATE', 'ENABLE', 'DISABLE', 'DELETE')",
            name="ck_alert_audit_operation",
        ),
        comment="规则变更审计日志",
    )
    op.create_index(
        "idx_alert_audit_rule",
        "alert_rule_audit_log",
        ["rule_id", sa.text("operated_at DESC")],
    )
    op.create_index(
        "idx_alert_audit_operator",
        "alert_rule_audit_log",
        ["operator", sa.text("operated_at DESC")],
    )
    op.create_index(
        "idx_alert_audit_type",
        "alert_rule_audit_log",
        ["operation_type", sa.text("operated_at DESC")],
    )

    # 5. alert_suppression（手动抑制记录）
    op.create_table(
        "alert_suppression",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("alert_rule.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("suppressed_by", sa.String(50), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        comment="手动抑制记录",
    )
    op.create_index("idx_alert_suppression_loop", "alert_suppression", ["loop_id"])
    op.create_index("idx_alert_suppression_expiry", "alert_suppression", ["end_at", "is_active"])
    op.create_index("idx_alert_suppression_rule", "alert_suppression", ["rule_id", "is_active"])


def downgrade() -> None:
    op.drop_table("alert_suppression")
    op.drop_table("alert_rule_audit_log")
    op.drop_table("alert_event")
    op.drop_table("alert_rule_subscription")
    op.drop_table("alert_rule")
