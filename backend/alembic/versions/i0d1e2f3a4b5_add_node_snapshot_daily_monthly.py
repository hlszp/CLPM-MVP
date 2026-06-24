"""add kpi_node_snapshot_daily and kpi_node_snapshot_monthly

对齐 GB/T 44693.2-2024 §6.4 综合评估的多级时间聚合：
- kpi_node_snapshot_daily：日级聚合快照（按 loop_count 加权平均当天 24 条小时快照）
- kpi_node_snapshot_monthly：月级聚合快照（按 loop_count 加权平均当月所有日快照）

字段与 kpi_node_snapshot_hourly 对齐，但 ts_start/ts_end 改为 stat_date / stat_month。
realtime_auto_rate 取当天/当月最后一次小时快照的值（非聚合）。

Revision ID: i0d1e2f3a4b5
Revises: h9c0d1e2f3a4
Create Date: 2026-06-24 19:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "i0d1e2f3a4b5"
down_revision = "h9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 新建 kpi_node_snapshot_daily 表（日级聚合快照）
    op.create_table(
        "kpi_node_snapshot_daily",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("plant_node_id", UUID(as_uuid=False), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
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
        sa.Column("realtime_auto_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("loop_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("algorithm_version", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["plant_node_id"], ["plant_node.id"], ondelete="CASCADE",
                                name="fk_kpi_node_snapshot_daily_node"),
        sa.CheckConstraint(
            "status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')",
            name="ck_kpi_node_snapshot_daily_status",
        ),
        sa.UniqueConstraint("plant_node_id", "stat_date",
                            name="uk_kpi_node_snapshot_daily_node_date"),
    )

    op.create_index("idx_kpi_node_snapshot_daily_node_id", "kpi_node_snapshot_daily",
                    ["plant_node_id"])
    op.create_index("idx_kpi_node_snapshot_daily_stat_date", "kpi_node_snapshot_daily",
                    ["stat_date"])
    op.create_index("idx_kpi_node_snapshot_daily_status", "kpi_node_snapshot_daily",
                    ["status"])
    op.create_index("idx_kpi_node_snapshot_daily_node_date", "kpi_node_snapshot_daily",
                    ["plant_node_id", "stat_date"])

    op.execute(
        "COMMENT ON TABLE kpi_node_snapshot_daily IS "
        "'节点级日性能评估快照（按 loop_count 加权聚合当天小时快照，对齐 GB/T 44693.2-2024 §6.4）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_node_snapshot_daily.stat_date IS '统计日期（DATE）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_node_snapshot_daily.realtime_auto_rate IS "
        "'实时自控率（%）：取当天最后一次小时快照的值（非聚合）'"
    )

    # 2. 新建 kpi_node_snapshot_monthly 表（月级聚合快照）
    op.create_table(
        "kpi_node_snapshot_monthly",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("plant_node_id", UUID(as_uuid=False), nullable=False),
        sa.Column("stat_month", sa.Date(), nullable=False),
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
        sa.Column("realtime_auto_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("loop_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("algorithm_version", sa.String(30), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["plant_node_id"], ["plant_node.id"], ondelete="CASCADE",
                                name="fk_kpi_node_snapshot_monthly_node"),
        sa.CheckConstraint(
            "status IN ('EXCELLENT','GOOD','FAIR','WARNING','POOR','INCONCLUSIVE')",
            name="ck_kpi_node_snapshot_monthly_status",
        ),
        sa.UniqueConstraint("plant_node_id", "stat_month",
                            name="uk_kpi_node_snapshot_monthly_node_month"),
    )

    op.create_index("idx_kpi_node_snapshot_monthly_node_id", "kpi_node_snapshot_monthly",
                    ["plant_node_id"])
    op.create_index("idx_kpi_node_snapshot_monthly_stat_month", "kpi_node_snapshot_monthly",
                    ["stat_month"])
    op.create_index("idx_kpi_node_snapshot_monthly_status", "kpi_node_snapshot_monthly",
                    ["status"])
    op.create_index("idx_kpi_node_snapshot_monthly_node_month", "kpi_node_snapshot_monthly",
                    ["plant_node_id", "stat_month"])

    op.execute(
        "COMMENT ON TABLE kpi_node_snapshot_monthly IS "
        "'节点级月性能评估快照（按 loop_count 加权聚合当月日快照，对齐 GB/T 44693.2-2024 §6.4）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_node_snapshot_monthly.stat_month IS '统计月份（DATE，月初）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_node_snapshot_monthly.realtime_auto_rate IS "
        "'实时自控率（%）：取当月最后一次小时快照的值（非聚合）'"
    )


def downgrade() -> None:
    op.drop_table("kpi_node_snapshot_monthly")
    op.drop_table("kpi_node_snapshot_daily")
