"""align DDS v4.1 fields: rename + add columns

对齐 DDS v4.1 / FDS v5.1 / UIUX v5.3 设计文档，统一字段命名与补齐缺失字段。

变更内容：
1. loop_ledger: level → importance_level (NOT NULL DEFAULT 2) + 新增 include_in_evaluation
2. metric_config: 新增 grading_thresholds (JSONB)
3. unit_kpi_summary: steady_rate → stability_rate, fast_response_rate → fast_rate
   + 新增 excluded_loops, status
4. kpi_snapshot_hourly: fast_response_rate → fast_rate, stiction_coeff → stiction_index,
   steady_state_time → settling_time, output_travel_index → output_trip_index
5. kpi_snapshot_custom: 同上 4 字段重命名
6. kpi_node_snapshot_hourly/daily/monthly: 同上 4 字段重命名

Revision ID: p9r0s1t2u3v4
Revises: n7q8r9s0t1u2
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "p9r0s1t2u3v4"
down_revision = "n7q8r9s0t1u2"
branch_labels = None
depends_on = None


# 需要重命名的 4 个字段（在 5 张表中统一重命名）
_RENAMED_FIELDS = [
    ("fast_response_rate", "fast_rate"),
    ("stiction_coeff", "stiction_index"),
    ("steady_state_time", "settling_time"),
    ("output_travel_index", "output_trip_index"),
]

# 包含上述 4 字段的表
_TABLES_WITH_RENAMED_FIELDS = [
    "kpi_snapshot_hourly",
    "kpi_snapshot_custom",
    "kpi_node_snapshot_hourly",
    "kpi_node_snapshot_daily",
    "kpi_node_snapshot_monthly",
]


def upgrade() -> None:
    # === 1. loop_ledger: level → importance_level + 新增 include_in_evaluation ===
    # 先删除旧索引和约束（v4.0 建表时可能未创建 level 约束，用 IF EXISTS 兼容）
    op.execute("DROP INDEX IF EXISTS idx_loop_ledger_level")
    op.execute("ALTER TABLE loop_ledger DROP CONSTRAINT IF EXISTS ck_loop_ledger_level")
    # 重命名列
    op.alter_column(
        "loop_ledger",
        "level",
        new_column_name="importance_level",
        existing_type=sa.SmallInteger(),
        existing_nullable=True,
        existing_server_default=sa.text("3"),
    )
    # 修改为 NOT NULL DEFAULT 2
    op.alter_column(
        "loop_ledger",
        "importance_level",
        existing_type=sa.SmallInteger(),
        nullable=False,
        server_default=sa.text("2"),
    )
    # 新增 CHECK 约束
    op.create_check_constraint(
        "ck_loop_ledger_importance_level",
        "loop_ledger",
        "importance_level IN (1, 2, 3)",
    )
    # 新增索引
    op.create_index(
        "idx_loop_ledger_importance_level",
        "loop_ledger",
        ["importance_level"],
    )
    # 新增 include_in_evaluation 字段
    op.add_column(
        "loop_ledger",
        sa.Column(
            "include_in_evaluation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="是否参与评估：true=参与综合评分与装置级聚合，false=仅计算单回路 KPI 不参与聚合",
        ),
    )

    # === 2. metric_config: 新增 grading_thresholds ===
    op.add_column(
        "metric_config",
        sa.Column(
            "grading_thresholds",
            JSONB,
            nullable=True,
            comment="5 级性能定级阈值（EXCELLENT/GOOD/FAIR/WARNING/POOR）",
        ),
    )

    # === 3. unit_kpi_summary: 字段重命名 + 新增字段 ===
    op.alter_column(
        "unit_kpi_summary",
        "steady_rate",
        new_column_name="stability_rate",
        existing_type=sa.Numeric(5, 2),
        existing_nullable=True,
    )
    op.alter_column(
        "unit_kpi_summary",
        "fast_response_rate",
        new_column_name="fast_rate",
        existing_type=sa.Numeric(5, 2),
        existing_nullable=True,
    )
    op.add_column(
        "unit_kpi_summary",
        sa.Column(
            "excluded_loops",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "unit_kpi_summary",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'SUCCESS'"),
            comment="聚合状态: SUCCESS/PARTIAL/EMPTY",
        ),
    )
    op.create_check_constraint(
        "ck_unit_kpi_summary_status",
        "unit_kpi_summary",
        "status IN ('SUCCESS', 'PARTIAL', 'EMPTY')",
    )

    # === 4-6. 5 张表统一重命名 4 个字段 ===
    for table_name in _TABLES_WITH_RENAMED_FIELDS:
        for old_name, new_name in _RENAMED_FIELDS:
            # 检查列是否存在（某些表可能没有所有 4 个字段）
            # kpi_node_snapshot_* 有 stiction_coeff/settling_time/output_trip_index 但没有 fast_response_rate 的情况
            # 实际上所有 5 张表都有这 4 个字段
            op.alter_column(
                table_name,
                old_name,
                new_column_name=new_name,
                existing_type=sa.Numeric(5, 2) if old_name != "steady_state_time" and old_name != "output_travel_index" else sa.Numeric(8, 2),
                existing_nullable=True,
            )


def downgrade() -> None:
    # === 反向：5 张表字段重命名回旧名 ===
    for table_name in _TABLES_WITH_RENAMED_FIELDS:
        for old_name, new_name in _RENAMED_FIELDS:
            op.alter_column(
                table_name,
                new_name,
                new_column_name=old_name,
                existing_type=sa.Numeric(5, 2) if old_name != "steady_state_time" and old_name != "output_travel_index" else sa.Numeric(8, 2),
                existing_nullable=True,
            )

    # === 反向：unit_kpi_summary ===
    op.drop_constraint("ck_unit_kpi_summary_status", "unit_kpi_summary", type_="check")
    op.drop_column("unit_kpi_summary", "status")
    op.drop_column("unit_kpi_summary", "excluded_loops")
    op.alter_column(
        "unit_kpi_summary",
        "fast_rate",
        new_column_name="fast_response_rate",
        existing_type=sa.Numeric(5, 2),
        existing_nullable=True,
    )
    op.alter_column(
        "unit_kpi_summary",
        "stability_rate",
        new_column_name="steady_rate",
        existing_type=sa.Numeric(5, 2),
        existing_nullable=True,
    )

    # === 反向：metric_config ===
    op.drop_column("metric_config", "grading_thresholds")

    # === 反向：loop_ledger ===
    op.drop_column("loop_ledger", "include_in_evaluation")
    op.execute("DROP INDEX IF EXISTS idx_loop_ledger_importance_level")
    op.execute("ALTER TABLE loop_ledger DROP CONSTRAINT IF EXISTS ck_loop_ledger_importance_level")
    op.alter_column(
        "loop_ledger",
        "importance_level",
        existing_type=sa.SmallInteger(),
        nullable=True,
        server_default=sa.text("3"),
    )
    op.alter_column(
        "loop_ledger",
        "importance_level",
        new_column_name="level",
        existing_type=sa.SmallInteger(),
        existing_nullable=True,
        existing_server_default=sa.text("3"),
    )
    op.create_check_constraint(
        "ck_loop_ledger_level",
        "loop_ledger",
        "level IS NULL OR level IN (1, 2, 3)",
    )
    op.create_index("idx_loop_ledger_level", "loop_ledger", ["level"])
