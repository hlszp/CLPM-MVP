"""add loop op_output_lower_limit and op_output_upper_limit columns

Revision ID: v6p1lmt001
Revises: v6p1merge001
Create Date: 2026-07-06

新增 loop_ledger.op_output_lower_limit / op_output_upper_limit 列，
用于饱和率算法计算控制器输出饱和时长。

设计依据：docs/设计文档/00-BASELINE/loop-range-and-output-limits-design-v1.0.md §5.1

字段语义：
- op_output_lower_limit: OP 输出下限位（NULL 时取 OP Tag range_min，再 NULL 时取 0.0）
- op_output_upper_limit: OP 输出上限位（NULL 时取 OP Tag range_max，再 NULL 时取 100.0）

应用层校验规则（service 层实现，不依赖数据库约束）：
- op_output_lower_limit < op_output_upper_limit
- op_output_lower_limit >= OP Tag.range_min
- op_output_upper_limit <= OP Tag.range_max
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "v6p1lmt001"
down_revision = "v6p1merge001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loop_ledger",
        sa.Column(
            "op_output_lower_limit",
            sa.Float(),
            nullable=True,
            comment="OP 输出下限位（NULL 时取 OP Tag range_min，再 NULL 时取 0.0）",
        ),
    )
    op.add_column(
        "loop_ledger",
        sa.Column(
            "op_output_upper_limit",
            sa.Float(),
            nullable=True,
            comment="OP 输出上限位（NULL 时取 OP Tag range_max，再 NULL 时取 100.0）",
        ),
    )


def downgrade() -> None:
    op.drop_column("loop_ledger", "op_output_upper_limit")
    op.drop_column("loop_ledger", "op_output_lower_limit")
