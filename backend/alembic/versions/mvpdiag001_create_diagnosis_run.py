"""create diagnosis_run table (MVP v2 diagnosis module)

Revision ID: mvpdiag001
Revises: f5timec001tc
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "mvpdiag001"
down_revision: str | None = "f5timec001tc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "diagnosis_run" in inspector.get_table_names():
        return

    op.create_table(
        "diagnosis_run",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("triggered_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("time_window_start", sa.DateTime(), nullable=False),
        sa.Column("time_window_end", sa.DateTime(), nullable=False),
        sa.Column("operator_group", sa.String(length=8), nullable=False, server_default="full"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="RUNNING"),
        sa.Column("data_gate", postgresql.JSONB(), nullable=True),
        sa.Column("operator_results", postgresql.JSONB(), nullable=True),
        sa.Column("fusion_results", postgresql.JSONB(), nullable=True),
        sa.Column("symptom_tags", postgresql.JSONB(), nullable=True),
        sa.Column("primary_category", sa.String(length=32), nullable=True),
        sa.Column("primary_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("secondary_categories", postgresql.JSONB(), nullable=True),
        sa.Column("pending_review", postgresql.JSONB(), nullable=True),
        sa.Column("severity", sa.String(length=8), nullable=True),
        sa.Column("rationale", postgresql.JSONB(), nullable=True),
        sa.Column("recommendations", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_charts", postgresql.JSONB(), nullable=True),
        sa.Column("threshold_version", sa.String(length=32), nullable=True),
        sa.Column("algorithm_version", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')",
            name="ck_diagnosis_run_status",
        ),
        sa.CheckConstraint(
            "primary_category IS NULL OR primary_category IN "
            "('TUNING', 'VALVE', 'INSTRUMENT', 'PROCESS', 'UTILIZATION', 'DESIGN', "
            "'DATA_INSUFFICIENT')",
            name="ck_diagnosis_run_category",
        ),
        sa.CheckConstraint(
            "severity IS NULL OR severity IN ('HIGH', 'MEDIUM', 'LOW')",
            name="ck_diagnosis_run_severity",
        ),
        comment="诊断运行记录（MVP v2：一次诊断一条完整结论）",
    )
    op.create_index("idx_diagnosis_run_loop_created", "diagnosis_run", ["loop_id", "created_at"])
    op.create_index("idx_diagnosis_run_category", "diagnosis_run", ["primary_category"])
    op.create_index("idx_diagnosis_run_task", "diagnosis_run", ["task_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "diagnosis_run" not in inspector.get_table_names():
        return
    op.drop_index("idx_diagnosis_run_task", table_name="diagnosis_run")
    op.drop_index("idx_diagnosis_run_category", table_name="diagnosis_run")
    op.drop_index("idx_diagnosis_run_loop_created", table_name="diagnosis_run")
    op.drop_table("diagnosis_run")
