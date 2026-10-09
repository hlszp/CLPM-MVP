"""create loop_health_flag

Revision ID: d0724685907c
Revises: b275ef85dc40
Create Date: 2026-10-10

回路数据健康标记表（2026-10-10 运维圈选功能）：
承载 SP_FOLLOWS_PV 等"数据/组态形态异常"标记，由每日判定任务全量重建，
供回路配置页筛选圈选后批量处置。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "d0724685907c"
down_revision = "b275ef85dc40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loop_health_flag",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("loop_id", sa.String(length=36), nullable=False),
        sa.Column(
            "flag_type",
            sa.String(length=32),
            nullable=False,
            comment="标记类型：SP_FOLLOWS_PV=自动时段 SP 跟随 PV 嫌疑",
        ),
        sa.Column("evidence", JSONB(), nullable=True, comment="判定证据（比值/样本数/窗口等）"),
        sa.Column(
            "suspected_cascade",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="疑似未登记串级（当前 Cascade 模式，随动属正常，默认不进圈选）",
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            comment="本次判定时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("loop_id", "flag_type", name="uniq_lhf_loop_flag"),
    )
    op.create_index("idx_lhf_flag_type", "loop_health_flag", ["flag_type"])


def downgrade() -> None:
    op.drop_index("idx_lhf_flag_type", table_name="loop_health_flag")
    op.drop_table("loop_health_flag")
