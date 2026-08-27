"""A-04/A-13 工作台整定聚合 service（M2 批次 G-整定 · F-TN-01~03）.

组装 Tab4「参数整定」四块数据（对齐原型 renderTune() #tab-tune）：
- batches       整定批次列表（F-TN-01，W11）：BLOCKED/READY/RUNNING 色点 + 前置依赖
                动态阻塞判定（B-06）：prereq_order_ids 任一 ∈ {PENDING/EXECUTING/VERIFYING}
                → effective status=BLOCKED + block_reason"前置工单 CL-xxxx 未闭合"；
                前置全部 CLOSED/CANCELLED 且库存储 BLOCKED → 自动解除为 READY
- pending_queue 待整定队列（F-TN-02，W12）：TuningRecord DRAFT/PENDING，
                BLOCKED 灰化（批次阻塞优先，其次同回路未闭合非 TUNING 工单）
- scatters      整定前后散点（F-TN-03，W13）：Δ=after-before，正绿负红，
                significance = Δ≥5（原型口径：验证提升 ≥5 分计为有效）
- fitness_gates 适用性 L0~L4 门禁（B-09，复用 G-诊断聚合；整定关注 L3 门禁
                ERR_TUNING_FITNESS_INSUFFICIENT）

散点数据源（B-12 固化优先）：
1. 批次 COMPLETED 时固化的 tuning_batch.scatters_before/after（[{loop_id, score, ...}]）
2. TUNING 类处置工单 kpi_before/kpi_after（->>'score'，VERIFYING/CLOSED 且前后值齐）

数据架构：不直接查 TDengine；评分取 kpi_snapshot_hourly 固化快照。
部分失败容错：每块独立 try/except，失败返回空/None 并 log.warning，不阻断其余块。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.diagnosis_v2_compat import category_label
from app.services.loop_fitness import get_latest_fitness_per_loop
from app.services.workbench_diagnosis import (
    _get_scope_unit_ids,
    _query_scope_loop_ids,
    shape_fitness_gates,
)
from app.services.workbench_overview import _iso, _scope_id_int, _to_float

logger = logging.getLogger(__name__)

# B-06：前置工单未闭合状态（任一 ∈ 此集合 → 批次 BLOCKED）
BLOCKING_ORDER_STATUSES = ("PENDING", "EXECUTING", "VERIFYING")
# 批次终态：不被阻塞重算覆盖
TERMINAL_BATCH_STATUSES = ("COMPLETED", "CANCELLED")

# 待整定队列状态（TuningRecord 11 态中的"待处理"两态）
PENDING_RECORD_STATUSES = ("DRAFT", "PENDING")

# 优先级阈值（对齐原型：58.4/61.2 高 · 66.5/71.3 中 · 74.8 低）
PRIORITY_HIGH_LT = 65.0
PRIORITY_MEDIUM_LT = 73.0

# 整定有效口径（原型 sum-band：验证提升 ≥5 分计为有效）
SIGNIFICANT_DELTA = 5.0

# 批次/队列/散点查询上限
BATCH_LIMIT = 20
QUEUE_LIMIT = 20
SCATTER_ORDER_LIMIT = 50

# 窗口 → 小时数（散点工单时间过滤口径）
WINDOW_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}


# ---------------------------------------------------------------------------
# 纯 shaper（无 DB，单测友好）
# ---------------------------------------------------------------------------


def resolve_batch_status(
    stored_status: str,
    prereq_orders: Sequence[Mapping[str, Any]],
) -> tuple[str, str | None]:
    """B-06 批次阻塞动态判定（纯函数）。

    - 终态（COMPLETED/CANCELLED）不重算，原样返回
    - 任一前置工单 ∈ {PENDING/EXECUTING/VERIFYING} → BLOCKED +
      block_reason"前置工单 {order_no} 未闭合"（多项时"CL-xxxx 等 N 项未闭合"）
    - 库存储 BLOCKED 且前置已全部闭合 → 自动解除为 READY
    - 其余状态（PENDING/READY/RUNNING）原样返回
    """
    if stored_status in TERMINAL_BATCH_STATUSES:
        return stored_status, None
    open_prereqs = [o for o in prereq_orders if o.get("status") in BLOCKING_ORDER_STATUSES]
    if open_prereqs:
        first_no = open_prereqs[0].get("order_no") or "未知工单"
        if len(open_prereqs) > 1:
            reason = f"前置工单 {first_no} 等 {len(open_prereqs)} 项未闭合"
        else:
            reason = f"前置工单 {first_no} 未闭合"
        return "BLOCKED", reason
    if stored_status == "BLOCKED":
        return "READY", None
    return stored_status, None


def shape_batches(
    rows: Sequence[Mapping[str, Any]],
    prereq_map: Mapping[str, Mapping[str, Any]],
    record_stats: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """整定批次卡片列表（F-TN-01）。

    - rows: tuning_batch 行（含 prereq_order_ids / scatters_before/after JSONB）
    - prereq_map: {order_id: {order_no, title, status}}（批量查询，避免 N+1）
    - record_stats: {batch_id: {loop_count, algorithms, owner}}
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        prereq_ids = [str(oid) for oid in (r.get("prereq_order_ids") or [])]
        prereq_orders: list[dict[str, Any]] = []
        for oid in prereq_ids:
            o = prereq_map.get(oid)
            if o is None:
                # 前置工单已被删除/不存在：按已闭合处理（不阻塞），展示占位
                prereq_orders.append(
                    {
                        "order_id": oid,
                        "order_no": oid[:8],
                        "title": None,
                        "status": None,
                        "closed": True,
                    }
                )
                continue
            prereq_orders.append(
                {
                    "order_id": oid,
                    "order_no": o.get("order_no"),
                    "title": o.get("title"),
                    "status": o.get("status"),
                    "closed": o.get("status") in ("CLOSED", "CANCELLED"),
                }
            )
        stored = r.get("status") or "PENDING"
        eff_status, eff_reason = resolve_batch_status(stored, prereq_orders)
        stats = record_stats.get(r.get("id")) or {}
        algorithms = stats.get("algorithms") or []
        score_before, score_after, score_delta = _batch_score_change(
            r.get("scatters_before"), r.get("scatters_after")
        )
        items.append(
            {
                "id": r.get("id"),
                "batch_no": r.get("batch_no"),
                "title": r.get("title"),
                "scope_type": r.get("scope_type"),
                "scope_id": r.get("scope_id"),
                "status": eff_status,
                "stored_status": stored,
                "block_reason": eff_reason or r.get("block_reason"),
                "prereq_orders": prereq_orders,
                "loop_count": int(stats.get("loop_count") or 0),
                "algorithms": list(algorithms),
                "strategy": " / ".join(algorithms) if algorithms else None,
                "score_before": score_before,
                "score_after": score_after,
                "score_delta": score_delta,
                "owner": stats.get("owner"),
                "expected_start_at": _iso(r.get("expected_start_at")),
                "actual_start_at": _iso(r.get("actual_start_at")),
                "completed_at": _iso(r.get("completed_at")),
                "created_at": _iso(r.get("created_at")),
            }
        )
    # 排序：BLOCKED 最前（需关注）→ RUNNING → READY → PENDING → COMPLETED → CANCELLED
    status_rank = {
        "BLOCKED": 0,
        "RUNNING": 1,
        "READY": 2,
        "PENDING": 3,
        "COMPLETED": 4,
        "CANCELLED": 5,
    }
    items.sort(key=lambda x: (status_rank.get(x["status"], 9), x.get("batch_no") or ""))
    return items


def _batch_score_change(before: Any, after: Any) -> tuple[float | None, float | None, float | None]:
    """批次评分变化：scatters_before/after 按 loop_id 配对后取均值。"""
    if not isinstance(before, list) or not isinstance(after, list):
        return None, None, None
    after_by_loop = {
        str(p.get("loop_id")): _to_float(p.get("score"))
        for p in after
        if isinstance(p, Mapping) and p.get("loop_id") is not None
    }
    pairs: list[tuple[float, float]] = []
    for p in before:
        if not isinstance(p, Mapping):
            continue
        lid = str(p.get("loop_id"))
        b = _to_float(p.get("score"))
        a = after_by_loop.get(lid)
        if b is not None and a is not None:
            pairs.append((b, a))
    if not pairs:
        return None, None, None
    avg_b = round(sum(b for b, _ in pairs) / len(pairs), 1)
    avg_a = round(sum(a for _, a in pairs) / len(pairs), 1)
    return avg_b, avg_a, round(avg_a - avg_b, 1)


def _priority_of(score: float | None) -> str:
    """待整定优先级（原型口径：评分越低优先级越高）。"""
    if score is None:
        return "MEDIUM"
    if score < PRIORITY_HIGH_LT:
        return "HIGH"
    if score < PRIORITY_MEDIUM_LT:
        return "MEDIUM"
    return "LOW"


def shape_pending_queue(
    rows: Sequence[Mapping[str, Any]],
    block_map: Mapping[str, Mapping[str, Any]],
    batch_map: Mapping[str, Mapping[str, Any]],
    score_map: Mapping[str, float],
    diag_src_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    """待整定队列（F-TN-02，W12）。

    - block_map: {loop_id: {order_no, status}}（同回路未闭合非 TUNING 工单）
    - batch_map: {record_id: {batch_no, status, block_reason}}（所属批次，已解析阻塞）
    - score_map: {loop_id: 最新评分}（kpi_snapshot_hourly 快照）
    - diag_src_map: {loop_id: 类别中文}（diagnosis_run 最新 primary_category → 建议来源"诊断：xxx"）
    阻塞优先级：批次 BLOCKED > 同回路前置工单未闭合。
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        record_id = str(r.get("record_id"))
        loop_id = str(r.get("loop_id"))
        blocked = False
        block_reason: str | None = None
        batch_no: str | None = None
        b = batch_map.get(record_id)
        if b is not None:
            batch_no = b.get("batch_no")
            if b.get("status") == "BLOCKED":
                blocked = True
                block_reason = b.get("block_reason") or "所属批次前置工单未闭合"
        if not blocked:
            o = block_map.get(loop_id)
            if o is not None:
                blocked = True
                block_reason = f"前置工单 {o.get('order_no')} 未闭合"
        score = score_map.get(loop_id)
        diag_src = diag_src_map.get(loop_id)
        created_by = r.get("created_by")
        if diag_src:
            source = f"诊断：{diag_src}"
        elif created_by:
            source = f"人工登记 · {created_by}"
        else:
            source = "人工登记"
        items.append(
            {
                "record_id": record_id,
                "loop_id": loop_id,
                "loop_name": r.get("loop_name"),
                "loop_desc": r.get("loop_desc"),
                "unit_name": r.get("unit_name"),
                "source": source,
                "score": score,
                "algorithm": r.get("algorithm"),
                "fitting_score": _to_float(r.get("fitting_score")),
                "priority": _priority_of(score),
                "blocked": blocked,
                "block_reason": block_reason,
                "batch_no": batch_no,
                "created_at": _iso(r.get("created_at")),
            }
        )
    # 排序：可操作在前（按评分升序，最差的优先整定），阻塞灰化沉底
    items.sort(
        key=lambda x: (
            x["blocked"],
            x["score"] is None,
            x["score"] if x["score"] is not None else 0.0,
        )
    )
    return items


def shape_scatter_points(
    pairs: Sequence[tuple[float, float]],
    meta: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """散点 Δ（F-TN-03，B-12）：Δ=after-before，significance = Δ≥5。

    - pairs/meta 等长：pairs[i] = (before, after)，meta[i] 携带
      loop_id / loop_name / batch_no / order_no
    """
    points: list[dict[str, Any]] = []
    for (before, after), m in zip(pairs, meta, strict=True):
        delta = round(after - before, 1)
        points.append(
            {
                "loop_id": str(m.get("loop_id")),
                "loop_name": m.get("loop_name"),
                "before": round(before, 1),
                "after": round(after, 1),
                "delta": delta,
                "significance": delta >= SIGNIFICANT_DELTA,
                "batch_no": m.get("batch_no"),
                "order_no": m.get("order_no"),
            }
        )
    # 排序：Δ 降序（改善最大居上，回退沉底）
    points.sort(key=lambda p: -p["delta"])
    return points


# ---------------------------------------------------------------------------
# async 查询 helper
# ---------------------------------------------------------------------------


async def _get_scope_source_ids(
    db: AsyncSession, scope_type: str, scope_id: int
) -> list[int] | None:
    """scope 子树 source_node_id 集合（含自身；GLOBAL/LOOP → None 不过滤）。

    用于 tuning_batch.scope_id（整数口径，与 plant_node.source_node_id 对齐）过滤。
    """
    if scope_type in ("GLOBAL", "LOOP"):
        return None
    result = await db.execute(
        text(
            """
            WITH RECURSIVE node_tree AS (
                SELECT id, source_node_id FROM plant_node WHERE source_node_id = :sid
                UNION ALL
                SELECT c.id, c.source_node_id
                FROM plant_node c JOIN node_tree t ON c.parent_id = t.id
            )
            SELECT DISTINCT source_node_id FROM node_tree WHERE source_node_id IS NOT NULL
            """
        ),
        {"sid": scope_id},
    )
    return [int(row[0]) for row in result.all()]


async def _query_batch_rows(
    db: AsyncSession, scope_type: str, scope_id: int
) -> list[dict[str, Any]]:
    """批次行（scope 子树过滤；GLOBAL 全量）。"""
    source_ids = await _get_scope_source_ids(db, scope_type, scope_id)
    scope_filter = ""
    params: dict[str, Any] = {"limit": BATCH_LIMIT}
    if source_ids is not None:
        scope_filter = "WHERE b.scope_id = ANY(:source_ids)"
        params["source_ids"] = source_ids
    result = await db.execute(
        text(
            f"""
            SELECT b.id, b.batch_no, b.title, b.scope_type, b.scope_id, b.status,
                   b.prereq_order_ids, b.block_reason,
                   b.scatters_before, b.scatters_after,
                   b.expected_start_at, b.actual_start_at, b.completed_at, b.created_at
            FROM tuning_batch b
            {scope_filter}
            ORDER BY b.created_at DESC
            LIMIT :limit
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in result.all()]


async def _query_prereq_orders(
    db: AsyncSession, order_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """前置工单批量查询（B-06 调度端批量而非 N+1）。{order_id: row}"""
    if not order_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT id::text AS order_id, order_no, title, status
            FROM handling_order
            WHERE id::text = ANY(:ids)
            """
        ),
        {"ids": [str(oid) for oid in order_ids]},
    )
    return {str(r.order_id): dict(r._mapping) for r in result.all()}


async def _query_batch_record_stats(
    db: AsyncSession, batch_ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """批次内记录聚合：回路数 + 算法集合 + 执行人（首个非空 created_by）。"""
    if not batch_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT tbr.batch_id,
                   count(*) AS loop_count,
                   array_agg(DISTINCT tr.algorithm) FILTER
                       (WHERE tr.algorithm IS NOT NULL) AS algorithms,
                   (array_agg(tr.created_by ORDER BY tbr.sort_order) FILTER
                       (WHERE tr.created_by IS NOT NULL))[1] AS owner
            FROM tuning_batch_records tbr
            JOIN tuning_record tr ON tr.id = tbr.tuning_record_id
            WHERE tbr.batch_id = ANY(:batch_ids)
            GROUP BY tbr.batch_id
            """
        ),
        {"batch_ids": [int(b) for b in batch_ids]},
    )
    out: dict[int, dict[str, Any]] = {}
    for r in result.all():
        m = dict(r._mapping)
        out[int(m["batch_id"])] = {
            "loop_count": int(m.get("loop_count") or 0),
            "algorithms": list(m.get("algorithms") or []),
            "owner": m.get("owner"),
        }
    return out


async def _query_pending_records(
    db: AsyncSession, unit_ids: list[str] | None
) -> list[dict[str, Any]]:
    """待整定记录（TuningRecord DRAFT/PENDING + 回路名/单元名）。"""
    unit_filter = "AND l.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(
            f"""
            SELECT tr.id AS record_id, tr.loop_id, tr.algorithm, tr.status,
                   tr.created_by, tr.created_at, tr.fitting_score,
                   l.tag_name AS loop_name, l.description AS loop_desc,
                   pu.name AS unit_name
            FROM tuning_record tr
            JOIN loop_ledger l ON l.id = tr.loop_id
            LEFT JOIN plant_node pu ON pu.id = l.unit_id
            WHERE tr.status IN ('DRAFT', 'PENDING')
              {unit_filter}
            ORDER BY tr.created_at DESC
            LIMIT :limit
            """
        ),
        {"unit_ids": unit_ids, "limit": QUEUE_LIMIT},
    )
    return [dict(row._mapping) for row in result.all()]


async def _query_loop_open_orders(
    db: AsyncSession, loop_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """同回路未闭合非 TUNING 工单（先硬件后整定阻塞语义；每回路取最早一条）。"""
    if not loop_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT DISTINCT ON (loop_id) loop_id::text AS loop_id, order_no, status
            FROM handling_order
            WHERE loop_id::text = ANY(:loop_ids)
              AND status IN ('PENDING', 'EXECUTING', 'VERIFYING')
              AND action_type != 'TUNING'
            ORDER BY loop_id, created_at ASC
            """
        ),
        {"loop_ids": [str(lid) for lid in loop_ids]},
    )
    return {str(r.loop_id): dict(r._mapping) for r in result.all()}


async def _query_record_batch_map(
    db: AsyncSession, record_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """记录 → 所属批次映射（待整定队列批次阻塞判定的输入）。"""
    if not record_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT tbr.tuning_record_id AS record_id,
                   b.id AS batch_id, b.batch_no, b.status, b.prereq_order_ids
            FROM tuning_batch_records tbr
            JOIN tuning_batch b ON b.id = tbr.batch_id
            WHERE tbr.tuning_record_id = ANY(:record_ids)
            """
        ),
        {"record_ids": [str(rid) for rid in record_ids]},
    )
    return {str(r.record_id): dict(r._mapping) for r in result.all()}


async def _query_latest_scores(db: AsyncSession, loop_ids: Sequence[str]) -> dict[str, float]:
    """每回路最新评分（kpi_snapshot_hourly 快照，DISTINCT ON）。"""
    if not loop_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT DISTINCT ON (loop_id) loop_id::text AS loop_id, score
            FROM kpi_snapshot_hourly
            WHERE loop_id::text = ANY(:loop_ids)
            ORDER BY loop_id, ts_start DESC
            """
        ),
        {"loop_ids": [str(lid) for lid in loop_ids]},
    )
    out: dict[str, float] = {}
    for r in result.all():
        v = _to_float(r.score)
        if v is not None:
            out[str(r.loop_id)] = v
    return out


async def _query_diag_src_map(db: AsyncSession, loop_ids: Sequence[str]) -> dict[str, str]:
    """每回路最新一条 primary_category 非 NULL 诊断 run 的中文类别（建议来源"诊断：xxx"）。

    口径（14 号方案阶段 A3）：改读 diagnosis_run（v2 引擎）；此前为 ACTIVE
    diagnosis_tag 的标签名。类别中文映射复用 diagnosis_v2_compat.category_label。
    """
    if not loop_ids:
        return {}
    result = await db.execute(
        text(
            """
            SELECT DISTINCT ON (loop_id) loop_id::text AS loop_id, primary_category
            FROM diagnosis_run
            WHERE loop_id::text = ANY(:loop_ids)
              AND primary_category IS NOT NULL
            ORDER BY loop_id, created_at DESC
            """
        ),
        {"loop_ids": [str(lid) for lid in loop_ids]},
    )
    return {
        str(r.loop_id): str(category_label(r.primary_category) or r.primary_category)
        for r in result.all()
    }


async def _query_loop_names(db: AsyncSession, loop_ids: Sequence[str]) -> dict[str, str]:
    """loop_id → 位号（散点批次快照补名称）。"""
    if not loop_ids:
        return {}
    result = await db.execute(
        text("SELECT id::text AS loop_id, tag_name FROM loop_ledger WHERE id::text = ANY(:ids)"),
        {"ids": [str(lid) for lid in loop_ids]},
    )
    return {str(r.loop_id): str(r.tag_name) for r in result.all() if r.tag_name}


async def _query_scatter_orders(
    db: AsyncSession, since: datetime, unit_ids: list[str] | None
) -> list[dict[str, Any]]:
    """TUNING 类工单前后 KPI（散点来源 2；VERIFYING/CLOSED 且前后 score 齐）。"""
    unit_filter = "AND l.unit_id = ANY(:unit_ids)" if unit_ids is not None else ""
    result = await db.execute(
        text(
            f"""
            SELECT o.loop_id::text AS loop_id, o.order_no,
                   o.kpi_before, o.kpi_after, l.tag_name AS loop_name
            FROM handling_order o
            JOIN loop_ledger l ON l.id = o.loop_id
            WHERE o.action_type = 'TUNING'
              AND o.status IN ('VERIFYING', 'CLOSED')
              AND o.kpi_before ->> 'score' IS NOT NULL
              AND o.kpi_after ->> 'score' IS NOT NULL
              AND COALESCE(o.verified_at, o.updated_at) >= :since
              {unit_filter}
            ORDER BY COALESCE(o.verified_at, o.updated_at) DESC
            LIMIT :limit
            """
        ),
        {"since": since, "unit_ids": unit_ids, "limit": SCATTER_ORDER_LIMIT},
    )
    return [dict(row._mapping) for row in result.all()]


# ---------------------------------------------------------------------------
# 散点组装（批次固化快照 + TUNING 工单，供 A-04 内嵌与 A-13 独立调用）
# ---------------------------------------------------------------------------


async def build_tuning_scatters(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: int | None = None,
    window: str = "30d",
    batch_id: int | None = None,
    _batch_rows: list[dict[str, Any]] | None = None,
    _unit_ids: list[str] | None | bool = ...,  # 内部复用哨兵：...=未查询
) -> dict[str, Any]:
    """组装 A-13 散点。batch_id 指定时仅返回该批次固化快照点。"""
    sid = _scope_id_int(scope_type, scope_id)
    points: list[dict[str, Any]] = []

    # --- 来源 1：批次固化快照（B-12）---
    try:
        if batch_id is not None:
            result = await db.execute(
                text(
                    """
                    SELECT id, batch_no, scatters_before, scatters_after
                    FROM tuning_batch WHERE id = :bid
                    """
                ),
                {"bid": batch_id},
            )
            snap_rows = [dict(r._mapping) for r in result.all()]
        else:
            rows = (
                _batch_rows
                if _batch_rows is not None
                else await _query_batch_rows(db, scope_type, sid)
            )
            snap_rows = [
                r
                for r in rows
                if r.get("scatters_before") is not None and r.get("scatters_after") is not None
            ]
        loop_ids: list[str] = []
        for r in snap_rows:
            for p in r.get("scatters_before") or []:
                if isinstance(p, Mapping) and p.get("loop_id") is not None:
                    loop_ids.append(str(p["loop_id"]))
        name_map = await _query_loop_names(db, sorted(set(loop_ids)))
        for r in snap_rows:
            after_by_loop = {
                str(p.get("loop_id")): _to_float(p.get("score"))
                for p in (r.get("scatters_after") or [])
                if isinstance(p, Mapping)
            }
            pairs: list[tuple[float, float]] = []
            metas: list[dict[str, Any]] = []
            for p in r.get("scatters_before") or []:
                if not isinstance(p, Mapping):
                    continue
                lid = str(p.get("loop_id"))
                b = _to_float(p.get("score"))
                a = after_by_loop.get(lid)
                if b is None or a is None:
                    continue
                pairs.append((b, a))
                metas.append(
                    {
                        "loop_id": lid,
                        "loop_name": name_map.get(lid),
                        "batch_no": r.get("batch_no"),
                        "order_no": None,
                    }
                )
            points.extend(shape_scatter_points(pairs, metas))
    except Exception:  # noqa: BLE001
        logger.warning("整定散点批次快照块构建失败", exc_info=True)

    # --- 来源 2：TUNING 工单 kpi_before/after（batch_id 过滤时不查）---
    if batch_id is None:
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            since = now - timedelta(hours=WINDOW_HOURS.get(window, 24 * 30))
            if _unit_ids is ...:
                unit_ids = await _get_scope_unit_ids(db, scope_type, sid)
            else:
                unit_ids = _unit_ids  # type: ignore[assignment]
            order_rows = await _query_scatter_orders(db, since, unit_ids)
            pairs2: list[tuple[float, float]] = []
            metas2: list[dict[str, Any]] = []
            batch_loop_ids = {p["loop_id"] for p in points}
            for r in order_rows:
                lid = str(r.get("loop_id"))
                if lid in batch_loop_ids:
                    continue  # 批次快照已覆盖的回路不重复出点
                b = _to_float((r.get("kpi_before") or {}).get("score"))
                a = _to_float((r.get("kpi_after") or {}).get("score"))
                if b is None or a is None:
                    continue
                pairs2.append((b, a))
                metas2.append(
                    {
                        "loop_id": lid,
                        "loop_name": r.get("loop_name"),
                        "batch_no": None,
                        "order_no": r.get("order_no"),
                    }
                )
            points.extend(shape_scatter_points(pairs2, metas2))
        except Exception:  # noqa: BLE001
            logger.warning("整定散点工单块构建失败", exc_info=True)

    points.sort(key=lambda p: -p["delta"])
    return {
        "points": points,
        "scope": {"type": scope_type, "id": scope_id},
        "window": window,
        "batch_id": batch_id,
    }


# ---------------------------------------------------------------------------
# 主入口（A-04）
# ---------------------------------------------------------------------------


async def build_tuning(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: int | None = None,
    window: str = "24h",
) -> dict[str, Any]:
    """组装 A-04 整定四块。部分失败容错：单块异常不阻断其余块。"""
    sid = _scope_id_int(scope_type, scope_id)
    tuning: dict[str, Any] = {
        "scope": {"type": scope_type, "id": scope_id},
        "window": window,
        "batches": [],
        "pending_queue": [],
        "scatters": [],
        "fitness_gates": shape_fitness_gates({}, 0, 0),
    }

    # scope 子树 UNIT 过滤（GLOBAL → None 全量；供队列/散点/门禁复用）
    try:
        unit_ids = await _get_scope_unit_ids(db, scope_type, sid)
    except Exception:  # noqa: BLE001
        logger.warning("整定 scope 子树查询失败，回退全量", exc_info=True)
        unit_ids = None

    # --- batches：批次列表 + 前置依赖解析（F-TN-01，B-06）---
    batch_rows: list[dict[str, Any]] = []
    try:
        batch_rows = await _query_batch_rows(db, scope_type, sid)
        prereq_ids: list[str] = []
        for r in batch_rows:
            prereq_ids.extend(str(oid) for oid in (r.get("prereq_order_ids") or []))
        prereq_map = await _query_prereq_orders(db, sorted(set(prereq_ids)))
        record_stats = await _query_batch_record_stats(db, [int(r["id"]) for r in batch_rows])
        tuning["batches"] = shape_batches(batch_rows, prereq_map, record_stats)
    except Exception:  # noqa: BLE001
        logger.warning("整定 batches 块构建失败", exc_info=True)

    # --- pending_queue：待整定队列（F-TN-02，阻塞灰化）---
    try:
        pending_rows = await _query_pending_records(db, unit_ids)
        record_ids = [str(r["record_id"]) for r in pending_rows]
        loop_ids = [str(r["loop_id"]) for r in pending_rows]
        # 记录所属批次（含未进入 batches 列表的跨 scope 批次）统一做阻塞解析
        raw_batch_map = await _query_record_batch_map(db, record_ids)
        extra_prereq_ids: list[str] = []
        for b in raw_batch_map.values():
            extra_prereq_ids.extend(str(oid) for oid in (b.get("prereq_order_ids") or []))
        extra_prereq_map = await _query_prereq_orders(db, sorted(set(extra_prereq_ids)))
        batch_map: dict[str, dict[str, Any]] = {}
        for rid, b in raw_batch_map.items():
            prereqs = [
                extra_prereq_map.get(str(oid), {"status": "CLOSED"})
                for oid in (b.get("prereq_order_ids") or [])
            ]
            eff_status, eff_reason = resolve_batch_status(b.get("status") or "PENDING", prereqs)
            batch_map[rid] = {
                "batch_no": b.get("batch_no"),
                "status": eff_status,
                "block_reason": eff_reason or b.get("block_reason"),
            }
        block_map = await _query_loop_open_orders(db, loop_ids)
        score_map = await _query_latest_scores(db, loop_ids)
        diag_src_map = await _query_diag_src_map(db, loop_ids)
        tuning["pending_queue"] = shape_pending_queue(
            pending_rows, block_map, batch_map, score_map, diag_src_map
        )
    except Exception:  # noqa: BLE001
        logger.warning("整定 pending_queue 块构建失败", exc_info=True)

    # --- scatters：整定前后散点（F-TN-03，复用 A-13 组装，30d 口径）---
    try:
        scatter_data = await build_tuning_scatters(
            db,
            scope_type=scope_type,
            scope_id=scope_id,
            window="30d",
            _batch_rows=batch_rows,
            _unit_ids=unit_ids,
        )
        tuning["scatters"] = scatter_data["points"]
    except Exception:  # noqa: BLE001
        logger.warning("整定 scatters 块构建失败", exc_info=True)

    # --- fitness_gates：适用性 L0~L4 门禁（B-09，整定关注 L3）---
    try:
        loop_ids_all = await _query_scope_loop_ids(db, unit_ids)
        latest = await get_latest_fitness_per_loop(db, loop_ids_all)
        level_counts: dict[str, int] = {}
        evaluated = 0
        for fl in latest.values():
            if fl.level:
                level_counts[fl.level] = level_counts.get(fl.level, 0) + 1
                evaluated += 1
        tuning["fitness_gates"] = shape_fitness_gates(level_counts, evaluated, len(loop_ids_all))
    except Exception:  # noqa: BLE001
        logger.warning("整定 fitness_gates 块构建失败", exc_info=True)

    return tuning
