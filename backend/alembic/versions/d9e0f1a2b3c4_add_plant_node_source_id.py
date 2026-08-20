"""plant_node 增加 AAS 同步来源标记列

Revision ID: b8c9d0e1f2a3
Revises: a9b8c7d6e5f4
Create Date: 2026-08-20

工厂配置页 AAS 工厂模型同步：source_node_id 记录 AAS AreaNode Id，
有值表示 AAS 同步节点（本地改名会被下次同步覆盖，主数据语义），
NULL 表示本地独立维护节点（不受同步影响）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: str | Sequence[str] | None = "a9b8c7d6e5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plant_node",
        sa.Column(
            "source_node_id",
            sa.BigInteger(),
            nullable=True,
            comment="AAS AreaNode Id 同步来源标记（有值=AAS 同步节点，本地改名会被下次同步覆盖）",
        ),
    )
    op.create_index(
        "idx_plant_node_source_node_id",
        "plant_node",
        ["source_node_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_plant_node_source_node_id", table_name="plant_node")
    op.drop_column("plant_node", "source_node_id")
