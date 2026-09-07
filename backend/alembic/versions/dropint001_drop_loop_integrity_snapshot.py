"""drop loop_integrity_snapshot table

数据检查（数据完整性检查）模块整体下线（2026-09-07 决策）：
- 数据入库为 COV（变化驱动）方式，强制秒级入库会显著放大数据存储量；
- 删除每日巡检快照表 ``loop_integrity_snapshot``（唯一写入方为
  ``app.tasks.data_integrity_check``，随服务一并移除）；
- 回路监控列表 / 测点配置页 / 数据质量报告的 PV 完整度展示同步移除，
  数据健康度收敛为 可信度 + 预处理有效率 双指标。

关联模型：app.models.metric.LoopIntegritySnapshot（已删）
关联任务：app.tasks.data_integrity_check（已删）

Revision ID: dropint001
Revises: r1p0int00001
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dropint001"
down_revision: str | None = "r1p0int00001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """升级：删除 loop_integrity_snapshot 表（含索引，drop_table 自动级联索引）。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "loop_integrity_snapshot" not in inspector.get_table_names():
        return
    op.drop_table("loop_integrity_snapshot")


def downgrade() -> None:
    """降级：恢复 loop_integrity_snapshot 表结构（数据不回填，仅结构回滚）。"""
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
        sa.Column("col_details", postgresql.JSONB(), nullable=True, comment="列级明细 JSONB"),
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
