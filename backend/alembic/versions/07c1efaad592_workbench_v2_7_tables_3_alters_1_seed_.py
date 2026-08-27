"""workbench v2.0 M1-W01 schema changes (7 tables + column alters).

Coverage:
- 8 new tables: module_plugin · workbench_window_summary · event_bus · sla_policy ·
  tuning_batch · tuning_batch_records · trend_flags · wb_cache_log
- Alters:
  * diagnosis_result  + recommended_category + evidence_summary
  * diagnosis_tag     + disposition_state + sla_deadline_at + sla_stage + 2 idx + 2 CK
  * handling_order    + sla_policy_id + sla_deadline_at + sla_stage
                      + reopen_count + reopen_reasons
                      + scope_type + scope_id + handler_id
                      + 3 idx + 2 FK + 2 CK
  * kpi_node_snapshot_daily + idx_kpi_daily_scope_date_desc
  * sys_user          + lane_capacity
- Seed data (ON CONFLICT DO NOTHING):
  * sla_policy: 8 action_types × 4 priorities = 32 条默认模板
  * module_plugin: 8 条 CLPM-MVP 基础模块注册表

Revision ID: 07c1efaad592
Revises: z2b3c4d5e6f7
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "07c1efaad592"
down_revision: str | Sequence[str] | None = "z2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------
def upgrade() -> None:
    conn = op.get_bind()
    is_pg = conn.dialect.name == "postgresql"
    json_type = pg.JSONB if is_pg else sa.JSON
    uuid_ = pg.UUID(as_uuid=False) if is_pg else sa.String(36)

    # ======================================================================
    # 1. sla_policy —— 创建在 handling_order 加 FK 之前
    # ======================================================================
    op.create_table(
        "sla_policy",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("warn_minutes", sa.Integer(), nullable=False),
        sa.Column("breach_minutes", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("scope_type", sa.String(length=16), nullable=True),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "action_type IN ('TUNING','VALVE','INSTRUMENT','LINK','PROCESS',"
            "'UTILIZATION','RECONFIG','OTHER')",
            name="ck_sla_action_type",
        ),
        sa.CheckConstraint(
            "priority IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_sla_priority",
        ),
        sa.CheckConstraint("warn_minutes > 0", name="ck_sla_warn_pos"),
        sa.CheckConstraint("breach_minutes > warn_minutes", name="ck_sla_breach_gt_warn"),
        sa.UniqueConstraint(
            "action_type",
            "priority",
            "scope_type",
            "scope_id",
            name="uniq_sla_policy_scope",
        ),
    )
    op.create_index("idx_sla_policy_default", "sla_policy", ["action_type", "is_default"])

    # ======================================================================
    # 2. module_plugin —— 模块 4 态注册表
    # ======================================================================
    op.create_table(
        "module_plugin",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("module_key", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=True),
        sa.Column("is_core", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column(
            "dependencies",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_pg else None,
        ),
        sa.Column("maintenance_window", json_type, nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_maintenance_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("module_key", name="uniq_module_plugin_key"),
        sa.CheckConstraint(
            "status IN ('CORE','ENABLED','MAINTENANCE','UNINSTALLED')",
            name="ck_module_plugin_status",
        ),
    )
    op.create_index("idx_module_plugin_order", "module_plugin", ["order_index"])
    op.create_index("idx_module_plugin_status", "module_plugin", ["status"])

    # ======================================================================
    # 3. tuning_batch + tuning_batch_records
    # ======================================================================
    op.create_table(
        "tuning_batch",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_no", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "prereq_order_ids",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_pg else None,
        ),
        sa.Column("block_reason", sa.String(length=500), nullable=True),
        sa.Column("scatters_before", json_type, nullable=True),
        sa.Column("scatters_after", json_type, nullable=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
        sa.Column("expected_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_no", name="uniq_tuning_batch_no"),
        sa.CheckConstraint(
            "status IN ('BLOCKED','PENDING','READY','RUNNING','COMPLETED','CANCELLED')",
            name="ck_tuning_batch_status",
        ),
        sa.CheckConstraint(
            "scope_type IN ('FACTORY','AREA','UNIT','LOOP')",
            name="ck_tuning_batch_scope_type",
        ),
    )
    op.create_index("idx_tuning_batch_scope", "tuning_batch", ["scope_type", "scope_id"])
    op.create_index("idx_tuning_batch_status", "tuning_batch", ["status", "created_at"])

    op.create_table(
        "tuning_batch_records",
        sa.Column(
            "batch_id",
            sa.BigInteger(),
            sa.ForeignKey("tuning_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tuning_record_id",
            uuid_,
            sa.ForeignKey("tuning_record.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("batch_id", "tuning_record_id"),
    )
    op.create_index(
        "idx_tbr_record_batch",
        "tuning_batch_records",
        ["tuning_record_id", "batch_id"],
    )

    # ======================================================================
    # 4. workbench_window_summary
    #    注意：window 是 PG 保留字。ORM create_table 不会对列名加双引号，
    #    会导致 syntax error，因此 PG 分支改用原生 DDL；非 PG 分支仍走 ORM。
    # ======================================================================
    if is_pg:
        op.execute(
            """
            CREATE TABLE workbench_window_summary (
                id BIGSERIAL PRIMARY KEY,
                scope_type VARCHAR(16) NOT NULL,
                scope_id INTEGER NOT NULL,
                "window" VARCHAR(8) NOT NULL,
                window_start TIMESTAMPTZ NOT NULL,
                window_end TIMESTAMPTZ NOT NULL,
                score NUMERIC(6,3) NOT NULL,
                status VARCHAR(16) NOT NULL,
                loop_count INTEGER NOT NULL DEFAULT 0,
                good_value_rate NUMERIC(6,3),
                auto_mode_rate NUMERIC(6,3),
                effective_auto_rate NUMERIC(6,3),
                steady_rate NUMERIC(6,3),
                accuracy_rate NUMERIC(6,3),
                fast_rate NUMERIC(6,3),
                oscillation_rate NUMERIC(6,3),
                saturation_rate NUMERIC(6,3),
                instrument_fault_rate NUMERIC(6,3),
                score_trend JSONB NOT NULL DEFAULT '[]'::jsonb,
                flags JSONB NOT NULL DEFAULT '[]'::jsonb,
                snapshot_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uniq_ws_scope_window_end UNIQUE (
                    scope_type, scope_id, "window", window_end
                ),
                CONSTRAINT ck_ws_window CHECK ("window" IN ('24h','7d','30d')),
                CONSTRAINT ck_ws_scope_type CHECK (
                    scope_type IN ('GLOBAL','FACTORY','AREA','UNIT','LOOP')
                ),
                CONSTRAINT ck_ws_status CHECK (
                    status IN ('EXCELLENT','GOOD','FAIR','POOR','CRITICAL','INCONCLUSIVE')
                ),
                CONSTRAINT ck_ws_score_range CHECK (score >= 0 AND score <= 100)
            )
            """
        )
        # PG: CREATE INDEX 列名 window 也要双引号
        op.execute(
            "CREATE INDEX idx_ws_scope_window ON workbench_window_summary "
            '(scope_type, scope_id, "window")'
        )
    else:
        op.create_table(
            "workbench_window_summary",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("scope_type", sa.String(length=16), nullable=False),
            sa.Column("scope_id", sa.Integer(), nullable=False),
            sa.Column("window", sa.String(length=8), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("score", sa.Numeric(6, 3), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column(
                "loop_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("good_value_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("auto_mode_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("effective_auto_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("steady_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("accuracy_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("fast_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("oscillation_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("saturation_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("instrument_fault_rate", sa.Numeric(6, 3), nullable=True),
            sa.Column("score_trend", json_type, nullable=False),
            sa.Column("flags", json_type, nullable=False),
            sa.Column(
                "snapshot_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scope_type",
                "scope_id",
                "window",
                "window_end",
                name="uniq_ws_scope_window_end",
            ),
            sa.CheckConstraint("window IN ('24h','7d','30d')", name="ck_ws_window"),
            sa.CheckConstraint(
                "scope_type IN ('GLOBAL','FACTORY','AREA','UNIT','LOOP')",
                name="ck_ws_scope_type",
            ),
            sa.CheckConstraint(
                "status IN ('EXCELLENT','GOOD','FAIR','POOR','CRITICAL','INCONCLUSIVE')",
                name="ck_ws_status",
            ),
            sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_ws_score_range"),
        )
        op.create_index(
            "idx_ws_scope_window",
            "workbench_window_summary",
            ["scope_type", "scope_id", "window"],
        )
    # 通用索引 snapshot_at（两分支皆可用）
    op.create_index("idx_ws_snapshot", "workbench_window_summary", ["snapshot_at"])

    # ======================================================================
    # 5. trend_flags —— 差分趋势异常标志（PG：column+CK "window" 全走 DDL）
    # ======================================================================
    if is_pg:
        op.execute(
            """
            CREATE TABLE trend_flags (
                id BIGSERIAL PRIMARY KEY,
                scope_type VARCHAR(16) NOT NULL,
                scope_id INTEGER NOT NULL,
                loop_id UUID,
                "window" VARCHAR(8) NOT NULL,
                kind VARCHAR(20) NOT NULL,
                severity VARCHAR(8) NOT NULL,
                flagged_at TIMESTAMPTZ NOT NULL,
                metric_name VARCHAR(32),
                prev_value NUMERIC(8,3),
                curr_value NUMERIC(8,3),
                delta_pct NUMERIC(7,2),
                description VARCHAR(500),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT tf_loop_fk FOREIGN KEY (loop_id)
                    REFERENCES loop_ledger(id) ON DELETE SET NULL,
                CONSTRAINT ck_tf_kind CHECK (
                    kind IN (
                        'dip','spike','deterioration','jump',
                        'oscillation_start','saturation_event'
                    )
                ),
                CONSTRAINT ck_tf_severity CHECK (
                    severity IN ('INFO','WARN','ERROR','CRITICAL')
                ),
                CONSTRAINT ck_tf_window CHECK ("window" IN ('24h','7d','30d')),
                CONSTRAINT ck_tf_scope_type CHECK (
                    scope_type IN ('GLOBAL','FACTORY','AREA','UNIT','LOOP')
                )
            )
            """
        )
        op.execute(
            "CREATE INDEX idx_tf_scope_window_flagged_desc ON trend_flags "
            '(scope_type, scope_id, "window", flagged_at DESC)'
        )
    else:
        op.create_table(
            "trend_flags",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("scope_type", sa.String(length=16), nullable=False),
            sa.Column("scope_id", sa.Integer(), nullable=False),
            sa.Column(
                "loop_id",
                uuid_,
                sa.ForeignKey("loop_ledger.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("window", sa.String(length=8), nullable=False),
            sa.Column("kind", sa.String(length=20), nullable=False),
            sa.Column("severity", sa.String(length=8), nullable=False),
            sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("metric_name", sa.String(length=32), nullable=True),
            sa.Column("prev_value", sa.Numeric(8, 3), nullable=True),
            sa.Column("curr_value", sa.Numeric(8, 3), nullable=True),
            sa.Column("delta_pct", sa.Numeric(7, 2), nullable=True),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "kind IN ('dip','spike','deterioration','jump','oscillation_start',"
                "'saturation_event')",
                name="ck_tf_kind",
            ),
            sa.CheckConstraint(
                "severity IN ('INFO','WARN','ERROR','CRITICAL')",
                name="ck_tf_severity",
            ),
            sa.CheckConstraint("window IN ('24h','7d','30d')", name="ck_tf_window"),
            sa.CheckConstraint(
                "scope_type IN ('GLOBAL','FACTORY','AREA','UNIT','LOOP')",
                name="ck_tf_scope_type",
            ),
        )
        op.create_index(
            "idx_tf_scope_window_flagged_desc",
            "trend_flags",
            ["scope_type", "scope_id", "window", sa.text("flagged_at DESC")],
        )
    op.create_index("idx_tf_loop", "trend_flags", ["loop_id", "flagged_at"])
    op.create_index("idx_tf_kind_severity", "trend_flags", ["kind", "severity"])

    # ======================================================================
    # 6. wb_cache_log —— WBFF 缓存命中日志
    # ======================================================================
    op.create_table(
        "wb_cache_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cache_key", sa.String(length=200), nullable=False),
        sa.Column("hit", sa.Boolean(), nullable=False),
        sa.Column("build_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_wbcl_created_desc", "wb_cache_log", [sa.text("created_at DESC")])
    op.create_index("idx_wbcl_key", "wb_cache_log", ["cache_key", "created_at"])
    op.create_index("idx_wbcl_endpoint_hit", "wb_cache_log", ["endpoint", "hit"])

    # ======================================================================
    # 7. event_bus —— 跨模块事件归一总线
    #    注：metadata 列存在 Declarative 保留冲突，此处仅 DB 列名。
    # ======================================================================
    op.create_table(
        "event_bus",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_module", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=True),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column(
            "loop_id",
            uuid_,
            sa.ForeignKey("loop_ledger.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "order_id",
            uuid_,
            sa.ForeignKey("handling_order.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "record_id",
            uuid_,
            sa.ForeignKey("tuning_record.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "tag_id",
            uuid_,
            sa.ForeignKey("diagnosis_tag.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "alert_event_id",
            uuid_,
            sa.ForeignKey("alert_event.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            json_type,
            nullable=False,
            server_default=sa.text("'{}'::jsonb") if is_pg else None,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "read_by_users",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_pg else None,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "source_module IN ('monitor','assess','diagnosis','tuning','handling',"
            "'alert','system')",
            name="ck_eb_source_module",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO','WARN','ERROR','CRITICAL')",
            name="ck_eb_severity",
        ),
    )
    op.create_index("idx_eb_scope", "event_bus", ["scope_type", "scope_id", "occurred_at"])
    op.create_index("idx_eb_occurred_desc", "event_bus", [sa.text("occurred_at DESC")])
    op.create_index(
        "idx_eb_read_users",
        "event_bus",
        ["read_by_users"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_eb_unread_count",
        "event_bus",
        ["id"],
        postgresql_where=sa.text("jsonb_array_length(read_by_users) = 0"),
    )
    op.create_index("idx_eb_source_type", "event_bus", ["source_module", "event_type"])

    # ======================================================================
    # 8. diagnosis_result alters (M-A) +2 col
    # ======================================================================
    op.add_column(
        "diagnosis_result",
        sa.Column("recommended_category", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "diagnosis_result",
        sa.Column("evidence_summary", sa.Text(), nullable=True),
    )

    # ======================================================================
    # 9. diagnosis_tag alters (M-B) +3 col + 2 CK + 2 Index
    # ======================================================================
    op.add_column(
        "diagnosis_tag",
        sa.Column(
            "disposition_state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNADDRESSED'"),
        ),
    )
    op.add_column(
        "diagnosis_tag",
        sa.Column("sla_deadline_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "diagnosis_tag",
        sa.Column(
            "sla_stage",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
    )
    op.create_check_constraint(
        "ck_diag_tag_disposition",
        "diagnosis_tag",
        "disposition_state IN ('UNADDRESSED','CONVERTED','ACK_REVIEWED','IGNORED')",
    )
    op.create_check_constraint(
        "ck_diag_tag_sla_stage",
        "diagnosis_tag",
        "sla_stage IN ('NONE','WARN','BREACH')",
    )
    op.create_index("idx_diag_tag_disposition", "diagnosis_tag", ["disposition_state"])
    op.create_index(
        "idx_diag_tag_active_sla",
        "diagnosis_tag",
        ["status", "sla_stage"],
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    # ======================================================================
    # 10. handling_order alters (M-D/M-E) +8 col + 2 FK + 2 CK + 3 Index
    # ======================================================================
    op.add_column(
        "handling_order",
        sa.Column(
            "sla_policy_id",
            sa.BigInteger(),
            sa.ForeignKey("sla_policy.id", ondelete="SET NULL", name="fk_ho_sla_policy"),
            nullable=True,
        ),
    )
    op.add_column(
        "handling_order",
        sa.Column("sla_deadline_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "handling_order",
        sa.Column(
            "sla_stage",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
    )
    op.add_column(
        "handling_order",
        sa.Column(
            "reopen_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "handling_order",
        sa.Column(
            "reopen_reasons",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'::jsonb") if is_pg else None,
        ),
    )
    op.add_column(
        "handling_order",
        sa.Column("scope_type", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "handling_order",
        sa.Column("scope_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "handling_order",
        sa.Column(
            "handler_id",
            uuid_,
            sa.ForeignKey("sys_user.id", ondelete="SET NULL", name="fk_ho_handler_user"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_handling_order_sla_stage",
        "handling_order",
        "sla_stage IN ('NONE','WARN','BREACH')",
    )
    op.create_check_constraint(
        "ck_handling_order_reopen_nonneg",
        "handling_order",
        "reopen_count >= 0",
    )
    op.create_index("idx_handling_order_scope", "handling_order", ["scope_type", "scope_id"])
    op.create_index("idx_handling_order_handler_id", "handling_order", ["handler_id"])
    op.create_index(
        "idx_handling_order_active_sla",
        "handling_order",
        ["status", "sla_deadline_at"],
        postgresql_where=sa.text(
            "status IN ('PENDING','EXECUTING','VERIFYING') AND sla_deadline_at IS NOT NULL"
        ),
    )

    # ======================================================================
    # 11. kpi_node_snapshot_daily index (M-F)
    # ======================================================================
    op.create_index(
        "idx_kpi_daily_scope_date_desc",
        "kpi_node_snapshot_daily",
        ["plant_node_id", sa.text("stat_date DESC")],
    )

    # ======================================================================
    # 12. sys_user lane_capacity (M-G)
    # ======================================================================
    op.add_column(
        "sys_user",
        sa.Column(
            "lane_capacity",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("6"),
        ),
    )

    # ======================================================================
    # 13. seed data (非破坏性 — ON CONFLICT DO NOTHING)
    # ======================================================================
    # 13.1 sla_policy 32 条 = 8 类 × 4 级默认模板（scope_type/scope_id 空 → 全局默认）
    #     warn_minutes < breach_minutes；priority 越高时限越短
    sla_templates = [
        # (action_type, LOW, MEDIUM, HIGH, CRITICAL)
        ("TUNING", (1440, 2880), (720, 1440), (240, 720), (60, 180)),
        ("VALVE", (1440, 2880), (720, 1440), (240, 720), (60, 180)),
        ("INSTRUMENT", (1440, 2880), (720, 1440), (240, 720), (60, 180)),
        ("LINK", (720, 1440), (360, 720), (120, 360), (30, 90)),
        ("PROCESS", (1440, 2880), (720, 1440), (240, 720), (60, 180)),
        ("UTILIZATION", (2880, 5760), (1440, 2880), (480, 1440), (120, 360)),
        ("RECONFIG", (2880, 5760), (1440, 2880), (480, 1440), (120, 360)),
        ("OTHER", (2880, 5760), (1440, 2880), (480, 1440), (240, 720)),
    ]
    priority_list = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    sla_rows: list[tuple] = []
    for action_type, low, med, high, crit in sla_templates:
        for prio, (warn_m, breach_m) in zip(priority_list, [low, med, high, crit], strict=False):
            is_default = "true" if prio == "MEDIUM" else "false"
            sla_rows.append((action_type, prio, warn_m, breach_m, is_default))

    sla_values_sql = ", ".join(
        f"('{at}','{prio}',{wm},{bm},{df})" for (at, prio, wm, bm, df) in sla_rows
    )
    op.execute(
        f"""
        INSERT INTO sla_policy (action_type, priority, warn_minutes, breach_minutes, is_default)
        VALUES {sla_values_sql}
        ON CONFLICT (action_type, priority, scope_type, scope_id) DO NOTHING
        """
    )

    # 13.2 module_plugin 8 条（CLPM-MVP 模块清单，对应 AGENTS.md §路由顺序）
    module_seeds = [
        # (key,        display,   status,    is_core, order)
        ("monitor", "运行监控", "CORE", "true", 1),
        ("assess", "绩效评估", "CORE", "true", 2),
        ("diagnosis", "回路诊断", "ENABLED", "false", 3),
        ("tuning", "参数整定", "ENABLED", "false", 4),
        ("handling", "问题处置", "ENABLED", "false", 5),
        ("reports", "统计报告", "CORE", "true", 6),
        ("config", "系统配置", "CORE", "true", 7),
        ("system", "系统管理", "CORE", "true", 8),
    ]
    module_values_sql = ", ".join(
        f"('{k}','{d}','{s}',{ic},{o})" for (k, d, s, ic, o) in module_seeds
    )
    op.execute(
        f"""
        INSERT INTO module_plugin (module_key, display_name, status, is_core, order_index)
        VALUES {module_values_sql}
        ON CONFLICT (module_key) DO NOTHING
        """
    )


# ---------------------------------------------------------------------------
# downgrade (严格反向：13 → 1 逆序 drop)
# ---------------------------------------------------------------------------
def downgrade() -> None:
    # 注：seed 数据不需要显式回滚，drop 表/列会一并清空对应行

    # 12. sys_user
    op.drop_column("sys_user", "lane_capacity")

    # 11. kpi_node_snapshot_daily index
    op.drop_index("idx_kpi_daily_scope_date_desc", table_name="kpi_node_snapshot_daily")

    # 10. handling_order — 先 index → constraint → FK → column
    op.drop_index("idx_handling_order_active_sla", table_name="handling_order")
    op.drop_index("idx_handling_order_handler_id", table_name="handling_order")
    op.drop_index("idx_handling_order_scope", table_name="handling_order")
    op.drop_constraint("ck_handling_order_reopen_nonneg", "handling_order", type_="check")
    op.drop_constraint("ck_handling_order_sla_stage", "handling_order", type_="check")
    op.drop_constraint("fk_ho_handler_user", "handling_order", type_="foreignkey")
    op.drop_constraint("fk_ho_sla_policy", "handling_order", type_="foreignkey")
    for col in [
        "handler_id",
        "scope_id",
        "scope_type",
        "reopen_reasons",
        "reopen_count",
        "sla_stage",
        "sla_deadline_at",
        "sla_policy_id",
    ]:
        op.drop_column("handling_order", col)

    # 9. diagnosis_tag
    op.drop_index("idx_diag_tag_active_sla", table_name="diagnosis_tag")
    op.drop_index("idx_diag_tag_disposition", table_name="diagnosis_tag")
    op.drop_constraint("ck_diag_tag_sla_stage", "diagnosis_tag", type_="check")
    op.drop_constraint("ck_diag_tag_disposition", "diagnosis_tag", type_="check")
    op.drop_column("diagnosis_tag", "sla_stage")
    op.drop_column("diagnosis_tag", "sla_deadline_at")
    op.drop_column("diagnosis_tag", "disposition_state")

    # 8. diagnosis_result
    op.drop_column("diagnosis_result", "evidence_summary")
    op.drop_column("diagnosis_result", "recommended_category")

    # 7. event_bus
    op.drop_index("idx_eb_source_type", table_name="event_bus")
    op.drop_index(
        "idx_eb_unread_count",
        table_name="event_bus",
    )
    op.drop_index("idx_eb_read_users", table_name="event_bus", postgresql_using="gin")
    op.drop_index("idx_eb_occurred_desc", table_name="event_bus")
    op.drop_index("idx_eb_scope", table_name="event_bus")
    op.drop_table("event_bus")

    # 6. wb_cache_log
    op.drop_index("idx_wbcl_endpoint_hit", table_name="wb_cache_log")
    op.drop_index("idx_wbcl_key", table_name="wb_cache_log")
    op.drop_index("idx_wbcl_created_desc", table_name="wb_cache_log")
    op.drop_table("wb_cache_log")

    # 5. trend_flags
    op.drop_index("idx_tf_kind_severity", table_name="trend_flags")
    op.drop_index("idx_tf_loop", table_name="trend_flags")
    op.drop_index("idx_tf_scope_window_flagged_desc", table_name="trend_flags")
    op.drop_table("trend_flags")

    # 4. workbench_window_summary
    op.drop_index("idx_ws_snapshot", table_name="workbench_window_summary")
    op.drop_index("idx_ws_scope_window", table_name="workbench_window_summary")
    op.drop_table("workbench_window_summary")

    # 3. tuning_batch_records → tuning_batch
    op.drop_index("idx_tbr_record_batch", table_name="tuning_batch_records")
    op.drop_table("tuning_batch_records")
    op.drop_index("idx_tuning_batch_status", table_name="tuning_batch")
    op.drop_index("idx_tuning_batch_scope", table_name="tuning_batch")
    op.drop_table("tuning_batch")

    # 2. module_plugin
    op.drop_index("idx_module_plugin_status", table_name="module_plugin")
    op.drop_index("idx_module_plugin_order", table_name="module_plugin")
    op.drop_table("module_plugin")

    # 1. sla_policy
    op.drop_index("idx_sla_policy_default", table_name="sla_policy")
    op.drop_table("sla_policy")
