"""add loop_action_item

Revision ID: b1ad2fabdea4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-18 10:32:48.209740

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1ad2fabdea4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "loop_action_item",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("loop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False, server_default="SYSTEM"),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("basis", sa.String(length=500), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("suggested_by", sa.String(length=64), nullable=False),
        sa.Column("suggested_at", sa.DateTime(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("source IN ('SYSTEM', 'MANUAL')", name="ck_loop_action_item_source"),
        sa.CheckConstraint("status IN ('PENDING')", name="ck_loop_action_item_status"),
        sa.CheckConstraint(
            "category IS NULL OR category IN "
            "('TUNING', 'VALVE', 'INSTRUMENT', 'COMMUNICATION', 'PROCESS', "
            "'UTILIZATION', 'DESIGN', 'DATA_INSUFFICIENT')",
            name="ck_loop_action_item_category",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["diagnosis_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["loop_id"], ["loop_ledger.id"], ondelete="CASCADE"),
        comment="回路处置建议（建议-处置-验证-关闭闭环，当前仅建议态）",
    )
    op.create_index("idx_loop_action_item_run", "loop_action_item", ["run_id"], unique=False)
    op.create_index(
        "idx_loop_action_item_loop",
        "loop_action_item",
        ["loop_id", "suggested_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_loop_action_item_run", table_name="loop_action_item")
    op.drop_index("idx_loop_action_item_loop", table_name="loop_action_item")
    op.drop_table("loop_action_item")
