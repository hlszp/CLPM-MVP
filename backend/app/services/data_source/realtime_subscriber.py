"""实时数据订阅器 — WebSocket 客户端.

连接模拟 SignalR Hub（``/signalr/realValueForClpmHub``），订阅全部活跃 Tag，
将实时值缓存到 Redis，供 API 查询。

消息协议遵循 RealDATA_API.md：
- 发送: {"method": "SubscribeAsync", "args": [["TAG001", "TAG002"]]}
- 接收: {"event": "updateRealValues", "data": [{"tagCode": "...", "value": "...", ...}]}
- 初始响应: {"code": 200, "data": [...]}

Redis 缓存:
- key: ``realtime:{tagCode}``
- value: JSON ``{"value": "12.5", "quality": 0, "collectTime": "..."}``
- TTL: 60 秒（超时自动清除过期数据）

断点续传（Gap Backfill）:
- 每次收到数据更新内存 ``_last_data_at``，并由 ``_flush_buffer`` 节流持久化到
  Redis checkpoint（``realtime:gap:last_data_ts``，epoch 秒）；
- 重连成功（含进程重启后首次连接）时检测缺口，超过
  ``GAP_BACKFILL_MIN_GAP_SECONDS`` 即触发后台补数任务，调用
  ``data_import.import_history_data``（skip 策略，依赖 TDengine 同 ts 覆盖语义）
  补全缺口窗口，并触发受影响小时的 KPI 回算；
- 单次补数窗口上限 ``GAP_BACKFILL_MAX_HOURS``，超出部分截断并告警，需手工导入；
- checkpoint 条件推进：仅补数全部成功（failed==0）才推进 checkpoint，
  部分失败/异常时缺口保留，并启动延迟重试定时器
  （``GAP_BACKFILL_RETRY_BASE_SECONDS`` 起步指数退避，
  上限 ``GAP_BACKFILL_RETRY_MAX_SECONDS``，连接在线也生效）；
- 补数执行经 ``task_tracker`` 登记任务记录（triggered_by=auto-backfill），
  任务列表可见；失败接 ``alerting`` 告警。

数据停滞看门狗（WS-B2）:
- N 秒（``SIGNALR_STALL_TIMEOUT_SECONDS``，默认 300s）无消息主动断开重连，
  覆盖"WS 连接活着但上游停推"盲区；看门狗在 ``_connect_and_subscribe`` 接收
  循环中以超时 recv 实现，断开后由主循环指数退避重连。

落库/接收 checkpoint 分离（WS-B2）:
- ``_last_data_at`` 为接收点（每条消息更新，用于看门狗）；
- ``_last_flushed_at`` 为落库点（仅成功 flush 后推进，持久化到 Redis，
  供进程重启后恢复缺口起点）；gap backfill 以落库点为窗口起点，
  避免 flush 失败时 checkpoint 已超前导致缺口遗漏。

SETNX 分布式锁（WS-B2）:
- 多副本部署时，gap backfill 经 Redis SETNX 抢锁（TTL =
  ``GAP_BACKFILL_LOCK_TTL_SECONDS``），未抢到锁的副本跳过，避免重复补数。

多 worker 进程订阅单例（Leader 锁）:
- 生产 ``uvicorn --workers 4`` 下每个 worker 进程都会执行 lifespan 调用
  ``start_subscriber()``；进程内单例无法跨进程去重，故引入 Redis Leader 锁
  （``realtime:subscriber:leader:lock``，SETNX + TTL =
  ``SUBSCRIBER_LEADER_LOCK_TTL_SECONDS``）：仅持锁进程真正连接 Hub 并回写
  TDengine，其余进程待命并周期抢锁；持锁进程每 TTL/3 续期（CAS 校验 token），
  崩溃/退出后其他进程在 TTL 内接管；Redis 异常时降级为无锁运行（不劣于
  无锁现状）。

时区显式转换（WS-B2）:
- ``_build_row`` 对 collectTime 显式 astimezone 到目标时区（Asia/Shanghai），
  消除 naive datetime 在 TDengine 侧的 8h 偏移风险。

订阅连接池化 + 扇入（2026-09-06）:
- 探针实测：AAS 服务端在单连接大订阅量（8649 位号）下推送扇出停摆
  （快照后 updateRealValues 归零、Pong 仍应答）且连接 ~200s 被强制回收；
  ≤1000 位号则推送连续且连接稳定。故活跃 Tag 按每片 ≤``_SHARD_SIZE``（1000）
  切分为 N 条独立分片连接（``_shard_loop`` 各自连接/订阅/心跳/重连），
  数据统一经 ``_cache_value`` 扇入 Redis 缓存/PubSub/写回，对前端透明；
- 每片独立应用层心跳（type=6）与片级停滞看门狗；分片建连错峰
  （``_SHARD_CONNECT_STAGGER``）；监督循环（``_run_pool``）每分钟比对
  活跃 Tag 集合，变化即整池重建（新增位号纳入新分片）。

订阅手工/事件刷新（免重启生效）:
- 订阅集合 = ``tag_registry WHERE is_linked=True``；回路/测点/绑定关系变更后，
  变更写路径提交后调用 ``notify_subscription_changed`` 向 Redis 控制频道
  （``realtime:control:subscription``）发布刷新指令（fire-and-forget）；
- 仅 Leader 进程经 ``_control_loop`` 监听控制频道（Leadership 切换时启停），
  收到指令后重查活跃 Tag、与 ``_subscribed_tags`` 做 diff，各**分片连接**在
  现有连接上以新 invocationId 重发自身位号的 ``SubscribeAsync``（Completion
  响应由所在分片接收循环统一处理），并清空落库映射缓存（不等 300s TTL）；
  新增位号触发连接池重建（监督循环下一拍以新 Tag 集合重建分片）；
- removed 的 Tag 不向 Hub 退订（Hub 语义不支持可靠退订，多推的值进 Redis 无害），
  结果中如实返回 removed 清单；
- 每次刷新结果写入 Redis ``realtime:subscription:refresh_result``（TTL 60s），
  供 ``POST /datasource/refresh-subscription`` 轮询读取（requestId 匹配）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import websockets

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.exceptions import BizError
from app.core.redis import redis_client
from app.core.tdengine import make_subtable_name
from app.core.tdengine_native import batch_insert_multi
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# 目标时区（Asia/Shanghai）—— TDengine 服务器本地时区，所有写入时间戳统一转至此
_TARGET_TZ = timezone(timedelta(hours=8))

# Redis 缓存 key 前缀
_REDIS_KEY_PREFIX = "realtime:"
_REDIS_TTL = 3600  # 秒（1 小时），确保页面刷新时能从缓存读取实时值
_PUBSUB_CHANNEL = "realtime:updates"  # Pub/Sub 频道，供 WebSocket 端点订阅

# 断点续传 checkpoint（最后落库时间，epoch 秒字符串）
_GAP_CHECKPOINT_KEY = "realtime:gap:last_data_ts"
_GAP_CHECKPOINT_WRITE_INTERVAL = 30.0  # checkpoint 写 Redis 节流间隔（秒）
_GAP_BACKFILL_END_MARGIN = 2.0  # 补数窗口末端留 2s 余量，避免与实时写入撞时间戳

# gap backfill SETNX 分布式锁 key（多副本防重复补数）
_GAP_BACKFILL_LOCK_KEY = "realtime:gap:backfill:lock"

# 多 worker 进程订阅单例 Leader 锁 key（仅持锁进程连接 Hub 并回写 TDengine）
_SUBSCRIBER_LEADER_LOCK_KEY = "realtime:subscriber:leader:lock"

# 订阅刷新控制频道（Pub/Sub）：测点/回路/绑定关系变更或 API 手工触发时发布
# {"type": "refresh", "requestId", "source", "requestedAt"}，仅 Leader 进程监听
_CONTROL_CHANNEL = "realtime:control:subscription"
# 最近一次订阅刷新结果 key（TTL 60s），供 API 轮询读取（requestId 匹配）
_REFRESH_RESULT_KEY = "realtime:subscription:refresh_result"
_REFRESH_RESULT_TTL = 60  # 秒
# 控制频道连接异常后的重建等待（秒）
_CONTROL_RECONNECT_DELAY = 5.0

# 看门狗 recv 超时（秒）—— 周期性检查消息停滞，超时即断开重连
_WATCHDOG_RECV_TIMEOUT = 30.0

# SP/MODE 等低频信号刷新间隔（秒）—— 自愈/存活检查循环的节拍；全量重订阅
# （重新调用 SubscribeAsync）另按 SIGNALR_RESUBSCRIBE_INTERVAL（默认 30 分钟）
# 节流，见 _refresh_loop。
_SIGNALR_REFRESH_INTERVAL = 60.0  # 1 分钟

# 应用层心跳（SignalR type=6 Ping）：连接空闲时周期发送，服务端 Pong 即存活。
# 背景（2026-09-06 探针实测）：AAS 网关会在无下行流量数分钟后回收 WebSocket
# 会话（静默冻结或 RST），协议级 ping 已因 AAS 不应答而禁用（见 config.py）；
# type=6 应用层 ping 双方均应答，既作保活流量也作快速探活。
_PING_KEEPALIVE_INTERVAL = 25.0
# Ping 发出后超过该时长未获 Pong 且期间无任何数据 → 判定连接死亡，立即重连
_PING_DEATH_TIMEOUT = 60.0

# 订阅连接池化（2026-09-06，探针实测驱动）：
# AAS 服务端在单连接大订阅量（8649 位号）下推送扇出停摆（快照后增量推送归零，
# Pong 仍应答）且连接 ~200s 被回收；≤1000 位号则推送连续（427 条/5min）且连接
# 稳定。故将活跃 Tag 切分为多条分片连接（每片 ≤_SHARD_SIZE 个位号）并行订阅，
# 数据统一扇入 _cache_value（Redis 缓存/PubSub/写回），对前端透明。
_SHARD_SIZE = 1000
_SHARD_CONNECT_STAGGER = 0.5  # 分片建连错峰间隔（秒），避免池启动瞬间 N 连并发
# 连接池监督循环检查 Tag 集合变化的周期（秒）
_POOL_TAG_CHECK_INTERVAL = 60.0

# 订阅分块大小：单条 SubscribeAsync 携带的位号数上限（2026-09-03 生产事故加固）
# 8600+ 位号单条订阅消息约 200KB，被远端 Hub 收到后立即关闭连接（code 1000，
# 疑似超服务端单消息上限）；分块后单条约 10KB，Completion 响应由接收循环统一处理
_SUBSCRIBE_CHUNK_SIZE = 500


@dataclass
class _ShardState:
    """单条分片连接的独立状态（连接池化：每条 WS 连接互不共享）."""

    index: int  # 分片序号（0 起）
    total: int  # 分片总数
    tags: list[str] = field(default_factory=list)  # 本分片订阅的位号（有序）
    ws: Any = None
    ping_pending_since: float | None = None  # 待应答心跳的发送时刻（epoch 秒）
    last_ping_sent_at: float = 0.0
    last_resubscribe_at: float = 0.0  # 上次全量重订阅时刻（低频信号保鲜）
    last_data_at: float | None = None  # 本分片最后收到数据消息时间（停滞看门狗用）


def _split_shards(tag_codes: list[str], shard_size: int) -> list[list[str]]:
    """把位号列表按 ``shard_size`` 切分为分片（保序，末片可不满）."""
    return [tag_codes[i : i + shard_size] for i in range(0, len(tag_codes), shard_size)]


# loop_part → (loop_id, unit_id) 缓存 TTL（秒）：flush 热路径不每拍查库
_LOOP_META_CACHE_TTL = 300.0
# 缓存缺失 loop_part 时的最小刷新间隔（秒）：防止未配置映射的 loop_part
# 每次 flush 都触发一次 DB 查询
_LOOP_META_MISS_REFRESH_MIN_INTERVAL = 60.0

# tag_name 后缀 → DDL 列名映射（与 tdengine.py 保持一致）
_ROLE_COLUMN_MAP: dict[str, str] = {
    "PV": "pv",
    "SP": "sp",
    "OP": "op",
    "MODE": "mode",
    "PID_P": "pid_p",
    "PID_I": "pid_i",
    "PID_D": "pid_d",
}


def _normalize_ts(ts_str: str) -> str:
    """将时间戳字符串显式转换到目标时区（Asia/Shanghai），返回 naive 格式字符串.

    - 带时区（含 Z 后缀）：astimezone 到 _TARGET_TZ
    - naive（无时区）：视为已在 _TARGET_TZ
    - 空或解析失败：取当前 _TARGET_TZ 时间

    返回格式 ``YYYY-MM-DD HH:MM:SS.fff``（毫秒精度，TDengine 兼容）。
    """
    if ts_str:
        try:
            dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_TARGET_TZ)
            else:
                dt = dt.astimezone(_TARGET_TZ)
            return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        except (ValueError, TypeError):
            pass
    return datetime.now(_TARGET_TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def next_reconnect_delay(current: float, *, cap: float) -> float:
    """计算下次重连等待秒数：当前等待翻倍，封顶 cap（指数退避）。

    避免远端 Hub 不可用时固定小间隔重连对服务持续施压。
    """
    return min(current * 2, cap)


def backfill_retry_delay(retry_count: int, *, base: float, cap: float) -> float:
    """补数失败第 retry_count 次重试的退避秒数：base 起步指数翻倍，封顶 cap。

    retry_count 从 1 开始：base, 2*base, 4*base, ...，封顶 cap。
    """
    return min(base * (2 ** max(retry_count - 1, 0)), cap)


# PV 角色对应的质量码列名
_QUALITY_COLUMN_MAP: dict[str, str | None] = {
    "PV": "pv_quality",
    "SP": None,
    "OP": None,
    "MODE": None,
    "PID_P": None,
    "PID_I": None,
    "PID_D": None,
}


class RealtimeSubscriber:
    """实时数据订阅器.

    生命周期：
    1. ``start()`` — 启动后台任务，连接 WebSocket Hub
    2. 定期从数据库查询全部活跃 Tag，发送订阅请求
    3. 接收推送，更新 Redis 缓存 + 写入 TDengine
    4. 断线自动重连
    5. ``stop()`` — 停止订阅，关闭连接

    TDengine 写入策略：
    - 实时推送是单 tag 值，TDengine 按回路行存储（PV/SP/OP/MODE 同 row）
    - 使用缓冲区 accumulate 各角色值，每秒 flush 一次
    - 缺失的角色用 NULL 填充
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._running = False
        self._subscribed_tags: set[str] = set()
        self._invocation_counter: int = 0  # SignalR invocationId 计数器
        self._buffer: dict[
            str, dict[str, Any]
        ] = {}  # {loop_part: {role: {"value": ..., "quality": ..., "ts": ...}}}
        self._buffer_lock = asyncio.Lock()
        # 后台 fire-and-forget 任务引用集合（防 GC 中途回收——事件循环对任务仅弱引用，
        # 2026-08-21 事故：MODE 缓存失效任务被 GC 出现 "Task was destroyed but it is pending"）
        self._bg_tasks: set[asyncio.Task] = set()
        # 低频角色（SP/MODE/PID_*）跨flush持久缓存：上次已知值。
        # 解决"低频角色没变化→buffer中缺失→flush写NULL"的问题——flush时合并_last_known，
        # 未在本tick出现的角色取最近已知值，保证TDengine宽表每行非PV字段完整。
        # 结构: {loop_part: {role: {"value": ..., "quality": ..., "ts": ...}}}
        self._last_known: dict[str, dict[str, Any]] = {}
        # 断点续传状态（接收点 vs 落库点分离）
        self._last_data_at: float | None = None  # 接收点：最后收到数据时间（epoch 秒，wall clock）
        self._last_flushed_at: float | None = None  # 落库点：最后成功 flush 时间（仅成功后推进）
        self._last_checkpoint_write: float = 0.0  # 上次写 checkpoint 的 monotonic 时间
        self._backfill_task: asyncio.Task | None = None  # 进行中的补数任务（单实例守卫）
        # 补数失败重试状态（连接在线也生效的延迟重试定时器）
        self._backfill_retry_task: asyncio.Task | None = None  # 待执行的重试定时器
        self._backfill_retry_count: int = 0  # 连续失败次数（决定指数退避间隔）
        self._retry_window_start: float | None = None  # 待重试缺口起点（多次失败取最早）
        # loop_part → (loop_id, unit_id) 缓存（实时写回 TDengine 时填充 USING TAGS；
        # TDengine TAGS 仅子表首次创建生效，实时先行创建的子表 TAG 必须带真实值）
        self._loop_meta_cache: dict[str, tuple[str, str]] = {}
        self._loop_meta_cache_at: float = 0.0  # 上次缓存刷新（含失败尝试）的 monotonic 时间
        # 测点 tag_name → (loop_part=回路 tag_name, role)，与 _loop_meta_cache 同源刷新
        self._tag_role_cache: dict[str, tuple[str, str]] = {}
        # 多 worker 进程订阅单例（Leader 锁）状态
        self._leader_task: asyncio.Task | None = None  # Leader 锁维护循环（抢锁/续期）
        self._leader_token: str = ""  # 本进程锁 token（hostname:pid:monotonic_ns）
        self._is_leader = False  # 当前是否持有 Leader 锁（仅持锁进程真正订阅）
        # 订阅刷新控制频道监听任务（仅 Leader 进程运行，随 Leadership 切换启停）
        self._control_task: asyncio.Task | None = None
        # 连接池共享状态：当前分片列表（refresh_subscription 用）与重建信号
        self._shard_states: list[_ShardState] = []
        self._rebuild_event: asyncio.Event = asyncio.Event()
        # 每代连接池只触发一次缺口补数检查（9 条分片建连各触发一次无意义）
        self._pool_backfill_done = False

    @property
    def _writeback_enabled(self) -> bool:
        """是否启用实时数据写回本地 TDengine 宽表。"""
        return settings.REALTIME_WRITEBACK_ENABLED

    async def start(self) -> None:
        """启动订阅后台任务."""
        if self._running:
            return
        if not settings.SIGNALR_ENABLED:
            logger.info("实时数据订阅已禁用（SIGNALR_ENABLED=False）")
            return
        if not settings.SIGNALR_HUB_URL:
            logger.warning("SIGNALR_HUB_URL 未配置，跳过实时数据订阅")
            return

        self._running = True
        # 进程重启场景：从 Redis checkpoint 恢复最后数据时间，
        # 使首次连接成功即可感知进程停机期间的数据缺口
        await self._load_checkpoint()
        # 多 worker 进程防护：Redis Leader 锁保证同一时刻仅一个进程真正订阅。
        # 抢到锁立即启动订阅任务；未抢到进入待命，由 _leader_loop 周期抢锁接管
        self._leader_token = f"{socket.gethostname()}:{os.getpid()}:{time.monotonic_ns()}"
        if await self._acquire_leader_lock():
            self._become_leader()
        else:
            logger.info("实时数据订阅待命：Leader 锁被其他 worker 进程持有，周期抢锁中")
        self._leader_task = asyncio.create_task(self._leader_loop())
        logger.info(
            "实时数据订阅任务已启动 (hub=%s, writeback=%s, leader=%s)",
            settings.SIGNALR_HUB_URL,
            self._writeback_enabled,
            self._is_leader,
        )

    async def stop(self) -> None:
        """停止订阅."""
        self._running = False
        # 停止 Leader 锁维护循环（停止续期/抢锁）
        if self._leader_task is not None:
            self._leader_task.cancel()
            try:
                await self._leader_task
            except asyncio.CancelledError:
                pass
            self._leader_task = None
        # 停止订阅任务并释放 Leader 锁（先停任务再放锁，避免接管进程与本进程并行写）
        was_leader = self._is_leader
        await self._resign_leader()
        if was_leader:
            await self._release_leader_lock()
        if self._backfill_task is not None:
            self._backfill_task.cancel()
            try:
                await self._backfill_task
            except asyncio.CancelledError:
                pass
            self._backfill_task = None
        if self._backfill_retry_task is not None:
            self._backfill_retry_task.cancel()
            try:
                await self._backfill_retry_task
            except asyncio.CancelledError:
                pass
            self._backfill_retry_task = None
        self._retry_window_start = None
        self._backfill_retry_count = 0
        # 停止前 flush 剩余数据（分片连接由 _run_pool 的 finally 统一收尾）
        await self._flush_buffer()
        logger.info("实时数据订阅已停止")

    def _on_main_task_done(self, task: asyncio.Task) -> None:
        """主任务退出观测：运行期内意外退出（非 stop() 触发）记错误日志.

        2026-08-21 事故根因之一：远端引擎重启掐断 WS 连接后主任务静默死亡，
        看门狗随主任务一同失效，无任何日志；本回调补齐退出可观测性，
        自愈重建由 ``_refresh_loop`` 的存活检查负责。
        """
        if not self._running or task.cancelled():
            return
        exc = None
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is None:
            logger.error("实时订阅主任务意外退出（无异常，疑似被 GC/外部取消）")
        else:
            logger.error("实时订阅主任务异常退出: %s", exc, exc_info=exc)

    # ------------------------------------------------------------------
    # 多 worker 进程订阅单例（Redis Leader 锁）
    # ------------------------------------------------------------------

    def _become_leader(self) -> None:
        """持有 Leader 锁：启动订阅主任务 / flush / 周期刷新 / 控制频道监听任务."""
        self._is_leader = True
        self._task = asyncio.create_task(self._run())
        # 主任务意外退出观测（2026-08-21 事故：远端引擎重启掐断连接后
        # 主任务静默死亡，看门狗随之失效，无任何日志可查）
        self._task.add_done_callback(self._on_main_task_done)
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._control_task = asyncio.create_task(self._control_loop())
        logger.warning("本进程已接管实时数据订阅（Leader）: token=%s", self._leader_token)

    async def _resign_leader(self) -> None:
        """失去/释放 Leader 锁：取消订阅主任务 / flush / 周期刷新 / 控制监听任务（幂等）."""
        self._is_leader = False
        for attr in ("_task", "_flush_task", "_refresh_task", "_control_task"):
            task = getattr(self, attr)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, attr, None)

    async def _leader_loop(self) -> None:
        """Leader 锁维护循环：持锁时周期续期（CAS），未持锁时周期抢锁接管.

        - 续期失败（锁被其他进程持有或已过期被抢走）→ 停任务转待命，继续抢锁；
        - Redis 异常时按既定降级策略处理（见 _acquire/_renew），不中断循环。
        """
        while self._running:
            try:
                ttl = int(settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS)
            except Exception:  # noqa: BLE001
                ttl = 30  # 配置异常兜底（正常路径 settings 为 int，不会走到）
            try:
                await asyncio.sleep(max(ttl / 3.0, 1.0))
                if not self._running:
                    break
                if self._is_leader:
                    if not await self._renew_leader_lock():
                        logger.warning("实时订阅 Leader 锁已丢失，本进程停止订阅转待命")
                        await self._resign_leader()
                elif await self._acquire_leader_lock():
                    self._become_leader()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("Leader 锁维护循环异常（下周期重试）: %s", exc)

    async def _acquire_leader_lock(self) -> bool:
        """SETNX 抢占订阅 Leader 锁（多 worker 进程防重复订阅）.

        Returns:
            True 抢到锁；False 锁已被其他进程持有。
            Redis 异常时降级返回 True（无锁运行，不劣于引入锁之前的现状）。
        """
        try:
            ok = await redis_client.set(
                _SUBSCRIBER_LEADER_LOCK_KEY,
                self._leader_token,
                nx=True,
                ex=int(settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS),
            )
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.warning("订阅 Leader 锁抢占异常（降级为无锁运行）: %s", exc)
            return True

    async def _renew_leader_lock(self) -> bool:
        """续期订阅 Leader 锁（CAS 校验 token，防给别人的锁续期）.

        Returns:
            True 仍持有锁；False 锁已丢失（被抢或过期后易主）。
            Redis 异常时返回 True 保持现状（下周期重试，避免闪断导致全员停订）。
        """
        _RENEW_LUA = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('EXPIRE', KEYS[1], ARGV[2]) else return 0 end"
        )
        try:
            ok = await redis_client.eval(
                _RENEW_LUA,
                1,
                _SUBSCRIBER_LEADER_LOCK_KEY,
                self._leader_token,
                str(int(settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS)),
            )
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.warning("订阅 Leader 锁续期异常（保持现状，下周期重试）: %s", exc)
            return True

    async def _release_leader_lock(self) -> None:
        """释放订阅 Leader 锁（CAS 校验 token，防误释放）。"""
        _RELEASE_LUA = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) else return 0 end"
        )
        try:
            await redis_client.eval(
                _RELEASE_LUA, 1, _SUBSCRIBER_LEADER_LOCK_KEY, self._leader_token
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("订阅 Leader 锁释放失败（可忽略，TTL 兜底）: %s", exc)

    def _spawn_bg(self, coro) -> None:
        """启动 fire-and-forget 后台任务并保持引用（防 GC 中途回收）.

        事件循环对任务仅持弱引用，无引用的任务可能被 GC 出现
        ``Task was destroyed but it is pending``（2026-08-21 日志实锤）。
        """
        t = asyncio.create_task(coro)
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

    async def _run(self) -> None:
        """主循环：订阅连接池监督（连接池化 + 扇入）.

        活跃 Tag 按每片 ≤``_SHARD_SIZE`` 个切分为 N 条独立 WS 连接，各自
        连接/订阅/接收/保活/重连（``_shard_loop``），数据统一经 ``_cache_value``
        扇入 Redis 缓存/PubSub/写回。Tag 集合变化或重建信号触发时整池重建。
        """
        while self._running:
            tag_codes = sorted(await self._get_active_tags())
            if not tag_codes:
                logger.info("无活跃 Tag，等待数据...")
                await asyncio.sleep(30)
                continue
            shards = _split_shards(tag_codes, _SHARD_SIZE)
            self._subscribed_tags = set(tag_codes)
            self._pool_backfill_done = False
            logger.info(
                "订阅连接池启动：%d 个 Tag 切分为 %d 条分片连接（每片 ≤%d）",
                len(tag_codes),
                len(shards),
                _SHARD_SIZE,
            )
            await self._run_pool(shards)
            if not self._running:
                break
            # 池退出（Tag 集合变化 / 重建信号 / 全体分片异常退出）→ 重建
            await asyncio.sleep(float(settings.SIGNALR_RECONNECT_INTERVAL))

    async def _run_pool(self, shards: list[list[str]]) -> None:
        """运行一代连接池：N 条分片并行，Tag 集合变化或全体退出时返回."""
        states = [
            _ShardState(index=i, total=len(shards), tags=chunk) for i, chunk in enumerate(shards)
        ]
        self._shard_states = states
        self._rebuild_event.clear()
        tasks = [asyncio.create_task(self._shard_loop(st)) for st in states]
        try:
            while self._running and tasks:
                done, _pending = await asyncio.wait(tasks, timeout=_POOL_TAG_CHECK_INTERVAL)
                if done:
                    for t in done:
                        exc = t.exception() if not t.cancelled() else None
                        st = states[tasks.index(t)]
                        logger.error(
                            "分片 %d/%d 任务意外退出（应由片内重连自愈）: %s",
                            st.index + 1,
                            st.total,
                            exc or "cancelled/无异常",
                        )
                    tasks = [t for t in tasks if not t.done()]
                    if not tasks:
                        logger.error("全部分片任务退出，交由监督循环重建连接池")
                        return
                if self._rebuild_event.is_set():
                    logger.info("收到重建信号，重建订阅连接池")
                    return
                current = sorted(await self._get_active_tags())
                if current and set(current) != self._subscribed_tags:
                    logger.info(
                        "活跃 Tag 集合变化（%d → %d），重建订阅连接池",
                        len(self._subscribed_tags),
                        len(current),
                    )
                    return
        finally:
            self._shard_states = []
            for t in tasks:
                t.cancel()
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True), timeout=5.0
                    )
                except TimeoutError:
                    logger.warning("分片任务收尾超时（5s），放弃等待")

    async def _shard_loop(self, state: _ShardState) -> None:
        """单分片主循环：连接 → 订阅 → 接收 → 重连（指数退避）."""
        base_delay = float(settings.SIGNALR_RECONNECT_INTERVAL)
        max_delay = float(settings.SIGNALR_RECONNECT_MAX_INTERVAL)
        delay = base_delay
        connected_at = 0.0
        # 分片建连错峰：避免池启动/重建瞬间 N 条连接同时打向 Hub
        await asyncio.sleep(state.index * _SHARD_CONNECT_STAGGER)
        while self._running:
            try:
                connected_at = time.monotonic()
                await self._connect_and_subscribe(state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # 连接健康存活超过 60s 才视为稳定连接，重置退避到 base；
                # 否则先按当前退避等待，再翻倍（base → ×2 → … → max 封顶），
                # 避免远端 Hub 不可用时固定小间隔重连持续施压
                if time.monotonic() - connected_at > 60:
                    delay = base_delay
                logger.warning(
                    "分片 %d/%d 订阅异常: %s，%.0fs 后重连",
                    state.index + 1,
                    state.total,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = next_reconnect_delay(delay, cap=max_delay)
            finally:
                # 确保旧连接在任何情况下都被关闭，防止服务器侧 CLOSE_WAIT 堆积
                await self._close_shard_ws(state)

    async def _close_shard_ws(self, state: _ShardState) -> None:
        """安全关闭分片 WebSocket 连接（幂等）。"""
        if state.ws is not None:
            try:
                await state.ws.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("关闭旧 WebSocket 连接时异常（可忽略）: %s", exc)
            finally:
                state.ws = None

    async def _send_subscribe_invocations(
        self, ws: Any, tag_codes: list[str], prefix: str
    ) -> list[str]:
        """分块发送 SubscribeAsync（每块 ≤ ``_SUBSCRIBE_CHUNK_SIZE`` 个位号）。

        单条全量订阅消息过大时会被远端 Hub 直接关闭连接（2026-09-03 实锤），
        分块后每条约 10KB；各块 Completion (type=3) 响应携带该块位号当前值，
        由所在分片的接收循环统一处理。

        必须包含 invocationId，否则 AAS 将调用视为 fire-and-forget，不返回
        Completion 响应（初始当前值会永远缺失）。

        Returns:
            各块 invocationId 列表（``{prefix}_{计数器}``，计数器逐块递增）。
        """
        invocation_ids: list[str] = []
        for i in range(0, len(tag_codes), _SUBSCRIBE_CHUNK_SIZE):
            chunk = tag_codes[i : i + _SUBSCRIBE_CHUNK_SIZE]
            self._invocation_counter += 1
            invocation_id = f"{prefix}_{self._invocation_counter}"
            subscribe_msg = (
                json.dumps(
                    {
                        "type": 1,
                        "invocationId": invocation_id,
                        "target": "SubscribeAsync",
                        "arguments": [chunk],
                    }
                )
                + "\x1e"
            )
            await ws.send(subscribe_msg)
            invocation_ids.append(invocation_id)
        return invocation_ids

    async def _connect_and_subscribe(self, state: _ShardState) -> None:
        """分片连接 Hub 并订阅本分片位号.

        SignalR JSON Hub Protocol 流程：
        1. WebSocket 连接
        2. 发送握手 {"protocol":"json","version":1}\\x1e
        3. 接收握手响应 {}\\x1e（成功）或 {"error":"..."}\\x1e（失败）
        4. 之后所有消息以 \\x1e (Record Separator) 分帧
        """
        # 先关闭残留的旧连接，防止泄漏
        await self._close_shard_ws(state)

        state.ws = await websockets.connect(
            settings.SIGNALR_HUB_URL,
            # 0/None → 禁用协议级 ping（生产 AAS 不应答，会周期性误判断连）
            ping_interval=settings.SIGNALR_PING_INTERVAL or None,
            ping_timeout=settings.SIGNALR_PING_TIMEOUT,
            open_timeout=settings.SIGNALR_OPEN_TIMEOUT,
        )
        logger.info(
            "分片 %d/%d 已连接实时数据 Hub: %s（%d 位号）",
            state.index + 1,
            state.total,
            settings.SIGNALR_HUB_URL,
            len(state.tags),
        )

        # SignalR 协议握手
        handshake_msg = json.dumps({"protocol": "json", "version": 1}) + "\x1e"
        await state.ws.send(handshake_msg)
        raw = await state.ws.recv()
        handshake = json.loads(raw.rstrip("\x1e"))
        if "error" in handshake:
            raise ConnectionError(f"SignalR 握手失败: {handshake['error']}")

        # 新连接重置心跳与重订阅节流状态；片级接收点（last_data_at）
        # 跨片内重连保留——停滞看门狗语义与单连接时期一致（最近一次
        # 真正缓存值的时间，即使发生在上一条连接上）
        state.ping_pending_since = None
        state.last_ping_sent_at = 0.0
        state.last_resubscribe_at = time.time()

        # 发送订阅请求（标准 SignalR JSON Hub Protocol: type=1 Invocation）
        # 必须包含 invocationId，否则 AAS 将调用视为 fire-and-forget，不返回
        # Completion (type=3) 响应——初始响应中包含所有订阅 Tag 的当前值
        # （含 SP/MODE/PID 等低频信号），缺少 invocationId 会导致这些值永远缺失。
        # 2026-09-03：分块订阅——单条大消息（8600+ 位号约 200KB）会被 Hub 关闭
        invocation_ids = await self._send_subscribe_invocations(state.ws, state.tags, "sub")
        logger.info(
            "分片 %d/%d 已订阅 %d 个 Tag（%d 块，invocationId=%s…）",
            state.index + 1,
            state.total,
            len(state.tags),
            len(invocation_ids),
            invocation_ids[0] if invocation_ids else "-",
        )

        # 断点续传：每代连接池首个分片建连成功即检测数据缺口并自动补数
        if not self._pool_backfill_done:
            self._pool_backfill_done = True
            await self._maybe_trigger_gap_backfill()

        # 接收初始响应（Completion: type=3, result 包含 {code, data}）
        # 一帧可能包含多条 \x1e 分隔的消息（Completion + 首批 push）
        raw = await state.ws.recv()
        for part in raw.split("\x1e"):
            if not part:
                continue
            try:
                initial = json.loads(part)
            except json.JSONDecodeError:
                continue
            await self._process_shard_message(state, initial)

        # 持续接收推送（一条 WebSocket 帧可能包含多条 \x1e 分隔的消息）
        # 数据停滞看门狗：以 _WATCHDOG_RECV_TIMEOUT 超时 recv 代替 async for，
        # 超时后检查本分片距上次数据是否超过 SIGNALR_STALL_TIMEOUT_SECONDS，
        # 超过则主动断开重连（覆盖"WS 活着但上游停推"盲区）
        stall_timeout = float(settings.SIGNALR_STALL_TIMEOUT_SECONDS)
        while self._running and state.ws is not None:
            try:
                raw_message = await asyncio.wait_for(
                    state.ws.recv(), timeout=_WATCHDOG_RECV_TIMEOUT
                )
            except TimeoutError:
                # recv 超时：先做应用层心跳保活/探活与低频信号重订阅，再查停滞
                await self._keepalive_tick(state)
                if self._is_ping_dead(state):
                    logger.warning(
                        "分片 %d/%d 应用层心跳 %.0fs 无 Pong（期间无数据），判定连接死亡，主动重连",
                        state.index + 1,
                        state.total,
                        time.time() - (state.ping_pending_since or 0.0),
                    )
                    await self._close_shard_ws(state)
                    return
                await self._resubscribe_tick(state)
                if state.last_data_at is not None:
                    idle = time.time() - state.last_data_at
                    if idle >= stall_timeout:
                        logger.warning(
                            "分片 %d/%d 数据停滞看门狗触发：%.0fs 无数据（阈值 %.0fs），"
                            "主动断开重连",
                            state.index + 1,
                            state.total,
                            idle,
                            stall_timeout,
                        )
                        await self._close_shard_ws(state)
                        return
                continue
            for part in raw_message.split("\x1e"):
                if not part:
                    continue
                try:
                    msg = json.loads(part)
                    await self._process_shard_message(state, msg)
                except json.JSONDecodeError:
                    logger.warning("收到非 JSON 消息: %s", part[:100])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("处理实时数据消息失败: %s", exc)

    async def _process_shard_message(self, state: _ShardState, msg: dict) -> None:
        """分片消息统一入口：type=6 心跳在片内处理，其余交共享处理器."""
        if msg.get("type") == 6:
            await self._handle_ping_frame(state)
            # 数据未到但 Pong 已到：pending 已清，无碍后续心跳
            return
        await self._handle_signalr_message(msg)
        # 片级接收点对齐全局接收点（仅真正缓存了值才推进——空 Completion /
        # 空推送不算数据，停滞语义与单连接时期一致；全局点由 _cache_value
        # 维护，补数/checkpoint 语义不变）
        if self._last_data_at is not None and (
            state.last_data_at is None or self._last_data_at > state.last_data_at
        ):
            state.last_data_at = self._last_data_at
        # 数据已到（含 Completion/Push），待应答的心跳视为已答——
        # 连接显然存活，避免 pending 卡住后续心跳发送
        if (
            state.ping_pending_since is not None
            and state.last_data_at is not None
            and state.last_data_at >= state.ping_pending_since
        ):
            state.ping_pending_since = None

    async def _handle_ping_frame(self, state: _ShardState) -> None:
        """type=6 帧：我方心跳的 Pong 仅清 pending；服务端主动 Ping 回 Pong.

        不可对 Pong 再回应答，否则双方互相触发形成 ping 风暴。
        """
        if state.ping_pending_since is not None:
            state.ping_pending_since = None
            return
        if state.ws is not None:
            try:
                await state.ws.send(json.dumps({"type": 6}) + "\x1e")
            except Exception as exc:  # noqa: BLE001
                logger.debug("回复服务端 Ping 失败（连接可能已死）: %s", exc)

    async def _keepalive_tick(self, state: _ShardState) -> None:
        """连接空闲时的应用层心跳：到期发送 SignalR type=6 Ping.

        仅在 recv 空闲超时（连接无下行流量）时调用——有数据流动的连接
        无需额外保活。上一发 Ping 未获 Pong 前不重复发送。
        """
        if state.ws is None or state.ping_pending_since is not None:
            return
        now = time.time()
        if now - state.last_ping_sent_at < _PING_KEEPALIVE_INTERVAL:
            return
        try:
            await state.ws.send(json.dumps({"type": 6}) + "\x1e")
        except Exception as exc:  # noqa: BLE001
            logger.debug("发送应用层心跳失败（连接可能已死）: %s", exc)
            return
        state.last_ping_sent_at = now
        state.ping_pending_since = now

    def _is_ping_dead(self, state: _ShardState) -> bool:
        """待应答 Ping 超过 _PING_DEATH_TIMEOUT 且期间无任何数据 → 连接判死.

        有数据流动的连接不判死（个别实现可能不回 Pong 但仍在推送有效数据）；
        纯僵尸连接（发收皆无响应）由该探活覆盖，检测时长
        _PING_DEATH_TIMEOUT ~ +_WATCHDOG_RECV_TIMEOUT。
        """
        if state.ping_pending_since is None:
            return False
        if state.last_data_at is not None and state.last_data_at >= state.ping_pending_since:
            return False
        return time.time() - state.ping_pending_since > _PING_DEATH_TIMEOUT

    async def _handle_signalr_message(self, msg: dict) -> None:
        """统一处理 SignalR JSON 协议消息.

        支持的消息类型：
        - type=3 Completion: SubscribeAsync 的返回值，result 包含 {code, data}，
          data 为所有订阅 Tag 的当前值（含 SP/MODE/PID 等低频信号）
        - type=1 Invocation (target=updateRealValues): 服务端推送的实时值变化
        - 自定义格式 {code:200, data:[...]}: 兼容非标准 SignalR 响应

        type=6 Ping/Pong 在分片接收循环（``_process_shard_message``）片内处理，
        不进入本处理器——Pong 应答关系是每条连接私有的。
        """
        msg_type = msg.get("type")
        target = msg.get("target") or msg.get("event")

        # type=3 Completion — SubscribeAsync 的返回值（初始订阅 + 周期刷新）
        if msg_type == 3:
            result = msg.get("result") or {}
            data = result.get("data") or []
            if result.get("code") == 200 and data:
                for item in data:
                    await self._cache_value(item)
                logger.debug(
                    "Completion 响应: %d 个 Tag 当前值已缓存（含 SP/MODE/PID）",
                    len(data),
                )
            return

        # type=1 Invocation — updateRealValues 推送
        if target == "updateRealValues":
            data = msg.get("data") or msg.get("arguments", [[]])[0]
            if isinstance(data, list):
                for item in data:
                    await self._cache_value(item)
            return

        # 兼容自定义格式（非标准 SignalR: 顶层 code=200）
        if msg.get("code") == 200:
            data = msg.get("data") or []
            if isinstance(data, list):
                for item in data:
                    await self._cache_value(item)
            return

    async def _refresh_loop(self) -> None:
        """订阅池自愈监护（独立于主任务存活）.

        低频信号（SP/MODE/PID）保鲜的重订阅已下沉到各分片
        （``_resubscribe_tick``，按 ``SIGNALR_RESUBSCRIBE_INTERVAL`` 节流），
        本循环只负责自愈（2026-08-21 事故修复）：每周期检查——
        - 主任务已退出（远端重启掐断连接后静默死亡）→ 重建主任务；
        - 数据停滞超过看门狗阈值 + 60s 余量但看门狗未生效（主任务卡死）→
          取消并重建主任务。
        """
        stall_timeout = float(settings.SIGNALR_STALL_TIMEOUT_SECONDS)
        while self._running:
            try:
                await asyncio.sleep(_SIGNALR_REFRESH_INTERVAL)
                if not self._running:
                    continue
                # --- 自愈 1：主任务已死 → 重建 ---
                if self._task is None or self._task.done():
                    logger.warning("自愈：实时订阅主任务已退出，重建连接池")
                    self._task = asyncio.create_task(self._run())
                    self._task.add_done_callback(self._on_main_task_done)
                    continue
                # --- 自愈 2：数据停滞超阈值但看门狗未处理（主任务卡死）→ 强制重建 ---
                if self._last_data_at is not None:
                    idle = time.time() - self._last_data_at
                    if idle > stall_timeout + 60:
                        logger.warning(
                            "自愈：数据停滞 %.0fs（阈值 %.0fs）且看门狗未生效，强制重建连接池",
                            idle,
                            stall_timeout,
                        )
                        self._task.cancel()
                        try:
                            await self._task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass
                        self._task = asyncio.create_task(self._run())
                        self._task.add_done_callback(self._on_main_task_done)
                        continue
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("订阅池自愈检查异常（可忽略，下个周期重试）: %s", exc)

    async def _resubscribe_tick(self, state: _ShardState) -> None:
        """分片低频信号保鲜：按 ``SIGNALR_RESUBSCRIBE_INTERVAL`` 节流重发订阅.

        AAS 口径"同一点位只订阅一次"，重订阅仅用于 SP/MODE/PID 保鲜；
        Completion (type=3) 响应由本分片接收循环统一处理。
        """
        if state.ws is None or not state.tags:
            return
        now = time.time()
        if now - state.last_resubscribe_at < settings.SIGNALR_RESUBSCRIBE_INTERVAL:
            return
        state.last_resubscribe_at = now
        await self._send_subscribe_invocations(state.ws, state.tags, "refresh")
        logger.debug(
            "分片 %d/%d 已发送周期刷新订阅请求 (%d tags)",
            state.index + 1,
            state.total,
            len(state.tags),
        )

    # ------------------------------------------------------------------
    # 订阅手工/事件刷新（Redis Pub/Sub 控制频道，仅 Leader 监听）
    # ------------------------------------------------------------------

    async def _control_loop(self) -> None:
        """Leader 进程监听订阅控制频道，收到刷新指令即执行 ``refresh_subscription``.

        仅 Leader（持锁进程）运行本任务，由 ``_become_leader``/``_resign_leader``
        随 Leadership 切换启停；Pub/Sub 连接异常时等待后重建订阅，不中断主订阅。
        """
        while self._running and self._is_leader:
            pubsub = None
            try:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(_CONTROL_CHANNEL)
                logger.info("已监听订阅控制频道: %s", _CONTROL_CHANNEL)
                while self._running and self._is_leader:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is None:
                        continue
                    await self._handle_control_message(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "订阅控制频道监听异常（%.0fs 后重建）: %s", _CONTROL_RECONNECT_DELAY, exc
                )
                try:
                    await asyncio.sleep(_CONTROL_RECONNECT_DELAY)
                except asyncio.CancelledError:
                    raise
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(_CONTROL_CHANNEL)
                        await pubsub.aclose()
                    except Exception:  # noqa: BLE001
                        pass

    async def _handle_control_message(self, msg: dict) -> None:
        """处理控制频道消息：type=refresh 时执行订阅刷新（其余忽略）."""
        data = msg.get("data")
        if not isinstance(data, str):
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("控制频道收到非 JSON 消息: %s", data[:100])
            return
        if payload.get("type") != "refresh":
            return
        logger.info("收到订阅刷新指令: source=%s", payload.get("source"))
        await self.refresh_subscription(
            source=str(payload.get("source") or "unknown"),
            request_id=payload.get("requestId"),
            requested_at=payload.get("requestedAt"),
        )

    async def refresh_subscription(
        self,
        *,
        source: str = "unknown",
        request_id: str | None = None,
        requested_at: str | None = None,
    ) -> dict:
        """刷新实时订阅：重查活跃 Tag → diff → 各分片现有连接上重发自身位号订阅.

        - 非 Leader / WS 未连接时不产生 WS 动作，写入带 error 的结果；
        - 重发与 ``_refresh_loop`` 同路径（分块发送，websockets 支持同 loop 不同
          task 并发 send，Completion 响应由 ``_connect_and_subscribe`` 接收循环
          统一处理）；
        - removed 的 Tag 不向 Hub 退订（Hub 语义不支持可靠退订，多推的值进 Redis
          无害），结果中如实返回 removed 清单；
        - 清空 ``_tag_role_cache``/``_loop_meta_cache``（落库映射随绑定关系变化，
          不等 300s TTL，下个 flush 节拍重建）；
        - 结果写入 Redis ``_REFRESH_RESULT_KEY``（TTL 60s），供 API 轮询读取。

        Returns:
            结果 dict（requestId/requestedAt/finishedAt/source/total/added/removed/
            invocationId/leaderPid/error）。
        """
        result: dict[str, Any] = {
            "requestId": request_id,
            "requestedAt": requested_at or datetime.now(UTC).isoformat(),
            "finishedAt": None,
            "source": source,
            "total": len(self._subscribed_tags),
            "added": [],
            "removed": [],
            "invocationId": None,
            "leaderPid": os.getpid(),
            "error": None,
        }
        try:
            if not self._is_leader:
                raise RuntimeError("本进程非实时订阅 Leader，无法执行刷新")
            connected_shards = [st for st in self._shard_states if st.ws is not None]
            if not connected_shards:
                raise RuntimeError("WebSocket 未连接（订阅器等待重连中），请稍后重试")

            new_set = set(await self._get_active_tags())
            old_set = self._subscribed_tags
            added = sorted(new_set - old_set)
            removed = sorted(old_set - new_set)
            result.update({"total": len(new_set), "added": added, "removed": removed})

            if new_set:
                # 各分片在其现有连接上全量重发自身位号（新 invocationId）：
                # AAS 以最新 SubscribeAsync 为准并回发 Completion (type=3)
                # 携带全部订阅 Tag 当前值。新增位号尚未属于任何分片 →
                # 触发连接池重建（监督循环在下一拍用新 Tag 集合重建分片）。
                first_ids: list[str] = []
                for st in connected_shards:
                    invocation_ids = await self._send_subscribe_invocations(
                        st.ws, st.tags, "manual_refresh"
                    )
                    if invocation_ids and not first_ids:
                        first_ids = invocation_ids
                result["invocationId"] = first_ids[0] if first_ids else None
                if added:
                    logger.info(
                        "订阅刷新发现新增位号 %d 个，触发连接池重建以纳入新分片",
                        len(added),
                    )
                    self._rebuild_event.set()
            else:
                logger.info("订阅刷新后无活跃 Tag，跳过重发 SubscribeAsync")
                self._rebuild_event.set()
            self._subscribed_tags = new_set

            # 落库映射缓存主动清空（不等 TTL），下个 flush 节拍重建
            self._tag_role_cache = {}
            self._loop_meta_cache = {}
            self._loop_meta_cache_at = 0.0
            logger.info(
                "订阅刷新完成 (source=%s): total=%d added=%d removed=%d invocationId=%s",
                source,
                len(new_set),
                len(added),
                len(removed),
                result["invocationId"],
            )
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)
            logger.warning("订阅刷新失败 (source=%s): %s", source, exc)
        finally:
            result["finishedAt"] = datetime.now(UTC).isoformat()
            try:
                await redis_client.set(
                    _REFRESH_RESULT_KEY,
                    json.dumps(result, ensure_ascii=False),
                    ex=_REFRESH_RESULT_TTL,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("订阅刷新结果写入 Redis 失败: %s", exc)
        return result

    async def _get_active_tags(self) -> list[str]:
        """查询数据库获取全部活跃 Tag 的 tag_name."""
        try:
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(TagRegistry.tag_name).where(TagRegistry.is_linked.is_(True))
                )
                return [row[0] for row in result.all()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询活跃 Tag 失败: %s", exc)
            return []

    async def _cache_value(self, item: dict) -> None:
        """将实时值缓存到 Redis + Pub/Sub 广播 + 可选写回 TDengine."""
        tag_code = item.get("tagCode", "")
        if not tag_code:
            return

        # 断点续传：记录最后收到数据时间（wall clock）
        self._last_data_at = time.time()

        # 写入 Redis + Pub/Sub 广播（pipeline 合并为单次往返：
        # 万点秒级推送下每消息 2 次串行 RT 会成为吞吐瓶颈）
        key = f"{_REDIS_KEY_PREFIX}{tag_code}"
        payload = {
            "tagCode": tag_code,
            "value": item.get("value", ""),
            "quality": item.get("quality", 0),
            "collectTime": item.get("collectTime", ""),
        }
        value = json.dumps(payload)
        pipe = redis_client.pipeline()
        pipe.setex(key, _REDIS_TTL, value)
        pipe.publish(_PUBSUB_CHANNEL, value)
        await pipe.execute()

        # 放入内部缓冲区（供 _flush_buffer 写入 Redis 1 小时缓存及可选的 TDengine）
        loop_part, role = await self._parse_tag_code(tag_code)
        if loop_part:
            role_payload = {
                "value": item.get("value"),
                "quality": item.get("quality"),
                "ts": item.get("collectTime", ""),
            }
            async with self._buffer_lock:
                if loop_part not in self._buffer:
                    self._buffer[loop_part] = {}
                self._buffer[loop_part][role] = role_payload
                # 同步更新跨flush持久缓存：保留每个角色最近已知值，
                # 供 flush 时合并进完整行，避免低频角色（SP/MODE/PID_*）写NULL
                if loop_part not in self._last_known:
                    self._last_known[loop_part] = {}
                self._last_known[loop_part][role] = role_payload

        # MODE 变化时主动失效回路统计缓存（loop:stats:type:*），确保监控页
        # 自动/手动/自控率卡片下次查询拿到最新值，而非等 60s TTL 自然过期。
        # MODE 低频变化（小时级），失效代价低。
        # 注意：经 _spawn_bg 保持引用，防 GC 中途回收（asyncio 任务弱引用陷阱）。
        if role == "MODE":
            self._spawn_bg(self._invalidate_loop_stats_cache())

    async def _invalidate_loop_stats_cache(self) -> None:
        """MODE 变化时失效回路统计缓存，确保监控卡片下次查询拿到最新值."""
        try:
            async for key in redis_client.scan_iter(match="loop:stats:type:*"):
                await redis_client.delete(key)
        except Exception as exc:  # noqa: BLE001
            # 失败可忽略：60s TTL 自然过期，最多延迟 1 分钟
            logger.debug("失效 loop 统计缓存失败（可忽略，TTL 60s 自然过期）: %s", exc)

    async def _parse_tag_code(self, tag_code: str) -> tuple[str, str]:
        """解析 tagCode 为 (loop_part, role)。

        以 PG 的 tag→role 映射为准（loop_part=回路台账 tag_name，权威来源）。
        历史 bug（2026-08-20 修复）：此前按点号切分且未命中硬判 PV——
        本项目测点名 `41LIC30044_PIDA_SP` 无点号 → 整名当 loop_part +
        角色恒 PV，导致子表名带角色后缀、角色列错置。

        兜底：映射未命中时按点号风格解析（兼容 signal_sim 仿真 tag）；
        仍无法识别返回 ("", "")，调用方跳过缓冲（Redis 实时值缓存不受影响）。
        """
        # 缓存过期时刷新（miss 且距上次刷新超过最小间隔，防 DB 故障时每条消息打库）
        now = time.monotonic()
        miss_due = now - self._loop_meta_cache_at > _LOOP_META_MISS_REFRESH_MIN_INTERVAL
        if (not self._tag_role_cache) or (tag_code not in self._tag_role_cache and miss_due):
            try:
                await self._refresh_loop_meta_cache()
            except Exception as exc:  # noqa: BLE001
                logger.warning("刷新 tag→role 映射失败: %s", exc)

        hit = self._tag_role_cache.get(tag_code)
        if hit:
            return hit
        if "." in tag_code:
            loop_part, role = tag_code.rsplit(".", 1)
            return loop_part, role.upper()
        return "", ""

    # ------------------------------------------------------------------
    # 断点续传（Gap Backfill）
    # ------------------------------------------------------------------

    async def _load_checkpoint(self) -> None:
        """启动时从 Redis 恢复最后落库时间 checkpoint（进程重启场景）.

        落库点（``_last_flushed_at``）从 Redis 恢复；接收点（``_last_data_at``）
        初始化为落库点（进程刚重启，尚未收到新消息）。
        """
        try:
            raw = await redis_client.get(_GAP_CHECKPOINT_KEY)
            if raw:
                ts = float(raw)
                self._last_flushed_at = ts
                self._last_data_at = ts
                logger.info(
                    "断点续传 checkpoint 已恢复: last_flushed_at=%s",
                    datetime.fromtimestamp(ts, UTC).isoformat(),
                )
        except (ValueError, TypeError) as exc:
            logger.warning("断点续传 checkpoint 格式无效，忽略: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取断点续传 checkpoint 失败（可忽略）: %s", exc)

    async def _maybe_save_checkpoint(self, *, force: bool = False) -> None:
        """将最后落库时间持久化到 Redis（节流 30s，供进程重启后恢复缺口起点）.

        仅持久化落库点 ``_last_flushed_at``（成功 flush 后才推进），
        接收点 ``_last_data_at`` 不持久化（flush 失败时缺口需保留）。
        """
        if self._last_flushed_at is None:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint_write < _GAP_CHECKPOINT_WRITE_INTERVAL:
            return
        try:
            await redis_client.set(_GAP_CHECKPOINT_KEY, str(self._last_flushed_at))
            self._last_checkpoint_write = now
        except Exception as exc:  # noqa: BLE001
            logger.debug("写入断点续传 checkpoint 失败（可忽略）: %s", exc)

    async def _maybe_trigger_gap_backfill(self) -> None:
        """检测数据缺口并触发断点续传（连接/重连成功后调用）。

        以落库点 ``_last_flushed_at`` 为窗口起点（而非接收点），
        避免 flush 失败时 checkpoint 已超前导致缺口遗漏。
        """
        if not settings.GAP_BACKFILL_ENABLED:
            return
        if self._last_flushed_at is None:
            return
        if self._backfill_task is not None and not self._backfill_task.done():
            logger.info("断点续传任务仍在执行中，跳过本次触发")
            return

        now = time.time()
        gap_seconds = now - self._last_flushed_at
        if gap_seconds < float(settings.GAP_BACKFILL_MIN_GAP_SECONDS):
            return

        max_seconds = float(settings.GAP_BACKFILL_MAX_HOURS) * 3600
        gap_start = self._last_flushed_at
        if gap_seconds > max_seconds:
            logger.warning(
                "数据缺口 %.1fh 超过单次补数上限（%dh），仅补最近 %dh；"
                "更早数据请通过「数据管理→历史数据导入」手工补齐",
                gap_seconds / 3600,
                settings.GAP_BACKFILL_MAX_HOURS,
                settings.GAP_BACKFILL_MAX_HOURS,
            )
            gap_start = now - max_seconds

        # 末端留余量，避免与正在写入的实时行撞时间戳
        gap_end = now - _GAP_BACKFILL_END_MARGIN
        if gap_end <= gap_start:
            return

        logger.warning(
            "检测到实时数据缺口 %.0fs（%s ~ %s），启动断点续传自动补数",
            gap_seconds,
            datetime.fromtimestamp(gap_start, UTC).isoformat(),
            datetime.fromtimestamp(gap_end, UTC).isoformat(),
        )
        self._backfill_task = asyncio.create_task(self._run_gap_backfill(gap_start, gap_end))

    async def _run_gap_backfill(self, gap_start: float, gap_end: float) -> None:
        """执行断点续传：经远端历史数据接口补全缺口窗口，并触发 KPI 回算.

        - SETNX 分布式锁：多副本部署时防重复补数（TTL =
          ``GAP_BACKFILL_LOCK_TTL_SECONDS``），未抢到锁则跳过
        - checkpoint 条件推进：仅全部回路成功（failed==0）才推进落库点
          ``_last_flushed_at``；部分失败/异常时缺口保留，启动延迟重试定时器
          （指数退避，连接在线也生效）
        - 任务可观测：执行前经 task_tracker 登记任务记录（triggered_by=auto-backfill），
          终态更新 SUCCESS/FAILED；失败接 alerting 告警
        - 异常仅记日志不抛出（不影响实时订阅主循环）
        """
        from sqlalchemy import select

        from app.models.loop import LoopLedger
        from app.schemas.task import TaskStatus, TaskType
        from app.services import task_tracker
        from app.services.alerting import send_alert
        from app.services.data_import import _batch_get_loop_data, import_history_data

        started = time.monotonic()
        ts_start = datetime.fromtimestamp(gap_start, UTC).isoformat()
        ts_end = datetime.fromtimestamp(gap_end, UTC).isoformat()
        task_id: str | None = None
        # SETNX 分布式锁：多副本防重复补数
        lock_token = str(time.monotonic_ns())
        lock_acquired = False
        try:
            lock_acquired = await self._acquire_backfill_lock(lock_token)
            if not lock_acquired:
                logger.info("断点续传跳过：另一副本正在补数（锁被占用）")
                return

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(LoopLedger.id).where(LoopLedger.is_active.is_(True))
                )
                loop_ids = [str(row[0]) for row in result.all()]
                # 过滤无有效 tag 映射的回路：它们在 import_history_data 内必然失败，
                # 计入 failed 会导致 checkpoint 不推进 + 每次重试 send_alert，
                # 一个未配置映射的回路即可造成无限重试告警风暴
                loop_data_map = await _batch_get_loop_data(db, loop_ids)
            mapped_loop_ids = [
                lid for lid in loop_ids if loop_data_map.get(lid, {}).get("role_tag_map")
            ]
            unmapped_count = len(loop_ids) - len(mapped_loop_ids)
            if unmapped_count:
                mapped_set = set(mapped_loop_ids)
                logger.debug(
                    "断点续传剔除 %d 个无 tag 映射回路（不计入失败口径）: %s",
                    unmapped_count,
                    [lid for lid in loop_ids if lid not in mapped_set],
                )
            loop_ids = mapped_loop_ids
            if not loop_ids:
                logger.warning("断点续传跳过：无有效 tag 映射的活跃回路")
                return

            # 任务登记：补数进任务列表（来源标记 auto-backfill，系统任务不通知个人）
            _SHANGHAI = timezone(timedelta(hours=8))
            title = f"断点续传补数-{datetime.now(_SHANGHAI).strftime('%m%d%H%M')}"
            task_id = await task_tracker.create_task(
                task_type=TaskType.BACKFILL,
                created_by="system",
                created_by_id="",
                ts_start=ts_start,
                ts_end=ts_end,
                loop_ids=loop_ids,
                loops_total=len(loop_ids),
                current_stage="补数中",
                triggered_by="auto-backfill",
                title=title,
            )
            await task_tracker.update_status(
                task_id,
                TaskStatus.RUNNING,
                started_at=datetime.now(UTC).isoformat(),
            )

            import_result = await import_history_data(
                loop_ids,
                ts_start,
                ts_end,
                interval=1,
                # skip：依赖 TDengine 同 ts 覆盖语义；不可用 overwrite
                # （overwrite 会先 DELETE 窗口，误删窗口边界内的实时行）
                conflict_strategy="skip",
                # 缺口跨整点边界时重算受影响小时的 KPI
                trigger_backfill=True,
            )
            total = import_result["total"]
            succeeded = import_result["succeeded"]
            failed = import_result["failed"]

            if failed == 0:
                # 补数全部成功：落库点推进到窗口末端，避免下次重连重复补
                # （接收点 _last_data_at 不推进——补数不等于收到新实时数据）
                self._last_flushed_at = max(self._last_flushed_at or 0.0, gap_end)
                await self._maybe_save_checkpoint(force=True)
                # 本次成功窗口覆盖待重试缺口时，清除重试状态
                if self._retry_window_start is not None and gap_start <= self._retry_window_start:
                    self._clear_backfill_retry()
                await task_tracker.update_status(
                    task_id,
                    TaskStatus.SUCCESS,
                    progress=1.0,
                    loops_done=succeeded,
                    current_stage="完成",
                    finished_at=datetime.now(UTC).isoformat(),
                )
                logger.warning(
                    "断点续传完成: range=%s~%s, loops=%d/%d, 耗时=%.1fs",
                    ts_start,
                    ts_end,
                    succeeded,
                    total,
                    time.monotonic() - started,
                )
                return

            # 部分失败：checkpoint 不推进，缺口保留待重试
            errors = "; ".join(import_result.get("errors", [])[:3])
            await task_tracker.update_status(
                task_id,
                TaskStatus.FAILED,
                progress=round(succeeded / total, 4) if total > 0 else None,
                loops_done=succeeded,
                error_message=f"{failed}/{total} 回路补数失败: {errors}",
                finished_at=datetime.now(UTC).isoformat(),
            )
            await send_alert(
                "断点续传部分失败",
                f"窗口 {ts_start}~{ts_end}：{failed}/{total} 回路补数失败（成功 {succeeded}），"
                f"checkpoint 未推进，将按指数退避自动重试。{errors}",
                severity="warning",
            )
            logger.warning(
                "断点续传部分失败（checkpoint 不推进，将安排重试）: range=%s~%s, "
                "loops=%d/%d, failed=%d, errors=%s",
                ts_start,
                ts_end,
                succeeded,
                total,
                failed,
                errors,
            )
            self._schedule_backfill_retry(gap_start)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # 任务终态与告警尽力而为（Redis 不可用时不再抛出，避免掩盖原始异常）
            if task_id is not None:
                try:
                    await task_tracker.update_status(
                        task_id,
                        TaskStatus.FAILED,
                        error_message=str(exc)[:500],
                        finished_at=datetime.now(UTC).isoformat(),
                    )
                except Exception:  # noqa: BLE001
                    pass
            try:
                await send_alert(
                    "断点续传失败",
                    f"窗口 {ts_start}~{ts_end} 补数异常: {exc}。将按指数退避自动重试。",
                    severity="warning",
                )
            except Exception:  # noqa: BLE001
                pass
            logger.error(
                "断点续传失败（不影响实时订阅，将按退避重试）: range=%s~%s, error=%s",
                ts_start,
                ts_end,
                exc,
            )
            self._schedule_backfill_retry(gap_start)
        finally:
            if lock_acquired:
                await self._release_backfill_lock(lock_token)

    async def _acquire_backfill_lock(self, token: str) -> bool:
        """SETNX 抢占 gap backfill 分布式锁（多副本防重复补数）.

        Returns:
            True 抢到锁；False 锁已被其他副本持有
        """
        try:
            ok = await redis_client.set(
                _GAP_BACKFILL_LOCK_KEY,
                token,
                nx=True,
                ex=int(settings.GAP_BACKFILL_LOCK_TTL_SECONDS),
            )
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.warning("断点续传锁抢占失败（降级为无锁执行）: %s", exc)
            return True

    async def _release_backfill_lock(self, token: str) -> None:
        """释放 gap backfill 分布式锁（CAS 校验 token，防误释放）。"""
        _RELEASE_LUA = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) else return 0 end"
        )
        try:
            await redis_client.eval(_RELEASE_LUA, 1, _GAP_BACKFILL_LOCK_KEY, token)
        except Exception as exc:  # noqa: BLE001
            logger.debug("断点续传锁释放失败（可忽略，TTL 兜底）: %s", exc)

    # ------------------------------------------------------------------
    # 补数失败重试（指数退避定时器）
    # ------------------------------------------------------------------

    def _schedule_backfill_retry(self, gap_start: float) -> None:
        """补数失败后安排延迟重试（指数退避，连接在线也生效）.

        多次失败时重试窗口起点取最早值（后失败的长窗口不会掩盖先失败的短窗口）；
        已有待执行定时器时取消重建（退避间隔按最新失败次数计算）。
        """
        if not settings.GAP_BACKFILL_ENABLED:
            return
        self._backfill_retry_count += 1
        self._retry_window_start = (
            min(self._retry_window_start, gap_start)
            if self._retry_window_start is not None
            else gap_start
        )
        delay = backfill_retry_delay(
            self._backfill_retry_count,
            base=float(settings.GAP_BACKFILL_RETRY_BASE_SECONDS),
            cap=float(settings.GAP_BACKFILL_RETRY_MAX_SECONDS),
        )
        if self._backfill_retry_task is not None and not self._backfill_retry_task.done():
            self._backfill_retry_task.cancel()
        self._backfill_retry_task = asyncio.create_task(self._retry_gap_backfill(delay))
        logger.warning(
            "断点续传重试已安排：%.0fs 后第 %d 次重试（退避上限 %.0fs，窗口起点 %s）",
            delay,
            self._backfill_retry_count,
            float(settings.GAP_BACKFILL_RETRY_MAX_SECONDS),
            datetime.fromtimestamp(self._retry_window_start, UTC).isoformat(),
        )

    def _clear_backfill_retry(self) -> None:
        """清除补数重试状态（缺口已成功补全时调用）。"""
        self._backfill_retry_count = 0
        self._retry_window_start = None
        if self._backfill_retry_task is not None and not self._backfill_retry_task.done():
            self._backfill_retry_task.cancel()
        self._backfill_retry_task = None

    async def _retry_gap_backfill(self, delay: float) -> None:
        """重试定时器：延迟后对失败缺口重新执行补数.

        重试窗口末端取触发时刻（而非原窗口末端），覆盖等待期间新产生的缺口；
        定时器触发时若已有补数在执行（如重连触发），按同延迟原地重排兜底——
        执行中的补数失败时会自行重排定时器，成功且覆盖本窗口时会清除重试状态。
        """
        try:
            await asyncio.sleep(delay)
            if not self._running:
                return
            if self._backfill_task is not None and not self._backfill_task.done():
                logger.info("断点续传重试触发时已有补数在执行，按同延迟 %.0fs 重排", delay)
                self._backfill_retry_task = asyncio.create_task(self._retry_gap_backfill(delay))
                return
            gap_start = self._retry_window_start
            if gap_start is None:
                return
            gap_end = time.time() - _GAP_BACKFILL_END_MARGIN
            if gap_end <= gap_start:
                return
            logger.warning(
                "断点续传重试触发（第 %d 次）: range=%s~%s",
                self._backfill_retry_count,
                datetime.fromtimestamp(gap_start, UTC).isoformat(),
                datetime.fromtimestamp(gap_end, UTC).isoformat(),
            )
            self._backfill_task = asyncio.create_task(self._run_gap_backfill(gap_start, gap_end))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("断点续传重试定时器异常: %s", exc)

    async def get_cached_values(self, tag_codes: list[str]) -> list[dict]:
        """从 Redis 读取缓存的实时值.

        Args:
            tag_codes: Tag 编码列表

        Returns:
            实时值列表（未缓存的 Tag 不含在结果中）
        """
        if not tag_codes:
            return []

        keys = [f"{_REDIS_KEY_PREFIX}{tc}" for tc in tag_codes]
        values = await redis_client.mget(keys)

        result: list[dict] = []
        for _tc, val in zip(tag_codes, values, strict=False):
            if val:
                try:
                    result.append(json.loads(val))
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    async def get_history_values(self, loop_part: str) -> list[dict]:
        """从 Redis 获取过去 1 小时的缓存数据（按时间升序返回）。"""
        key = f"{_REDIS_KEY_PREFIX}history:{loop_part}"
        raw_list = await redis_client.lrange(key, 0, -1)
        if not raw_list:
            return []

        result = []
        for raw in raw_list:
            try:
                result.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        # Redis lpush 导致最新数据在前面，因此需要反转列表以满足时间升序
        result.reverse()
        return result

    async def _get_loop_meta_map(self, loop_parts: list[str]) -> dict[str, tuple[str, str]]:
        """获取 loop_part → (loop_id, unit_id) 映射（带 TTL 缓存，flush 热路径不每拍查库）.

        缓存过期或存在未知 loop_part（距上次刷新超过最小间隔）时刷新；
        刷新失败时沿用旧缓存，缺失的 loop_part 回退为空串（不阻塞 flush）。
        """
        now = time.monotonic()
        stale = now - self._loop_meta_cache_at > _LOOP_META_CACHE_TTL
        has_missing = any(lp not in self._loop_meta_cache for lp in loop_parts)
        miss_due = now - self._loop_meta_cache_at > _LOOP_META_MISS_REFRESH_MIN_INTERVAL
        if stale or (has_missing and miss_due):
            try:
                await self._refresh_loop_meta_cache()
            except Exception as exc:  # noqa: BLE001
                self._loop_meta_cache_at = now  # 失败也记时间，避免每拍重试
                logger.warning("刷新 loop_part→loop_id 映射缓存失败（沿用旧缓存）: %s", exc)
        return {lp: self._loop_meta_cache.get(lp, ("", "")) for lp in loop_parts}

    async def _refresh_loop_meta_cache(self) -> None:
        """从数据库重建两个缓存（仅含活跃且有 tag 映射的回路）.

        - ``_loop_meta_cache``: loop_part(=回路台账 tag_name) → (loop_id, unit_id)
        - ``_tag_role_cache``: 测点 tag_name → (loop_part, role)

        历史 bug（2026-08-20 修复）：此前 loop_part 从「第一个测点名」rsplit('.')
        反推，但测点名用下划线分隔角色（xx_PV）→ 剥离失败且顺序不稳定，
        导致同一回路多张子表、_parse_tag_code 角色误判。现在 loop_part 唯一
        权威来源 = 回路台账 tag_name（天然不含测点角色后缀）。
        """
        from sqlalchemy import select

        from app.models.loop import LoopLedger, LoopTagMapping
        from app.models.tag import TagRegistry

        async with AsyncSessionLocal() as db:
            loop_result = await db.execute(
                select(LoopLedger.id, LoopLedger.tag_name, LoopLedger.unit_id).where(
                    LoopLedger.is_active.is_(True)
                )
            )
            loops = loop_result.all()
            if not loops:
                return
            loop_ids = [str(r[0]) for r in loops]
            mapping_result = await db.execute(
                select(LoopTagMapping.loop_id, LoopTagMapping.tag_role, TagRegistry.tag_name)
                .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
                .where(LoopTagMapping.loop_id.in_(loop_ids))
            )
            role_rows = mapping_result.all()

        # loop_id → 回路台账 tag_name（loop_part 权威来源）
        lid_to_loop_name: dict[str, str] = {}
        meta_cache: dict[str, tuple[str, str]] = {}
        for lid, tag_name, unit_id in loops:
            if not tag_name:
                continue
            lid_to_loop_name[str(lid)] = tag_name
            meta_cache.setdefault(tag_name, (str(lid), str(unit_id) if unit_id else ""))

        # 测点 tag_name → (loop_part, role)
        tag_role_cache: dict[str, tuple[str, str]] = {}
        for lid, tag_role, tag_name in role_rows:
            loop_name = lid_to_loop_name.get(str(lid))
            if loop_name and tag_name:
                tag_role_cache[tag_name] = (loop_name, str(tag_role).upper())

        self._loop_meta_cache = meta_cache
        self._tag_role_cache = tag_role_cache
        self._loop_meta_cache_at = time.monotonic()

    async def _flush_loop(self) -> None:
        """按配置间隔 flush 缓冲区到 TDengine（默认 1 秒）。"""
        while self._running:
            try:
                await asyncio.sleep(settings.TDENGINE_FLUSH_INTERVAL)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("TDengine flush 异常: %s", exc)

    async def _flush_buffer(self) -> None:
        """将缓冲区数据批量写入 Redis 1 小时缓存（及可选的 TDengine）.

        落库点 ``_last_flushed_at`` 仅在成功写入后推进：
        - writeback 启用时：TDengine 批量写入成功后才推进（重试耗尽则不推进，
          缺口保留，由 gap backfill 补全）；
        - writeback 禁用时：Redis 历史缓存写入成功后即推进。
        """
        async with self._buffer_lock:
            if not self._buffer:
                return
            buffer_copy = dict(self._buffer)
            self._buffer.clear()

        # 实时写回需携带真实 loop_id/unit_id（TDengine USING TAGS 仅子表首次创建
        # 生效，实时先行创建的子表 TAG 必须正确，否则永远为空且无法后续补写）
        loop_meta: dict[str, tuple[str, str]] = {}
        if self._writeback_enabled:
            loop_meta = await self._get_loop_meta_map(list(buffer_copy))

        # 1. 写入 Redis 1 小时缓存 (pipeline)
        pipe = redis_client.pipeline()
        tables_rows: list[dict[str, Any]] = []

        for loop_part, roles_data in buffer_copy.items():
            # 合并跨flush持久缓存：本tick buffer优先（新值覆盖旧值），
            # 未出现在本tick中的低频角色（SP/MODE/PID_*）取最近已知值，避免写NULL
            merged_roles: dict[str, Any] = dict(self._last_known.get(loop_part, {}))
            merged_roles.update(roles_data)
            row = self._build_row(merged_roles)
            row_dict = {
                "ts": row[0],
                "pv": row[1],
                "sp": row[2],
                "op": row[3],
                "mode": row[4],
                "pid_p": row[5],
                "pid_i": row[6],
                "pid_d": row[7],
                "pv_quality": row[8],
            }
            key = f"{_REDIS_KEY_PREFIX}history:{loop_part}"
            pipe.lpush(key, json.dumps(row_dict))
            # 保留 75 分钟（1Hz×4500 点）：整点 KPI 任务计算"上一完整小时"，
            # 需覆盖 [H-1, H)，恰 3600 点只有 ~60s 迟到余量，4500 点给出 ~15 分钟余量
            pipe.ltrim(key, 0, 4499)
            pipe.expire(key, 7200)

            # 为 TDengine 准备数据
            if self._writeback_enabled:
                subtable = make_subtable_name(loop_part)
                loop_id, unit_id = loop_meta.get(loop_part, ("", ""))
                tables_rows.append(
                    {
                        "subtable": subtable,
                        "loop_id": loop_id,
                        "unit_id": unit_id,
                        "rows": [row],
                    }
                )

        redis_ok = True
        try:
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            redis_ok = False
            logger.warning("Redis 历史数据写入失败: %s", exc)

        # 2. 批量写入 TDengine (如果启用)
        tdengine_ok = True
        if tables_rows:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    count = await batch_insert_multi(tables_rows)
                    logger.debug("批量写入 %d 行到 %d 个子表", count, len(tables_rows))
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt < max_retries - 1:
                        wait = 0.5 * (2**attempt)  # 0.5s, 1s, 2s
                        logger.warning(
                            "批量写入失败 (尝试 %d/%d): %s，%gs 后重试",
                            attempt + 1,
                            max_retries,
                            exc,
                            wait,
                        )
                        await asyncio.sleep(wait)
                    else:
                        tdengine_ok = False
                        logger.error("批量写入最终失败 (%d 个子表): %s", len(tables_rows), exc)

        # 落库点推进：writeback 启用时需 TDengine 成功；禁用时需 Redis 成功
        flush_succeeded = tdengine_ok if self._writeback_enabled else redis_ok
        if flush_succeeded and self._last_data_at is not None:
            self._last_flushed_at = self._last_data_at

        # 断点续传 checkpoint 持久化（节流，进程重启后据此恢复缺口起点）
        await self._maybe_save_checkpoint()

    def _build_row(self, roles_data: dict[str, dict]) -> tuple:
        """构造单行数据。

        列顺序: ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality
        对应 st_loop_data 超级表 schema。

        时间戳经显式 astimezone 到目标时区（Asia/Shanghai），
        消除 naive datetime 在 TDengine 侧的 8h 偏移风险。
        """
        # 行时间戳统一取 PV 角色的 collectTime（PV 为高频基准，保证行 ts 与 PV 值
        # 同源，避免取到低频角色（如 PID 参数）的滞后时间戳）；PV 缺失时回退到任一角色
        ts_str = roles_data.get("PV", {}).get("ts", "")
        if not ts_str:
            for role_data in roles_data.values():
                ts_str = role_data.get("ts", "")
                if ts_str:
                    break
        # 显式时区转换：统一到 _TARGET_TZ（Asia/Shanghai），格式化为 naive 字符串
        ts_str = _normalize_ts(ts_str)

        # 提取各角色值（缺失值用 None，_format_row 会转为 NULL）
        pv_val = self._parse_float(roles_data.get("PV", {}).get("value"))
        sp_val = self._parse_float(roles_data.get("SP", {}).get("value"))
        op_val = self._parse_float(roles_data.get("OP", {}).get("value"))
        mode_val = self._parse_int(roles_data.get("MODE", {}).get("value"))
        pid_p_val = self._parse_float(roles_data.get("PID_P", {}).get("value"))
        pid_i_val = self._parse_float(roles_data.get("PID_I", {}).get("value"))
        pid_d_val = self._parse_float(roles_data.get("PID_D", {}).get("value"))
        pv_quality_val = self._parse_int(roles_data.get("PV", {}).get("quality"))

        return (
            ts_str,
            pv_val,
            sp_val,
            op_val,
            mode_val,
            pid_p_val,
            pid_i_val,
            pid_d_val,
            pv_quality_val,
        )

    @staticmethod
    def _parse_float(v: Any) -> float | None:
        """安全解析 float，无效值返回 None。"""
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_int(v: Any) -> int | None:
        """安全解析 int，无效值返回 None。"""
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None


# 全局单例
_subscriber: RealtimeSubscriber | None = None


def get_subscriber() -> RealtimeSubscriber:
    """获取全局 RealtimeSubscriber 单例."""
    global _subscriber
    if _subscriber is None:
        _subscriber = RealtimeSubscriber()
    return _subscriber


def _build_refresh_payload(source: str, request_id: str | None) -> str:
    """构造控制频道刷新指令 JSON."""
    return json.dumps(
        {
            "type": "refresh",
            "requestId": request_id,
            "source": source,
            "requestedAt": datetime.now(UTC).isoformat(),
        },
        ensure_ascii=False,
    )


async def notify_subscription_changed(source: str) -> None:
    """发布订阅刷新通知（fire-and-forget，失败仅记日志不影响业务主流程）.

    在测点/回路/绑定关系变更写路径提交后调用；Leader 进程监听控制频道，
    收到后重查活跃 Tag 并在各分片现有连接上重发自身位号订阅（免重启生效）。
    """
    try:
        await redis_client.publish(_CONTROL_CHANNEL, _build_refresh_payload(source, None))
    except Exception as exc:  # noqa: BLE001
        logger.warning("发布订阅刷新通知失败（不影响业务，source=%s）: %s", source, exc)


async def request_subscription_refresh(*, timeout: float = 15.0, interval: float = 0.5) -> dict:
    """API 侧发起订阅刷新并轮询等待 Leader 执行结果.

    流程：预检（订阅启用/订阅器运行）→ 清除上一轮结果 key → 发布带 requestId 的
    刷新指令 → 每 interval 秒轮询结果 key，requestId 匹配即返回。

    Returns:
        Leader 写入的结果 dict（含 added/removed/invocationId/leaderPid，
        Leader 侧执行失败时 error 字段非空）。

    Raises:
        BizError: ERR_SIGNALR_DISABLED（订阅已禁用）/
                  ERR_SUBSCRIBER_NOT_RUNNING（订阅器未运行）/
                  ERR_REDIS_UNAVAILABLE（Redis 不可用）/
                  ERR_SUBSCRIPTION_REFRESH_TIMEOUT（超时无响应，Leader 可能在重连/选举）
    """
    if not settings.SIGNALR_ENABLED:
        raise BizError(
            code="ERR_SIGNALR_DISABLED",
            message="实时订阅已禁用（SIGNALR_ENABLED=False），请在链路配置中启用并重启后端",
            status_code=400,
        )
    if not get_subscriber()._running:
        raise BizError(
            code="ERR_SUBSCRIBER_NOT_RUNNING",
            message="实时订阅器未运行（SIGNALR_HUB_URL 未配置或启动失败）",
            status_code=400,
        )

    request_id = uuid4().hex
    try:
        # 先清掉上一轮结果，防止读到旧结果；再发布刷新指令
        await redis_client.delete(_REFRESH_RESULT_KEY)
        await redis_client.publish(
            _CONTROL_CHANNEL, _build_refresh_payload("manual-api", request_id)
        )
    except Exception as exc:  # noqa: BLE001
        raise BizError(
            code="ERR_REDIS_UNAVAILABLE",
            message=f"Redis 不可用，无法发起订阅刷新: {exc}",
            status_code=503,
        ) from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            raw = await redis_client.get(_REFRESH_RESULT_KEY)
        except Exception:  # noqa: BLE001
            continue
        if not raw:
            continue
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # requestId 匹配才是本次请求的结果（事件驱动的刷新结果 requestId=None，跳过）
        if result.get("requestId") == request_id:
            return result
    raise BizError(
        code="ERR_SUBSCRIPTION_REFRESH_TIMEOUT",
        message=(
            f"订阅刷新超时（{timeout:.0f}s 无响应）：订阅 Leader 可能正在重连或选举中，请稍后重试"
        ),
        status_code=504,
    )


async def start_subscriber() -> None:
    """启动实时数据订阅（应用启动时调用）."""
    sub = get_subscriber()
    await sub.start()


async def stop_subscriber() -> None:
    """停止实时数据订阅（应用关闭时调用）."""
    global _subscriber
    if _subscriber is not None:
        await _subscriber.stop()
        _subscriber = None
