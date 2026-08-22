"""add_fitness_fields_to_kpi_snapshots

Revision ID: c3bee6758850
Revises: h4c5d6e7f8a9
Create Date: 2026-08-22 15:10:08.326462

P2 IA优化：为 kpi_snapshot_hourly 和 kpi_snapshot_custom 新增适用性分层字段。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3bee6758850"
down_revision: str | Sequence[str] | None = "h4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema — 为两张快照表加 3 字段 + CHECK + INDEX."""
    # --- kpi_snapshot_custom ---
    op.add_column(
        "kpi_snapshot_custom",
        sa.Column("fitness_level", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "kpi_snapshot_custom",
        sa.Column("fitness_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "kpi_snapshot_custom",
        sa.Column("fitness_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_kpi_snapshot_custom_fitness_level",
        "kpi_snapshot_custom",
        ["fitness_level"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_kpi_custom_fitness_level",
        "kpi_snapshot_custom",
        "fitness_level IS NULL OR fitness_level IN ('L0', 'L1', 'L2', 'L3', 'L4')",
    )

    # --- kpi_snapshot_hourly ---
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("fitness_level", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("fitness_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("fitness_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "idx_kpi_snapshot_fitness_level",
        "kpi_snapshot_hourly",
        ["fitness_level"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_kpi_snapshot_fitness_level",
        "kpi_snapshot_hourly",
        "fitness_level IS NULL OR fitness_level IN ('L0', 'L1', 'L2', 'L3', 'L4')",
    )

    # 更新表注释
    op.execute(
        "COMMENT ON TABLE kpi_snapshot_hourly IS "
        "'每小时性能评估快照（好值率基于 PV 质量码统计，含P2适用性分层字段）'"
    )
    op.execute(
        "COMMENT ON TABLE kpi_snapshot_custom IS "
        "'自定义评估任务快照（按需触发，不参与装置级聚合，含P2适用性分层字段）'"
    )


def downgrade() -> None:
    """Downgrade schema — 回滚所有新增对象."""
    # --- kpi_snapshot_hourly ---
    op.drop_constraint("ck_kpi_snapshot_fitness_level", "kpi_snapshot_hourly", type_="check")
    op.drop_index("idx_kpi_snapshot_fitness_level", table_name="kpi_snapshot_hourly")
    op.drop_column("kpi_snapshot_hourly", "fitness_detail")
    op.drop_column("kpi_snapshot_hourly", "fitness_tags")
    op.drop_column("kpi_snapshot_hourly", "fitness_level")

    # --- kpi_snapshot_custom ---
    op.drop_constraint("ck_kpi_custom_fitness_level", "kpi_snapshot_custom", type_="check")
    op.drop_index("ix_kpi_snapshot_custom_fitness_level", table_name="kpi_snapshot_custom")
    op.drop_column("kpi_snapshot_custom", "fitness_detail")
    op.drop_column("kpi_snapshot_custom", "fitness_tags")
    op.drop_column("kpi_snapshot_custom", "fitness_level")

    # 还原表注释
    op.execute(
        "COMMENT ON TABLE kpi_snapshot_hourly IS '每小时性能评估快照（好值率基于 PV 质量码统计）'"
    )
    op.execute(
        "COMMENT ON TABLE kpi_snapshot_custom IS '自定义评估任务快照（按需触发，不参与装置级聚合）'"
    )
