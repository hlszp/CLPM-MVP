"""handling_order 实施记录与验证证据列（P3-05，DEC-06 已裁）

新增 5 列（全部可空/带默认，存量行零回填——历史记录语义为"未知/未确认"，
不伪填 submitted_at，不伪判证据等级）：
- implemented_at：实际实施时刻（与 submitted_at 补录时刻分离）
- closure_evidence：闭环证据三分类 MANUAL_CONFIRMATION/DATA_VERIFIED/
  COMPARABLE_CONDITION_VERIFIED（+ CHECK 约束；存量行保持 NULL=未验证）
- actual_rollback_confirmed / rollback_confirmed_at / rollback_confirmed_by /
  rollback_note：DCS 人工回退事实（独立于 ROLLED_BACK 判断结论）

Revision ID: p305evd001
Revises: 170fddd7af99
Create Date: 2026-10-12

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "p305evd001"
down_revision = "170fddd7af99"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "handling_order",
        sa.Column("implemented_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "handling_order",
        sa.Column("closure_evidence", sa.String(32), nullable=True),
    )
    op.add_column(
        "handling_order",
        sa.Column(
            "actual_rollback_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "handling_order",
        sa.Column("rollback_confirmed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "handling_order",
        sa.Column("rollback_confirmed_by", sa.String(64), nullable=True),
    )
    op.add_column(
        "handling_order",
        sa.Column("rollback_note", sa.String(200), nullable=True),
    )
    op.create_check_constraint(
        "ck_handling_order_closure_evidence",
        "handling_order",
        "closure_evidence IS NULL OR closure_evidence IN "
        "('MANUAL_CONFIRMATION', 'DATA_VERIFIED', 'COMPARABLE_CONDITION_VERIFIED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_handling_order_closure_evidence", "handling_order", type_="check")
    op.drop_column("handling_order", "rollback_note")
    op.drop_column("handling_order", "rollback_confirmed_by")
    op.drop_column("handling_order", "rollback_confirmed_at")
    op.drop_column("handling_order", "actual_rollback_confirmed")
    op.drop_column("handling_order", "closure_evidence")
    op.drop_column("handling_order", "implemented_at")
