"""add instrument_fault_rate to unit_kpi_summary

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-23 20:00:00.000000

Phase 1（HiaMonitor 借鉴重构，2026-07-23）：为 ``unit_kpi_summary`` 装置级
KPI 汇总表新增 ``instrument_fault_rate`` 列（Numeric(5,2), nullable=True）。

背景：仪表故障率是 Phase 1 唯一参与装置级加权聚合的新指标（AGGREGATABLE）。
回路级（``kpi_snapshot_hourly``，33cee6882ec8）与节点级三张快照表
（``kpi_node_snapshot_*``，d6e7f8a9b0c1）已新增该列，本迁移将其扩展到
装置级汇总表，使 ``dashboard.get_board_aggregate`` 的加权聚合 SQL
（``_WINDOW_RATE_FIELD_KEYS``）与 ``performance._aggregate_kpi_cards`` 能正确
读写该字段。

新列 nullable=True，向后兼容。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "unit_kpi_summary"
_COLUMN = "instrument_fault_rate"


def upgrade() -> None:
    """Upgrade schema：为 unit_kpi_summary 新增 instrument_fault_rate 列。"""
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Numeric(precision=5, scale=2), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema：移除 unit_kpi_summary 的 instrument_fault_rate 列。"""
    op.drop_column(_TABLE, _COLUMN)
