"""kpi_node_snapshot_hourly.assessment_stats JSONB（P3-08d）

10§3.6.2/§3.6.3：节点级固定参评名册与完整 P 可评分统计落库位置定稿
（08a 遗留④同批裁决：calculation_result_record 不另加
assessmentProfile/releaseGeneration 列——六要素经 record payload
assessmentContext + manifest source 已可查，账本行为不可变事实源；
如后续查询性能需要再立迁移卡）。

旧行 NULL=历史口径不可追溯（不猜算，10§3.6.2）；同值另随 NODE record
payload assessment_stats 键入账本。

Revision ID: p3d608ds01
Revises: p302task01
Create Date: 2026-10-12

"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "p3d608ds01"
down_revision = "p302task01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kpi_node_snapshot_hourly",
        sa.Column("assessment_stats", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kpi_node_snapshot_hourly", "assessment_stats")
