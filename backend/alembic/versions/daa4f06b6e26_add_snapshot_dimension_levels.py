"""add assess/diagnose/tune level columns to kpi snapshots

三性分离（2026-10-03 R5 裁决，docs/设计文档/评估性能总览页改版-方案-2026-10-03.md §5）：
kpi_snapshot_hourly / kpi_snapshot_custom 各加 assess_level / diagnose_level /
tune_level 三列（String(2)，L0~L4 语义同 fitness_level，L4=开放）。
默认映射下三维与现行三模块消费行为等价；旧快照 NULL 由读取方回退 fitness_level。

Revision ID: daa4f06b6e26
Revises: dropint001
Create Date: 2026-10-03

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "daa4f06b6e26"
down_revision = "dropint001"
branch_labels = None
depends_on = None

_COLUMNS = ("assess_level", "diagnose_level", "tune_level")
_TABLES = ("kpi_snapshot_hourly", "kpi_snapshot_custom")


def upgrade() -> None:
    for table in _TABLES:
        for col in _COLUMNS:
            op.add_column(
                table,
                sa.Column(
                    col, sa.String(2), nullable=True, comment=f"三性分离等级（{col}，L0~L4）"
                ),
            )


def downgrade() -> None:
    for table in _TABLES:
        for col in _COLUMNS:
            op.drop_column(table, col)
