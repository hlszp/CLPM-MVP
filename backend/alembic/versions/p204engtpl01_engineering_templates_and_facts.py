"""engineering templates + loop engineering facts (P2-04 工程模板/阈值/工程事实)

Revision ID: p204engtpl01
Revises: p203dssnap01
Create Date: 2026-10-12

P2-04 工程模板、阈值真实消费与维护界面——本批后端范围（方案 01 §3.2 /
03 §5 P2-04 / 07 号文 §3.3）：

1. 新表 ``engineering_template``：响应/控制特征模板实体（template_key 全局
   唯一 = config_override TEMPLATE 层 scope_id）——模板实体化后 TEMPLATE→
   回路映射可精确计数（消除 affectedLoops=None 诚实降级的当前成因）。
2. 新表 ``engineering_template_version``：追加式不可变版本（发布新版只新增
   行不改旧行；已固定引用按 (template_id, version) 精确解析，发布新版后
   解析结果不变）。
3. ``loop_ledger`` 增列：control_action_direction（DIRECT/REVERSE/UNKNOWN，
   默认 UNKNOWN 显式未知态——07 号文 §3.3 控制作用方向）、
   engineering_template_id（模板关联，SET NULL）、engineering_template_version
   （pin 的不可变版本号；NULL=跟随 current_version 指针）。
4. ``dcs_pid_structure`` 增列：representation_status（KNOWN/UNKNOWN，默认
   KNOWN）——未知 DCS 型号的 PID 表示存显式"未知"态，禁止默认标准 kp/ti/td。
5. ``config_publication`` 操作枚举扩为含 TEMPLATE_VERSION（工程模板版本
   发布接统一发布链：revision 推进 + before/after + 审计；版本行本身
   追加式不可变）。

无存量回填：既有回路控制方向即 UNKNOWN（显式未知，非正/反假设）；既有
PID 结构行默认 KNOWN（其 p_type/i_unit/d_unit 原有语义不变）。

downgrade：两张新表直接 DROP；loop_ledger/dcs_pid_structure 增列直接 DROP
（均为本批新增的可空/带默认列，无存量数据损失）；config_publication 操作
枚举还原为四值（TEMPLATE_VERSION 历史行随前向修复口径处理，不盲删历史）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p204engtpl01"
down_revision: str | None = "p203dssnap01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. 工程模板实体（响应/控制特征；TEMPLATE 层覆盖的作用域目标）
    # ------------------------------------------------------------------
    op.create_table(
        "engineering_template",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("template_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="RESPONSE"),
        sa.Column(
            "current_version",
            sa.Integer(),
            nullable=True,
            comment="当前版本号；NULL=尚无发布版本。发布/回退经统一发布链推进 revision",
        ),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_by", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "category IN ('RESPONSE', 'CONTROL')",
            name="ck_engineering_template_category",
        ),
        sa.UniqueConstraint("template_key", name="uq_engineering_template_key"),
    )
    op.create_index("idx_engineering_template_key", "engineering_template", ["template_key"])

    # ------------------------------------------------------------------
    # 2. 模板不可变版本（追加式；发布新版不改旧行）
    # ------------------------------------------------------------------
    op.create_table(
        "engineering_template_version",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "engineering_template.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("published_by", sa.String(50), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("template_id", "version", name="uq_engineering_template_version"),
    )
    op.create_index(
        "idx_engineering_template_version_template",
        "engineering_template_version",
        ["template_id", "version"],
    )

    # ------------------------------------------------------------------
    # 3. loop_ledger 工程事实列（07 号文 §3.3）
    # ------------------------------------------------------------------
    op.add_column(
        "loop_ledger",
        sa.Column(
            "control_action_direction",
            sa.String(16),
            nullable=False,
            server_default="UNKNOWN",
            comment="控制作用方向：DIRECT(正作用) / REVERSE(反作用) / UNKNOWN(未知，显式态)",
        ),
    )
    op.create_check_constraint(
        "ck_loop_ledger_control_action_direction",
        "loop_ledger",
        "control_action_direction IN ('DIRECT', 'REVERSE', 'UNKNOWN')",
    )
    op.add_column(
        "loop_ledger",
        sa.Column(
            "engineering_template_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("engineering_template.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "loop_ledger",
        sa.Column(
            "engineering_template_version",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_loop_ledger_engineering_template",
        "loop_ledger",
        ["engineering_template_id"],
    )

    # ------------------------------------------------------------------
    # 4. dcs_pid_structure：PID 表示已知状态（显式未知态）
    # ------------------------------------------------------------------
    op.add_column(
        "dcs_pid_structure",
        sa.Column(
            "representation_status",
            sa.String(16),
            nullable=False,
            server_default="KNOWN",
            comment="PID 表示状态：KNOWN(已知) / UNKNOWN(显式未知，禁止默认标准表示)",
        ),
    )
    op.create_check_constraint(
        "ck_dcs_pid_structure_representation_status",
        "dcs_pid_structure",
        "representation_status IN ('KNOWN', 'UNKNOWN')",
    )

    # ------------------------------------------------------------------
    # 5. config_publication 操作枚举扩为含 TEMPLATE_VERSION（P2-04）
    # ------------------------------------------------------------------
    op.drop_constraint("ck_config_publication_operation", "config_publication", type_="check")
    op.create_check_constraint(
        "ck_config_publication_operation",
        "config_publication",
        "operation IN ('PUBLISH', 'RESET', 'ROLLBACK', 'LEGACY_SYNC', 'TEMPLATE_VERSION')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_config_publication_operation", "config_publication", type_="check")
    op.create_check_constraint(
        "ck_config_publication_operation",
        "config_publication",
        "operation IN ('PUBLISH', 'RESET', 'ROLLBACK', 'LEGACY_SYNC')",
    )
    op.drop_constraint(
        "ck_dcs_pid_structure_representation_status",
        "dcs_pid_structure",
        type_="check",
    )
    op.drop_column("dcs_pid_structure", "representation_status")
    op.drop_index("idx_loop_ledger_engineering_template", table_name="loop_ledger")
    op.drop_column("loop_ledger", "engineering_template_version")
    op.drop_column("loop_ledger", "engineering_template_id")
    op.drop_constraint("ck_loop_ledger_control_action_direction", "loop_ledger", type_="check")
    op.drop_column("loop_ledger", "control_action_direction")
    op.drop_index(
        "idx_engineering_template_version_template", table_name="engineering_template_version"
    )
    op.drop_table("engineering_template_version")
    op.drop_index("idx_engineering_template_key", table_name="engineering_template")
    op.drop_table("engineering_template")
