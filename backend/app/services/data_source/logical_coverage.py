"""逻辑时间覆盖计算（P3-3）：完整性/门禁按覆盖时长而非事件行数.

设计依据：设计文档 §6.3——"内部改为逻辑时间覆盖，而非按测点事件行数÷3600。
正常 COV 的无变化时段可以完整；真实未知时段不能因填网格变完整"。

口径：
- 分母 = 窗口时长（闭区间按秒数+1，与 TimeWindow 语义一致）；
- 分子 = 该回路**当前绑定**各点 confirmed 覆盖段与窗口交集的并集时长
  （会话段 + 点级段合并计算；gap 段不算覆盖）；
- 任一必需角色（PV）无覆盖 → 该秒不计入完整（角色并集口径，v1 取 PV
  为主判据——与既有完整性检查"PV 列填充率"关注一致）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def loop_window_coverage_ratio(
    loop_id: str,
    start: datetime,
    end: datetime,
    *,
    db=None,
) -> float:
    """回路窗口的逻辑覆盖占比 [0,1]（PV 点 confirmed 覆盖 / 窗口时长）."""
    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopTagMapping
    from app.models.point_history import HistoryCoverageSegment

    start_u, end_u = _utc(start), _utc(end)
    if end_u <= start_u:
        return 0.0

    own_session = db is None
    if own_session:
        db = AsyncSessionLocal()
    try:
        pv_map = (
            (
                await db.execute(
                    select(LoopTagMapping.tag_id).where(
                        LoopTagMapping.loop_id == loop_id,
                        LoopTagMapping.tag_role == "PV",
                    )
                )
            )
            .scalars()
            .all()
        )
        if not pv_map:
            return 0.0
        pids = [str(p) for p in pv_map]
        rows = (
            await db.execute(
                select(
                    HistoryCoverageSegment.seg_start,
                    HistoryCoverageSegment.seg_end,
                ).where(
                    HistoryCoverageSegment.point_id.in_(pids),
                    HistoryCoverageSegment.status == "confirmed",
                    HistoryCoverageSegment.seg_start <= end_u,
                )
            )
        ).all()
    finally:
        if own_session:
            await db.close()

    # 区间与窗口求交 → 并集时长
    clipped = [
        (max(_utc(r[0]), start_u), min(_utc(r[1]), end_u))
        for r in rows
        if _utc(r[1]) > start_u and _utc(r[0]) < end_u
    ]
    clipped.sort()
    covered = 0.0
    cur_s: datetime | None = None
    cur_e: datetime | None = None
    for s, e in clipped:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                covered += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        covered += (cur_e - cur_s).total_seconds()
    window_s = (end_u - start_u).total_seconds() + 1.0  # 闭区间含两端
    return min(covered / window_s, 1.0)
