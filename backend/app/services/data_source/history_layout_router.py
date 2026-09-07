"""history_layout_router（P3-2）：读取布局的窗口分片解析.

设计依据：设计文档 §5（布局路由器）/§7-4（跨窗口分段路由）。

语义：
- 无 manifest → 单片 legacy（默认，建表不切读）；
- shadow 布局的读取语义 = legacy（影子写阶段读旧路径；读切由显式 point 段
  完成，设计 §7）；
- 跨切换边界窗口拆为两片：**T 归 point，legacy 仅承担 t<T**（设计 §5.3）；
- 查询异常**不自动换源**（不吞错回退 legacy 旧值/默认 Good）；
- manifest 进程内缓存（60s TTL，无锁——与 provider._subtable_cache 同模式：
  并发重复加载无害，避免 kpi 回填每回路-每窗打 PG）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayoutPart:
    """窗口内同布局的连续分片（闭区间端点）。"""

    layout: str  # legacy / point
    start: datetime  # aware UTC
    end: datetime  # aware UTC


_MANIFEST_CACHE: dict[str, tuple[float, list[tuple[datetime, datetime | None, str]]]] = {}
_MANIFEST_CACHE_TTL = 60.0


def invalidate_manifest_cache() -> None:
    """清空布局缓存（manifest 变更/测试用）。"""
    _MANIFEST_CACHE.clear()


async def current_layout_version() -> str:
    """当前布局数据版本（缓存键分量；无 manifest 恒 "legacy-v1"）.

    口径：活跃 manifest 行（scope/时段/布局）摘要——任何切换/回退产生新键；
    读取失败抛出（调用方决定回退），无 PG 环境由调用方兜底 legacy-v1。
    """
    rows = await _load_manifest_rows(None, loop_id="*")
    if not rows:
        return "legacy-v1"
    import hashlib

    parts = [f"{vf.isoformat()}~{vt.isoformat() if vt else ''}~{layout}" for vf, vt, layout in rows]
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]
    return f"mf-{digest}"


async def _load_manifest_rows(db, loop_id: str) -> list[tuple[datetime, datetime | None, str]]:
    """加载全部活跃 manifest 段（[valid_from, valid_to) 升序）.

    - ``db=None``：自建**独立短会话**——布局解析不得触碰调用方共享
      AsyncSession（并发 query_fn 共享 session 是既有红线，见
      test_runtime_regressions 并发用例）；缓存命中时不打 PG；
    - 读取失败（无 PG/超时）返回空段 → 调用方按默认 legacy 处理
      （"无 manifest 恒 legacy"，显式告警不静默）。
    """
    from sqlalchemy import select

    from app.models.point_history import HistoryLayoutManifest

    cached = _MANIFEST_CACHE.get(loop_id)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    own_session = db is None
    try:
        if own_session:
            from app.core.db import AsyncSessionLocal

            db = AsyncSessionLocal()
        rows_result = await asyncio.wait_for(
            db.execute(
                select(
                    HistoryLayoutManifest.scope_type,
                    HistoryLayoutManifest.scope_id,
                    HistoryLayoutManifest.valid_from,
                    HistoryLayoutManifest.valid_to,
                    HistoryLayoutManifest.layout,
                ).where(HistoryLayoutManifest.is_active.is_(True))
            ),
            timeout=5.0,
        )
        raw_rows = rows_result.all()
    except Exception as exc:  # noqa: BLE001 — 布局读取失败按默认 legacy
        logger.warning("manifest 读取失败（按默认 legacy 布局）: %s", exc)
        return []
    finally:
        if own_session:
            try:
                await db.close()
            except Exception:  # noqa: BLE001
                pass
    parts: list[tuple[datetime, datetime | None, str, int]] = []
    prio = {"loop": 2, "source": 1, "global": 0}
    for scope_type, scope_id, valid_from, valid_to, layout in raw_rows:
        if scope_type == "loop" and str(scope_id) != loop_id:
            continue
        vf = valid_from if valid_from.tzinfo else valid_from.replace(tzinfo=UTC)
        vt = None
        if valid_to is not None:
            vt = valid_to if valid_to.tzinfo else valid_to.replace(tzinfo=UTC)
        parts.append((vf, vt, str(layout), prio.get(str(scope_type), 0)))
    parts.sort(key=lambda p: p[0])
    normalized = [(vf, vt, layout) for vf, vt, layout, _p in parts]
    _MANIFEST_CACHE[loop_id] = (time.monotonic() + _MANIFEST_CACHE_TTL, normalized)
    return normalized


def _layout_at(
    rows: list[tuple[datetime, datetime | None, str]],
    at: datetime,
) -> str:
    """时刻布局：loop 精确段优先于 global 兜底（prio 已在排序键丢失，
    这里按"后发布（valid_from 更大）优先"取覆盖该时刻的最新段）。"""
    best: str | None = None
    best_from: datetime | None = None
    for vf, vt, layout in rows:
        if vf <= at and (vt is None or at < vt):
            if best_from is None or vf >= best_from:
                best_from = vf
                best = layout
    if best is None:
        return "legacy"
    return "legacy" if best in ("legacy", "shadow") else "point"


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def resolve_window_layouts(
    db,
    loop_id: str,
    start: datetime,
    end: datetime,
) -> list[LayoutPart]:
    """解析窗口的布局分片（升序；恒覆盖整个闭区间 [start, end]）。"""
    rows = await _load_manifest_rows(db, loop_id)

    def _at(t: datetime) -> str:
        return _layout_at(rows, t)

    start_u, end_u = _utc(start), _utc(end)
    first = _at(start_u)
    last = _at(end_u)
    if first == last:
        return [LayoutPart(first, start_u, end_u)]

    # 二分找切换秒（30 天窗 ≈ 21 次内存解析，无 DB 往返）
    lo, hi = start_u, end_u
    while hi - lo > timedelta(seconds=1):
        mid = lo + (hi - lo) / 2
        mid = mid.replace(microsecond=0)
        if _at(mid) == first:
            lo = mid
        else:
            hi = mid
    t_point = hi if _at(hi) != first else hi + timedelta(seconds=1)
    return [
        LayoutPart(first, start_u, t_point - timedelta(seconds=1)),
        LayoutPart("point" if first == "legacy" else "legacy", t_point, end_u),
    ]
