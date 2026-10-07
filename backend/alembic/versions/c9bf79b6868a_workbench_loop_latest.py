"""workbench_loop_latest 预计算表 + diagnosis_run 慢查询索引

Revision ID: c9bf79b6868a
Revises: 17fdbfa579af
Create Date: 2026-10-07

驾驶舱 P1 根治（生产 1209 回路下 /workbench/diagnosis、/cockpit/overview、
/workbench/tuning 实时聚合 9~25s 超前端 10s 超时线）：
1. 新表 workbench_loop_latest：每回路最新 fitness 快照 + 最新未处置诊断 run
   摘要（双 LATERAL 预展开），5min Celery 全量重算，接口改纯读；
2. diagnosis_run 补两个复合索引：(status, created_at) 覆盖 open_tags/concl/
   rule_stats 的窗口过滤；finished_at 覆盖 cockpit 漏斗 diagnosed 段
   （此前全表扫）。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "c9bf79b6868a"
down_revision = "17fdbfa579af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workbench_loop_latest (
            id BIGSERIAL PRIMARY KEY,
            loop_id UUID NOT NULL,
            tag_name VARCHAR(64),
            unit_id UUID,
            unit_name VARCHAR(128),
            factory_name VARCHAR(128),
            score NUMERIC(6, 2),
            fitness_level VARCHAR(8),
            fitness_tags JSONB,
            fitness_detail JSONB,
            assess_level VARCHAR(8),
            diagnose_level VARCHAR(8),
            tune_level VARCHAR(8),
            snapshot_ts TIMESTAMPTZ,
            latest_run_id UUID,
            latest_category VARCHAR(32),
            latest_severity VARCHAR(16),
            latest_confidence NUMERIC(4, 3),
            latest_conclusion TEXT,
            latest_run_at TIMESTAMPTZ,
            top_symptom JSONB,
            terminal_cnt INTEGER NOT NULL DEFAULT 0,
            is_open BOOLEAN NOT NULL DEFAULT FALSE,
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uniq_wll_loop UNIQUE (loop_id)
        )
        """
    )
    op.create_index(
        "idx_wll_open", "workbench_loop_latest", ["is_open", "latest_severity", "latest_run_at"]
    )
    op.create_index("idx_wll_unit", "workbench_loop_latest", ["unit_id"])

    op.create_index(
        "idx_diagnosis_run_status_created",
        "diagnosis_run",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_diagnosis_run_finished_at",
        "diagnosis_run",
        ["finished_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_diagnosis_run_finished_at", table_name="diagnosis_run")
    op.drop_index("idx_diagnosis_run_status_created", table_name="diagnosis_run")
    op.drop_index("idx_wll_unit", table_name="workbench_loop_latest")
    op.drop_index("idx_wll_open", table_name="workbench_loop_latest")
    op.drop_table("workbench_loop_latest")
