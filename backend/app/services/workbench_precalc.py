"""工作台三窗口 KPI 预计算 service（workbench-precalc M2）.

数据架构（对齐 cockpit_overview）：不直接查 TDengine；以 ``unit_kpi_summary``
（装置级小时汇总，由 KPI 聚合链路实时写入）为唯一数据源，按
GLOBAL/FACTORY/AREA/UNIT × 24h/7d/30d 加权聚合，upsert 到
``workbench_window_summary`` 供驾驶舱/工作台快速渲染。

口径要点：
- 加权：按 ``evaluated_loops``，每指标独立「非 NULL 分母」（同 cockpit
  环比口径），避免某指标 NULL 拉低整体
- 率值字段存 0-1 小数：消费方统一按此口径（workbench_overview
  lose_factors 阈值 0.90 / shape_heatmap v*100 / cockpit _rate_to_pct）
- score_trend：窗口内按小时（24h）/ 日（7d、30d）桶聚合的加权均值序列
- distribution.metric_slopes：当前窗口 vs 上一等长窗口的 6 指标差值
- distribution.level_dist：scope 子树回路最新快照等级分桶（复用
  get_grade_distribution，含 sys_config 动态阈值，Global=全厂）
- flags / mode_dist / data_quality：暂无真实检测口径，置空数组（不造数据）
- window_end 对齐 5min 网格（beat 周期），同网格内重跑幂等覆盖；
  每 (scope × window) 保留最近 ``_RETAIN_ROWS`` 行防止表膨胀
- 窗口起点用参数化 naive UTC（PG 会话时区 +8，勿裸用 now()）
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plant_node import PlantNode
from app.models.unit_kpi_summary import UnitKpiSummary
from app.models.workbench_summary import WorkbenchWindowSummary

logger = logging.getLogger(__name__)

# 窗口 → 小时数（与模型 WINDOWS 对齐）
WINDOW_HOURS: dict[str, int] = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}
WINDOWS: tuple[str, ...] = ("24h", "7d", "30d")

# score_trend 桶大小（小时）：24h 逐小时 24 点 / 7d、30d 逐日
_TREND_BUCKET_HOURS: dict[str, int] = {"24h": 1, "7d": 24, "30d": 48}

# unit_kpi_summary（0-100）→ workbench 率值列（0-1）映射；
# stability_rate → steady_rate 列名差异在此归一
_RATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("auto_mode_rate", "auto_mode_rate"),
    ("effective_auto_rate", "effective_auto_rate"),
    ("stability_rate", "steady_rate"),
    ("accuracy_rate", "accuracy_rate"),
    ("fast_rate", "fast_rate"),
    ("good_value_rate", "good_value_rate"),
    ("oscillation_rate", "oscillation_rate"),
    ("saturation_rate", "saturation_rate"),
    ("instrument_fault_rate", "instrument_fault_rate"),
)

# metric_slopes 展示的 6 指标（对齐 workbench_assessment EVAL_METRICS 语义）
_SLOPE_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("effective_auto_rate", "有效自控", False),
    ("steady_rate", "平稳率", False),
    ("accuracy_rate", "准确率", False),
    ("fast_rate", "快速率", False),
    ("good_value_rate", "好值率", False),
    ("instrument_fault_rate", "仪表故障率", True),  # 反向：升高为恶化
)

# 等级分桶展示（label/颜色对齐 seed/原型口径；计数来自真实分布）
_LEVEL_BUCKETS: tuple[tuple[str, str, float], ...] = (
    ("优（≥90）", "#2E7D32", 90),
    ("良（75–90）", "#7CB342", 75),
    ("中（60–75）", "#F59E0B", 60),
    ("差（<60）", "#D93025", 0),
)

# 率值小数位（0-1 标度保留 4 位，避免 0.9123 精度损失）
_RATE_NDIGITS = 4
# 每行数 / 防膨胀保留行数
_RETAIN_ROWS = 64
# 网格对齐（秒）——与 beat 周期一致
_GRID_SECONDS = 300


# ---------------------------------------------------------------------------
# 纯函数（单测友好）
# ---------------------------------------------------------------------------


def floor_grid(dt: datetime) -> datetime:
    """对齐到 5min 网格（beat 周期），保证同网格内 upsert 幂等覆盖。

    入参/出参均为 naive UTC（DB 存储口径）；naive 时间必须先挂 UTC 再取
    epoch，否则会受运行机器本地时区影响。
    """
    epoch = int(dt.replace(tzinfo=UTC).timestamp())
    return datetime.fromtimestamp(epoch - epoch % _GRID_SECONDS, tz=UTC).replace(tzinfo=None)


def score_to_status(score: float | None) -> str:
    """综合评分 → workbench 六档 status（对齐原型优/良/中/差阈值）。"""
    if score is None:
        return "INCONCLUSIVE"
    if score >= 90:
        return "EXCELLENT"
    if score >= 75:
        return "GOOD"
    if score >= 60:
        return "FAIR"
    if score >= 40:
        return "POOR"
    return "CRITICAL"


def _to_f(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _round_rate(val: Any) -> float | None:
    """0-100 标度 → 0-1 小数（4 位），None 透传。"""
    v = _to_f(val)
    if v is None:
        return None
    return round(v / 100, _RATE_NDIGITS)


class _RowLike:
    """聚合入参的最小接口（UnitKpiSummary 或测试替身）。"""

    avg_score: Any
    evaluated_loops: Any
    snapshot_time: Any
    auto_mode_rate: Any
    effective_auto_rate: Any
    stability_rate: Any
    accuracy_rate: Any
    fast_rate: Any
    good_value_rate: Any
    oscillation_rate: Any
    saturation_rate: Any
    instrument_fault_rate: Any


def aggregate_rows(rows: Sequence[_RowLike]) -> dict[str, Any]:
    """窗口内 UnitKpiSummary 行 → 加权聚合（每指标独立非 NULL 分母）。

    返回 {score, loop_count, rates: {rate_field: 0-1|None}}；
    无有效行时 score/loop_count 为 0 且 rates 全 None（status 判 INCONCLUSIVE）。
    """
    score_vals = [
        (_to_f(r.avg_score), int(r.evaluated_loops or 0))
        for r in rows
        if _to_f(r.avg_score) is not None
    ]
    score_den = sum(w for _s, w in score_vals if w > 0)
    score = round(sum(s * w for s, w in score_vals) / score_den, 3) if score_den > 0 else None

    rates: dict[str, float | None] = {}
    for src, dst in _RATE_FIELDS:
        pairs = [
            (_to_f(getattr(r, src)), int(r.evaluated_loops or 0))
            for r in rows
            if _to_f(getattr(r, src)) is not None
        ]
        den = sum(w for _v, w in pairs if w > 0)
        avg = sum(v * w for v, w in pairs) / den if den > 0 else None
        rates[dst] = _round_rate(avg) if avg is not None else None

    return {
        "score": score,
        "loop_count": score_den,  # 参评回路数（有评分的加权分母）
        "rates": rates,
    }


def build_trend_points(
    rows: Sequence[_RowLike],
    window: str,
) -> list[dict[str, Any]]:
    """窗口内快照按时间桶聚合 → score_trend [{t, v}]。

    桶大小：24h 逐小时 / 7d、30d 逐日（对齐模型注释 24pts/7pts/15pts 量级）；
    空桶跳过（诚实序列，不插值）。
    """
    bucket_hours = _TREND_BUCKET_HOURS[window]
    bucket_seconds = bucket_hours * 3600
    buckets: dict[datetime, list[_RowLike]] = {}
    for r in rows:
        if _to_f(r.avg_score) is None:
            continue
        # snapshot_time 为 naive UTC（DB 口径），显式挂 UTC 取 epoch，
        # 桶 key 统一还原为 naive UTC
        epoch = int(r.snapshot_time.replace(tzinfo=UTC).timestamp())
        bucket_epoch = epoch - epoch % bucket_seconds
        key = datetime.fromtimestamp(bucket_epoch, tz=UTC).replace(tzinfo=None)
        buckets.setdefault(key, []).append(r)
    points: list[dict[str, Any]] = []
    for key in sorted(buckets):
        agg = aggregate_rows(buckets[key])
        if agg["score"] is not None:
            points.append({"t": key.isoformat(), "v": agg["score"]})
    return points


def build_metric_slopes(cur: dict[str, Any], prev: dict[str, Any]) -> list[dict[str, Any]]:
    """当前窗口 vs 上一等长窗口的 6 指标差值（百分点，0-100 口径）。"""
    slopes: list[dict[str, Any]] = []
    for field, label, reverse in _SLOPE_METRICS:
        c = cur["rates"].get(field)
        p = prev["rates"].get(field)
        if c is None or p is None:
            continue
        delta = round((c - p) * 100, 1)  # 0-1 → 百分点
        good = (delta < 0) if reverse else (delta > 0)
        slopes.append({"metric": label, "delta": delta, "direction": "good" if good else "bad"})
    # 改善居下绿、恶化居上红（对齐原型 slope-row 排序）
    slopes.sort(key=lambda s: 0 if s["direction"] == "bad" else 1)
    return slopes


def shape_level_dist(dist: dict[str, int] | None) -> list[dict[str, Any]]:
    """get_grade_distribution 结果 → 原型甜甜圈五档（优/良/中/差/不可评）。"""
    dist = dist or {}
    out: list[dict[str, Any]] = []
    for label, color, threshold in _LEVEL_BUCKETS:
        if threshold >= 90:
            count = int(dist.get("EXCELLENT", 0) or 0)
        elif threshold >= 75:
            count = int(dist.get("GOOD", 0) or 0)
        elif threshold >= 60:
            count = int(dist.get("FAIR", 0) or 0)
        else:
            count = int(dist.get("WARNING", 0) or 0) + int(dist.get("POOR", 0) or 0)
        out.append({"label": label, "count": count, "color": color, "stripe": False})
    inconclusive = int(dist.get("INCONCLUSIVE", 0) or 0)
    out.append({"label": "不可评", "count": inconclusive, "color": "#C9D6E8", "stripe": True})
    return out


# ---------------------------------------------------------------------------
# async 主流程
# ---------------------------------------------------------------------------


async def _load_scope_map(db: AsyncSession) -> dict[str, Any]:
    """载工厂树，返回 {scope_key: (scope_id, unit_node_ids)}。

    - GLOBAL → scope_id=0，全部 UNIT
    - FACTORY/AREA → 自身 source_node_id + 子树 UNIT
    - UNIT → 自身 source_node_id + 仅自身
    跳过 source_node_id 为 NULL 的节点（无法对齐预计算表 scope_id）。
    """
    nodes = list(
        (await db.execute(select(PlantNode).order_by(PlantNode.sort_order))).scalars().all()
    )
    children: dict[str | None, list[Any]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n)

    def descendants_of(node: Any) -> list[Any]:
        out: list[Any] = []
        for child in children.get(node.id, []):
            out.append(child)
            out.extend(descendants_of(child))
        return out

    scopes: dict[str, tuple[int, list[str]]] = {}
    units_all = [n for n in nodes if n.type == "UNIT"]
    scopes["GLOBAL:0"] = (0, [n.id for n in units_all])
    for n in nodes:
        if n.type not in ("FACTORY", "AREA", "UNIT") or n.source_node_id is None:
            continue
        if n.type == "UNIT":
            unit_ids = [n.id]
        else:
            unit_ids = [d.id for d in descendants_of(n) if d.type == "UNIT"]
        scopes[f"{n.type}:{n.source_node_id}"] = (int(n.source_node_id), unit_ids)
    return scopes


async def _query_window_rows(
    db: AsyncSession, unit_node_ids: list[str], start: datetime, end: datetime
) -> list[UnitKpiSummary]:
    """窗口内指定 UNIT 集合的装置级快照行。"""
    if not unit_node_ids:
        return []
    result = await db.execute(
        select(UnitKpiSummary)
        .where(UnitKpiSummary.node_id.in_(unit_node_ids))
        .where(UnitKpiSummary.snapshot_time >= start)
        .where(UnitKpiSummary.snapshot_time < end)
    )
    return list(result.scalars().all())


async def _upsert_row(
    db: AsyncSession,
    *,
    scope_type: str,
    scope_id: int,
    window: str,
    window_start: datetime,
    window_end: datetime,
    agg: dict[str, Any],
    trend: list[dict[str, Any]],
    distribution: dict[str, Any],
) -> bool:
    """单行 upsert（ON CONFLICT (scope,window,window_end) DO UPDATE）。"""
    score = agg["score"] if agg["score"] is not None else 0.0
    stmt = (
        pg_insert(WorkbenchWindowSummary)
        .values(
            scope_type=scope_type,
            scope_id=scope_id,
            window_w=window,
            window_start=window_start,
            window_end=window_end,
            score=score,
            status=score_to_status(agg["score"]),
            loop_count=agg["loop_count"],
            **agg["rates"],
            score_trend=trend,
            flags=[],
            distribution=distribution,
            snapshot_at=func.now(),
        )
        .on_conflict_do_update(
            constraint="uniq_ws_scope_window_end",
            set_={
                "window_start": window_start,
                "score": score,
                "status": score_to_status(agg["score"]),
                "loop_count": agg["loop_count"],
                **agg["rates"],
                "score_trend": trend,
                "flags": [],
                "distribution": distribution,
                "snapshot_at": func.now(),
            },
        )
    )
    await db.execute(stmt)
    return True


async def _prune_rows(db: AsyncSession) -> int:
    """每 (scope × window) 仅保留最新 _RETAIN_ROWS 行，返回删除行数。"""
    rn = (
        func.row_number()
        .over(
            partition_by=(
                WorkbenchWindowSummary.scope_type,
                WorkbenchWindowSummary.scope_id,
                WorkbenchWindowSummary.window_w,
            ),
            order_by=WorkbenchWindowSummary.window_end.desc(),
        )
        .label("rn")
    )
    subq = select(WorkbenchWindowSummary.id.label("id"), rn).subquery()
    stmt = delete(WorkbenchWindowSummary).where(
        WorkbenchWindowSummary.id.in_(select(subq.c.id).where(subq.c.rn > _RETAIN_ROWS))
    )
    result = await db.execute(stmt)
    return int(result.rowcount or 0)


async def build_and_upsert(db: AsyncSession) -> dict[str, Any]:
    """三窗口 × 各 scope 聚合并 upsert。返回统计信息。"""
    from app.services.performance import get_grade_distribution

    now = datetime.now(UTC).replace(tzinfo=None)
    window_end = floor_grid(now).replace(tzinfo=None)

    scopes = await _load_scope_map(db)

    written = 0
    errors = 0
    for window in WINDOWS:
        hours = WINDOW_HOURS[window]
        w_start = window_end - timedelta(hours=hours)
        p_start = w_start - timedelta(hours=hours)
        for scope_key, (scope_id, unit_ids) in scopes.items():
            scope_type = scope_key.split(":", 1)[0]
            try:
                rows = await _query_window_rows(db, unit_ids, w_start, window_end)
                agg = aggregate_rows(rows)
                trend = build_trend_points(rows, window)
                prev_rows = await _query_window_rows(db, unit_ids, p_start, w_start)
                prev_agg = aggregate_rows(prev_rows)

                level_dist: list[dict[str, Any]] = []
                try:
                    grade = await get_grade_distribution(
                        db, plant_node_ids=unit_ids if scope_key != "GLOBAL:0" else None
                    )
                    level_dist = shape_level_dist(grade)
                except Exception:  # noqa: BLE001 — 等级分布失败不阻断该行写入
                    logger.warning("precalc level_dist 失败 scope=%s", scope_key, exc_info=True)

                distribution = {
                    "level_dist": level_dist,
                    "mode_dist": [],
                    "data_quality": [],
                    "metric_slopes": build_metric_slopes(agg, prev_agg),
                }
                await _upsert_row(
                    db,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    window=window,
                    window_start=w_start,
                    window_end=window_end,
                    agg=agg,
                    trend=trend,
                    distribution=distribution,
                )
                written += 1
            except Exception:  # noqa: BLE001 — 单 scope 失败不阻断其余
                errors += 1
                logger.warning("precalc scope=%s window=%s 失败", scope_key, window, exc_info=True)

    pruned = await _prune_rows(db)
    await db.commit()
    logger.info(
        "workbench_precalc: 写入 %d 行（%d 错误），清理 %d 行",
        written,
        errors,
        pruned,
    )
    return {
        "status": "ok",
        "written": written,
        "errors": errors,
        "pruned": pruned,
    }
