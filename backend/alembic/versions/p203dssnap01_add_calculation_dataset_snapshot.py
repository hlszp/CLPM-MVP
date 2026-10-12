"""add calculation_dataset_snapshot (P2-03 计算上下文与历史复现)

Revision ID: p203dssnap01
Revises: p202cfgpub01
Create Date: 2026-10-12

P2-03 计算上下文与历史复现（方案 01 §3.2 + P0-02 冻结 v2 §3 DEC-10 +
P2-03 辨识证据契约冻结稿/07 号文 §3.2）：

1. 新表 ``calculation_dataset_snapshot``：不可变输入包（NPZ+JSON manifest，
   禁 pickle）元数据。区分绑定身份 ``dataset_ref`` 与数值输入身份
   ``input_hash``（内容 SHA256）；保留契约 NORMAL 90 天 / FROZEN 固定引用；
   清理先标 ``cleaned_at`` 再删文件（隔离清理任务）。
2. ``tuning_record`` 加列：``dataset_snapshot_id``（FK SET NULL）/
   ``source_record_id``（最终方案来源记录）/ ``dcs_template_revision`` /
   ``calc_context``（CalculationContext JSON 载体）。
3. ``process_model_version`` 加列 ``dataset_snapshot_id``（模型版本绑定
   实际输入包，独立终验引用可重放的基础）。
4. ``diagnosis_run`` 加列 ``dataset_snapshot_id``（诊断宽表原始输入包）。
5. 无存量回填：既有记录不带包 → 各列 NULL = 显式"不可完整复现"
   （与 P1-05 datasetSnapshotId 可空口径一致，不伪补）。

downgrade：DROP 新表 + DROP 各加列。已保存包文件不由本迁移管理
（清理走服务层保留契约，downgrade 生产回退按方案 §6 前向修复）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p203dssnap01"
down_revision: str | None = "p202cfgpub01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. 不可变输入包元数据表
    # ------------------------------------------------------------------
    op.create_table(
        "calculation_dataset_snapshot",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("dataset_ref", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("ts_start", sa.DateTime(), nullable=False),
        sa.Column("ts_end", sa.DateTime(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("created_by_record_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("retention_class", sa.String(8), nullable=False, server_default="NORMAL"),
        sa.Column("frozen_reason", sa.String(200), nullable=True),
        sa.Column("frozen_by_record_ids", postgresql.JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cleaned_at", sa.DateTime(), nullable=True),
        sa.Column("loop_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("timezone('UTC', now())"),
        ),
        sa.CheckConstraint(
            "retention_class IN ('NORMAL', 'FROZEN')",
            name="ck_calc_dataset_snapshot_retention",
        ),
        sa.CheckConstraint(
            "kind IN ('KPI_GRID', 'IDENTIFICATION', 'DIAGNOSIS_RAW')",
            name="ck_calc_dataset_snapshot_kind",
        ),
        sa.CheckConstraint(
            "retention_class <> 'FROZEN' OR frozen_reason IS NOT NULL",
            name="ck_calc_dataset_snapshot_frozen_reason",
        ),
        sa.CheckConstraint("ts_end > ts_start", name="ck_calc_dataset_snapshot_window"),
        sa.UniqueConstraint(
            "dataset_ref",
            "input_hash",
            "ts_start",
            "ts_end",
            "created_by_record_id",
            name="uq_calc_dataset_snapshot_content_ref",
        ),
        comment="不可变计算输入包元数据（P2-03，NPZ+manifest 内容寻址）",
    )
    op.create_index(
        "idx_calc_dataset_snapshot_input_hash", "calculation_dataset_snapshot", ["input_hash"]
    )
    op.create_index(
        "idx_calc_dataset_snapshot_retention",
        "calculation_dataset_snapshot",
        ["retention_class", "expires_at"],
    )
    op.create_index("idx_calc_dataset_snapshot_loop", "calculation_dataset_snapshot", ["loop_id"])

    # ------------------------------------------------------------------
    # 2. tuning_record 计算上下文列
    # ------------------------------------------------------------------
    op.add_column(
        "tuning_record",
        sa.Column(
            "dataset_snapshot_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "calculation_dataset_snapshot.id",
                name="fk_tuning_record_dataset_snapshot",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    op.add_column("tuning_record", sa.Column("source_record_id", postgresql.UUID(), nullable=True))
    op.add_column("tuning_record", sa.Column("dcs_template_revision", sa.String(64), nullable=True))
    op.add_column("tuning_record", sa.Column("calc_context", sa.JSON(), nullable=True))

    # ------------------------------------------------------------------
    # 3. process_model_version 输入包引用
    # ------------------------------------------------------------------
    op.add_column(
        "process_model_version",
        sa.Column(
            "dataset_snapshot_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "calculation_dataset_snapshot.id",
                name="fk_process_model_version_dataset_snapshot",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # 4. diagnosis_run 输入包引用
    # ------------------------------------------------------------------
    op.add_column(
        "diagnosis_run",
        sa.Column(
            "dataset_snapshot_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "calculation_dataset_snapshot.id",
                name="fk_diagnosis_run_dataset_snapshot",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("diagnosis_run", "dataset_snapshot_id")
    op.drop_column("process_model_version", "dataset_snapshot_id")
    op.drop_column("tuning_record", "calc_context")
    op.drop_column("tuning_record", "dcs_template_revision")
    op.drop_column("tuning_record", "source_record_id")
    op.drop_column("tuning_record", "dataset_snapshot_id")
    op.drop_index("idx_calc_dataset_snapshot_loop", table_name="calculation_dataset_snapshot")
    op.drop_index("idx_calc_dataset_snapshot_retention", table_name="calculation_dataset_snapshot")
    op.drop_index("idx_calc_dataset_snapshot_input_hash", table_name="calculation_dataset_snapshot")
    op.drop_table("calculation_dataset_snapshot")
