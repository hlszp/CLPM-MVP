"""workbench_window_summary 增加 distribution JSONB 列

G-评估批次（M2 · F-EV-01~03）：为 trend 块的等级分布 / 控制模式分布 /
数据质量 / 分项斜率提供预计算存储位，沿用 workbench_window_summary 的
precalc 架构（本 service 不直接查 TDengine，分布数据由 precalc 任务或
seed 写入此列）。

结构（JSONB）：
{
  "level_dist":  [{"label","count","color","stripe"}],   # 回路等级分布
  "mode_dist":   [{"label","count","color"}],            # 控制模式分布
  "data_quality":[{"label","count","level"}],            # 数据质量
  "metric_slopes":[{"metric","delta","direction"}]       # 分项近 24h 变化量
}

Revision ID: b7e8f9a0c1d2
Revises: a9229d815d0d
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e8f9a0c1d2"
down_revision: str | Sequence[str] | None = "a9229d815d0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workbench_window_summary "
        "ADD COLUMN IF NOT EXISTS distribution JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE workbench_window_summary DROP COLUMN IF EXISTS distribution")
