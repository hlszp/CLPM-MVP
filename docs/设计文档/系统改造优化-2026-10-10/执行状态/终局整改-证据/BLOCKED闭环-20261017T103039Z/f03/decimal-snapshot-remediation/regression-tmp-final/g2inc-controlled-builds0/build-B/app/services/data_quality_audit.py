"""位号级数据质量体检（点表口径，2026-09-26）。

替代已删除的 scripts/td_quality_audit.py（原实现读的是已退役的宽表，见
同目录测试 test_layout_selfcheck.py 的结构性守护：app/ 内不得出现该表名）。
与既有 app/services/data_quality_stats.py 的边界（不重复造轮子）：
- data_quality_stats：回路级、数据源 = kpi_snapshot_hourly（INCONCLUSIVE 率等评估口径）；
- 本模块：**位号级**、数据源 = st_point_data_v1（真实点事件），回答四类问题：
    1. 断流位号（窗口内零行）；
    2. 有值但质量码坏（quality_class != QC_GOOD 的占比）；
    3. 覆盖率不足（与等长前一窗口的**事件密度比**比较——点表是 COV 稀疏存点，
       不能用 行数 vs 1Hz×3600 这种宽表口径）；
    4. 缺口被 HELD 填平（复用 SeriesContext 的 held_too_long，R2 新增语义）。

只读：不写库、不执行 DDL；时间字面量一律 ISO-Z（裸字面量会被 TDengine 按会话时区
解释，静默偏移 8 小时——本会话已踩过两次）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tdengine import execute_sql

#: 质量码：1 = Good（见 point_history_repository.QC_GOOD）
QC_GOOD = 1

#: 判定标签
ISSUE_NO_DATA = "no_data"
ISSUE_BAD_QUALITY = "bad_quality"
ISSUE_LOW_DENSITY = "low_density"


def iso_z(dt: datetime) -> str:
    """ISO-Z 字面量（UTC 口径）；naive 视为 UTC。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def previous_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """等长前一窗口（事件密度基线）。"""
    span = end - start
    return start - span, start


async def resolve_audit_points(
    db: AsyncSession, *, loop_id: str | None = None
) -> list[dict[str, Any]]:
    """解析待体检的位号集合：tag_registry（可选按回路绑定过滤）。

    返回 [{"pointId", "tagName", "loopId", "role"}]；loop_id 为空时覆盖全部位号。
    """
    from app.models.loop import LoopTagMapping
    from app.models.tag import TagRegistry

    rows: list[dict[str, Any]] = []
    if loop_id:
        stmt = (
            select(
                TagRegistry.id,
                TagRegistry.tag_name,
                LoopTagMapping.loop_id,
                LoopTagMapping.tag_role,
            )
            .join(LoopTagMapping, LoopTagMapping.tag_id == TagRegistry.id)
            .where(LoopTagMapping.loop_id == loop_id)
        )
        for pid, name, lid, role in (await db.execute(stmt)).all():
            rows.append({"pointId": str(pid), "tagName": name, "loopId": str(lid), "role": role})
        return rows

    stmt = select(TagRegistry.id, TagRegistry.tag_name)
    for pid, name in (await db.execute(stmt)).all():
        rows.append({"pointId": str(pid), "tagName": name, "loopId": None, "role": None})
    return rows


async def fetch_point_stats(
    point_ids: list[str], start: datetime, end: datetime
) -> dict[str, dict[str, Any]]:
    """按 point_id 汇总窗口内点事件（只读；ISO-Z 字面量）。

    返回 {pointId: {"rows", "badRows", "firstTs", "lastTs"}}；缺失的点不在返回里
    （调用方据此判"断流"）。TDengine 支持按 TAGS 列分组的聚合（GROUP BY point_id）。
    """
    if not point_ids:
        return {}
    id_list = ", ".join("'" + p + "'" for p in point_ids)
    sql = (
        "SELECT point_id, COUNT(*) AS n, "
        "SUM(CASE WHEN quality_class IS NULL OR quality_class <> "
        f"{QC_GOOD} THEN 1 ELSE 0 END) AS bad, "
        # TDengine 不支持在分组聚合里对 TIMESTAMP 用 MIN/MAX（会返回空错误信息），
        # 时间列必须用 FIRST/LAST —— 这是本会话踩过的坑（2026-09-26）。
        "FIRST(ts) AS t0, LAST(ts) AS t1 "
        f"FROM {settings.TDENGINE_DB}.st_point_data_v1 "
        f"WHERE ts >= '{iso_z(start)}' AND ts <= '{iso_z(end)}' AND point_id IN ({id_list}) "
        "GROUP BY point_id"
    )
    rows = await execute_sql(sql, raise_on_error=True)
    out: dict[str, dict[str, Any]] = {}
    for r in rows or []:
        pid = str(r.get("point_id") or "")
        if not pid:
            continue
        n = int(r.get("n") or 0)
        # 实测坑（2026-09-26）：TDengine 对"窗口内零行"的分组仍会返回 bad=1
        # （见 SUM(CASE WHEN quality_class IS NULL OR <> 1)），零行时强制归零，
        # 否则"断流位号"会被同时误报成"质量码坏"。
        out[pid] = {
            "rows": n,
            "badRows": int(r.get("bad") or 0) if n else 0,
            "firstTs": r.get("t0"),
            "lastTs": r.get("t1"),
        }
    return out


def classify_point(
    stat: dict[str, Any] | None,
    ref_stat: dict[str, Any] | None,
    *,
    min_density_ratio: float,
) -> tuple[list[str], float | None]:
    """单位号判定：返回 (issues, densityRatio)。"""
    issues: list[str] = []
    if not stat or not stat.get("rows"):
        return [ISSUE_NO_DATA], None

    if stat.get("badRows"):
        issues.append(ISSUE_BAD_QUALITY)

    ref_rows = int((ref_stat or {}).get("rows") or 0)
    density: float | None = None
    if ref_rows >= 2:
        density = round(int(stat["rows"]) / ref_rows, 4)
        if density < min_density_ratio:
            issues.append(ISSUE_LOW_DENSITY)
    elif ref_rows == 0:
        # 基线窗口本身无数据：密度不可比，仅登记为 None（不误判）
        density = None
    return issues, density


async def fetch_held_too_long(
    loop_ids: list[str], start: datetime, end: datetime
) -> dict[str, Any]:
    """复用 SeriesContext：每个回路的 held_too_long 槽数与 PV 覆盖率（只读）。

    这是原宽表工具没有的新信息（R2：保持过久的槽位不再计为观测）。
    单回路失败不影响整体体检（返回 error 字段）。
    """
    from app.core.db import AsyncSessionLocal
    from app.services.data_source.logical_wide_builder import build_logical_wide

    out: dict[str, Any] = {}
    for lid in loop_ids:
        try:
            async with AsyncSessionLocal() as db:
                raw = await build_logical_wide(db, lid, ["pv"], start, end, 60)
            ctx = raw.series_context
            reasons = dict(getattr(ctx, "unknown_reasons", {}) or {})
            cov = ctx.role_coverage.get("pv")
            out[lid] = {
                "heldTooLong": int(reasons.get("held_too_long", 0)),
                "gap": int(reasons.get("gap", 0)),
                "pvCoverage": (round(float(ctx.source_coverage_ratio("pv")), 4) if cov else None),
                "expectedSlots": int(getattr(ctx, "expected_slots", 0) or 0),
            }
        except Exception as exc:  # noqa: BLE001 - 单回路失败不阻塞整体体检
            out[lid] = {"error": str(exc)}
    return out


async def audit_data_quality(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    loop_id: str | None = None,
    min_density_ratio: float = 0.5,
    with_held: bool = True,
) -> dict[str, Any]:
    """位号级数据质量体检（只读）。

    参数
    ----
    start / end：窗口（naive 视为 UTC）
    loop_id：仅体检某条回路；为空则全量位号
    min_density_ratio：事件密度比阈值（相对等长前一窗口）
    with_held：是否复用 SeriesContext 统计 held_too_long（需按回路构建，稍慢）
    """
    points = await resolve_audit_points(db, loop_id=loop_id)
    ids = [p["pointId"] for p in points]
    ref_start, ref_end = previous_window(start, end)
    cur = await fetch_point_stats(ids, start, end)
    ref = await fetch_point_stats(ids, ref_start, ref_end)

    items: list[dict[str, Any]] = []
    for p in points:
        pid = p["pointId"]
        issues, density = classify_point(
            cur.get(pid), ref.get(pid), min_density_ratio=min_density_ratio
        )
        items.append(
            {
                **p,
                "rows": int((cur.get(pid) or {}).get("rows") or 0),
                "badRows": int((cur.get(pid) or {}).get("badRows") or 0),
                "densityRatio": density,
                "issues": issues,
            }
        )

    loop_ids = sorted({i["loopId"] for i in items if i.get("loopId")})
    held = await fetch_held_too_long(loop_ids, start, end) if (with_held and loop_ids) else {}
    # 把回路的 held_too_long 归并到每一位号（同回路共享）
    for it in items:
        h = held.get(it.get("loopId") or "") or {}
        it["heldTooLong"] = int(h.get("heldTooLong") or 0)
        it["pvCoverage"] = h.get("pvCoverage")

    summary = {
        "points": len(items),
        "noData": sum(1 for i in items if ISSUE_NO_DATA in i["issues"]),
        "badQuality": sum(1 for i in items if ISSUE_BAD_QUALITY in i["issues"]),
        "lowDensity": sum(1 for i in items if ISSUE_LOW_DENSITY in i["issues"]),
        "withHeldTooLong": sum(1 for i in items if i.get("heldTooLong")),
    }
    return {
        "window": {
            "start": iso_z(start),
            "end": iso_z(end),
            "reference": {"start": iso_z(ref_start), "end": iso_z(ref_end)},
        },
        "minDensityRatio": min_density_ratio,
        "summary": summary,
        "points": items,
        "loops": [{"loopId": k, **v} for k, v in sorted(held.items())],
    }
