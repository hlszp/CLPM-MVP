"""回路核查最小实体（P6-05 后续分片一 E1，契约 01a §2.1）

新建 ``loop_investigation``——回路核查最小实体：

- 阶段机 4 态 ``TODO/IN_PROGRESS/WAITING/CLOSED``（DB CHECK 枚举）；
  结束结果 4 类 ``NO_FURTHER_ACTION/ANSWERED/HANDED_OFF/CANCELLED``，
  仅 CLOSED 持有且 CLOSED 必有结果与结束时刻（CHECK 互斥）；
- ``WAITING`` 三要素（等待原因/复查条件/等待责任）进入必填、离开必空
  （CHECK 强制）；
- 去重：``(loop_id, source_kind, source_id, problem_key)`` 未结束记录
  部分唯一索引——同回路同来源同问题至多一条进行中核查；已结束（含
  CANCELLED）不阻塞同问题再现（取消≠回路正常，不阻塞重建）；
- 负责人稳定身份 ``owner_id``（sys_user.id，SET NULL）；``owner_name``
  仅分派时刻姓名快照（只作显示，不作分派身份）；
- 移交物证：``HANDED_OFF`` 必有 ``close_target_kind='loop_investigation'``
  + 目标回链（CHECK）；``handoff_source_id`` 自引用回指上一条；
- ``revision>=1`` 行级 CAS + ``last_request_id`` 幂等（Slice A 同口径）。

外键：loop 删除 CASCADE；user 删除 SET NULL（核查属回路工作历史）。

downgrade 删表（本批新建，无存量数据语义）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p605invest01"
down_revision: str | None = "p605person01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "loop_investigation",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "loop_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("loop_ledger.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("problem_key", sa.String(128), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.Column("stage", sa.String(16), nullable=False, server_default="TODO"),
        sa.Column("outcome", sa.String(24), nullable=True),
        sa.Column("outcome_note", sa.String(500), nullable=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("sys_user.id", ondelete="SET NULL", name="fk_li_owner_user"),
            nullable=True,
        ),
        sa.Column("owner_name", sa.String(100), nullable=True),
        sa.Column("wait_reason", sa.String(200), nullable=True),
        sa.Column("wait_review_condition", sa.String(200), nullable=True),
        sa.Column("wait_responsible", sa.String(120), nullable=True),
        sa.Column("close_target_kind", sa.String(32), nullable=True),
        sa.Column("close_target_id", sa.String(64), nullable=True),
        sa.Column(
            "handoff_source_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "loop_investigation.id", ondelete="SET NULL", name="fk_li_handoff_source"
            ),
            nullable=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_request_id", sa.String(64), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("sys_user.id", ondelete="SET NULL", name="fk_li_created_by"),
            nullable=True,
        ),
        sa.Column(
            "closed_by",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("sys_user.id", ondelete="SET NULL", name="fk_li_closed_by"),
            nullable=True,
        ),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "stage IN ('TODO', 'IN_PROGRESS', 'WAITING', 'CLOSED')",
            name="ck_loop_investigation_stage",
        ),
        sa.CheckConstraint(
            "(stage = 'CLOSED' AND outcome IS NOT NULL AND outcome IN "
            "('NO_FURTHER_ACTION', 'ANSWERED', 'HANDED_OFF', 'CANCELLED') "
            "AND closed_at IS NOT NULL) "
            "OR (stage <> 'CLOSED' AND outcome IS NULL AND closed_at IS NULL)",
            name="ck_loop_investigation_outcome",
        ),
        sa.CheckConstraint(
            "(stage = 'WAITING' AND wait_reason IS NOT NULL "
            "AND wait_review_condition IS NOT NULL AND wait_responsible IS NOT NULL) "
            "OR (stage <> 'WAITING' AND wait_reason IS NULL "
            "AND wait_review_condition IS NULL AND wait_responsible IS NULL)",
            name="ck_loop_investigation_waiting",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome <> 'HANDED_OFF' OR "
            "(close_target_kind = 'loop_investigation' AND close_target_id IS NOT NULL)",
            name="ck_loop_investigation_handoff_target",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_loop_investigation_revision"),
    )
    # 来源身份+问题身份去重硬保证（未结束记录部分唯一）
    op.create_index(
        "uk_loop_investigation_active_identity",
        "loop_investigation",
        ["loop_id", "source_kind", "source_id", "problem_key"],
        unique=True,
        postgresql_where=sa.text("stage <> 'CLOSED'"),
    )
    op.create_index(
        "idx_loop_investigation_loop_list",
        "loop_investigation",
        ["loop_id", "stage", "created_at"],
    )
    op.create_index(
        "idx_loop_investigation_owner",
        "loop_investigation",
        ["owner_id", "stage"],
    )


def downgrade() -> None:
    op.drop_index("idx_loop_investigation_owner", table_name="loop_investigation")
    op.drop_index("idx_loop_investigation_loop_list", table_name="loop_investigation")
    op.drop_index("uk_loop_investigation_active_identity", table_name="loop_investigation")
    op.drop_table("loop_investigation")
