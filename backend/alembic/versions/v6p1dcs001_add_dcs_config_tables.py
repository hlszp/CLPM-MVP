"""add DCS config tables and loop_ledger.dcs_model_id

新增 4 张 DCS 配置表 + loop_ledger 扩展字段，实现配置驱动的 MODE 映射：
- dcs_vendor: DCS 厂商品牌（5 家：和利时/中控/霍尼韦尔/横河/艾默生）
- dcs_model: DCS 型号（全局唯一 code，关联品牌）
- mode_definition: 标准 MODE 定义（0-4，替代硬编码 AUTO_MODES）
- dcs_mode_mapping: MODE 映射矩阵（NULL=本系统默认，非 NULL=具体型号映射）
- loop_ledger.dcs_model_id: 关联到具体型号（NULL=使用本系统默认）

种子数据：
- 5 个品牌 + 5 个主流型号
- mode_definition 5 行（0-4，is_auto 标志）
- dcs_mode_mapping 本系统默认 5 行（1:1 映射）
- 每个型号 5 行映射（默认 1:1，可后续按实际品牌调整）

设计依据：
- DDS §3.1 / 算法说明 §4.0.3
- 用户需求：映射关系配置驱动、型号全局唯一、矩阵表、整合数据接入页面

Revision ID: v6p1dcs001
Revises: w3b4c5d6e7f8
Create Date: 2026-07-09
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "v6p1dcs001"
down_revision = "w3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # 1. dcs_vendor（DCS 厂商品牌）
    # =========================================================================
    op.create_table(
        "dcs_vendor",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("code", sa.String(50), nullable=False, unique=True, comment="品牌代码"),
        sa.Column("name", sa.String(100), nullable=False, comment="品牌中文名"),
        sa.Column("name_en", sa.String(100), nullable=True, comment="品牌英文名"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        comment="DCS 厂商品牌",
    )
    op.create_index("idx_dcs_vendor_sort", "dcs_vendor", ["sort_order"])

    # 种子数据：5 家主流 DCS 厂商
    op.bulk_insert(
        sa.table(
            "dcs_vendor",
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("name_en", sa.String),
            sa.column("description", sa.String),
            sa.column("sort_order", sa.Integer),
        ),
        [
            {"code": "hollysys", "name": "和利时", "name_en": "HollySys",
             "description": "北京和利时系统工程股份有限公司", "sort_order": 1},
            {"code": "supcon", "name": "中控", "name_en": "SUPCON",
             "description": "浙江中控技术股份有限公司", "sort_order": 2},
            {"code": "honeywell", "name": "霍尼韦尔", "name_en": "Honeywell",
             "description": "霍尼韦尔国际公司", "sort_order": 3},
            {"code": "yokogawa", "name": "横河", "name_en": "Yokogawa",
             "description": "横河电机株式会社", "sort_order": 4},
            {"code": "emerson", "name": "艾默生", "name_en": "Emerson",
             "description": "艾默生电气公司", "sort_order": 5},
        ],
    )

    # =========================================================================
    # 2. dcs_model（DCS 型号，全局唯一 code）
    # =========================================================================
    op.create_table(
        "dcs_model",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=False),
            sa.ForeignKey("dcs_vendor.id", ondelete="RESTRICT"),
            nullable=False,
            comment="所属品牌 ID",
        ),
        sa.Column("code", sa.String(100), nullable=False, unique=True, comment="型号代码（全局唯一）"),
        sa.Column("name", sa.String(200), nullable=False, comment="型号名称"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        comment="DCS 型号（全局唯一）",
    )
    op.create_index("idx_dcs_model_vendor", "dcs_model", ["vendor_id"])
    op.create_index("idx_dcs_model_sort", "dcs_model", ["sort_order"])

    # 查询品牌 ID 以建立型号关联
    conn = op.get_bind()
    vendors = {row.code: str(row.id) for row in conn.execute(sa.text("SELECT id, code FROM dcs_vendor"))}

    # 种子数据：每个品牌一个主流型号
    models_data = [
        {"vendor_code": "hollysys", "model_code": "hollysys-macs",
         "model_name": "MACS 系统", "description": "和利时 MACS V 集散控制系统"},
        {"vendor_code": "supcon", "model_code": "supcon-ecs700",
         "model_name": "ECS-700", "description": "中控 ECS-700 集散控制系统"},
        {"vendor_code": "honeywell", "model_code": "honeywell-experion",
         "model_name": "Experion PKS", "description": "霍尼韦尔 Experion 过程知识系统"},
        {"vendor_code": "yokogawa", "model_code": "yokogawa-centum",
         "model_name": "CENTUM CS3000", "description": "横河 CENTUM CS3000 集散控制系统"},
        {"vendor_code": "emerson", "model_code": "emerson-deltav",
         "model_name": "DeltaV", "description": "艾默生 DeltaV 集散控制系统"},
    ]
    op.bulk_insert(
        sa.table(
            "dcs_model",
            sa.column("vendor_id", UUID),
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("description", sa.String),
            sa.column("sort_order", sa.Integer),
        ),
        [
            {
                "vendor_id": vendors[m["vendor_code"]],
                "code": m["model_code"],
                "name": m["model_name"],
                "description": m["description"],
                "sort_order": idx + 1,
            }
            for idx, m in enumerate(models_data)
        ],
    )

    # =========================================================================
    # 3. mode_definition（标准 MODE 定义，替代硬编码 AUTO_MODES）
    # =========================================================================
    op.create_table(
        "mode_definition",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("standard_mode", sa.Integer, nullable=False, unique=True, comment="标准 MODE 值 0-4"),
        sa.Column("label_zh", sa.String(20), nullable=False, comment="中文标签"),
        sa.Column("label_en", sa.String(20), nullable=False, comment="英文标签"),
        sa.Column("is_auto", sa.Boolean, nullable=False, server_default=sa.text("FALSE"),
                  comment="是否计入自控率"),
        sa.Column("color", sa.String(20), nullable=False, server_default=sa.text("'#999999'"),
                  comment="图表配色"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint("standard_mode IN (0, 1, 2, 3, 4)", name="ck_mode_definition_standard_mode"),
        comment="标准 MODE 定义（配置驱动，替代硬编码 AUTO_MODES）",
    )
    op.create_index("idx_mode_definition_sort", "mode_definition", ["sort_order"])

    # 种子数据：5 行标准 MODE 定义
    op.bulk_insert(
        sa.table(
            "mode_definition",
            sa.column("standard_mode", sa.Integer),
            sa.column("label_zh", sa.String),
            sa.column("label_en", sa.String),
            sa.column("is_auto", sa.Boolean),
            sa.column("color", sa.String),
            sa.column("sort_order", sa.Integer),
            sa.column("description", sa.String),
        ),
        [
            {"standard_mode": 0, "label_zh": "手动", "label_en": "MANUAL",
             "is_auto": False, "color": "#d4380d", "sort_order": 0,
             "description": "操作员直接操作 OP"},
            {"standard_mode": 1, "label_zh": "自动", "label_en": "AUTO",
             "is_auto": True, "color": "#52c41a", "sort_order": 1,
             "description": "单回路 PID 自动控制"},
            {"standard_mode": 2, "label_zh": "串级", "label_en": "CAS",
             "is_auto": True, "color": "#1890ff", "sort_order": 2,
             "description": "主-副回路串级控制"},
            {"standard_mode": 3, "label_zh": "远程", "label_en": "REMOTE",
             "is_auto": True, "color": "#722ed1", "sort_order": 3,
             "description": "SCADA/上位机远程设定"},
            {"standard_mode": 4, "label_zh": "先控", "label_en": "APC",
             "is_auto": True, "color": "#13c2c2", "sort_order": 4,
             "description": "先进过程控制（MPC 等）"},
        ],
    )

    # =========================================================================
    # 4. dcs_mode_mapping（MODE 映射矩阵）
    # =========================================================================
    op.create_table(
        "dcs_mode_mapping",
        sa.Column(
            "id", UUID(as_uuid=False), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "dcs_model_id",
            UUID(as_uuid=False),
            sa.ForeignKey("dcs_model.id", ondelete="CASCADE"),
            nullable=True,
            comment="关联型号 ID；NULL=本系统默认映射",
        ),
        sa.Column("standard_mode", sa.Integer, nullable=False, comment="本系统标准 MODE 值 0-4"),
        sa.Column("raw_mode_value", sa.Integer, nullable=False, comment="该型号 DCS 实际 MODE 值"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        comment="DCS MODE 值映射矩阵",
    )
    # 型号映射唯一约束（partial unique index）
    op.create_index(
        "uk_dcs_mode_mapping_model_mode",
        "dcs_mode_mapping",
        ["dcs_model_id", "standard_mode"],
        unique=True,
        postgresql_where=sa.text("dcs_model_id IS NOT NULL"),
    )
    # 本系统默认唯一约束（partial unique index）
    op.create_index(
        "uk_dcs_mode_mapping_default",
        "dcs_mode_mapping",
        ["standard_mode"],
        unique=True,
        postgresql_where=sa.text("dcs_model_id IS NULL"),
    )
    op.create_index(
        "idx_dcs_mode_mapping_model_raw", "dcs_mode_mapping", ["dcs_model_id", "raw_mode_value"]
    )

    # 种子数据 1：本系统默认映射（dcs_model_id=NULL，1:1 映射）
    op.bulk_insert(
        sa.table(
            "dcs_mode_mapping",
            sa.column("dcs_model_id", UUID),
            sa.column("standard_mode", sa.Integer),
            sa.column("raw_mode_value", sa.Integer),
            sa.column("description", sa.String),
        ),
        [
            {"dcs_model_id": None, "standard_mode": sm, "raw_mode_value": sm,
             "description": "本系统默认映射（1:1）"}
            for sm in range(5)
        ],
    )

    # 种子数据 2：每个型号 5 行映射（默认 1:1，可后续按实际品牌调整）
    model_ids = {row.code: str(row.id) for row in conn.execute(sa.text("SELECT id, code FROM dcs_model"))}
    model_mappings = []
    for model_code, model_id in model_ids.items():
        for sm in range(5):
            model_mappings.append({
                "dcs_model_id": model_id,
                "standard_mode": sm,
                "raw_mode_value": sm,
                "description": f"{model_code} 默认映射（1:1，可按实际 DCS 调整）",
            })
    op.bulk_insert(
        sa.table(
            "dcs_mode_mapping",
            sa.column("dcs_model_id", UUID),
            sa.column("standard_mode", sa.Integer),
            sa.column("raw_mode_value", sa.Integer),
            sa.column("description", sa.String),
        ),
        model_mappings,
    )

    # =========================================================================
    # 5. loop_ledger 扩展字段：dcs_model_id
    # =========================================================================
    op.add_column(
        "loop_ledger",
        sa.Column(
            "dcs_model_id",
            UUID(as_uuid=False),
            sa.ForeignKey("dcs_model.id", ondelete="SET NULL"),
            nullable=True,
            comment="关联 DCS 型号 ID；NULL=使用本系统默认 MODE 映射",
        ),
    )
    op.create_index("idx_loop_ledger_dcs_model", "loop_ledger", ["dcs_model_id"])


def downgrade() -> None:
    # loop_ledger 字段
    op.drop_index("idx_loop_ledger_dcs_model", table_name="loop_ledger")
    op.drop_column("loop_ledger", "dcs_model_id")

    # dcs_mode_mapping
    op.drop_index("idx_dcs_mode_mapping_model_raw", table_name="dcs_mode_mapping")
    op.drop_index("uk_dcs_mode_mapping_default", table_name="dcs_mode_mapping")
    op.drop_index("uk_dcs_mode_mapping_model_mode", table_name="dcs_mode_mapping")
    op.drop_table("dcs_mode_mapping")

    # mode_definition
    op.drop_index("idx_mode_definition_sort", table_name="mode_definition")
    op.drop_table("mode_definition")

    # dcs_model
    op.drop_index("idx_dcs_model_sort", table_name="dcs_model")
    op.drop_index("idx_dcs_model_vendor", table_name="dcs_model")
    op.drop_table("dcs_model")

    # dcs_vendor
    op.drop_index("idx_dcs_vendor_sort", table_name="dcs_vendor")
    op.drop_table("dcs_vendor")
