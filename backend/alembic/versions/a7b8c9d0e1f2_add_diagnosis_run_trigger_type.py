"""add diagnosis_run.trigger_type

Revision ID: a7b8c9d0e1f2
Revises: mvpdiag002
Create Date: 2026-08-18

自动诊断三层触发（设计文档 07-诊断模块设计方案 §12）：
MANUAL 手动 / SCHEDULED 分级定时 / EVENT 预警事件。
"""

import sqlalchemy as sa

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "mvpdiag002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_run",
        sa.Column(
            "trigger_type",
            sa.String(16),
            nullable=False,
            server_default="MANUAL",
        ),
    )
    op.create_check_constraint(
        "ck_diagnosis_run_trigger_type",
        "diagnosis_run",
        "trigger_type IN ('MANUAL', 'SCHEDULED', 'EVENT')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_diagnosis_run_trigger_type", "diagnosis_run", type_="check")
    op.drop_column("diagnosis_run", "trigger_type")
