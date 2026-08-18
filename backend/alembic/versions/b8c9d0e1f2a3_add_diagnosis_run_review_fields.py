"""diagnosis_run 人工复核字段（复核闭环，2026-08-18）。

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-18

新增列：
- review_status   PENDING/REVIEWED（默认 PENDING，CHECK 约束）
- review_results  JSONB 复核结论多选（原因分类代码数组）
- review_comment  复核意见（≤500 字）
- reviewed_by / reviewed_at 复核人与时间
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_run",
        sa.Column("review_status", sa.String(16), nullable=False, server_default="PENDING"),
    )
    op.add_column("diagnosis_run", sa.Column("review_results", JSONB(), nullable=True))
    op.add_column("diagnosis_run", sa.Column("review_comment", sa.String(500), nullable=True))
    op.add_column("diagnosis_run", sa.Column("reviewed_by", sa.String(64), nullable=True))
    op.add_column("diagnosis_run", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.create_check_constraint(
        "ck_diagnosis_run_review_status",
        "diagnosis_run",
        "review_status IN ('PENDING', 'REVIEWED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_diagnosis_run_review_status", "diagnosis_run", type_="check")
    op.drop_column("diagnosis_run", "reviewed_at")
    op.drop_column("diagnosis_run", "reviewed_by")
    op.drop_column("diagnosis_run", "review_comment")
    op.drop_column("diagnosis_run", "review_results")
    op.drop_column("diagnosis_run", "review_status")
