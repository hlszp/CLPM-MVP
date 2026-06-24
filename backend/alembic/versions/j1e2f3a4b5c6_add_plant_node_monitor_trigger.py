"""add monitor trigger fields to plant_node

plant_node 扩展 2 个位号触发监控字段（nullable，向后兼容）：
- monitor_tag_id: 位号触发监控的位号 ID（FK→tag_registry）
- monitor_trigger_value: 触发监控的位号值（如 "true"/"1"/"ON"）

用于 SVC-10 位号触发监控检查：当 plant_node.monitor_tag_id 配置后，
查询 TDengine 最新值，等于 monitor_trigger_value 时该节点下回路应监控。

Revision ID: j1e2f3a4b5c6
Revises: i0d1e2f3a4b5
Create Date: 2026-06-24 20:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "j1e2f3a4b5c6"
down_revision = "i0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plant_node",
        sa.Column(
            "monitor_tag_id",
            UUID(as_uuid=False),
            sa.ForeignKey("tag_registry.id", ondelete="RESTRICT"),
            nullable=True,
            comment="位号触发监控的位号 ID（NULL 表示默认监控）",
        ),
    )
    op.add_column(
        "plant_node",
        sa.Column(
            "monitor_trigger_value",
            sa.String(20),
            nullable=True,
            comment='触发监控的位号值（如 "true"/"1"/"ON"）',
        ),
    )
    op.create_index(
        "idx_plant_node_monitor_tag_id", "plant_node", ["monitor_tag_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_plant_node_monitor_tag_id", table_name="plant_node")
    op.drop_column("plant_node", "monitor_trigger_value")
    op.drop_column("plant_node", "monitor_tag_id")
