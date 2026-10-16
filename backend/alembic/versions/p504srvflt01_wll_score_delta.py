"""workbench_loop_latest 增加 prev_score/score_delta（P5-04 服务端劣化排序）

用途（消费方在 P5-04 域内）：

``GET /loops/monitor?sortBy=scoreDelta`` —— 驾驶舱回路状态墙"劣化降序"
排序键（较昨日评分增量升序，无增量排最后）改为服务端执行。排序数据源
读本表预计算列（5min Celery ``workbench-loop-latest`` 全量重算），替代
每请求对 ``kpi_snapshot_hourly`` 的全表 DISTINCT ON 昨日基线查询
（与 2026-10-03 评分排序读表提速同构）。

列语义：
- ``prev_score``：今日 0 点前最新一条参评快照评分（无则 NULL）；
- ``score_delta``：``score - prev_score``（任一为 NULL 则 NULL）。

downgrade 删列（纯预计算派生列，无数据语义）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p504srvflt01"
down_revision: str | None = "p503cache01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "workbench_loop_latest"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("prev_score", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column("score_delta", sa.Numeric(7, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "score_delta")
    op.drop_column(_TABLE, "prev_score")
