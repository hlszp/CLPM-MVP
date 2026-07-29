"""timestamp defaults to UTC（server_default now() 时区统一）

server_default=func.now() 在 PG timezone=Asia/Shanghai 下写入 +8 墙钟，
与全链路 naive UTC 约定（写入侧 Python 显式 naive UTC、读取侧补 Z 解析）
差 8 小时：diagnosis_task.triggered_at 显示为未来时间、耗时计算为负。

迁移内容：
1. 6 个列的 server_default 改为 (now() AT TIME ZONE 'UTC')：
   - diagnosis_task.triggered_at
   - diagnosis_trigger_log.triggered_at（如存在）
   - diagnosis_approval.requested_at（如存在）
   - kpi_snapshot_hourly.created_at
   - clpm_metric_data_requirement.updated_at
   - unit_kpi_summary.created_at
2. 历史数据修正：diagnosis_task.triggered_at 全部由 PG now() 写入
   （Python 代码从不显式赋值），统一 -8h 对齐 naive UTC。
   其他表历史数据不搬移（Python 显式写入与 server_default 混用，
   无法安全区分，只保证新数据正确）。

revision: h8b9c0d1e2f3
down_revision: g7a8b9c0d1e2
"""

from alembic import op

revision = "h8b9c0d1e2f3"
down_revision = "g7a8b9c0d1e2"
branch_labels = None
depends_on = None

# (table, column)
_COLUMNS = [
    ("diagnosis_task", "triggered_at"),
    ("kpi_snapshot_custom", "created_at"),
    ("clpm_metric_data_requirement", "updated_at"),
    ("unit_kpi_summary", "created_at"),
]

# diagnosis.py 中另两处 server_default=func.now()——表名以模型为准，
# 若不存在则跳过（防御）
_OPTIONAL_COLUMNS = [
    ("diagnosis_tag", "triggered_at"),
    ("diagnosis_config_change", "requested_at"),
]

_UTC_DEFAULT = "(now() AT TIME ZONE 'UTC')"


def _table_exists(conn, name: str) -> bool:
    from sqlalchemy import text

    return bool(
        conn.execute(
            text("SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=:name"),
            {"name": name},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    columns = list(_COLUMNS)
    for table, column in _OPTIONAL_COLUMNS:
        if _table_exists(conn, table):
            columns.append((table, column))
    for table, column in columns:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {_UTC_DEFAULT}")
    # 历史数据修正：diagnosis_task.triggered_at 全部由 PG now()（+8 墙钟）写入
    op.execute("UPDATE diagnosis_task SET triggered_at = triggered_at - INTERVAL '8 hours'")


def downgrade() -> None:
    conn = op.get_bind()
    columns = list(_COLUMNS)
    for table, column in _OPTIONAL_COLUMNS:
        if _table_exists(conn, table):
            columns.append((table, column))
    for table, column in columns:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()")
    op.execute("UPDATE diagnosis_task SET triggered_at = triggered_at + INTERVAL '8 hours'")
