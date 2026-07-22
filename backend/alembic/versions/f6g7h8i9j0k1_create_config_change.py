"""create diagnosis_config_change table (C5)

整改计划 C5：关键配置审批流。

危化企业关键诊断配置变更（触发阈值、规则启停等）须经第二人审批后方可生效。
"双人确认"：审批人不能与申请人相同。

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6g7h8i9j0k1"
down_revision: str | None = "e5f6g7h8i9j0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_config_change",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("before_value", sa.Text, nullable=True),
        sa.Column("after_value", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("requested_by", sa.String(50), nullable=False),
        sa.Column("requested_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_by", sa.String(50), nullable=True),
        sa.Column("reviewed_at", sa.DateTime, nullable=True),
        sa.Column("review_note", sa.Text, nullable=True),
        sa.Column("effective_from", sa.DateTime, nullable=True),
        sa.CheckConstraint(
            "target_type IN ('config', 'rule', 'trigger')",
            name="ck_diag_config_change_target",
        ),
        sa.CheckConstraint(
            "change_type IN ('update', 'enable', 'disable')",
            name="ck_diag_config_change_type",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_diag_config_change_status",
        ),
    )
    op.create_index(
        "idx_diag_config_change_status",
        "diagnosis_config_change",
        ["status"],
    )
    op.create_index(
        "idx_diag_config_change_target",
        "diagnosis_config_change",
        ["target_type", "target_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_diag_config_change_target", table_name="diagnosis_config_change")
    op.drop_index("idx_diag_config_change_status", table_name="diagnosis_config_change")
    op.drop_table("diagnosis_config_change")
