"""add threshold_version to diagnosis_result (C4)

整改计划 C4：配置版本与回滚。

在 diagnosis_result 表增加 threshold_version 列，记录诊断时使用的
阈值配置版本号，实现"历史结论可查当时阈值"的可追溯性（PRD §7 NFR）。

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6g7h8i9j0"
down_revision: str | None = "d4e5f6g7h8i9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_result",
        sa.Column("threshold_version", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("diagnosis_result", "threshold_version")
