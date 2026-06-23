"""add kpi_snapshot_hourly composite indexes

为 kpi_snapshot_hourly 表添加复合索引，优化常见查询模式：
- idx_kpi_snapshot_loop_ts (loop_id, ts_start) — 按回路+时间范围查询
- idx_kpi_snapshot_ts_status_score (ts_start, status, score) — 诊断引擎按时间+状态+评分查询

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-06-23 11:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "idx_kpi_snapshot_loop_ts",
        "kpi_snapshot_hourly",
        ["loop_id", "ts_start"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_kpi_snapshot_ts_status_score",
        "kpi_snapshot_hourly",
        ["ts_start", "status", "score"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_kpi_snapshot_ts_status_score", table_name="kpi_snapshot_hourly")
    op.drop_index("idx_kpi_snapshot_loop_ts", table_name="kpi_snapshot_hourly")
