"""add tracker D4 effect_verified columns

整改计划 D4：A/B 闭环看板 — 整改效果验证字段补全。

IMPLEMENTED 后 T+7d 由 Celery 周期任务自动计算 A/B 对比结果并回写
本字段，使管理视图能看到整改前后效果（整改有效率统计依赖）。

新增列：
- effect_verified：整改效果验证结果
  - True  = 改善（A/B 对比中改善指标数 > 恶化指标数）
  - False = 恶化（恶化指标数 > 改善指标数）或无明显变化但已验证
  - None  = 未验证（IMPLEMENTED 后未到 T+7d，或数据不足待重试）
- effect_verified_at：验证时间（周期任务回写时设置）
- ab_compare_summary：A/B 对比结果快照（JSONB）
  含改善/恶化/持平指标数、关键 KPI 变化列表，避免每次查看都重算

新增索引：
- idx_action_tracker_effect_verified：按验证结果筛选（D4-4 整改有效率卡片）
- idx_action_tracker_status_updated：(action_status, updated_at) 复合索引
  D4-2 周期任务查询 "IMPLEMENTED 且 updated_at <= now()-7d 且 effect_verified IS NULL"
  依赖此索引避免全表扫描

Revision ID: e4f5g6h7i8j9
Revises: d1a2b3c4e5f6
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5g6h7i8j9"
down_revision: str | None = "d1a2b3c4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 新增列
    op.add_column(
        "action_tracker",
        sa.Column(
            "effect_verified",
            sa.Boolean(),
            nullable=True,
            comment="整改效果验证：True=改善 / False=恶化或无明显变化 / None=未验证",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "effect_verified_at",
            sa.DateTime(),
            nullable=True,
            comment="整改效果验证时间",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "ab_compare_summary",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
            comment="A/B 对比结果快照（改善/恶化指标数 + 关键 KPI 变化）",
        ),
    )

    # 2. 新增索引
    op.create_index(
        "idx_action_tracker_effect_verified",
        "action_tracker",
        ["effect_verified"],
    )
    op.create_index(
        "idx_action_tracker_status_updated",
        "action_tracker",
        ["action_status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_action_tracker_status_updated", table_name="action_tracker")
    op.drop_index("idx_action_tracker_effect_verified", table_name="action_tracker")
    op.drop_column("action_tracker", "ab_compare_summary")
    op.drop_column("action_tracker", "effect_verified_at")
    op.drop_column("action_tracker", "effect_verified")
