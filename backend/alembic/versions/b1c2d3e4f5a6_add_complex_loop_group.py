"""add complex_loop_group_id and complex_role to loop_ledger

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-07-24

P4 复杂回路聚合 RFC S2（决策点 1 方案 A）：loop_ledger 新增两个字段，
将同属一个物理复杂控制回路（串级/超驰/NooM）的多行 loop_ledger 分组。

- complex_loop_group_id (UUID, NULL)：同 ID 回路归为一组，NULL=普通单回路
- complex_role (VARCHAR(10), NULL)：MAIN(主回路,聚合代表) / SUB(副回路)，NULL=普通单回路

约束：
- ck_loop_ledger_complex_role：complex_role 取值校验（NULL 或 MAIN/SUB）
- ck_loop_ledger_complex_group_coherence：分组 ID 与角色须同时 NULL 或同时非 NULL
- idx_loop_ledger_complex_group：按分组 ID 查询同组回路（聚合去重用）

不初始化种子数据：现有回路均为普通单回路，两字段默认 NULL，零行为变更。
节点聚合去重逻辑（S3 _dedup_complex_groups）在应用层实现，本迁移仅提供 schema 基础。

设计依据：docs/过程文档/complex-loop-aggregation-rfc-2026-07-24.md §三 决策点 1
关联代码：app/models/loop.py（LoopLedger.complex_loop_group_id / complex_role）
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增复杂回路分组字段 + 约束 + 索引。"""
    op.add_column(
        "loop_ledger",
        sa.Column(
            "complex_loop_group_id",
            UUID(as_uuid=False),
            nullable=True,
            comment="复杂回路分组 ID；同 ID 回路归为一个物理控制回路，NULL=普通单回路",
        ),
    )
    op.add_column(
        "loop_ledger",
        sa.Column(
            "complex_role",
            sa.String(10),
            nullable=True,
            comment="复杂回路角色：MAIN(主回路,聚合代表) / SUB(副回路)；NULL=普通单回路",
        ),
    )
    op.create_check_constraint(
        "ck_loop_ledger_complex_role",
        "loop_ledger",
        "complex_role IS NULL OR complex_role IN ('MAIN', 'SUB')",
    )
    op.create_check_constraint(
        "ck_loop_ledger_complex_group_coherence",
        "loop_ledger",
        "(complex_loop_group_id IS NULL AND complex_role IS NULL) "
        "OR (complex_loop_group_id IS NOT NULL AND complex_role IS NOT NULL)",
    )
    op.create_index(
        "idx_loop_ledger_complex_group",
        "loop_ledger",
        ["complex_loop_group_id"],
        unique=False,
    )


def downgrade() -> None:
    """回滚复杂回路分组字段 + 约束 + 索引。"""
    op.drop_index("idx_loop_ledger_complex_group", table_name="loop_ledger")
    op.drop_constraint("ck_loop_ledger_complex_group_coherence", "loop_ledger", type_="check")
    op.drop_constraint("ck_loop_ledger_complex_role", "loop_ledger", type_="check")
    op.drop_column("loop_ledger", "complex_role")
    op.drop_column("loop_ledger", "complex_loop_group_id")
