"""V62-P3-006 algorithm 新增 IDENTIFICATION_ONLY，回填遗留 IMC 占位记录.

revision: p3c3d4e5f6g7
down_revision: p3b2c3d4e5f6

变更：
1. ``tuning_record.algorithm`` CHECK 约束新增 ``IDENTIFICATION_ONLY`` 值；
2. 将遗留 ``algorithm='IMC'`` 且 ``recommended_pid IS NULL`` 的纯辨识记录
   回填为 ``algorithm='IDENTIFICATION_ONLY'``（不再用 IMC 占位）。

判定标准：``recommended_pid IS NULL`` 表示未执行 PID 整定，是纯辨识记录。
有 ``recommended_pid`` 的记录保留原 algorithm（真实整定算法）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "p3c3d4e5f6g7"
down_revision = "p3b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """更新 CHECK 约束 + 回填遗留 IMC 占位记录."""
    # 1. 替换 CHECK 约束（新增 IDENTIFICATION_ONLY）
    op.drop_constraint("ck_tuning_record_algo", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_algo",
        "tuning_record",
        "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC', 'IDENTIFICATION_ONLY')",
    )

    # 2. 回填遗留 IMC 占位记录为 IDENTIFICATION_ONLY
    # 判定：recommended_pid IS NULL → 纯辨识记录（未执行 PID 整定）
    op.execute(
        sa.text(
            """
            UPDATE tuning_record
            SET algorithm = 'IDENTIFICATION_ONLY'
            WHERE algorithm = 'IMC'
              AND recommended_pid IS NULL
            """
        )
    )


def downgrade() -> None:
    """回滚：IDENTIFICATION_ONLY → IMC，恢复原 CHECK 约束."""
    op.execute(
        sa.text(
            """
            UPDATE tuning_record
            SET algorithm = 'IMC'
            WHERE algorithm = 'IDENTIFICATION_ONLY'
            """
        )
    )
    op.drop_constraint("ck_tuning_record_algo", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_algo",
        "tuning_record",
        "algorithm IN ('IMC', 'LAMBDA', 'ZN', 'COHEN_COON', 'SIMC')",
    )
