"""add diagnostic fields to node snapshot tables

P1 #14 B4: 补全节点级聚合缺失的 4 个 KPI 字段，对齐回路级 KpiSnapshotHourly：
- stiction_coeff       阀门黏滞系数（Numeric(5,2)）
- steady_state_time    稳态时间（秒，Numeric(8,2)）
- output_travel_index  输出行程指标（Numeric(8,2)）
- ideal_settling_time  理想稳态时间（秒，Numeric(8,2)）

影响表：
- kpi_node_snapshot_hourly
- kpi_node_snapshot_daily
- kpi_node_snapshot_monthly

设计依据：GB/T 44693.2-2024 §6.4 节点级综合评估；
节点级聚合用回路重要性权重加权均值（与现有 9 个字段一致）。

Revision ID: l5p6q7r8s9t0
Revises: k2f3a4b5c6d7
Create Date: 2026-07-02 19:30:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "l5p6q7r8s9t0"
down_revision = "k2f3a4b5c6d7"
branch_labels = None
depends_on = None


#: 受影响的 3 张节点级快照表
_NODE_TABLES = (
    "kpi_node_snapshot_hourly",
    "kpi_node_snapshot_daily",
    "kpi_node_snapshot_monthly",
)

#: 新增的 4 个诊断字段（字段名, Numeric 精度）
_NEW_FIELDS = (
    ("stiction_coeff", sa.Numeric(5, 2)),
    ("steady_state_time", sa.Numeric(8, 2)),
    ("output_travel_index", sa.Numeric(8, 2)),
    ("ideal_settling_time", sa.Numeric(8, 2)),
)


def upgrade() -> None:
    for table in _NODE_TABLES:
        for col_name, col_type in _NEW_FIELDS:
            op.add_column(
                table,
                sa.Column(col_name, col_type, nullable=True),
            )
            op.execute(
                f"COMMENT ON COLUMN {table}.{col_name} IS "
                f"'P1 #14: 节点级加权均值（对齐 GB/T 44693.2-2024 §6.4）'"
            )


def downgrade() -> None:
    for table in _NODE_TABLES:
        # 逆序删除以保持与 upgrade 对称
        for col_name, _ in reversed(_NEW_FIELDS):
            op.drop_column(table, col_name)
