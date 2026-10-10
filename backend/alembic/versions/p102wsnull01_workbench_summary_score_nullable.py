"""workbench_window_summary.score nullable（CAL-07 伪 0 修复）

Revision ID: p102wsnull01
Revises: 15d4a3bae833
Create Date: 2026-10-11

P1-02 CAL-07（2026-10-10）：预计算窗口内无任何有效评分时，score 落 NULL
（status=INCONCLUSIVE）而非 0.0——0.0 会被读方当真实 0 分绩效渲染。
- ck_ws_score_range(score >= 0 AND score <= 100) 对 NULL 恒通过（PG CHECK
  语义：NULL 非 FALSE 即通过），无需调整。
- downgrade 先把存量 NULL 回填 0 再恢复 NOT NULL（与旧代码写入语义一致），
  回退不失败；NULL→0 的信息损失在回退方向可接受（回到旧伪 0 口径）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p102wsnull01"
down_revision: str | Sequence[str] | None = "15d4a3bae833"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "workbench_window_summary",
        "score",
        existing_type=sa.Numeric(precision=6, scale=3),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 存量 NULL 行回填 0（旧代码伪 0 口径），否则恢复 NOT NULL 会失败
    op.execute("UPDATE workbench_window_summary SET score = 0 WHERE score IS NULL")
    op.alter_column(
        "workbench_window_summary",
        "score",
        existing_type=sa.Numeric(precision=6, scale=3),
        nullable=False,
    )
