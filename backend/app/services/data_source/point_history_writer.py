"""PointHistoryWriter（P2-1/P2-2）：实时事件 → 测点子表的有界批量写.

设计依据：设计文档 §3/§4.3/§6.1；整改计划 P2。

与 RealtimeSubscriber 的关系：
- **同一事件流，不新建第二订阅者**（计划 P2-5）：subscriber 在 ``_cache_value``
  中把原始点事件同步转投本 writer（``submit_raw``）；宽表写路径原样保留，
  由 storage_mode 决定两路的启停（legacy：writer 不启动，零开销）；
- 事件按**事件**组织（T02）：同一点同一 tick 的多次变化、纯质量变化、合法
  迟到事件都会进入队列——**不做** ``loop→role`` 最新值覆盖；显示缓存语义
  （最新值）仍在 subscriber 侧，与本层无关。

可靠性（设计 §4.3，复用 R07 已修复措施的同等语义，不重造领导者机制）：
- 队列有界（``max_queue``）：溢出计 ``events_dropped_queue_full`` 并登记
  **缺口窗口**（gap，禁止默填为正常）；
- 批量写分块 + 3 次退避重试；最终失败进有界重试缓冲，进程内重放；
- TD 确认后才推进覆盖段（pending→confirmed 由批次确认驱动）；
- 进程崩溃时未持久接纳的事件不承诺无损：恢复后以缺口窗口登记
  （_load_pending_gaps 风格的语义由 coverage gap 段承载）。

质量解码：**唯一**入口 ``decode_quality``（按 quality_schema）；AAS 枚举
映射为暂定口径（P0-5 U1 未确认项——真实语义在 P4 用 zpdev 样本核verify，
未确认前不得把未知码解为 Good）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

#: 队列上限（事件数）：8649 点 × COV 突发余量
DEFAULT_MAX_QUEUE = 200_000
#: 单次 flush 间隔（秒，与宽表 flush 同拍但独立节拍）
DEFAULT_FLUSH_INTERVAL = 1.0
#: 重试缓冲上限（事件数）：溢出即丢弃并登记缺口
DEFAULT_MAX_RETRY_BUFFER = 100_000
#: 覆盖段 PG 写入节流（秒）
DEFAULT_COVERAGE_FLUSH_INTERVAL = 30.0
#: 单批最大事件数（分块写）
DEFAULT_BATCH_EVENTS = 2_000

#: 质量解码暂定口径（P0-5 U1：真实 AAS 枚举未确认；未知码恒 UNKNOWN）
_AAS_RAW_TO_CLASS: dict[int, int] = {
    1: 1,  # Good
    0: 0,  # Bad
    2: -1,  # Uncertain → UNKNOWN
    3: -1,  # 离线 → UNKNOWN
}
#: 远端历史 qualities（mock 契约：0=未知 1=Good 2=Bad 3=离线）
_AAS_HISTORY_RAW_TO_CLASS: dict[int, int] = {
    1: 1,
    2: 0,
    3: -1,
    0: -1,
}
#: OPC DA 位编码：limit-constant/limit-... 等带质量位的 Good 变体（0xC0~0xEF 段）
_OPCDA_GOOD_CODES = frozenset(
    {
        192,
        193,
        194,
        195,
        196,
        199,
        200,
        203,
        204,
        207,
        216,
        219,
        220,
        223,
        224,
        227,
        228,
        231,
        232,
        235,
        236,
        239,
    }
)


def decode_quality(raw: Any, quality_schema: int) -> tuple[int, int | None]:
    """原码 → (quality_class, quality_raw)。无法识别 → (-1, raw)（绝不 Good）."""
    if raw is None:
        return -1, None
    try:
        code = int(float(raw))
    except (ValueError, TypeError):
        return -1, None
    if quality_schema == 1:  # AAS 枚举（实时）
        return _AAS_RAW_TO_CLASS.get(code, -1), code
    if quality_schema == 2:  # OPC DA 位编码
        if code in _OPCDA_GOOD_CODES:
            return 1, code
        return (0 if code < 64 else -1), code
    return -1, code


def decode_history_quality(raw: Any) -> tuple[int, int | None]:
    """远端历史 qualities 数组元素 → (class, raw)（AAS 历史口径，暂定）。"""
    if raw is None:
        return -1, None
    try:
        code = int(float(raw))
    except (ValueError, TypeError):
        return -1, None
    return _AAS_HISTORY_RAW_TO_CLASS.get(code, -1), code


def parse_source_ts(ts_val: Any) -> datetime | None:
    """源时间（collectTime 等）→ aware UTC；空/不可解析 → None（不伪造 now）。"""
    if not ts_val:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class RawPointInput:
    """__slots__ 轻量事件（热路径）。"""

    __slots__ = (
        "point_id",
        "ts",
        "value",
        "quality_class",
        "quality_raw",
        "quality_schema",
        "source_kind",
    )

    def __init__(
        self,
        point_id: str,
        ts: datetime,
        value: float | int | None,
        quality_class: int,
        quality_raw: int | None,
        quality_schema: int | None,
        source_kind: int,
    ) -> None:
        self.point_id = point_id
        self.ts = ts
        self.value = value
        self.quality_class = quality_class
        self.quality_raw = quality_raw
        self.quality_schema = quality_schema
        self.source_kind = source_kind


class PointHistoryWriter:
    """点历史写器（进程内单例语义；由 RealtimeSubscriber 生命周期托管）。"""

    def __init__(
        self,
        *,
        max_queue: int = DEFAULT_MAX_QUEUE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        max_retry: int = DEFAULT_MAX_RETRY_BUFFER,
        coverage_flush_interval: float = DEFAULT_COVERAGE_FLUSH_INTERVAL,
        source_id: str = "clpm-realtime",
    ) -> None:
        self._queue: deque[RawPointInput] = deque()
        self._max_queue = max_queue
        self._flush_interval = flush_interval
        self._max_retry = max_retry
        self._coverage_interval = coverage_flush_interval
        self._source_id = source_id
        self._lock = asyncio.Lock()  # 实例级（非模块级，遵守红线）
        self._task: asyncio.Task | None = None
        self._running = False
        self._retry_buffer: deque[RawPointInput] = deque()
        # 覆盖跟踪（内存，节流落 PG）
        self._session_start: datetime | None = None
        self._confirmed_until: datetime | None = None
        self._gap_windows: list[tuple[datetime, datetime]] = []
        self._last_coverage_flush = 0.0
        self._current_segment_id: str | None = None
        self._seg_start: datetime | None = None
        # tag_code → point_id 缓存（与 subscriber 映射同源 PG；TTL 300s）
        self._tag_points: dict[str, str] = {}
        self._tag_points_at = 0.0
        # 统计（S0 契约 §8 风格）
        self.metrics: dict[str, int] = {
            "events_received": 0,
            "events_dropped_queue_full": 0,
            "events_dropped_no_point": 0,
            "events_dropped_bad_ts": 0,
            "events_written": 0,
            "events_identical_skipped": 0,
            "conflicts_skipped": 0,
            "chunks_failed": 0,
            "gap_windows": 0,
        }

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # 终态 flush：把在途事件尽力落库（进程内优雅停机）
        try:
            await self._flush_once(final=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PointHistoryWriter 停机 flush 失败（缺口由恢复流程登记）: %s", exc)

    @property
    def pending(self) -> int:
        return len(self._queue) + len(self._retry_buffer)

    # ------------------------------------------------------------------
    # 事件入口（subscriber._cache_value 同步转投；绝不 await 网络）
    # ------------------------------------------------------------------

    def submit_raw(
        self,
        tag_code: str,
        value: Any,
        quality: Any,
        collect_time: Any,
        *,
        source_kind: int = 1,
    ) -> bool:
        """原始 AAS 消息 → 点事件入队（解析失败/无点身份计数量并丢弃该事件）.

        value 非有限（NaN/Inf/工业异常串）→ 事件仍入队但 value=None（质量照记）。
        返回是否入队。
        """
        self.metrics["events_received"] += 1
        point_id = self._tag_points.get(tag_code)
        if point_id is None:
            self.metrics["events_dropped_no_point"] += 1
            return False
        ts = parse_source_ts(collect_time)
        if ts is None:
            self.metrics["events_dropped_bad_ts"] += 1
            return False
        qclass, qraw = decode_quality(quality, 1)
        try:
            fval = float(value) if value is not None and value != "" else None
            if fval is not None and fval != fval:  # NaN
                fval = None
            elif fval in (float("inf"), float("-inf")):
                fval = None
        except (ValueError, TypeError):
            fval = None
        ev = RawPointInput(point_id, ts, fval, qclass, qraw, 1, source_kind)
        return self._enqueue(ev)

    def submit_event(self, ev: RawPointInput) -> bool:
        """已构造事件入队（导入/回放路径）。"""
        self.metrics["events_received"] += 1
        return self._enqueue(ev)

    def _enqueue(self, ev: RawPointInput) -> bool:
        if len(self._queue) >= self._max_queue:
            # 缺口窗口按**被丢弃事件的源时间**登记（不是墙钟——源时间可能
            # 晚于/早于本进程时刻，覆盖语义以数据时间轴为准）
            self._register_gap_memory(ev.ts, ev.ts)
            self.metrics["events_dropped_queue_full"] += 1
            return False
        self._queue.append(ev)
        if self._session_start is None or ev.ts < self._session_start:
            if self._session_start is None:
                self._session_start = ev.ts
        return True

    def _register_gap_memory(self, start: datetime, end: datetime) -> None:
        if self._gap_windows and self._gap_windows[-1][1] >= start:
            s0, e0 = self._gap_windows[-1]
            self._gap_windows[-1] = (s0, max(e0, end))
        else:
            self._gap_windows.append((start, end))
        self.metrics["gap_windows"] += 1

    # ------------------------------------------------------------------
    # tag → point 身份缓存
    # ------------------------------------------------------------------

    async def refresh_tag_points(self, force: bool = False) -> None:
        """从 tag_registry 刷新 tag_code → point_id（TTL 300s；失败保留旧映射）."""
        now = time.monotonic()
        if not force and self._tag_points and now - self._tag_points_at < 300.0:
            return
        try:
            from sqlalchemy import select

            from app.core.db import AsyncSessionLocal
            from app.models.tag import TagRegistry

            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(TagRegistry.id, TagRegistry.tag_name))).all()
            self._tag_points = {name: str(pid) for pid, name in rows}
            self._tag_points_at = now
            logger.info("PointHistoryWriter 点身份映射已刷新: %d 点", len(self._tag_points))
        except Exception as exc:  # noqa: BLE001
            logger.warning("刷新点身份映射失败（沿用旧映射 %d 点）: %s", len(self._tag_points), exc)

    # ------------------------------------------------------------------
    # flush
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        # 幂等建表（st_point_data_v1，zpdev 2026-09-07 实测缺口：全新环境
        # 首次启动该表不存在，flush 持续 0x2603）——启动先 ensure 一次；
        # 失败不阻断循环（下方异常分支含表缺失自愈）。
        try:
            from app.services.data_source.point_history_repository import ensure_schema

            await ensure_schema()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PointHistoryWriter schema 初始化失败（异常分支自愈兜底）: %s", exc)
        await self.refresh_tag_points(force=True)
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("PointHistoryWriter flush 异常: %s", exc)
                # 表缺失自愈（0x2603：TD 重建/误删表后重新幂等建表，
                # 下一拍 flush 恢复；其他异常不触发）
                if "not exist" in str(exc) or "0x2603" in str(exc):
                    try:
                        from app.services.data_source.point_history_repository import (
                            ensure_schema,
                        )

                        await ensure_schema()
                    except Exception as ensure_exc:  # noqa: BLE001
                        logger.warning("PointHistoryWriter schema 自愈失败: %s", ensure_exc)

    async def _flush_once(self, *, final: bool = False) -> None:
        from app.services.data_source.point_history_repository import (
            PointEvent,
            write_events,
        )

        # 重试缓冲优先（旧失败先于新数据）
        batch: list[RawPointInput] = []
        if self._retry_buffer:
            batch.extend(self._retry_buffer)
            self._retry_buffer.clear()
        async with self._lock:
            if self._queue:
                batch.extend(self._queue)
                self._queue.clear()
        if not batch:
            await self._maybe_flush_coverage()
            return

        # 周期性点身份保鲜（低成本：TTL 内 no-op）
        if not final:
            await self.refresh_tag_points()

        # 分块
        events: list[PointEvent] = []
        for ev in batch:
            events.append(
                PointEvent(
                    point_id=ev.point_id,
                    ts=ev.ts,
                    value=ev.value,
                    quality_class=ev.quality_class,
                    quality_raw=ev.quality_raw,
                    quality_schema=ev.quality_schema,
                    source_kind=ev.source_kind,
                    received_at=datetime.now(UTC),
                    source_id=self._source_id,
                )
            )
        for i in range(0, len(events), DEFAULT_BATCH_EVENTS):
            chunk = events[i : i + DEFAULT_BATCH_EVENTS]
            result = await write_events(chunk)
            if result.failed:
                self.metrics["chunks_failed"] += 1
                self._retry_buffer.extend(chunk)
                # 重试缓冲有界：溢出部分丢弃并登记缺口
                dropped = 0
                while len(self._retry_buffer) > self._max_retry:
                    self._retry_buffer.popleft()
                    dropped += 1
                if dropped:
                    self._register_gap_memory(min(e.ts for e in chunk), datetime.now(UTC))
                logger.error(
                    "点历史批次写入失败（%d 事件进重试缓冲，丢 %d 登记缺口）: %s",
                    len(chunk),
                    dropped,
                    result.error,
                )
                continue
            self.metrics["events_written"] += result.inserted
            self.metrics["events_identical_skipped"] += result.identical_skipped
            self.metrics["conflicts_skipped"] += len(result.conflicts)
            ts_max = max(e.ts for e in chunk)
            if self._confirmed_until is None or ts_max > self._confirmed_until:
                self._confirmed_until = ts_max
        await self._maybe_flush_coverage()

    # ------------------------------------------------------------------
    # 覆盖登记（节流落 PG）
    # ------------------------------------------------------------------

    async def _maybe_flush_coverage(self, *, force: bool = False) -> None:
        """覆盖登记（节流）：每会话一个 confirmed 段持续延伸；gap 时切段。

        模型（设计 §4.2/§4.3）：
        - 会话内无缺口 → 单段 [session_start, confirmed_until) 持续延伸（1 行）；
        - 缺口（队列满/重试溢出/停机丢失）→ 当前段闭合并登记 gap 段（status=gap，
          已知未知窗口，builder 不得当覆盖），恢复后开新 confirmed 段；
        - PG 不可达时保留内存状态，下一节拍重试（不丢 gap 语义）。
        """
        now = time.monotonic()
        if not force and now - self._last_coverage_flush < self._coverage_interval:
            return
        self._last_coverage_flush = now
        gaps = list(self._gap_windows)
        self._gap_windows.clear()
        if self._session_start is None and not self._current_segment_id:
            return
        try:
            from sqlalchemy import select

            from app.core.db import AsyncSessionLocal
            from app.models.point_history import HistoryCoverageSegment
            from app.services.data_source import point_history_metadata as meta

            async with AsyncSessionLocal() as db:
                seg_end = (self._confirmed_until or self._session_start) + timedelta(milliseconds=1)
                if self._current_segment_id is None:
                    seg = await meta.register_coverage(
                        db,
                        seg_start=self._session_start,
                        seg_end=seg_end,
                        session_id=self._source_id,
                        source_task="point-writer",
                    )
                    seg.status = "confirmed"
                    self._current_segment_id = seg.id
                    self._seg_start = seg.seg_start
                else:
                    row = (
                        (
                            await db.execute(
                                select(HistoryCoverageSegment).where(
                                    HistoryCoverageSegment.id == self._current_segment_id
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if row is None:
                        # 段被外部清理/删除：重开一段
                        seg = await meta.register_coverage(
                            db,
                            seg_start=self._seg_start or self._session_start,
                            seg_end=seg_end,
                            session_id=self._source_id,
                            source_task="point-writer",
                        )
                        seg.status = "confirmed"
                        self._current_segment_id = seg.id
                        self._seg_start = seg.seg_start
                    else:
                        row.seg_end = max(row.seg_end, seg_end)
                for gs, ge in gaps:
                    # 闭合当前段 → gap 段 → 新段起点
                    cur = (
                        (
                            await db.execute(
                                select(HistoryCoverageSegment).where(
                                    HistoryCoverageSegment.id == self._current_segment_id
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if cur is not None:
                        cur.seg_end = min(cur.seg_end, gs)
                    gap_seg = await meta.register_coverage(
                        db,
                        seg_start=gs,
                        seg_end=ge + timedelta(milliseconds=1),
                        session_id=self._source_id,
                        source_task="point-writer",
                    )
                    gap_seg.status = "gap"
                    new_end = max(seg_end, ge + timedelta(milliseconds=1))
                    new_seg = await meta.register_coverage(
                        db,
                        seg_start=ge,
                        seg_end=new_end,
                        session_id=self._source_id,
                        source_task="point-writer",
                    )
                    new_seg.status = "confirmed"
                    self._current_segment_id = new_seg.id
                    self._seg_start = new_seg.seg_start
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            # 失败恢复：gap 窗口回退内存，下一节拍重试
            self._gap_windows = gaps + self._gap_windows
            logger.warning("覆盖登记失败（下一节拍重试）: %s", exc)
