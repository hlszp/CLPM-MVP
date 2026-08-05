"""P1a: action_tracker扩展闭环状态机字段

Revision ID: e093854b7bed
Revises: b7c8d9e0f1g2
Create Date: 2026-08-05 15:55:41.946021

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e093854b7bed"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1g2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # P1a: 闭环状态机扩展字段
    op.add_column(
        "action_tracker",
        sa.Column("implemented_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "action_tracker",
        sa.Column("implemented_by", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "action_tracker",
        sa.Column("new_pid_p", sa.Float(), nullable=True),
    )
    op.add_column(
        "action_tracker",
        sa.Column("new_pid_i", sa.Float(), nullable=True),
    )
    op.add_column(
        "action_tracker",
        sa.Column("new_pid_d", sa.Float(), nullable=True),
    )
    op.add_column(
        "action_tracker",
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "action_tracker",
        sa.Column("reopen_reason", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("action_tracker", "reopen_reason")
    op.drop_column("action_tracker", "closed_at")
    op.drop_column("action_tracker", "new_pid_d")
    op.drop_column("action_tracker", "new_pid_i")
    op.drop_column("action_tracker", "new_pid_p")
    op.drop_column("action_tracker", "implemented_by")
    op.drop_column("action_tracker", "implemented_at")
