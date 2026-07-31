"""V62-P3-007 tuning_record 新增人工实施清单字段.

revision: p3d4e5f6g7h8
down_revision: p3c3d4e5f6g7

新增三个可空 JSON 列，承载人工实施清单：
- ``current_pid``: 当前 PID 值快照（整定建议生成时的 DCS 当前值）
- ``risk_assessment``: 风险评估（risk_level/factors/description）
- ``rollback_pid``: 回退 PID 值（实施失败时恢复；通常 = current_pid）

三列均为 nullable，向后兼容存量记录。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "p3d4e5f6g7h8"
down_revision = "p3c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tuning_record",
        sa.Column("current_pid", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "tuning_record",
        sa.Column("risk_assessment", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "tuning_record",
        sa.Column("rollback_pid", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tuning_record", "rollback_pid")
    op.drop_column("tuning_record", "risk_assessment")
    op.drop_column("tuning_record", "current_pid")
