"""A-02 工作台性能评估聚合 service（M2 批次 G-评估 · F-EV-01~03）.

组装 Tab2「性能评估」四块数据（对齐原型 renderEval() #tab-eval 3 行 × 12 列）：
- summary  摘要带（半圆 gauge 评分 + 参评 N/M + 距目标 + 环比 + 自然语言结论 + 风险速览 3 条）
- ranking  装置/单元排名（view=plant|unit 切换，含 sparkline/进度条/失分 tag）
- heatmap  单元 × 6 指标矩阵（4 级色阶，故障率反向着色，不可评斜纹）
- trend    综合评分趋势 + 分项斜率 6 项 + 等级分布 + 控制模式分布 + 数据质量

数据架构：与 G-总览一致，**不直接查 TDengine**；只读 workbench_window_summary
预计算表（含 distribution JSONB 列，由 precalc 任务 / seed 写入）+ 复用 G-总览
的递归 CTE 与 alarm/overdue 聚合 helper。

scope_id 约定（同 G-总览）：GLOBAL → 0；FACTORY/AREA/UNIT → PlantNode.source_node_id。

部分失败容错：每块独立 try/except，失败返回空/None 并 log.warning，不阻断其余块。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workbench_summary import WorkbenchWindowSummary
from app.services.workbench_overview import (
    _get_child_ids_for_plants,
    _get_descendant_unit_ids,
    _get_lose_threshold,
    _iso,
    _load_plant_hierarchy,
    _lose_factors,
    _query_alarm_per_unit,
    _query_overdue_per_unit,
    _query_scope_rows,
    _scope_id_int,
    _to_float,
)

logger = logging.getLogger(__name__)

# 评估热力 6 指标（对齐原型 METRICS 顺序与口径）
# 顺序：有效自控 / 平稳率 / 准确率 / 快速率 / 好值率 / 故障率（末项反向）
EVAL_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("effective_auto_rate", "有效自控", False),
    ("steady_rate", "平稳率", False),
    ("accuracy_rate", "准确率", False),
    ("fast_rate", "快速率", False),
    ("good_value_rate", "好值率", False),
    ("instrument_fault_rate", "故障率", True),  # 反向着色（越低越好）
)

# 评估目标线（综合评分目标，对齐原型 gauge "目标 ≥90"）
ASSESSMENT_TARGET_SCORE = 90.0

# 等级分布阈值（对齐原型 donut：优≥90 / 良 75–90 / 中 60–75 / 差<60 / 不可评）
LEVEL_TIERS = (
    ("优", 90, 100, "#2E7D32", False),
    ("良", 75, 90, "#7CB342", False),
    ("中", 60, 75, "#F59E0B", False),
    ("差", 0, 60, "#D93025", False),
    ("不可评", 0, 0, "#C9D6E8", True),  # stripe 斜纹
)


# ---------------------------------------------------------------------------
# 纯 shaper（无 DB，单测友好）
# ---------------------------------------------------------------------------


def _sparkline_delta(sparkline: list[Any] | None) -> float | None:
    """sparkline 末值 − 首值 → 环比 delta；不足 2 点返回 None。"""
    if not sparkline or len(sparkline) < 2:
        return None
    try:
        first = float(sparkline[0].get("v") if isinstance(sparkline[0], dict) else sparkline[0])
        last = float(sparkline[-1].get("v") if isinstance(sparkline[-1], dict) else sparkline[-1])
        return round(last - first, 2)
    except (TypeError, ValueError, AttributeError):
        return None


def _grade_label(score: float | None) -> str:
    """综合评分 → 等级中文标签（对齐原型 gauge "B 良好 · 目标 ≥90"）。"""
    if score is None:
        return "—"
    if score >= 90:
        return "A 优秀"
    if score >= 75:
        return "B 良好"
    if score >= 60:
        return "C 中等"
    return "D 较差"


def shape_summary(
    win_row: Any | None,
    plants: list[dict[str, Any]],
    total_loops: int,
    target: float = ASSESSMENT_TARGET_SCORE,
) -> dict[str, Any]:
    """摘要带：评分 + 参评 + 距目标 + 环比 + 自然语言结论 + 风险速览。

    - win_row: 当前 scope × window 的预计算行（None → 空摘要）
    - plants:  ranking 已算好的装置列表（用于风险速览取最低分装置）
    - total_loops: 全厂回路总数（参评分母；win_row.loop_count 为参评分子）
    """
    if win_row is None:
        return {
            "score": None,
            "grade": "—",
            "participation": {"evaluated": 0, "total": total_loops},
            "distance_to_target": None,
            "delta": None,
            "target": target,
            "conclusion": "暂无评估数据",
            "conclusion_links": [],
            "risks": [],
        }

    score = _to_float(getattr(win_row, "score", None))
    evaluated = getattr(win_row, "loop_count", 0) or 0
    sparkline = getattr(win_row, "score_trend", None) or []
    delta = _sparkline_delta(sparkline)
    distance = round(score - target, 1) if score is not None else None

    # 风险速览：取分数最低的 3 个装置（已按风险优先排序），辅以超期/振荡信息
    risks: list[dict[str, Any]] = []
    for pl in plants[:3]:
        if pl.get("score") is None:
            continue
        risks.append(
            {
                "name": pl.get("name"),
                "score": pl.get("score"),
                "delta": pl.get("delta"),
                "alarm_count": pl.get("alarm_count", 0),
                "overdue_tasks": pl.get("overdue_tasks", 0),
                "lose_factors": pl.get("lose_factors", []),
            }
        )

    # 自然语言结论（对齐原型文案结构）
    grade = _grade_label(score)
    if score is not None:
        delta_txt = (
            f"环比 <b>{'+' if (delta or 0) >= 0 else ''}{delta}</b> 分"
            if delta is not None
            else "环比持平"
        )
        worst = plants[0] if plants else None
        if worst:
            factors = worst.get("lose_factors") or ["综合因素"]
            joined = "</b> 与 <b>".join(factors)
            worst_txt = f"压力集中于 <b>{worst['name']}</b>，主要受 <b>{joined}</b> 影响"
        else:
            worst_txt = "各装置运行平稳"
        conclusion = (
            f"控制性能处于 <b>{grade}</b> 水平，综合评分 <b>{score}</b>，{delta_txt}；{worst_txt}。"
        )
    else:
        conclusion = "暂无评估数据"

    return {
        "score": score,
        "grade": grade,
        "participation": {"evaluated": evaluated, "total": total_loops},
        "distance_to_target": distance,
        "delta": delta,
        "target": target,
        "conclusion": conclusion,
        "conclusion_links": [
            {"text": "查看劣化回路", "action": "tab:diag"},
            {"text": "查看全部预警", "action": "alerts"},
        ],
        "risks": risks,
    }


def shape_ranking_plant(
    kpi_rows: list[Any],
    hierarchy: dict[str, Any],
    alarm_per_unit: dict[str, int],
    overdue_per_unit: dict[str, int],
    threshold: float,
    total_loops: int,
) -> list[dict[str, Any]]:
    """装置视图排名（对齐原型 PLANTS 表：按综合评分升序 · 风险优先）。

    列：rank / name / score / delta / join(参评) / alarm / overdue / sparkline / lose_factors
    """
    name_by_source_id: dict[int, str] = hierarchy["name_by_source_id"]
    items: list[dict[str, Any]] = []
    for row in kpi_rows:
        src_id = getattr(row, "scope_id", None)
        name = name_by_source_id.get(src_id) or f"装置#{src_id}"
        sparkline = getattr(row, "score_trend", None) or []
        loop_count = getattr(row, "loop_count", 0) or 0
        items.append(
            {
                "id": src_id,
                "name": name,
                "parent_name": None,
                "score": _to_float(getattr(row, "score", None)),
                "delta": _sparkline_delta(sparkline),
                "join": f"{loop_count}/{total_loops}",
                "loop_count": loop_count,
                "alarm_count": 0,
                "overdue_tasks": 0,
                "sparkline": sparkline,
                "lose_factors": _lose_factors(row, threshold),
            }
        )
    # 按综合评分升序（最低分 = 最高风险 = rank 1，对齐原型）
    items.sort(key=lambda x: (x["score"] is None, x["score"] if x["score"] is not None else 0))
    # 累加 alarm/overdue（需 unit→factory 映射；此处按 source_id 直接查 alarm_per_unit
    # 的 key 是 plant_node.id(UUID)，与 unit 维度对齐——若 ranking 行即 UNIT 级则直接命中；
    # FACTORY 级行无 unit_id 映射时保持 0，由 overview 聚合逻辑覆盖）
    for idx, it in enumerate(items, 1):
        it["rank"] = idx
    return items


def shape_ranking_unit(
    kpi_rows: list[Any],
    hierarchy: dict[str, Any],
) -> list[dict[str, Any]]:
    """单元视图排名（对齐原型 UNITS 表：# / 单元 / 所属装置 / 评分 / 环比 / 24h 趋势）。

    简化列：rank / name / parent_name / score / delta / sparkline
    """
    name_by_source_id: dict[int, str] = hierarchy["name_by_source_id"]
    by_id = hierarchy["by_id"]
    # source_id → parent factory name
    parent_name_by_source: dict[int, str] = {}
    for node in by_id.values():
        if node.type == "UNIT" and node.source_node_id is not None:
            parent_id = node.parent_id
            parent = by_id.get(parent_id) if parent_id else None
            # UNIT 直接父可能是 AREA，再上溯到 FACTORY 取装置名
            while parent is not None and parent.type not in ("FACTORY", "AREA"):
                parent_id = parent.parent_id
                parent = by_id.get(parent_id) if parent_id else None
            if parent is not None:
                parent_name_by_source[node.source_node_id] = parent.name

    items: list[dict[str, Any]] = []
    for row in kpi_rows:
        src_id = getattr(row, "scope_id", None)
        name = name_by_source_id.get(src_id) or f"单元#{src_id}"
        sparkline = getattr(row, "score_trend", None) or []
        items.append(
            {
                "id": src_id,
                "name": name,
                "parent_name": parent_name_by_source.get(src_id, "—"),
                "score": _to_float(getattr(row, "score", None)),
                "delta": _sparkline_delta(sparkline),
                "join": None,
                "loop_count": getattr(row, "loop_count", 0) or 0,
                "alarm_count": 0,
                "overdue_tasks": 0,
                "sparkline": sparkline,
                "lose_factors": [],
            }
        )
    items.sort(key=lambda x: (x["score"] is None, x["score"] if x["score"] is not None else 0))
    for idx, it in enumerate(items, 1):
        it["rank"] = idx
    return items


def shape_heatmap(
    kpi_rows: list[Any],
    hierarchy: dict[str, Any],
) -> dict[str, Any]:
    """单元 × 6 指标热力矩阵（对齐原型 heat：8 单元 × 6 指标 · 4 级色阶）。

    返回 {metrics:[{key,label,reverse}], units:[{id,name,plant,score,values:[number|null]}]}
    values 顺序与 metrics 对齐；null → 前端斜纹 N/A。
    """
    name_by_source_id: dict[int, str] = hierarchy["name_by_source_id"]
    by_id = hierarchy["by_id"]
    units: list[dict[str, Any]] = []
    for row in kpi_rows:
        src_id = getattr(row, "scope_id", None)
        # parent plant name（同 ranking_unit）
        parent_name = "—"
        for node in by_id.values():
            if node.type == "UNIT" and node.source_node_id == src_id:
                parent_id = node.parent_id
                parent = by_id.get(parent_id) if parent_id else None
                while parent is not None and parent.type not in ("FACTORY", "AREA"):
                    parent_id = parent.parent_id
                    parent = by_id.get(parent_id) if parent_id else None
                if parent is not None:
                    parent_name = parent.name
                break
        values: list[float | None] = []
        for key, _label, _rev in EVAL_METRICS:
            v = _to_float(getattr(row, key, None))
            # 归一为 0~100 口径（与原型 heatColor 阈值 92/84/76 对齐）
            values.append(round(v * 100, 1) if v is not None else None)
        units.append(
            {
                "id": src_id,
                "name": name_by_source_id.get(src_id) or f"单元#{src_id}",
                "plant": parent_name,
                "score": _to_float(getattr(row, "score", None)),
                "values": values,
            }
        )
    # 按评分升序（最低分居首，对齐原型热力行序）
    units.sort(key=lambda x: (x["score"] is None, x["score"] if x["score"] is not None else 0))
    return {
        "metrics": [{"key": k, "label": lb, "reverse": rv} for k, lb, rv in EVAL_METRICS],
        "units": units,
    }


def shape_trend(
    win_row: Any | None,
    prev_win_row: Any | None,
    target: float = ASSESSMENT_TARGET_SCORE,
) -> dict[str, Any]:
    """综合评分趋势 + 分项斜率 + 等级分布 + 控制模式 + 数据质量。

    - win_row: 当前窗口行（提供 score_trend + distribution JSONB）
    - prev_win_row: 上一周期窗口行（None → 前端派生 prev 系列，对齐 ScoreTrendChart）
    - distribution JSONB: {level_dist, mode_dist, data_quality, metric_slopes}
    """
    empty = {
        "series": {"current": [], "previous": []},
        "target": target,
        "slopes": [],
        "level_dist": [],
        "mode_dist": [],
        "data_quality": [],
        "snapshot_at": None,
    }
    if win_row is None:
        return empty

    current = list(getattr(win_row, "score_trend", None) or [])
    previous = list(getattr(prev_win_row, "score_trend", None) or [])
    dist = getattr(win_row, "distribution", None) or {}

    return {
        "series": {"current": current, "previous": previous},
        "target": target,
        "slopes": dist.get("metric_slopes", []) or [],
        "level_dist": dist.get("level_dist", []) or [],
        "mode_dist": dist.get("mode_dist", []) or [],
        "data_quality": dist.get("data_quality", []) or [],
        "snapshot_at": _iso(getattr(win_row, "snapshot_at", None)),
    }


# ---------------------------------------------------------------------------
# async 查询 helper
# ---------------------------------------------------------------------------


async def _query_scope_row(
    db: AsyncSession, scope_type: str, scope_id: int, window: str
) -> WorkbenchWindowSummary | None:
    """查指定 scope × window 的单行（用于 summary/trend 主系列）。"""
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == scope_type)
        .where(WorkbenchWindowSummary.scope_id == scope_id)
        .where(WorkbenchWindowSummary.window_w == window)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _query_prev_window_row(
    db: AsyncSession, scope_type: str, scope_id: int, window: str
) -> WorkbenchWindowSummary | None:
    """查上一周期窗口行（24h→取 7d 的同 scope 行作为"上一周期"近似）。

    原型 prev 由当前 trend 派生（trend[i]-1.2+噪声），此处优先取真实相邻窗口行，
    缺失时由前端按原型公式派生。
    """
    prev_window = {"24h": "7d", "7d": "30d"}.get(window)
    if prev_window is None:
        return None
    return await _query_scope_row(db, scope_type, scope_id, prev_window)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def build_assessment(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: int | None = None,
    window: str = "24h",
    view: str = "plant",
) -> dict[str, Any]:
    """组装 A-02 评估四块。部分失败容错：单块异常不阻断其余块。"""
    sid = _scope_id_int(scope_type, scope_id)
    assessment: dict[str, Any] = {
        "scope": {"type": scope_type, "id": scope_id},
        "window": window,
        "view": view,
        "summary": None,
        "ranking": [],
        "heatmap": {"metrics": [], "units": []},
        "trend": None,
    }

    # --- 主 scope 行（summary + trend 共用）---
    win_row = await _query_scope_row(db, scope_type, sid, window)

    # --- lose_factor 阈值 ---
    threshold = await _get_lose_threshold(db)

    # --- ranking + heatmap 共用的层级与子树查询 ---
    hierarchy = await _load_plant_hierarchy(db)
    alarm_per_unit = await _query_alarm_per_unit(db)
    overdue_per_unit = await _query_overdue_per_unit(db)

    # 全厂回路总数（参评分母）：取 GLOBAL 24h 行 loop_count；缺失时用当前 scope 行
    global_row = (
        win_row if scope_type == "GLOBAL" else await _query_scope_row(db, "GLOBAL", 0, window)
    )
    total_loops = getattr(global_row, "loop_count", 0) or 0 if global_row else 0

    # --- ranking（plant 视图：下一层 FACTORY/AREA/UNIT；unit 视图：UNIT 子树）---
    try:
        if view == "unit":
            desc_unit_ids = await _get_descendant_unit_ids(db, scope_type, sid)
            if not desc_unit_ids:
                unit_rows = await _query_scope_rows(db, "UNIT", window)
            else:
                stmt = (
                    select(WorkbenchWindowSummary)
                    .where(WorkbenchWindowSummary.scope_type == "UNIT")
                    .where(WorkbenchWindowSummary.scope_id.in_(desc_unit_ids))
                    .where(WorkbenchWindowSummary.window_w == window)
                )
                unit_rows = list((await db.execute(stmt)).scalars().all())
            assessment["ranking"] = shape_ranking_unit(unit_rows, hierarchy)
        else:
            child_type, child_ids = await _get_child_ids_for_plants(db, scope_type, sid)
            if scope_type == "GLOBAL" or not child_ids:
                plant_rows = await _query_scope_rows(db, child_type, window)
            else:
                stmt = (
                    select(WorkbenchWindowSummary)
                    .where(WorkbenchWindowSummary.scope_type == child_type)
                    .where(WorkbenchWindowSummary.scope_id.in_(child_ids))
                    .where(WorkbenchWindowSummary.window_w == window)
                )
                plant_rows = list((await db.execute(stmt)).scalars().all())
            assessment["ranking"] = shape_ranking_plant(
                plant_rows,
                hierarchy,
                alarm_per_unit,
                overdue_per_unit,
                threshold,
                total_loops,
            )
    except Exception:  # noqa: BLE001
        logger.warning("评估 ranking 块构建失败", exc_info=True)

    # --- heatmap（单元 × 6 指标，按 scope 递归过滤 UNIT 行）---
    try:
        desc_unit_ids = await _get_descendant_unit_ids(db, scope_type, sid)
        if not desc_unit_ids:
            unit_rows = await _query_scope_rows(db, "UNIT", window)
        else:
            stmt = (
                select(WorkbenchWindowSummary)
                .where(WorkbenchWindowSummary.scope_type == "UNIT")
                .where(WorkbenchWindowSummary.scope_id.in_(desc_unit_ids))
                .where(WorkbenchWindowSummary.window_w == window)
            )
            unit_rows = list((await db.execute(stmt)).scalars().all())
        assessment["heatmap"] = shape_heatmap(unit_rows, hierarchy)
    except Exception:  # noqa: BLE001
        logger.warning("评估 heatmap 块构建失败", exc_info=True)

    # --- summary（依赖 ranking 的 plants 风险速览；ranking 失败时用空 plants 兜底）---
    try:
        ranking_plants = assessment["ranking"] if view == "plant" and assessment["ranking"] else []
        assessment["summary"] = shape_summary(win_row, ranking_plants, total_loops)
    except Exception:  # noqa: BLE001
        logger.warning("评估 summary 块构建失败", exc_info=True)

    # --- trend（score_trend + distribution JSONB）---
    try:
        prev_row = await _query_prev_window_row(db, scope_type, sid, window)
        assessment["trend"] = shape_trend(win_row, prev_row)
    except Exception:  # noqa: BLE001
        logger.warning("评估 trend 块构建失败", exc_info=True)

    return assessment
