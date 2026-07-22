"""add tracker D2 columns + open-state unique index (D2)

整改计划 D2：Tracker 模型补全。

新增列：
- created_at：建单时间（闭环时长统计 = updated_at - created_at）
- comment：处理意见/审查备注
- moc_ref：MOC（变更管理）关联编号
- moc_not_applicable：MOC 是否不适用（D3 必填校验依赖）
- moc_reason：MOC 不适用时的依据说明
- diagnosis_result_id：诊断结果外键（ON DELETE SET NULL，保留历史）

新增约束：
- uk_action_tracker_open：部分唯一索引 (loop_id, diagnosis_label)
  WHERE action_status IN ('PENDING', 'IN_PROGRESS')
  同一回路同一标签在开放态下唯一，闭环后允许新建（历史保留）。
  D1 自动建单依赖此约束防重复。

注意：delete_loop 是软删除（is_active=False），ON DELETE CASCADE 不会触发，
故 diagnosis_result_id 的 ON DELETE SET NULL 仅在硬删除时生效，日常保留历史。

Revision ID: b2d3e4f5g6h7
Revises: a1c1d2e3f4g5
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d3e4f5g6h7"
down_revision: str | None = "a1c1d2e3f4g5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 新增列
    op.add_column(
        "action_tracker",
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
            comment="建单时间",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "comment",
            sa.String(500),
            nullable=True,
            comment="处理意见/审查备注",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "moc_ref",
            sa.String(255),
            nullable=True,
            comment="MOC（变更管理）关联编号",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "moc_not_applicable",
            sa.Boolean(),
            nullable=True,
            comment="MOC 是否不适用",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "moc_reason",
            sa.String(500),
            nullable=True,
            comment="MOC 不适用时的依据说明",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "diagnosis_result_id",
            sa.UUID(as_uuid=False),
            nullable=True,
            comment="诊断结果外键",
        ),
    )
    op.create_foreign_key(
        "fk_action_tracker_diag_result",
        "action_tracker",
        "diagnosis_result",
        ["diagnosis_result_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 部分唯一索引：开放态 (loop_id, diagnosis_label) 唯一
    op.create_index(
        "uk_action_tracker_open",
        "action_tracker",
        ["loop_id", "diagnosis_label"],
        unique=True,
        postgresql_where=sa.text(
            "action_status IN ('PENDING', 'IN_PROGRESS') "
            "AND loop_id IS NOT NULL AND diagnosis_label IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uk_action_tracker_open", table_name="action_tracker")
    op.drop_constraint("fk_action_tracker_diag_result", "action_tracker", type_="foreignkey")
    op.drop_column("action_tracker", "diagnosis_result_id")
    op.drop_column("action_tracker", "moc_reason")
    op.drop_column("action_tracker", "moc_not_applicable")
    op.drop_column("action_tracker", "moc_ref")
    op.drop_column("action_tracker", "comment")
    op.drop_column("action_tracker", "created_at")
