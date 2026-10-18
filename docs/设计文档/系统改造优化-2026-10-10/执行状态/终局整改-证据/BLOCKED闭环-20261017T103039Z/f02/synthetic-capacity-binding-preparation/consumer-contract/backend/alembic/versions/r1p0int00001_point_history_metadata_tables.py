"""point history metadata tables (tag-timeseries-refactor P1)

Revision ID: r1p0int00001
Revises: g7b8c9d0e1f2
Create Date: 2026-09-06

测点子表重构 P1：六张元数据表（设计 §4.2）。
TDengine 侧 DDL（st_point_data_v1）不在 alembic 管辖，由
app.services.data_source.point_history_repository.ensure_schema 幂等执行
（见 db/tdengine/02_point_history.sql 文档化契约）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "r1p0int00001"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 绑定历史区间不重叠约束需要 btree_gist（标准 contrib 扩展）
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "loop_tag_binding_history",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "loop_id",
            UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag_role", sa.String(20), nullable=False),
        sa.Column(
            "tag_id",
            UUID(as_uuid=False),
            sa.ForeignKey("tag_registry.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mapping_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("basis", sa.String(32), nullable=False, server_default="MVP_INIT"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_ltbh_valid_range"),
        sa.UniqueConstraint("loop_id", "tag_role", "valid_from", name="uq_ltbh_from"),
    )
    op.create_index("ix_ltbh_loop_role", "loop_tag_binding_history", ["loop_id", "tag_role"])
    op.create_index("ix_ltbh_tag", "loop_tag_binding_history", ["tag_id"])
    # 设计 §4.2：同回路角色生效区间不得重叠（半开区间 [valid_from, valid_to)）
    op.execute(
        "ALTER TABLE loop_tag_binding_history ADD CONSTRAINT ex_ltbh_no_overlap "
        "EXCLUDE USING gist (loop_id WITH =, tag_role WITH =, "
        "tstzrange(valid_from, COALESCE(valid_to, 'infinity'), '[)') WITH &&)"
    )

    op.create_table(
        "history_write_batch",
        sa.Column("batch_id", UUID(as_uuid=False), primary_key=True),
        sa.Column("source_task", sa.String(64), nullable=True),
        sa.Column("target_kind", sa.String(16), nullable=False, server_default="point_events"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("input_digest", sa.String(64), nullable=True),
        sa.Column("stats", JSONB(), nullable=False, server_default="{}"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("recovery_info", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'partial', 'failed', 'cancelled')",
            name="ck_hwb_status",
        ),
    )

    op.create_table(
        "history_coverage_segment",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "point_id",
            UUID(as_uuid=False),
            sa.ForeignKey("tag_registry.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("seg_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seg_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("binding_version", sa.String(64), nullable=True),
        sa.Column(
            "batch_id",
            UUID(as_uuid=False),
            sa.ForeignKey("history_write_batch.batch_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_task", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("seg_end > seg_start", name="ck_hcs_half_open"),
        sa.CheckConstraint("(point_id IS NULL) <> (session_id IS NULL)", name="ck_hcs_scope"),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'gap')", name="ck_hcs_status"),
    )
    op.create_index("ix_hcs_point_window", "history_coverage_segment", ["point_id", "seg_start"])
    op.create_index("ix_hcs_session", "history_coverage_segment", ["session_id", "seg_start"])

    op.create_table(
        "history_layout_manifest",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("scope_type", sa.String(16), nullable=False, server_default="global"),
        sa.Column("scope_id", sa.String(64), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("layout", sa.String(16), nullable=False, server_default="legacy"),
        sa.Column("data_version", sa.String(64), nullable=False, server_default="v1"),
        sa.Column("basis", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_hlm_range"),
        sa.CheckConstraint("layout IN ('legacy', 'shadow', 'point')", name="ck_hlm_layout"),
        sa.CheckConstraint("scope_type IN ('global', 'loop', 'source')", name="ck_hlm_scope"),
    )
    op.create_index("ix_hlm_scope", "history_layout_manifest", ["scope_type", "scope_id"])

    op.create_table(
        "history_point_conflict",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "point_id",
            UUID(as_uuid=False),
            sa.ForeignKey("tag_registry.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts_ms", sa.BigInteger(), nullable=False),
        sa.Column("existing_hash", sa.String(64), nullable=False),
        sa.Column("existing_payload", JSONB(), nullable=False),
        sa.Column("new_payload", JSONB(), nullable=False),
        sa.Column("source_task", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved_skip', 'resolved_overwrite')",
            name="ck_hpc_status",
        ),
        sa.UniqueConstraint("point_id", "ts_ms", "existing_hash", name="uq_hpc_point_ts"),
    )
    op.create_index("ix_hpc_status", "history_point_conflict", ["status"])

    op.create_table(
        "point_state_anchor",
        sa.Column(
            "point_id",
            UUID(as_uuid=False),
            sa.ForeignKey("tag_registry.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("anchor_ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("quality_raw", sa.Integer(), nullable=True),
        sa.Column("quality_class", sa.Integer(), nullable=False),
        sa.Column("quality_schema", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("coverage_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_version", sa.String(64), nullable=False, server_default="v1"),
        sa.CheckConstraint("quality_class IN (1, 0, -1)", name="ck_psa_quality"),
    )
    op.create_index("ix_psa_point_ts", "point_state_anchor", ["point_id", "anchor_ts"])


def downgrade() -> None:
    op.drop_table("point_state_anchor")
    op.drop_table("history_point_conflict")
    op.drop_table("history_layout_manifest")
    op.drop_index("ix_hcs_session", table_name="history_coverage_segment")
    op.drop_index("ix_hcs_point_window", table_name="history_coverage_segment")
    op.drop_table("history_coverage_segment")
    op.drop_table("history_write_batch")
    op.drop_index("ix_ltbh_tag", table_name="loop_tag_binding_history")
    op.drop_index("ix_ltbh_loop_role", table_name="loop_tag_binding_history")
    op.execute("ALTER TABLE loop_tag_binding_history DROP CONSTRAINT IF EXISTS ex_ltbh_no_overlap")
    op.drop_table("loop_tag_binding_history")
