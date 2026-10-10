"""add calculation_result_record ledger + projection pointers (P1-05)

Revision ID: p105resledger01
Revises: d0724685907c
Create Date: 2026-10-12

P1-05 结果账本与兼容投影前置（DEC-10 已裁决按推荐；DEC-10a 节点表补唯一约束）：

1. 新表 ``calculation_result_record``（追加式不可变结果账本，唯一键
   (logical_run_id, object_kind, object_id, ts_start, ts_end)）与
   ``calculation_result_legacy_map``（旧投影行 ID → recordId 映射）。
2. 四投影表加可空 ``result_record_id``（FK → 账本，SET NULL）：
   kpi_snapshot_hourly / loop_confidence_latest / kpi_snapshot_custom /
   kpi_node_snapshot_hourly。
3. 存量归档：kpi_snapshot_hourly、kpi_node_snapshot_hourly 全量行与
   loop_confidence_latest 无匹配行归档为 record；algorithmVersion/
   configRevision 未知标 LEGACY_UNVERIFIABLE（不伪补版本）；旧表 ID→
   recordId 映射入 legacy_map，并回填投影行 result_record_id。
4. DEC-10a：kpi_node_snapshot_hourly 按 (plant_node_id, ts_start) 保留
   每键最新行（created_at 最新，id 大者胜）去重存量后建唯一约束
   uq_kpi_node_snapshot_hourly_node_ts。被去重的行已先行归档为 record
   （含 legacy_map 映射），历史仍可按旧 ID 读取。

downgrade 说明：账本表为新增表，downgrade 直接 DROP；节点表去重删除的
重复行不可由 downgrade 复活（其内容保留在账本 record 中）。生产回退按
方案 §6 走前向修复，不盲 downgrade。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p105resledger01"
down_revision: str | None = "d0724685907c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROJECTION_TABLES = (
    "kpi_snapshot_hourly",
    "loop_confidence_latest",
    "kpi_snapshot_custom",
    "kpi_node_snapshot_hourly",
)

# 与模型层 app/models/calculation_result.py 常量一致
_LEGACY_UNVERIFIABLE = "LEGACY_UNVERIFIABLE"

# 归档 INSERT 的公共列清单（PG16+ 内置 gen_random_uuid）
_ARCHIVE_COLUMNS = (
    "id, logical_run_id, object_kind, object_id, ts_start, ts_end, "
    "source_record_id, algorithm_version, config_revision, dataset_snapshot_id, "
    "status, payload, created_at"
)

# 归档映射表 INSERT 头（复用，避免超长行）
_MAP_INSERT = (
    "INSERT INTO calculation_result_legacy_map (id, legacy_table, legacy_id, record_id, created_at)"
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. 账本表 + 映射表
    # ------------------------------------------------------------------
    op.create_table(
        "calculation_result_record",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("logical_run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("object_kind", sa.String(8), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("ts_start", sa.DateTime(), nullable=False),
        sa.Column("ts_end", sa.DateTime(), nullable=False),
        sa.Column(
            "source_record_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("calculation_result_record.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("algorithm_version", sa.String(50), nullable=False),
        sa.Column("config_revision", sa.String(64), nullable=False),
        sa.Column("dataset_snapshot_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(timezone('UTC', now()))"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "object_kind IN ('LOOP', 'NODE')",
            name="ck_calc_result_record_object_kind",
        ),
        sa.CheckConstraint(
            "status IN ('COMPLETED', 'FAILED')",
            name="ck_calc_result_record_status",
        ),
        sa.CheckConstraint("ts_end > ts_start", name="ck_calc_result_record_window"),
        sa.UniqueConstraint(
            "logical_run_id",
            "object_kind",
            "object_id",
            "ts_start",
            "ts_end",
            name="uq_calc_result_record_run_object_window",
        ),
        comment="追加式计算结果账本（不可变 record，P1-05）",
    )
    op.create_index(
        "idx_calc_result_record_object_window",
        "calculation_result_record",
        ["object_kind", "object_id", "ts_start"],
    )

    op.create_table(
        "calculation_result_legacy_map",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("legacy_table", sa.String(40), nullable=False),
        sa.Column("legacy_id", sa.String(64), nullable=False),
        sa.Column(
            "record_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("calculation_result_record.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(timezone('UTC', now()))"),
            nullable=False,
        ),
        sa.UniqueConstraint("legacy_table", "legacy_id", name="uq_calc_legacy_map_table_id"),
        comment="旧投影表 ID→结果账本 recordId 映射（P1-05 迁移归档）",
    )
    op.create_index("idx_calc_legacy_map_record_id", "calculation_result_legacy_map", ["record_id"])

    # ------------------------------------------------------------------
    # 2. 四投影表加可空 result_record_id（FK → 账本，SET NULL）
    # ------------------------------------------------------------------
    for table in _PROJECTION_TABLES:
        op.add_column(
            table,
            sa.Column("result_record_id", postgresql.UUID(as_uuid=False), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_result_record",
            table,
            "calculation_result_record",
            ["result_record_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ------------------------------------------------------------------
    # 3a. 存量归档：kpi_snapshot_hourly → LOOP record（loop_id 为空的死数据跳过）
    # ------------------------------------------------------------------
    op.execute(
        f"""
        WITH ins AS (
            INSERT INTO calculation_result_record ({_ARCHIVE_COLUMNS})
            SELECT
                gen_random_uuid(),
                gen_random_uuid(),
                'LOOP',
                s.loop_id,
                s.ts_start,
                s.ts_end,
                NULL,
                COALESCE(s.algorithm_version, '{_LEGACY_UNVERIFIABLE}'),
                '{_LEGACY_UNVERIFIABLE}',
                NULL,
                'COMPLETED',
                jsonb_build_object(
                    'legacy', jsonb_build_object(
                        'table', 'kpi_snapshot_hourly', 'id', s.id::text),
                    'snapshot', to_jsonb(s)
                ),
                timezone('UTC', now())
            FROM kpi_snapshot_hourly s
            WHERE s.loop_id IS NOT NULL
            RETURNING id, (payload->'legacy'->>'id') AS legacy_id
        )
        {_MAP_INSERT}
        SELECT gen_random_uuid(), 'kpi_snapshot_hourly', legacy_id, id, timezone('UTC', now())
        FROM ins
        """
    )
    op.execute(
        """
        UPDATE kpi_snapshot_hourly s
        SET result_record_id = r.id
        FROM calculation_result_record r
        WHERE r.object_kind = 'LOOP'
          AND r.payload->'legacy'->>'table' = 'kpi_snapshot_hourly'
          AND (r.payload->'legacy'->>'id') = s.id::text
        """
    )

    # ------------------------------------------------------------------
    # 3b. 存量归档：kpi_node_snapshot_hourly 全量行 → NODE record
    #     （先归档再去重——被去重的重复行保留为账本证据，旧 ID 仍可读）
    # ------------------------------------------------------------------
    op.execute(
        f"""
        WITH ins AS (
            INSERT INTO calculation_result_record ({_ARCHIVE_COLUMNS})
            SELECT
                gen_random_uuid(),
                gen_random_uuid(),
                'NODE',
                s.plant_node_id,
                s.ts_start,
                s.ts_end,
                NULL,
                COALESCE(s.algorithm_version, '{_LEGACY_UNVERIFIABLE}'),
                '{_LEGACY_UNVERIFIABLE}',
                NULL,
                'COMPLETED',
                jsonb_build_object(
                    'legacy', jsonb_build_object(
                        'table', 'kpi_node_snapshot_hourly', 'id', s.id::text),
                    'snapshot', to_jsonb(s)
                ),
                timezone('UTC', now())
            FROM kpi_node_snapshot_hourly s
            RETURNING id, (payload->'legacy'->>'id') AS legacy_id
        )
        {_MAP_INSERT}
        SELECT gen_random_uuid(), 'kpi_node_snapshot_hourly', legacy_id, id, timezone('UTC', now())
        FROM ins
        """
    )

    # ------------------------------------------------------------------
    # 4. DEC-10a：节点表按 (plant_node_id, ts_start) 保留每键最新行去重，
    #    再建唯一约束（downgrade 删约束后 upgrade 可幂等重放去重）
    # ------------------------------------------------------------------
    op.execute(
        """
        DELETE FROM kpi_node_snapshot_hourly d
        USING kpi_node_snapshot_hourly k
        WHERE d.plant_node_id = k.plant_node_id
          AND d.ts_start = k.ts_start
          AND (k.created_at, k.id::text) > (d.created_at, d.id::text)
        """
    )
    op.create_unique_constraint(
        "uq_kpi_node_snapshot_hourly_node_ts",
        "kpi_node_snapshot_hourly",
        ["plant_node_id", "ts_start"],
    )
    op.execute(
        """
        UPDATE kpi_node_snapshot_hourly s
        SET result_record_id = r.id
        FROM calculation_result_record r
        WHERE r.object_kind = 'NODE'
          AND r.payload->'legacy'->>'table' = 'kpi_node_snapshot_hourly'
          AND (r.payload->'legacy'->>'id') = s.id::text
        """
    )

    # ------------------------------------------------------------------
    # 3c. loop_confidence_latest：优先指向同一 (loop, 窗口) 的小时快照归档
    #     record；无匹配行（孤儿投影）才单独归档为本表 record
    # ------------------------------------------------------------------
    op.execute(
        """
        UPDATE loop_confidence_latest l
        SET result_record_id = r.id
        FROM calculation_result_record r
        WHERE r.object_kind = 'LOOP'
          AND r.payload->'legacy'->>'table' = 'kpi_snapshot_hourly'
          AND r.object_id = l.loop_id
          AND r.ts_start = l.data_ts_start
          AND r.ts_end = l.data_ts_end
        """
    )
    op.execute(
        f"""
        WITH ins AS (
            INSERT INTO calculation_result_record ({_ARCHIVE_COLUMNS})
            SELECT
                gen_random_uuid(),
                gen_random_uuid(),
                'LOOP',
                l.loop_id,
                l.data_ts_start,
                l.data_ts_end,
                NULL,
                COALESCE(l.algorithm_version, '{_LEGACY_UNVERIFIABLE}'),
                '{_LEGACY_UNVERIFIABLE}',
                NULL,
                'COMPLETED',
                jsonb_build_object(
                    'legacy', jsonb_build_object(
                        'table', 'loop_confidence_latest', 'id', l.id::text),
                    'latest', to_jsonb(l)
                ),
                timezone('UTC', now())
            FROM loop_confidence_latest l
            WHERE l.result_record_id IS NULL
            RETURNING id, (payload->'legacy'->>'id') AS legacy_id
        )
        {_MAP_INSERT}
        SELECT gen_random_uuid(), 'loop_confidence_latest', legacy_id, id, timezone('UTC', now())
        FROM ins
        """
    )
    op.execute(
        """
        UPDATE loop_confidence_latest l
        SET result_record_id = r.id
        FROM calculation_result_record r
        WHERE r.object_kind = 'LOOP'
          AND r.payload->'legacy'->>'table' = 'loop_confidence_latest'
          AND (r.payload->'legacy'->>'id') = l.id::text
        """
    )
    # kpi_snapshot_custom：仅加列（result_record_id 保持 NULL，兼容读；
    # 迁移归档范围按任务卡为 hourly/latest/node 三表，custom 存量不归档）


def downgrade() -> None:
    # 逆序回滚：约束 → 投影列（连带 FK）→ 映射表 → 账本表。
    # 注意：节点表被去重的重复行不可复活（内容已在账本，downgrade 一并丢弃）。
    op.drop_constraint(
        "uq_kpi_node_snapshot_hourly_node_ts", "kpi_node_snapshot_hourly", type_="unique"
    )
    for table in reversed(_PROJECTION_TABLES):
        op.drop_constraint(f"fk_{table}_result_record", table, type_="foreignkey")
        op.drop_column(table, "result_record_id")
    op.drop_index("idx_calc_legacy_map_record_id", table_name="calculation_result_legacy_map")
    op.drop_table("calculation_result_legacy_map")
    op.drop_index("idx_calc_result_record_object_window", table_name="calculation_result_record")
    op.drop_table("calculation_result_record")
