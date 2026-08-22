"""merge heads: handling v2.0 dual entity + tag type dict

合并并行分叉（2026-08-20，两分支同挂 f2a3b4c5d6e7）：
- a5b6c7d8e9f0 处置模块 v2.0 双实体（handling_order + loop_action_item 改造）
- g3b4c5d6e7f8 tag 类型字典（并行会话）

纯 merge 迁移（无 DDL），参照 v6p1merge001 惯例。

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0, g3b4c5d6e7f8
Create Date: 2026-08-20 17:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = ("a5b6c7d8e9f0", "g3b4c5d6e7f8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge branch heads (no-op)."""
    pass


def downgrade() -> None:
    """Downgrade merge (no-op; branch heads remain)."""
    pass
