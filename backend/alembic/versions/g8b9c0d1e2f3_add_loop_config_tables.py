"""add loop config tables and loop_ledger fields

新建 3 张配置表 + loop_ledger 扩展 3 字段，对齐国标 GB/T 44693.2-2024：
- loop_mode_mapping：回路投用定义（MODE 值到控制模式的映射）
- loop_type_weight：回路类型权重（稳定型/慢速型/快速型/逻辑型，附表1）
- loop_level_weight：回路级别权重（一级3/二级2/三级1，附表2）
- loop_ledger 加 level/modeattr_tag_id/data_retention_days 字段

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-24 17:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from decimal import Decimal
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "g8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # 1. 新建 loop_mode_mapping（投用定义配置）
    # =========================================================================
    op.create_table(
        "loop_mode_mapping",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("loop_id", UUID(as_uuid=False),
                  sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("mode_value", sa.Integer, nullable=False,
                  comment="DCS 返回的 MODE 值"),
        sa.Column("mode_label", sa.String(20), nullable=False,
                  comment="控制模式：AUTO/CAS/REMOTE/APC/MANUAL"),
        sa.Column("is_auto", sa.Boolean, nullable=False, server_default=sa.text("FALSE"),
                  comment="是否算自动控制"),
        sa.Column("is_effective", sa.Boolean, nullable=False, server_default=sa.text("FALSE"),
                  comment="是否算有效自动（不饱和）"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("loop_id", "mode_value", name="uk_loop_mode_mapping_loop_mode"),
        comment="回路投用定义：MODE 值到控制模式的映射",
    )
    op.create_index("idx_loop_mode_mapping_loop_id", "loop_mode_mapping", ["loop_id"])

    # =========================================================================
    # 2. 新建 loop_type_weight（回路类型权重，国标附表1）
    # =========================================================================
    op.create_table(
        "loop_type_weight",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("loop_type", sa.String(20), nullable=False, unique=True,
                  comment="回路类型：STABLE/SLOW/FAST/LOGIC"),
        sa.Column("type_name", sa.String(50), nullable=False, comment="类型名称"),
        sa.Column("weight_a", sa.Numeric(3, 2), nullable=False,
                  comment="准确率权重 a"),
        sa.Column("weight_f", sa.Numeric(3, 2), nullable=False,
                  comment="快速率权重 f"),
        sa.Column("weight_s", sa.Numeric(3, 2), nullable=False,
                  comment="平稳率权重 s"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        comment="回路类型权重（对齐 GB/T 44693.2-2024 附表1）",
    )

    # 初始数据（国标附表1）
    op.bulk_insert(
        sa.table(
            "loop_type_weight",
            sa.column("loop_type", sa.String),
            sa.column("type_name", sa.String),
            sa.column("weight_a", sa.Numeric),
            sa.column("weight_f", sa.Numeric),
            sa.column("weight_s", sa.Numeric),
            sa.column("description", sa.Text),
        ),
        [
            {
                "loop_type": "STABLE",
                "type_name": "稳定型",
                "weight_a": Decimal("0.2"),
                "weight_f": Decimal("0.3"),
                "weight_s": Decimal("0.5"),
                "description": "温度/压力控制，a/f/s 相似",
            },
            {
                "loop_type": "SLOW",
                "type_name": "慢速型",
                "weight_a": Decimal("0.3"),
                "weight_f": Decimal("0.1"),
                "weight_s": Decimal("0.6"),
                "description": "缓慢调节，f 偏小",
            },
            {
                "loop_type": "FAST",
                "type_name": "快速型",
                "weight_a": Decimal("0.2"),
                "weight_f": Decimal("0.5"),
                "weight_s": Decimal("0.3"),
                "description": "副回路/速度控制，f 偏大",
            },
            {
                "loop_type": "LOGIC",
                "type_name": "逻辑型",
                "weight_a": Decimal("0.0"),
                "weight_f": Decimal("0.5"),
                "weight_s": Decimal("0.6"),
                "description": "逻辑规则控制，a 偏小",
            },
        ],
    )

    # =========================================================================
    # 3. 新建 loop_level_weight（回路级别权重，国标附表2）
    # =========================================================================
    op.create_table(
        "loop_level_weight",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("level", sa.Integer, nullable=False, unique=True,
                  comment="回路级别：1/2/3"),
        sa.Column("level_name", sa.String(50), nullable=False, comment="级别名称"),
        sa.Column("weight", sa.Numeric(3, 1), nullable=False,
                  comment="级别权重：3.0/2.0/1.0"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
        comment="回路级别权重（对齐 GB/T 44693.2-2024 附表2）",
    )

    # 初始数据（国标附表2）
    op.bulk_insert(
        sa.table(
            "loop_level_weight",
            sa.column("level", sa.Integer),
            sa.column("level_name", sa.String),
            sa.column("weight", sa.Numeric),
            sa.column("description", sa.Text),
        ),
        [
            {
                "level": 1,
                "level_name": "一级",
                "weight": Decimal("3.0"),
                "description": "决定性影响：负荷控制/联锁相关",
            },
            {
                "level": 2,
                "level_name": "二级",
                "weight": Decimal("2.0"),
                "description": "辅助保障：稳定性/设备安全",
            },
            {
                "level": 3,
                "level_name": "三级",
                "weight": Decimal("1.0"),
                "description": "次要辅助：维持辅助设备运行",
            },
        ],
    )

    # =========================================================================
    # 4. loop_ledger 扩展字段
    # =========================================================================
    op.add_column(
        "loop_ledger",
        sa.Column("level", sa.SmallInteger, nullable=True, server_default=sa.text("3"),
                  comment="回路级别 1/2/3（默认3，对齐国标附表2）"),
    )
    op.add_column(
        "loop_ledger",
        sa.Column("modeattr_tag_id", UUID(as_uuid=False),
                  sa.ForeignKey("tag_registry.id", ondelete="RESTRICT"),
                  nullable=True,
                  comment="APC 识别位号 ID（位号值为 program 时算自动控制）"),
    )
    op.add_column(
        "loop_ledger",
        sa.Column("data_retention_days", sa.Integer, nullable=True,
                  comment="数据保存周期（天），NULL 表示用系统默认"),
    )

    # 为 level 字段创建索引（常用于筛选和聚合）
    op.create_index("idx_loop_ledger_level", "loop_ledger", ["level"])


def downgrade() -> None:
    # loop_ledger 字段
    op.drop_index("idx_loop_ledger_level", table_name="loop_ledger")
    op.drop_column("loop_ledger", "data_retention_days")
    op.drop_column("loop_ledger", "modeattr_tag_id")
    op.drop_column("loop_ledger", "level")

    # 表
    op.drop_table("loop_level_weight")
    op.drop_table("loop_type_weight")
    op.drop_index("idx_loop_mode_mapping_loop_id", table_name="loop_mode_mapping")
    op.drop_table("loop_mode_mapping")
