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

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sys_config import SysConfig

#: 阶段锁定 sys_config key
STAGE_LOCK_KEY = "report_stage_lock"

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


# ---------------------------------------------------------------------------
# 阶段判定与锁定（IA 优化 P3，2026-08-22）
# ---------------------------------------------------------------------------
_STAGE_ORDER = {"S1": 1, "S2": 2, "S3": 3}


async def get_stage_lock(db: AsyncSession) -> dict[str, Any]:
    """读取阶段锁定配置。

    返回: { locked: bool, lockedStage: 'S1'|'S2'|'S3'|None }
    """
    row = await db.execute(select(SysConfig).where(SysConfig.key == STAGE_LOCK_KEY))
    cfg = row.scalar_one_or_none()
    if not cfg or not cfg.value:
        return {"locked": False, "lockedStage": None}
    # value 格式：S1 / S2 / S3
    if cfg.value in ("S1", "S2", "S3"):
        return {"locked": True, "lockedStage": cfg.value}
    return {"locked": False, "lockedStage": None}


async def set_stage_lock(
    db: AsyncSession, stage: str | None, operator: str | None = None
) -> dict[str, Any]:
    """设置阶段锁定（stage=None 时解除锁定）。"""
    if stage is not None and stage not in ("S1", "S2", "S3"):
        raise ValueError(f"非法阶段: {stage}")
    cfg = (
        await db.execute(select(SysConfig).where(SysConfig.key == STAGE_LOCK_KEY))
    ).scalar_one_or_none()
    value = stage if stage else None
    description = (
        f"管理总览阶段锁定（{operator or 'system'}）"
        if stage
        else "管理总览阶段锁定（未锁定，自动判定）"
    )
    if cfg:
        cfg.value = value
        cfg.description = description
        cfg.updated_by = operator
    else:
        cfg = SysConfig(
            key=STAGE_LOCK_KEY,
            value=value,
            description=description,
            updated_by=operator,
        )
        db.add(cfg)
    await db.commit()
    return await get_stage_lock(db)


async def determine_maturity_stage(
    db: AsyncSession, plant_node_id: str | None = None
) -> dict[str, Any]:
    """基于数据库实际记录自动判定成熟度阶段。

    规则（§2.6 + §9.4）：
      - S1 基础可视：无诊断记录 **且** 无处置工单
      - S2 闭环管理：有诊断记录或处置工单（≥1 条）
      - S3 持续优化：有整定记录 + 有效果验证（kpi_before/after 非空）且 CLOSED 工单 ≥5 条

    返回:
      {
        detectedStage: 'S1'|'S2'|'S3',
        availability: { s1Available, s2Available, s3Available },
        counts: { diagnosisRuns, handlingOrders, tuningRecords, closedVerifiedOrders }
      }
    """
    unit_ids = await _resolve_subtree_unit_ids(db, plant_node_id)
    unit_join = ""
    params: dict[str, Any] = {}
    if unit_ids is not None:
        unit_join = "JOIN loop_ledger ll ON ll.id = t.loop_id WHERE ll.unit_id = ANY(:unit_ids)"
        params["unit_ids"] = unit_ids

    diag_count = int(
        (
            await db.execute(
                text(
                    f"SELECT COUNT(*) FROM diagnosis_run t {unit_join}"
                    if unit_join
                    else "SELECT COUNT(*) FROM diagnosis_run t"
                ),
                params if unit_join else {},
            )
        ).scalar()
        or 0
    )
    order_count = int(
        (
            await db.execute(
                text(
                    f"SELECT COUNT(*) FROM handling_order t {unit_join}"
                    if unit_join
                    else "SELECT COUNT(*) FROM handling_order t"
                ),
                params if unit_join else {},
            )
        ).scalar()
        or 0
    )
    tuning_count_val = int(
        (
            await db.execute(
                text(
                    f"""
                    SELECT COUNT(*) FROM tuning_record t
                    {unit_join.replace("t.loop_id", "t.loop_id") if unit_join else ""}
                    AND t.status = 'COMPLETED'
                    """
                    if unit_join
                    else "SELECT COUNT(*) FROM tuning_record t WHERE t.status = 'COMPLETED'"
                ),
                params if unit_join else {},
            )
        ).scalar()
        or 0
    )
    # S3 闭环 + 验证工单（CLOSED 且有 kpi_before/after）≥5
    closed_verified_where = [
        "t.status = 'CLOSED'",
        "t.kpi_before IS NOT NULL",
        "t.kpi_after IS NOT NULL",
    ]
    cv_params = dict(params) if unit_join else {}
    cv_join = ""
    if unit_ids is not None:
        cv_join = "JOIN loop_ledger ll ON ll.id = t.loop_id WHERE ll.unit_id = ANY(:unit_ids) AND "
    else:
        cv_join = "WHERE "
    closed_verified_count = int(
        (
            await db.execute(
                text(
                    f"SELECT COUNT(*) FROM handling_order t {cv_join}"
                    + " AND ".join(closed_verified_where)
                ),
                cv_params,
            )
        ).scalar()
        or 0
    )

    s2_avail = diag_count >= 1 or order_count >= 1
    s3_avail = tuning_count_val >= 1 and closed_verified_count >= 5

    if s3_avail:
        detected = "S3"
    elif s2_avail:
        detected = "S2"
    else:
        detected = "S1"

    return {
        "detectedStage": detected,
        "availability": {
            "s1Available": True,
            "s2Available": s2_avail,
            "s3Available": s3_avail,
        },
        "counts": {
            "diagnosisRuns": diag_count,
            "handlingOrders": order_count,
            "tuningRecords": tuning_count_val,
            "closedVerifiedOrders": closed_verified_count,
        },
    }


def resolve_effective_stage(
    requested: str,
    lock_info: dict[str, Any],
    maturity: dict[str, Any],
) -> tuple[str, str, bool]:
    """综合：请求阶段 + 锁定配置 + 自动判定 → 实际展示阶段。

    Returns: (effectiveStage, originStage, isLocked)
      - originStage: 实际阶段的来源（'AUTO' 自动 / 'LOCK' 锁定）
    """
    if lock_info.get("locked") and lock_info.get("lockedStage"):
        return lock_info["lockedStage"], lock_info["lockedStage"], True
    # 未锁定时，允许前端请求预览（请求 S2/S3 但条件不足时，自动降级到检测到的阶段）
    detected = maturity["detectedStage"]
    if _STAGE_ORDER.get(requested, 1) <= _STAGE_ORDER.get(detected, 1):
        return requested, "AUTO", False
    return detected, "AUTO", False


# ---------------------------------------------------------------------------
# 公共子查询工具
# ---------------------------------------------------------------------------


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
    """管理总览聚合（P3：S1/S2/S3 自适应填充，固定骨架不跳动）。"""
    # 1) 阶段判定 + 锁定（基于全量数据，不受 start/end 窗口影响）
    lock_info = await get_stage_lock(db)
    maturity = await determine_maturity_stage(db, plant_node_id)
    effective_stage, stage_origin, is_locked = resolve_effective_stage(stage, lock_info, maturity)

    unit_ids = await _resolve_subtree_unit_ids(db, plant_node_id)
    unit_paths = await _load_unit_paths(db)

    unit_filter = ""
    params: dict[str, Any] = {}
    if unit_ids is not None:
        unit_filter = "WHERE ll.unit_id = ANY(:unit_ids)"
        params["unit_ids"] = unit_ids

    avail = maturity["availability"]
    s2_enabled = avail["s2Available"] and _STAGE_ORDER.get(effective_stage, 1) >= 2
    s3_enabled = avail["s3Available"] and _STAGE_ORDER.get(effective_stage, 1) >= 3

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
    scored_ids: list[str] = []
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

    # ------------------------------------------------------------------
    # S1 KPI（5 个，P0 固定位置）
    # ------------------------------------------------------------------
    evaluable_total = evaluable or total
    eval_rate = _ratio(evaluated_count, evaluable_total)
    health_rate = _ratio(len(healthy), evaluated_count) if evaluated_count else None
    anomaly_count = len(anomaly)
    data_health_rate = (
        round(sum(data_health_vals) / len(data_health_vals) * 100.0, 1)
        if data_health_vals
        else None
    )

    # 健康率状态：参评样本中健康占比 ≥80% 视为 ok
    _hr = len(healthy) / evaluated_count if evaluated_count else 0
    health_status_val = "ok" if _hr >= 0.8 else ("warning" if evaluated_count else "neutral")
    eval_status = (
        "ok"
        if eval_rate is not None and eval_rate >= 80
        else ("warning" if eval_rate is not None and eval_rate >= 50 else "info")
    )
    dh_status = (
        "ok"
        if data_health_rate is not None and data_health_rate >= 90
        else ("warning" if data_health_rate is not None and data_health_rate >= 70 else "info")
    )

    kpis: list[dict[str, Any]] = [
        {
            "key": "totalLoops",
            "label": "回路总数",
            "value": total,
            "unit": "个",
            "status": "neutral",
            "context": f"其中可参评回路 {evaluable} 个（unit.is_eval 启用）",
        },
        {
            "key": "healthRate",
            "label": "健康率",
            "value": health_rate,
            "unit": "%",
            "status": health_status_val,
            "context": (
                f"参评回路 {evaluated_count} 中 ≥60 分 {len(healthy)} 条，"
                f"<60 分异常 {anomaly_count} 条"
            ),
        },
        {
            "key": "evaluationRate",
            "label": "参评率",
            "value": eval_rate,
            "unit": "%",
            "status": eval_status,
            "context": f"时间窗内实际参与评估 {evaluated_count} / 可参评 {evaluable_total}",
        },
        {
            "key": "anomalyCount",
            "label": "异常数",
            "value": anomaly_count,
            "unit": "个",
            "status": "error" if anomaly_count > 0 else "ok",
            "context": "评分 < 60 分的回路数量",
        },
        {
            "key": "dataHealthRate",
            "label": "数据健康率",
            "value": data_health_rate,
            "unit": "%",
            "status": dh_status,
            "context": "参评回路 PV Good 值率均值（质量码）",
        },
    ]

    # ------------------------------------------------------------------
    # S2 闭环指标 KPI + CLOSED 处置状态 map（TOP 回路列用）
    # ------------------------------------------------------------------
    closed_loop_trend: list[dict[str, Any]] | None = None
    anomaly_distribution_change: list[dict[str, Any]] | None = None
    handling_status_map: dict[str, str] = {}
    closed_this_month_val: int | None = None
    avg_cycle_hours_val: float | None = None
    closed_rate_val: float | None = None
    ineffective_rate_val: float | None = None

    if s2_enabled:
        # ---- S2 KPI: 工单聚合 ----
        ho_unit_filter = ""
        ho_params: dict[str, Any] = {}
        if unit_ids is not None:
            ho_unit_filter = "AND ll.unit_id = ANY(:unit_ids)"
            ho_params["unit_ids"] = unit_ids
        ho_stats_sql = f"""
            SELECT
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE ho.status = 'CLOSED') AS closed_cnt,
              COUNT(*) FILTER (WHERE ho.status = 'REOPENED') AS reopened_cnt,
              AVG(EXTRACT(EPOCH FROM (
                COALESCE(ho.verified_at, ho.updated_at) - COALESCE(ho.started_at, ho.created_at)
              )) / 3600.0)
                FILTER (WHERE ho.status IN ('CLOSED','VERIFYING')
                    AND (ho.started_at IS NOT NULL OR ho.created_at IS NOT NULL))
                AS avg_cycle_h,
              COUNT(*) FILTER (
                WHERE ho.status = 'CLOSED'
                  AND EXTRACT(YEAR FROM COALESCE(ho.verified_at, ho.updated_at))
                    = EXTRACT(YEAR FROM CURRENT_TIMESTAMP)
                  AND EXTRACT(MONTH FROM COALESCE(ho.verified_at, ho.updated_at))
                    = EXTRACT(MONTH FROM CURRENT_TIMESTAMP)
              ) AS closed_this_month
            FROM handling_order ho
            JOIN loop_ledger ll ON ll.id = ho.loop_id
            WHERE 1=1 {ho_unit_filter}
        """
        ho_stats_row = (await db.execute(text(ho_stats_sql), ho_params)).one()
        total_orders = int(ho_stats_row.total or 0)
        closed_cnt = int(ho_stats_row.closed_cnt or 0)
        reopened_cnt = int(ho_stats_row.reopened_cnt or 0)
        closed_this_month_val = int(ho_stats_row.closed_this_month or 0)
        avg_cycle_hours_val = (
            round(float(ho_stats_row.avg_cycle_h), 1)
            if ho_stats_row.avg_cycle_h is not None
            else None
        )
        closed_rate_val = _ratio(closed_cnt, total_orders)
        # 无效重开率=重开次数/(闭环+重开)，闭环=0时null
        _closed_plus_reopen = closed_cnt + reopened_cnt
        ineffective_rate_val = (
            round(reopened_cnt / _closed_plus_reopen * 100.0, 1) if _closed_plus_reopen else None
        )

        # ---- S2 KPI 追加到 kpis ----
        kpis.extend(
            [
                {
                    "key": "closedLoopRate",
                    "label": "闭环率",
                    "value": closed_rate_val,
                    "unit": "%",
                    "status": (
                        "ok" if closed_rate_val is not None and closed_rate_val >= 80 else "warning"
                    ),
                    "context": f"闭环 {closed_cnt} / 总工单 {total_orders}",
                },
                {
                    "key": "avgCycleHours",
                    "label": "平均处置时长",
                    "value": avg_cycle_hours_val,
                    "unit": "h",
                    "status": "neutral",
                    "context": "创建→闭环 小时均值",
                },
                {
                    "key": "closedThisMonth",
                    "label": "本月整改",
                    "value": closed_this_month_val,
                    "unit": "条",
                    "status": "neutral",
                    "context": "当月 CLOSED 工单数",
                },
                {
                    "key": "ineffectiveRate",
                    "label": "无效重开率",
                    "value": ineffective_rate_val,
                    "unit": "%",
                    "status": (
                        "error"
                        if ineffective_rate_val is not None and ineffective_rate_val >= 15
                        else "neutral"
                    ),
                    "context": f"重开 {reopened_cnt} / 闭环+重开 {_closed_plus_reopen}",
                },
            ]
        )

        # ---- 处置闭环趋势（近 6 个月，按月份聚合工单总数/闭环数）----
        trend_params = dict(ho_params)
        clt_where = ["1=1"]
        if unit_ids is not None:
            clt_where.append("ll.unit_id = ANY(:unit_ids)")
        clt_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT to_char(date_trunc('month', ho.created_at), 'YYYY-MM') AS d,
                           COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE ho.status = 'CLOSED') AS closed
                    FROM handling_order ho
                    JOIN loop_ledger ll ON ll.id = ho.loop_id
                    WHERE {" AND ".join(clt_where)}
                      AND ho.created_at >= CURRENT_TIMESTAMP - INTERVAL '6 months'
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                trend_params,
            )
        ).all()
        closed_loop_trend = [
            {
                "month": r.d,
                "total": int(r.total or 0),
                "closed": int(r.closed or 0),
                "closedRate": _ratio(int(r.closed or 0), int(r.total or 0)),
            }
            for r in clt_rows
        ]

        # ---- 异常类型分布变化（30 天 vs 上一周期 30 天）----
        adc_params: dict[str, Any] = {}
        if unit_ids is not None:
            adc_params["unit_ids"] = unit_ids
        adc_where_cur = [
            "dr.status IN ('SUCCESS','PARTIAL')",
            "dr.created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'",
        ]
        adc_where_prev = [
            "dr.status IN ('SUCCESS','PARTIAL')",
            "dr.created_at < CURRENT_TIMESTAMP - INTERVAL '30 days'",
            "dr.created_at >= CURRENT_TIMESTAMP - INTERVAL '60 days'",
        ]
        if unit_ids is not None:
            adc_where_cur.append("ll.unit_id = ANY(:unit_ids)")
            adc_where_prev.append("ll.unit_id = ANY(:unit_ids)")
        cur_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT COALESCE(dr.primary_category, 'UNKNOWN') AS cat, COUNT(*) AS cnt
                    FROM diagnosis_run dr
                    JOIN loop_ledger ll ON ll.id = dr.loop_id
                    WHERE {" AND ".join(adc_where_cur)}
                    GROUP BY 1
                    """
                ),
                adc_params,
            )
        ).all()
        prev_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT COALESCE(dr.primary_category, 'UNKNOWN') AS cat, COUNT(*) AS cnt
                    FROM diagnosis_run dr
                    JOIN loop_ledger ll ON ll.id = dr.loop_id
                    WHERE {" AND ".join(adc_where_prev)}
                    GROUP BY 1
                    """
                ),
                adc_params,
            )
        ).all()
        _prev_map = {r.cat: int(r.cnt or 0) for r in prev_rows}
        cur_total = sum(int(r.cnt or 0) for r in cur_rows) or 1
        prev_total = sum(_prev_map.values()) or 1
        anomaly_distribution_change = []
        for r in cur_rows:
            cat = r.cat
            cur = int(r.cnt or 0)
            prev = _prev_map.get(cat, 0)
            anomaly_distribution_change.append(
                {
                    "category": cat,
                    "label": _CATEGORY_LABELS.get(cat, "未分类"),
                    "currentCount": cur,
                    "previousCount": prev,
                    "currentRatio": round(cur / cur_total, 4),
                    "previousRatio": round(prev / prev_total, 4),
                    "deltaCount": cur - prev,
                }
            )

        # ---- TOP 回路处置状态：每个问题回路最新工单状态 ----
        if scored_ids:
            hs_rows = (
                await db.execute(
                    text(
                        """
                        SELECT DISTINCT ON (loop_id) loop_id, status
                        FROM handling_order
                        WHERE loop_id = ANY(:ids)
                        ORDER BY loop_id, created_at DESC
                        """
                    ),
                    {"ids": scored_ids},
                )
            ).all()
            handling_status_map = {str(r.loop_id): r.status for r in hs_rows}

    # ------------------------------------------------------------------
    # S3 持续优化 KPI + 收益趋势 + TOP 回路收益估算列
    # ------------------------------------------------------------------
    benefit_trend: list[dict[str, Any]] | None = None
    benefit_estimate_map: dict[str, float] = {}
    kpi_improvement_val: float | None = None
    auto_rate_improvement_val: float | None = None
    benefit_estimate_val: float | int | None = None  # P3 预留 null
    benchmark_gap_val: float | None = None

    if s3_enabled:
        # ---- S3 KPI：前后 KPI 改善均值（已闭环且有 kpi_before/after 工单）----
        bm_params: dict[str, Any] = {}
        if unit_ids is not None:
            bm_params["unit_ids"] = unit_ids
        bm_where = [
            "ho.status = 'CLOSED'",
            "ho.kpi_before IS NOT NULL",
            "ho.kpi_after IS NOT NULL",
        ]
        if unit_ids is not None:
            bm_where.append("ll.unit_id = ANY(:unit_ids)")
        kpi_imp_row = (
            await db.execute(
                text(
                    f"""
                    SELECT
                      AVG((ho.kpi_after ->> 'score')::float8
                          - (ho.kpi_before ->> 'score')::float8) AS score_delta,
                      AVG((ho.kpi_after ->> 'effectiveAutoRate')::float8
                          - (ho.kpi_before ->> 'effectiveAutoRate')::float8) AS auto_delta,
                      COUNT(*) AS n
                    FROM handling_order ho
                    JOIN loop_ledger ll ON ll.id = ho.loop_id
                    WHERE {" AND ".join(bm_where)}
                    """
                ),
                bm_params,
            )
        ).one()
        kpi_improvement_val = _f(kpi_imp_row.score_delta)
        # 自控率提升：百分点，近 90 天窗口自控率曲线首尾差
        auto_rate_improvement_val = _f(kpi_imp_row.auto_delta)

        # ---- 自控率提升曲线（近 90 天按天均值，作为收益趋势图底）----
        bt_params = dict(params)
        bt_where = ["k.ts_start >= CURRENT_TIMESTAMP - INTERVAL '90 days'"]
        if unit_ids is not None:
            bt_where.append("ll.unit_id = ANY(:unit_ids)")
        bt_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT to_char(date_trunc('day', k.ts_start), 'YYYY-MM-DD') AS d,
                           AVG(k.effective_auto_rate) AS auto_rate,
                           AVG(k.score) AS score
                    FROM kpi_snapshot_hourly k
                    JOIN loop_ledger ll ON ll.id = k.loop_id
                    WHERE {" AND ".join(bt_where)}
                    GROUP BY 1 ORDER BY 1
                    """
                ),
                bt_params,
            )
        ).all()
        benefit_trend = [
            {
                "date": r.d,
                "autoRate": _f(r.auto_rate),
                "score": _f(r.score),
            }
            for r in bt_rows
        ]
        # 90 天自控率提升（曲线首尾差）作为备选提升值
        if len(bt_rows) >= 2 and auto_rate_improvement_val is None:
            first_r = bt_rows[0]
            last_r = bt_rows[-1]
            if first_r.auto_rate is not None and last_r.auto_rate is not None:
                auto_rate_improvement_val = round(
                    float(last_r.auto_rate) - float(first_r.auto_rate), 1
                )

        # ---- 标杆差距：TOP 装置均分 - 最差装置均分（窗口内）----
        bg_where = ["ll.unit_id IS NOT NULL"]
        if unit_ids is not None:
            bg_where.append("ll.unit_id = ANY(:unit_ids)")
        if start_date and end_date:
            bg_where.append("k.ts_start >= :start AND k.ts_start < :end")
            bg_params_bg = dict(params)
        else:
            bg_params_bg = dict(bm_params)
        bg_rows = (
            await db.execute(
                text(
                    f"""
                    SELECT ll.unit_id, AVG(k.score) AS avg_score
                    FROM loop_ledger ll
                    JOIN kpi_snapshot_hourly k ON k.loop_id = ll.id
                    WHERE {" AND ".join(bg_where)}
                    GROUP BY ll.unit_id
                    HAVING COUNT(DISTINCT ll.id) >= 3
                    """
                ),
                bg_params_bg,
            )
        ).all()
        bg_scores = [float(r.avg_score) for r in bg_rows if r.avg_score is not None]
        if len(bg_scores) >= 2:
            benchmark_gap_val = round(max(bg_scores) - min(bg_scores), 1)

        benefit_estimate_val = None  # P3 预留：经济收益不做计算

        # ---- S3 KPI 追加 ----
        kpis.extend(
            [
                {
                    "key": "kpiImprovement",
                    "label": "KPI 改善",
                    "value": kpi_improvement_val,
                    "unit": "分",
                    "status": (
                        "ok"
                        if kpi_improvement_val is not None and kpi_improvement_val > 0
                        else "warning"
                    ),
                    "context": f"闭环 {int(kpi_imp_row.n or 0)} 单评分差值均值",
                },
                {
                    "key": "autoRateImprovement",
                    "label": "自控提升",
                    "value": auto_rate_improvement_val,
                    "unit": "pp",
                    "status": (
                        "ok"
                        if auto_rate_improvement_val is not None and auto_rate_improvement_val > 0
                        else "neutral"
                    ),
                    "context": "有效自控率百分点提升",
                },
                {
                    "key": "benchmarkGap",
                    "label": "标杆差",
                    "value": benchmark_gap_val,
                    "unit": "分",
                    "status": "neutral",
                    "context": "TOP 装置 vs 最差装置均分差",
                },
                # benefitEstimate 预留占位（固定 12 格，第 12 格=预估收益）
                {
                    "key": "benefitEstimate",
                    "label": "预估收益",
                    "value": benefit_estimate_val,
                    "unit": "万元",
                    "status": "neutral",
                    "context": "经济收益口径待配置",
                },
            ]
        )

        # ---- TOP 回路预估收益（取该回路处置闭环 score_delta，无则 null）----
        if scored_ids:
            be_rows = (
                await db.execute(
                    text(
                        """
                        SELECT loop_id,
                               ((kpi_after ->> 'score')::float8
                                 - (kpi_before ->> 'score')::float8) AS score_delta
                        FROM handling_order
                        WHERE loop_id = ANY(:ids)
                          AND status = 'CLOSED'
                          AND kpi_before IS NOT NULL AND kpi_after IS NOT NULL
                        ORDER BY updated_at DESC
                        """
                    ),
                    {"ids": scored_ids},
                )
            ).all()
            # 一个回路可能有多条，取最新（已按 updated_at DESC，去重保留第一条）
            seen: set[str] = set()
            for r in be_rows:
                lid = str(r.loop_id)
                if lid in seen:
                    continue
                seen.add(lid)
                if r.score_delta is not None:
                    benefit_estimate_map[lid] = round(float(r.score_delta), 1)

    # ------------------------------------------------------------------
    # TOP 问题回路：追加处置状态（S2）、预估收益（S3）列
    # ------------------------------------------------------------------
    for tl in top_loops:
        lid = tl["loopId"]
        if s2_enabled:
            tl["handlingStatus"] = handling_status_map.get(lid)
        else:
            tl["handlingStatus"] = None
        if s3_enabled:
            tl["benefitEstimate"] = benefit_estimate_map.get(lid)
        else:
            tl["benefitEstimate"] = None

    return {
        "stage": effective_stage,
        "stageOrigin": stage_origin,  # 'AUTO' | 'LOCK'
        "isLocked": is_locked,
        "availability": avail,
        "maturityCounts": maturity["counts"],
        "kpis": kpis,
        "healthTrend": health_trend,
        "closedLoopTrend": closed_loop_trend if s2_enabled else None,
        "anomalyDistributionChange": anomaly_distribution_change if s2_enabled else None,
        "benefitTrend": benefit_trend if s3_enabled else None,
        "topProblemLoops": top_loops,
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
    "determine_maturity_stage",
    "get_benefit",
    "get_diagnosis_statistics",
    "get_overview",
    "get_stage_lock",
    "resolve_effective_stage",
    "set_stage_lock",
    "STAGE_LOCK_KEY",
]
