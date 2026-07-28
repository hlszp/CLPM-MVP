"""tuning_phase2_schema: TuningRecord 新增辨识元数据与多 PID 对比字段.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-28

变更内容（技术方案 §4.1/§4.2）：
- 新增 12 列：identify_method/data_source/time_window_start/time_window_end/
  confidence_level/confidence_reason/excitation_score/residual_test_passed/
  pid_candidates/candidate_results/task_id/completed_at
- 状态机 CHECK 约束扩展（兼容旧枚举 + 新枚举）
- 新增 identify_method / data_source CHECK 约束
- 存量数据迁移：PENDING→DRAFT, APPLIED/VERIFIED→COMPLETED
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """升级：新增字段 + 更新约束 + 数据迁移."""

    # 1. 新增列
    op.add_column("tuning_record", sa.Column("identify_method", sa.String(30), nullable=True))
    op.add_column("tuning_record", sa.Column("data_source", sa.String(20), nullable=True))
    op.add_column("tuning_record", sa.Column("time_window_start", sa.DateTime(), nullable=True))
    op.add_column("tuning_record", sa.Column("time_window_end", sa.DateTime(), nullable=True))
    op.add_column("tuning_record", sa.Column("confidence_level", sa.String(12), nullable=True))
    op.add_column("tuning_record", sa.Column("confidence_reason", sa.String(200), nullable=True))
    op.add_column("tuning_record", sa.Column("excitation_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("tuning_record", sa.Column("residual_test_passed", sa.Boolean(), nullable=True))
    op.add_column("tuning_record", sa.Column("pid_candidates", JSON(), nullable=True))
    op.add_column("tuning_record", sa.Column("candidate_results", JSON(), nullable=True))
    op.add_column("tuning_record", sa.Column("task_id", sa.String(64), nullable=True))
    op.add_column("tuning_record", sa.Column("completed_at", sa.DateTime(), nullable=True))

    # 2. 存量数据迁移：旧状态 → 新状态
    op.execute("UPDATE tuning_record SET status = 'DRAFT' WHERE status = 'PENDING'")
    op.execute("UPDATE tuning_record SET status = 'COMPLETED' WHERE status = 'APPLIED'")
    op.execute("UPDATE tuning_record SET status = 'COMPLETED' WHERE status = 'VERIFIED'")

    # 3. 更新 status CHECK 约束（新枚举 + 兼容期保留旧值防空迁移报错）
    op.drop_constraint("ck_tuning_record_status", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_status",
        "tuning_record",
        "status IN ('DRAFT', 'RUNNING', 'IDENTIFIED', 'SIMULATED', "
        "'COMPLETED', 'INCONCLUSIVE', 'ROLLED_BACK', "
        "'PENDING', 'APPLIED', 'VERIFIED')",
    )

    # 4. 新增 identify_method CHECK 约束
    op.create_check_constraint(
        "ck_tuning_record_identify_method",
        "tuning_record",
        "identify_method IS NULL OR identify_method IN ("
        "'HISTORICAL_ARX', 'HISTORICAL_ARMAX', 'HISTORICAL_IV', "
        "'STEP_TWO_POINT', 'STEP_AREA', 'STEP_NLS')",
    )

    # 5. 新增 data_source CHECK 约束
    op.create_check_constraint(
        "ck_tuning_record_data_source",
        "tuning_record",
        "data_source IS NULL OR data_source IN ('HISTORY', 'STEP_EXPERIMENT')",
    )


def downgrade() -> None:
    """降级：移除新增字段与约束，恢复旧状态枚举."""
    # 移除新约束
    op.drop_constraint("ck_tuning_record_data_source", "tuning_record", type_="check")
    op.drop_constraint("ck_tuning_record_identify_method", "tuning_record", type_="check")

    # 恢复旧 status 约束
    op.drop_constraint("ck_tuning_record_status", "tuning_record", type_="check")
    op.create_check_constraint(
        "ck_tuning_record_status",
        "tuning_record",
        "status IN ('PENDING', 'IDENTIFIED', 'SIMULATED', 'APPLIED', 'VERIFIED')",
    )

    # 恢复旧状态值
    op.execute("UPDATE tuning_record SET status = 'PENDING' WHERE status = 'DRAFT'")
    op.execute("UPDATE tuning_record SET status = 'APPLIED' WHERE status = 'COMPLETED'")

    # 移除新增列
    op.drop_column("tuning_record", "completed_at")
    op.drop_column("tuning_record", "task_id")
    op.drop_column("tuning_record", "candidate_results")
    op.drop_column("tuning_record", "pid_candidates")
    op.drop_column("tuning_record", "residual_test_passed")
    op.drop_column("tuning_record", "excitation_score")
    op.drop_column("tuning_record", "confidence_reason")
    op.drop_column("tuning_record", "confidence_level")
    op.drop_column("tuning_record", "time_window_end")
    op.drop_column("tuning_record", "time_window_start")
    op.drop_column("tuning_record", "data_source")
    op.drop_column("tuning_record", "identify_method")
