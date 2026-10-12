"""diagnosis_run 批次幂等唯一约束 (task_id, loop_id)

P3-02（自动任务 ID、幂等与恢复）：同任务同回路只允许一条 run 行——
Celery 重试/worker 硬杀后重投递重跑批次时不重复已完成 run 与 SYSTEM 建议。

存量去重：历史批次重试可能已产生同 (task_id, loop_id) 多行；保留每组
created_at 最新一行，较早行 task_id 置 NULL（证据保留、仅脱开任务关联，
不删除）。task_id 为 NULL 的行不受约束影响（PG 唯一约束 NULL 互不相等）。

Revision ID: p302task01
Revises: p402authz02
Create Date: 2026-10-11

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "p302task01"
down_revision = "p402authz02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 存量去重：每组 (task_id, loop_id) 保留 created_at 最新一行，较早行脱链
    op.execute(
        """
        UPDATE diagnosis_run AS dr
        SET task_id = NULL
        WHERE dr.task_id IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM diagnosis_run AS newer
            WHERE newer.task_id = dr.task_id
              AND newer.loop_id = dr.loop_id
              AND (newer.created_at, newer.id) > (dr.created_at, dr.id)
          )
        """
    )
    # 2) 唯一约束（幂等键）
    op.create_unique_constraint(
        "uq_diagnosis_run_task_loop", "diagnosis_run", ["task_id", "loop_id"]
    )


def downgrade() -> None:
    # 注意：upgrade 中被置 NULL 的 task_id 不回填（原始关联已不可判定）。
    op.drop_constraint("uq_diagnosis_run_task_loop", "diagnosis_run", type_="unique")
