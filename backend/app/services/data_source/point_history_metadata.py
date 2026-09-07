"""点历史 PG 元数据管理（P1-2/P1-3）.

设计依据：设计文档 §4.2/§4.3。所有函数接受调用方传入的 AsyncSession
（绑定变更与 loop_tag_mapping 同事务的契约由此保证——调用方在同一个
session/transaction 里先改 mapping 再调 :func:`record_binding_change`）。

崩溃恢复顺序（设计 §4.3，P1-3 契约）：
1. TD 写入成功但 PG 批次未确认 → 重放该批次（幂等：同 payload skip）；
2. PG 批次 confirmed 但 TD 缺行（响应丢失类）→ 以 TD 事实为准复核
   （``reconcile_batch`` 读回比对，缺行部分把批次退回 partial）；
3. pending 批次窗口不构成覆盖（builder 不读作已知）。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update

from app.models.point_history import (
    HistoryCoverageSegment,
    HistoryLayoutManifest,
    HistoryPointConflict,
    HistoryWriteBatch,
    LoopTagBindingHistory,
    PointStateAnchor,
)

logger = logging.getLogger(__name__)

UNBIND_SENTINEL_TAG_ID = "00000000-0000-0000-0000-000000000000"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# P1-2 绑定历史
# ---------------------------------------------------------------------------


async def record_binding_change(
    session: Any,
    *,
    loop_id: str,
    tag_role: str,
    new_tag_id: str | None,
    effective_at: datetime,
    basis: str = "REBIND",
) -> LoopTagBindingHistory | None:
    """登记一次绑定变更：闭合旧区间 + 开新区间（与 mapping 变更同事务调用）.

    - new_tag_id=None → 解绑（UNBIND，闭合旧区间不开新区间）；
    - 首次建史（无旧区间）由 ``initialize_binding_history`` 批量完成，
      不走本函数（不把未知过去追溯成当前绑定）。
    """
    eff = _aware(effective_at)
    # 闭合该 (loop, role) 当前开放区间（防倒挂：起点晚于生效时刻的开放段
    # 说明登记乱序/残留——跳过并告警，不让 valid_to < valid_from 违约）
    open_rows = (
        (
            await session.execute(
                select(LoopTagBindingHistory)
                .where(
                    LoopTagBindingHistory.loop_id == loop_id,
                    LoopTagBindingHistory.tag_role == tag_role,
                    LoopTagBindingHistory.valid_to.is_(None),
                )
                .order_by(LoopTagBindingHistory.valid_from.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in open_rows:
        if _aware(row.valid_from) < eff:
            row.valid_to = eff
        else:
            logger.warning(
                "绑定历史登记乱序（%s/%s 起点 %s 晚于生效 %s，跳过闭合）",
                loop_id,
                tag_role,
                row.valid_from,
                eff,
            )
    if new_tag_id is None:
        return None
    version = 1 + (
        (
            await session.execute(
                select(func.max(LoopTagBindingHistory.mapping_version)).where(
                    LoopTagBindingHistory.loop_id == loop_id,
                    LoopTagBindingHistory.tag_role == tag_role,
                )
            )
        ).scalar_one()
        or 0
    )
    entry = LoopTagBindingHistory(
        loop_id=loop_id,
        tag_role=tag_role,
        tag_id=new_tag_id,
        valid_from=eff,
        valid_to=None,
        mapping_version=version,
        basis=basis,
    )
    session.add(entry)
    await session.flush()
    return entry


async def initialize_binding_history(
    session: Any,
    *,
    effective_at: datetime,
    basis: str = "MVP_INIT",
) -> int:
    """从当前 loop_tag_mapping 建立绑定历史基线（只覆盖可信当前时刻，T05）.

    幂等：已存在该 (loop, role) 开放区间且 tag 相同 → 跳过；
    不同（mapping 已漂移但未走 record_binding_change）→ 视为改绑登记
    （闭合旧区间开新区间），保证历史不与现状矛盾。
    """
    from app.models.loop import LoopTagMapping

    eff = _aware(effective_at)
    mappings = (await session.execute(select(LoopTagMapping))).scalars().all()
    created = 0
    for m in mappings:
        open_row = (
            (
                await session.execute(
                    select(LoopTagBindingHistory)
                    .where(
                        LoopTagBindingHistory.loop_id == str(m.loop_id),
                        LoopTagBindingHistory.tag_role == m.tag_role,
                        LoopTagBindingHistory.valid_to.is_(None),
                    )
                    .order_by(LoopTagBindingHistory.valid_from.desc())
                )
            )
            .scalars()
            .first()
        )
        if open_row is not None and str(open_row.tag_id) == str(m.tag_id):
            continue
        if open_row is not None:
            if _aware(open_row.valid_from) < eff:
                open_row.valid_to = eff
            else:
                continue  # 起点晚于基线时刻的段保持不动（乱序保护）
        version = 1 + (
            (
                await session.execute(
                    select(func.max(LoopTagBindingHistory.mapping_version)).where(
                        LoopTagBindingHistory.loop_id == str(m.loop_id),
                        LoopTagBindingHistory.tag_role == m.tag_role,
                    )
                )
            ).scalar_one()
            or 0
        )
        session.add(
            LoopTagBindingHistory(
                loop_id=str(m.loop_id),
                tag_role=m.tag_role,
                tag_id=str(m.tag_id),
                valid_from=eff,
                valid_to=None,
                mapping_version=version,
                basis=basis if open_row is None else "REBIND",
            )
        )
        created += 1
    await session.flush()
    return created


async def resolve_bindings(
    session: Any,
    loop_id: str,
    at_times: list[datetime],
) -> dict[datetime, dict[str, str | None]]:
    """按时刻解析回路角色 → 当时绑定的 tag_id（历史分段读取用）.

    时刻早于任何区间起点 → 该角色 None（未知过去不追溯）。
    区间右端半开：valid_to = t 时该绑定已失效。
    """
    rows = (
        (
            await session.execute(
                select(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id == loop_id)
            )
        )
        .scalars()
        .all()
    )
    out: dict[datetime, dict[str, str | None]] = {}
    for t in at_times:
        tt = _aware(t)
        per_role: dict[str, str | None] = {}
        for row in rows:
            vfrom = _aware(row.valid_from)
            vto = _aware(row.valid_to) if row.valid_to is not None else None
            if vfrom <= tt and (vto is None or tt < vto):
                per_role[row.tag_role] = str(row.tag_id)
        out[t] = per_role
    return out


# ---------------------------------------------------------------------------
# P1-3 批次 / 覆盖 / 冲突 / 锚点 / 布局
# ---------------------------------------------------------------------------


async def create_batch(
    session: Any,
    *,
    window_start: datetime,
    window_end: datetime,
    source_task: str | None = None,
    input_digest: str | None = None,
) -> HistoryWriteBatch:
    batch = HistoryWriteBatch(
        source_task=source_task,
        window_start=_aware(window_start),
        window_end=_aware(window_end),
        input_digest=input_digest,
        status="pending",
    )
    session.add(batch)
    await session.flush()
    return batch


async def confirm_batch(
    session: Any,
    batch_id: str,
    *,
    stats: dict[str, Any] | None = None,
    partial_reason: str | None = None,
) -> None:
    """TD 确认后推进批次终态（confirmed/partial，设计 §4.3）."""
    status = "partial" if partial_reason else "confirmed"
    await session.execute(
        update(HistoryWriteBatch)
        .where(HistoryWriteBatch.batch_id == batch_id)
        .values(
            status=status,
            stats=stats or {},
            failure_reason=partial_reason,
        )
    )
    # 该批次挂起的覆盖段转 confirmed
    await session.execute(
        update(HistoryCoverageSegment)
        .where(HistoryCoverageSegment.batch_id == batch_id)
        .values(status="confirmed")
    )


async def register_coverage(
    session: Any,
    *,
    seg_start: datetime,
    seg_end: datetime,
    point_id: str | None = None,
    session_id: str | None = None,
    batch_id: str | None = None,
    binding_version: str | None = None,
    source_task: str | None = None,
) -> HistoryCoverageSegment:
    """登记一段覆盖（pending；TD 确认后由 confirm_batch 转 confirmed）."""
    seg = HistoryCoverageSegment(
        point_id=point_id,
        session_id=session_id,
        seg_start=_aware(seg_start),
        seg_end=_aware(seg_end),
        status="pending",
        batch_id=batch_id,
        binding_version=binding_version,
        source_task=source_task,
    )
    session.add(seg)
    await session.flush()
    return seg


async def merge_confirmed_coverage(
    session: Any,
    *,
    point_id: str | None = None,
    session_id: str | None = None,
) -> int:
    """合并相邻/重叠的 confirmed 覆盖段（元数据生命周期，设计 §4.2）.

    相邻（a.end == b.start）或重叠的段合并为一段；返回删除的段数。
    不跨越真实 gap（不相邻即保留段间空隙）。
    """
    cond = [HistoryCoverageSegment.status == "confirmed"]
    if point_id is not None:
        cond.append(HistoryCoverageSegment.point_id == point_id)
    else:
        cond.append(HistoryCoverageSegment.point_id.is_(None))
    if session_id is not None:
        cond.append(HistoryCoverageSegment.session_id == session_id)
    else:
        cond.append(HistoryCoverageSegment.session_id.is_(None))
    rows = (
        (
            await session.execute(
                select(HistoryCoverageSegment)
                .where(*cond)
                .order_by(HistoryCoverageSegment.seg_start)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) < 2:
        return 0
    removed = 0
    current = rows[0]
    for nxt in rows[1:]:
        if _aware(nxt.seg_start) <= _aware(current.seg_end):
            current.seg_end = max(_aware(current.seg_end), _aware(nxt.seg_end))
            await session.delete(nxt)
            removed += 1
    await session.flush()
    return removed


async def known_coverage(
    session: Any,
    *,
    point_id: str | None = None,
    session_id: str | None = None,
) -> list[tuple[datetime, datetime]]:
    """confirmed 覆盖区间列表（builder 判"可保持时段"的依据之一）."""
    cond = [HistoryCoverageSegment.status == "confirmed"]
    if point_id is not None:
        cond.append(HistoryCoverageSegment.point_id == point_id)
    else:
        cond.append(HistoryCoverageSegment.point_id.is_(None))
    if session_id is not None:
        cond.append(HistoryCoverageSegment.session_id == session_id)
    else:
        cond.append(HistoryCoverageSegment.session_id.is_(None))
    rows = (
        (
            await session.execute(
                select(HistoryCoverageSegment)
                .where(*cond)
                .order_by(HistoryCoverageSegment.seg_start)
            )
        )
        .scalars()
        .all()
    )
    return [(_aware(r.seg_start), _aware(r.seg_end)) for r in rows]


async def register_conflicts(
    session: Any,
    conflicts: list[dict[str, Any]],
    *,
    source_task: str | None = None,
) -> int:
    """登记同 ts 不同 payload 冲突（默认保留既有事实）。"""
    added = 0
    for c in conflicts:
        existing = c.get("existing") or {}
        incoming = c.get("incoming") or {}
        row = HistoryPointConflict(
            point_id=c["point_id"],
            ts_ms=c["ts_ms"],
            existing_hash=str(existing.get("payload_hash") or ""),
            existing_payload={
                "value": existing.get("value"),
                "quality_class": existing.get("quality_class"),
                "quality_raw": existing.get("quality_raw"),
            },
            new_payload={
                "value": incoming.get("value"),
                "quality_class": incoming.get("quality_class"),
                "quality_raw": incoming.get("quality_raw"),
            },
            source_task=source_task,
            status="resolved_skip" if c.get("resolution") == "skip" else "pending",
        )
        session.add(row)
        added += 1
    await session.flush()
    return added


async def upsert_anchor(
    session: Any,
    *,
    point_id: str,
    anchor_ts: datetime,
    value: float | None,
    quality_class: int,
    quality_raw: int | None = None,
    quality_schema: int | None = None,
    coverage_until: datetime | None = None,
    data_version: str = "v1",
) -> PointStateAnchor:
    """写入/更新状态锚点（携带原 sourceTime，不伪造变化事件，设计 §4.2）."""
    anchor_ts = _aware(anchor_ts)
    row = (
        (
            await session.execute(
                select(PointStateAnchor).where(
                    PointStateAnchor.point_id == point_id,
                    PointStateAnchor.anchor_ts == anchor_ts,
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        row = PointStateAnchor(
            point_id=point_id,
            anchor_ts=anchor_ts,
            value=value,
            quality_class=quality_class,
            quality_raw=quality_raw,
            quality_schema=quality_schema,
            coverage_until=_aware(coverage_until) if coverage_until else None,
            data_version=data_version,
        )
        session.add(row)
    else:
        row.value = value
        row.quality_class = quality_class
        row.quality_raw = quality_raw
        row.quality_schema = quality_schema
        row.coverage_until = _aware(coverage_until) if coverage_until else None
        row.data_version = data_version
    await session.flush()
    return row


async def applicable_anchors_batch(
    session: Any,
    *,
    point_ids: list[str],
    window_start: datetime,
) -> dict[str, Any]:
    """批量取各点窗口起点适用的最新锚点（单查询；builder 热路径用）.

    语义与 :func:`applicable_anchor` 一致：anchor_ts ≤ 窗口起点且未被
    coverage_until 排除；同点多锚点取最新。
    """
    if not point_ids:
        return {}
    ws = _aware(window_start)
    rows = (
        (
            await session.execute(
                select(PointStateAnchor)
                .where(PointStateAnchor.point_id.in_(point_ids))
                .order_by(PointStateAnchor.point_id, PointStateAnchor.anchor_ts.desc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, Any] = {}
    seen: set[str] = set()
    for row in rows:  # 每点按 anchor_ts 降序，首个适用即该点最新锚点
        pid = str(row.point_id)
        if pid in seen:
            continue
        at = _aware(row.anchor_ts)
        if at <= ws and (row.coverage_until is None or _aware(row.coverage_until) > ws):
            out[pid] = row
        seen.add(pid)
    return out


async def applicable_anchor(
    session: Any,
    *,
    point_id: str,
    window_start: datetime,
) -> PointStateAnchor | None:
    """窗口起点适用的最新锚点（anchor_ts ≤ 窗口起点，且未被 coverage_until 排除）."""
    ws = _aware(window_start)
    rows = (
        (
            await session.execute(
                select(PointStateAnchor)
                .where(PointStateAnchor.point_id == point_id)
                .order_by(PointStateAnchor.anchor_ts.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        at = _aware(row.anchor_ts)
        if at <= ws and (row.coverage_until is None or _aware(row.coverage_until) > ws):
            return row
    return None


# ---------------------------------------------------------------------------
# 布局 manifest（P1-5；路由消费在 P3）
# ---------------------------------------------------------------------------


async def set_layout(
    session: Any,
    *,
    layout: str,
    valid_from: datetime,
    valid_to: datetime | None = None,
    scope_type: str = "global",
    scope_id: str | None = None,
    basis: str | None = None,
    data_version: str = "v1",
) -> HistoryLayoutManifest:
    """登记布局区间（global 为兜底，loop/source 精确覆盖；半开区间）.

    P3-4：布局变更即失效布局路由器缓存 + 全量计算缓存（L1/L2/L3）——
    布局切换是一次性迁移操作，全量失效代价可接受且语义安全（旧布局缓存行
    不得命中新布局读取，设计 §5.5）。
    """
    entry = HistoryLayoutManifest(
        scope_type=scope_type,
        scope_id=scope_id,
        valid_from=_aware(valid_from),
        valid_to=_aware(valid_to) if valid_to else None,
        layout=layout,
        data_version=data_version,
        basis=basis,
    )
    session.add(entry)
    await session.flush()
    invalidate_layout_caches()
    return entry


def invalidate_layout_caches() -> None:
    """布局缓存失效：路由器进程内 manifest 缓存 + 全量计算缓存（best-effort）."""
    from app.services.data_source.history_layout_router import invalidate_manifest_cache

    invalidate_manifest_cache()

    async def _invalidate_computation_caches() -> None:
        from app.core.redis import redis_client
        from app.services.cache.invalidation import CacheInvalidator

        await CacheInvalidator(redis_client).invalidate_all()

    try:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_invalidate_computation_caches())
        else:
            asyncio.run(_invalidate_computation_caches())
    except Exception as exc:  # noqa: BLE001 — 失效失败显式告警（不静默）
        logger.warning("布局变更后计算缓存失效失败（存在旧缓存遮蔽风险）: %s", exc)


async def resolve_layout(
    session: Any,
    *,
    loop_id: str,
    at: datetime,
) -> str:
    """解析某回路在某时刻的读取布局（loop 精确段优先于 global 兜底）.

    无任何 manifest → 'legacy'（默认；建表不切读，设计 §7-1）。
    同层多段重叠时取 valid_from 最新（后发布优先）。
    """
    tt = _aware(at)
    for scope_type, scope_id in (("loop", loop_id), ("global", None)):
        cond = [
            HistoryLayoutManifest.scope_type == scope_type,
            HistoryLayoutManifest.is_active.is_(True),
            HistoryLayoutManifest.valid_from <= tt,
        ]
        if scope_id is not None:
            cond.append(HistoryLayoutManifest.scope_id == scope_id)
        rows = (
            (
                await session.execute(
                    select(HistoryLayoutManifest)
                    .where(*cond)
                    .order_by(HistoryLayoutManifest.valid_from.desc())
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.valid_to is None or tt < _aware(row.valid_to):
                return row.layout
    return "legacy"


async def archive_expired_batches(
    session: Any,
    *,
    older_than: datetime,
    keep_days_confirmed: int = 30,
) -> int:
    """归档老旧 confirmed/cancelled 批次（生命周期；保留覆盖段与冲突审计）."""
    cutoff = _aware(older_than) - timedelta(days=keep_days_confirmed)
    rows = (
        (
            await session.execute(
                select(HistoryWriteBatch.batch_id).where(
                    HistoryWriteBatch.status.in_(["confirmed", "cancelled"]),
                    HistoryWriteBatch.created_at < cutoff,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0
    await session.execute(
        delete(HistoryWriteBatch).where(HistoryWriteBatch.batch_id.in_(list(rows)))
    )
    return len(rows)
