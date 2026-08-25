"""add workbench 3 materialized views

3 个物化视图支撑工作台看板聚合查询：
- mv_staff_workload：人员负载看板（A-08），每用户一行，聚合活跃工单数
- mv_diagnosis_pareto：诊断 Pareto 分布（A-01），按根因类别聚合
- mv_handling_funnel：处置漏斗（A-05），按 scope 聚合工单状态分布

均使用 REFRESH MATERIALIZED VIEW CONCURRENTLY（需 UNIQUE INDEX），
由 Celery beat ``refresh-workbench-mv@5min`` 任务刷新（与 precalc 错峰 2min）。

Revision ID: a9229d815d0d
Revises: 07c1efaad592
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9229d815d0d"
down_revision: str | Sequence[str] | None = "07c1efaad592"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------
def upgrade() -> None:
    # ======================================================================
    # MV-01: mv_staff_workload — 人员负载看板
    # 每用户一行，聚合活跃工单数（PENDING/EXECUTING/VERIFYING）、SLA 告警数
    # ======================================================================
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_staff_workload AS
        SELECT
            h.handler_id AS user_id,
            u.display_name AS user_name,
            COUNT(*) FILTER (
                WHERE h.status IN ('PENDING','EXECUTING','VERIFYING')
            ) AS active_count,
            COUNT(*) FILTER (
                WHERE h.status = 'PENDING'
            ) AS pending_count,
            COUNT(*) FILTER (
                WHERE h.status = 'EXECUTING'
            ) AS executing_count,
            COUNT(*) FILTER (
                WHERE h.status = 'VERIFYING'
            ) AS verifying_count,
            COUNT(*) FILTER (
                WHERE h.sla_stage IN ('WARN','BREACH')
            ) AS sla_warned_count,
            COUNT(*) FILTER (
                WHERE h.status = 'CLOSED'
            ) AS closed_count,
            MAX(h.updated_at) AS last_activity_at
        FROM handling_order h
        LEFT JOIN sys_user u ON u.id = h.handler_id
        WHERE h.handler_id IS NOT NULL
        GROUP BY h.handler_id, u.display_name
        WITH NO DATA
        """
    )
    # UNIQUE INDEX 支持 REFRESH CONCURRENTLY
    op.execute("CREATE UNIQUE INDEX idx_mv_staff_workload_user ON mv_staff_workload (user_id)")

    # ======================================================================
    # MV-02: mv_diagnosis_pareto — 诊断根因 Pareto 分布
    # 按根因类别聚合诊断 Tag 数量，支撑 A-01 看板 Pareto 图
    # ======================================================================
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_diagnosis_pareto AS
        SELECT
            dr.recommended_category AS root_cause,
            COUNT(DISTINCT dt.id) AS tag_count,
            COUNT(*) FILTER (
                WHERE dt.disposition_state = 'CONVERTED'
            ) AS converted_count,
            COUNT(*) FILTER (
                WHERE dt.disposition_state = 'IGNORED'
            ) AS ignored_count,
            COUNT(*) FILTER (
                WHERE dt.sla_stage IN ('WARN','BREACH')
            ) AS sla_warned_count
        FROM diagnosis_tag dt
        LEFT JOIN diagnosis_result dr ON dr.loop_id = dt.loop_id
        WHERE dt.status = 'ACTIVE'
          AND dr.recommended_category IS NOT NULL
        GROUP BY dr.recommended_category
        WITH NO DATA
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_diagnosis_pareto_root ON mv_diagnosis_pareto (root_cause)"
    )

    # ======================================================================
    # MV-03: mv_handling_funnel — 处置漏斗
    # 按 scope 聚合工单状态分布，支撑 A-05 看板漏斗图
    # 含 GLOBAL 汇总行（scope_type='GLOBAL', scope_id=0）
    # ======================================================================
    op.execute(
        """
        CREATE MATERIALIZED VIEW mv_handling_funnel AS
        SELECT
            COALESCE(h.scope_type, 'GLOBAL') AS scope_type,
            COALESCE(h.scope_id, 0) AS scope_id,
            COUNT(*) AS total_count,
            COUNT(*) FILTER (WHERE h.status = 'PENDING') AS pending_count,
            COUNT(*) FILTER (WHERE h.status = 'EXECUTING') AS executing_count,
            COUNT(*) FILTER (WHERE h.status = 'VERIFYING') AS verifying_count,
            COUNT(*) FILTER (WHERE h.status = 'CLOSED') AS closed_count,
            COUNT(*) FILTER (WHERE h.status = 'REOPENED') AS reopened_count,
            COUNT(*) FILTER (WHERE h.status = 'CANCELLED') AS cancelled_count,
            COUNT(*) FILTER (WHERE h.sla_stage = 'BREACH') AS breached_count,
            AVG(
                CASE
                    WHEN h.status = 'CLOSED'
                    THEN EXTRACT(EPOCH FROM (
                        COALESCE(h.verified_at, h.updated_at) - h.created_at
                    )) / 3600
                END
            ) AS avg_cycle_hours
        FROM handling_order h
        GROUP BY COALESCE(h.scope_type, 'GLOBAL'), COALESCE(h.scope_id, 0)
        WITH NO DATA
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_handling_funnel_scope "
        "ON mv_handling_funnel (scope_type, scope_id)"
    )

    # 首次刷新（WITH NO DATA 创建后需手动填充一次）
    # 注：不能用 CONCURRENTLY（首次刷新无数据），用普通 REFRESH
    op.execute("REFRESH MATERIALIZED VIEW mv_staff_workload")
    op.execute("REFRESH MATERIALIZED VIEW mv_diagnosis_pareto")
    op.execute("REFRESH MATERIALIZED VIEW mv_handling_funnel")


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------
def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_handling_funnel")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_diagnosis_pareto")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_staff_workload")
