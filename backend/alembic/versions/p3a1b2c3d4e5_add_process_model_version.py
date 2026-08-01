"""add process_model_version（V62-P3-003 模型生命周期最小聚合）

v6.2 Phase 3 模型实体 ADR 通过后新增的最小聚合。承载回路过程模型
G(s)=PV/OP 的不可变版本化证据，支持 CANDIDATE/CURRENT/RETIRED 生命周期。

本迁移只建表 + 给 tuning_record 加可空外键；一次性回填 / 影子读比对 /
切换读取 / 停止旧参数新写由 P3-005 落地。

设计依据：v6.2 方案 §7.3（首版不建 process_model 主表，不建独立审批表 /
工况表 / 误差表，全部合并进本聚合）。

并发一致性（P3-004）：同一 loop_id 下 status=CURRENT 至多一条，由部分唯一
索引 uk_process_model_version_current 在数据库层强制。

revision: p3a1b2c3d4e5
down_revision: h8b9c0d1e2f3
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "p3a1b2c3d4e5"
down_revision = "h8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "process_model_version",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("loop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="'CANDIDATE'"),
        sa.Column("data_window_start", sa.DateTime(), nullable=True),
        sa.Column("data_window_end", sa.DateTime(), nullable=True),
        sa.Column("data_hash", sa.String(length=64), nullable=True),
        sa.Column("condition_summary", postgresql.JSON(), nullable=True),
        sa.Column("algorithm_version", sa.String(length=50), nullable=True),
        sa.Column("identify_method", sa.String(length=30), nullable=True),
        sa.Column("model_type", sa.String(length=20), nullable=False),
        sa.Column("model_params", postgresql.JSON(), nullable=True),
        sa.Column("theta_source", sa.String(length=20), nullable=True),
        sa.Column("sampling_period", sa.Float(), nullable=True),
        sa.Column("metrics", postgresql.JSON(), nullable=True),
        sa.Column("residual_test", postgresql.JSON(), nullable=True),
        sa.Column("uncertainty", postgresql.JSON(), nullable=True),
        sa.Column("physical_feasibility", postgresql.JSON(), nullable=True),
        sa.Column("confidence_level", sa.String(length=12), nullable=True),
        sa.Column("confidence_reason", sa.String(length=500), nullable=True),
        sa.Column("published_by", sa.String(length=50), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("supersedes_version_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("retired_reason", sa.String(length=500), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("retired_by", sa.String(length=50), nullable=True),
        sa.Column("created_by", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.ForeignKeyConstraint(
            ["loop_id"],
            ["loop_ledger.id"],
            ondelete="CASCADE",
            name="fk_process_model_version_loop_id",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"],
            ["process_model_version.id"],
            ondelete="SET NULL",
            name="fk_process_model_version_supersedes",
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE', 'CURRENT', 'RETIRED')",
            name="ck_process_model_version_status",
        ),
        sa.CheckConstraint(
            "model_type IN ('FOPDT', 'SOPDT', 'IPDT')",
            name="ck_process_model_version_model_type",
        ),
        sa.CheckConstraint(
            "theta_source IS NULL OR theta_source IN ('EXPLICIT', 'SEARCHED', 'HEURISTIC_2TS')",
            name="ck_process_model_version_theta_source",
        ),
        sa.CheckConstraint(
            "identify_method IS NULL OR identify_method IN ("
            "'HISTORICAL_ARX', 'HISTORICAL_ARMAX', 'HISTORICAL_IV', "
            "'STEP_TWO_POINT', 'STEP_AREA', 'STEP_NLS')",
            name="ck_process_model_version_identify_method",
        ),
        sa.CheckConstraint(
            "confidence_level IS NULL OR confidence_level IN "
            "('A', 'B', 'C', 'D', 'E', 'INCONCLUSIVE')",
            name="ck_process_model_version_confidence",
        ),
        comment="过程模型版本聚合（V62-P3-003，不可变版本化辨识证据）",
    )
    # P3-004 并发一致性：同一回路至多一个 CURRENT（部分唯一索引）
    op.create_index(
        "uk_process_model_version_current",
        "process_model_version",
        ["loop_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CURRENT'"),
    )
    # (loop_id, version) 唯一：版本号单回路单调不重复
    op.create_index(
        "uk_process_model_version_loop_version",
        "process_model_version",
        ["loop_id", "version"],
        unique=True,
    )
    op.create_index(
        "idx_process_model_version_loop_status",
        "process_model_version",
        ["loop_id", "status"],
        unique=False,
    )

    # V62-P3-006：tuning_record 引用模型版本（可空，兼容旧 record）
    op.add_column(
        "tuning_record",
        sa.Column("process_model_version_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_tuning_record_process_model_version",
        "tuning_record",
        "process_model_version",
        ["process_model_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_tuning_record_process_model_version",
        "tuning_record",
        type_="foreignkey",
    )
    op.drop_column("tuning_record", "process_model_version_id")
    op.drop_index("idx_process_model_version_loop_status", table_name="process_model_version")
    op.drop_index("uk_process_model_version_loop_version", table_name="process_model_version")
    op.drop_index("uk_process_model_version_current", table_name="process_model_version")
    op.drop_table("process_model_version")
