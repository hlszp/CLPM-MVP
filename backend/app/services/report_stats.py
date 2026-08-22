"""统计报告聚合查询服务（IA 优化 P0，2026-08-22）。

三个只读聚合接口的业务逻辑：
- get_overview: 管理总览 S1 基础指标（健康率/参评率/异常数/数据健康率 + 健康趋势 + TOP 问题回路）
- get_diagnosis_statistics: 诊断统计（基于 DiagnosisRun 表，不使用旧 DiagnosisResult）
- get_benefit: 收益报告（整定记录 + KPI 快照 + 处置工单，仅技术指标）

设计约束：
- 不新增 DB 迁移，全部基于现有表查询。
- plant_node_id 按工厂节点子树（含自身）下钻过滤。
- 时间参数统一 naive UTC（与项目既有口径一致）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 诊断原因分类标签（与 diagnosis_v2._CATEGORY_LABELS 保持一致）
_CATEGORY_LABELS = {
    "TUNING": "参数问题（PID 整定）",
    "VALVE": "阀门/执行机构问题",
    "INSTRUMENT": "仪表/测量问题",
    "COMMUNICATION": "通信链路问题",
    "PROCESS": "工艺/外扰问题",
    "UTILIZATION": "投用/操作问题",
    "DESIGN": "组态/设计问题",
    "DATA_INSUFFICIENT": "数据不足/无法判定",
}

#: 置信度分桶
_CONFIDENCE_BUCKETS = [
    ("0-0.3", "低（<0.3）", 0.0, 0.3),
    ("0.3-0.6", "中（0.3~0.6）", 0.3, 0.6),
    ("0.6-0.8", "较高（0.6~0.8）", 0.6, 0.8),
    ("0.8-1.0", "高（≥0.8）", 0.8, 1.01),
]


async def _resolve_subtree_unit_ids(
    db: AsyncSession, plant_node_id: str | None
) -> list[str] | None:
    """plant_node 递归子树 → unit_id 列表；None 表示不按装置过滤。"""
    if not plant_node_id:
        return None
    rows = (
        await db.execute(
            text(
                """
                WITH RECURSIVE node_tree AS (
                    SELECT id FROM plant_node WHERE id = :root_id
                    UNION ALL
                    SELECT child.id FROM plant_node child
                    JOIN node_tree nt ON child.parent_id = nt.id
                )
                SELECT id FROM node_tree
                """
            ),
            {"root_id": plant_node_id},
        )
    ).all()
    return [str(r.id) for r in rows]


async def _load_unit_paths(db: AsyncSession) -> dict[str, str]:
    """unit_id → '装置/单元' 路径（直接父节点 + 自身名）。"""
    rows = (
        await db.execute(
            text(
                """
                SELECT n.id AS id, n.name AS name, p.name AS parent_name
                FROM plant_node n
                LEFT JOIN plant_node p ON p.id = n.parent_id
                """
            )
        )
    ).all()
    paths: dict[str, str] = {}
    for r in rows:
        paths[str(r.id)] = f"{r.parent_name}/{r.name}" if r.parent_name else r.name
    return paths


def _ratio(num: Any, den: Any, digits: int = 1) -> float | None:
    if not num or not den:
        return None
    return round(float(num) / float(den) * 100.0, digits)


def _f(v: Any, digits: int = 1) -> float | None:
    if v is None:
        return None
    return round(float(v), digits)


# ---------------------------------------------------------------------------
# 管理总览
# ---------------------------------------------------------------------------
async def get_overview(
    db: AsyncSession,
    stage: str,
    start_date: datetime | None,
    end_date: datetime | None,
    plant_node_id: str | None,
) -> dict[str, Any]:
    """管理总览聚合（P0 返回 S1 数据，S2/S3 字段恒 None）。"""
    unit_ids = await _resolve_subtree_unit_ids(db, plant_node_id)
    unit_paths = await _load_unit_paths(db)

    unit_filter = ""
    params: dict[str, Any] = {}
    if unit_ids is not None:
        unit_filter = "WHERE ll.unit_id = ANY(:unit_ids)"
        params["unit_ids"] = unit_ids

    # 回路基数 + 参评率 + 窗口内每回路均分（健康/异常判定）
    if start_date and end_date:
        snap_join = (
            "JOIN kpi_snapshot_hourly k ON k.loop_id = ll.id "
            "AND k.ts_start >= :start AND k.ts_start < :end"
        )
        params["start"] = start_date
        params["end"] = end_date
    else:
        snap_join = ""

    total_row = (
        await db.execute(
            text(
                f"""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE ll.include_in_evaluation = true) AS evaluable
                FROM loop_ledger ll
                {unit_filter}
                """
            ),
            params,
        )
    ).one()

    # 窗口内每回路平均得分（用于健康/异常计数 + 数据健康率）
    loop_avg_params = dict(params)
    if snap_join:
        loop_where = "WHERE ll.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
        loop_avg_sql = f"""
            SELECT ll.id AS loop_id,
                   AVG(k.score) AS avg_score,
                   AVG(k.good_value_rate) AS avg_good_value,
                   AVG(k.effective_auto_rate) AS avg_auto
            FROM loop_ledger ll
            {snap_join}
            {loop_where}
            GROUP BY ll.id
            """
    else:
        loop_avg_sql = f"""
            SELECT ll.id AS loop_id, NULL::float AS avg_score,
                   NULL::float AS avg_good_value, NULL::float AS avg_auto
            FROM loop_ledger ll
            {unit_filter}
            """
    loop_avg = (await db.execute(text(loop_avg_sql), loop_avg_params)).all()

    evaluated = [r for r in loop_avg if r.avg_score is not None]
    healthy = [r for r in evaluated if float(r.avg_score) >= 60.0]
    anomaly = [r for r in evaluated if float(r.avg_score) < 60.0]
    data_health_vals = [float(r.avg_good_value) for r in evaluated if r.avg_good_value is not None]

    total = int(total_row.total)
    evaluable = int(total_row.evaluable)
    evaluated_count = len(evaluated)

    # 健康趋势：按天均分
    health_trend: list[dict[str, Any]] = []
    if start_date and end_date:
        trend_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT to_char(date_trunc('day', k.ts_start), 'YYYY-MM-DD') AS d,
                           AVG(k.score) AS avg_score,
                           COUNT(DISTINCT k.loop_id) AS loop_count
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
        health_trend = [
            {
                "date": r.d,
                "score": _f(r.avg_score),
                "loopCount": int(r.loop_count),
            }
            for r in trend_rows
        ]

    # TOP 问题回路：均分最低的 10 条（有评分）+ 最近诊断主分类
    problem_loop_ids = [str(r.loop_id) for r in evaluated]
    top_loops: list[dict[str, Any]] = []
    if problem_loop_ids:
        scored = sorted(evaluated, key=lambda r: float(r.avg_score))[:10]
        scored_ids = [str(r.loop_id) for r in scored]
        score_map = {str(r.loop_id): _f(r.avg_score) for r in scored}
        diag_rows = (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT ON (loop_id)
                           loop_id, primary_category, severity
                    FROM diagnosis_run
                    WHERE loop_id = ANY(:ids) AND status IN ('SUCCESS', 'PARTIAL')
                    ORDER BY loop_id, created_at DESC
                    """
                ),
                {"ids": scored_ids},
            )
        ).all()
        diag_map = {str(r.loop_id): (r.primary_category, r.severity) for r in diag_rows}
        name_rows = (
            await db.execute(
                text("SELECT id, tag_name, unit_id FROM loop_ledger WHERE id = ANY(:ids)"),
                {"ids": scored_ids},
            )
        ).all()
        name_map = {
            str(r.id): (r.tag_name, str(r.unit_id) if r.unit_id else None) for r in name_rows
        }
        for lid in scored_ids:
            tag_name, uid = name_map.get(lid, (lid, None))
            cat, sev = diag_map.get(lid, (None, None))
            top_loops.append(
                {
                    "loopId": lid,
                    "loopTagName": tag_name,
                    "unitPath": unit_paths.get(uid) if uid else None,
                    "latestScore": score_map.get(lid),
                    "primaryCategory": cat,
                    "primaryCategoryLabel": _CATEGORY_LABELS.get(cat) if cat else None,
                    "severity": sev,
                }
            )

    # 健康率状态：参评样本中健康占比 ≥80% 视为 ok
    if evaluated_count:
        health_status = "ok" if len(healthy) / evaluated_count >= 0.8 else "warning"
    else:
        health_status = "neutral"

    kpis = [
        {
            "key": "totalLoops",
            "label": "回路总数",
            "value": total,
            "unit": "个",
            "status": "neutral",
            "context": f"参评 {evaluable} 个",
        },
        {
            "key": "healthRate",
            "label": "健康率",
            "value": _ratio(len(healthy), evaluated_count),
            "unit": "%",
            "status": health_status,
            "context": f"健康 {len(healthy)} / 参评 {evaluated_count}",
        },
        {
            "key": "evaluationRate",
            "label": "参评率",
            "value": _ratio(evaluated_count, total),
            "unit": "%",
            "status": "neutral",
            "context": f"已评 {evaluated_count} / 总数 {total}",
        },
        {
            "key": "anomalyCount",
            "label": "异常数",
            "value": len(anomaly),
            "unit": "个",
            "status": "error" if anomaly else "neutral",
            "context": "均分 < 60",
        },
        {
            "key": "dataHealthRate",
            "label": "数据健康率",
            "value": (
                round(sum(data_health_vals) / len(data_health_vals), 1)
                if data_health_vals
                else None
            ),
            "unit": "%",
            "status": "ok",
            "context": "PV 好值率均值",
        },
    ]

    return {
        "stage": stage,
        "kpis": kpis,
        "healthTrend": health_trend,
        "topProblemLoops": top_loops,
        "closedLoopTrend": None,
        "benefitTrend": None,
    }


# ---------------------------------------------------------------------------
# 诊断统计
# ---------------------------------------------------------------------------
async def get_diagnosis_statistics(
    db: AsyncSession,
    start_date: datetime | None,
    end_date: datetime | None,
    plant_node_id: str | None,
) -> dict[str, Any]:
    """诊断统计（基于 DiagnosisRun，不使用旧 DiagnosisResult）。"""
    unit_ids = await _resolve_subtree_unit_ids(db, plant_node_id)
    unit_paths = await _load_unit_paths(db)

    where = ["dr.status IN ('SUCCESS', 'PARTIAL')"]
    params: dict[str, Any] = {}
    if start_date:
        where.append("dr.created_at >= :start")
        params["start"] = start_date
    if end_date:
        where.append("dr.created_at < :end")
        params["end"] = end_date
    if unit_ids is not None:
        where.append("ll.unit_id = ANY(:unit_ids)")
        params["unit_ids"] = unit_ids
    where_sql = "WHERE " + " AND ".join(where)

    total_row = (
        await db.execute(
            text(
                f"""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE dr.status = 'SUCCESS') AS success_cnt,
                       COUNT(*) FILTER (WHERE dr.review_status = 'PENDING') AS pending_cnt
                FROM diagnosis_run dr
                JOIN loop_ledger ll ON ll.id = dr.loop_id
                {where_sql}
                """
            ),
            params,
        )
    ).one()
    total = int(total_row.total)

    # 分类分布
    cat_rows = (
        await db.execute(
            text(
                f"""
                SELECT COALESCE(dr.primary_category, 'UNKNOWN') AS category,
                       COUNT(*) AS cnt
                FROM diagnosis_run dr
                JOIN loop_ledger ll ON ll.id = dr.loop_id
                {where_sql}
                GROUP BY 1 ORDER BY cnt DESC
                """
            ),
            params,
        )
    ).all()
    category_distribution = [
        {
            "category": r.category,
            "label": _CATEGORY_LABELS.get(r.category, "未分类"),
            "count": int(r.cnt),
            "ratio": round(int(r.cnt) / total, 4) if total else 0,
        }
        for r in cat_rows
    ]

    # 置信度分布
    conf_rows = (
        await db.execute(
            text(
                f"""
                SELECT dr.primary_confidence AS conf
                FROM diagnosis_run dr
                JOIN loop_ledger ll ON ll.id = dr.loop_id
                {where_sql} AND dr.primary_confidence IS NOT NULL
                """
            ),
            params,
        )
    ).all()
    conf_vals = [float(r.conf) for r in conf_rows]
    confidence_distribution: list[dict[str, Any]] = []
    for key, label, lo, hi in _CONFIDENCE_BUCKETS:
        cnt = sum(1 for v in conf_vals if lo <= v < hi)
        confidence_distribution.append(
            {
                "range": key,
                "label": label,
                "count": cnt,
                "ratio": round(cnt / len(conf_vals), 4) if conf_vals else 0,
            }
        )

    # TOP 异常回路（诊断次数多 + HIGH 多）
    top_rows = (
        await db.execute(
            text(
                f"""
                SELECT dr.loop_id AS loop_id,
                       COUNT(*) AS run_count,
                       COUNT(*) FILTER (WHERE dr.severity = 'HIGH') AS high_count
                FROM diagnosis_run dr
                JOIN loop_ledger ll ON ll.id = dr.loop_id
                {where_sql}
                GROUP BY dr.loop_id
                ORDER BY high_count DESC, run_count DESC
                LIMIT 10
                """
            ),
            params,
        )
    ).all()
    top_ids = [str(r.loop_id) for r in top_rows]
    name_map: dict[str, tuple[str, str | None]] = {}
    if top_ids:
        name_rows = (
            await db.execute(
                text("SELECT id, tag_name, unit_id FROM loop_ledger WHERE id = ANY(:ids)"),
                {"ids": top_ids},
            )
        ).all()
        name_map = {
            str(r.id): (r.tag_name, str(r.unit_id) if r.unit_id else None) for r in name_rows
        }
        latest_rows = (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT ON (loop_id)
                           loop_id, primary_category, severity, primary_confidence
                    FROM diagnosis_run
                    WHERE loop_id = ANY(:ids) AND status IN ('SUCCESS', 'PARTIAL')
                    ORDER BY loop_id, created_at DESC
                    """
                ),
                {"ids": top_ids},
            )
        ).all()
        latest_map = {
            str(r.loop_id): (r.primary_category, r.severity, r.primary_confidence)
            for r in latest_rows
        }
    else:
        latest_map = {}

    top_abnormal: list[dict[str, Any]] = []
    for r in top_rows:
        lid = str(r.loop_id)
        tag_name, uid = name_map.get(lid, (lid, None))
        cat, sev, conf = latest_map.get(lid, (None, None, None))
        top_abnormal.append(
            {
                "loopId": lid,
                "loopTagName": tag_name,
                "unitPath": unit_paths.get(uid) if uid else None,
                "runCount": int(r.run_count),
                "highCount": int(r.high_count),
                "latestCategory": cat,
                "latestCategoryLabel": _CATEGORY_LABELS.get(cat) if cat else None,
                "latestSeverity": sev,
                "latestConfidence": _f(conf, 3) if conf is not None else None,
            }
        )

    # 趋势（按天）
    trend: list[dict[str, Any]] = []
    if start_date and end_date:
        trend_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT to_char(date_trunc('day', dr.created_at), 'YYYY-MM-DD') AS d,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE dr.severity = 'HIGH') AS high
                    FROM diagnosis_run dr
                    JOIN loop_ledger ll ON ll.id = dr.loop_id
                    {where_sql}
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                params,
            )
        ).all()
        trend = [{"date": r.d, "total": int(r.total), "high": int(r.high)} for r in trend_rows]

    return {
        "total": total,
        "successCount": int(total_row.success_cnt),
        "reviewPendingCount": int(total_row.pending_cnt),
        "categoryDistribution": category_distribution,
        "confidenceDistribution": confidence_distribution,
        "topAbnormalLoops": top_abnormal,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# 收益报告（技术指标）
# ---------------------------------------------------------------------------
async def get_benefit(
    db: AsyncSession,
    start_date: datetime | None,
    end_date: datetime | None,
    plant_node_id: str | None,
) -> dict[str, Any]:
    """收益报告：整定前后 KPI 对比、自控率提升曲线、装置标杆（仅技术指标）。"""
    unit_ids = await _resolve_subtree_unit_ids(db, plant_node_id)

    where = ["1=1"]
    params: dict[str, Any] = {}
    if start_date:
        where.append("ho.verified_at >= :start")
        params["start"] = start_date
    if end_date:
        where.append("ho.verified_at < :end")
        params["end"] = end_date
    if unit_ids is not None:
        where.append("ll.unit_id = ANY(:unit_ids)")
        params["unit_ids"] = unit_ids
    where_sql = "WHERE " + " AND ".join(where)

    # 整定记录数
    tuning_count = int(
        (
            await db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM tuning_record tr
                    WHERE tr.status = 'COMPLETED'
                    """
                )
            )
        ).scalar()
        or 0
    )

    # 处置工单前后 KPI 对比（kpi_before/kpi_after JSONB）
    cmp_row = (
        await db.execute(
            text(
                f"""
                SELECT
                  AVG((ho.kpi_before ->> 'score')::float8) AS before_score,
                  AVG((ho.kpi_after  ->> 'score')::float8) AS after_score,
                  AVG((ho.kpi_before ->> 'effectiveAutoRate')::float8) AS before_auto,
                  AVG((ho.kpi_after  ->> 'effectiveAutoRate')::float8) AS after_auto,
                  AVG((ho.kpi_before ->> 'goodValueRate')::float8) AS before_good,
                  AVG((ho.kpi_after  ->> 'goodValueRate')::float8) AS after_good,
                  AVG((ho.kpi_before ->> 'oscillationRate')::float8) AS before_osc,
                  AVG((ho.kpi_after  ->> 'oscillationRate')::float8) AS after_osc,
                  COUNT(*) FILTER (WHERE ho.status = 'CLOSED') AS closed_cnt
                FROM handling_order ho
                JOIN loop_ledger ll ON ll.id = ho.loop_id
                {where_sql}
                  AND ho.kpi_before IS NOT NULL AND ho.kpi_after IS NOT NULL
                """
            ),
            params,
        )
    ).one()

    def _delta(after: Any, before: Any) -> float | None:
        if after is None or before is None:
            return None
        return round(float(after) - float(before), 1)

    kpi_comparison = [
        {
            "metric": "score",
            "label": "综合评分",
            "before": _f(cmp_row.before_score),
            "after": _f(cmp_row.after_score),
            "delta": _delta(cmp_row.after_score, cmp_row.before_score),
            "unit": "分",
        },
        {
            "metric": "effectiveAutoRate",
            "label": "有效自控率",
            "before": _f(cmp_row.before_auto),
            "after": _f(cmp_row.after_auto),
            "delta": _delta(cmp_row.after_auto, cmp_row.before_auto),
            "unit": "%",
        },
        {
            "metric": "goodValueRate",
            "label": "PV 好值率",
            "before": _f(cmp_row.before_good),
            "after": _f(cmp_row.after_good),
            "delta": _delta(cmp_row.after_good, cmp_row.before_good),
            "unit": "%",
        },
        {
            "metric": "oscillationRate",
            "label": "振荡率",
            "before": _f(cmp_row.before_osc),
            "after": _f(cmp_row.after_osc),
            "delta": _delta(cmp_row.before_osc, cmp_row.after_osc),
            "unit": "%",
        },
    ]

    # 自控率提升曲线（按月，KpiSnapshotHourly 全量快照均值）
    curve: list[dict[str, Any]] = []
    if start_date and end_date:
        curve_params = dict(params)
        curve_where = ["k.ts_start >= :start", "k.ts_start < :end"]
        if unit_ids is not None:
            curve_where.append("ll.unit_id = ANY(:unit_ids)")
        curve_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT to_char(date_trunc('month', k.ts_start), 'YYYY-MM') AS d,
                           AVG(k.effective_auto_rate) AS avg_auto,
                           AVG(k.score) AS avg_score
                    FROM kpi_snapshot_hourly k
                    JOIN loop_ledger ll ON ll.id = k.loop_id
                    WHERE {" AND ".join(curve_where)}
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                curve_params,
            )
        ).all()
        curve = [
            {"date": r.d, "autoRate": _f(r.avg_auto), "score": _f(r.avg_score)} for r in curve_rows
        ]

    # 装置标杆：按 unit 聚合均分/自控率 + 该 unit 下已闭环工单改善均值
    bench_where = ["ll.unit_id IS NOT NULL"]
    if unit_ids is not None:
        bench_where.append("ll.unit_id = ANY(:unit_ids)")
    if start_date and end_date:
        bench_where.append("k.ts_start >= :start AND k.ts_start < :end")
    bench_rows = (
        await db.execute(
            text(
                f"""
                SELECT ll.unit_id AS unit_id,
                       AVG(k.score) AS avg_score,
                       AVG(k.effective_auto_rate) AS avg_auto,
                       COUNT(DISTINCT ll.id) AS loop_count
                FROM loop_ledger ll
                JOIN kpi_snapshot_hourly k ON k.loop_id = ll.id
                WHERE {" AND ".join(bench_where)}
                GROUP BY ll.unit_id
                ORDER BY avg_score DESC NULLS LAST
                LIMIT 20
                """
            ),
            params,
        )
    ).all()
    bench_unit_ids = [str(r.unit_id) for r in bench_rows if r.unit_id]
    delta_map: dict[str, float | None] = {}
    if bench_unit_ids:
        delta_rows = (
            await db.execute(
                text(
                    """
                    SELECT ll.unit_id AS unit_id,
                           AVG((ho.kpi_after ->> 'score')::float8
                               - (ho.kpi_before ->> 'score')::float8) AS avg_delta
                    FROM handling_order ho
                    JOIN loop_ledger ll ON ll.id = ho.loop_id
                    WHERE ll.unit_id = ANY(:ids)
                      AND ho.kpi_before IS NOT NULL AND ho.kpi_after IS NOT NULL
                    GROUP BY ll.unit_id
                    """
                ),
                {"ids": bench_unit_ids},
            )
        ).all()
        delta_map = {str(r.unit_id): _f(r.avg_delta) for r in delta_rows if r.unit_id}
    unit_paths = await _load_unit_paths(db)
    benchmark = [
        {
            "unitId": str(r.unit_id) if r.unit_id else None,
            "unitName": unit_paths.get(str(r.unit_id), "未分配装置") if r.unit_id else "未分配装置",
            "loopCount": int(r.loop_count),
            "avgScore": _f(r.avg_score),
            "avgAutoRate": _f(r.avg_auto),
            "avgDelta": delta_map.get(str(r.unit_id)),
        }
        for r in bench_rows
    ]

    return {
        "tuningCount": tuning_count,
        "closedOrderCount": int(cmp_row.closed_cnt or 0),
        "kpiComparison": kpi_comparison,
        "autoRateCurve": curve,
        "benchmark": benchmark,
    }


def default_report_window() -> tuple[datetime, datetime]:
    """默认近 30 天窗口（naive UTC，结束为当前时间）。"""
    end = datetime.utcnow()
    start = end - timedelta(days=30)
    return start, end


__all__ = [
    "default_report_window",
    "get_benefit",
    "get_diagnosis_statistics",
    "get_overview",
]
