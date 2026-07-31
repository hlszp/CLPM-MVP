"""V62-P3-008 Action Tracker 新增 assignee + planned_at 字段.

revision: p3e5f6g7h8i9
down_revision: p3d4e5f6g7h8

新增两列：
- ``assignee``: 实施责任人（与 triggered_by 建单人区分）
- ``planned_at``: 计划执行时间（与 updated_at 实际完成时间区分）

均为 nullable，向后兼容存量记录。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "p3e5f6g7h8i9"
down_revision = "p3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "action_tracker",
        sa.Column("assignee", sa.String(50), nullable=True),
    )
    op.add_column(
        "action_tracker",
        sa.Column("planned_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("action_tracker", "planned_at")
    op.drop_column("action_tracker", "assignee")
