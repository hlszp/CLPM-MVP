"""rename action_status RESOLVED to IMPLEMENTED

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-24 00:00:00

将 action_tracker.action_status 枚举值 RESOLVED 重命名为 IMPLEMENTED，
对齐 FDS §5.4.4 "已实施" 语义。
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 更新现有数据：RESOLVED → IMPLEMENTED
    op.execute(
        "UPDATE action_tracker SET action_status = 'IMPLEMENTED' WHERE action_status = 'RESOLVED'"
    )
    # 2. 替换 CheckConstraint
    op.drop_constraint("ck_action_tracker_status", "action_tracker", type_="check")
    op.create_check_constraint(
        "ck_action_tracker_status",
        "action_tracker",
        "action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'IMPLEMENTED')",
    )


def downgrade() -> None:
    # 1. 替换 CheckConstraint 回原值
    op.drop_constraint("ck_action_tracker_status", "action_tracker", type_="check")
    op.create_check_constraint(
        "ck_action_tracker_status",
        "action_tracker",
        "action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'RESOLVED')",
    )
    # 2. 更新现有数据：IMPLEMENTED → RESOLVED
    op.execute(
        "UPDATE action_tracker SET action_status = 'RESOLVED' WHERE action_status = 'IMPLEMENTED'"
    )
