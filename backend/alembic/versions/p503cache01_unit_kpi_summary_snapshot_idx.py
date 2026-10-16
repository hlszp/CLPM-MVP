"""unit_kpi_summary snapshot_time 单列索引（P5-03，03 号文 §8）

用途（消费方均在 P5-03 域内）：

1. ``app/services/agg_cache.py::data_revision_suffix`` —— 数据源代数取
   ``max(snapshot_time)``（每个聚合请求一次，"新窗落库即换缓存 key"）。
   无索引时 max() 退化为全表聚合，千回路×720h 量级下成为每请求热点；
   有索引后为反向索引扫描（微秒级）。
2. ``app/services/workbench_precalc.py`` 小时可合并统计复用——60d 跨窗
   单查询按 ``snapshot_time`` 范围过滤全部 UNIT 行（node_id IN + 时间窗），
   与既有 ix_unit_kpi_summary_node_time 二选一由规划器按选择性决定。

downgrade 直接删索引（无数据语义）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p503cache01"
down_revision: str | None = "p502attm01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "unit_kpi_summary"
_INDEX = "ix_unit_kpi_summary_snapshot_time"


def upgrade() -> None:
    op.create_index(_INDEX, _TABLE, ["snapshot_time"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
