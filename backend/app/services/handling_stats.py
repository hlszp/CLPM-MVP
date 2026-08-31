"""处置统计聚合 service（报告模块优化 P0-2 下沉 / P2-1 闭环增强，2026-08）。

从 endpoints/handling.py 内联聚合逻辑下沉为单一实现（方案 R1）：
- /handling/statistics（模块端点，契约不变）与
  /reports/handling-statistics（报告自持端点）共用本 service。
- 模块端点仅传 months → 行为与历史完全一致（全量聚合、北京时间月界）。
- 报告端点可附加 start/end/plant_node_id 筛选（时间窗按 created_at 过滤，
  闭环数指标按 verified_at 归窗；装置过滤经 WITH RECURSIVE 子树下钻 unit_id）。
- P2-1（方案 §5.1）：响应向后兼容只增字段——sla（按时闭环率 + WARN/BREACH）/
  suggestionFunnel（建议五态漏斗）/ verifyResult（整改有效率）/
  staffWorkload（直读 mv_staff_workload，全量口径）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 处置类型中文名（§5；endpoints/handling.py 引用回导）
ACTION_TYPE_LABELS = {
    "TUNING": "参数整定",
    "VALVE": "阀门检修",
    "INSTRUMENT": "仪表校验",
    "LINK": "链路修复",
    "PROCESS": "工艺调整",
    "UTILIZATION": "恢复投用",
    "RECONFIG": "组态改造",
    "OTHER": "其他",
}

#: 建议状态中文名 + 漏斗展示顺序（P2-1 建议漏斗，§5.1）
SUGGESTION_STATUS_LABELS = {
    "PENDING": "待审核",
    "ACCEPTED": "已接受",
    "CONVERTED": "已转工单",
    "REJECTED": "已驳回",
    "IGNORED": "已忽略",
}
_FUNNEL_ORDER = ("PENDING", "ACCEPTED", "CONVERTED", "REJECTED", "IGNORED")

_BJ_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 回路范围聚合 SQL（建议/工单双实体；handling.py 引用回导）
# ---------------------------------------------------------------------------

#: 建议侧按回路聚合子查询（loop_action_item，五态分布）
#: {lf} 为回路范围 WHERE 注入点（plantNodeId/importanceLevel 过滤下推，
#: 避免全表 GROUP BY 后外层过滤；无过滤时为空串）
_SU_AGG_SQL = """
    SELECT loop_id,
           COUNT(*) FILTER (WHERE status = 'PENDING')   AS su_pending,
           COUNT(*) FILTER (WHERE status = 'ACCEPTED')  AS su_accepted,
           COUNT(*) FILTER (WHERE status = 'CONVERTED') AS su_converted,
           COUNT(*) FILTER (WHERE status = 'REJECTED')  AS su_rejected,
           COUNT(*) FILTER (WHERE status = 'IGNORED')   AS su_ignored,
           COUNT(*) AS suggestion_total,
           MAX(suggested_at) AS last_suggested_at
    FROM loop_action_item{lf} GROUP BY loop_id
"""

#: 工单侧按回路聚合子查询（handling_order，六态分布 + 闭环率 + 最近处置；{lf} 同上）
#: 列一律以别名 t 限定——{lf} 可能注入 JOIN loop_ledger（topLoops 下推），
#: loop_ledger 也有 status 列，裸列名会触发 AmbiguousColumnError
_HO_AGG_SQL = """
    SELECT t.loop_id,
           COUNT(*) FILTER (WHERE t.status = 'PENDING')   AS ho_pending,
           COUNT(*) FILTER (WHERE t.status = 'EXECUTING') AS ho_executing,
           COUNT(*) FILTER (WHERE t.status = 'VERIFYING') AS ho_verifying,
           COUNT(*) FILTER (WHERE t.status = 'CLOSED')    AS ho_closed,
           COUNT(*) FILTER (WHERE t.status = 'REOPENED')  AS ho_reopened,
           COUNT(*) FILTER (WHERE t.status = 'CANCELLED') AS ho_cancelled,
           COUNT(*) AS order_total,
           COUNT(*) FILTER (WHERE t.verify_result IS NOT NULL) AS ho_verified,
           COUNT(*) FILTER (WHERE t.verify_result = 'INEFFECTIVE') AS ho_ineffective,
           MAX(t.started_at) AS last_handled_at,
           MAX(t.updated_at) AS last_order_at,
           (ARRAY_AGG(t.handler ORDER BY t.started_at DESC NULLS LAST)
               FILTER (WHERE t.handler IS NOT NULL))[1] AS last_handled_by,
           (ARRAY_AGG(
               (t.kpi_after ->> 'score')::float8 - (t.kpi_before ->> 'score')::float8
               ORDER BY t.verified_at DESC NULLS LAST)
               FILTER (WHERE t.status = 'CLOSED'
                       AND t.kpi_before ->> 'score' IS NOT NULL
                       AND t.kpi_after  ->> 'score' IS NOT NULL))[1] AS last_closed_kpi_delta
    FROM handling_order t{lf} GROUP BY t.loop_id
"""


def _build_loop_agg_sql(loop_filter: str = "") -> str:
    """组装双实体聚合 SQL；loop_filter 为建议/工单内层聚合的回路范围 WHERE。"""
    lf = f" WHERE {loop_filter}" if loop_filter else ""
    su = _SU_AGG_SQL.format(lf=lf)
    ho = _HO_AGG_SQL.format(lf=lf)
    return f"""
    SELECT ll.id AS loop_id, ll.tag_name AS loop_tag_name,
           ll.description AS loop_description, ll.importance_level, ll.unit_id,
           su.su_pending, su.su_accepted, su.su_converted, su.su_rejected,
           su.su_ignored, su.suggestion_total, su.last_suggested_at,
           ho.ho_pending, ho.ho_executing, ho.ho_verifying, ho.ho_closed,
           ho.ho_reopened, ho.ho_cancelled, ho.order_total, ho.ho_verified,
           ho.ho_ineffective, ho.last_handled_at, ho.last_order_at,
           ho.last_handled_by, ho.last_closed_kpi_delta
    FROM loop_ledger ll
    LEFT JOIN ({su}) su ON su.loop_id = ll.id
    LEFT JOIN ({ho}) ho ON ho.loop_id = ll.id
"""


# ---------------------------------------------------------------------------
# plant_node 工具（handling.py 引用回导）
# ---------------------------------------------------------------------------


def _build_unit_paths(rows: list[Any]) -> dict[str, str]:
    """plant_node 平铺行 → 节点全路径映射（"装置.单元" 树回溯）。"""
    nodes = {
        str(r.id): (r.name, str(r.parent_id) if r.parent_id else None)
        for r in rows
        if r.id is not None
    }
    paths: dict[str, str] = {}
    for nid in nodes:
        parts: list[str] = []
        cur: str | None = nid
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            node = nodes.get(cur)
            if node is None:
                break
            parts.append(node[0])
            cur = node[1]
        paths[nid] = ".".join(reversed(parts))
    return paths


async def _load_unit_paths(db: AsyncSession) -> dict[str, str]:
    rows = list((await db.execute(text("SELECT id, name, parent_id FROM plant_node"))).all())
    return _build_unit_paths(rows)


async def _load_subtree_unit_ids(db: AsyncSession, plant_node_id: str) -> list[str]:
    """plant_node 递归子树（含自身）→ unit id 列表（装置下钻筛选）。"""
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


# ---------------------------------------------------------------------------
# 统计聚合主体
# ---------------------------------------------------------------------------


def _shift_months_bj(dt_bj: datetime, back: int) -> datetime:
    """北京时间 naive 月份回退（无 dateutil 依赖的手写实现）。"""
    total = dt_bj.year * 12 + (dt_bj.month - 1) - back
    return dt_bj.replace(year=total // 12, month=total % 12 + 1)


async def build_handling_statistics(
    db: AsyncSession,
    *,
    months: int = 6,
    start: datetime | None = None,
    end: datetime | None = None,
    plant_node_id: str | None = None,
) -> dict[str, Any]:
    """处置统计聚合（§6.3，工单维度 + 建议驳回率）。

    - summary：period 闭环数（默认北京时间本月；传时间窗则按 verified_at 归窗）/
      闭环率 / 平均处置时长（创建→验证闭环）/ 无效重开率 / 平均 KPI 改善分 /
      驳回率（建议侧 REJECTED/已审核）/ 平均排程周期（工单创建→开工均值）；
      无数据时相关项为 null（前端空态显 —）
    - monthly：近 N 月（默认，北京时间月界按 verified_at 归月，空月补零）；
      传时间窗则按窗口逐月展开（上限 24 桶）
    - byType / byUnit / topLoops：类型分布、装置闭环分布、重开次数 Top 10（工单口径）
    - sla / suggestionFunnel / verifyResult / staffWorkload：P2-1 闭环增强
      （方案 §5.1，向后兼容只增字段）——按时闭环率 + WARN/BREACH 计数、
      建议五态漏斗、整改有效率、人员工作量（直读 mv_staff_workload，
      全量口径不随筛选；MV 不可用时降级为空列表）

    筛选语义（仅报告端点使用；模块端点不传，行为与历史一致）：
    - start/end：工单按 created_at 半开区间过滤（闭环数按 verified_at 归窗）；
      驳回率按 suggested_at 归窗
    - plant_node_id：经 WITH RECURSIVE 解析装置子树，过滤 loop_ledger.unit_id
    """
    now_bj = datetime.now(_BJ_TZ)
    month_start_bj = now_bj.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_utc = month_start_bj.astimezone(UTC).replace(tzinfo=None)

    unit_ids: list[str] | None = None
    if plant_node_id:
        unit_ids = await _load_subtree_unit_ids(db, plant_node_id)

    # 工单/建议聚合的公共过滤片段（summary/byType/byUnit/topLoops/驳回率共用）
    params: dict[str, Any] = {}
    ho_where = ["1=1"]
    if start is not None:
        ho_where.append("ho.created_at >= :win_start")
        params["win_start"] = start
    if end is not None:
        ho_where.append("ho.created_at < :win_end")
        params["win_end"] = end
    if unit_ids is not None:
        ho_where.append("ll.unit_id = ANY(:unit_ids)")
        params["unit_ids"] = unit_ids
    ho_join = "JOIN loop_ledger ll ON ll.id = ho.loop_id" if unit_ids is not None else ""

    su_where = ["1=1"]
    su_join = ""
    if start is not None:
        su_where.append("su.suggested_at >= :win_start")
    if end is not None:
        su_where.append("su.suggested_at < :win_end")
    if unit_ids is not None:
        su_where.append("ll.unit_id = ANY(:unit_ids)")
        su_join = "JOIN loop_ledger ll ON ll.id = su.loop_id"
    su_where_sql = " AND ".join(su_where)

    # --- summary（工单聚合；闭环数按 verified_at 归期） ---
    closed_from = start if start is not None else month_start_utc
    closed_to = end  # None → 不设上界（本月口径）
    summary_params = dict(params)
    summary_params["closed_from"] = closed_from
    summary_params["closed_to"] = closed_to
    s = (
        await db.execute(
            text(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE ho.status = 'CLOSED'
                                   AND ho.verified_at >= :closed_from
                                   AND (CAST(:closed_to AS timestamp) IS NULL
                                        OR ho.verified_at < :closed_to))
                    AS closed_period,
                  COUNT(*) FILTER (WHERE ho.status = 'CLOSED') AS closed_total,
                  COUNT(*) FILTER (WHERE ho.verify_result IS NOT NULL) AS verified_total,
                  COUNT(*) FILTER (WHERE ho.verify_result = 'INEFFECTIVE') AS ineffective_total,
                  AVG(EXTRACT(EPOCH FROM (ho.verified_at - ho.created_at)) / 3600.0)
                    FILTER (WHERE ho.status = 'CLOSED' AND ho.verified_at IS NOT NULL)
                    AS avg_cycle_hours,
                  AVG(EXTRACT(EPOCH FROM (ho.started_at - ho.created_at)) / 3600.0)
                    FILTER (WHERE ho.started_at IS NOT NULL)
                    AS avg_schedule_hours,
                  AVG((ho.kpi_after ->> 'score')::float8 - (ho.kpi_before ->> 'score')::float8)
                    FILTER (WHERE ho.status = 'CLOSED'
                            AND ho.kpi_before ->> 'score' IS NOT NULL
                            AND ho.kpi_after  ->> 'score' IS NOT NULL)
                    AS avg_kpi_delta
                FROM handling_order ho
                {ho_join}
                WHERE {" AND ".join(ho_where)}
                """
            ),
            summary_params,
        )
    ).one()

    # 建议驳回率（§6.3：REJECTED / 已审核（ACCEPTED+CONVERTED+REJECTED））
    rej = (
        await db.execute(
            text(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE su.status IN
                    ('ACCEPTED', 'CONVERTED', 'REJECTED')) AS reviewed_total,
                  COUNT(*) FILTER (WHERE su.status = 'REJECTED') AS rejected_total
                FROM loop_action_item su
                {su_join}
                WHERE {su_where_sql}
                """
            ),
            params,
        )
    ).one()

    def _rate(num: Any, den: Any) -> float | None:
        return round(float(num) / float(den), 4) if den else None

    def _hours(v: Any) -> float | None:
        return round(float(v), 1) if v else None

    summary = {
        "closedThisMonth": int(s.closed_period),
        "closeRate": _rate(s.closed_total, s.verified_total),
        "avgCycleHours": _hours(s.avg_cycle_hours),
        "ineffectiveRate": _rate(s.ineffective_total, s.verified_total),
        "avgKpiDelta": _hours(s.avg_kpi_delta),
        "rejectRate": _rate(rej.rejected_total, rej.reviewed_total),
        "avgScheduleHours": _hours(s.avg_schedule_hours),
    }

    # --- monthly：verified_at 归月（北京时间），空月补零 ---
    monthly_params: dict[str, Any] = {}
    monthly_where = ["verify_result IS NOT NULL AND verified_at >= :range_start"]
    if end is not None:
        monthly_where.append("verified_at < :win_end")
        monthly_params["win_end"] = end
    if unit_ids is not None:
        monthly_where.append("ll.unit_id = ANY(:unit_ids)")
        monthly_params["unit_ids"] = unit_ids
        monthly_join = "JOIN loop_ledger ll ON ll.id = ho.loop_id"
    else:
        monthly_join = ""

    if start is not None:
        # 传入时间窗：窗口逐月展开（上限 24 桶）
        start_bj = start.replace(tzinfo=UTC).astimezone(_BJ_TZ)
        end_bj = end.replace(tzinfo=UTC).astimezone(_BJ_TZ) if end is not None else now_bj
        range_start_bj = start_bj.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        bucket_end_bj = end_bj.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # 超过 24 个月时收敛到最近 24 个整月
        limit_start = _shift_months_bj(bucket_end_bj, 23)
        if range_start_bj < limit_start:
            range_start_bj = limit_start
    else:
        range_start_bj = _shift_months_bj(month_start_bj, months - 1)
        bucket_end_bj = month_start_bj
    range_start_utc = range_start_bj.astimezone(UTC).replace(tzinfo=None)
    monthly_params["range_start"] = range_start_utc

    monthly_rows = (
        await db.execute(
            text(
                f"""
                SELECT to_char(
                         (ho.verified_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai'),
                         'YYYY-MM') AS month,
                       COUNT(*) FILTER (WHERE ho.status = 'CLOSED') AS closed,
                       COUNT(*) AS verified
                FROM handling_order ho
                {monthly_join}
                WHERE {" AND ".join(monthly_where)}
                GROUP BY 1
                """
            ),
            monthly_params,
        )
    ).all()
    monthly_map = {r.month: (int(r.closed), int(r.verified)) for r in monthly_rows}
    monthly: list[dict[str, Any]] = []
    cur = range_start_bj
    while cur <= bucket_end_bj:
        key = cur.strftime("%Y-%m")
        closed, verified = monthly_map.get(key, (0, 0))
        monthly.append(
            {
                "month": key,
                "closed": closed,
                "closeRate": round(closed / verified, 4) if verified else None,
            }
        )
        cur = cur.replace(day=28) + timedelta(days=4)  # 跨月进位：28 号 +4 天必到下月
        cur = cur.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # --- byType（工单类型分布；同窗同装置过滤） ---
    by_type_rows = (
        await db.execute(
            text(
                f"""
                SELECT ho.action_type, COUNT(*) AS cnt
                FROM handling_order ho
                {ho_join}
                WHERE {" AND ".join(ho_where)}
                GROUP BY ho.action_type ORDER BY cnt DESC
                """
            ),
            params,
        )
    ).all()
    by_type = [
        {
            "type": r.action_type,
            "label": ACTION_TYPE_LABELS.get(r.action_type, r.action_type),
            "count": int(r.cnt),
        }
        for r in by_type_rows
    ]

    # --- byUnit（装置闭环分布；同窗同装置过滤。自带 ll JOIN，不复用 ho_join） ---
    by_unit_rows = (
        await db.execute(
            text(
                f"""
                SELECT COALESCE(pn.name, '未分配装置') AS unit,
                       COUNT(*) FILTER (WHERE ho.status = 'CLOSED') AS closed
                FROM handling_order ho
                JOIN loop_ledger ll ON ll.id = ho.loop_id
                LEFT JOIN plant_node pn ON pn.id = ll.unit_id
                WHERE {" AND ".join(ho_where)}
                GROUP BY 1 ORDER BY closed DESC
                """
            ),
            params,
        )
    ).all()
    by_unit = [{"unit": r.unit, "closed": int(r.closed)} for r in by_unit_rows]

    # --- topLoops（重开 Top 10；有筛选时注入内层聚合，避免全表 GROUP BY 后过滤） ---
    has_filters = start is not None or end is not None or unit_ids is not None
    if has_filters:
        top_filter = " AND ".join(f for f in ho_where if f != "1=1").replace("ho.", "t.")
        inner_ho = _HO_AGG_SQL.format(
            lf=f" JOIN loop_ledger t_ll ON t_ll.id = t.loop_id WHERE {top_filter}"
        )
    else:
        inner_ho = _HO_AGG_SQL.format(lf="")
    top_params: dict[str, Any] = {"unit_ids": params["unit_ids"]} if unit_ids is not None else {}
    # 时间窗筛选下推内层聚合时，:win_start/:win_end 绑定值必须随参（P2 修复）
    if "win_start" in params:
        top_params["win_start"] = params["win_start"]
    if "win_end" in params:
        top_params["win_end"] = params["win_end"]
    top_rows = list(
        (
            await db.execute(
                text(
                    f"""
                    SELECT * FROM (
                        SELECT ll.id AS loop_id, ll.tag_name AS loop_tag_name,
                               ll.importance_level, ll.unit_id,
                               COALESCE(ho.order_total, 0) AS order_total,
                               COALESCE(ho.ho_reopened, 0) AS ho_reopened,
                               COALESCE(ho.ho_ineffective, 0) AS ho_ineffective,
                               ho.last_closed_kpi_delta
                        FROM loop_ledger ll
                        LEFT JOIN ({inner_ho}) ho ON ho.loop_id = ll.id
                        {"" if unit_ids is None else "WHERE ll.unit_id = ANY(:unit_ids)"}
                    ) agg
                    WHERE agg.order_total > 0
                    ORDER BY agg.ho_reopened DESC, agg.ho_ineffective DESC,
                             agg.order_total DESC
                    LIMIT 10
                    """
                ),
                top_params,
            )
        ).all()
    )
    unit_paths = await _load_unit_paths(db)
    top_loops = [
        {
            "loopId": str(r.loop_id),
            "loopTagName": r.loop_tag_name,
            "unitPath": unit_paths.get(str(r.unit_id)) if r.unit_id else None,
            "orderTotal": int(r.order_total),
            "reopened": int(r.ho_reopened),
            "lastClosedKpiDelta": (
                round(float(r.last_closed_kpi_delta), 2)
                if r.last_closed_kpi_delta is not None
                else None
            ),
        }
        for r in top_rows
    ]

    # ------------------------------------------------------------------
    # P2-1 闭环增强（方案 §5.1）：SLA 达成率 / 建议漏斗 / 整改有效率 /
    # 人员工作量（向后兼容只增字段，/handling/statistics 契约不破）
    # ------------------------------------------------------------------

    # --- SLA 达成率 + 整改有效率（同窗同装置过滤的工单口径） ---
    sla_row = (
        await db.execute(
            text(
                f"""
                SELECT
                  COUNT(*) FILTER (WHERE ho.sla_stage = 'WARN')  AS sla_warn_count,
                  COUNT(*) FILTER (WHERE ho.sla_stage = 'BREACH') AS sla_breach_count,
                  COUNT(*) FILTER (WHERE ho.status = 'CLOSED'
                                   AND ho.sla_deadline_at IS NOT NULL) AS sla_closed_total,
                  COUNT(*) FILTER (WHERE ho.status = 'CLOSED'
                                   AND ho.sla_deadline_at IS NOT NULL
                                   AND ho.verified_at <= ho.sla_deadline_at) AS sla_on_time,
                  COUNT(*) FILTER (WHERE ho.verify_result = 'EFFECTIVE') AS effective_count,
                  COUNT(*) FILTER (WHERE ho.verify_result = 'INEFFECTIVE') AS ineffective_count
                FROM handling_order ho
                {ho_join}
                WHERE {" AND ".join(ho_where)}
                """
            ),
            params,
        )
    ).one()
    sla = {
        "onTimeRate": _rate(sla_row.sla_on_time, sla_row.sla_closed_total),
        "onTimeClosed": int(sla_row.sla_on_time),
        "slaClosedTotal": int(sla_row.sla_closed_total),
        "warnCount": int(sla_row.sla_warn_count),
        "breachCount": int(sla_row.sla_breach_count),
    }
    verify_result = {
        "effective": int(sla_row.effective_count),
        "ineffective": int(sla_row.ineffective_count),
        "effectiveRate": _rate(
            sla_row.effective_count,
            sla_row.effective_count + sla_row.ineffective_count,
        ),
    }

    # --- 建议漏斗（五态流转量，suggested_at 归窗同驳回率口径） ---
    funnel_rows = (
        await db.execute(
            text(
                f"""
                SELECT su.status, COUNT(*) AS cnt
                FROM loop_action_item su
                {su_join}
                WHERE {su_where_sql}
                GROUP BY su.status
                """
            ),
            params,
        )
    ).all()
    funnel_map = {r.status: int(r.cnt) for r in funnel_rows}
    suggestion_funnel = [
        {
            "status": st,
            "label": SUGGESTION_STATUS_LABELS[st],
            "count": funnel_map.get(st, 0),
        }
        for st in _FUNNEL_ORDER
    ]

    # --- 人员工作量（直读 mv_staff_workload 物化视图，零聚合成本） ---
    # 全量口径（MV 由 refresh-workbench-mv@5min 刷新），不随时间窗/装置筛选
    staff_workload: list[dict[str, Any]] = []
    try:
        mv_rows = (
            await db.execute(
                text(
                    """
                    SELECT user_name, active_count, closed_count, sla_warned_count
                    FROM mv_staff_workload
                    ORDER BY active_count DESC, closed_count DESC
                    LIMIT 20
                    """
                )
            )
        ).all()
        staff_workload = [
            {
                "userName": r.user_name or "未分配",
                "activeCount": int(r.active_count),
                "closedCount": int(r.closed_count),
                "slaWarnedCount": int(r.sla_warned_count),
            }
            for r in mv_rows
        ]
    except Exception:  # MV 缺失/未刷新等场景降级为空（报告域不因此 500）
        staff_workload = []

    return {
        "summary": summary,
        "monthly": monthly,
        "byType": by_type,
        "byUnit": by_unit,
        "topLoops": top_loops,
        "sla": sla,
        "suggestionFunnel": suggestion_funnel,
        "verifyResult": verify_result,
        "staffWorkload": staff_workload,
    }
