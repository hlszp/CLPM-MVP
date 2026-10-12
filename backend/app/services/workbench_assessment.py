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

口径（2026-10-10 P1-02，01§3.1）：
- 应评分母 = 回路台账参评三条件计数（CAL-06：is_active AND status='READY'
  AND include_in_evaluation，与 kpi_calc._eval_loop_selection_stmt 一致）；
- 环比 = 当前窗评分 − 上一等长窗评分（CAL-06，不再本窗首末差冒充环比）；
- 未计算显式区分（CAL-07：score NULL/旧伪 0 行 → None + score_state）；
- 子树 .in_() 查询每节点最新行去重（PERF-03 部分，DISTINCT ON 模式）。

部分失败容错：每块独立 try/except，失败返回空/None 并 log.warning，不阻断其余块。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.loop import LoopLedger
from app.models.workbench_summary import WorkbenchWindowSummary
from app.services.workbench_overview import (
    _get_child_ids_for_plants,
    _get_descendant_unit_ids,
    _get_lose_threshold,
    _iso,
    _load_plant_hierarchy,
    _loops_per_hour,
    _lose_factors,
    _query_alarm_per_unit,
    _query_overdue_per_unit,
    _query_scope_rows,
    _scope_id_int,
    _to_float,
)
from app.services.workbench_scope import node_scope_id

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


def _window_delta(cur: Any, prev: Any) -> float | None:
    """环比 delta = 当前窗口评分 − 上一等长窗口评分（CAL-06，2026-10-10）。

    01§3.1：环比使用上一等长窗口，不能用本窗口末值减首值冒充环比——
    原实现取本窗 score_trend 首末差，衡量的是窗内走势而非跨窗对比
    （首末差 +2.2 时上一等长窗对比可能是 -3.8，符号都可能错）。
    任一侧"未计算"（None）时返回 None，不虚构环比。
    """
    c = _to_float(cur)
    p = _to_float(prev)
    if c is None or p is None:
        return None
    return round(c - p, 2)


def _row_score(row: Any) -> float | None:
    """预计算行 → 综合评分（None 感知 + 旧伪 0 行过渡守卫，CAL-07）。

    - score 为 NULL（迁移 p102wsnull01 后新口径：无有效评分落 NULL）→ None；
    - loop_count=0 但 score 非 NULL（迁移前旧代码把无评分聚合写成 0.0 的
      存量行）→ 同样视为"未计算"返回 None，不把缺评按 0 分渲染。
    """
    if row is None:
        return None
    if not (getattr(row, "loop_count", 0) or 0):
        return None
    return _to_float(getattr(row, "score", None))


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
    prev_win_row: Any | None = None,
    target: float = ASSESSMENT_TARGET_SCORE,
) -> dict[str, Any]:
    """摘要带：评分 + 参评 + 距目标 + 环比 + 自然语言结论 + 风险速览。

    - win_row: 当前 scope × window 的预计算行（None → 空摘要）
    - plants:  ranking 已算好的装置列表（用于风险速览取最低分装置）
    - total_loops: 应评回路数（CAL-06：回路台账参评口径，build_assessment
      传入；win_row.loop_count 为参评分子）
    - prev_win_row: 上一等长窗口行（CAL-06：环比=当前窗−上一等长窗评分，
      None/未计算 → delta=None，不再用本窗首末差冒充环比）
    - score_state（CAL-07）：显式区分 COMPUTED / NOT_COMPUTED（未计算），
      不把缺评渲染成 0 分。
    """
    if win_row is None:
        return {
            "score": None,
            "score_state": "NOT_COMPUTED",
            "grade": "—",
            "participation": {"evaluated": 0, "total": total_loops},
            "distance_to_target": None,
            "delta": None,
            "target": target,
            "conclusion": "暂无评估数据",
            "conclusion_links": [],
            "risks": [],
        }

    score = _row_score(win_row)
    # 0930：参评口径与 overview 一致（窗口累计 → 平均每小时参评回路数）
    evaluated = _loops_per_hour(
        getattr(win_row, "loop_count", 0) or 0, getattr(win_row, "window_w", None)
    )
    delta = _window_delta(score, _row_score(prev_win_row))
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
            f"环比 <b>{'+' if (delta or 0) >= 0 else ''}{delta:.2f}</b> 分"
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
        # 2026-10-07 用户裁决：驾驶舱数值统一保留两位小数（结论文案内嵌数值同口径）
        conclusion = (
            f"控制性能处于 <b>{grade}</b> 水平，综合评分 "
            f"<b>{score:.2f}</b>，{delta_txt}；{worst_txt}。"
        )
    else:
        # CAL-07：行存在但未计算（score NULL 或旧伪 0 行）→ 显式"未计算"，
        # 与 win_row 缺行（暂无评估数据）区分
        conclusion = "本窗口无参评回路产出评分（未计算）"

    return {
        "score": score,
        "score_state": "COMPUTED" if score is not None else "NOT_COMPUTED",
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
    prev_rows_by_scope: dict[int, Any] | None = None,
) -> list[dict[str, Any]]:
    """装置视图排名（对齐原型 PLANTS 表：按综合评分升序 · 风险优先）。

    列：rank / name / score / delta / join(参评) / alarm / overdue / sparkline / lose_factors
    delta（CAL-06）：当前窗评分 − 上一等长窗评分（prev_rows_by_scope 按
    scope_id 提供上一等长窗行；缺失/未计算 → None）。
    """
    name_by_source_id: dict[int, str] = hierarchy["name_by_source_id"]
    unit_to_factory: dict[str, str] = hierarchy["unit_to_factory"]
    factories = hierarchy["factories"]
    prev_rows_by_scope = prev_rows_by_scope or {}
    # source_node_id → factory_id（与 overview.shape_plants 同款聚合）
    factory_id_by_source = {node_scope_id(f.source_node_id, f.id): f.id for f in factories}
    units_per_factory: dict[str, list[str]] = {}
    for unit_id, factory_id in unit_to_factory.items():
        units_per_factory.setdefault(factory_id, []).append(unit_id)

    items: list[dict[str, Any]] = []
    for row in kpi_rows:
        src_id = getattr(row, "scope_id", None)
        name = name_by_source_id.get(src_id) or f"装置#{src_id}"
        sparkline = getattr(row, "score_trend", None) or []
        loop_count = getattr(row, "loop_count", 0) or 0
        factory_id = factory_id_by_source.get(src_id)
        unit_ids = units_per_factory.get(factory_id, []) if factory_id else []
        score = _row_score(row)
        items.append(
            {
                "id": src_id,
                "name": name,
                "parent_name": None,
                "score": score,
                "delta": _window_delta(score, _row_score(prev_rows_by_scope.get(src_id))),
                "join": f"{loop_count}/{total_loops}",
                "loop_count": loop_count,
                # 0930：真值聚合（此前硬编码 0，排名表预警/超期两列恒空）
                "alarm_count": sum(alarm_per_unit.get(uid, 0) for uid in unit_ids),
                "overdue_tasks": sum(overdue_per_unit.get(uid, 0) for uid in unit_ids),
                "sparkline": sparkline,
                "lose_factors": _lose_factors(row, threshold),
            }
        )
    # 按综合评分升序（最低分 = 最高风险 = rank 1，对齐原型）
    items.sort(key=lambda x: (x["score"] is None, x["score"] if x["score"] is not None else 0))
    for idx, it in enumerate(items, 1):
        it["rank"] = idx
    return items


def shape_ranking_unit(
    kpi_rows: list[Any],
    hierarchy: dict[str, Any],
    prev_rows_by_scope: dict[int, Any] | None = None,
) -> list[dict[str, Any]]:
    """单元视图排名（对齐原型 UNITS 表：# / 单元 / 所属装置 / 评分 / 环比 / 24h 趋势）。

    简化列：rank / name / parent_name / score / delta / sparkline
    delta（CAL-06）：当前窗评分 − 上一等长窗评分（同 shape_ranking_plant）。
    """
    name_by_source_id: dict[int, str] = hierarchy["name_by_source_id"]
    by_id = hierarchy["by_id"]
    prev_rows_by_scope = prev_rows_by_scope or {}
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
        score = _row_score(row)
        items.append(
            {
                "id": src_id,
                "name": name,
                "parent_name": parent_name_by_source.get(src_id, "—"),
                "score": score,
                "delta": _window_delta(score, _row_score(prev_rows_by_scope.get(src_id))),
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
    by_id: dict[str, Any] = hierarchy["by_id"]
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
        # P1-02 补充接线（2026-10-10）：_row_score 同款守卫——存量伪 0 行
        # （loop_count=0 但聚合列被旧代码写成 0.0）整行视为"未计算"，
        # 指标值全列置 null（前端斜纹 N/A），不渲染 0 分色阶冒充真实评估
        row_computed = bool(getattr(row, "loop_count", 0) or 0)
        values: list[float | None] = []
        for key, _label, _rev in EVAL_METRICS:
            v = _to_float(getattr(row, key, None)) if row_computed else None
            # 归一为 0~100 口径（与原型 heatColor 阈值 92/84/76 对齐）
            values.append(round(v * 100, 1) if v is not None else None)
        units.append(
            {
                "id": src_id,
                "name": name_by_source_id.get(src_id) or f"单元#{src_id}",
                "plant": parent_name,
                "score": _row_score(row),
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

    # 0930 粒度守卫：previous 桶距与 current 差异过大（如 24h 小时桶混排 7d 日桶）
    # 时丢弃 previous——错粒度序列同轴混排会让趋势曲线失真（诚实留空优于错图）
    def _bucket_hours(points: list[Any]) -> float | None:
        if len(points) < 2:
            return None
        try:
            a = datetime.fromisoformat(points[0]["t"].replace("Z", "+00:00"))
            b = datetime.fromisoformat(points[1]["t"].replace("Z", "+00:00"))
            return abs((b - a).total_seconds()) / 3600
        except (KeyError, ValueError, TypeError):
            return None

    cur_bh = _bucket_hours(current)
    prev_bh = _bucket_hours(previous)
    if cur_bh and prev_bh and abs(cur_bh - prev_bh) > max(cur_bh, prev_bh) * 0.5:
        previous = []
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
    """查指定 scope × window 的单行（用于 summary/trend 主系列）。

    按窗口终点降序取最新行（precalc 每 5min 网格 upsert 新行，旧行保留
    供趋势回看；无序时可能命中历史行）。
    """
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == scope_type)
        .where(WorkbenchWindowSummary.scope_id == scope_id)
        .where(WorkbenchWindowSummary.window_w == window)
        .order_by(WorkbenchWindowSummary.window_end.desc())
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


# 窗口 → 小时数（与 WorkbenchWindowSummary.WINDOWS 对齐；环比上一等长窗换算用）
_WINDOW_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}


async def _query_prev_equal_row(
    db: AsyncSession, scope_type: str, scope_id: int, window: str, window_end: Any
) -> WorkbenchWindowSummary | None:
    """查上一等长窗口行（CAL-06，2026-10-10）：环比对比基准。

    同 scope × window，取 window_end ≤ 当前窗终点 − 窗长 的最新一行——
    预计算按 5min 网格 upsert，正常情况下 target 恰有对应行；该网格缺行时
    取更早的最近行（窗口近似等长）。不与 _query_prev_window_row（24h→7d
    跨窗长的趋势"上一周期"序列）混用。
    """
    hours = _WINDOW_HOURS.get(window)
    if hours is None or window_end is None:
        return None
    target = window_end - timedelta(hours=hours)
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == scope_type)
        .where(WorkbenchWindowSummary.scope_id == scope_id)
        .where(WorkbenchWindowSummary.window_w == window)
        .where(WorkbenchWindowSummary.window_end <= target)
        .order_by(WorkbenchWindowSummary.window_end.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _query_prev_scope_rows(
    db: AsyncSession, scope_type: str, scope_ids: list[int], window: str, window_end: Any
) -> dict[int, Any]:
    """批量查多个 scope 的上一等长窗口行（DISTINCT ON scope_id）→ {scope_id: row}。

    供 ranking 行级环比使用（CAL-06）；ids 为空或窗口不支持时返回空 dict
    （行级 delta 落 None，不虚构）。
    """
    hours = _WINDOW_HOURS.get(window)
    if hours is None or window_end is None or not scope_ids:
        return {}
    target = window_end - timedelta(hours=hours)
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == scope_type)
        .where(WorkbenchWindowSummary.scope_id.in_(scope_ids))
        .where(WorkbenchWindowSummary.window_w == window)
        .where(WorkbenchWindowSummary.window_end <= target)
        .distinct(WorkbenchWindowSummary.scope_id)
        .order_by(WorkbenchWindowSummary.scope_id, WorkbenchWindowSummary.window_end.desc())
    )
    return {row.scope_id: row for row in result.scalars().all()}


async def _query_scope_rows_in_ids(
    db: AsyncSession, scope_type: str, scope_ids: list[int], window: str
) -> list[Any]:
    """子树/子节点分支（scope_id.in_()）查询 + 每节点最新行去重（PERF-03，2026-10-10）。

    复用 workbench_overview._query_scope_rows 的 DISTINCT ON 模式：预计算表
    每 (scope × window) 保留最多 64 行历史快照，.in_() 不去重时排名/热力图
    会把同一 scope 的历史行渲染成重复条目。ids 为空返回空列表。
    """
    if not scope_ids:
        return []
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == scope_type)
        .where(WorkbenchWindowSummary.scope_id.in_(scope_ids))
        .where(WorkbenchWindowSummary.window_w == window)
        .distinct(WorkbenchWindowSummary.scope_id)
        .order_by(WorkbenchWindowSummary.scope_id, WorkbenchWindowSummary.window_end.desc())
    )
    return list(result.scalars().all())


# 应评回路数（子树）：参评三条件 + source_node_id 子树内 UNIT 挂载（CAL-06）
_EVALUABLE_SUBTREE_SQL = text(
    """
    WITH RECURSIVE node_tree AS (
        SELECT id, type FROM plant_node WHERE source_node_id = :sid
        UNION ALL
        SELECT c.id, c.type FROM plant_node c JOIN node_tree t ON c.parent_id = t.id
    )
    SELECT count(*) AS cnt
    FROM loop_ledger l
    WHERE l.is_active = TRUE
      AND l.status = 'READY'
      AND l.include_in_evaluation = TRUE
      AND l.unit_id IN (SELECT id FROM node_tree WHERE type = 'UNIT')
    """
)


async def _count_evaluable_loops(db: AsyncSession, scope_type: str, scope_id: int) -> int:
    """应评回路数（CAL-06，2026-10-10）：回路台账参评口径直接计数。

    参评三条件与 kpi_calc._eval_loop_selection_stmt 全量选路一致：
    is_active=True AND status='READY' AND include_in_evaluation=True（读方
    同批过滤）。不再从 GLOBAL 预计算行 loop_count（窗口内已评回路·小时
    累计）反推——该口径漏计窗口内未产出快照的参评回路，参与率虚高。

    - GLOBAL → 全厂参评回路数；
    - FACTORY/AREA/UNIT → source_node_id 子树（含自身）UNIT 挂载的参评回路数；
    - 其他 scope 类型（本端点不使用）→ 0 并告警。
    """
    if scope_type == "GLOBAL":
        stmt = (
            select(func.count())
            .select_from(LoopLedger)
            .where(
                LoopLedger.is_active.is_(True),
                LoopLedger.status == "READY",
                LoopLedger.include_in_evaluation.is_(True),
            )
        )
        return int((await db.execute(stmt)).scalar() or 0)
    if scope_type in ("FACTORY", "AREA", "UNIT"):
        result = await db.execute(_EVALUABLE_SUBTREE_SQL, {"sid": scope_id})
        return int(result.scalar() or 0)
    logger.warning("应评回路计数不支持 scope_type=%s，按 0 处理", scope_type)
    return 0


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
    """组装 A-02 评估四块。部分失败容错：单块异常不阻断其余块。

    口径（2026-10-10 P1-02）：
    - 应评分母 = 回路台账参评三条件计数（CAL-06，_count_evaluable_loops）；
    - 环比 = 当前窗 − 上一等长窗评分（CAL-06，_window_delta + 上一等长窗行）；
    - 未计算显式区分（CAL-07：score NULL/旧伪 0 行 → None + score_state）；
    - ranking/heatmap 子树查询每节点最新行去重（PERF-03）。
    """
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

    # --- CAL-06：主 scope 上一等长窗口行（环比基准；缺行 → delta=None）---
    prev_equal_row = None
    if win_row is not None:
        prev_equal_row = await _query_prev_equal_row(
            db, scope_type, sid, window, getattr(win_row, "window_end", None)
        )

    # --- lose_factor 阈值 ---
    threshold = await _get_lose_threshold(db)

    # --- ranking + heatmap 共用的层级与子树查询 ---
    hierarchy = await _load_plant_hierarchy(db)
    alarm_per_unit = await _query_alarm_per_unit(db)
    overdue_per_unit = await _query_overdue_per_unit(db)

    # 应评回路数（CAL-06）：回路台账参评口径，替代 GLOBAL 预计算行 loop_count 反推
    try:
        total_loops = await _count_evaluable_loops(db, scope_type, sid)
    except Exception:  # noqa: BLE001 — 分母查询失败不阻断四块，按未知 0 显式降级
        total_loops = 0
        logger.warning("应评回路计数失败，参与率分母按 0 显式降级", exc_info=True)

    # --- ranking（plant 视图：下一层 FACTORY/AREA/UNIT；unit 视图：UNIT 子树）---
    try:
        if view == "unit":
            desc_unit_ids = await _get_descendant_unit_ids(db, scope_type, sid)
            if not desc_unit_ids:
                unit_rows = await _query_scope_rows(db, "UNIT", window)
            else:
                # PERF-03：.in_() 分支加每节点最新行去重
                unit_rows = await _query_scope_rows_in_ids(db, "UNIT", desc_unit_ids, window)
            prev_rows_by_scope = await _query_prev_rows_for(db, "UNIT", unit_rows, window)
            assessment["ranking"] = shape_ranking_unit(unit_rows, hierarchy, prev_rows_by_scope)
        else:
            child_type, child_ids = await _get_child_ids_for_plants(db, scope_type, sid)
            if scope_type == "GLOBAL" or not child_ids:
                plant_rows = await _query_scope_rows(db, child_type, window)
            else:
                # PERF-03：.in_() 分支加每节点最新行去重
                plant_rows = await _query_scope_rows_in_ids(db, child_type, child_ids, window)
            prev_rows_by_scope = await _query_prev_rows_for(db, child_type, plant_rows, window)
            assessment["ranking"] = shape_ranking_plant(
                plant_rows,
                hierarchy,
                alarm_per_unit,
                overdue_per_unit,
                threshold,
                total_loops,
                prev_rows_by_scope,
            )
    except Exception:  # noqa: BLE001
        logger.warning("评估 ranking 块构建失败", exc_info=True)

    # --- heatmap（单元 × 6 指标，按 scope 递归过滤 UNIT 行）---
    try:
        desc_unit_ids = await _get_descendant_unit_ids(db, scope_type, sid)
        if not desc_unit_ids:
            unit_rows = await _query_scope_rows(db, "UNIT", window)
        else:
            # PERF-03：.in_() 分支加每节点最新行去重
            unit_rows = await _query_scope_rows_in_ids(db, "UNIT", desc_unit_ids, window)
        assessment["heatmap"] = shape_heatmap(unit_rows, hierarchy)
    except Exception:  # noqa: BLE001
        logger.warning("评估 heatmap 块构建失败", exc_info=True)

    # --- summary（依赖 ranking 的 plants 风险速览；ranking 失败时用空 plants 兜底）---
    try:
        ranking_plants = assessment["ranking"] if view == "plant" and assessment["ranking"] else []
        assessment["summary"] = shape_summary(win_row, ranking_plants, total_loops, prev_equal_row)
    except Exception:  # noqa: BLE001
        logger.warning("评估 summary 块构建失败", exc_info=True)

    # --- trend（score_trend + distribution JSONB）---
    try:
        prev_row = await _query_prev_window_row(db, scope_type, sid, window)
        assessment["trend"] = shape_trend(win_row, prev_row)
    except Exception:  # noqa: BLE001
        logger.warning("评估 trend 块构建失败", exc_info=True)

    return assessment


async def _query_prev_rows_for(
    db: AsyncSession, scope_type: str, rows: list[Any], window: str
) -> dict[int, Any]:
    """ranking 行集 → 上一等长窗口行映射（CAL-06）。

    以行集内最新 window_end 为基准批量查（同窗各行 window_end 一致——预计算
    同网格 upsert）；行集为空返回空 dict。
    """
    if not rows:
        return {}
    ends = [getattr(r, "window_end", None) for r in rows]
    cur_end = max((e for e in ends if e is not None), default=None)
    ids = [getattr(r, "scope_id", None) for r in rows]
    return await _query_prev_scope_rows(db, scope_type, ids, window, cur_end)
