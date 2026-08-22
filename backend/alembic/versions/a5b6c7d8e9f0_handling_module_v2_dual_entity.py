"""handling module v2.0: dual-entity (suggestion + work order)

处置模块 v2.0 双实体重构（docs/MVP设计/08-处置模块设计方案.md §3/§9）：
- 新建 handling_order（工单：排程-执行-验证-闭环载体，§3.2 全字段 + 索引）
- loop_action_item 收敛为建议实体（§3.1）：
  * 加审核 4 列：reviewed_by / reviewed_at / rejected_reason / converted_order_id
    （FK→handling_order.id ON DELETE SET NULL）
  * status CHECK 换 5 态（PENDING/ACCEPTED/CONVERTED/REJECTED/IGNORED）
  * 存量数据迁移（§9，幂等）：status IN (HANDLING/VERIFYING/CLOSED/REOPENED) 行
    生成工单（HANDLING→EXECUTING，其余同名映射；执行/验证字段平移；
    handled_by→handler、handled_at→started_at、source=DIAGNOSIS、
    suggestion_ids=[id]、title=left(content,50)、order_no=HD-YYYYMMDD-NNN 当日序号），
    原行置 CONVERTED + converted_order_id 回链
  * 删处置执行 13 列（action_type/action_detail/handled_by/handled_at/submitted_at/
    verify_run_id/verify_result/verify_note/verified_by/verified_at/kpi_before/
    kpi_after/tuning_record_id）
  * run_id FK 改 nullable + ON DELETE SET NULL（手动建议放空迁移）
  * idx_loop_action_item_status 排序列 updated_at → suggested_at（建议清单口径）
- 表数 47 → 48（test_process_model_version.py 断言同步）

Revision ID: a5b6c7d8e9f0
Revises: e1f2a3b4c5d6
Create Date: 2026-08-20 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 存量数据迁移（§9，幂等）：已进入处置流程的行 → 生成工单（语句 1：INSERT）
#: 再回链置 CONVERTED（语句 2：UPDATE——与 INSERT 分语句执行，FK 触发器
#: 在同事务不同语句间可见；同一条 data-modifying CTE 内 UPDATE 看不到
#: CTE INSERT 的行，会触发 fk_loop_action_item_converted_order 违反）
_MIGRATE_INSERT_SQL = """
INSERT INTO handling_order (
    id, order_no, loop_id, source, suggestion_ids, title,
    action_type, action_detail, planned_at, planned_by, handler, started_at,
    submitted_at, verify_run_id, verify_result, verify_note, verified_by,
    verified_at, kpi_before, kpi_after, tuning_record_id, status,
    cancel_reason, feedback_log, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    db.day_prefix || '-' || lpad((db.seq_base + c.row_num)::text, 3, '0'),
    c.loop_id,
    'DIAGNOSIS',
    to_jsonb(ARRAY[c.id::text]),
    left(c.content, 50),
    COALESCE(c.action_type, 'OTHER'),
    c.action_detail,
    NULL,
    c.suggested_by,
    c.handled_by,
    c.handled_at,
    c.submitted_at,
    c.verify_run_id,
    c.verify_result,
    c.verify_note,
    c.verified_by,
    c.verified_at,
    c.kpi_before,
    c.kpi_after,
    c.tuning_record_id,
    CASE c.status WHEN 'HANDLING' THEN 'EXECUTING' ELSE c.status END,
    NULL,
    NULL,
    now(),
    now()
FROM (
    SELECT ai.*, ROW_NUMBER() OVER (ORDER BY suggested_at, id) AS row_num
    FROM loop_action_item ai
    WHERE ai.status IN ('HANDLING', 'VERIFYING', 'CLOSED', 'REOPENED')
      AND ai.converted_order_id IS NULL
) c CROSS JOIN (
    SELECT 'HD-' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD') AS day_prefix,
           COALESCE((
               SELECT COUNT(*) FROM handling_order
               WHERE order_no LIKE 'HD-' ||
                     to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD') || '-%'
           ), 0) AS seq_base
) db
"""

_MIGRATE_UPDATE_SQL = """
UPDATE loop_action_item ai
SET converted_order_id = ho.id,
    status = 'CONVERTED',
    updated_at = now()
FROM handling_order ho
WHERE ai.id::text = ho.suggestion_ids ->> 0
  AND ai.status IN ('HANDLING', 'VERIFYING', 'CLOSED', 'REOPENED')
  AND ai.converted_order_id IS NULL
"""

#: 执行域 13 列（§3.1 迁移走 → handling_order）
_EXEC_COLUMNS = (
    "tuning_record_id",
    "kpi_after",
    "kpi_before",
    "verified_at",
    "verified_by",
    "verify_note",
    "verify_result",
    "verify_run_id",
    "submitted_at",
    "handled_at",
    "handled_by",
    "action_detail",
    "action_type",
)


def upgrade() -> None:
    """Upgrade schema + 存量数据迁移（幂等：converted_order_id IS NULL 防重跑）."""
    # 1. 新建 handling_order（§3.2）
    op.create_table(
        "handling_order",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("order_no", sa.String(length=32), nullable=False, unique=True),
        sa.Column("loop_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("suggestion_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("action_type", sa.String(length=16), nullable=False),
        sa.Column("action_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("planned_at", sa.DateTime(), nullable=True),
        sa.Column("planned_by", sa.String(length=64), nullable=True),
        sa.Column("handler", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("feedback_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("verify_run_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("verify_result", sa.String(length=16), nullable=True),
        sa.Column("verify_note", sa.String(length=500), nullable=True),
        sa.Column("verified_by", sa.String(length=64), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("kpi_before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("kpi_after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tuning_record_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("cancel_reason", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('DIAGNOSIS', 'MANUAL')", name="ck_handling_order_source"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'EXECUTING', 'VERIFYING', 'CLOSED', 'REOPENED', 'CANCELLED')",
            name="ck_handling_order_status",
        ),
        sa.CheckConstraint(
            "action_type IN ('TUNING', 'VALVE', 'INSTRUMENT', 'LINK', 'PROCESS', "
            "'UTILIZATION', 'RECONFIG', 'OTHER')",
            name="ck_handling_order_action_type",
        ),
        sa.CheckConstraint(
            "verify_result IS NULL OR verify_result IN ('EFFECTIVE', 'INEFFECTIVE')",
            name="ck_handling_order_verify_result",
        ),
        sa.ForeignKeyConstraint(["loop_id"], ["loop_ledger.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verify_run_id"], ["diagnosis_run.id"], ondelete="SET NULL"),
        comment="处置工单（处置模块 v2.0：排程-执行-验证-闭环执行载体）",
    )
    op.create_index(
        "idx_handling_order_status",
        "handling_order",
        ["status", sa.text("updated_at DESC")],
        unique=False,
    )
    op.create_index("idx_handling_order_loop", "handling_order", ["loop_id"], unique=False)
    op.create_index("idx_handling_order_planned", "handling_order", ["planned_at"], unique=False)

    # 2. loop_action_item 加审核 4 列
    op.add_column("loop_action_item", sa.Column("reviewed_by", sa.String(length=64), nullable=True))
    op.add_column("loop_action_item", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "loop_action_item", sa.Column("rejected_reason", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "loop_action_item",
        sa.Column("converted_order_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_loop_action_item_converted_order",
        "loop_action_item",
        "handling_order",
        ["converted_order_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. 换 status CHECK：先 drop 旧六态（数据迁移需写 CONVERTED，旧 CHECK 不含）
    op.drop_constraint("ck_loop_action_item_status", "loop_action_item", type_="check")

    # 4. 存量数据迁移（§9，幂等：INSERT 与 UPDATE 分语句执行，见 _MIGRATE_INSERT_SQL 注释）
    op.execute(_MIGRATE_INSERT_SQL)
    op.execute(_MIGRATE_UPDATE_SQL)

    # 5. 建 5 态 CHECK（此时存量仅剩 PENDING/IGNORED/CONVERTED，全部合法）
    op.create_check_constraint(
        "ck_loop_action_item_status",
        "loop_action_item",
        "status IN ('PENDING', 'ACCEPTED', 'CONVERTED', 'REJECTED', 'IGNORED')",
    )

    # 6. 删处置执行 13 列（连带 FK 与枚举 CHECK）
    op.drop_constraint("fk_loop_action_item_verify_run", "loop_action_item", type_="foreignkey")
    op.drop_constraint("ck_loop_action_item_verify_result", "loop_action_item", type_="check")
    op.drop_constraint("ck_loop_action_item_action_type", "loop_action_item", type_="check")
    for col in _EXEC_COLUMNS:
        op.drop_column("loop_action_item", col)

    # 7. run_id FK 改 nullable + ON DELETE SET NULL（手动建议放空迁移）
    op.drop_constraint("loop_action_item_run_id_fkey", "loop_action_item", type_="foreignkey")
    op.alter_column(
        "loop_action_item", "run_id", existing_type=postgresql.UUID(as_uuid=False), nullable=True
    )
    op.create_foreign_key(
        "fk_loop_action_item_run",
        "loop_action_item",
        "diagnosis_run",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 8. 建议清单主查询索引：排序列 updated_at → suggested_at（§6.1 建议口径）
    op.drop_index("idx_loop_action_item_status", table_name="loop_action_item")
    op.create_index(
        "idx_loop_action_item_status",
        "loop_action_item",
        ["status", sa.text("suggested_at DESC")],
        unique=False,
    )

    # 9. 表注释更新
    op.execute(
        "COMMENT ON TABLE loop_action_item IS '回路处置建议（处置模块 v2.0：建议汇聚与审核对象）'"
    )


def downgrade() -> None:
    """Downgrade schema（仅结构还原，迁移数据不回写，§9）."""
    op.execute(
        "COMMENT ON TABLE loop_action_item IS "
        "'回路处置建议（处置模块 Phase 1：建议-处置-验证-关闭全生命周期）'"
    )
    # 建议索引还原 updated_at 排序
    op.drop_index("idx_loop_action_item_status", table_name="loop_action_item")
    op.create_index(
        "idx_loop_action_item_status",
        "loop_action_item",
        ["status", sa.text("updated_at DESC")],
        unique=False,
    )
    # run_id 还原 NOT NULL + CASCADE（存量 NULL 行兜底挂不存在的 run 会失败，结构口径优先）
    op.drop_constraint("fk_loop_action_item_run", "loop_action_item", type_="foreignkey")
    op.execute(
        "UPDATE loop_action_item SET run_id = '00000000-0000-0000-0000-000000000000' "
        "WHERE run_id IS NULL"
    )
    op.alter_column(
        "loop_action_item", "run_id", existing_type=postgresql.UUID(as_uuid=False), nullable=False
    )
    op.create_foreign_key(
        "loop_action_item_run_id_fkey",
        "loop_action_item",
        "diagnosis_run",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # 还原执行 13 列 + FK + CHECK（数据不回写）
    op.add_column("loop_action_item", sa.Column("action_type", sa.String(length=16), nullable=True))
    op.add_column(
        "loop_action_item",
        sa.Column("action_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("loop_action_item", sa.Column("handled_by", sa.String(length=64), nullable=True))
    op.add_column("loop_action_item", sa.Column("handled_at", sa.DateTime(), nullable=True))
    op.add_column("loop_action_item", sa.Column("submitted_at", sa.DateTime(), nullable=True))
    op.add_column(
        "loop_action_item",
        sa.Column("verify_run_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "loop_action_item", sa.Column("verify_result", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "loop_action_item", sa.Column("verify_note", sa.String(length=500), nullable=True)
    )
    op.add_column("loop_action_item", sa.Column("verified_by", sa.String(length=64), nullable=True))
    op.add_column("loop_action_item", sa.Column("verified_at", sa.DateTime(), nullable=True))
    op.add_column(
        "loop_action_item",
        sa.Column("kpi_before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "loop_action_item",
        sa.Column("kpi_after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "loop_action_item",
        sa.Column("tuning_record_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_loop_action_item_verify_run",
        "loop_action_item",
        "diagnosis_run",
        ["verify_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_loop_action_item_action_type",
        "loop_action_item",
        "action_type IS NULL OR action_type IN "
        "('TUNING', 'VALVE', 'INSTRUMENT', 'LINK', 'PROCESS', "
        "'UTILIZATION', 'RECONFIG', 'OTHER')",
    )
    op.create_check_constraint(
        "ck_loop_action_item_verify_result",
        "loop_action_item",
        "verify_result IS NULL OR verify_result IN ('EFFECTIVE', 'INEFFECTIVE')",
    )
    # status CHECK 还原六态前，CONVERTED 行回置 PENDING（工单数据随表删除不保留）
    op.drop_constraint("ck_loop_action_item_status", "loop_action_item", type_="check")
    op.execute(
        "UPDATE loop_action_item SET status = 'PENDING' WHERE status IN ('ACCEPTED', 'CONVERTED')"
    )
    op.create_check_constraint(
        "ck_loop_action_item_status",
        "loop_action_item",
        "status IN ('PENDING', 'HANDLING', 'VERIFYING', 'CLOSED', 'REOPENED', 'IGNORED')",
    )
    # 删审核 4 列（先 drop FK）+ 删 handling_order 表
    op.drop_constraint(
        "fk_loop_action_item_converted_order", "loop_action_item", type_="foreignkey"
    )
    op.drop_column("loop_action_item", "converted_order_id")
    op.drop_column("loop_action_item", "rejected_reason")
    op.drop_column("loop_action_item", "reviewed_at")
    op.drop_column("loop_action_item", "reviewed_by")
    op.drop_index("idx_handling_order_planned", table_name="handling_order")
    op.drop_index("idx_handling_order_loop", table_name="handling_order")
    op.drop_index("idx_handling_order_status", table_name="handling_order")
    op.drop_table("handling_order")
