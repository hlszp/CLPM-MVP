"""LOOP_TYPE 回路类型字典化（2026-08-20）

Revision ID: h4c5d6e7f8a9
Revises: g3b4c5d6e7f8
Create Date: 2026-08-20

变更内容：
1. 种子数据：LOOP_TYPE 7 项（温度/压力/液位/流量/分析/速度/其他）
2. 删除 loop_ledger.loop_type 的 CHECK 约束 ck_loop_ledger_loop_type
   （合法性改由字典校验，支持用户自定义回路类型如：电流/转速）

背景：回路导入 Excel 填中文自定义回路类型（电流/转速）时，
原 CHECK 约束拒绝落库（CheckViolationError）。字典化后用户可在
「系统管理 → 字典管理 → 回路类型」添加自定义项，导入归一化即时支持。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "g3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# LOOP_TYPE 种子（code 与存量数据一致；label=中文别名，导入支持中英文）
_LOOP_TYPE_SEED = [
    ("TEMPERATURE", "温度", 1),
    ("PRESSURE", "压力", 2),
    ("LEVEL", "液位", 3),
    ("FLOW", "流量", 4),
    ("ANALYSIS", "分析", 5),
    ("SPEED", "速度", 6),
    ("OTHER", "其他", 7),
]


def upgrade() -> None:
    # 1. 种子数据：LOOP_TYPE 7 项（id 续接 MEASURE_TYPE 1~7 / TAG_TYPE 8~15）
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
                "dict_type": "LOOP_TYPE",
                "item_code": code,
                "item_label": label,
                "sort_order": sort,
                "is_enabled": True,
                "updated_by": "seed",
            }
            for i, (code, label, sort) in enumerate(_LOOP_TYPE_SEED, start=16)
        ],
    )

    # 2. 删除 loop_ledger.loop_type CHECK 约束（合法性改由字典校验）
    op.drop_constraint("ck_loop_ledger_loop_type", "loop_ledger", type_="check")


def downgrade() -> None:
    # 恢复 CHECK 约束（自定义类型数据需先清理，否则约束创建失败）
    op.create_check_constraint(
        "ck_loop_ledger_loop_type",
        "loop_ledger",
        "loop_type IN ('TEMPERATURE', 'PRESSURE', 'LEVEL', 'FLOW', 'ANALYSIS', 'SPEED', 'OTHER')",
    )
    op.execute("DELETE FROM sys_dict_item WHERE dict_type = 'LOOP_TYPE'")
