"""A-01 工作台总览聚合 service（M2 批次 G-总览 · F-OV-01~05）.

组装 Tab1「系统总览」六块数据：
- windows   三窗口 KPI（24h/7d/30d，读 workbench_window_summary 预计算表 M-02）
- plants    装置排名（FACTORY 行 + sparkline + lose_factors + alarm + overdue）
- units     单元 ×6 指标热力（UNIT 行，缺数据 → None，前端 CSS 斜纹）
- pareto    异常类型分布（MV-02 mv_diagnosis_pareto）
- roots     根因 Top N（DiagnosisTag 按 tag_code 聚合，active 优先）
- funnel    处置漏斗（MV-03 mv_handling_funnel，4 泳道计数 + 超期）

数据架构：本 service **不直接查 TDengine**；计算类历史数据由 workbench_precalc
任务预计算写入 workbench_window_summary，本 service 只读预计算表/MV。
（docs/过程文档/data-architecture-decision-local-first-2026-07-20.md）

scope_id 约定（G-总览定义，precalc 任务须遵守）：
- GLOBAL → scope_id=0
- FACTORY/AREA/UNIT → scope_id = PlantNode.source_node_id（AAS 整数 ID）
  （PlantNode.id 为 UUID，source_node_id 为 int，与 workbench_window_summary.scope_id 对齐）

部分失败容错：每块独立 try/except，失败返回空/None 并 log.warning，不阻断其余块。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.diagnosis import DiagnosisTag
from app.models.handling_order import HandlingOrder
from app.models.plant_node import PlantNode
from app.models.sys_config import SysConfig
from app.models.workbench_summary import WorkbenchWindowSummary

logger = logging.getLogger(__name__)

# 6 项 KPI（"越高越好"）— MiniKpiStrip(6×3)、HeatMatrix(6 列)、lose_factors 判定统一口径
# 取 workbench_window_summary 中 6 个正向率；oscillation/saturation/instrument_fault 为反向率不计
# 标签与 app/services/performance.py KPI_NAME_MAP 对齐
KPI_METRICS: tuple[tuple[str, str], ...] = (
    ("good_value_rate", "好值率"),
    ("auto_mode_rate", "自控率"),
    ("effective_auto_rate", "有效自控率"),
    ("steady_rate", "平稳率"),
    ("accuracy_rate", "准确率"),
    ("fast_rate", "快速率"),
)

# lose_factors 判定阈值：6 指标率低于此值计入损失因子列表（运行时经 sys_config 可调）
DEFAULT_LOSE_FACTOR_THRESHOLD = 0.90
LOSE_FACTOR_CONFIG_KEY = "workbench.lose_factor_threshold"

ROOTS_TOP_N = 10  # 根因 Top N（用户决策：10 条）

WINDOWS_ALL = ("24h", "7d", "30d")


# ---------------------------------------------------------------------------
# 纯工具
# ---------------------------------------------------------------------------


def _to_float(val: Any) -> float | None:
    """Decimal/数值 → float，None 透传。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _iso(val: Any) -> str | None:
    """datetime → ISO 字符串，None 透传。"""
    if val is None:
        return None
    try:
        return val.isoformat()
    except (AttributeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 纯 shaper（无 DB，单测友好）
# ---------------------------------------------------------------------------


def _shape_window_row(row: Any) -> dict[str, Any]:
    """单行 workbench_window_summary → window 块字典。"""
    metrics = {key: _to_float(getattr(row, key, None)) for key, _ in KPI_METRICS}
    return {
        "score": _to_float(getattr(row, "score", None)),
        "status": getattr(row, "status", None),
        "loop_count": getattr(row, "loop_count", 0) or 0,
        "metrics": metrics,
        "score_trend": getattr(row, "score_trend", None) or [],
        "flags": getattr(row, "flags", None) or [],
        "snapshot_at": _iso(getattr(row, "snapshot_at", None)),
    }


def shape_windows(rows: list[Any]) -> dict[str, Any]:
    """三窗口 KPI：rows 为 scope 行（最多 3 条 24h/7d/30d）→ {window: block|None}。"""
    out: dict[str, Any] = {}
    for row in rows:
        win = getattr(row, "window_w", None) or getattr(row, "window", None)
        if win:
            out[win] = _shape_window_row(row)
    for w in WINDOWS_ALL:
        out.setdefault(w, None)
    return out


def _lose_factors(row: Any, threshold: float) -> list[str]:
    """6 指标率低于阈值 → 返回损失因子中文标签列表。"""
    factors: list[str] = []
    for key, label in KPI_METRICS:
        v = _to_float(getattr(row, key, None))
        if v is not None and v < threshold:
            factors.append(label)
    return factors


def shape_plants(
    kpi_rows: list[Any],
    hierarchy: dict[str, Any],
    alarm_per_unit: dict[str, int],
    overdue_per_unit: dict[str, int],
    threshold: float,
) -> list[dict[str, Any]]:
    """装置排名：FACTORY 预计算行 + 层级映射 → 排名列表（按 score 降序）。"""
    unit_to_factory: dict[str, str] = hierarchy["unit_to_factory"]
    name_by_source_id: dict[int, str] = hierarchy["name_by_source_id"]
    factories = hierarchy["factories"]
    # source_node_id → factory_id（UUID）映射
    factory_id_by_source = {
        f.source_node_id: f.id for f in factories if f.source_node_id is not None
    }
    # 倒置：factory_id → [unit_id]
    units_per_factory: dict[str, list[str]] = {}
    for unit_id, factory_id in unit_to_factory.items():
        units_per_factory.setdefault(factory_id, []).append(unit_id)

    plants: list[dict[str, Any]] = []
    for row in kpi_rows:
        src_id = getattr(row, "scope_id", None)
        name = name_by_source_id.get(src_id) or f"装置#{src_id}"
        factory_id = factory_id_by_source.get(src_id)
        unit_ids = units_per_factory.get(factory_id, []) if factory_id else []
        alarm_count = sum(alarm_per_unit.get(uid, 0) for uid in unit_ids)
        overdue = sum(overdue_per_unit.get(uid, 0) for uid in unit_ids)
        plants.append(
            {
                "id": src_id,
                "name": name,
                "score": _to_float(getattr(row, "score", None)),
                "status": getattr(row, "status", None),
                "loop_count": getattr(row, "loop_count", 0) or 0,
                "sparkline": getattr(row, "score_trend", None) or [],
                "lose_factors": _lose_factors(row, threshold),
                "alarm_count": alarm_count,
                "overdue_tasks": overdue,
            }
        )
    # 得分降序排名；None 得分排末尾
    plants.sort(key=lambda p: (p["score"] is None, -(p["score"] or 0)))
    for idx, p in enumerate(plants, 1):
        p["rank"] = idx
    return plants


def shape_units(kpi_rows: list[Any], hierarchy: dict[str, Any]) -> list[dict[str, Any]]:
    """单元热力：UNIT 预计算行 → {id,name,score,status,metrics{6|None}}。"""
    name_by_source_id: dict[int, str] = hierarchy["name_by_source_id"]
    units: list[dict[str, Any]] = []
    for row in kpi_rows:
        src_id = getattr(row, "scope_id", None)
        units.append(
            {
                "id": src_id,
                "name": name_by_source_id.get(src_id) or f"单元#{src_id}",
                "score": _to_float(getattr(row, "score", None)),
                "status": getattr(row, "status", None),
                "metrics": {key: _to_float(getattr(row, key, None)) for key, _ in KPI_METRICS},
            }
        )
    return units


def shape_pareto(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """MV-02 行 → Pareto 列表（root_cause/tag_count/converted/ignored/sla_warned）。"""
    return [
        {
            "root_cause": r.get("root_cause"),
            "tag_count": r.get("tag_count", 0) or 0,
            "converted_count": r.get("converted_count", 0) or 0,
            "ignored_count": r.get("ignored_count", 0) or 0,
            "sla_warned_count": r.get("sla_warned_count", 0) or 0,
        }
        for r in rows
    ]


_SEVERITY_RANK_TO_LABEL = {4: "CRITICAL", 3: "ERROR", 2: "WARN", 1: "INFO"}


def shape_roots(rows: list[Any], top_n: int = ROOTS_TOP_N) -> list[dict[str, Any]]:
    """DiagnosisTag 聚合行 → 根因 Top N（tag_code/count/active/severity）。

    severity 取该 tag_code 下最严重一档（CRITICAL>ERROR>WARN>INFO），由 SQL MAX(rank) 映射。
    """
    items: list[dict[str, Any]] = []
    for r in rows:
        rank = getattr(r, "severity_rank", None)
        if isinstance(r, dict):
            rank = r.get("severity_rank")
        severity = _SEVERITY_RANK_TO_LABEL.get(int(rank)) if rank is not None else None
        items.append(
            {
                "tag_code": getattr(r, "tag_code", None)
                or (r.get("tag_code") if isinstance(r, dict) else None),
                "tag_name": getattr(r, "tag_name", None)
                or (r.get("tag_name") if isinstance(r, dict) else None),
                "count": getattr(r, "count", None)
                or (r.get("count") if isinstance(r, dict) else 0)
                or 0,
                "active_count": getattr(r, "active_count", None)
                or (r.get("active_count") if isinstance(r, dict) else 0)
                or 0,
                "severity": severity,
            }
        )
    # active 优先，再按总数降序
    items.sort(key=lambda x: (-(x["active_count"] or 0), -(x["count"] or 0)))
    return items[:top_n]


def shape_funnel(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """MV-03 GLOBAL 行 → 漏斗（4 泳道计数 + 超期 + 平均周期）。"""
    if not row:
        return None
    return {
        "pending": row.get("pending_count", 0) or 0,
        "executing": row.get("executing_count", 0) or 0,
        "verifying": row.get("verifying_count", 0) or 0,
        "closed": row.get("closed_count", 0) or 0,
        "reopened": row.get("reopened_count", 0) or 0,
        "breached": row.get("breached_count", 0) or 0,
        "avg_cycle_hours": _to_float(row.get("avg_cycle_hours")),
    }


# ---------------------------------------------------------------------------
# async 查询 helper
# ---------------------------------------------------------------------------


async def _get_lose_threshold(db: AsyncSession) -> float:
    """读 sys_config lose_factor 阈值，缺失/非法回退默认 0.90。"""
    try:
        result = await db.execute(select(SysConfig).where(SysConfig.key == LOSE_FACTOR_CONFIG_KEY))
        row = result.scalar_one_or_none()
        if row and row.value:
            try:
                return float(row.value)
            except ValueError:
                logger.warning(
                    "sys_config %s 值非法：%r，回退默认", LOSE_FACTOR_CONFIG_KEY, row.value
                )
    except Exception:  # noqa: BLE001 — sys_config 读失败不应阻断总览
        logger.warning(
            "读取 sys_config %s 失败，回退默认阈值", LOSE_FACTOR_CONFIG_KEY, exc_info=True
        )
    return DEFAULT_LOSE_FACTOR_THRESHOLD


async def _load_plant_hierarchy(db: AsyncSession) -> dict[str, Any]:
    """加载 PlantNode 三层，构建 unit→factory / source_id→name 映射。"""
    result = await db.execute(select(PlantNode))
    nodes = result.scalars().all()
    by_id = {n.id: n for n in nodes}
    unit_to_factory: dict[str, str] = {}
    for n in nodes:
        if n.type == "UNIT":
            fid = _resolve_factory_id(n, by_id)
            if fid:
                unit_to_factory[n.id] = fid
    name_by_source_id = {n.source_node_id: n.name for n in nodes if n.source_node_id is not None}
    return {
        "by_id": by_id,
        "unit_to_factory": unit_to_factory,
        "name_by_source_id": name_by_source_id,
        "factories": [n for n in nodes if n.type == "FACTORY"],
    }


def _resolve_factory_id(node: Any, by_id: dict[str, Any]) -> str | None:
    """沿 parent_id 上溯到 FACTORY 节点 id。"""
    current = node
    seen: set[str] = set()
    while current and current.id not in seen:
        seen.add(current.id)
        if current.type == "FACTORY":
            return current.id
        parent_id = current.parent_id
        current = by_id.get(parent_id) if parent_id else None
    return None


async def _query_windows(db: AsyncSession, scope_type: str, scope_id: int) -> list[Any]:
    """查三窗口 KPI 行（GLOBAL 取 scope_id=0）。"""
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == scope_type)
        .where(WorkbenchWindowSummary.scope_id == scope_id)
    )
    return list(result.scalars().all())


async def _query_scope_rows(db: AsyncSession, scope_type: str, window: str) -> list[Any]:
    """查指定层级 + 窗口的预计算行（用于 plants=FACTORY / units=UNIT）。"""
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == scope_type)
        .where(WorkbenchWindowSummary.window_w == window)
    )
    return list(result.scalars().all())


async def _get_child_ids_for_plants(
    db: AsyncSession, scope_type: str, scope_id: int
) -> tuple[None | str, list[int]]:
    """查 plants 排名所需的下一层 scope_type + source_node_id 列表。

    - GLOBAL → ("FACTORY", [])  意为查全部 FACTORY 行
    - FACTORY → ("AREA", [子 area source_node_id, …])
    - AREA → ("UNIT", [子 unit source_node_id, …])
    """
    if scope_type == "GLOBAL":
        return "FACTORY", []

    # 递归查直接子节点（下一层）
    child_type = "AREA" if scope_type == "FACTORY" else "UNIT"
    result = await db.execute(
        text(
            """
            WITH RECURSIVE node_tree AS (
                SELECT id, source_node_id, type, parent_id
                FROM plant_node WHERE source_node_id = :sid
                UNION ALL
                SELECT c.id, c.source_node_id, c.type, c.parent_id
                FROM plant_node c JOIN node_tree t ON c.parent_id = t.id
            )
            SELECT source_node_id FROM node_tree
            WHERE type = :child_type AND source_node_id IS NOT NULL
              AND source_node_id != :sid
            """
        ),
        {"sid": scope_id, "child_type": child_type},
    )
    ids = [int(row[0]) for row in result.all() if row[0] is not None]
    return child_type, ids


async def _get_descendant_unit_ids(
    db: AsyncSession, scope_type: str, scope_id: int
) -> list[int]:
    """递归查所有 UNIT 后代的 source_node_id（用于 units 热力图过滤）。

    - GLOBAL → []  意为查全部 UNIT 行
    - FACTORY/AREA → 递归子树中所有 type=UNIT 的 source_node_id
    """
    if scope_type == "GLOBAL":
        return []

    result = await db.execute(
        text(
            """
            WITH RECURSIVE node_tree AS (
                SELECT id, source_node_id, type, parent_id
                FROM plant_node WHERE source_node_id = :sid
                UNION ALL
                SELECT c.id, c.source_node_id, c.type, c.parent_id
                FROM plant_node c JOIN node_tree t ON c.parent_id = t.id
            )
            SELECT source_node_id FROM node_tree
            WHERE type = 'UNIT' AND source_node_id IS NOT NULL
              AND source_node_id != :sid
            """
        ),
        {"sid": scope_id},
    )
    return [int(row[0]) for row in result.all() if row[0] is not None]


async def _query_alarm_per_unit(db: AsyncSession) -> dict[str, int]:
    """ACTIVE diagnosis_tag 按 loop.unit_id 聚合计数（→ 再映射到 factory）。"""
    from app.models.loop import LoopLedger

    result = await db.execute(
        select(LoopLedger.unit_id, func.count(DiagnosisTag.id))
        .join(DiagnosisTag, DiagnosisTag.loop_id == LoopLedger.id)
        .where(DiagnosisTag.status == "ACTIVE")
        .group_by(LoopLedger.unit_id)
    )
    return {row[0]: int(row[1]) for row in result.all() if row[0]}


async def _query_overdue_per_unit(db: AsyncSession) -> dict[str, int]:
    """BREACH handling_order 按 loop.unit_id 聚合计数（未闭合工单）。"""
    from app.models.loop import LoopLedger

    result = await db.execute(
        select(LoopLedger.unit_id, func.count(HandlingOrder.id))
        .join(HandlingOrder, HandlingOrder.loop_id == LoopLedger.id)
        .where(HandlingOrder.sla_stage == "BREACH")
        .where(HandlingOrder.status.notin_(["CLOSED", "CANCELLED"]))
        .group_by(LoopLedger.unit_id)
    )
    return {row[0]: int(row[1]) for row in result.all() if row[0]}


async def _query_pareto(db: AsyncSession) -> list[dict[str, Any]]:
    """MV-02 mv_diagnosis_pareto 全量（按 tag_count 降序）。"""
    result = await db.execute(
        text(
            "SELECT root_cause, tag_count, converted_count, ignored_count, sla_warned_count "
            "FROM mv_diagnosis_pareto ORDER BY tag_count DESC"
        )
    )
    return [dict(r._mapping) for r in result.all()]


async def _query_roots(db: AsyncSession, top_n: int = ROOTS_TOP_N) -> list[Any]:
    """DiagnosisTag 按 tag_code 聚合 Top N（总数 + active 子计数 + 最高严重度 rank）。

    使用 text() 直接写 SQL 以规避 SQLAlchemy func.case + GROUP BY 在 asyncpg 下
    的列引用歧义问题。
    """
    result = await db.execute(
        text(
            """
            SELECT tag_code,
                   max(tag_name) AS tag_name,
                   count(*) AS count,
                   count(*) FILTER (WHERE status = 'ACTIVE') AS active_count,
                   MAX(CASE
                       WHEN severity = 'CRITICAL' THEN 4
                       WHEN severity = 'ERROR' THEN 3
                       WHEN severity = 'WARN' THEN 2
                       ELSE 1
                   END) AS severity_rank
            FROM diagnosis_tag
            GROUP BY tag_code
            ORDER BY count DESC
            LIMIT :top_n
            """
        ),
        {"top_n": top_n},
    )
    return [dict(r._mapping) for r in result.all()]


async def _query_funnel(db: AsyncSession, scope_type: str, scope_id: int) -> dict[str, Any] | None:
    """MV-03 mv_handling_funnel 指定 scope 行（GLOBAL: scope_type='GLOBAL', scope_id=0）。"""
    result = await db.execute(
        text(
            "SELECT pending_count, executing_count, verifying_count, closed_count, "
            "reopened_count, breached_count, avg_cycle_hours "
            "FROM mv_handling_funnel WHERE scope_type = :st AND scope_id = :sid"
        ),
        {"st": scope_type, "sid": scope_id},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _scope_id_int(scope_type: str, scope_id: int | None) -> int:
    """scope_id 归一：GLOBAL → 0，其余透传。"""
    if scope_type == "GLOBAL" or scope_id is None:
        return 0
    return int(scope_id)


async def build_overview(
    db: AsyncSession,
    scope_type: str = "GLOBAL",
    scope_id: int | None = None,
    window: str = "24h",
) -> dict[str, Any]:
    """组装 A-01 总览六块。部分失败容错：单块异常不阻断其余块。"""
    sid = _scope_id_int(scope_type, scope_id)
    overview: dict[str, Any] = {
        "scope": {"type": scope_type, "id": scope_id},
        "window": window,
        "windows": dict.fromkeys(WINDOWS_ALL),
        "plants": [],
        "units": [],
        "pareto": [],
        "roots": [],
        "funnel": None,
    }

    # --- windows：三窗口 KPI（选中 scope）---
    try:
        win_rows = await _query_windows(db, scope_type, sid)
        overview["windows"] = shape_windows(win_rows)
    except Exception:  # noqa: BLE001
        logger.warning("总览 windows 块构建失败", exc_info=True)

    # --- lose_factor 阈值（sys_config 可调）---
    threshold = await _get_lose_threshold(db)

    # --- plants：下一层排名（GLOBAL→工厂 / FACTORY→装置 / AREA→单元）+ alarm/overdue 聚合 ---
    try:
        hierarchy = await _load_plant_hierarchy(db)
        child_type, child_ids = await _get_child_ids_for_plants(db, scope_type, sid)
        if scope_type == "GLOBAL" or not child_ids:
            # GLOBAL → 全部 FACTORY 行
            plant_rows = await _query_scope_rows(db, child_type, window)
        else:
            # FACTORY/AREA → 限定子节点 ID
            stmt = (
                select(WorkbenchWindowSummary)
                .where(WorkbenchWindowSummary.scope_type == child_type)
                .where(WorkbenchWindowSummary.scope_id.in_(child_ids))
                .where(WorkbenchWindowSummary.window_w == window)
            )
            plant_rows = list((await db.execute(stmt)).scalars().all())
        alarm_per_unit = await _query_alarm_per_unit(db)
        overdue_per_unit = await _query_overdue_per_unit(db)
        overview["plants"] = shape_plants(
            plant_rows, hierarchy, alarm_per_unit, overdue_per_unit, threshold
        )
    except Exception:  # noqa: BLE001
        logger.warning("总览 plants 块构建失败", exc_info=True)

    # --- units：单元 ×6 指标热力（按 scope 递归过滤 UNIT 行）---
    try:
        hierarchy_u = await _load_plant_hierarchy(db)
        desc_unit_ids = await _get_descendant_unit_ids(db, scope_type, sid)
        if not desc_unit_ids:
            # GLOBAL → 全部 UNIT 行
            unit_rows = await _query_scope_rows(db, "UNIT", window)
        else:
            stmt_u = (
                select(WorkbenchWindowSummary)
                .where(WorkbenchWindowSummary.scope_type == "UNIT")
                .where(WorkbenchWindowSummary.scope_id.in_(desc_unit_ids))
                .where(WorkbenchWindowSummary.window_w == window)
            )
            unit_rows = list((await db.execute(stmt_u)).scalars().all())
        overview["units"] = shape_units(unit_rows, hierarchy_u)
    except Exception:  # noqa: BLE001
        logger.warning("总览 units 块构建失败", exc_info=True)

    # --- pareto：异常类型分布（MV-02）---
    try:
        overview["pareto"] = shape_pareto(await _query_pareto(db))
    except Exception:  # noqa: BLE001
        logger.warning("总览 pareto 块构建失败", exc_info=True)

    # --- roots：根因 Top N（DiagnosisTag 聚合）---
    try:
        root_rows = await _query_roots(db, ROOTS_TOP_N)
        overview["roots"] = shape_roots(root_rows, ROOTS_TOP_N)
    except Exception:  # noqa: BLE001
        logger.warning("总览 roots 块构建失败", exc_info=True)

    # --- funnel：处置漏斗（MV-03，scope 行）---
    try:
        # 非全局 scope 的 funnel 行用其 scope_type/scope_id；GLOBAL 用 'GLOBAL'/0
        ft = scope_type if scope_type != "GLOBAL" else "GLOBAL"
        overview["funnel"] = shape_funnel(await _query_funnel(db, ft, sid))
    except Exception:  # noqa: BLE001
        logger.warning("总览 funnel 块构建失败", exc_info=True)

    return overview
