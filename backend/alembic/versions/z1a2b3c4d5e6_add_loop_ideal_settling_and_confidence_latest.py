"""add loop ideal_settling_time and loop_confidence_latest

Revision ID: z1a2b3c4d5e6
Revises: v6p1merge002
Create Date: 2026-07-20

三项功能落库（2026-07-20）：
1. loop_ledger.ideal_settling_time：回路级理想稳态时间（秒），空则按控制类型默认值
2. loop_confidence_latest：回路最新一次可信度评估结果（每回路一条，
   随小时快照 UPSERT 覆盖更新，供回路性能页可信度抽屉查询）

注意：开发库可能已被集成测试用 checkfirst 建过 loop_confidence_latest，
使用 CREATE IF NOT EXISTS 语义（op.execute + DDL 条件创建）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "z1a2b3c4d5e6"
down_revision: str | None = "v6p1merge002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. loop_ledger 增加理想稳态时间列（空则按控制类型默认值）
    op.add_column(
        "loop_ledger",
        sa.Column(
            "ideal_settling_time",
            sa.Float(),
            nullable=True,
            comment="理想稳态时间（秒），空则按控制类型默认值",
        ),
    )

    # 2. loop_confidence_latest 表（条件创建，兼容集成测试 checkfirst 已建场景）
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "loop_confidence_latest" not in inspector.get_table_names():
        op.create_table(
            "loop_confidence_latest",
            sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
            sa.Column(
                "loop_id",
                postgresql.UUID(as_uuid=False),
                sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("eval_time", sa.DateTime(), nullable=False, comment="评估时间（naive UTC）"),
            sa.Column("data_ts_start", sa.DateTime(), nullable=False, comment="数据源时间区间起"),
            sa.Column("data_ts_end", sa.DateTime(), nullable=False, comment="数据源时间区间止"),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("score", sa.Numeric(5, 2), nullable=True),
            sa.Column("confidence_level", sa.String(1), nullable=True),
            sa.Column("valid_rate", sa.Float(), nullable=True),
            sa.Column("metrics", postgresql.JSONB(), nullable=True, comment="12 子指标值+可信度"),
            sa.Column("algorithm_version", sa.String(50), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "status IN ('SUCCESS', 'INCONCLUSIVE', 'PARTIAL')",
                name="ck_loop_confidence_latest_status",
            ),
            sa.CheckConstraint(
                "confidence_level IS NULL OR confidence_level IN ('A', 'B', 'C', 'D', 'E')",
                name="ck_loop_confidence_latest_confidence",
            ),
            comment="回路最新一次可信度评估结果（每回路一条，随小时快照覆盖更新）",
        )
        op.create_index(
            "idx_loop_confidence_latest_loop_id",
            "loop_confidence_latest",
            ["loop_id"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("idx_loop_confidence_latest_loop_id", table_name="loop_confidence_latest")
    op.drop_table("loop_confidence_latest")
    op.drop_column("loop_ledger", "ideal_settling_time")
