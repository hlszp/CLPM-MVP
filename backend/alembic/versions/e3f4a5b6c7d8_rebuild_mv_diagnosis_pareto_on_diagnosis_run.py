"""rebuild mv_diagnosis_pareto on diagnosis_run

14 号方案阶段 A2（A2-5）：mv_diagnosis_pareto 数据源由旧引擎表
（diagnosis_tag × diagnosis_result）重建为诊断 v2 引擎表 diagnosis_run：
- 按 primary_category（8 类枚举 → 中文标签展示域）聚合近 30d SUCCESS run 数
- converted/ignored = 该类 run 中已转工单 / 已忽略计数（loop_action_item 关联）
- sla_warned_count 恒 0（D1=a SLA 下线，保列稳消费方结构）
- 窗口基准用 now() AT TIME ZONE 'utc'（naive UTC 列口径，规避会话时区 +8 偏移）

refresh_workbench_mv 任务零改动（MV 名与 UNIQUE 索引保持，CONCURRENTLY 刷新可用）。
旧引擎表不 DROP（D4=a，仅停读）。

Revision ID: e3f4a5b6c7d8
Revises: d9a0b1c2e3f4
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d9a0b1c2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app.services.diagnosis_v2_compat.CATEGORY_LABELS_V2 同值的中文标签域
# （MV DDL 无法引用 Python 常量，同步维护；标签为稳定枚举）
_MV_DDL = """
CREATE MATERIALIZED VIEW mv_diagnosis_pareto AS
SELECT
    CASE r.primary_category
        WHEN 'TUNING' THEN '参数问题（PID 整定）'
        WHEN 'VALVE' THEN '阀门/执行机构问题'
        WHEN 'INSTRUMENT' THEN '仪表/测量问题'
        WHEN 'COMMUNICATION' THEN '通信链路问题'
        WHEN 'PROCESS' THEN '工艺/外扰问题'
        WHEN 'UTILIZATION' THEN '投用/操作问题'
        WHEN 'DESIGN' THEN '组态/设计问题'
        WHEN 'DATA_INSUFFICIENT' THEN '数据不足/无法判定'
        ELSE r.primary_category
    END AS root_cause,
    COUNT(*) AS tag_count,
    COUNT(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM loop_action_item a
        WHERE a.run_id = r.id AND a.converted_order_id IS NOT NULL
    )) AS converted_count,
    COUNT(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM loop_action_item a
        WHERE a.run_id = r.id AND a.status = 'IGNORED'
    )) AS ignored_count,
    0::bigint AS sla_warned_count
FROM diagnosis_run r
WHERE r.status = 'SUCCESS'
  AND r.primary_category IS NOT NULL
  AND r.created_at >= (now() AT TIME ZONE 'utc') - interval '30 days'
GROUP BY r.primary_category
WITH NO DATA
"""

# 旧定义（a9229d815d0d MV-02，downgrade 回退用）
_MV_DDL_OLD = """
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


def upgrade() -> None:
    # DROP 旧 MV（自动带走 UNIQUE 索引）→ 基于 diagnosis_run 重建 → 重建唯一索引 → 首次普通 REFRESH
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_diagnosis_pareto")
    op.execute(_MV_DDL)
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_diagnosis_pareto_root ON mv_diagnosis_pareto (root_cause)"
    )
    # 首次刷新不能用 CONCURRENTLY（WITH NO DATA 创建后无数据可并发校验）
    op.execute("REFRESH MATERIALIZED VIEW mv_diagnosis_pareto")


def downgrade() -> None:
    # 回退为旧引擎表口径（a9229d815d0d 原定义）
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_diagnosis_pareto")
    op.execute(_MV_DDL_OLD)
    op.execute(
        "CREATE UNIQUE INDEX idx_mv_diagnosis_pareto_root ON mv_diagnosis_pareto (root_cause)"
    )
    op.execute("REFRESH MATERIALIZED VIEW mv_diagnosis_pareto")
