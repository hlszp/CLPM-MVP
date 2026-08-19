"""add diagnosis_run.metric_summary (诊断指标汇总落库，方案 A)

诊断时间窗内 KPI 快照均值 + 算子特征聚合为统一 0~100 口径的
metricSummary 字段，随诊断 run 输出（坏值率/饱和率/振荡率/粘滞系数/
稳定时间/行程指数 + 6 正向率）。

设计依据：诊断模块 metricSummary 方案 A（2026-08-19）
关联代码：app/services/diagnosis_orchestrator.py:_build_metric_summary

Revision ID: a3b4c5d6e7f8
Revises: c2d3e4f5a6b7
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision = "a3b4c5d6e7f8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnosis_run",
        sa.Column(
            "metric_summary",
            JSONB(),
            nullable=True,
            comment="诊断指标汇总（窗口 KPI 均值 + 算子特征，0~100 统一口径）",
        ),
    )


def downgrade() -> None:
    op.drop_column("diagnosis_run", "metric_summary")
