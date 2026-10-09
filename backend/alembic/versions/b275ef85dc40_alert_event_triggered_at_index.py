"""alert_event 触发时间单列索引——预警列表默认查询点查化

Revision ID: b275ef85dc40
Revises: 53f2f20435f1
Create Date: 2026-10-09

全站性能体检（2026-10-09）：GET /alert/events 默认查询（无过滤/纯分页，
预警页实时 Tab + 驾驶舱诊断/整定页徽标拉取）为 ORDER BY triggered_at DESC
LIMIT n。alert_event 现有索引首列均为 loop_id / rule_id / status 等，
时间排序无索引可走，生产 95.8 万行退化为全表排序，单次 ~15s——同时它是
驾驶舱页签 10s 前端超时（axios 默认超时上限）的直接来源，并长时间占用
DB 连接放大其余接口排队。

本迁移补 triggered_at DESC 单列索引：时间排序 limit 查询走 index backward
scan 点查，行为零变化。count(*) 全表扫（~200ms）保留不动。

CREATE INDEX CONCURRENTLY：生产表 ~百万行且预警引擎持续写入，非并发建索引
会阻塞写入。注意 CONCURRENTLY 失败会遗留 INVALID 索引，需 DROP 后重试。
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b275ef85dc40"
down_revision = "53f2f20435f1"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_alert_event_triggered_at"
INDEX_NAME_ST = "idx_alert_event_status_time"
TABLE = "alert_event"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} ON {TABLE} (triggered_at DESC)"
        )
        # 复合 (status, triggered_at DESC)：预警实时 Tab（status=ACTIVE 过滤+时间
        # 排序）与工作台总览预警卡（状态优先两段式查询）各段索引直达
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME_ST}"
            f" ON {TABLE} (status, triggered_at DESC)"
        )
    # 归档表（结构含约束/索引全量复制；2026-10-09 用户裁决：超 31 天事件归档，
    # 主表保持滚动窗口。首批迁移由 Beat 任务 alert_event_archive 分批执行）
    op.execute(f"CREATE TABLE IF NOT EXISTS alert_event_archive (LIKE {TABLE} INCLUDING ALL)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_event_archive")
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME_ST}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
