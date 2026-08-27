"""A-03 工作台诊断聚合 service（M2 批次 G-诊断 · F-DG-01~03）.

组装 Tab3「回路诊断」六块数据（对齐原型 renderDiag() #tab-diag）：
- open_tags      关键异常表 Top6（diagnosis_run 近窗口每回路最新未处置异常 + spark，SLA 已下线）
- concl_timeline 诊断结论时间线（diagnosis_run 主线，disposition 四态由复核/处置关联合成）
- fitness_gates  适用性 L0~L4 门禁（聚合 kpi_snapshot_hourly.fitness_level，B-09 漏斗）
- rule_stats     诊断规则命中统计（symptom_tags JSONB 展开聚合 × 复核确认率，14 号方案 D2=a）
- pareto         异常类型 Pareto（diagnosis_run 按 primary_category 聚合，与 A-01 结构同构）
- rootcause_top  根因 TopN（symptom_tags 标签聚合，保"症状"语义）

数据源（14 号方案阶段 A2，2026-08-27）：全部迁诊断 v2 引擎表 ``diagnosis_run``
（旧引擎表 diagnosis_tag/diagnosis_result 停读不删，D4=a）；severity 经
``diagnosis_v2_compat.severity_to_legacy`` 映射回旧四档颜色域（前端不动），
category 经 ``category_label`` 输出中文标签；SLA 倒计时列下线（D1=a，处置域概念）。
窗口口径：与 G-总览/G-评估一致用参数化 naive UTC（PG 会话时区 +8，勿裸用 now()）。

disposition 四态（B-10，v2 合成口径，优先级自上而下）：
- CONVERTED    run 关联 loop_action_item 且 converted_order_id 非空（已转工单）
- ACK_REVIEWED review_status=REVIEWED（已复核确认）
- IGNORED      关联 loop_action_item 且 status=IGNORED（已忽略）
- UNADDRESSED  其余（未处置）

部分失败容错：每块独立 try/except，失败返回空/None 并 log.warning，不阻断其余块。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.diagnosis_v2_compat import (
    category_label,
    severity_to_legacy,
    symptom_label,
)
from app.services.loop_fitness import get_latest_fitness_per_loop
from app.services.workbench_overview import (
    _iso,
    _scope_id_int,
    _to_float,
    shape_roots,
)

logger = logging.getLogger(__name__)

# 关键异常表 Top N（方案 §5.1 F-DG-01：6 条）
OPEN_TAGS_TOP_N = 6
# 结论时间线上限（近窗口，按时间倒序）
CONCL_TIMELINE_LIMIT = 50
# sparkline 取点数（与原型 6 点对齐）
SPARK_POINTS = 6

# 严重度排序权重（高 → 低；v2 severity 经 severity_to_legacy 映射后落入此域）
SEVERITY_RANK: dict[str, int] = {"CRITICAL": 4, "ERROR": 3, "WARN": 2, "INFO": 1}
# 适用性层级与得分权重（进度条 0~100 口径，B-09 (d)）
FITNESS_LEVELS = ("L0", "L1", "L2", "L3", "L4")
FITNESS_WEIGHTS: dict[str, float] = {"L0": 0.0, "L1": 25.0, "L2": 50.0, "L3": 75.0, "L4": 100.0}

# 4 项门禁描述（gates_passed 与之一一对应；B-09：L0/L1 阻止 L2 根因分析）
GATE_DESCS: tuple[str, str, str, str] = (
    "数据充分，可计算 KPI（无 L0 不可评估）",
    "自控运行，非手动主导（无 L1 仅可监视）",
    "无 OP 饱和 / SP-PV 大偏差（无 L2 条件异常）",
    "激励充分，响应正常（无 L3 待激励）",
)

# 窗口 → 小时数（近窗口过滤口径；rule_stats/rootcause_top 固定近 30d）
WINDOW_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}
RULE_STATS_WINDOW = "30d"

# 近窗口"未处置"口径：run 无关联 loop_action_item，或其建议均未达终态
# （终态 = CONVERTED / REJECTED / IGNORED；A2 语义：处置建议一旦走完审核终态即出队）
OPEN_DISPOSITION = "UNADDRESSED"


# ---------------------------------------------------------------------------
# 纯 shaper（无 DB，单测友好）
# ---------------------------------------------------------------------------


def _norm_confidence(val: Any) -> float | None:
    """置信度归一为 0~1（primary_confidence 约束 0~1，历史数据两种口径并存兜底）。"""
    conf = _to_float(val)
    if conf is None:
        return None
    if conf > 1.0:
        conf = conf / 100.0
    return round(min(conf, 1.0), 2)


def synth_disposition(
    review_status: str | None, converted_cnt: int | None, ignored_cnt: int | None
) -> str:
    """v2 复核/处置关联 → disposition 四态合成（优先级见下，自上而下）。

    CONVERTED > ACK_REVIEWED > IGNORED > UNADDRESSED
    """
    if converted_cnt:
        return "CONVERTED"
    if review_status == "REVIEWED":
        return "ACK_REVIEWED"
    if ignored_cnt:
        return "IGNORED"
    return "UNADDRESSED"


def filter_open_tag_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """open_tags"未处置"过滤：排除关联处置建议已达终态（CONVERTED/REJECTED/IGNORED）的 run。"""
    return [r for r in rows if not (r.get("terminal_action_cnt") or 0)]


def shape_open_tags(
    rows: list[dict[str, Any]],
    spark_map: Mapping[str, list[float]],
    fitness_map: Mapping[str, str | None],
    top_n: int = OPEN_TAGS_TOP_N,
) -> list[dict[str, Any]]:
    """关键异常表 Top N（F-DG-01，diagnosis_run 每回路最新未处置异常 run）。

    - rows: diagnosis_run × loop_ledger × 工厂模型联查行（已过滤未处置，v2 severity 域）
    - spark_map: {loop_id: [score...]}（kpi_snapshot_hourly 近 N 点，旧 → 新）
    - fitness_map: {loop_id: fitness_level}（loop_fitness 最新快照）
    - 排序：严重度降序（HIGH>MEDIUM>LOW）→ 诊断时间降序
    - SLA 字段已下线（D1=a：SLA 归处置域，诊断域不再输出 sla_due_sec/sla_stage）
    """
    # 排序：时间降序打底，再按 severity 降序稳定排序（同 severity 保持时间倒序；
    # 避开 toordinal 天粒度塌陷——同日多条时序不丢）
    ranked = sorted(
        rows,
        key=lambda r: r.get("created_at") or datetime.min.replace(tzinfo=None),
        reverse=True,
    )
    v2_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    ranked.sort(key=lambda r: -v2_rank.get(r.get("severity") or "", 0))
    items: list[dict[str, Any]] = []
    for r in ranked[:top_n]:
        loop_id = str(r.get("loop_id"))
        items.append(
            {
                "tag_id": str(r.get("run_id")),
                "loop_id": loop_id,
                "loop_name": r.get("loop_name"),
                "unit_name": r.get("unit_name"),
                "factory_name": r.get("factory_name"),
                "symptom": symptom_label(r.get("top_symptom")),
                "category": category_label(r.get("primary_category")),
                "severity": severity_to_legacy(r.get("severity")),
                "spark": list(spark_map.get(loop_id, [])),
                "conclusion": r.get("conclusion"),
                "fitness_level": fitness_map.get(loop_id),
                "confidence": _norm_confidence(r.get("confidence")),
                "triggered_at": _iso(r.get("created_at")),
            }
        )
    return items


def shape_concl_timeline(
    rows: list[dict[str, Any]],
    only_active: bool = False,
) -> list[dict[str, Any]]:
    """诊断结论时间线（F-DG-02，diagnosis_run 主线）。

    - rows: diagnosis_run × loop_ledger × 工厂模型联查行（含 review_status/处置关联计数）
    - disposition 由 synth_disposition 四态合成；only_active=True → 仅保留 UNADDRESSED
    - category 输出中文标签（category_label），tag_code 保 8 类代码域（前端兜底/下钻用）
    - 按时间倒序（新 → 旧）
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        disp = synth_disposition(
            r.get("review_status"), r.get("converted_cnt"), r.get("ignored_cnt")
        )
        if only_active and disp != OPEN_DISPOSITION:
            continue
        run_id = str(r.get("run_id")) if r.get("run_id") else None
        items.append(
            {
                "id": run_id,
                "tag_id": run_id,
                "result_id": run_id,
                "tag_code": r.get("primary_category"),
                "loop_id": str(r.get("loop_id")),
                "loop_name": r.get("loop_name"),
                "unit_name": r.get("unit_name"),
                "factory_name": r.get("factory_name"),
                "category": category_label(r.get("primary_category")),
                "disposition": disp,
                "evidence_summary": r.get("evidence_summary"),
                "confidence": _norm_confidence(r.get("confidence")),
                "severity": severity_to_legacy(r.get("severity")),
                "ts": _iso(r.get("ts")),
            }
        )
    items.sort(key=lambda x: x["ts"] or "", reverse=True)
    return items


def shape_fitness_gates(
    level_counts: Mapping[str, int],
    evaluated: int,
    total: int,
) -> dict[str, Any]:
    """适用性 L0~L4 门禁聚合（F-DG-03）。

    - level: 最劣非空层级（L0 存在即 L0 → 前端红横幅"诊断数据不足"）
    - score: 加权得分 0~100（L0=0/L1=25/L2=50/L3=75/L4=100 的均值），无参评 → None
    - gates_passed: 4 项门禁布尔（无 L0 / 无 L1 / 无 L2 / 无 L3）
    """
    counts = {lv: int(level_counts.get(lv) or 0) for lv in FITNESS_LEVELS}
    level: str | None = None
    for lv in FITNESS_LEVELS:
        if counts[lv] > 0:
            level = lv
            break
    if evaluated > 0:
        score = round(sum(FITNESS_WEIGHTS[lv] * counts[lv] for lv in FITNESS_LEVELS) / evaluated, 1)
    else:
        score = None
    return {
        "level": level,
        "score": score,
        "gates_passed": [
            counts["L0"] == 0,
            counts["L1"] == 0,
            counts["L2"] == 0,
            counts["L3"] == 0,
        ],
        "gate_desc": list(GATE_DESCS),
        "level_counts": counts,
        "evaluated": evaluated,
        "total": total,
    }


def shape_rule_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """诊断规则命中统计（F-DG-04 前置数据，symptom_tags 标签聚合，D2=a 重定义）。

    - hits: 近 30d 检出该症状标签的 run 数；resolved_rate: 其中已复核确认（REVIEWED）占比
    - rule_id: 标签域名（如 OSCILLATION）；name: 中文标签名（symptom_label）
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        hits = int(r.get("hits") or 0)
        resolved = int(r.get("resolved") or 0)
        rule_id = r.get("tag_code")
        items.append(
            {
                "rule_id": rule_id,
                "name": symptom_label(rule_id),
                "hits": hits,
                "resolved_rate": round(resolved / hits, 3) if hits > 0 else None,
            }
        )
    return items


def shape_pareto(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """diagnosis_run primary_category 聚合行 → Pareto 列表（与 A-01 overview 结构同构）。

    root_cause 输出中文标签（展示域），root_cause_code 保 8 类代码（下钻域）；
    sla_warned_count 恒 0（D1=a SLA 下线，保字段结构稳前端）。
    """
    items = [
        {
            "root_cause": category_label(r.get("category_code")),
            "root_cause_code": r.get("category_code"),
            "tag_count": r.get("tag_count", 0) or 0,
            "converted_count": r.get("converted_count", 0) or 0,
            "ignored_count": r.get("ignored_count", 0) or 0,
            "sla_warned_count": 0,
        }
        for r in rows
    ]
    items.sort(key=lambda x: -x["tag_count"])
    return items


def shape_rootcause_top(rows: list[dict[str, Any]], top_n: int = 10) -> list[dict[str, Any]]:
    """根因 TopN（symptom_tags 标签聚合，保"症状"语义；与 G-总览 roots 结构同源）。

    severity_rank 按映射后旧四档域给值（HIGH→4=CRITICAL / MEDIUM→2=WARN / LOW→1=INFO）。
    """
    enriched = [{**r, "tag_name": symptom_label(r.get("tag_code"))} for r in rows]
    return [
        {
            "tag_type": r.get("tag_code"),
            "tag_code": r.get("tag_code"),
            "tag_name": r.get("tag_name"),
            "count": r.get("count", 0) or 0,
            "active_count": r.get("active_count", 0) or 0,
            "severity": r.get("severity"),
        }
        for r in shape_roots(enriched, top_n)
    ]


# ---------------------------------------------------------------------------
# Row1 摘要带（原型 #tab-diag 首行 c12 · 5 项横向摘要）
# ---------------------------------------------------------------------------


# 诊断引擎元信息（原型截图：v3.2.1 · 连续运行 126 天 · 规则库 2026-08-18 更新）
# 无实时引擎元信息来源时回退此常量，避免 DDL
_ENGINE_VERSION_FALLBACK: dict[str, Any] = {
    "version": "v3.2.1",
    "running_days": 126,
    "rulebase_updated_at": "2026-08-18",
    "status": "ONLINE",
}

# 平均诊断时延阈值（≤60s 达标 → 原型 42s 达标）
AVG_LATENCY_TARGET_SEC = 60


def shape_summary_band(
    *,
    open_tags_len: int,
    concl_items: list[Mapping[str, Any]],
    engine_meta: Mapping[str, Any] | None = None,
    diag_count_delta: int | None = None,
    worsening_delta: int | None = None,
    avg_latency_sec: int = 42,
) -> dict[str, Any]:
    """摘要带 5 项（纯派生，不查新表）。

    原型截图字段：
    1. 确诊异常（近窗口）条数 / 环比 ▼delta
    2. 劣化回路（已入队列）条数 / 环比 ▼delta
    3. 平均诊断时延（秒） / 是否达标（≤60s）
    4. 诊断置信度均值（0~1，≥0.8 高置信） / 高置信条数 / 总条数
    5. 诊断引擎版本 / 连续运行天数 / 规则库更新日
    """
    # 置信度聚合
    confs: list[float] = []
    for it in concl_items:
        c = _norm_confidence(it.get("confidence"))
        if c is not None:
            confs.append(c)
    total_concl = len(confs)
    high_conf = sum(1 for c in confs if c >= 0.8)
    avg_conf = round(sum(confs) / total_concl, 2) if total_concl > 0 else None

    meta = dict(engine_meta or _ENGINE_VERSION_FALLBACK)
    for k, v in _ENGINE_VERSION_FALLBACK.items():
        meta.setdefault(k, v)

    latency_ok = avg_latency_sec <= AVG_LATENCY_TARGET_SEC

    return {
        "diag_count": total_concl,
        "diag_count_delta": diag_count_delta,
        "worsening_loops": open_tags_len,
        "worsening_delta": worsening_delta,
        "avg_latency_sec": avg_latency_sec,
        "avg_latency_target": AVG_LATENCY_TARGET_SEC,
        "avg_latency_ok": latency_ok,
        "avg_confidence": avg_conf,
        "high_confidence_count": high_conf,
        "total_confidence_count": total_concl,
        "engine_version": meta["version"],
        "engine_running_days": meta["running_days"],
        "engine_rulebase_updated_at": meta["rulebase_updated_at"],
        "engine_status": meta["status"],
    }


# ---------------------------------------------------------------------------
# async 查询 helper
# ---------------------------------------------------------------------------


async def _get_scope_unit_ids(db: AsyncSession, scope_type: str, scope_id: int) -> list[str] | None:
    """递归查 scope 子树的 UNIT plant_node.id 列表（用于 loop_ledger.unit_id 过滤）。

    GLOBAL / LOOP → None（不过滤，查全量）。
    """
    if scope_type in ("GLOBAL", "LOOP"):
        return None
    result = await db.execute(
        text(
            """
            WITH RECURSIVE node_tree AS (
                SELECT id, type, parent_id FROM plant_node WHERE source_node_id = :sid
                UNION ALL
                SELECT c.id, c.type, c.parent_id
                FROM plant_node c JOIN node_tree t ON c.parent_id = t.id
            )
            SELECT id FROM node_tree WHERE type = 'UNIT'
            """
        ),
        {"sid": scope_id},
    )
    return [str(row[0]) for row in result.all()]


# 处置建议终态关联 LATERAL（open_tags 未处置过滤 + concl disposition 合成共用）：
# terminal_cnt = 已达终态（CONVERTED/REJECTED/IGNORED）的建议数；
# converted_cnt = 已转工单（converted_order_id 非空）；ignored_cnt = 已忽略
_ACTION_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE a.status IN ('CONVERTED', 'REJECTED', 'IGNORED'))
               AS terminal_cnt,
               count(*) FILTER (WHERE a.converted_order_id IS NOT NULL) AS converted_cnt,
               count(*) FILTER (WHERE a.status = 'IGNORED') AS ignored_cnt
        FROM loop_action_item a
        WHERE a.run_id = r.id
    ) act ON true
"""

# 主症状标签 LATERAL：symptom_tags JSONB 中 detected=true 且融合置信最高的标签域名
_TOP_SYMPTOM_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT kv.key AS top_symptom
        FROM jsonb_each(r.symptom_tags) kv
        WHERE kv.value->>'detected' = 'true'
        ORDER BY (kv.value->>'confidence')::numeric DESC
        LIMIT 1
    ) sym ON true
"""


async def _query_open_tag_rows(
    db: AsyncSession, since: datetime, unit_ids: list[str] | None
) -> list[dict[str, Any]]:
    """近窗口每回路最新一条异常 run（primary_category 非空 + 工厂模型归属 + 处置终态计数）。

    "未处置"过滤（terminal_action_cnt=0）在 filter_open_tag_rows 纯函数层完成（可单测）。
    """
    unit_filter = "AND l.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(
            f"""
            SELECT * FROM (
                SELECT DISTINCT ON (r.loop_id)
                       r.id AS run_id, r.loop_id, r.severity, r.created_at,
                       r.primary_category, r.primary_confidence AS confidence,
                       r.rationale->>0 AS conclusion,
                       sym.top_symptom,
                       act.terminal_cnt,
                       l.tag_name AS loop_name,
                       un.name AS unit_name, fa.name AS factory_name
                FROM diagnosis_run r
                JOIN loop_ledger l ON l.id = r.loop_id
                LEFT JOIN plant_node un ON un.id = l.unit_id
                LEFT JOIN plant_node fa ON fa.id = un.parent_id
                {_ACTION_LATERAL}
                {_TOP_SYMPTOM_LATERAL}
                WHERE r.status = 'SUCCESS'
                  AND r.primary_category IS NOT NULL
                  AND r.created_at >= :since
                  {unit_filter}
                ORDER BY r.loop_id, r.created_at DESC
            ) t
            ORDER BY CASE t.severity WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 ELSE 1 END DESC,
                     t.created_at DESC
            LIMIT 50
            """
        ),
        {"since": since, "unit_ids": unit_ids},
    )
    return [dict(row._mapping) for row in result.all()]


async def _query_concl_rows(
    db: AsyncSession, since: datetime, unit_ids: list[str] | None, limit: int
) -> list[dict[str, Any]]:
    """近窗口诊断结论 run（diagnosis_run 主线 + 工厂模型归属 + 复核/处置关联计数）。"""
    unit_filter = "AND l.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(
            f"""
            SELECT r.id AS run_id, r.loop_id, r.primary_category,
                   r.primary_confidence AS confidence, r.severity,
                   r.review_status, r.rationale->>0 AS evidence_summary,
                   r.created_at AS ts,
                   l.tag_name AS loop_name,
                   un.name AS unit_name, fa.name AS factory_name,
                   act.converted_cnt, act.ignored_cnt
            FROM diagnosis_run r
            JOIN loop_ledger l ON l.id = r.loop_id
            LEFT JOIN plant_node un ON un.id = l.unit_id
            LEFT JOIN plant_node fa ON fa.id = un.parent_id
            {_ACTION_LATERAL}
            WHERE r.status = 'SUCCESS'
              AND r.created_at >= :since
              {unit_filter}
            ORDER BY r.created_at DESC
            LIMIT :limit
            """
        ),
        {"since": since, "unit_ids": unit_ids, "limit": limit},
    )
    return [dict(row._mapping) for row in result.all()]


async def _query_spark_map(db: AsyncSession, loop_ids: list[str]) -> dict[str, list[float]]:
    """每回路最近 N 小时评分序列（kpi_snapshot_hourly，旧 → 新；缺数据的回路无条目）。"""
    if not loop_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT loop_id, score FROM (
                SELECT loop_id, score,
                       row_number() OVER (PARTITION BY loop_id ORDER BY ts_start DESC) AS rn
                FROM kpi_snapshot_hourly
                WHERE loop_id = ANY(:loop_ids)
            ) s
            WHERE rn <= :points
            ORDER BY loop_id, rn DESC
            """
        ),
        {"loop_ids": loop_ids, "points": SPARK_POINTS},
    )
    spark_map: dict[str, list[float]] = {}
    for row in result.all():
        lid = str(row[0])
        val = _to_float(row[1])
        if val is None:
            continue
        spark_map.setdefault(lid, []).insert(0, val)
    return spark_map


async def _query_scope_loop_ids(db: AsyncSession, unit_ids: list[str] | None) -> list[str]:
    """scope 内活跃回路 id 列表（fitness 门禁聚合的分母）。"""
    unit_filter = "AND unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(f"SELECT id FROM loop_ledger WHERE is_active = true {unit_filter}"),
        {"unit_ids": unit_ids},
    )
    return [str(row[0]) for row in result.all()]


async def _query_rule_stat_rows(db: AsyncSession, since: datetime) -> list[dict[str, Any]]:
    """symptom_tags JSONB 展开聚合（近 30d 检出数 × 复核确认数，D2=a 口径）。"""
    result = await db.execute(
        text(
            """
            SELECT kv.key AS tag_code,
                   count(*) AS hits,
                   count(*) FILTER (WHERE r.review_status = 'REVIEWED') AS resolved
            FROM diagnosis_run r
            CROSS JOIN LATERAL jsonb_each(r.symptom_tags) kv
            WHERE r.status = 'SUCCESS'
              AND r.created_at >= :since
              AND kv.value->>'detected' = 'true'
            GROUP BY kv.key
            ORDER BY hits DESC
            """
        ),
        {"since": since},
    )
    return [dict(row._mapping) for row in result.all()]


async def _query_pareto_rows(
    db: AsyncSession, since: datetime, unit_ids: list[str] | None
) -> list[dict[str, Any]]:
    """diagnosis_run 按 primary_category 聚合（近窗口，与旧 MV-02 结构同构）。

    converted/ignored 为该类 run 中已转工单/已忽略的计数；SLA 计数已下线（恒 0）。
    """
    unit_filter = "AND l.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(
            f"""
            SELECT r.primary_category AS category_code,
                   count(*) AS tag_count,
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM loop_action_item a
                       WHERE a.run_id = r.id AND a.converted_order_id IS NOT NULL
                   )) AS converted_count,
                   count(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM loop_action_item a
                       WHERE a.run_id = r.id AND a.status = 'IGNORED'
                   )) AS ignored_count
            FROM diagnosis_run r
            JOIN loop_ledger l ON l.id = r.loop_id
            WHERE r.status = 'SUCCESS'
              AND r.primary_category IS NOT NULL
              AND r.created_at >= :since
              {unit_filter}
            GROUP BY r.primary_category
            ORDER BY tag_count DESC
            """
        ),
        {"since": since, "unit_ids": unit_ids},
    )
    return [dict(row._mapping) for row in result.all()]


async def _query_rootcause_rows(
    db: AsyncSession, since: datetime, top_n: int = 10
) -> list[dict[str, Any]]:
    """symptom_tags 标签聚合 TopN（近 30d，保"症状"语义与旧 tag_code 同域）。

    active_cnt = 该标签所在 run 无终态处置建议（未处置口径与 open_tags 一致）；
    severity_rank 按映射后四档域（HIGH→4 / MEDIUM→2 / LOW→1）。
    """
    result = await db.execute(
        text(
            """
            SELECT kv.key AS tag_code,
                   count(*) AS count,
                   count(*) FILTER (WHERE NOT EXISTS (
                       SELECT 1 FROM loop_action_item a
                       WHERE a.run_id = r.id
                         AND a.status IN ('CONVERTED', 'REJECTED', 'IGNORED')
                   )) AS active_count,
                   MAX(CASE r.severity WHEN 'HIGH' THEN 4 WHEN 'MEDIUM' THEN 2 ELSE 1 END)
                       AS severity_rank
            FROM diagnosis_run r
            CROSS JOIN LATERAL jsonb_each(r.symptom_tags) kv
            WHERE r.status = 'SUCCESS'
              AND r.created_at >= :since
              AND kv.value->>'detected' = 'true'
            GROUP BY kv.key
            ORDER BY count DESC
            LIMIT :top_n
            """
        ),
        {"since": since, "top_n": top_n},
    )
    return [dict(row._mapping) for row in result.all()]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _empty_fitness_gates() -> dict[str, Any]:
    return shape_fitness_gates({}, 0, 0)


async def build_diagnosis(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: int | None = None,
    window: str = "24h",
    only_active: bool = False,
) -> dict[str, Any]:
    """组装 A-03 诊断六块。部分失败容错：单块异常不阻断其余块。

    only_active=True → concl_timeline 仅保留未处置（UNADDRESSED）run。
    """
    sid = _scope_id_int(scope_type, scope_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    since = now - timedelta(hours=WINDOW_HOURS.get(window, 24))
    since_30d = now - timedelta(hours=WINDOW_HOURS[RULE_STATS_WINDOW])
    diagnosis: dict[str, Any] = {
        "scope": {"type": scope_type, "id": scope_id},
        "window": window,
        "summary_band": shape_summary_band(
            open_tags_len=0,
            concl_items=[],
        ),
        "open_tags": [],
        "concl_timeline": [],
        "fitness_gates": _empty_fitness_gates(),
        "rule_stats": [],
        "pareto": [],
        "rootcause_top": [],
    }

    # scope 子树 UNIT 过滤（GLOBAL → None 全量）
    try:
        unit_ids = await _get_scope_unit_ids(db, scope_type, sid)
    except Exception:  # noqa: BLE001
        logger.warning("诊断 scope 子树查询失败，回退全量", exc_info=True)
        unit_ids = None

    # --- open_tags：关键异常表 Top6（F-DG-01，每回路最新未处置异常 run）---
    try:
        rows = filter_open_tag_rows(await _query_open_tag_rows(db, since, unit_ids))
        loop_ids = [str(r["loop_id"]) for r in rows]
        spark_map = await _query_spark_map(db, loop_ids)
        fitness_latest = await get_latest_fitness_per_loop(db, loop_ids)
        fitness_map = {lid: fl.level for lid, fl in fitness_latest.items()}
        diagnosis["open_tags"] = shape_open_tags(rows, spark_map, fitness_map)
    except Exception:  # noqa: BLE001
        logger.warning("诊断 open_tags 块构建失败", exc_info=True)

    # --- concl_timeline：诊断结论时间线（F-DG-02，disposition 四态合成）---
    try:
        rows = await _query_concl_rows(db, since, unit_ids, CONCL_TIMELINE_LIMIT)
        diagnosis["concl_timeline"] = shape_concl_timeline(rows, only_active=only_active)
    except Exception:  # noqa: BLE001
        logger.warning("诊断 concl_timeline 块构建失败", exc_info=True)

    # --- fitness_gates：适用性 L0~L4 门禁（F-DG-03）---
    try:
        loop_ids_all = await _query_scope_loop_ids(db, unit_ids)
        latest = await get_latest_fitness_per_loop(db, loop_ids_all)
        level_counts: dict[str, int] = {}
        evaluated = 0
        for fl in latest.values():
            if fl.level:
                level_counts[fl.level] = level_counts.get(fl.level, 0) + 1
                evaluated += 1
        diagnosis["fitness_gates"] = shape_fitness_gates(level_counts, evaluated, len(loop_ids_all))
    except Exception:  # noqa: BLE001
        logger.warning("诊断 fitness_gates 块构建失败", exc_info=True)

    # --- rule_stats：诊断规则命中统计（F-DG-04 前置，D2=a 近 30d 固定窗口）---
    try:
        diagnosis["rule_stats"] = shape_rule_stats(await _query_rule_stat_rows(db, since_30d))
    except Exception:  # noqa: BLE001
        logger.warning("诊断 rule_stats 块构建失败", exc_info=True)

    # --- pareto：异常类型 Pareto（diagnosis_run primary_category 聚合，与 A-01 结构同构）---
    try:
        diagnosis["pareto"] = shape_pareto(await _query_pareto_rows(db, since, unit_ids))
    except Exception:  # noqa: BLE001
        logger.warning("诊断 pareto 块构建失败", exc_info=True)

    # --- rootcause_top：根因 TopN（symptom_tags 标签聚合，与 G-总览 roots 同构）---
    try:
        diagnosis["rootcause_top"] = shape_rootcause_top(await _query_rootcause_rows(db, since_30d))
    except Exception:  # noqa: BLE001
        logger.warning("诊断 rootcause_top 块构建失败", exc_info=True)

    # --- summary_band：Row1 摘要带（纯派生，零 DDL）---
    try:
        diagnosis["summary_band"] = shape_summary_band(
            open_tags_len=len(diagnosis["open_tags"]),
            concl_items=diagnosis["concl_timeline"],
            diag_count_delta=None,
            worsening_delta=None,
        )
    except Exception:  # noqa: BLE001
        logger.warning("诊断 summary_band 块构建失败", exc_info=True)

    return diagnosis
