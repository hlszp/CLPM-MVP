"""add config_override + config_publication (P2-02 分层配置发布)

Revision ID: p202cfgpub01
Revises: p102wsnull01
Create Date: 2026-10-12

P2-02 分层配置发布与跨进程一致（方案 01 §3.2 统一作用域）：

1. 新表 ``config_override``：TEMPLATE / NODE / LOOP 三层覆盖
   （DEFAULT 层复用 algorithm_parameter + metric_config.threshold 兼容读取，
   TASK 层为内存快照不落库——迁移诊断归层：metric_config.threshold 归入
   全局层且保持其在旧三层链中的内部次序，不静默改变旧优先级）。
2. 新表 ``config_publication``：统一发布快照（全局单调 revision 唯一 +
   operation/scope/reason/operator/before/after/affected_loops/
   rollback_revision）。revision 为持久版本号（Redis 仅加速通知）；
   并发发布靠 revision 唯一约束实现 expectedRevision 乐观锁。
3. 无存量数据回填：revision 0 = 迁移前遗留配置状态（LEGACY，发布账本
   从 1 起编）；不伪补历史发布记录。

downgrade：两张均为新增表，直接 DROP；发布历史随表删除，生产回退按
方案 §6 走前向修复（新 revision 回滚），不盲 downgrade。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p202cfgpub01"
down_revision: str | None = "p102wsnull01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. 分层配置覆盖表（TEMPLATE / NODE / LOOP）
    # ------------------------------------------------------------------
    op.create_table(
        "config_override",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("scope_id", sa.String(64), nullable=False),
        sa.Column("metric_code", sa.String(50), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("published_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "layer IN ('TEMPLATE', 'NODE', 'LOOP')",
            name="ck_config_override_layer",
        ),
        sa.UniqueConstraint("layer", "scope_id", "metric_code", name="uq_config_override_scope"),
    )
    op.create_index("ix_config_override_metric", "config_override", ["metric_code"])
    op.create_index("ix_config_override_scope", "config_override", ["layer", "scope_id"])

    # ------------------------------------------------------------------
    # 2. 统一发布快照表（追加式发布账本）
    # ------------------------------------------------------------------
    op.create_table(
        "config_publication",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("layer", sa.String(16), nullable=True),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("operator", sa.String(50), nullable=False),
        sa.Column("before_value", sa.Text(), nullable=True),
        sa.Column("after_value", sa.Text(), nullable=True),
        sa.Column("affected_loops", sa.Integer(), nullable=True),
        sa.Column("rollback_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "operation IN ('PUBLISH', 'RESET', 'ROLLBACK', 'LEGACY_SYNC')",
            name="ck_config_publication_operation",
        ),
        sa.UniqueConstraint("revision", name="uq_config_publication_revision"),
    )
    op.create_index("ix_config_publication_created_at", "config_publication", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_config_publication_created_at", table_name="config_publication")
    op.drop_table("config_publication")
    op.drop_index("ix_config_override_scope", table_name="config_override")
    op.drop_index("ix_config_override_metric", table_name="config_override")
    op.drop_table("config_override")
