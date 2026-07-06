"""add UNIQUE constraint on (loop_id, ts_start) for kpi_snapshot_hourly

修复 kpi_snapshot_hourly 表缺少 (loop_id, ts_start) 唯一约束的问题。
此约束确保每个回路在每个小时仅有一条快照记录，防止并发写入产生重复。

迁移步骤：
1. 删除已存在的重复记录（保留每组最早的一条）
2. 删除旧的普通 btree 索引 ix_kpi_snapshot_hourly_loop_ts
3. 创建 UNIQUE 约束 uq_kpi_snapshot_hourly_loop_ts

设计依据：DDS §2.8；GB/T 44693.2-2024 §6.4 数据唯一性要求

Revision ID: q1a2b3c4d5e6
Revises: p9r0s1t2u3v4
Create Date: 2026-07-06
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "q1a2b3c4d5e6"
down_revision = "p9r0s1t2u3v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """添加 UNIQUE(loop_id, ts_start) 约束。

    先删除重复记录（保留最早插入的一条，即 MIN(id)），
    再删除旧普通索引，最后创建 UNIQUE 约束。
    """
    # 步骤 1：删除重复记录，每组只保留 MIN(id::text) 那一条
    # PostgreSQL 不支持 MIN(uuid)，需转 text
    op.execute(
        """
        DELETE FROM kpi_snapshot_hourly
        WHERE id::text NOT IN (
            SELECT MIN(id::text) AS keep_id
            FROM kpi_snapshot_hourly
            WHERE loop_id IS NOT NULL
            GROUP BY loop_id, ts_start
        )
        AND loop_id IS NOT NULL
        """
    )

    # 步骤 2：删除旧普通 btree 索引（k2f3a4b5c6d7 迁移创建）
    op.execute("DROP INDEX IF EXISTS ix_kpi_snapshot_hourly_loop_ts")

    # 步骤 3：创建 UNIQUE 约束
    op.create_unique_constraint(
        "uq_kpi_snapshot_hourly_loop_ts",
        "kpi_snapshot_hourly",
        ["loop_id", "ts_start"],
    )


def downgrade() -> None:
    """回滚：恢复普通 btree 索引，删除 UNIQUE 约束。"""
    op.drop_constraint(
        "uq_kpi_snapshot_hourly_loop_ts",
        "kpi_snapshot_hourly",
        type_="unique",
    )
    op.create_index(
        "ix_kpi_snapshot_hourly_loop_ts",
        "kpi_snapshot_hourly",
        ["loop_id", "ts_start"],
        unique=False,
    )
