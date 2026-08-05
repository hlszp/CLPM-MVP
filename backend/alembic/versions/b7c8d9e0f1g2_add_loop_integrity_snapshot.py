"""add loop_integrity_snapshot table

新增回路数据完整性每日巡检快照表（每回路每天一条，随每日 02:00
巡检任务 UPSERT 覆盖），供回路监控列表/测点配置页快速展示 PV 完整度，
无需列表页实时查 TDengine（27 回路 × 7 列 COUNT 需 ~3s，列表不可接受）。

设计依据：data-quality-enhancement-plan-2026-08-05.md §2.2 方案 A
关联模型：app.models.metric.LoopIntegritySnapshot
关联任务：app.tasks.data_integrity_check.run_daily_integrity_check

Revision ID: b7c8d9e0f1g2
Revises: y5d6e7f8a9b0
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c8d9e0f1g2"
down_revision: str | None = "y5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 条件创建：兼容集成测试 checkfirst 已建场景
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "loop_integrity_snapshot" in inspector.get_table_names():
        # 已存在则补建索引（idempotent）
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("loop_integrity_snapshot")}
        if "idx_loop_integrity_check_date" not in existing_indexes:
            op.create_index(
                "idx_loop_integrity_check_date",
                "loop_integrity_snapshot",
                ["check_date"],
            )
        if "idx_loop_integrity_loop_id" not in existing_indexes:
            op.create_index(
                "idx_loop_integrity_loop_id",
                "loop_integrity_snapshot",
                ["loop_id"],
            )
        return

    op.create_table(
        "loop_integrity_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "check_date",
            sa.DateTime(),
            nullable=False,
            comment="巡检日期（Asia/Shanghai naive date）",
        ),
        sa.Column("ts_start", sa.DateTime(), nullable=False, comment="巡检时间窗口起（naive UTC）"),
        sa.Column("ts_end", sa.DateTime(), nullable=False, comment="巡检时间窗口止（naive UTC）"),
        sa.Column("overall_completeness", sa.Float(), nullable=True, comment="整体完整度 0.0~1.0"),
        sa.Column(
            "pv_completeness",
            sa.Float(),
            nullable=True,
            comment="PV 列完整度 0.0~1.0（<0.95 告警）",
        ),
        sa.Column("op_completeness", sa.Float(), nullable=True, comment="OP 列完整度 0.0~1.0"),
        sa.Column(
            "col_details",
            postgresql.JSONB(),
            nullable=True,
            comment="列级明细 JSONB: {pv:{completeness,count,expected},...}",
        ),
        sa.Column("missing_columns", postgresql.JSONB(), nullable=True, comment="缺失列列表"),
        sa.Column(
            "status", sa.String(20), nullable=False, comment="OK/WARNING/CRITICAL/DATA_UNAVAILABLE"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.timezone("UTC", sa.func.now()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('OK', 'WARNING', 'CRITICAL', 'DATA_UNAVAILABLE')",
            name="ck_loop_integrity_status",
        ),
        sa.UniqueConstraint("loop_id", "check_date", name="uq_loop_integrity_loop_date"),
        comment="回路数据完整性每日巡检快照（每回路每天一条）",
    )
    op.create_index("idx_loop_integrity_check_date", "loop_integrity_snapshot", ["check_date"])
    op.create_index("idx_loop_integrity_loop_id", "loop_integrity_snapshot", ["loop_id"])


def downgrade() -> None:
    op.drop_index("idx_loop_integrity_loop_id", table_name="loop_integrity_snapshot")
    op.drop_index("idx_loop_integrity_check_date", table_name="loop_integrity_snapshot")
    op.drop_table("loop_integrity_snapshot")
