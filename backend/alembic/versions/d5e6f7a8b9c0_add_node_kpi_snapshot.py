"""add is_kpi_enabled to plant_node and create kpi_node_snapshot_hourly

对齐 GB/T 44693.2-2024 §6.4 综合评估：
- plant_node 新增 is_kpi_enabled 字段（标记是否纳入性能评估）
- 新建 kpi_node_snapshot_hourly 表（节点级每小时性能评估快照）

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-06-24 15:00:00

"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plant_node 新增 is_kpi_enabled 字段
    op.add_column(
        "plant_node",
        sa.Column("is_kpi_enabled", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True),
    )
    op.execute(
        "COMMENT ON COLUMN plant_node.is_kpi_enabled IS "
        "'是否纳入性能评估（TRUE 时生成节点级 KPI 快照）'"
    )

    # 2. 新建 kpi_node_snapshot_hourly 表
    op.create_table(
        "kpi_node_snapshot_hourly",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("plant_node_id", UUID(as_uuid=False), nullable=False),
        sa.Column("ts_start", sa.DateTime(), nullable=False),
        sa.Column("ts_end", sa.DateTime(), nullable=False),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("good_value_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("auto_mode_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("effective_auto_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("steady_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("accuracy_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("fast_response_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("oscillation_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("saturation_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("auto_loop_ratio", sa.Numeric(5, 2), nullable=True),
        sa.Column("loop_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("algorithm_version", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["plant_node_id"],
            ["plant_node.id"],
            ondelete="CASCADE",
            name="fk_kpi_node_snapshot_node",
        ),
        sa.CheckConstraint(
            "status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')",
            name="ck_kpi_node_snapshot_status",
        ),
        sa.CheckConstraint("ts_end > ts_start", name="ck_kpi_node_snapshot_window"),
    )

    # 索引
    op.create_index("idx_kpi_node_snapshot_node_id", "kpi_node_snapshot_hourly", ["plant_node_id"])
    op.create_index("idx_kpi_node_snapshot_ts_start", "kpi_node_snapshot_hourly", ["ts_start"])
    op.create_index("idx_kpi_node_snapshot_status", "kpi_node_snapshot_hourly", ["status"])
    op.create_index(
        "idx_kpi_node_snapshot_node_ts", "kpi_node_snapshot_hourly", ["plant_node_id", "ts_start"]
    )
    op.create_index(
        "idx_kpi_node_snapshot_ts_status",
        "kpi_node_snapshot_hourly",
        ["ts_start", "status", "score"],
    )

    # 注释
    op.execute(
        "COMMENT ON TABLE kpi_node_snapshot_hourly IS "
        "'节点级每小时性能评估快照（按 plant_node 递归聚合，对齐 GB/T 44693.2-2024 §6.4）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_node_snapshot_hourly.plant_node_id IS "
        "'工厂节点 ID（FACTORY/UNIT/EQUIPMENT 任意层级）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_node_snapshot_hourly.status IS "
        "'节点级定级：EXCELLENT/GOOD/FAIR/WARNING/POOR/INCONCLUSIVE'"
    )


def downgrade() -> None:
    op.drop_table("kpi_node_snapshot_hourly")
    op.drop_column("plant_node", "is_kpi_enabled")
