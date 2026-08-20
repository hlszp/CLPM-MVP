"""plant_node 表结构优化（2026-08-20 评估）

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-08-20

变更内容（工厂模型表结构评估结论，4 项）：
1. 删除死字段：monitor_tag_id / monitor_trigger_value（SVC-10 位号触发
   监控从未接线，字段全 NULL，函数无调用方）
2. 加同父重名唯一约束：uq_plant_node_parent_name（表达式索引，
   COALESCE 归一化根节点 parent_id，防同步/导入/手工三入口造出同层重名）
3. 加 sort_order 排序字段（同级展示顺序：sort_order → name）
4. 加 updated_by 操作人字段（表内可见最后操作人，免翻审计日志）
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 删除死字段（SVC-10 位号触发监控，从未接线）
    op.drop_index("idx_plant_node_monitor_tag_id", table_name="plant_node")
    op.drop_column("plant_node", "monitor_tag_id")
    op.drop_column("plant_node", "monitor_trigger_value")

    # 3. 加 sort_order 排序字段
    op.add_column(
        "plant_node",
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="同级展示排序（小值在前，同值按名称）",
        ),
    )

    # 4. 加 updated_by 操作人字段
    op.add_column(
        "plant_node",
        sa.Column(
            "updated_by",
            sa.String(length=100),
            nullable=True,
            comment="最后操作人（用户名 / aas:sync / import:用户名）",
        ),
    )

    # 2. 同父重名唯一约束（表达式索引：根节点 parent_id 归一化处理）
    op.create_index(
        "uq_plant_node_parent_name",
        "plant_node",
        [
            sa.text("COALESCE(parent_id::text, '00000000-0000-0000-0000-000000000000')"),
            sa.text("name"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_plant_node_parent_name", table_name="plant_node")
    op.drop_column("plant_node", "updated_by")
    op.drop_column("plant_node", "sort_order")
    op.add_column(
        "plant_node",
        sa.Column(
            "monitor_trigger_value",
            sa.VARCHAR(length=20),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.add_column(
        "plant_node",
        postgresql.UUID(as_uuid=False),
        autoincrement=False,
        nullable=True,
    )
    op.create_index("idx_plant_node_monitor_tag_id", "plant_node", ["monitor_tag_id"])
