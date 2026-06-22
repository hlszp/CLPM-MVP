"""add loop fields and sys_config table

新增 loop_ledger.score_weights / remark / updated_by 列；
新增 sys_config 表（key-value 系统配置存储）。

Revision ID: a1b2c3d4e5f6
Revises: 772edf67d12d
Create Date: 2026-06-22 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "772edf67d12d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. loop_ledger 新增列
    op.add_column(
        "loop_ledger",
        sa.Column("score_weights", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "loop_ledger",
        sa.Column("remark", sa.String(500), nullable=True),
    )
    op.add_column(
        "loop_ledger",
        sa.Column("updated_by", sa.String(50), nullable=True),
    )

    # 2. 新增 sys_config 表
    op.create_table(
        "sys_config",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("idx_sys_config_key", "sys_config", ["key"], unique=True)

    # 3. 写入 AAS 默认配置
    op.execute(
        "INSERT INTO sys_config (key, value, description, updated_by, updated_at) VALUES "
        "('aas.endpoint', 'opc.tcp://localhost:4840', 'AAS OPC UA 端点', 'system', NOW()), "
        "('aas.sync_interval_seconds', '300', 'AAS 同步周期（秒）', 'system', NOW()), "
        "('aas.sync_enabled', 'true', 'AAS 同步启停状态', 'system', NOW()), "
        "('aas.security_mode', 'None', 'AAS 安全模式', 'system', NOW()) "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_sys_config_key", table_name="sys_config")
    op.drop_table("sys_config")
    op.drop_column("loop_ledger", "updated_by")
    op.drop_column("loop_ledger", "remark")
    op.drop_column("loop_ledger", "score_weights")
