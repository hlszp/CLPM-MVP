"""add instrument_fault_rate to node snapshots

Revision ID: d6e7f8a9b0c1
Revises: c588a06c1c05
Create Date: 2026-07-23 19:30:00.000000

Phase 1（HiaMonitor 借鉴重构，2026-07-23）：为三张节点级快照表同步新增
``instrument_fault_rate`` 列（Numeric(5,2), nullable=True）。

  - kpi_node_snapshot_hourly
  - kpi_node_snapshot_daily
  - kpi_node_snapshot_monthly

背景：仪表故障率是 Phase 1 唯一参与节点级加权聚合的新指标（AGGREGATABLE）。
回路级 ``kpi_snapshot_hourly`` 已在 33cee6882ec8 中新增该列，本迁移将其
扩展到节点级三张表，使 ``node_performance.aggregate_node_snapshot`` 的加权
聚合 SQL（KPI_FIELDS）与 ``node_aggregation._weighted_average``
（AGGREGATE_FIELDS）能正确读写该字段。

三张节点级快照表必须保持列对齐（项目硬约束）。新列 nullable=True，向后兼容。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "c588a06c1c05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Phase 1 新增列定义（三张节点级快照表共用，保持对齐）
_PHASE1_NODE_COLUMN: tuple[str, sa.Numeric] = (
    "instrument_fault_rate",
    sa.Numeric(precision=5, scale=2),
)

_NODE_SNAPSHOT_TABLES = (
    "kpi_node_snapshot_hourly",
    "kpi_node_snapshot_daily",
    "kpi_node_snapshot_monthly",
)


def upgrade() -> None:
    """Upgrade schema：为三张节点级快照表各新增 instrument_fault_rate 列。"""
    col_name, col_type = _PHASE1_NODE_COLUMN
    for table in _NODE_SNAPSHOT_TABLES:
        op.add_column(
            table,
            sa.Column(col_name, col_type, nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema：移除三张节点级快照表的 instrument_fault_rate 列。"""
    col_name, _ = _PHASE1_NODE_COLUMN
    for table in _NODE_SNAPSHOT_TABLES:
        op.drop_column(table, col_name)
