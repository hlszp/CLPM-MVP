"""add diagnosis_task table and diagnosis_result.task_id column

Revision ID: v6p1diag001
Revises: v6p1lmt001
Create Date: 2026-07-07

新增诊断任务表 diagnosis_task，并给 diagnosis_result 表添加 task_id 列和索引。
为现有 diagnosis_result 记录回填 diagnosis_task 记录（每个 loop_id + diagnosed_at 组合
创建一条 task，status='SUCCESS', trigger_type='auto', is_archived=True）。

设计依据：PRD §5.6 诊断中心 / IDS §2.4 诊断任务管理
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "v6p1diag001"
down_revision = "v6p1lmt001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建 diagnosis_task 表
    op.create_table(
        "diagnosis_task",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
        ),
        sa.Column("loop_id", UUID(as_uuid=False), nullable=False),
        sa.Column("trigger_type", sa.String(10), nullable=False),
        sa.Column("triggered_by", sa.String(50), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("time_range_start", sa.DateTime(), nullable=True),
        sa.Column("time_range_end", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "triggered_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("archived_by", sa.String(50), nullable=True),
        sa.ForeignKeyConstraint(
            ["loop_id"],
            ["loop_ledger.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("trigger_type IN ('manual', 'auto')", name="ck_diag_task_trigger_type"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_diag_task_status",
        ),
        sa.Index("idx_diagnosis_task_loop_id", "loop_id"),
        sa.Index("idx_diagnosis_task_status", "status"),
        sa.Index("idx_diagnosis_task_archived", "is_archived"),
        comment="诊断任务表：承载用户手动触发或系统自动触发的回路诊断任务全生命周期记录",
    )

    # 2. 给 diagnosis_result 表添加 task_id 列
    op.add_column(
        "diagnosis_result",
        sa.Column("task_id", UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_diagnosis_result_task_id",
        "diagnosis_result",
        "diagnosis_task",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_diagnosis_result_task_id",
        "diagnosis_result",
        ["task_id"],
    )

    # 3. 为现有 diagnosis_result 记录回填 diagnosis_task 记录
    # 每个 loop_id + diagnosed_at 组合创建一条 task，status='SUCCESS',
    # trigger_type='auto', is_archived=True
    op.execute(
        """
        INSERT INTO diagnosis_task (
            id, loop_id, trigger_type, triggered_by, status,
            time_range_start, time_range_end, triggered_at, completed_at,
            is_archived, archived_at
        )
        SELECT
            gen_random_uuid(),
            loop_id,
            'auto',
            'system',
            'SUCCESS',
            diagnosed_at,
            diagnosed_at,
            diagnosed_at,
            diagnosed_at,
            true,
            diagnosed_at
        FROM (
            SELECT DISTINCT loop_id, diagnosed_at
            FROM diagnosis_result
            WHERE task_id IS NULL AND loop_id IS NOT NULL
        ) AS distinct_combos
        """
    )

    # 4. 回填 diagnosis_result.task_id：关联到对应的回填 task
    op.execute(
        """
        UPDATE diagnosis_result AS dr
        SET task_id = (
            SELECT dt.id
            FROM diagnosis_task AS dt
            WHERE dt.loop_id = dr.loop_id
              AND dt.triggered_at = dr.diagnosed_at
              AND dt.trigger_type = 'auto'
              AND dt.is_archived = true
            LIMIT 1
        )
        WHERE dr.task_id IS NULL AND dr.loop_id IS NOT NULL
        """
    )


def downgrade() -> None:
    # 删除 diagnosis_result.task_id 索引、外键、列
    op.drop_index("idx_diagnosis_result_task_id", table_name="diagnosis_result")
    op.drop_constraint("fk_diagnosis_result_task_id", "diagnosis_result", type_="foreignkey")
    op.drop_column("diagnosis_result", "task_id")
    # 删除 diagnosis_task 表
    op.drop_table("diagnosis_task")
