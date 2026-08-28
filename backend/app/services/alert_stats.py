"""预警统计聚合 service（报告模块优化 P1-3，2026-08-28）。

预警统计报告（方案 §4.2）自持聚合：监控是基础模块，此报告在任何模块
组合下完整可用。

口径：
- 预警总数/活跃数：窗口内 triggered_at 触发的事件；活跃数为其中当前
  status='ACTIVE' 的事件
- MTTA：窗口内已确认事件 avg(acknowledged_at − triggered_at)（小时）
- MTTR：窗口内已解决事件 avg(resolved_at − triggered_at)（小时）
- 误报率：is_false_positive=true 占已标记集（IS NOT NULL）
- 抑制统计：当前活跃抑制条数（alert_suppression.is_active，全量）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.handling_stats import _load_subtree_unit_ids

#: severity 堆叠柱固定序列（ck_alert_event_severity）
SEVERITY_ORDER = ("INFO", "WARN", "ERROR", "CRITICAL")


def _f(v: Any, digits: int = 1) -> float | None:
    if v is None:
        return None
    return round(float(v), digits)


def _build_filters(
    *,
    start: datetime,
    end: datetime,
    unit_ids: list[str] | None,
    severity: str | None,
    status: str | None,
    alias: str = "ae",
) -> tuple[str, dict[str, Any]]:
    """窗口 + 装置 + severity/status 过滤 WHERE 片段（触发时间归窗）。"""
    conds = [f"{alias}.triggered_at >= :start", f"{alias}.triggered_at < :end"]
    if unit_ids is not None:
        conds.append("ll.unit_id = ANY(:unit_ids)")
    if severity:
        conds.append(f"{alias}.severity = :severity")
    if status:
        conds.append(f"{alias}.status = :status")
    params: dict[str, Any] = {"start": start, "end": end}
    if unit_ids is not None:
        params["unit_ids"] = unit_ids
    if severity:
        params["severity"] = severity
    if status:
        params["status"] = status
    return " AND ".join(conds), params


async def build_alert_statistics(
    db: AsyncSession,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    plant_node_id: str | None = None,
    severity: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """预警统计报告聚合（§4.2）。

    - summary：预警总数 / 活跃数 / MTTA / MTTR（小时）/ 误报率（窗口内）+
      活跃抑制条数（当前态）
    - trend：按天 severity 堆叠量（INFO/WARN/ERROR/CRITICAL）
    - statusDistribution / severityDistribution：状态与严重度分布
    - topRules / topLoops：TOP10 规则 / 回路（计数 + 误报数）
    """
    if start is None or end is None:
        end = datetime.now(UTC).replace(tzinfo=None)
        start = end - timedelta(days=30)

    unit_ids = await _load_subtree_unit_ids(db, plant_node_id) if plant_node_id else None
    where, params = _build_filters(
        start=start, end=end, unit_ids=unit_ids, severity=severity, status=status
    )

    # 1) KPI 汇总（单行）
    summary_row = (
        await db.execute(
            text(
                f"""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE ae.status = 'ACTIVE') AS active,
                       AVG(EXTRACT(EPOCH FROM (ae.acknowledged_at - ae.triggered_at))
                           / 3600.0)
                           FILTER (WHERE ae.acknowledged_at IS NOT NULL) AS mtta_hours,
                       AVG(EXTRACT(EPOCH FROM (ae.resolved_at - ae.triggered_at))
                           / 3600.0)
                           FILTER (WHERE ae.resolved_at IS NOT NULL) AS mttr_hours,
                       COUNT(*) FILTER (WHERE ae.is_false_positive IS NOT NULL)
                           AS marked_total,
                       COUNT(*) FILTER (WHERE ae.is_false_positive = true) AS fp_total
                FROM alert_event ae
                JOIN loop_ledger ll ON ll.id = ae.loop_id
                WHERE {where}
                """
            ),
            params,
        )
    ).one()

    active_suppressions = (
        await db.execute(text("SELECT COUNT(*) FROM alert_suppression WHERE is_active = true"))
    ).scalar_one()

    # 2) 按天 severity 堆叠
    trend_rows = (
        await db.execute(
            text(
                f"""
                SELECT to_char(date_trunc('day', ae.triggered_at), 'YYYY-MM-DD') AS d,
                       ae.severity, COUNT(*) AS cnt
                FROM alert_event ae
                JOIN loop_ledger ll ON ll.id = ae.loop_id
                WHERE {where}
                GROUP BY 1, 2 ORDER BY 1
                """
            ),
            params,
        )
    ).all()
    trend: list[dict[str, Any]] = []
    by_day: dict[str, dict[str, Any]] = {}
    for r in trend_rows:
        row = by_day.setdefault(r.d, {"date": r.d, **dict.fromkeys(SEVERITY_ORDER, 0)})
        row[r.severity] = int(r.cnt)
    trend = list(by_day.values())

    # 3) 状态 / 严重度分布
    async def _dist(group_col: str) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                text(
                    f"""
                    SELECT ae.{group_col} AS k, COUNT(*) AS cnt
                    FROM alert_event ae
                    JOIN loop_ledger ll ON ll.id = ae.loop_id
                    WHERE {where}
                    GROUP BY 1 ORDER BY 2 DESC
                    """
                ),
                params,
            )
        ).all()
        return [{"key": r.k, "count": int(r.cnt)} for r in rows]

    status_distribution = await _dist("status")
    severity_distribution = await _dist("severity")

    # 4) TOP10 规则 / 回路（计数 + 误报数）
    top_rules = (
        await db.execute(
            text(
                f"""
                SELECT ae.rule_code,
                       MAX(ar.rule_name) AS rule_name,
                       COUNT(*) AS cnt,
                       COUNT(*) FILTER (WHERE ae.is_false_positive = true) AS fp_cnt
                FROM alert_event ae
                JOIN loop_ledger ll ON ll.id = ae.loop_id
                LEFT JOIN alert_rule ar ON ar.id = ae.rule_id
                WHERE {where}
                GROUP BY ae.rule_code ORDER BY cnt DESC LIMIT 10
                """
            ),
            params,
        )
    ).all()
    top_loops = (
        await db.execute(
            text(
                f"""
                SELECT ae.loop_id,
                       MAX(ll.tag_name) AS loop_tag_name,
                       COUNT(*) AS cnt,
                       COUNT(*) FILTER (WHERE ae.is_false_positive = true) AS fp_cnt
                FROM alert_event ae
                JOIN loop_ledger ll ON ll.id = ae.loop_id
                WHERE {where}
                GROUP BY ae.loop_id ORDER BY cnt DESC LIMIT 10
                """
            ),
            params,
        )
    ).all()

    total = int(summary_row.total)
    return {
        "summary": {
            "total": total,
            "active": int(summary_row.active),
            "mttaHours": _f(summary_row.mtta_hours),
            "mttrHours": _f(summary_row.mttr_hours),
            "falsePositiveRate": round(
                int(summary_row.fp_total) / int(summary_row.marked_total) * 100.0, 1
            )
            if int(summary_row.marked_total)
            else None,
            "activeSuppressions": int(active_suppressions),
        },
        "trend": trend,
        "statusDistribution": status_distribution,
        "severityDistribution": severity_distribution,
        "topRules": [
            {
                "ruleCode": r.rule_code,
                "ruleName": r.rule_name,
                "count": int(r.cnt),
                "falsePositives": int(r.fp_cnt),
            }
            for r in top_rules
        ],
        "topLoops": [
            {
                "loopId": str(r.loop_id),
                "loopTagName": r.loop_tag_name,
                "count": int(r.cnt),
                "falsePositives": int(r.fp_cnt),
            }
            for r in top_loops
        ],
    }
