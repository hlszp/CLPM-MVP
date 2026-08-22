"""sys_dict_item 通用字典表（测点类型可配置，2026-08-20）

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-20

变更内容：
1. 新建 sys_dict_item 表（dict_type + item_code 唯一约束）
2. 种子数据：MEASURE_TYPE 7 项（替代 tag service 硬编码枚举）
3. 删除 tag_registry.measure_type 的 CHECK 约束
   （合法性改由字典校验，支持用户自定义类型）
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# MEASURE_TYPE 种子（code=英文枚举与存量数据一致，label=中文别名）
_MEASURE_TYPE_SEED = [
    ("TEMPERATURE", "温度", 1),
    ("PRESSURE", "压力", 2),
    ("LEVEL", "液位", 3),
    ("FLOW", "流量", 4),
    ("ANALYSIS", "分析", 5),
    ("SPEED", "速度", 6),
    ("OTHER", "其他", 7),
]


def upgrade() -> None:
    # 1. 建 sys_dict_item 表
    op.create_table(
        "sys_dict_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dict_type", sa.String(50), nullable=False),
        sa.Column("item_code", sa.String(50), nullable=False),
        sa.Column("item_label", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("dict_type", "item_code", name="uk_sys_dict_item_type_code"),
        comment="通用字典项（可配置枚举）",
    )
    op.create_index("ix_sys_dict_item_dict_type", "sys_dict_item", ["dict_type"])

    # 2. 种子数据：MEASURE_TYPE 7 项
    op.bulk_insert(
        sa.table(
            "sys_dict_item",
            sa.column("id", sa.String),
            sa.column("dict_type", sa.String),
            sa.column("item_code", sa.String),
            sa.column("item_label", sa.String),
            sa.column("sort_order", sa.Integer),
            sa.column("is_enabled", sa.Boolean),
            sa.column("updated_by", sa.String),
        ),
        [
            {
                "id": f"00000000-0000-0000-0000-{i:012d}",
                "dict_type": "MEASURE_TYPE",
                "item_code": code,
                "item_label": label,
                "sort_order": sort,
                "is_enabled": True,
                "updated_by": "seed",
            }
            for i, (code, label, sort) in enumerate(_MEASURE_TYPE_SEED, start=1)
        ],
    )

    # 3. 删除 tag_registry.measure_type CHECK 约束（合法性改由字典校验）
    op.drop_constraint("ck_tag_registry_measure_type", "tag_registry", type_="check")


def downgrade() -> None:
    # 恢复 CHECK 约束（自定义类型数据需先清理，否则约束创建失败）
    op.create_check_constraint(
        "ck_tag_registry_measure_type",
        "tag_registry",
        "measure_type IS NULL OR measure_type IN "
        "('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', 'ANALYSIS', 'SPEED', 'OTHER')",
    )
    op.drop_index("ix_sys_dict_item_dict_type", table_name="sys_dict_item")
    op.drop_table("sys_dict_item")
