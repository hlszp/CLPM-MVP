"""P3-01: 整定知识库表 + ActionTracker.tuning_record_id 外键

Revision ID: f1a2b3c4d5e6
Revises: e093854b7bed
Create Date: 2026-08-05 20:30:00.000000

变更：
1. action_tracker 追加 tuning_record_id（nullable, FK→tuning_record.id, ondelete SET NULL）
   + idx_action_tracker_tuning_record 索引
2. 新建 tuning_knowledge_entry 表（不可变快照，验证完成时聚合生成）
   + 5 个索引（含 tracker_id unique 防重）+ match_source check 约束
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e093854b7bed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ========== 1. action_tracker 追加 tuning_record_id ==========
    op.add_column(
        "action_tracker",
        sa.Column("tuning_record_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_action_tracker_tuning_record",
        "action_tracker",
        "tuning_record",
        ["tuning_record_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_action_tracker_tuning_record",
        "action_tracker",
        ["tuning_record_id"],
    )

    # ========== 2. 新建 tuning_knowledge_entry 表 ==========
    op.create_table(
        "tuning_knowledge_entry",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
        ),
        sa.Column(
            "tracker_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "tuning_record_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("loop_type", sa.String(length=20), nullable=True),
        sa.Column("control_type", sa.String(length=20), nullable=True),
        sa.Column("tag_name", sa.String(length=100), nullable=False),
        sa.Column("diagnosis_label", sa.String(length=100), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("model_type", sa.String(length=20), nullable=True),
        sa.Column("algorithm", sa.String(length=50), nullable=True),
        sa.Column("identify_method", sa.String(length=30), nullable=True),
        sa.Column("confidence_level", sa.String(length=12), nullable=True),
        sa.Column("pid_before", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("pid_after", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("kpi_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("effect_verified", sa.Boolean(), nullable=True),
        sa.Column("improved_count", sa.SmallInteger(), nullable=True),
        sa.Column("deteriorated_count", sa.SmallInteger(), nullable=True),
        sa.Column(
            "match_source",
            sa.String(length=20),
            nullable=False,
            server_default="none",
        ),
        sa.Column("implemented_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "match_source IN ('exact', 'time_window', 'none')",
            name="ck_tke_match_source",
        ),
        sa.ForeignKeyConstraint(
            ["tracker_id"],
            ["action_tracker.id"],
            name="fk_tke_tracker",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tuning_record_id"],
            ["tuning_record.id"],
            name="fk_tke_tuning_record",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["loop_id"],
            ["loop_ledger.id"],
            name="fk_tke_loop",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_tke_loop_type_label",
        "tuning_knowledge_entry",
        ["loop_type", "diagnosis_label"],
    )
    op.create_index(
        "idx_tke_label",
        "tuning_knowledge_entry",
        ["diagnosis_label"],
    )
    op.create_index(
        "idx_tke_loop_id",
        "tuning_knowledge_entry",
        ["loop_id"],
    )
    op.create_index(
        "idx_tke_effect",
        "tuning_knowledge_entry",
        ["effect_verified"],
    )
    op.create_index(
        "idx_tke_tracker",
        "tuning_knowledge_entry",
        ["tracker_id"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_tke_tracker", table_name="tuning_knowledge_entry")
    op.drop_index("idx_tke_effect", table_name="tuning_knowledge_entry")
    op.drop_index("idx_tke_loop_id", table_name="tuning_knowledge_entry")
    op.drop_index("idx_tke_label", table_name="tuning_knowledge_entry")
    op.drop_index("idx_tke_loop_type_label", table_name="tuning_knowledge_entry")
    op.drop_table("tuning_knowledge_entry")
    op.drop_index("idx_action_tracker_tuning_record", table_name="action_tracker")
    op.drop_constraint("fk_action_tracker_tuning_record", "action_tracker", type_="foreignkey")
    op.drop_column("action_tracker", "tuning_record_id")
