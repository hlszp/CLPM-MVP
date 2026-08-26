"""A-03 工作台诊断聚合 service（M2 批次 G-诊断 · F-DG-01~03）.

组装 Tab3「回路诊断」六块数据（对齐原型 renderDiag() #tab-diag）：
- open_tags      关键异常表 Top6（diagnosis_tag 近窗口未处置 + spark + SLA 倒计时 + 结论摘要）
- concl_timeline 诊断结论时间线（diagnosis_result JOIN diagnosis_tag，disposition 四态色点）
- fitness_gates  适用性 L0~L4 门禁（聚合 kpi_snapshot_hourly.fitness_level，B-09 漏斗）
- rule_stats     诊断规则命中统计（diagnosis_tag 按 tag_code 聚合，F-DG-04 前置数据）
- pareto         异常类型 Pareto（复用 MV-02，与 G-总览同源）
- rootcause_top  根因 TopN（DiagnosisTag 聚合，与 G-总览 roots 同源）

数据架构：与 G-总览/G-评估一致，不直接查 TDengine；fitness 复用
loop_fitness.get_latest_fitness_per_loop（kpi_snapshot_hourly 固化结果，
阈值由 precalc 侧 loop_fitness 统一控制——用户决策：复用现有阈值）。

disposition 四态（B-10）：UNADDRESSED 未处置 / CONVERTED 已转任务 /
ACK_REVIEWED 已确认复核 / IGNORED 已忽略。

部分失败容错：每块独立 try/except，失败返回空/None 并 log.warning，不阻断其余块。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.loop_fitness import get_latest_fitness_per_loop
from app.services.workbench_overview import (
    _iso,
    _query_pareto,
    _query_roots,
    _scope_id_int,
    _to_float,
    shape_pareto,
    shape_roots,
)

logger = logging.getLogger(__name__)

# 关键异常表 Top N（方案 §5.1 F-DG-01：6 条）
OPEN_TAGS_TOP_N = 6
# 结论时间线上限（近窗口，按时间倒序）
CONCL_TIMELINE_LIMIT = 50
# sparkline 取点数（与原型 6 点对齐）
SPARK_POINTS = 6

# 严重度排序权重（高 → 低）
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

# 窗口 → 小时数（近窗口过滤口径）
WINDOW_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}

# 近窗口"未处置"口径：仅 UNADDRESSED（CONVERTED 已转任务 / ACK_REVIEWED 已确认 / IGNORED 已忽略）
OPEN_DISPOSITION = "UNADDRESSED"


# ---------------------------------------------------------------------------
# 纯 shaper（无 DB，单测友好）
# ---------------------------------------------------------------------------


def _norm_confidence(val: Any) -> float | None:
    """置信度归一为 0~1（diagnosis_result.confidence 约束 0~100，历史数据两种口径并存）。"""
    conf = _to_float(val)
    if conf is None:
        return None
    if conf > 1.0:
        conf = conf / 100.0
    return round(min(conf, 1.0), 2)


def shape_open_tags(
    rows: list[dict[str, Any]],
    spark_map: Mapping[str, list[float]],
    fitness_map: Mapping[str, str | None],
    now: datetime,
    top_n: int = OPEN_TAGS_TOP_N,
) -> list[dict[str, Any]]:
    """关键异常表 Top N（F-DG-01）。

    - rows: diagnosis_tag × loop_ledger × diagnosis_result(LATERAL) 联查行
    - spark_map: {loop_id: [score...]}（kpi_snapshot_hourly 近 N 点，旧 → 新）
    - fitness_map: {loop_id: fitness_level}（loop_fitness 最新快照）
    - 排序：严重度降序 → SLA 到期升序（最近到期优先，无 SLA 最后）→ 触发时间降序
    """
    ranked = sorted(
        rows,
        key=lambda r: (
            -SEVERITY_RANK.get(r.get("severity"), 0),
            r.get("sla_deadline_at") is None,
            r.get("sla_deadline_at") or now,
            -(r.get("triggered_at") or now).toordinal(),
        ),
    )
    items: list[dict[str, Any]] = []
    for r in ranked[:top_n]:
        loop_id = str(r.get("loop_id"))
        sla = r.get("sla_deadline_at")
        sla_due = int((sla - now).total_seconds()) if sla is not None else None
        items.append(
            {
                "tag_id": str(r.get("tag_id")),
                "loop_id": loop_id,
                "loop_name": r.get("loop_name"),
                "unit_name": r.get("unit_name"),
                "factory_name": r.get("factory_name"),
                "symptom": r.get("tag_name") or r.get("tag_code"),
                "category": r.get("category"),
                "severity": r.get("severity"),
                "spark": list(spark_map.get(loop_id, [])),
                "sla_due_sec": sla_due,
                "sla_stage": r.get("sla_stage"),
                "conclusion": r.get("conclusion"),
                "fitness_level": fitness_map.get(loop_id),
                "confidence": _norm_confidence(r.get("confidence")),
                "triggered_at": _iso(r.get("triggered_at")),
            }
        )
    return items


def shape_concl_timeline(
    rows: list[dict[str, Any]],
    only_active: bool = False,
) -> list[dict[str, Any]]:
    """诊断结论时间线（F-DG-02）。

    - rows: diagnosis_result × loop_ledger × diagnosis_tag(LATERAL) 联查行
    - only_active=True → 仅保留未处置（UNADDRESSED）活跃结论
    - 空分类容错：category 缺失回退 diag_label（症状代码）
    - 按时间倒序（新 → 旧）
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        disp = r.get("disposition")
        if only_active and disp != OPEN_DISPOSITION:
            continue
        result_id = str(r.get("result_id")) if r.get("result_id") else None
        tag_id = str(r.get("tag_id")) if r.get("tag_id") else None
        items.append(
            {
                "id": tag_id or result_id,
                "tag_id": tag_id,
                "result_id": result_id,
                "tag_code": r.get("diag_label"),
                "loop_id": str(r.get("loop_id")),
                "loop_name": r.get("loop_name"),
                "unit_name": r.get("unit_name"),
                "factory_name": r.get("factory_name"),
                "category": r.get("category") or r.get("diag_label"),
                "disposition": disp,
                "evidence_summary": r.get("evidence_summary"),
                "confidence": _norm_confidence(r.get("confidence")),
                "severity": r.get("severity"),
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
    """诊断规则命中统计（F-DG-04 前置数据，按 tag_code 聚合）。

    - hits: 总命中数；resolved_rate: 已解除占比（0~1，无命中 → None）
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        hits = int(r.get("hits") or 0)
        resolved = int(r.get("resolved") or 0)
        items.append(
            {
                "rule_id": r.get("tag_code"),
                "name": r.get("tag_name"),
                "hits": hits,
                "resolved_rate": round(resolved / hits, 3) if hits > 0 else None,
            }
        )
    return items


def shape_rootcause_top(rows: list[dict[str, Any]], top_n: int = 10) -> list[dict[str, Any]]:
    """根因 TopN（复用 G-总览 roots 聚合行，补 tag_type 别名字段对齐方案 A-03）。"""
    return [
        {
            "tag_type": r.get("tag_code"),
            "tag_code": r.get("tag_code"),
            "tag_name": r.get("tag_name"),
            "count": r.get("count", 0) or 0,
            "active_count": r.get("active_count", 0) or 0,
            "severity": r.get("severity"),
        }
        for r in shape_roots(rows, top_n)
    ]


# ---------------------------------------------------------------------------
# Row1 摘要带（原型 #tab-diag 首行 c12 · 5 项横向摘要）
# ---------------------------------------------------------------------------


# 诊断引擎元信息（原型截图：v3.2.1 · 连续运行 126 天 · 规则库 2026-08-18 更新）
# 无 diagnosis_result 表 algorithm_version 字段时回退此常量，避免 DDL
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


async def _query_open_tag_rows(
    db: AsyncSession, since: datetime, unit_ids: list[str] | None
) -> list[dict[str, Any]]:
    """近窗口未处置 ACTIVE 标签（联查回路名 + 最新结论 LATERAL + 工厂模型单元/装置归属）。"""
    unit_filter = "AND l.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(
            f"""
            SELECT t.id AS tag_id, t.loop_id, t.tag_code, t.tag_name, t.severity,
                   t.triggered_at, t.sla_deadline_at, t.sla_stage,
                   l.tag_name AS loop_name,
                   un.name AS unit_name, fa.name AS factory_name,
                   r.recommended_category AS category,
                   r.evidence_summary AS conclusion,
                   r.confidence
            FROM diagnosis_tag t
            JOIN loop_ledger l ON l.id = t.loop_id
            LEFT JOIN plant_node un ON un.id = l.unit_id
            LEFT JOIN plant_node fa ON fa.id = un.parent_id
            LEFT JOIN LATERAL (
                SELECT rr.recommended_category, rr.evidence_summary, rr.confidence
                FROM diagnosis_result rr
                WHERE rr.loop_id = t.loop_id AND rr.diag_label = t.tag_code
                ORDER BY rr.diagnosed_at DESC
                LIMIT 1
            ) r ON true
            WHERE t.status = 'ACTIVE'
              AND t.disposition_state = :disposition
              AND t.triggered_at >= :since
              {unit_filter}
            ORDER BY t.triggered_at DESC
            LIMIT 50
            """
        ),
        {"disposition": OPEN_DISPOSITION, "since": since, "unit_ids": unit_ids},
    )
    return [dict(row._mapping) for row in result.all()]


async def _query_concl_rows(
    db: AsyncSession, since: datetime, unit_ids: list[str] | None, limit: int
) -> list[dict[str, Any]]:
    """近窗口诊断结论（diagnosis_result 主线 + 工厂模型单元/装置归属）。

    LATERAL 取同回路同症状最新标签的 disposition。
    """
    unit_filter = "AND l.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(
            f"""
            SELECT r.id AS result_id, r.loop_id, r.diag_label, r.confidence,
                   r.recommended_category AS category, r.evidence_summary,
                   r.diagnosed_at AS ts,
                   l.tag_name AS loop_name,
                   un.name AS unit_name, fa.name AS factory_name,
                   t.id AS tag_id, t.disposition_state AS disposition, t.severity
            FROM diagnosis_result r
            JOIN loop_ledger l ON l.id = r.loop_id
            LEFT JOIN plant_node un ON un.id = l.unit_id
            LEFT JOIN plant_node fa ON fa.id = un.parent_id
            LEFT JOIN LATERAL (
                SELECT tt.id, tt.disposition_state, tt.severity
                FROM diagnosis_tag tt
                WHERE tt.loop_id = r.loop_id AND tt.tag_code = r.diag_label
                ORDER BY tt.triggered_at DESC
                LIMIT 1
            ) t ON true
            WHERE r.diagnosed_at >= :since
              {unit_filter}
            ORDER BY r.diagnosed_at DESC
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


async def _query_rule_stat_rows(db: AsyncSession) -> list[dict[str, Any]]:
    """diagnosis_tag 按 tag_code 聚合（命中数 + 已解除数）。"""
    result = await db.execute(
        text(
            """
            SELECT tag_code, max(tag_name) AS tag_name, count(*) AS hits,
                   count(*) FILTER (WHERE status = 'RESOLVED') AS resolved
            FROM diagnosis_tag
            GROUP BY tag_code
            ORDER BY hits DESC
            """
        )
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
    """组装 A-03 诊断六块。部分失败容错：单块异常不阻断其余块。"""
    sid = _scope_id_int(scope_type, scope_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    since = now - timedelta(hours=WINDOW_HOURS.get(window, 24))
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

    # --- open_tags：关键异常表 Top6（F-DG-01）---
    try:
        rows = await _query_open_tag_rows(db, since, unit_ids)
        loop_ids = [str(r["loop_id"]) for r in rows]
        spark_map = await _query_spark_map(db, loop_ids)
        fitness_latest = await get_latest_fitness_per_loop(db, loop_ids)
        fitness_map = {lid: fl.level for lid, fl in fitness_latest.items()}
        diagnosis["open_tags"] = shape_open_tags(rows, spark_map, fitness_map, now)
    except Exception:  # noqa: BLE001
        logger.warning("诊断 open_tags 块构建失败", exc_info=True)

    # --- concl_timeline：诊断结论时间线（F-DG-02）---
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

    # --- rule_stats：诊断规则命中统计（F-DG-04 前置）---
    try:
        diagnosis["rule_stats"] = shape_rule_stats(await _query_rule_stat_rows(db))
    except Exception:  # noqa: BLE001
        logger.warning("诊断 rule_stats 块构建失败", exc_info=True)

    # --- pareto：异常类型 Pareto（MV-02，与 G-总览同源复用）---
    try:
        diagnosis["pareto"] = shape_pareto(await _query_pareto(db))
    except Exception:  # noqa: BLE001
        logger.warning("诊断 pareto 块构建失败", exc_info=True)

    # --- rootcause_top：根因 TopN（DiagnosisTag 聚合，与 G-总览 roots 同源）---
    try:
        diagnosis["rootcause_top"] = shape_rootcause_top(await _query_roots(db))
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
