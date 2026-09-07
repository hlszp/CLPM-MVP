"""LogicalWideBuilder（P3-1）：测点子表 → 1 秒逻辑宽表的唯一构建器.

设计依据：设计文档 §5（唯一实现）/§5.2 构建步骤/§5.3 时间边界/§5.4 质量兼容。

定位：
- **唯一**从 ``st_point_data_v1`` 组装算法数据的实现；算法/endpoints 不直接
  访问测点子表（设计 §5）；
- 输出契约与 legacy 宽表路径一致：``RawTimeSeries``（角色小写键、
  ``pv_quality`` 质量键、naive UTC 时间戳）——下游预处理/DataPlanner 零改动。

语义规则（作为等价性差分的预期固定）：
1. 网格 = 窗口内全部 **UTC 整秒**（含两端；非整秒窗口不扩大——首点向上
   取整、末点向下取整，设计 §5.3"不偷偷取整扩大数据窗口"）；
2. 因果保持：t 时刻取**不晚于 t 的最近状态**（事件毫秒保留，秒内变化生效
   于其后首个网格点，不看未来）；段起点初值 = 该点分段前最后事件，无则
   适用锚点（锚点携带原 sourceTime，按其时刻因果生效，不伪造新变化）；
3. 正常 COV 常值保持（无变化≠缺失）；**未知**（无初值/改绑新点无锚点/
   会话 gap/该时刻无绑定）→ 值 None + 质量 -1，**不删行**；
4. 质量随状态保持（BAD 不被跳过沿用旧 Good，V04）；三态直接输出 1/0/-1
   （解码已在写入侧唯一入口完成，本层不猜原码）；
5. 改绑窗口分段：新点状态只来自新点事件/锚点，禁止沿用旧点值（V09）；
6. MODE 角色输出整数语义（点表 DOUBLE 存储还原 int，对齐宽表 TINYINT）；
7. interval_s 保留参数但 v1 恒 1s 网格（HF 组复用 BASE 不降分辨率，§5.2-5）。
"""

from __future__ import annotations

import asyncio
import bisect
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.contracts import series_context as sc
from app.contracts.data_types import RawTimeSeries
from app.services.data_source import point_history_metadata as meta
from app.services.data_source import point_history_repository as repo

logger = logging.getLogger(__name__)

_ROLE_INT = frozenset({"mode"})  # 整数语义角色（legacy 宽表 TINYINT 对齐）
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _ceil_second(dt: datetime) -> datetime:
    dt = _to_utc(dt)
    if dt.microsecond:
        return dt.replace(microsecond=0) + timedelta(seconds=1)
    return dt


def _floor_second(dt: datetime) -> datetime:
    return _to_utc(dt).replace(microsecond=0)


class _BindingSegment:
    """角色绑定的生效段 [start, end)（end=None 开放）。"""

    __slots__ = ("start", "end", "point_id", "initial_check")

    def __init__(self, start: datetime, end: datetime | None, point_id: str) -> None:
        self.start = start
        self.end = end
        self.point_id = point_id
        # 初值探测时刻（段与窗口交集起点；初值/锚点在该时刻前生效）
        self.initial_check: datetime | None = None


class _PointStream:
    """单点因果状态流：窗口事件 + 段起点初值/锚点（虚拟事件），按时刻可查."""

    def __init__(self) -> None:
        self._ts: list[datetime] = []
        self._states: list[dict[str, Any]] = []

    def add(self, ts: datetime, state: dict[str, Any] | None) -> None:
        if state is None or ts is None:
            return
        i = bisect.bisect_left(self._ts, ts)
        if i < len(self._ts) and self._ts[i] == ts:
            self._states[i] = state  # 同 ts 后写覆盖（到达序）
        else:
            self._ts.insert(i, ts)
            self._states.insert(i, state)

    def state_at(self, at: datetime) -> dict[str, Any] | None:
        i = bisect.bisect_right(self._ts, at)
        if i == 0:
            return None
        return self._states[i - 1]


async def _resolve_role_segments(
    db: Any,
    loop_id: str,
    roles: list[str],
    grid_start: datetime,
    grid_end: datetime,
) -> dict[str, list[_BindingSegment]]:
    """解析各角色绑定分段（有历史按历史；无历史回退当前映射单段）."""
    from app.models.loop import LoopTagMapping
    from app.models.point_history import LoopTagBindingHistory

    rows = (
        await db.execute(
            select(
                LoopTagBindingHistory.tag_role,
                LoopTagBindingHistory.tag_id,
                LoopTagBindingHistory.valid_from,
                LoopTagBindingHistory.valid_to,
            ).where(LoopTagBindingHistory.loop_id == loop_id)
        )
    ).all()
    # 内部统一大写角色键（绑定历史/映射存大写；调用方传小写）
    out: dict[str, list[_BindingSegment]] = {r.upper(): [] for r in roles}
    if not rows:
        mappings = (
            await db.execute(
                select(LoopTagMapping.tag_role, LoopTagMapping.tag_id).where(
                    LoopTagMapping.loop_id == loop_id
                )
            )
        ).all()
        by_role = {str(m[0]).upper(): str(m[1]) for m in mappings}
        for role in out:
            pid = by_role.get(role)
            if pid:
                out[role] = [_BindingSegment(datetime.min.replace(tzinfo=UTC), None, pid)]
    else:
        for role_db, tag_id, valid_from, valid_to in rows:
            role = str(role_db).upper()
            if role in out:
                out[role].append(
                    _BindingSegment(
                        _to_utc(valid_from),
                        _to_utc(valid_to) if valid_to is not None else None,
                        str(tag_id),
                    )
                )
    for role in out:
        out[role].sort(key=lambda s: s.start)
        for seg in out[role]:
            if seg.end is None or seg.end > grid_start:
                if seg.start <= grid_end:
                    seg.initial_check = min(max(seg.start, grid_start), grid_end)
    return out


async def build_logical_wide(
    db: Any,
    loop_id: str,
    tag_roles: list[str],
    start: datetime,
    end: datetime,
    interval_s: int = 1,  # noqa: ARG001 — 契约保留；v1 恒 1s（不降采样）
) -> RawTimeSeries:
    """构建窗口的 1 秒逻辑宽表（设计 §5.2 全步骤实现）。"""
    grid_start = _ceil_second(start)
    grid_end = _floor_second(end)
    requested = [r.lower() for r in tag_roles]
    # PV_QUALITY 是伪角色（BASE/QUALITY_HF 组的列语义，非点绑定）：
    # legacy 路径把宽表 pv_quality 列作为信号返回——此处等价地把 PV 质量
    # 三态数组作为该信号输出；内部计算补入 PV 角色求质量列
    pseudo_quality = "pv_quality" in requested
    roles = [r for r in requested if r != "pv_quality"]
    # PV 质量列恒需 PV 状态：内部计算恒含 PV（未请求时输出前移除）
    internal_roles = list(roles)
    if "pv" not in internal_roles:
        internal_roles.append("pv")
    if grid_end < grid_start:
        return RawTimeSeries(timestamps=[], signals={r: [] for r in requested}, quality_codes={})

    segments = await _resolve_role_segments(db, loop_id, internal_roles, grid_start, grid_end)

    # 点集合与分段初值探测
    initial_probes: dict[tuple[str, datetime], None] = {}
    for role_segs in segments.values():
        for seg in role_segs:
            if seg.initial_check is not None:
                initial_probes.setdefault((seg.point_id, seg.initial_check))
    all_points = {pid for pid, _ in initial_probes}

    streams: dict[str, _PointStream] = {}
    if all_points:
        # 窗口内事件（一次批量）
        # 窗口内事件：≤16 点按点并行读（TD 连接池并发，单点子表扫描快于
        # 多子表合并大查询）；>16 点退回单条批量 IN 查询（SQL 次数仍有界）。
        # 最小列集（ts/value/quality_class——减小 TD 响应体量）。
        sorted_points = sorted(all_points)
        if 1 < len(sorted_points) <= 16:
            per_point = await asyncio.gather(
                *[
                    repo.read_events([pid], grid_start, grid_end, full_columns=False)
                    for pid in sorted_points
                ]
            )
            events_map = {pid: m[pid] for m, pid in zip(per_point, sorted_points, strict=True)}
        else:
            events_map = await repo.read_events(
                sorted_points, grid_start, grid_end, full_columns=False
            )
        # 各探测时刻的窗口前最后状态（batch per 时刻）
        by_before: dict[datetime, list[str]] = {}
        for pid, before in initial_probes:
            by_before.setdefault(before, []).append(pid)
        initial_states: dict[tuple[str, datetime], dict[str, Any] | None] = {}
        for before, pids in by_before.items():
            states = await repo.read_last_states_before(pids, before)
            for pid, st in states.items():
                initial_states[(pid, before)] = st
        # 锚点（批量单查询；作为虚拟事件按其 sourceTime 生效）
        anchors = await meta.applicable_anchors_batch(
            db, point_ids=sorted(all_points), window_start=grid_start
        )
        # 组装每点因果流
        for pid in all_points:
            stream = _PointStream()
            for ev in events_map.get(pid, []):
                if ev.get("ts") is not None:
                    stream.add(ev["ts"], ev)
            anchor = anchors.get(pid)
            if anchor is not None:
                stream.add(
                    _to_utc(anchor.anchor_ts),
                    {
                        "value": anchor.value,
                        "quality_class": anchor.quality_class,
                        "quality_raw": anchor.quality_raw,
                        "ts": _to_utc(anchor.anchor_ts),
                    },
                )
            for (apid, _before), st in initial_states.items():
                if apid == pid and st is not None and st.get("ts") is not None:
                    stream.add(st["ts"], st)
            streams[pid] = stream

    # 会话 gap 窗口（队列满/停机丢失登记 → 未知强制，禁止默填）
    gap_windows = await _load_gap_windows(db)

    # 网格
    grid: list[datetime] = []
    signals: dict[str, list[Any]] = {r: [] for r in roles}
    pv_quality: list[int] = []
    t = grid_start
    while t <= grid_end:
        grid.append(t)
        t += timedelta(seconds=1)
    n = len(grid)

    # 槽状态记录（AD01 上下文）：role → [(known, reason)]；known 含
    # OBSERVED（段内事件）与 HELD（初值/锚点保持）；unknown 归因常量见
    # series_context 模块（gap/no_initial/rebind/unbound）
    slot_status: dict[str, list[tuple[bool, str | None]]] = {}
    rebind_boundaries: list[datetime] = []
    for role in internal_roles:
        col: list[Any] = []
        status: list[tuple[bool, str | None]] = []
        segs = segments[role.upper()]
        first_event_seen = False
        active_pid: str | None = None
        # 预计算该角色网格内的改绑边界索引集（避免逐槽列表扫描）
        role_rebind_idx: set[int] = set()
        _pre_seen: str | None = None
        for _i, _ts in enumerate(grid):
            _seg = _active_segment(segs, _ts)
            _pid = _seg.point_id if _seg is not None else None
            if _pre_seen is not None and _pid is not None and _pid != _pre_seen:
                role_rebind_idx.add(_i)
            if _pid is not None:
                _pre_seen = _pid
        for ts_i, ts in enumerate(grid):
            value = None
            seg = _active_segment(segs, ts)
            if seg is not None and active_pid is not None and seg.point_id != active_pid:
                # 改绑边界：新点自该时刻起生效（新点需自己的初值/事件）
                rebind_boundaries.append(ts)
                first_event_seen = False
                active_pid = seg.point_id
            if seg is not None:
                active_pid = seg.point_id
            if seg is None:
                status.append((False, sc.UNKNOWN_REASON_UNBOUND))
                col.append(None)
                if role == "pv":
                    pv_quality.append(-1)
                continue
            if _in_gap(ts, gap_windows):
                status.append((False, sc.UNKNOWN_REASON_GAP))
                col.append(None)
                if role == "pv":
                    pv_quality.append(-1)
                continue
            st = streams.get(seg.point_id)
            ev = st.state_at(ts) if st is not None else None
            if ev is not None:
                value = ev.get("value")
                if role in _ROLE_INT and value is not None:
                    value = int(round(float(value)))
                if role == "pv":
                    pv_quality.append(int(ev.get("quality_class", -1)))
                col.append(value)
                status.append((True, None))
                first_event_seen = True
            else:
                # 无状态归因：首事件前=无初值；改绑后新点无事件=rebind；
                # 段内事件后状态耗尽（锚点 coverage_until 边界后）=no_initial
                if first_event_seen:
                    reason = sc.UNKNOWN_REASON_NO_INITIAL
                else:
                    reason = (
                        sc.UNKNOWN_REASON_REBIND
                        if ts_i in role_rebind_idx
                        else sc.UNKNOWN_REASON_NO_INITIAL
                    )
                status.append((False, reason))
                col.append(None)
                if role == "pv":
                    pv_quality.append(-1)
        signals[role] = col
        slot_status[role] = status

    # pv_quality 仅在 PV 角色遍历时逐点追加（n 恰等于网格点数）
    timestamps_naive = [ts.replace(tzinfo=None) for ts in grid]
    if pseudo_quality:
        # 伪角色信号 = PV 质量三态数组（与 legacy 宽表列语义一致）
        assert len(pv_quality) == n
        signals["pv_quality"] = list(pv_quality)
    # 输出仅含请求角色（内部补入的 PV 在未请求时移除）
    signals = {r: signals[r] for r in requested if r in signals}
    quality_codes: dict[str, list[int]] = {}
    if "pv" in roles:
        assert len(pv_quality) == n  # PV 角色逐网格点恰好追加一次
        quality_codes["pv_quality"] = pv_quality

    context = _build_context(
        loop_id=loop_id,
        grid=grid,
        roles=internal_roles,
        slot_status=slot_status,
        rebind_boundaries=rebind_boundaries,
        segments=segments,
        gap_windows=gap_windows,
    )
    return RawTimeSeries(
        timestamps=timestamps_naive,
        signals=signals,
        quality_codes=quality_codes,
        series_context=context,
    )


def _build_context(
    *,
    loop_id: str,
    grid: list[datetime],
    roles: list[str],
    slot_status: dict[str, list[tuple[bool, str | None]]],
    rebind_boundaries: list[datetime],
    segments: dict[str, list[_BindingSegment]],
    gap_windows: list[tuple[datetime, datetime]],
) -> sc.SeriesContext:
    """组装 SeriesContext（AD01：覆盖游程/未知归因/分段边界/数据身份）。"""
    import hashlib

    grid_start, grid_end = grid[0], grid[-1]
    role_coverage: dict[str, sc.RoleCoverage] = {}
    unknown_reasons: dict[str, int] = {}
    for role in roles:
        status = slot_status.get(role, [])
        runs = sc.RoleCoverage()
        cur_known: bool | None = None
        cur_start = grid_start
        for i, ts in enumerate(grid):
            known, reason = status[i] if i < len(status) else (False, None)
            if not known and reason:
                unknown_reasons[reason] = unknown_reasons.get(reason, 0) + 1
            if known != cur_known:
                if cur_known is not None:
                    _append_run(runs, cur_known, cur_start, ts)
                cur_known = known
                cur_start = ts
        if cur_known is not None:
            _append_run(runs, cur_known, cur_start, grid_end + timedelta(seconds=1))
        role_coverage[role] = runs

    boundaries = [sc.SegmentBoundary(at=b, kind="rebind") for b in sorted(set(rebind_boundaries))]
    # 数据身份：loop + 各角色绑定点集 + 边界时刻的摘要（布局由路由层决定，
    # provider 混合窗拼接处统一改标 mixed）
    ident_parts = [loop_id]
    for role in sorted(segments):
        ident_parts.append(f"{role}:" + ",".join(seg.point_id for seg in segments[role]))
    ident_parts.extend(str(b) for b in sorted(set(rebind_boundaries)))
    dataset_ref = hashlib.sha1("|".join(ident_parts).encode()).hexdigest()[:16]

    seg_count = sum(len(v) for v in segments.values())
    return sc.SeriesContext(
        layout=sc.LAYOUT_POINT,
        grid_start=grid_start,
        grid_end=grid_end,
        grid_period_s=1.0,
        expected_slots=len(grid),
        dataset_ref=dataset_ref,
        binding_version=f"b{seg_count}",
        coverage_revision=f"g{len(gap_windows)}",
        role_coverage=role_coverage,
        unknown_reasons=unknown_reasons,
        segment_boundaries=boundaries,
        # v1：改绑边界视同解释配置变化候选（新点量程可能不同；解释配置
        # 历史未建前的保守近似——登记于补充方案实施记录）
        interpretation_consistent=not boundaries,
    )


def _append_run(cov: sc.RoleCoverage, known: bool, start: datetime, end: datetime) -> None:
    run = sc.IntervalRun(start=start, end=end)
    if known:
        cov.known.append(run)
    else:
        cov.unknown.append(run)


def _active_segment(segs: list[_BindingSegment], at: datetime) -> _BindingSegment | None:
    for seg in segs:
        if seg.start <= at and (seg.end is None or at < seg.end):
            return seg
    return None


def _in_gap(ts: datetime, gap_windows: list[tuple[datetime, datetime]]) -> bool:
    return any(gs <= ts < ge for gs, ge in gap_windows)


async def _load_gap_windows(db: Any) -> list[tuple[datetime, datetime]]:
    from app.models.point_history import HistoryCoverageSegment

    rows = (
        await db.execute(
            select(HistoryCoverageSegment.seg_start, HistoryCoverageSegment.seg_end).where(
                HistoryCoverageSegment.status == "gap"
            )
        )
    ).all()
    return [(_to_utc(r[0]), _to_utc(r[1])) for r in rows]
