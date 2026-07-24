"""add dcs_pid_structure table

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-24

P5 PID 结构模板：新增 dcs_pid_structure 子表，1:1 关联 dcs_model.id，
承载各 DCS 型号的 PID 结构参数（P 类型、I/D 单位、微分滤波）。

设计依据：HiaMonitor 借鉴重构计划 §6（评审 P1-1：复用既有 DCS 体系，不新建表族）
关联代码：app/models/dcs_pid_structure.py

不初始化种子数据：DCS 型号未配置 PID 结构时，整定按默认假设（PROPORTION/SECONDS）
处理；用户在"系统管理 → PID 结构模板"页按型号配置后生效。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "a9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 dcs_pid_structure 表（1:1 关联 dcs_model）."""
    op.create_table(
        "dcs_pid_structure",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "dcs_model_id",
            UUID(as_uuid=False),
            sa.ForeignKey("dcs_model.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            comment="关联型号 ID（1:1，唯一）",
        ),
        sa.Column(
            "p_type",
            sa.String(20),
            nullable=False,
            server_default="PROPORTION",
            comment="比例项类型：PROPORTION(增益) / PROPORTION_BAND(比例度)",
        ),
        sa.Column(
            "i_unit",
            sa.String(10),
            nullable=False,
            server_default="SECONDS",
            comment="积分时间单位：SECONDS / MINUTES",
        ),
        sa.Column(
            "d_unit",
            sa.String(10),
            nullable=False,
            server_default="SECONDS",
            comment="微分时间单位：SECONDS / MINUTES",
        ),
        sa.Column(
            "d_filter_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="是否启用微分滤波",
        ),
        sa.Column(
            "d_filter_unit",
            sa.String(10),
            nullable=True,
            comment="微分滤波单位（d_filter_enabled=False 时为 NULL）",
        ),
        sa.Column(
            "d_filter_multiplier",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="微分滤波是否为乘法因子",
        ),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "d_filter_enabled = false OR d_filter_unit IS NOT NULL",
            name="ck_dcs_pid_structure_filter_unit",
        ),
    )
    op.create_index(
        "idx_dcs_pid_structure_model",
        "dcs_pid_structure",
        ["dcs_model_id"],
        unique=False,
    )


def downgrade() -> None:
    """删除 dcs_pid_structure 表."""
    op.drop_index("idx_dcs_pid_structure_model", table_name="dcs_pid_structure")
    op.drop_table("dcs_pid_structure")
