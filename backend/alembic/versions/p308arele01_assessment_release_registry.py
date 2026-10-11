"""assessment release registry + diagnosis assessment_meta + kind CHECK (P3-08a)

Revision ID: p308arele01
Revises: p204engtpl01
Create Date: 2026-10-12

P3-08a 公共接线（评估基础侧）——模型变更与迁移同批，**只加不改**：

1. 新表 ``assessment_release_registry``：评估发布代际登记（上线清单六要素
   assessmentProfile/algorithmVersion/dataPolicyVersion/configRevision/
   buildDigest/releaseGeneration + snapshotSchemaVersion、消费者最低版本、
   作用范围、回退 profile；状态 STAGED/ACTIVE/RETIRED，同 profile 至多
   一条 ACTIVE——partial unique index）。EA-D05：验收后直接采用（无线上
   SHADOW/节点试点前置）；发布/回退=登记状态迁移，不删行。
   种子：预登记 LEGACY_V2 的首个代际（STAGED，不自动激活——现行口径
   releaseGeneration 未登记时消费侧显式 null，不伪代际；激活走
   activate_release 显式动作）。
2. ``diagnosis_run`` 增列 ``assessment_meta``（JSONB 可空）：D0 契约 O-1
   定稿——诊断科学结论元信息（judgementStatus/causeStatus 等，08§4.2
   结构）。写入方归诊断侧（P3-01/D1），本迁移只做载体；NULL=历史 run 无
   元信息（LEGACY_UNVERIFIABLE 口径）。
3. ``calculation_dataset_snapshot`` kind CHECK 扩值 ``KPI_ORIGINAL_EVENTS``
   （原事件包，10§3.5 两类对照输入；既有行无该值，纯扩枚举）。

downgrade：三步全部可逆（DROP 新表 / DROP 新列 / 还原 CHECK 四值→三值），
无存量数据损失（新增列可空、新 kind 无存量行）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "p308arele01"
down_revision: str | None = "p204engtpl01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_CONSTRAINT = "ck_calc_dataset_snapshot_kind"


def upgrade() -> None:
    op.create_table(
        "assessment_release_registry",
        sa.Column("id", UUID(as_uuid=False), nullable=False, primary_key=True),
        sa.Column("release_generation", sa.String(64), nullable=False),
        sa.Column("assessment_profile", sa.String(50), nullable=False),
        sa.Column("algorithm_version", sa.String(50), nullable=False),
        sa.Column("data_policy_version", sa.String(32), nullable=False),
        sa.Column("config_revision", sa.String(64), nullable=True),
        sa.Column("build_digest", sa.String(64), nullable=True),
        sa.Column("snapshot_schema_version", sa.String(16), nullable=False),
        sa.Column("consumer_min_version", sa.String(32), nullable=True),
        sa.Column("scope", JSONB(), nullable=True),
        sa.Column("rollback_profile", sa.String(50), nullable=True),
        sa.Column("status", sa.String(8), nullable=False, server_default="STAGED"),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('STAGED', 'ACTIVE', 'RETIRED')",
            name="ck_assessment_release_status",
        ),
        sa.UniqueConstraint("release_generation", name="uq_assessment_release_generation"),
        comment="评估发布代际登记（上线清单六要素，P3-08a，EA-D05 直接采用）",
    )
    op.create_index(
        "idx_assessment_release_profile",
        "assessment_release_registry",
        ["assessment_profile", "status"],
    )
    op.create_index(
        "uq_assessment_release_active_profile",
        "assessment_release_registry",
        ["assessment_profile"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    # 种子：预登记 LEGACY_V2 首个代际（STAGED——激活走显式动作，不自动生效；
    # 未激活前消费侧 releaseGeneration=null 显式标记"发布代际未登记"）
    op.execute(
        "INSERT INTO assessment_release_registry "
        "(id, release_generation, assessment_profile, algorithm_version, "
        " data_policy_version, snapshot_schema_version, status, note, created_at) "
        "VALUES (gen_random_uuid(), 'rg-0001-legacy-v2', 'LEGACY_V2', "
        "'KPI_CALC_v2.0', 'pre_v1', '1.3', 'STAGED', "
        "'P3-08a 预登记：现行正式评估口径（激活后任务固定 releaseGeneration）', "
        "timezone('UTC', now())) "
        "ON CONFLICT (release_generation) DO NOTHING"
    )

    op.add_column(
        "diagnosis_run",
        sa.Column("assessment_meta", JSONB(), nullable=True),
    )

    op.drop_constraint(_KIND_CONSTRAINT, "calculation_dataset_snapshot", type_="check")
    op.create_check_constraint(
        _KIND_CONSTRAINT,
        "calculation_dataset_snapshot",
        "kind IN ('KPI_GRID', 'IDENTIFICATION', 'DIAGNOSIS_RAW', 'KPI_ORIGINAL_EVENTS')",
    )


def downgrade() -> None:
    op.drop_constraint(_KIND_CONSTRAINT, "calculation_dataset_snapshot", type_="check")
    op.create_check_constraint(
        _KIND_CONSTRAINT,
        "calculation_dataset_snapshot",
        "kind IN ('KPI_GRID', 'IDENTIFICATION', 'DIAGNOSIS_RAW')",
    )
    op.drop_column("diagnosis_run", "assessment_meta")
    op.drop_index("uq_assessment_release_active_profile", table_name="assessment_release_registry")
    op.drop_index("idx_assessment_release_profile", table_name="assessment_release_registry")
    op.drop_table("assessment_release_registry")
