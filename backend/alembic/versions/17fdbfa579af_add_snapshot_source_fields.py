"""add source/source_task_id to kpi snapshots (评估记录来源标注)

评估模块整合方案 B1（2026-10-03 用户裁决）：
- kpi_snapshot_hourly / kpi_snapshot_custom 各加 ``source``（SCHEDULED /
  MANUAL_STANDARD / MANUAL_CUSTOM / BACKFILL）与 ``source_task_id``（溯源任务行）。
- 存量回填（用户裁决）：hourly 统一标 ``SCHEDULED``（定时调度，诚实近似）；
  custom 全部为手动任务产出 → ``MANUAL_CUSTOM`` 且 ``source_task_id = task_id``。

Revision ID: 17fdbfa579af
Revises: daa4f06b6e26
Create Date: 2026-10-03

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "17fdbfa579af"
down_revision = "daa4f06b6e26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("kpi_snapshot_hourly", "kpi_snapshot_custom"):
        op.add_column(table, sa.Column("source", sa.String(16), nullable=True, comment="评估来源"))
        op.add_column(
            table,
            sa.Column(
                "source_task_id",
                sa.String(36),
                nullable=True,
                comment="来源任务 ID（手动触发时溯源）",
            ),
        )
    # 存量回填（用户裁决 2026-10-03）
    op.execute("UPDATE kpi_snapshot_hourly SET source = 'SCHEDULED' WHERE source IS NULL")
    op.execute(
        "UPDATE kpi_snapshot_custom SET source = 'MANUAL_CUSTOM', "
        "source_task_id = task_id WHERE source IS NULL"
    )


def downgrade() -> None:
    for table in ("kpi_snapshot_hourly", "kpi_snapshot_custom"):
        op.drop_column(table, "source_task_id")
        op.drop_column(table, "source")
