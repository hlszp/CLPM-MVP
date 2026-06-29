"""add fast_response_rate and effective_auto_rate to kpi_snapshot_hourly

对齐 GB/T 44693.2-2024 标准，新增两个 KPI 字段：
- fast_response_rate: 快速率（控制回路对设定值变化的响应速度）
- effective_auto_rate: 有效自控率（作为综合评分乘数因子）

同时更新 metric_config 种子数据：
- GOOD_VALUE_RATE 权重改为 0（仅显示不参与加权）
- 新增 FAST_RESPONSE_RATE 指标配置

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-06-24 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. kpi_snapshot_hourly 新增列
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("fast_response_rate", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "kpi_snapshot_hourly",
        sa.Column("effective_auto_rate", sa.Numeric(5, 2), nullable=True),
    )

    # 2. 更新 metric_config：GOOD_VALUE_RATE 权重改为 0（仅显示不参与加权）
    op.execute(
        "UPDATE metric_config SET weight = 0.00, "
        "formula = 'count(pv_quality=Good) / count(*) * 100 (display only)' "
        "WHERE metric_code = 'GOOD_VALUE_RATE'"
    )

    # 3. 新增 FAST_RESPONSE_RATE 指标配置
    op.execute(
        "INSERT INTO metric_config "
        "(id, metric_code, metric_name, formula, weight, threshold, control_type, "
        "is_enabled, updated_by, updated_at, version) VALUES "
        "('00000000-0000-0000-0000-000000000407', 'FAST_RESPONSE_RATE', '快速率', "
        "'count(response_time <= threshold) / count(sp_changes) * 100', 10.00, "
        '\'{"min": 80, "max": 100, "alert": "warning"}\'::jsonb, \'FAST\', '
        "TRUE, 'admin', NOW(), 1) "
        "ON CONFLICT (metric_code) DO NOTHING"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 删除 FAST_RESPONSE_RATE 指标配置
    op.execute("DELETE FROM metric_config WHERE metric_code = 'FAST_RESPONSE_RATE'")
    # 恢复 GOOD_VALUE_RATE 权重
    op.execute(
        "UPDATE metric_config SET weight = 10.00, "
        "formula = 'count(pv_quality=Good) / count(*) * 100' "
        "WHERE metric_code = 'GOOD_VALUE_RATE'"
    )
    # 删除新增列
    op.drop_column("kpi_snapshot_hourly", "effective_auto_rate")
    op.drop_column("kpi_snapshot_hourly", "fast_response_rate")
