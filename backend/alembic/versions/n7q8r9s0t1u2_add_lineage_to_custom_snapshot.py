"""add sampling_freq/quality_policy to kpi_snapshot_custom

P2 #29 B6: 补齐自定义任务快照表的数据血缘字段。

背景：
- 标准任务快照表 ``kpi_snapshot_hourly`` 已包含 5 个数据血缘字段
  （``sampling_freq`` / ``quality_policy`` / ``valid_rate`` /
  ``confidence_level`` / ``data_lineage``）
- 自定义任务快照表 ``kpi_snapshot_custom`` 仅包含后 3 个，缺少
  ``sampling_freq`` / ``quality_policy`` 两个字段
- ``_persist_snapshot`` 在写入 custom 表前显式剔除这两个字段，
  导致自定义任务的数据血缘追溯能力弱于标准任务

设计依据：
- DDS §2.14：自定义任务快照应支持完整数据血缘审计
- PRD §4.3.7.B：自定义任务需可追溯至原始数据来源
- GB/T 44693.2-2024 §6.5：评估结果应记录采样频率与质量策略

本迁移在 ``kpi_snapshot_custom`` 表新增两列：
- ``sampling_freq`` VARCHAR(10) NULL（如 "1s" / "1min"）
- ``quality_policy`` VARCHAR(30) NULL（如 "TDengine" / "OPC_DA"）

Revision ID: n7q8r9s0t1u2
Revises: m6q7r8s9t0u1
Create Date: 2026-07-04 10:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "n7q8r9s0t1u2"
down_revision = "m6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kpi_snapshot_custom",
        sa.Column(
            "sampling_freq",
            sa.String(10),
            nullable=True,
            comment="P2 #29: 数据采样频率（与 kpi_snapshot_hourly.sampling_freq 对齐）",
        ),
    )
    op.add_column(
        "kpi_snapshot_custom",
        sa.Column(
            "quality_policy",
            sa.String(30),
            nullable=True,
            comment="P2 #29: 质量策略（与 kpi_snapshot_hourly.quality_policy 对齐）",
        ),
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_custom.sampling_freq IS "
        "'P2 #29: 数据采样频率（如 1s / 1min）'"
    )
    op.execute(
        "COMMENT ON COLUMN kpi_snapshot_custom.quality_policy IS "
        "'P2 #29: 质量策略（如 TDengine / OPC_DA）'"
    )


def downgrade() -> None:
    op.drop_column("kpi_snapshot_custom", "quality_policy")
    op.drop_column("kpi_snapshot_custom", "sampling_freq")
