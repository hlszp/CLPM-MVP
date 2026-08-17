"""add COMMUNICATION to diagnosis_run category enum

Revision ID: mvpdiag002
Revises: mvpdiag001
Create Date: 2026-08-17

诊断分类新增"通信链路问题"（COMMUNICATION）：Q001 连续 Bad 断流从
INSTRUMENT 细分为独立分类，PG CHECK 约束重建以扩展枚举。
"""

from alembic import op

revision = "mvpdiag002"
down_revision = "mvpdiag001"
branch_labels = None
depends_on = None

_NEW_CONSTRAINT = (
    "primary_category IS NULL OR primary_category IN "
    "('TUNING', 'VALVE', 'INSTRUMENT', 'COMMUNICATION', 'PROCESS', "
    "'UTILIZATION', 'DESIGN', 'DATA_INSUFFICIENT')"
)
_OLD_CONSTRAINT = (
    "primary_category IS NULL OR primary_category IN "
    "('TUNING', 'VALVE', 'INSTRUMENT', 'PROCESS', 'UTILIZATION', 'DESIGN', "
    "'DATA_INSUFFICIENT')"
)


def upgrade() -> None:
    op.drop_constraint("ck_diagnosis_run_category", "diagnosis_run", type_="check")
    op.create_check_constraint("ck_diagnosis_run_category", "diagnosis_run", _NEW_CONSTRAINT)


def downgrade() -> None:
    # 回滚前需确认无 COMMUNICATION 行，否则约束重建失败（符合预期）
    op.execute("DELETE FROM diagnosis_run WHERE primary_category = 'COMMUNICATION'")
    op.drop_constraint("ck_diagnosis_run_category", "diagnosis_run", type_="check")
    op.create_check_constraint("ck_diagnosis_run_category", "diagnosis_run", _OLD_CONSTRAINT)
