"""converge ORM/DB schema drift — 模型与库结构对齐（P2 数据正确性整改）

收敛 4 类漂移：审计日志 target_id 类型、uk_* 唯一约束口径、
缺失索引补登、时间戳列 NOT NULL。

`alembic check` 实测暴露 4 类结构性漂移，本迁移与同批模型改动共同收敛：

1. ``sys_audit_log.target_id`` 库 VARCHAR(36) vs 模型 UUID(as_uuid=False)
   → 以库为准，模型改 ``String(36)``（app/models/audit.py）。
   target_id 可能是 loop_id / user_id / 报表 id / 任务 id 等业务标识，
   并不保证是 UUID，VARCHAR(36) 更安全。**无需 DDL**（模型向库对齐）。

2. ``loop_ledger`` / ``plant_node`` / ``sys_user`` 的
   ``created_at`` / ``updated_at`` 库 nullable=True vs 模型 nullable=False
   → 以模型为准（库应 NOT NULL）。依据：两张表的所有写入路径
   （ORM ``default=func.now()`` / ``onupdate=func.now()``、seed 脚本）
   都必写这两个字段，dev 库实测 0 行 NULL；NOT NULL 可挡住未来
   裸 SQL 写入漏字段。**本迁移负责 DDL**：先防御性回填 NULL → now()，
   再 SET NOT NULL。

3. 库中索引 ``idx_kpi_snapshot_ts_loop``（x4c5d6e7f8a9 迁移创建）、
   ``idx_loop_ledger_dcs_model``（v6p1dcs001 迁移创建）在模型元数据缺失，
   下次 autogen 会生成 DROP 生产索引的迁移
   → 补入模型 metadata（app/models/metric.py、app/models/loop.py）。
   **无需 DDL**（模型向库对齐）。

4. 10 个 uk_* 唯一约束：库为命名 UniqueConstraint、模型为同名唯一索引
   （功能等价但污染 check 输出）
   → 收敛为命名 UniqueConstraint（与库一致，且对齐项目既有先例
   metric.py ``uq_kpi_snapshot_hourly_loop_ts``）。**无需 DDL**。

涉及的 10 个 uk_*：uk_loop_ledger_tag_name、uk_loop_tag_mapping_loop_role、
uk_sys_user_username、uk_sys_user_email、uk_tag_registry_tag_name、
uk_loop_mode_mapping_loop_mode、uk_algorithm_param_code_type、
uk_engine_rule_code、uk_metric_config_code、uk_diagnosis_config_code。

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 需要补齐 NOT NULL 的（表, 列）清单
_NOT_NULL_TARGETS: tuple[tuple[str, str], ...] = (
    ("loop_ledger", "created_at"),
    ("loop_ledger", "updated_at"),
    ("plant_node", "created_at"),
    ("plant_node", "updated_at"),
    ("sys_user", "created_at"),
    ("sys_user", "updated_at"),
)


def upgrade() -> None:
    # 防御性回填：历史 NULL 行补 now()，避免 SET NOT NULL 失败
    for table, column in _NOT_NULL_TARGETS:
        op.execute(f"UPDATE {table} SET {column} = now() WHERE {column} IS NULL")
    for table, column in _NOT_NULL_TARGETS:
        op.alter_column(table, column, existing_type=sa.DateTime(), nullable=False)


def downgrade() -> None:
    for table, column in _NOT_NULL_TARGETS:
        op.alter_column(table, column, existing_type=sa.DateTime(), nullable=True)
