"""add realtime_auto_rate to kpi_node_snapshot_hourly

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-24 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kpi_node_snapshot_hourly",
        sa.Column("realtime_auto_rate", sa.Numeric(5, 2), nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN kpi_node_snapshot_hourly.realtime_auto_rate "
        "IS '实时自控率（%）：当前时刻处于自动模式的回路占比'"
    )


def downgrade() -> None:
    op.drop_column("kpi_node_snapshot_hourly", "realtime_auto_rate")
