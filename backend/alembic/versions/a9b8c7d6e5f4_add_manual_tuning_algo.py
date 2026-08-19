"""add MANUAL_TUNING to tuning_record algorithm constraint

整定矩阵第 6 行"手动整定"：algorithm 新增 MANUAL_TUNING（参数由工程师
手工设定，不经算法计算）。schema Literal 已同步扩展，本迁移对齐 DB 层
CheckConstraint。

Revision ID: a9b8c7d6e5f4
Revises: a3b4c5d6e7f8
Create Date: 2026-08-19 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CONSTRAINT = (
    "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC', 'IDENTIFICATION_ONLY')"
)
_NEW_CONSTRAINT = (
    "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC', "
    "'IDENTIFICATION_ONLY', 'MANUAL_TUNING')"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("ck_tuning_record_algo", "tuning_record", type_="check")
    op.create_check_constraint("ck_tuning_record_algo", "tuning_record", _NEW_CONSTRAINT)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_tuning_record_algo", "tuning_record", type_="check")
    op.create_check_constraint("ck_tuning_record_algo", "tuning_record", _OLD_CONSTRAINT)
