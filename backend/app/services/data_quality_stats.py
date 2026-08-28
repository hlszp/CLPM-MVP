"""数据质量统计聚合 service（报告模块优化 P1-1，2026-08-28）。

数据质量报告（方案 §4.1）自持聚合：只依赖 monitor/assess 基础模块数据，
可插拔模块（诊断/整定/处置）全拔时仍完整可用。

口径：
- 参评率：loop_ledger.include_in_evaluation 占比（全量，与 get_overview 一致）
- 数据健康率：窗口内 per-loop 好值率均值再做均值（0~100 口径，≤1.0 兼容
  0~1 比率量纲，同 report_stats._percent_mean / Task #21）
- INCONCLUSIVE 率：窗口内 kpi_snapshot_hourly INCONCLUSIVE 快照占比
- 可信度分布：loop_confidence_latest（每回路最新一次评估）A~E + 未评估
- 未参评原因归因（优先级）：未纳入参评 → L0 数据不足 → 评估 INCONCLUSIVE
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.handling_stats import _load_subtree_unit_ids, _load_unit_paths
from app.services.report_stats import _percent_mean

#: 每回路最新完整性巡检（DISTINCT ON，check_date 降序）
_LATEST_INTEGRITY_SQL = """
    SELECT DISTINCT ON (lis.loop_id)
           lis.loop_id, lis.pv_completeness, lis.overall_completeness,
           lis.status AS integrity_status, lis.check_date
    FROM loop_integrity_snapshot lis
    ORDER BY lis.loop_id, lis.check_date DESC
"""

#: 每回路最新适用性分层（fitness_level 非空的最新快照）
_LATEST_FITNESS_SQL = """
    SELECT DISTINCT ON (k.loop_id)
           k.loop_id, k.fitness_level
    FROM kpi_snapshot_hourly k
    WHERE k.fitness_level IS NOT NULL
    ORDER BY k.loop_id, k.ts_start DESC
"""

#: 每回路最新一次可信度评估（每回路单行表，直读）
_CONFIDENCE_SQL = """
    SELECT lcl.loop_id, lcl.confidence_level,
           lcl.status AS eval_status, lcl.eval_time
    FROM loop_confidence_latest lcl
"""


def _percent(v: Any, digits: int = 1) -> float | None:
    """0~1 完整度比率 → 百分比（None 透传）。"""
    if v is None:
        return None
    return round(float(v) * 100.0, digits)


def _good_value(v: Any) -> float | None:
    """好值率量纲兼容（同 _percent_mean 启发式：≤1.0 视为 0~1 比率）。"""
    if v is None:
        return None
    val = float(v)
    return round(val * 100.0, 1) if val <= 1.0 else round(val, 1)


def non_eval_reason(
    include_in_evaluation: bool,
    fitness_level: str | None,
    eval_status: str | None,
) -> str | None:
    """未参评原因归因（§4.1 明细表列；按优先级取首个命中）。"""
    if not include_in_evaluation:
        return "未纳入参评"
    if fitness_level == "L0":
        return "L0 数据不足"
    if eval_status == "INCONCLUSIVE":
        return "评估 INCONCLUSIVE"
    return None


async def build_data_quality_stats(
    db: AsyncSession,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    plant_node_id: str | None = None,
) -> dict[str, Any]:
    """数据质量报告聚合（§4.1）。

    - summary：回路总数 / 参评回路数 / 参评率（全量）、数据健康率与
      INCONCLUSIVE 率（窗口内，默认近 30 天）、可信度 A~E 分布
      （loop_confidence_latest 最新一次）
    - trend：按天数据健康率 / INCONCLUSIVE 率双折线（窗口内）
    - items：回路明细（装置.单元路径 / 最新完整性 / 窗口好值率 / 可信度 /
      fitness_level / 未参评原因）
    """
    if start is None or end is None:
        end = datetime.now(UTC).replace(tzinfo=None)
        start = end - timedelta(days=30)

    unit_ids = await _load_subtree_unit_ids(db, plant_node_id) if plant_node_id else None
    unit_filter = "WHERE ll.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    params: dict[str, Any] = {"start": start, "end": end}
    if unit_ids is not None:
        params["unit_ids"] = unit_ids

    # 1) 回路基数 + 参评（全量，不受窗口影响）
    loop_rows = (
        await db.execute(
            text(
                f"""
                SELECT ll.id, ll.tag_name, ll.description, ll.unit_id,
                       ll.include_in_evaluation
                FROM loop_ledger ll
                {unit_filter}
                ORDER BY ll.tag_name
                """
            ),
            params,
        )
    ).all()

    total = len(loop_rows)
    evaluable = sum(1 for r in loop_rows if r.include_in_evaluation)

    # 2) 窗口内 per-loop KPI 聚合（好值率均值 + INCONCLUSIVE 计数）
    kpi_rows = (
        await db.execute(
            text(
                f"""
                SELECT k.loop_id,
                       AVG(k.good_value_rate) AS avg_good_value,
                       COUNT(*) AS snap_total,
                       COUNT(*) FILTER (WHERE k.status = 'INCONCLUSIVE')
                           AS inconclusive_total
                FROM kpi_snapshot_hourly k
                JOIN loop_ledger ll ON ll.id = k.loop_id
                WHERE k.ts_start >= :start AND k.ts_start < :end
                      {"AND ll.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""}
                GROUP BY k.loop_id
                """
            ),
            params,
        )
    ).all()
    kpi_by_loop = {str(r.loop_id): r for r in kpi_rows}

    snap_total = sum(int(r.snap_total) for r in kpi_rows)
    inconclusive_total = sum(int(r.inconclusive_total) for r in kpi_rows)
    # 先按值归一量纲（≤1.0 视为比率 ×100），再均值（避免混合量纲时启发式失效）
    data_health_vals = [
        v for v in (_good_value(r.avg_good_value) for r in kpi_rows) if v is not None
    ]

    # 3) 按天趋势（健康率 + INCONCLUSIVE 率）
    trend: list[dict[str, Any]] = []
    trend_rows = (
        await db.execute(
            text(
                f"""
                SELECT to_char(date_trunc('day', k.ts_start), 'YYYY-MM-DD') AS d,
                       AVG(k.good_value_rate) AS health_rate,
                       COUNT(*) FILTER (WHERE k.status = 'INCONCLUSIVE')::float
                           / NULLIF(COUNT(*), 0) * 100 AS inconclusive_rate
                FROM kpi_snapshot_hourly k
                JOIN loop_ledger ll ON ll.id = k.loop_id
                WHERE k.ts_start >= :start AND k.ts_start < :end
                      {"AND ll.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""}
                GROUP BY 1 ORDER BY 1
                """
            ),
            params,
        )
    ).all()
    if trend_rows:
        # 量纲兼容：整批日均好值率 ≤1.0 视为 0~1 比率（同 _percent_mean 启发式）
        rates = [float(r.health_rate) for r in trend_rows if r.health_rate is not None]
        scale = 100.0 if rates and max(rates) <= 1.0 else 1.0
        trend = [
            {
                "date": r.d,
                "healthRate": round(float(r.health_rate) * scale, 1)
                if r.health_rate is not None
                else None,
                "inconclusiveRate": round(float(r.inconclusive_rate), 1)
                if r.inconclusive_rate is not None
                else None,
            }
            for r in trend_rows
        ]

    # 4) 最新完整性 / fitness / 可信度（全量最新态，与窗口无关）
    integrity_by_loop = {
        str(r.loop_id): r for r in (await db.execute(text(_LATEST_INTEGRITY_SQL))).all()
    }
    fitness_by_loop = {
        str(r.loop_id): r.fitness_level for r in (await db.execute(text(_LATEST_FITNESS_SQL))).all()
    }
    conf_by_loop = {str(r.loop_id): r for r in (await db.execute(text(_CONFIDENCE_SQL))).all()}

    # 可信度分布（A~E + 未评估，全量回路对齐 loop_confidence_latest）
    conf_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0, "UNKNOWN": 0}
    for r in loop_rows:
        c = conf_by_loop.get(str(r.id))
        level = (c.confidence_level if c else None) or "UNKNOWN"
        conf_counts[level] = conf_counts.get(level, 0) + 1

    # 5) 明细行
    unit_paths = await _load_unit_paths(db)
    items: list[dict[str, Any]] = []
    for r in loop_rows:
        loop_id = str(r.id)
        kpi = kpi_by_loop.get(loop_id)
        integ = integrity_by_loop.get(loop_id)
        conf = conf_by_loop.get(loop_id)
        fitness = fitness_by_loop.get(loop_id)
        items.append(
            {
                "loopId": loop_id,
                "loopTagName": r.tag_name,
                "loopDescription": r.description,
                "unitPath": unit_paths.get(r.unit_id, "") if r.unit_id else "",
                "includeInEvaluation": bool(r.include_in_evaluation),
                "pvCompleteness": _percent(integ.pv_completeness) if integ else None,
                "overallCompleteness": _percent(integ.overall_completeness) if integ else None,
                "integrityStatus": integ.integrity_status if integ else None,
                "checkedAt": integ.check_date.strftime("%Y-%m-%d") if integ else None,
                "goodValueRate": _good_value(kpi.avg_good_value) if kpi else None,
                "confidenceLevel": conf.confidence_level if conf else None,
                "evalStatus": conf.eval_status if conf else None,
                "evalTime": conf.eval_time.strftime("%Y-%m-%d %H:%M")
                if conf and conf.eval_time
                else None,
                "fitnessLevel": fitness,
                "nonEvalReason": non_eval_reason(
                    bool(r.include_in_evaluation), fitness, conf.eval_status if conf else None
                ),
            }
        )

    return {
        "summary": {
            "totalLoops": total,
            "evaluableLoops": evaluable,
            "evaluateRate": round(evaluable / total * 100.0, 1) if total else None,
            "dataHealthRate": _percent_mean(data_health_vals),
            "inconclusiveRate": round(inconclusive_total / snap_total * 100.0, 1)
            if snap_total
            else None,
            "confidenceDistribution": [{"level": k, "count": v} for k, v in conf_counts.items()],
        },
        "trend": trend,
        "items": items,
    }
