"""add fault diagnosis metrics to kpi_snapshot_hourly

kpi_snapshot_hourly 扩展 3 个故障诊断指标字段（nullable，向后兼容）：
- stiction_coeff: 黏滞系数（0-100，0=无黏滞）
- steady_state_time: 稳态时间（秒）
- output_travel_index: 输出值行程指数（0-100）

注：kpi_snapshot_hourly 为诊断中心与性能评估共享表，新增字段均为 nullable，
不影响现有性能评估逻辑。

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-06-24 18:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "h9c0d1e2f3a4"
down_revision = "g8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("stiction_coeff", sa.Numeric(5, 2), nullable=True,
                  comment="黏滞系数（0-100，0=无黏滞）"),
    )
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("steady_state_time", sa.Numeric(8, 2), nullable=True,
                  comment="稳态时间（秒）：PV 与 SP 偏差在 ±2% 内的时长"),
    )
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("output_travel_index", sa.Numeric(8, 2), nullable=True,
                  comment="输出值行程指数（0-100）：OP 总行程归一化指数"),
    )


def downgrade() -> None:
    op.drop_column("kpi_snapshot_hourly", "output_travel_index")
    op.drop_column("kpi_snapshot_hourly", "steady_state_time")
    op.drop_column("kpi_snapshot_hourly", "stiction_coeff")
