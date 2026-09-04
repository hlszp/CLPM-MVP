"""驾驶舱总览聚合 service（11 号方案 §10，C1 批次）.

组装驾驶舱聚合端点数据：
- overview    KPI 指标带 + 闭环治理漏斗（GET /cockpit/overview）
- roles       后台访问角色清单（GET /cockpit/backend-access-roles）
- node_tree   工厂→装置→单元三层树 + 回路计数（GET /cockpit/node-tree）

数据架构：本 service **不直接查 TDengine**；KPI 读 workbench_window_summary
预计算表（GLOBAL scope_id=0）与 unit_kpi_summary 聚合，闭环漏斗读现有业务表
（diagnosis_run / tuning_record / handling_order / alert_event）。

部分失败容错：每块独立 try/except，失败返回空/None 并 log.warning，不阻断其余块。
窗口起点用参数化 naive UTC（datetime.now(UTC).replace(tzinfo=None)，
PG 会话时区 +8，勿裸用 now()）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertEvent
from app.models.diagnosis_run import DiagnosisRun
from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger
from app.models.plant_node import PlantNode
from app.models.sys_config import SysConfig
from app.models.tuning import TuningRecord
from app.models.unit_kpi_summary import UnitKpiSummary
from app.models.workbench_summary import WorkbenchWindowSummary

logger = logging.getLogger(__name__)

# 窗口 → 小时数（24h/7d/30d，与 workbench_window_summary.window 对齐）
WINDOW_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}

# 回路五档等级（grade-distribution 口径，INCONCLUSIVE 不进分布条）
GRADE_KEYS = ("EXCELLENT", "GOOD", "FAIR", "WARNING", "POOR")

# sys_config 键：驾驶舱"管理后台"入口允许角色（逗号分隔）
BACKEND_ROLES_CONFIG_KEY = "cockpit.backend_access_roles"
DEFAULT_BACKEND_ROLES = ("IC_ENGINEER", "PE_ENGINEER", "ADMIN", "EXPERT")

# 待办口径：handling_order 未闭合活动态（PENDING/EXECUTING/VERIFYING）
TODO_ACTIVE_STATUSES = ("PENDING", "EXECUTING", "VERIFYING")
# 工单终态（超期/积压统计排除）
ORDER_TERMINAL_STATUSES = ("CLOSED", "CANCELLED")
# 漏斗 diagnosed 口径：diagnosis_run 完成态（无 COMPLETED 态，SUCCESS/PARTIAL 为完成）
DIAGNOSIS_DONE_STATUSES = ("SUCCESS", "PARTIAL")
# 漏斗 tuned 口径：tuning_record 方案已确认（进入实施/验证；无独立 CONFIRMED 态）
TUNING_CONFIRMED_STATUSES = ("APPLIED", "VERIFIED")


# ---------------------------------------------------------------------------
# 纯工具 / shaper（无 DB，单测友好）
# ---------------------------------------------------------------------------


def _to_float(val: Any) -> float | None:
    """Decimal/数值 → float，None 透传。"""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def parse_backend_roles(value: str | None) -> list[str]:
    """解析 sys_config 角色清单：逗号分隔 → 去空白去空项；缺失/为空回退默认。"""
    if value:
        roles = [r.strip() for r in value.split(",") if r.strip()]
        if roles:
            return roles
    return list(DEFAULT_BACKEND_ROLES)


def shape_node_tree(
    nodes: Sequence[Any],
    loop_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    """工厂→装置→单元三层树 + 回路计数向上累加（纯函数）。

    - nodes: plant_node 行（属性访问：id/name/type/parent_id/source_node_id/sort_order）
    - loop_counts: {unit_id: 活跃回路数}（loop_ledger is_active=true 按 unit_id 聚合）
    - 输出节点：{id=source_node_id(int|None), nodeId=plant_node.id, name, type,
      loopCount, children}；UNIT 取自身计数，FACTORY/AREA 为子树累加
    - 同级按 sort_order → name 排序；parent 缺失的节点按根节点处理
    """
    by_id = {n.id: n for n in nodes}
    items: dict[str, dict[str, Any]] = {
        n.id: {
            "id": n.source_node_id,
            "nodeId": n.id,
            "name": n.name,
            "type": n.type,
            "loopCount": 0,
            "children": [],
        }
        for n in nodes
    }
    roots: list[dict[str, Any]] = []
    for n in sorted(nodes, key=lambda x: (x.sort_order or 0, x.name or "")):
        it = items[n.id]
        if n.type == "UNIT":
            it["loopCount"] = int(loop_counts.get(n.id, 0) or 0)
        if n.parent_id and n.parent_id in by_id:
            items[n.parent_id]["children"].append(it)
        else:
            roots.append(it)

    def _accumulate(node: dict[str, Any]) -> int:
        total = node["loopCount"]
        for child in node["children"]:
            total += _accumulate(child)
        node["loopCount"] = total
        return total

    for root in roots:
        _accumulate(root)
    return roots


def _delta(cur: float | None, prev: float | None) -> float | None:
    """环比差值：两侧齐全才计算，否则 None。"""
    if cur is None or prev is None:
        return None
    return round(cur - prev, 2)


def _rate_to_pct(val: Any) -> float | None:
    """率值归一到 0-100 标度：workbench_window_summary 存 0-1 小数（如 0.912），
    unit_kpi_summary 存 0-100；≤1.5 视为小数标度乘 100，保证与环比 delta
    （0-100 标度）及前端 `${v}%` 直显口径一致。"""
    v = _to_float(val)
    if v is None:
        return None
    return round(v * 100, 2) if v <= 1.5 else round(v, 2)


def _default_kpi() -> dict[str, Any]:
    return {
        "score": None,
        "scoreDelta": None,
        "autoRate": None,
        "autoRateDelta": None,
        "loopTotal": 0,
        "gradeDistribution": dict.fromkeys(GRADE_KEYS, 0),
        "degradedCount": 0,
        "degradedDelta": None,
        "todoPending": 0,
        "todoOverdue": 0,
        "alertActive": 0,
        "alertUnconfirmed": 0,
    }


def _default_funnel() -> dict[str, Any]:
    return {
        "discovered": 0,
        "diagnosed": 0,
        "tuned": 0,
        "closed": 0,
        "backlog": {"pending": 0, "inProgress": 0, "verifying": 0},
    }


# ---------------------------------------------------------------------------
# async 查询 helper
# ---------------------------------------------------------------------------


async def _query_global_summary_row(db: AsyncSession, window: str) -> WorkbenchWindowSummary | None:
    """全厂窗口 KPI 行（GLOBAL scope_id=0，取最新 window_end）。"""
    result = await db.execute(
        select(WorkbenchWindowSummary)
        .where(WorkbenchWindowSummary.scope_type == "GLOBAL")
        .where(WorkbenchWindowSummary.scope_id == 0)
        .where(WorkbenchWindowSummary.window_w == window)
        .order_by(WorkbenchWindowSummary.window_end.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _query_window_weighted_avg(
    db: AsyncSession, start: datetime, end: datetime
) -> dict[str, float | None]:
    """窗口内全厂加权均值（unit_kpi_summary，仅 UNIT 节点避免父子重复累加）。

    口径同 dashboard board/trend：按 evaluated_loops 加权，每个字段用独立的
    「非 NULL 分母」——SUM(evaluated_loops) FILTER (WHERE field IS NOT NULL)。
    """
    score_num = func.sum(UnitKpiSummary.avg_score * UnitKpiSummary.evaluated_loops)
    score_den = func.sum(UnitKpiSummary.evaluated_loops).filter(
        UnitKpiSummary.avg_score.is_not(None)
    )
    auto_num = func.sum(UnitKpiSummary.auto_mode_rate * UnitKpiSummary.evaluated_loops)
    auto_den = func.sum(UnitKpiSummary.evaluated_loops).filter(
        UnitKpiSummary.auto_mode_rate.is_not(None)
    )
    stmt = (
        select(score_num, score_den, auto_num, auto_den)
        .join(PlantNode, UnitKpiSummary.node_id == PlantNode.id)
        .where(
            PlantNode.type == "UNIT",
            UnitKpiSummary.snapshot_time >= start,
            UnitKpiSummary.snapshot_time <= end,
        )
    )
    row = (await db.execute(stmt)).one()

    def _avg(num: Any, den: Any) -> float | None:
        den_f = _to_float(den)
        if not den_f or den_f <= 0:
            return None
        num_f = _to_float(num)
        return round(num_f / den_f, 2) if num_f is not None else None

    return {"score": _avg(row[0], row[1]), "auto_rate": _avg(row[2], row[3])}


async def _query_todo_counts(db: AsyncSession) -> dict[str, int]:
    """处置待办：未闭合活动态计数 + sla_stage='BREACH' 且未闭合超期计数。"""
    pending = await db.execute(
        select(func.count())
        .select_from(HandlingOrder)
        .where(HandlingOrder.status.in_(TODO_ACTIVE_STATUSES))
    )
    overdue = await db.execute(
        select(func.count())
        .select_from(HandlingOrder)
        .where(HandlingOrder.sla_stage == "BREACH")
        .where(HandlingOrder.status.notin_(ORDER_TERMINAL_STATUSES))
    )
    return {"pending": int(pending.scalar() or 0), "overdue": int(overdue.scalar() or 0)}


async def _query_alert_counts(db: AsyncSession, since: datetime) -> dict[str, int]:
    """预警事件：时间窗内 ACTIVE 计数 + 时间窗内未确认（acknowledged_at IS NULL）计数。"""
    active = await db.execute(
        select(func.count())
        .select_from(AlertEvent)
        .where(AlertEvent.status == "ACTIVE", AlertEvent.triggered_at >= since)
    )
    unconfirmed = await db.execute(
        select(func.count())
        .select_from(AlertEvent)
        .where(AlertEvent.acknowledged_at.is_(None), AlertEvent.triggered_at >= since)
    )
    return {"active": int(active.scalar() or 0), "unconfirmed": int(unconfirmed.scalar() or 0)}


async def _query_funnel_counts(db: AsyncSession, start: datetime, end: datetime) -> dict[str, int]:
    """闭环治理漏斗四级计数（时间窗内，口径见各段注释）。

    - discovered: diagnosis_run status='SUCCESS' AND primary_category 非 NULL
      的 DISTINCT loop_id（与 workbench_overview alarm 口径一致）
    - diagnosed: diagnosis_run 完成态（SUCCESS/PARTIAL）finished_at ∈ 窗口
      的 DISTINCT loop_id
    - tuned: tuning_record 已确认方案（APPLIED/VERIFIED）created_at ∈ 窗口计数
    - closed: handling_order CLOSED 且 verified_at ∈ 窗口计数
      （loop_action_item 无 CLOSED 态，处置闭环以工单 CLOSED 为准，
      与 governance-summary closedInWindow 同口径）
    """
    discovered = await db.execute(
        select(func.count(func.distinct(DiagnosisRun.loop_id))).where(
            DiagnosisRun.status == "SUCCESS",
            DiagnosisRun.primary_category.is_not(None),
            DiagnosisRun.created_at >= start,
            DiagnosisRun.created_at <= end,
        )
    )
    diagnosed = await db.execute(
        select(func.count(func.distinct(DiagnosisRun.loop_id))).where(
            DiagnosisRun.status.in_(DIAGNOSIS_DONE_STATUSES),
            DiagnosisRun.finished_at.isnot(None),
            DiagnosisRun.finished_at >= start,
            DiagnosisRun.finished_at <= end,
        )
    )
    tuned = await db.execute(
        select(func.count())
        .select_from(TuningRecord)
        .where(
            TuningRecord.status.in_(TUNING_CONFIRMED_STATUSES),
            TuningRecord.created_at >= start,
            TuningRecord.created_at <= end,
        )
    )
    closed = await db.execute(
        select(func.count())
        .select_from(HandlingOrder)
        .where(
            HandlingOrder.status == "CLOSED",
            HandlingOrder.verified_at.isnot(None),
            HandlingOrder.verified_at >= start,
            HandlingOrder.verified_at <= end,
        )
    )
    return {
        "discovered": int(discovered.scalar() or 0),
        "diagnosed": int(diagnosed.scalar() or 0),
        "tuned": int(tuned.scalar() or 0),
        "closed": int(closed.scalar() or 0),
    }


async def _query_backlog(db: AsyncSession) -> dict[str, int]:
    """处置积压：handling_order 当前 PENDING/EXECUTING/VERIFYING 各态计数。"""
    result = await db.execute(
        select(HandlingOrder.status, func.count())
        .where(HandlingOrder.status.in_(TODO_ACTIVE_STATUSES))
        .group_by(HandlingOrder.status)
    )
    counts = {row[0]: int(row[1]) for row in result.all()}
    return {
        "pending": counts.get("PENDING", 0),
        "inProgress": counts.get("EXECUTING", 0),
        "verifying": counts.get("VERIFYING", 0),
    }


async def _get_backend_roles(db: AsyncSession) -> list[str]:
    """读 sys_config 后台访问角色清单，缺失/为空回退默认。"""
    try:
        result = await db.execute(
            select(SysConfig).where(SysConfig.key == BACKEND_ROLES_CONFIG_KEY)
        )
        row = result.scalar_one_or_none()
        return parse_backend_roles(row.value if row else None)
    except Exception:  # noqa: BLE001 — sys_config 读失败不应阻断驾驶舱
        logger.warning(
            "读取 sys_config %s 失败，回退默认角色", BACKEND_ROLES_CONFIG_KEY, exc_info=True
        )
        return list(DEFAULT_BACKEND_ROLES)


async def _query_loop_counts_per_unit(db: AsyncSession) -> dict[str, int]:
    """loop_ledger 活跃回路按 unit_id 聚合计数（is_active 过滤口径同 loops 列表）。"""
    result = await db.execute(
        select(LoopLedger.unit_id, func.count())
        .where(LoopLedger.is_active.is_(True))
        .where(LoopLedger.unit_id.isnot(None))
        .group_by(LoopLedger.unit_id)
    )
    return {row[0]: int(row[1]) for row in result.all() if row[0]}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


async def build_overview(db: AsyncSession, window: str = "24h") -> dict[str, Any]:
    """组装驾驶舱总览（KPI 指标带 + 闭环治理漏斗）。部分失败容错。"""
    hours = WINDOW_HOURS.get(window, 24)
    now = datetime.now(UTC).replace(tzinfo=None)
    start = now - timedelta(hours=hours)
    prev_start = start - timedelta(hours=hours)

    kpi = _default_kpi()
    funnel = _default_funnel()

    # --- 回路总数：loop_ledger 活跃回路实时计数 ---
    # 语义是"配置回路总数"（与回路配置页对齐），不依赖 KPI 评分链路是否
    # 有数据；预计算行的 loop_count 是"窗口内参评回路数"，两者语义不同，
    # 此前混用导致总数与实际配置对不上（2026-09-04 修复）。
    try:
        active_loops = await db.scalar(
            select(func.count()).select_from(LoopLedger).where(LoopLedger.is_active.is_(True))
        )
        kpi["loopTotal"] = int(active_loops or 0)
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱回路总数查询失败", exc_info=True)

    # --- 全厂窗口 KPI 行：score / autoRate ---
    try:
        row = await _query_global_summary_row(db, window)
        if row is not None:
            # INCONCLUSIVE = 窗口内无参评数据（precalc 写入 0 占位）：
            # 显示 None（前端 "—"）而非误导性的 0.0
            if row.status == "INCONCLUSIVE":
                kpi["score"] = None
                kpi["autoRate"] = None
            else:
                kpi["score"] = _to_float(row.score)
                kpi["autoRate"] = _rate_to_pct(row.auto_mode_rate)
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱 KPI 窗口行查询失败", exc_info=True)

    # --- 环比：当前窗口 vs 上一等长窗口（unit_kpi_summary 加权均值） ---
    try:
        cur_avg = await _query_window_weighted_avg(db, start, now)
        prev_avg = await _query_window_weighted_avg(db, prev_start, start)
        kpi["scoreDelta"] = _delta(cur_avg["score"], prev_avg["score"])
        kpi["autoRateDelta"] = _delta(cur_avg["auto_rate"], prev_avg["auto_rate"])
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱 KPI 环比计算失败", exc_info=True)

    # --- 等级分布 + 劣化回路数（复用 grade-distribution service 口径） ---
    try:
        from app.services.performance import get_grade_distribution

        dist = await get_grade_distribution(db, start=start, end=now)
        kpi["gradeDistribution"] = {k: int(dist.get(k, 0) or 0) for k in GRADE_KEYS}
        kpi["degradedCount"] = (
            kpi["gradeDistribution"]["WARNING"] + kpi["gradeDistribution"]["POOR"]
        )
        try:
            prev_dist = await get_grade_distribution(db, start=prev_start, end=start)
            prev_degraded = int(prev_dist.get("WARNING", 0) or 0) + int(
                prev_dist.get("POOR", 0) or 0
            )
            kpi["degradedDelta"] = kpi["degradedCount"] - prev_degraded
        except Exception:  # noqa: BLE001
            logger.warning("驾驶舱劣化数环比计算失败", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱等级分布查询失败", exc_info=True)

    # --- 处置待办 ---
    try:
        todo = await _query_todo_counts(db)
        kpi["todoPending"] = todo["pending"]
        kpi["todoOverdue"] = todo["overdue"]
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱处置待办统计失败", exc_info=True)

    # --- 预警事件 ---
    try:
        alerts = await _query_alert_counts(db, start)
        kpi["alertActive"] = alerts["active"]
        kpi["alertUnconfirmed"] = alerts["unconfirmed"]
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱预警事件统计失败", exc_info=True)

    # --- 闭环治理漏斗 ---
    try:
        counts = await _query_funnel_counts(db, start, now)
        funnel.update(counts)
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱漏斗计数查询失败", exc_info=True)
    try:
        funnel["backlog"] = await _query_backlog(db)
    except Exception:  # noqa: BLE001
        logger.warning("驾驶舱漏斗积压查询失败", exc_info=True)

    return {"window": window, "kpi": kpi, "funnel": funnel}


async def build_backend_access_roles(db: AsyncSession) -> dict[str, list[str]]:
    """ "管理后台"入口允许角色清单（缺失/为空回退默认）。"""
    return {"roles": await _get_backend_roles(db)}


async def build_node_tree(db: AsyncSession) -> list[dict[str, Any]]:
    """工厂→装置→单元三层树 + 各节点回路计数（loop_ledger 活跃口径向上累加）。"""
    nodes = list((await db.execute(select(PlantNode))).scalars().all())
    loop_counts = await _query_loop_counts_per_unit(db)
    return shape_node_tree(nodes, loop_counts)
