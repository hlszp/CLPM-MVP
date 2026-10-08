"""kpi_snapshot_hourly 复合索引 (loop_id, ts_end)——per-loop latest 点查化

Revision ID: 53f2f20435f1
Revises: c9bf79b6868a
Create Date: 2026-10-08

回路监视/驾驶舱性能批（2026-10-08）：
kpi_snapshot_hourly 现有索引均为单列（loop_id / ts_start / status），全系统的
per-loop latest 查询（DISTINCT ON (loop_id) ORDER BY loop_id, ts_end DESC——
/loops/monitor 排序子查询、等级筛选、行内 kpiSummary、昨日基线、aggregate 聚合）
都退化为"每回路取全部行组内排序"。生产 1209 回路 × ~900 行/回路 ≈ 百万行处理，
单次请求秒级。

本迁移补 (loop_id, ts_end) 复合索引：DISTINCT ON 每组首行走 index backward
scan 点查，全部上述查询同批受益，行为零变化（仍实时，无预计算延迟）。
与驾驶舱 P1 的 workbench_loop_latest 预计算表互补：实时性敏感的列表查询走
本索引，实时性不敏感的 fitness 面走预计算表。

CREATE INDEX CONCURRENTLY：生产表 ~百万行且持续写入（每小时快照 UPSERT），
非并发建索引会阻塞写入。注意 CONCURRENTLY 失败会遗留 INVALID 索引，
需 DROP 后重试（部署指令已含检查步骤）。
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "53f2f20435f1"
down_revision = "c9bf79b6868a"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_kpi_snapshot_loop_ts_end"
TABLE = "kpi_snapshot_hourly"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} ON {TABLE} (loop_id, ts_end)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
