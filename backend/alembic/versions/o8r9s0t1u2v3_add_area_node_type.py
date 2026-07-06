"""add AREA node type to plant_node

Revision ID: o8r9s0t1u2v3
Revises: p9r0s1t2u3v4
Create Date: 2026-07-06 14:20:00.000000

扩展 plant_node.type CHECK 约束，新增 AREA（装置/车间）类型，
支持 FACTORY → AREA → UNIT 三层工厂结构。
回路挂在 UNIT 节点下。
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "o8r9s0t1u2v3"
down_revision = "p9r0s1t2u3v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """扩展 CHECK 约束：新增 AREA 类型，支持三层工厂结构。

    保留原有 EQUIPMENT 类型，兼容已有数据。
    """
    op.execute("ALTER TABLE plant_node DROP CONSTRAINT IF EXISTS ck_plant_node_type")
    op.execute(
        "ALTER TABLE plant_node ADD CONSTRAINT ck_plant_node_type "
        "CHECK (type IN ('FACTORY', 'AREA', 'UNIT', 'EQUIPMENT'))"
    )


def downgrade() -> None:
    """回滚：恢复原 CHECK 约束（不含 AREA）。

    注意：若数据库中已存在 AREA 类型节点，回滚会失败。
    需先删除或转换 AREA 节点为 UNIT 类型。
    """
    op.execute("ALTER TABLE plant_node DROP CONSTRAINT IF EXISTS ck_plant_node_type")
    op.execute(
        "ALTER TABLE plant_node ADD CONSTRAINT ck_plant_node_type "
        "CHECK (type IN ('FACTORY', 'UNIT', 'EQUIPMENT'))"
    )
