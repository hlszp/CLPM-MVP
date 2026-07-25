"""add tracker D1 columns: trigger_type / triggered_by / severity

整改计划 D1：诊断→Tracker 自动建单字段补全。

D2（b2d3e4f5g6h7）已补全 created_at / comment / moc_* / diagnosis_result_id
列与开放态部分唯一索引 uk_action_tracker_open。本迁移追加 D1 自动建单所需
的来源与优先级字段，使 action_tracker 能区分系统自动建单与用户手工建单，
并承载诊断严重等级（从 diagnosis_tag.severity 冗余），便于按优先级筛选。

新增列：
- trigger_type：建单方式 auto(系统自动) / manual(用户手工)，默认 manual
  保证存量数据兼容（存量行视为手工建单）
- triggered_by：建单人，auto 时为 'system'，manual 时为用户名
- severity：严重等级 INFO/WARN/ERROR/CRITICAL（从 diagnosis_tag.severity
  冗余，建单后不再变化，避免每次查询 tracker 都 JOIN diagnosis_tag）

新增约束：
- ck_action_tracker_trigger_type：trigger_type ∈ ('auto', 'manual')
- ck_action_tracker_severity：severity 为空或 ∈ INFO/WARN/ERROR/CRITICAL

新增索引：
- idx_action_tracker_trigger_type：按建单方式筛选
- idx_action_tracker_severity_status：按严重等级 + 状态筛选（工作台诊断聚合卡）
- idx_action_tracker_loop_created：(loop_id, created_at DESC)，performance.py
  "最新一条开放态 tracker"查询依赖

Revision ID: d1a2b3c4e5f6
Revises: b1c2d3e4f5a6
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1a2b3c4e5f6"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 新增列
    op.add_column(
        "action_tracker",
        sa.Column(
            "trigger_type",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'manual'"),
            comment="建单方式：auto(系统自动) / manual(用户手工)",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "triggered_by",
            sa.String(50),
            nullable=True,
            comment="建单人：auto 时为 system，manual 时为用户名",
        ),
    )
    op.add_column(
        "action_tracker",
        sa.Column(
            "severity",
            sa.String(20),
            nullable=True,
            comment="严重等级（从 diagnosis_tag 冗余）：INFO/WARN/ERROR/CRITICAL",
        ),
    )

    # 2. CHECK 约束
    op.create_check_constraint(
        "ck_action_tracker_trigger_type",
        "action_tracker",
        "trigger_type IN ('auto', 'manual')",
    )
    op.create_check_constraint(
        "ck_action_tracker_severity",
        "action_tracker",
        "severity IS NULL OR severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')",
    )

    # 3. 索引
    op.create_index(
        "idx_action_tracker_trigger_type",
        "action_tracker",
        ["trigger_type"],
    )
    op.create_index(
        "idx_action_tracker_severity_status",
        "action_tracker",
        ["severity", "action_status"],
    )
    # 建单时间排序：performance.py "最新一条"查询依赖
    op.create_index(
        "idx_action_tracker_loop_created",
        "action_tracker",
        ["loop_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_action_tracker_loop_created", table_name="action_tracker")
    op.drop_index("idx_action_tracker_severity_status", table_name="action_tracker")
    op.drop_index("idx_action_tracker_trigger_type", table_name="action_tracker")
    op.drop_constraint("ck_action_tracker_severity", "action_tracker", type_="check")
    op.drop_constraint("ck_action_tracker_trigger_type", "action_tracker", type_="check")
    op.drop_column("action_tracker", "severity")
    op.drop_column("action_tracker", "triggered_by")
    op.drop_column("action_tracker", "trigger_type")
