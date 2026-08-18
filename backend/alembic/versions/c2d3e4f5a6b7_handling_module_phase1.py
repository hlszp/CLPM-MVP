"""handling module phase1: extend loop_action_item lifecycle

处置模块 Phase 1（docs/MVP设计/08-处置模块设计方案.md §3）：
- 加列：action_type / action_detail / handled_by / handled_at / submitted_at /
  verify_run_id / verify_result / verify_note / verified_by / verified_at /
  kpi_before / kpi_after / tuning_record_id / ignore_reason
- status CheckConstraint 放宽：PENDING → 六态（PENDING/HANDLING/VERIFYING/CLOSED/REOPENED/IGNORED）
- 新增约束：action_type 8 类、verify_result EFFECTIVE/INEFFECTIVE
- 新增索引：idx_loop_action_item_status (status, updated_at DESC)
- 表注释更新（处置模块 Phase 1 全生命周期）

Revision ID: c2d3e4f5a6b7
Revises: b1ad2fabdea4
Create Date: 2026-08-18 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1ad2fabdea4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. 新增 14 列（全部 nullable，存量 PENDING 行无需回填）
    op.add_column("loop_action_item", sa.Column("action_type", sa.String(length=16), nullable=True))
    op.add_column(
        "loop_action_item",
        sa.Column("action_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("loop_action_item", sa.Column("handled_by", sa.String(length=64), nullable=True))
    op.add_column("loop_action_item", sa.Column("handled_at", sa.DateTime(), nullable=True))
    op.add_column("loop_action_item", sa.Column("submitted_at", sa.DateTime(), nullable=True))
    op.add_column(
        "loop_action_item",
        sa.Column("verify_run_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "loop_action_item", sa.Column("verify_result", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "loop_action_item", sa.Column("verify_note", sa.String(length=500), nullable=True)
    )
    op.add_column("loop_action_item", sa.Column("verified_by", sa.String(length=64), nullable=True))
    op.add_column("loop_action_item", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.add_column(
        "loop_action_item",
        sa.Column("kpi_before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "loop_action_item",
        sa.Column("kpi_after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "loop_action_item",
        sa.Column("tuning_record_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "loop_action_item", sa.Column("ignore_reason", sa.String(length=200), nullable=True)
    )

    # 2. verify_run_id 外键（复诊记录删除后置空）
    op.create_foreign_key(
        "fk_loop_action_item_verify_run",
        "loop_action_item",
        "diagnosis_run",
        ["verify_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. status CheckConstraint 放宽为六态（drop + recreate）
    op.drop_constraint("ck_loop_action_item_status", "loop_action_item", type_="check")
    op.create_check_constraint(
        "ck_loop_action_item_status",
        "loop_action_item",
        "status IN ('PENDING', 'HANDLING', 'VERIFYING', 'CLOSED', 'REOPENED', 'IGNORED')",
    )

    # 4. 新增枚举约束
    op.create_check_constraint(
        "ck_loop_action_item_action_type",
        "loop_action_item",
        "action_type IS NULL OR action_type IN "
        "('TUNING', 'VALVE', 'INSTRUMENT', 'LINK', 'PROCESS', "
        "'UTILIZATION', 'RECONFIG', 'OTHER')",
    )
    op.create_check_constraint(
        "ck_loop_action_item_verify_result",
        "loop_action_item",
        "verify_result IS NULL OR verify_result IN ('EFFECTIVE', 'INEFFECTIVE')",
    )

    # 5. 处置清单主查询索引（状态 + 最近更新排序）
    op.create_index(
        "idx_loop_action_item_status",
        "loop_action_item",
        ["status", sa.text("updated_at DESC")],
        unique=False,
    )

    # 6. 表注释更新（处置模块 Phase 1 全生命周期）
    op.execute(
        "COMMENT ON TABLE loop_action_item IS "
        "'回路处置建议（处置模块 Phase 1：建议-处置-验证-关闭全生命周期）'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "COMMENT ON TABLE loop_action_item IS "
        "'回路处置建议（建议-处置-验证-关闭闭环，当前仅建议态）'"
    )
    op.drop_index("idx_loop_action_item_status", table_name="loop_action_item")
    op.drop_constraint("ck_loop_action_item_verify_result", "loop_action_item", type_="check")
    op.drop_constraint("ck_loop_action_item_action_type", "loop_action_item", type_="check")
    op.drop_constraint("ck_loop_action_item_status", "loop_action_item", type_="check")
    op.create_check_constraint(
        "ck_loop_action_item_status", "loop_action_item", "status IN ('PENDING')"
    )
    op.drop_constraint("fk_loop_action_item_verify_run", "loop_action_item", type_="foreignkey")
    op.drop_column("loop_action_item", "ignore_reason")
    op.drop_column("loop_action_item", "tuning_record_id")
    op.drop_column("loop_action_item", "kpi_after")
    op.drop_column("loop_action_item", "kpi_before")
    op.drop_column("loop_action_item", "verified_at")
    op.drop_column("loop_action_item", "verified_by")
    op.drop_column("loop_action_item", "verify_note")
    op.drop_column("loop_action_item", "verify_result")
    op.drop_column("loop_action_item", "verify_run_id")
    op.drop_column("loop_action_item", "submitted_at")
    op.drop_column("loop_action_item", "handled_at")
    op.drop_column("loop_action_item", "handled_by")
    op.drop_column("loop_action_item", "action_detail")
    op.drop_column("loop_action_item", "action_type")
