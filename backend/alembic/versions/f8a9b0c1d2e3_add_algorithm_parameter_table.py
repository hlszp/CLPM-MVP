"""add algorithm_parameter table

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-24

P0-B 配置化基础设施：新建 algorithm_parameter 表，存储每个指标在每个控制类型
（STABLE/SLOW/FAST/LOGIC）下的算法参数覆盖。

种子数据：3 个计算器 × 4 控制类型 = 12 行
- oscillation_rate: {"similarity_threshold": 0.4, "min_ratio": 0.05, "max_ratio": 15.0}
- fast_rate: {"ideal_settling_ratio": 1.0, "settling_tolerance": 0.0}
- accuracy_rate: {"e_max_percentile": 100}

注意：种子值与计算器硬编码常量一致（behavior-preserving），未配置时不改变现有计算结果。
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None

# 种子数据：3 个计算器 × 4 控制类型（默认值与计算器硬编码常量一致，behavior-preserving）
_SEED_METRICS = {
    "oscillation_rate": {
        "similarity_threshold": 0.4,
        "min_ratio": 0.05,
        "max_ratio": 15.0,
    },
    "fast_rate": {
        "ideal_settling_ratio": 1.0,
        "settling_tolerance": 0.0,
    },
    "accuracy_rate": {
        "e_max_percentile": 100,
    },
}

_CONTROL_TYPES = ("STABLE", "SLOW", "FAST", "LOGIC")

_SEED_DESCRIPTIONS = {
    "oscillation_rate": "振荡率算法参数（相似度阈值 + 过滤比率）",
    "fast_rate": "快速率算法参数（理想稳态比 + 稳态容差）",
    "accuracy_rate": "准确率算法参数（误差百分位截断）",
}


def upgrade() -> None:
    op.create_table(
        "algorithm_parameter",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("metric_code", sa.String(50), nullable=False),
        sa.Column("control_type", sa.String(20), nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default="{}"),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", sa.String(50), nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.CheckConstraint(
            "control_type IN ('STABLE', 'SLOW', 'FAST', 'LOGIC')",
            name="ck_algorithm_parameter_control_type",
        ),
        sa.UniqueConstraint(
            "metric_code",
            "control_type",
            name="uk_algorithm_param_code_type",
        ),
    )

    # 插入种子数据
    import uuid

    rows = []
    for metric_code, params in _SEED_METRICS.items():
        for ct in _CONTROL_TYPES:
            rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "metric_code": metric_code,
                    "control_type": ct,
                    "params": params,
                    "description": _SEED_DESCRIPTIONS[metric_code],
                    "is_enabled": True,
                    "version": 1,
                }
            )

    op.bulk_insert(
        sa.table(
            "algorithm_parameter",
            sa.column("id", UUID(as_uuid=False)),
            sa.column("metric_code", sa.String),
            sa.column("control_type", sa.String),
            sa.column("params", JSONB),
            sa.column("description", sa.String),
            sa.column("is_enabled", sa.Boolean),
            sa.column("version", sa.Integer),
        ),
        rows,
    )


def downgrade() -> None:
    op.drop_table("algorithm_parameter")
