"""calculation_result_attempt 尝试日志表（P5-02，G2 遗留③）

Revision ID: p502attm01
Revises: p403scope01
Create Date: 2026-10-13

P0-02 数值语义冻结 v2 §2.2 现成稿（DEC-10 用户裁决"全部按推荐"）：

- 独立表 ``calculation_result_attempt``：attemptId PK、logicalRunId、
  attempt_no、started_at、finished_at、error_class、error_message、
  worker_pid。暂态重试只写 attempt 日志，不写终态 record。
- 同 logicalRunId 最多 3 次 attempt（首跑 + 2 重试）；第 3 次仍失败由
  写入链追加 ``status=FAILED`` 不可变终态 record（代码侧接线，本迁移
  只建表）。FAILED 后再次计算=显式重评：新 logicalRunId +
  sourceRecordId 指向该 FAILED 记录（列既有）。

与冻结稿字面"logicalRunId FK"的显式偏差（已登记）：record 表
logical_run_id 非唯一（批逻辑运行覆盖 N 对象 record），PostgreSQL 外键
要求被引用列唯一；且 attempt 行必须在尚无任何 record 时可写。故不设
FK，以 ``(logical_run_id, attempt_no)`` 唯一约束保证重投递幂等。

与引导 SQL（db/postgresql/01_schema.sql §8.9b）同批同内容；downgrade
整表回退（日志表无下游引用，可安全丢弃）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p502attm01"
down_revision: str | None = "p403scope01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTEMPT_TABLE = "calculation_result_attempt"


def upgrade() -> None:
    op.create_table(
        _ATTEMPT_TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("logical_run_id", sa.UUID(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error_class", sa.String(length=200), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("worker_pid", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("logical_run_id", "attempt_no", name="uq_calc_attempt_run_no"),
        comment="计算尝试日志（暂态重试不落终态，P5-02 G2遗留③）",
    )
    op.create_index("idx_calc_attempt_logical_run_id", _ATTEMPT_TABLE, ["logical_run_id"])


def downgrade() -> None:
    op.drop_index("idx_calc_attempt_logical_run_id", table_name=_ATTEMPT_TABLE)
    op.drop_table(_ATTEMPT_TABLE)
