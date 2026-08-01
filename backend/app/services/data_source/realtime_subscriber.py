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

时区显式转换（WS-B2）:
- ``_build_row`` 对 collectTime 显式 astimezone 到目标时区（Asia/Shanghai），
  消除 naive datetime 在 TDengine 侧的 8h 偏移风险。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import websockets

from app.core.config import settings
from app.core.db import AsyncSessionLocal
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

# 看门狗 recv 超时（秒）—— 周期性检查消息停滞，超时即断开重连
_WATCHDOG_RECV_TIMEOUT = 30.0

# SP/MODE 等低频信号刷新间隔（秒）—— AAS 仅在值变化时推送 updateRealValues，
# SP/MODE/PID 变化频率低，需定期重新调用 SubscribeAsync 获取 Completion 响应中的
# 当前值，刷新 Redis 缓存（TTL 3600s），避免低频信号过期后前端显示空白。
_SIGNALR_REFRESH_INTERVAL = 300.0  # 5 分钟

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
        self._ws: Any = None
        self._running = False
        self._subscribed_tags: set[str] = set()
        self._invocation_counter: int = 0  # SignalR invocationId 计数器
        self._buffer: dict[
            str, dict[str, Any]
        ] = {}  # {loop_part: {role: {"value": ..., "quality": ..., "ts": ...}}}
        self._buffer_lock = asyncio.Lock()
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
        self._task = asyncio.create_task(self._run())
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info(
            "实时数据订阅任务已启动 (hub=%s, writeback=%s)",
            settings.SIGNALR_HUB_URL,
            self._writeback_enabled,
        )

    async def stop(self) -> None:
        """停止订阅."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
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
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        # 停止前 flush 剩余数据
        await self._flush_buffer()
        logger.info("实时数据订阅已停止")

    async def _run(self) -> None:
        """主循环：连接 → 订阅 → 接收 → 重连（指数退避）."""
        base_delay = float(settings.SIGNALR_RECONNECT_INTERVAL)
        max_delay = float(settings.SIGNALR_RECONNECT_MAX_INTERVAL)
        delay = base_delay
        connected_at = 0.0
        while self._running:
            try:
                connected_at = time.monotonic()
                await self._connect_and_subscribe()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                # 连接健康存活超过 60s 才视为稳定连接，重置退避到 base；
                # 否则先按当前退避等待，再翻倍（base → ×2 → … → max 封顶），
                # 避免远端 Hub 不可用时固定小间隔重连持续施压
                if time.monotonic() - connected_at > 60:
                    delay = base_delay
                logger.warning("实时数据订阅异常: %s，%.0fs 后重连", exc, delay)
                await asyncio.sleep(delay)
                delay = next_reconnect_delay(delay, cap=max_delay)
            finally:
                # 确保旧连接在任何情况下都被关闭，防止服务器侧 CLOSE_WAIT 堆积
                await self._close_ws_safely()

    async def _close_ws_safely(self) -> None:
        """安全关闭 WebSocket 连接（幂等）。"""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("关闭旧 WebSocket 连接时异常（可忽略）: %s", exc)
            finally:
                self._ws = None

    async def _connect_and_subscribe(self) -> None:
        """连接 Hub 并订阅数据.

        SignalR JSON Hub Protocol 流程：
        1. WebSocket 连接
        2. 发送握手 {"protocol":"json","version":1}\\x1e
        3. 接收握手响应 {}\\x1e（成功）或 {"error":"..."}\\x1e（失败）
        4. 之后所有消息以 \\x1e (Record Separator) 分帧
        """
        # 先关闭残留的旧连接，防止泄漏
        await self._close_ws_safely()

        self._ws = await websockets.connect(
            settings.SIGNALR_HUB_URL,
            ping_interval=settings.SIGNALR_PING_INTERVAL,
            ping_timeout=settings.SIGNALR_PING_TIMEOUT,
            open_timeout=settings.SIGNALR_OPEN_TIMEOUT,
        )
        logger.info("已连接实时数据 Hub: %s", settings.SIGNALR_HUB_URL)

        # SignalR 协议握手
        handshake_msg = json.dumps({"protocol": "json", "version": 1}) + "\x1e"
        await self._ws.send(handshake_msg)
        raw = await self._ws.recv()
        handshake = json.loads(raw.rstrip("\x1e"))
        if "error" in handshake:
            raise ConnectionError(f"SignalR 握手失败: {handshake['error']}")
        logger.info("SignalR 握手成功")

        # 查询全部活跃 Tag 并订阅
        tag_codes = await self._get_active_tags()
        if not tag_codes:
            logger.info("无活跃 Tag，等待数据...")
            await asyncio.sleep(30)
            return

        # 发送订阅请求（标准 SignalR JSON Hub Protocol: type=1 Invocation）
        # 必须包含 invocationId，否则 AAS 将调用视为 fire-and-forget，不返回
        # Completion (type=3) 响应——初始响应中包含所有订阅 Tag 的当前值
        # （含 SP/MODE/PID 等低频信号），缺少 invocationId 会导致这些值永远缺失。
        self._invocation_counter += 1
        invocation_id = f"sub_{self._invocation_counter}"
        subscribe_msg = (
            json.dumps(
                {
                    "type": 1,
                    "invocationId": invocation_id,
                    "target": "SubscribeAsync",
                    "arguments": [tag_codes],
                }
            )
            + "\x1e"
        )
        await self._ws.send(subscribe_msg)
        logger.info("已订阅 %d 个 Tag (invocationId=%s)", len(tag_codes), invocation_id)
        self._subscribed_tags = set(tag_codes)

        # 断点续传：连接成功（重连/进程重启后首连）即检测数据缺口并自动补数
        await self._maybe_trigger_gap_backfill()

        # 接收初始响应（Completion: type=3, result 包含 {code, data}）
        # 一帧可能包含多条 \x1e 分隔的消息（Completion + 首批 push）
        raw = await self._ws.recv()
        for part in raw.split("\x1e"):
            if not part:
                continue
            try:
                initial = json.loads(part)
            except json.JSONDecodeError:
                continue
            await self._handle_signalr_message(initial)

        # 持续接收推送（一条 WebSocket 帧可能包含多条 \x1e 分隔的消息）
        # 数据停滞看门狗：以 _WATCHDOG_RECV_TIMEOUT 超时 recv 代替 async for，
        # 超时后检查距上次消息是否超过 SIGNALR_STALL_TIMEOUT_SECONDS，
        # 超过则主动断开重连（覆盖"WS 活着但上游停推"盲区）
        stall_timeout = float(settings.SIGNALR_STALL_TIMEOUT_SECONDS)
        while self._running and self._ws is not None:
            try:
                raw_message = await asyncio.wait_for(
                    self._ws.recv(), timeout=_WATCHDOG_RECV_TIMEOUT
                )
            except TimeoutError:
                # recv 超时：检查数据停滞
                if self._last_data_at is not None:
                    idle = time.time() - self._last_data_at
                    if idle >= stall_timeout:
                        logger.warning(
                            "数据停滞看门狗触发：%.0fs 无消息（阈值 %.0fs），主动断开重连",
                            idle,
                            stall_timeout,
                        )
                        await self._close_ws_safely()
                        return
                continue
            for part in raw_message.split("\x1e"):
                if not part:
                    continue
                try:
                    msg = json.loads(part)
                    await self._handle_signalr_message(msg)
                except json.JSONDecodeError:
                    logger.warning("收到非 JSON 消息: %s", part[:100])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("处理实时数据消息失败: %s", exc)

    async def _handle_signalr_message(self, msg: dict) -> None:
        """统一处理 SignalR JSON 协议消息.

        支持的消息类型：
        - type=3 Completion: SubscribeAsync 的返回值，result 包含 {code, data}，
          data 为所有订阅 Tag 的当前值（含 SP/MODE/PID 等低频信号）
        - type=1 Invocation (target=updateRealValues): 服务端推送的实时值变化
        - type=6 Ping: 心跳，需回复 type=6 Pong
        - 自定义格式 {code:200, data:[...]}: 兼容非标准 SignalR 响应
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

        # type=6 Ping — 回复 Pong
        if msg_type == 6:
            if self._ws is not None:
                await self._ws.send(json.dumps({"type": 6}) + "\x1e")
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
        """周期刷新低频信号（SP/MODE/PID）当前值.

        AAS 仅在值变化时推送 updateRealValues，SP/MODE/PID 等低频信号变化少，
        需定期重新调用 SubscribeAsync 获取 Completion 响应中的当前值，
        刷新 Redis 缓存（TTL 3600s），避免低频信号过期后前端显示空白。

        刷新通过同一 WebSocket 连接发送 invocation，Completion 响应由
        ``_connect_and_subscribe`` 的接收循环经 ``_handle_signalr_message`` 处理。
        """
        while self._running:
            try:
                await asyncio.sleep(_SIGNALR_REFRESH_INTERVAL)
                if not self._running or self._ws is None or not self._subscribed_tags:
                    continue
                # 重新发送 SubscribeAsync 获取所有订阅 Tag 的当前值
                # AAS 会返回 Completion (type=3) 响应，由接收循环统一处理
                self._invocation_counter += 1
                invocation_id = f"refresh_{self._invocation_counter}"
                refresh_msg = (
                    json.dumps(
                        {
                            "type": 1,
                            "invocationId": invocation_id,
                            "target": "SubscribeAsync",
                            "arguments": [list(self._subscribed_tags)],
                        }
                    )
                    + "\x1e"
                )
                await self._ws.send(refresh_msg)
                logger.debug(
                    "已发送周期刷新订阅请求 (%d tags, invocationId=%s)",
                    len(self._subscribed_tags),
                    invocation_id,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("周期刷新订阅请求失败（可忽略，下个周期重试）: %s", exc)

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

        # 写入 Redis
        key = f"{_REDIS_KEY_PREFIX}{tag_code}"
        payload = {
            "tagCode": tag_code,
            "value": item.get("value", ""),
            "quality": item.get("quality", 0),
            "collectTime": item.get("collectTime", ""),
        }
        value = json.dumps(payload)
        await redis_client.setex(key, _REDIS_TTL, value)

        # Pub/Sub 广播给 WebSocket 端点
        await redis_client.publish(_PUBSUB_CHANNEL, value)

        # 放入内部缓冲区（供 _flush_buffer 写入 Redis 1 小时缓存及可选的 TDengine）
        loop_part, role = self._parse_tag_code(tag_code)
        if loop_part:
            async with self._buffer_lock:
                if loop_part not in self._buffer:
                    self._buffer[loop_part] = {}
                self._buffer[loop_part][role] = {
                    "value": item.get("value"),
                    "quality": item.get("quality"),
                    "ts": item.get("collectTime", ""),
                }

        # MODE 变化时主动失效回路统计缓存（loop:stats:type:*），确保监控页
        # 自动/手动/自控率卡片下次查询拿到最新值，而非等 60s TTL 自然过期。
        # MODE 低频变化（小时级），失效代价低。
        if role == "MODE":
            asyncio.create_task(self._invalidate_loop_stats_cache())

    async def _invalidate_loop_stats_cache(self) -> None:
        """MODE 变化时失效回路统计缓存，确保监控卡片下次查询拿到最新值."""
        try:
            async for key in redis_client.scan_iter(match="loop:stats:type:*"):
                await redis_client.delete(key)
        except Exception as exc:  # noqa: BLE001
            # 失败可忽略：60s TTL 自然过期，最多延迟 1 分钟
            logger.debug("失效 loop 统计缓存失败（可忽略，TTL 60s 自然过期）: %s", exc)

    def _parse_tag_code(self, tag_code: str) -> tuple[str, str]:
        """解析 tagCode 为回路部分和角色。

        示例: "LIC-101.PV" → ("LIC-101", "PV")
              "LIC-101" → ("LIC-101", "PV")
        """
        if "." in tag_code:
            loop_part, role = tag_code.rsplit(".", 1)
            return loop_part, role.upper()
        return tag_code, "PV"

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
        """从数据库重建 loop_part → (loop_id, unit_id) 缓存（仅含活跃且有 tag 映射的回路）."""
        from sqlalchemy import select

        from app.models.loop import LoopLedger
        from app.services.data_import import _batch_get_loop_data

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(LoopLedger.id).where(LoopLedger.is_active.is_(True)))
            loop_ids = [str(row[0]) for row in result.all()]
            data_map = await _batch_get_loop_data(db, loop_ids) if loop_ids else {}

        cache: dict[str, tuple[str, str]] = {}
        for lid, meta in data_map.items():
            role_tag_map = meta.get("role_tag_map") or {}
            if not role_tag_map:
                continue
            first_tag = next(iter(role_tag_map.values()))
            loop_part = first_tag.rsplit(".", 1)[0] if "." in first_tag else first_tag
            cache.setdefault(loop_part, (lid, meta.get("unit_id") or ""))
        self._loop_meta_cache = cache
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
            row = self._build_row(roles_data)
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
