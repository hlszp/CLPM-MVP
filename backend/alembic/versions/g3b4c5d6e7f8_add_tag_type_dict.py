"""TAG_TYPE 参数类型字典化（2026-08-20）

Revision ID: g3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-20

变更内容：
1. 种子数据：TAG_TYPE 8 项（PV 测量值/SP 设定值/OP 操作值/MODE 模式/
   PID_P 比例/PID_I 积分/PID_D 微分/OTHER 其他）
2. 删除 tag_registry.tag_type 的 CHECK 约束
   （合法性改由字典校验，支持用户自定义参数类型）

与处置模块迁移 a5b6c7d8e9f0 并行分叉（同为 f2a3b4c5d6e7 的子级），
由 b6c7d8e9f0a1 merge 收敛。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# TAG_TYPE 种子（code 与存量数据一致；label=中文别名，导入支持中英文）
_TAG_TYPE_SEED = [
    ("PV", "测量值", 1),
    ("SP", "设定值", 2),
    ("OP", "操作值", 3),
    ("MODE", "模式", 4),
    ("PID_P", "比例（P）", 5),
    ("PID_I", "积分（I）", 6),
    ("PID_D", "微分（D）", 7),
    ("OTHER", "其他", 8),
]


def upgrade() -> None:
    # 1. 种子数据：TAG_TYPE 8 项（id 续接 MEASURE_TYPE 种子的 1~7）
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
                "dict_type": "TAG_TYPE",
                "item_code": code,
                "item_label": label,
                "sort_order": sort,
                "is_enabled": True,
                "updated_by": "seed",
            }
            for i, (code, label, sort) in enumerate(_TAG_TYPE_SEED, start=8)
        ],
    )

    # 2. 删除 tag_registry.tag_type CHECK 约束（合法性改由字典校验）
    op.drop_constraint("ck_tag_registry_type", "tag_registry", type_="check")


def downgrade() -> None:
    # 恢复 CHECK 约束（自定义类型数据需先清理，否则约束创建失败）
    op.create_check_constraint(
        "ck_tag_registry_type",
        "tag_registry",
        "tag_type IN ('PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D', 'OTHER')",
    )
    op.execute("DELETE FROM sys_dict_item WHERE dict_type = 'TAG_TYPE'")
