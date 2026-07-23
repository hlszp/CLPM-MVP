"""add phase1 fault and stat metrics to snapshots

Revision ID: 33cee6882ec8
Revises: f6g7h8i9j0k1
Create Date: 2026-07-23 16:12:05.089878

Phase 1（HiaMonitor 借鉴重构，2026-07-23）：为 kpi_snapshot_hourly 与
kpi_snapshot_custom 两张快照表同步新增 16 个指标列：

  - 仪表故障率（instrument_fault_rate）：复用既有 outlier_detection 结果，AGGREGATABLE
  - PV/SP/OP/偏差 统计指标（pv_mean/pv_std/sp_mean/sp_std/op_mean/op_std/
    error_mean/error_std）：DISPLAY_ONLY，不参与节点聚合
  - 阀门诊断指标（valve_linearity/valve_nonlinearity/valve_op_min/valve_op_max/
    oscillation_amplitude）：DISPLAY_ONLY
  - 设定值穿越次数（setpoint_crossing_count）：Numeric(10, 0)，对齐全 Decimal 管道
    （_extract_kpi_values 统一转 Decimal），非 Integer
  - 时间常数（time_constant）：DISPLAY_ONLY

两张快照表必须保持列对齐（项目硬约束）。所有新列均 nullable=True，向后兼容。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "33cee6882ec8"
down_revision: str | Sequence[str] | None = "f6g7h8i9j0k1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Phase 1 新增列定义（两张快照表共用，保持对齐）
# 顺序与 models/metric.py 中定义一致
_PHASE1_COLUMNS: list[tuple[str, sa.Numeric]] = [
    ("instrument_fault_rate", sa.Numeric(precision=5, scale=2)),
    ("pv_mean", sa.Numeric(precision=10, scale=3)),
    ("pv_std", sa.Numeric(precision=10, scale=3)),
    ("sp_mean", sa.Numeric(precision=10, scale=3)),
    ("sp_std", sa.Numeric(precision=10, scale=3)),
    ("op_mean", sa.Numeric(precision=10, scale=3)),
    ("op_std", sa.Numeric(precision=10, scale=3)),
    ("error_mean", sa.Numeric(precision=10, scale=3)),
    ("error_std", sa.Numeric(precision=10, scale=3)),
    ("valve_linearity", sa.Numeric(precision=5, scale=4)),
    ("valve_nonlinearity", sa.Numeric(precision=5, scale=4)),
    ("valve_op_min", sa.Numeric(precision=8, scale=2)),
    ("valve_op_max", sa.Numeric(precision=8, scale=2)),
    ("oscillation_amplitude", sa.Numeric(precision=8, scale=2)),
    ("setpoint_crossing_count", sa.Numeric(precision=10, scale=0)),
    ("time_constant", sa.Numeric(precision=8, scale=2)),
]

_SNAPSHOT_TABLES = ("kpi_snapshot_hourly", "kpi_snapshot_custom")


def upgrade() -> None:
    """Upgrade schema：为两张快照表各新增 16 个指标列（共 32 列）。"""
    for table in _SNAPSHOT_TABLES:
        for col_name, col_type in _PHASE1_COLUMNS:
            op.add_column(
                table,
                sa.Column(col_name, col_type, nullable=True),
            )


def downgrade() -> None:
    """Downgrade schema：移除两张快照表的 16 个 Phase 1 指标列。"""
    for table in _SNAPSHOT_TABLES:
        # 逆序删除，保持与 upgrade 对称（顺序对 DROP COLUMN 无实质影响）
        for col_name, _ in reversed(_PHASE1_COLUMNS):
            op.drop_column(table, col_name)
