"""实时数据订阅器 — WebSocket 客户端.

连接模拟 SignalR Hub（``/signalr/realValueForClpmHub``），订阅全部活跃 Tag，
将实时值缓存到 Redis，供 API 查询。

消息协议遵循 RealDATA_API.md：
- 发送: {"method": "SubscribeAsync", "args": [["TAG001", "TAG002"]]}
- 接收: {"event": "updateRealValues", "data": [{"tagCode": "...", "value": "...", ...}]}
- 初始响应: {"code": 200, "data": [...]}

Redis 缓存:
- key: ``realtime:{tagCode}``
- value: JSON ``{"value": "12.5", "quality": 0, "collectTime": "...",
  "valueValid": true, "recvAt": "...", "stale": false}``（后三字段为
  2026-09-06 整改增量可选字段，消费侧容错缺省）
- TTL: 60 秒（超时自动清除过期数据）

采集路径重排（R03/R06，2026-09-06 整改）:
- ``_cache_value`` 不再逐点 await Redis——①轻量校验（app/core/numeric 共享
  契约，无效值计 points_invalid、value 置 None 但质量/时间照常记录）→
  ②同步段写入历史缓冲/last_known → ③显示快照（SETEX+PUBLISH）进入有界
  "每 tag 最新值"待发字典，由 ``_display_flush_loop`` 按 ≤200ms 或 ≤256
  命令组批 pipeline 发送（逐项异常隔离+计 cache_write_failed）；
- Redis 故障只损失显示新鲜度，绝不阻断 TDengine 历史缓冲。

断点续传（Gap Backfill）:
- 每次收到数据更新内存 ``_last_data_at``，并由 ``_flush_buffer`` 节流持久化到
  Redis checkpoint（``realtime:gap:last_data_ts``，epoch 秒）；
- R08（2026-09-06 整改）：**每次分片建连成功**都按回路核对缺口——per-loop
  已确认落库水位（``realtime:gap:loop_wm`` hash，loop_part → 行 ts epoch，
  该回路行进入成功批的最大行 ts，30s 节流持久化）优先，尚无水位的回路以
  全局 checkpoint 兜底（进程重启首连防漏检）；稳定来源身份 = loop_part
  （不按分片物理编号，reshard 不串位）。超 ``GAP_BACKFILL_MIN_GAP_SECONDS``
  的回路集合登记到持久待补列表（``realtime:gap:pending``，重叠合并去重）；
  开关开启时仅经 ``data_import.import_history_data``（skip 策略）消费补全并
  触发受影响小时的 KPI 回算，成功才出队+推进水位；开关关闭只登记不调远端；
- 单次补数窗口上限 ``GAP_BACKFILL_MAX_HOURS``，超出部分截断并告警，需手工导入；
- checkpoint 条件推进：仅补数全部成功（failed==0）才推进 checkpoint，
  部分失败/异常时缺口保留，并启动延迟重试定时器
  （``GAP_BACKFILL_RETRY_BASE_SECONDS`` 起步指数退避，
  上限 ``GAP_BACKFILL_RETRY_MAX_SECONDS``，连接在线也生效；重试优先消费
  持久待补列表）；空返回≠完整——failed==0 即推进但计 ``backfill_empty_windows``；
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
  崩溃/退出后其他进程在 TTL 内接管。
- Redis 异常三态语义（R04，2026-09-06 整改）：待命者抢锁异常 → 保持待命，
  **绝不因异常成为 Leader**；现任者续租异常 → 保持现状但租约期限
  （``lease_expires_at`` = 最近一次确认成功时刻 + TTL）不延长，超出租约仍
  无法确认持有 → 退位停止接收/写回并登记控制面故障窗口（lease_lost_windows）。

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
from app.core.numeric import finite_or_none, parse_finite_float, parse_mode_int
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

# ---------------------------------------------------------------------------
# 数据链路整改 S1 常量（R03/R04/R07/R09/R10，契约见
# docs/过程文档/2026-09-06-data-pipeline-remediation-s0-contract.md）
# ---------------------------------------------------------------------------

# R03：显示快照（SETEX+PUBLISH）批量发送参数——接收路径绝不 await Redis，
# 快照进入有界"每 tag 最新值"待发字典，由独立任务按 ≤200ms 节拍或 ≤256 命令
# （每项 2 命令 × 128 项）组批 pipeline 发送；Redis 故障只损失显示新鲜度
_DISPLAY_FLUSH_INTERVAL = 0.2
_DISPLAY_BATCH_MAX_ITEMS = 128  # 每项 SETEX+PUBLISH 共 2 命令 → 单批 ≤256 命令
_DISPLAY_FLUSH_MAX_BACKOFF = 2.0  # 连续失败时的发送退避上限（秒）

# R07：TDengine 批次行数上限（分块成功独立记录）；未确认窗口重试缓冲上限
_TD_BATCH_MAX_ROWS = 500
_MAX_UNCONFIRMED_WINDOWS = 10  # 进程内登记的未确认窗口数上限（超出仅记录不重试）
_MAX_RETRY_ROWS_PER_WINDOW = 2000  # 单窗口进入重试缓冲的 TD 行数上限

# R10：订阅 invocation 发出后首个响应（Completion 初始快照）的等待超时（秒）。
# 需覆盖大订阅量（每片 1000 位号、分块订阅）下服务端生成与回发快照的时延，
# 过短会在健康分片上误触发重连风暴；超时走既有片级退避重连
_FIRST_RESPONSE_TIMEOUT = 30.0

# ---------------------------------------------------------------------------
# 数据链路整改 S2 常量（R02/R05/R08，契约见
# docs/过程文档/2026-09-06-data-pipeline-remediation-s0-contract.md §4/§5）
# ---------------------------------------------------------------------------

# R02：Redis 历史缓存 key 前缀与整键 TTL（秒）。TTL 为"最后一次写入后 2 小时
# 删除整键"，非逐点时间窗；点数上限由 REALTIME_HISTORY_MAX_POINTS_PER_LOOP
# （LTRIM）约束，全局字节预算由 REALTIME_HISTORY_GLOBAL_BUDGET_BYTES 约束
_HISTORY_KEY_PREFIX = f"{_REDIS_KEY_PREFIX}history:"
_HISTORY_TTL_SECONDS = 7200
# R02：预算跟踪的内建 TTL 模型清扫节拍（秒）——按内存记录的 key 过期时刻
# 近似扣减字节（与 Redis EXPIRE 语义一致：每次写入刷新整键 TTL）
_HISTORY_SWEEP_INTERVAL = 60.0

# R08：per-loop 已确认落库水位（hash：loop_part → 行 ts epoch 秒字符串）。
# 稳定来源身份 = loop_part（**不按分片物理编号**——reshard 重建分片后身份
# 不串位）；节流持久化与既有 checkpoint 同风格（30s）
_GAP_LOOP_WM_KEY = "realtime:gap:loop_wm"
# R08：持久待补缺口列表（list of JSON：{loops, start, end, registeredAt}）。
# 补数成功才出队；失败/崩溃条目保留，重启后仍可见（消费侧按水位覆盖检查收敛）
_GAP_PENDING_KEY = "realtime:gap:pending"


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


def _json_safe_value(raw: Any) -> Any:
    """显示载荷 value 字段 JSON 安全化（R06 出口守卫）.

    非有限数值（NaN/Infinity/溢出）折算 None——配合 ``json.dumps(allow_nan=False)``
    保证 SETEX/PUBLISH 载荷恒为合法 JSON；字符串字面量（含 "-1.#QNAN0"）原样
    保留，由消费侧按 valueValid 判定有效性。
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int | float):
        return finite_or_none(raw)
    return raw


def _parse_known_ts(ts_str: Any) -> datetime | None:
    """解析时间戳为 _TARGET_TZ 感知时刻；空/不可解析返回 None（不伪造 now）.

    R05（S0 契约 §4.1）：行时间与角色状态只使用**确有输入且可解析**的
    sourceTime——``_normalize_ts`` 的 now() 回退仅保留给"解析失败但确有输入"
    的显式登记场景，行构建/迟到判定一律走本函数（None = 未知）。
    """
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TARGET_TZ)
    return dt.astimezone(_TARGET_TZ)


def _format_target_ts(dt: datetime) -> str:
    """格式化为目标时区 naive 字符串（毫秒精度，TDengine 兼容）."""
    return dt.astimezone(_TARGET_TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _collect_role_source_times(roles_data: dict[str, Any]) -> dict[str, datetime]:
    """收集行内各角色的已知 sourceTime（空/不可解析 → 不参与，绝不伪造 now）."""
    known: dict[str, datetime] = {}
    for role, entry in roles_data.items():
        if not isinstance(entry, dict):
            continue
        dt = _parse_known_ts(entry.get("ts"))
        if dt is not None:
            known[role] = dt
    return known


def _normalize_ts(ts_str: str) -> str:
    """将时间戳字符串显式转换到目标时区（Asia/Shanghai），返回 naive 格式字符串.

    - 带时区（含 Z 后缀）：astimezone 到 _TARGET_TZ
    - naive（无时区）：视为已在 _TARGET_TZ
    - 空或解析失败：取当前 _TARGET_TZ 时间

    返回格式 ``YYYY-MM-DD HH:MM:SS.fff``（毫秒精度，TDengine 兼容）。
    R05 后该 now() 回退仅用于显式登记场景；行构建/迟到判定/去重改用
    ``_parse_known_ts``（未知即 None，不伪造时间）。
    """
    dt = _parse_known_ts(ts_str)
    if dt is None:
        dt = datetime.now(_TARGET_TZ)
    return _format_target_ts(dt)


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

# 数据链路统一计数器键（S0 契约 §8，S4 对账口径）。leader_epoch/buffer_rows_pending
# 为 Gauge（当前值），其余为累计值；history_dup_dropped/history_budget_exceeded/
# late_rejected/rows_dropped_no_ts 由 S2/A 落地；backfill_empty_windows/
# gap_windows_registered 为 R08 补充观测计数（映射在 S2 报告登记）。
_METRIC_KEYS: tuple[str, ...] = (
    "msgs_received",
    "points_received",
    "points_invalid",
    "late_rejected",
    "unbound_tag_msgs",
    "rows_dropped_no_ts",
    "cache_write_failed",
    "history_dup_dropped",
    "history_budget_exceeded",
    "buffer_rows_pending",
    "rows_written",
    "rows_failed",
    "unconfirmed_windows",
    "leader_epoch",
    "lease_lost_windows",
    "backfill_empty_windows",
    "gap_windows_registered",
)


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
        # R11 绑定代次：loop_part → {role → 绑定源 tag} 反查（ingest 双向校验用）
        self._loop_role_tags: dict[str, dict[str, str]] = {}
        # R11 绑定代次：loop_part → 绑定版本号（绑定变化 epoch+1，旧代次状态清理）
        self._loop_epochs: dict[str, int] = {}
        # 多 worker 进程订阅单例（Leader 锁）状态
        self._leader_task: asyncio.Task | None = None  # Leader 锁维护循环（抢锁/续期）
        self._leader_token: str = ""  # 本进程锁 token（hostname:pid:monotonic_ns）
        self._is_leader = False  # 当前是否持有 Leader 锁（仅持锁进程真正订阅）
        # R04 租约：最近一次确认持有（抢锁/续租成功）时刻 + TTL；续租异常不续期，
        # 超出该期限仍无法确认持有 → 退位停止接收/写回并登记控制面故障窗口
        self._lease_expires_at: float | None = None
        self._leader_epoch: int = 0  # Leader 代次（每次接管 +1，计数器 leader_epoch）
        # 订阅刷新控制频道监听任务（仅 Leader 进程运行，随 Leadership 切换启停）
        self._control_task: asyncio.Task | None = None
        # 连接池共享状态：当前分片列表（refresh_subscription 用）与重建信号
        self._shard_states: list[_ShardState] = []
        self._rebuild_event: asyncio.Event = asyncio.Event()
        # R03 显示快照待发字典：tag → (redis_key, payload_json)，仅存每 tag 最新值
        # （有界 ≤ 活跃 tag 数），由 _display_flush_loop 批量发送
        self._display_pending: dict[str, tuple[str, str]] = {}
        self._display_pending_lock = asyncio.Lock()
        self._display_flush_task: asyncio.Task | None = None
        self._display_flush_backoff = 0.0  # 连续失败时的发送退避（秒）
        # R07 未确认窗口（失败批次有界重试缓冲）：
        # [{"start", "end", "td_tables": [...], "history": [(key, row_json, row_ts), ...]}]
        self._unconfirmed_windows: list[dict[str, Any]] = []
        self._confirmed_boundary: float | None = None  # 已确认成功批的最大接收边界
        # R08 per-loop 已确认落库水位：loop_part → 行 ts epoch 秒（行进入成功批的
        # 最大行 ts；内存推进 + Redis hash 节流持久化，缺口检测优先于全局 checkpoint）
        self._loop_watermarks: dict[str, float] = {}
        self._loop_wm_loaded = False  # 是否已从 Redis 加载过水位（懒加载一次）
        self._last_wm_write = 0.0  # 上次水位持久化的 monotonic 时间（30s 节流）
        # R08 持久待补缺口：Redis list `realtime:gap:pending`；_inflight_gap_entry
        # 记录当前消费中的条目快照（注册侧不与在途窗口合并，避免执行中窗口被扩展）
        self._inflight_gap_entry: dict[str, Any] | None = None
        # R08：缺口检测前绑定映射刷新的节流（monotonic，DB 故障时防每次重连打库）
        self._gap_meta_refresh_at = 0.0
        # R02 历史缓存三重限制的跟踪状态（Leader 单写者，进程内有效）：
        # - 写入前去重水位：loop_part → 最近已推送行 ts（规范化字符串，字典序即时间序）；
        #   重启后首次 flush 经 LINDEX 0 懒加载回填
        self._last_pushed_row_ts_map: dict[str, str | None] = {}
        self._last_pushed_loaded: set[str] = set()
        # - 全局字节预算近似跟踪：全部 history 键 JSON 载荷字节的近似值
        #   （写入累加、LTRIM 按均值扣减、TTL 过期按内存模型清扫；误差登记于 S2 报告）
        self._history_bytes_total = 0
        self._history_key_bytes: dict[str, int] = {}
        self._history_key_rows: dict[str, int] = {}
        self._history_key_expire_at: dict[str, float] = {}
        self._last_history_sweep = 0.0
        # 数据链路统一计数器（S0 契约 §8）
        self._metrics: dict[str, int] = dict.fromkeys(_METRIC_KEYS, 0)

    def _incr(self, name: str, n: int = 1) -> None:
        """递增内存计数器（周期日志输出，见 _flush_loop；不引入新依赖）."""
        self._metrics[name] = self._metrics.get(name, 0) + n

    @staticmethod
    def _update_is_newer(existing: dict[str, Any], new: dict[str, Any]) -> bool:
        """R05 逐角色确定性接受规则（S0 契约 §4.1）.

        新到更新当且仅当 ``sourceTime > 已存 sourceTime``，或
        ``sourceTime == 已存 sourceTime 且 recvAt ≥ 已存 recvAt`` 时接受；
        否则拒绝（不回退已存状态）。推论：

        - 新 sourceTime 未知而已存已知 → 拒绝（未知不得回退已知时间）；
        - 两者均未知 → 按 recvAt ≥ 接受（同 ts 同值幂等口径）；
        - 已存未知而新已知 → 接受（状态从未知改善为已知）。
        """
        new_dt = _parse_known_ts(new.get("ts"))
        old_dt = _parse_known_ts(existing.get("ts"))
        new_recv = float(new.get("recvAt") or 0.0)
        old_recv = float(existing.get("recvAt") or 0.0)
        if new_dt is None:
            if old_dt is not None:
                return False
            return new_recv >= old_recv
        if old_dt is None:
            return True
        if new_dt > old_dt:
            return True
        if new_dt == old_dt:
            return new_recv >= old_recv
        return False

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
        # 显示快照尽力发送一次（best-effort，失败不影响停止流程）
        try:
            await self._flush_display_pending()
        except Exception:  # noqa: BLE001
            pass
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
        """持有 Leader 锁：启动订阅主任务 / flush / 周期刷新 / 控制频道监听 / 显示批量发送任务."""
        self._is_leader = True
        # R04：接管即建立租约（正常路径 acquire 成功时已设；此处兜底防直接调用
        # 造成无租约 Leader——过期检查依赖该值）
        if self._lease_expires_at is None:
            try:
                self._lease_expires_at = time.time() + float(
                    settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS
                )
            except (TypeError, ValueError):  # pragma: no cover - 配置异常兜底
                self._lease_expires_at = time.time() + 30.0
        self._leader_epoch += 1
        self._metrics["leader_epoch"] = self._leader_epoch
        self._task = asyncio.create_task(self._run())
        # 主任务意外退出观测（2026-08-21 事故：远端引擎重启掐断连接后
        # 主任务静默死亡，看门狗随之失效，无任何日志可查）
        self._task.add_done_callback(self._on_main_task_done)
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._control_task = asyncio.create_task(self._control_loop())
        # R03：显示快照批量发送任务（接收路径不再 await Redis）
        self._display_flush_task = asyncio.create_task(self._display_flush_loop())
        logger.warning("本进程已接管实时数据订阅（Leader）: token=%s", self._leader_token)

    async def _resign_leader(self) -> None:
        """失去/释放 Leader 锁：取消订阅主任务/flush/周期刷新/控制监听/显示批量发送任务（幂等）."""
        self._is_leader = False
        for attr in (
            "_task",
            "_flush_task",
            "_refresh_task",
            "_control_task",
            "_display_flush_task",
        ):
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
        - Redis 异常时按 R04 三态语义处理（见 _maintain_leadership），不中断循环。
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
                await self._maintain_leadership()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("Leader 锁维护循环异常（下周期重试）: %s", exc)

    async def _maintain_leadership(self) -> None:
        """单个维护周期（R04 三态语义）.

        - 待命者：抢锁异常 → 返回 False，**不成为 Leader**（杜绝 Redis 断网时
          多 worker 全部 fail-open 重复采集）；
        - 现任者：续租成功 → 续期租约；续租明确失败（锁易主）→ 退位；
          续租异常（状态未知）→ 保持现状但**不续期** lease_expires_at，
          超出租约期限仍无法确认持有 → 退位停止接收/写回并登记
          控制面故障窗口（计数 lease_lost_windows）。
        """
        if self._is_leader:
            renewed = await self._renew_leader_lock()
            if not renewed:
                logger.warning("实时订阅 Leader 锁已丢失，本进程停止订阅转待命")
                await self._resign_leader()
                return
            if self._lease_expires_at is not None and time.time() > self._lease_expires_at:
                self._incr("lease_lost_windows")
                logger.warning(
                    "实时订阅 Leader 租约过期仍无法确认持有（Redis 控制面故障，epoch=%d），"
                    "本进程停止订阅转待命",
                    self._leader_epoch,
                )
                await self._resign_leader()
                return
        elif await self._acquire_leader_lock():
            self._become_leader()

    async def _acquire_leader_lock(self) -> bool:
        """SETNX 抢占订阅 Leader 锁（多 worker 进程防重复订阅）.

        Returns:
            True 抢到锁；False 锁已被其他进程持有，或**抢锁异常（待命）**——
            R04：Redis 断网时待命者绝不能因异常成为 Leader（原 fail-open 会
            使 4 worker 全部启动订阅、重复采集写回）。
        """
        try:
            ok = await redis_client.set(
                _SUBSCRIBER_LEADER_LOCK_KEY,
                self._leader_token,
                nx=True,
                ex=int(settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("订阅 Leader 锁抢占异常（保持待命，不成为 Leader）: %s", exc)
            return False
        if ok:
            self._lease_expires_at = time.time() + float(
                settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS
            )
        return bool(ok)

    async def _renew_leader_lock(self) -> bool:
        """续期订阅 Leader 锁（CAS 校验 token，防给别人的锁续期）.

        Returns:
            True 仍持有锁（含续租异常时"保持现状"——但租约期限不延长）；
            False 锁已丢失（被抢或过期后易主）。
            R04：续租成功才推进 ``lease_expires_at``（最近一次确认成功时刻 + TTL）；
            异常时保持现状由调用方按租约到期判定退位，不再无条件视为持有。
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("订阅 Leader 锁续期异常（保持现状，租约到期未确认将退位）: %s", exc)
            return True
        if ok:
            self._lease_expires_at = time.time() + float(
                settings.SUBSCRIBER_LEADER_LOCK_TTL_SECONDS
            )
        return bool(ok)

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

        R10（2026-09-06 整改）：握手 recv 与首响应 recv 分别限时——此前两处
        ``recv()`` 无超时，服务端不回握手/首响应时任务停在应用心跳与看门狗
        启动之前，永久等待。超时抛 TimeoutError，走 ``_shard_loop`` 既有片级
        退避重连。同帧多消息（握手+Pong、Completion+首批推送）一并分发处理。
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

        # SignalR 协议握手（限时：复用 SIGNALR_OPEN_TIMEOUT）
        handshake_msg = json.dumps({"protocol": "json", "version": 1}) + "\x1e"
        await state.ws.send(handshake_msg)
        raw = await asyncio.wait_for(
            state.ws.recv(), timeout=float(settings.SIGNALR_OPEN_TIMEOUT or 15)
        )
        handshake_parts = [p for p in raw.split("\x1e") if p]
        try:
            handshake = json.loads(handshake_parts[0])
        except json.JSONDecodeError as exc:
            raise ConnectionError(f"SignalR 握手响应非 JSON: {handshake_parts[0][:100]}") from exc
        if "error" in handshake:
            raise ConnectionError(f"SignalR 握手失败: {handshake['error']}")
        # 同帧多消息（R10）：握手帧可能紧随 Pong/推送，订阅发出后统一分发
        deferred_parts = handshake_parts[1:]

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

        # R08：**每次**分片建连成功都核对缺口水位（同代池内重连、reshard 重建后
        # 同样适用——原"每代一次"标记会漏检故障片的第二/三次重连）。稳定来源
        # 身份 = loop_part（按回路核对，不按分片物理编号，重建分片不串位）
        await self._maybe_trigger_gap_backfill(state)

        # 握手帧内携带的其余消息（如 Pong/无关 Completion）分发处理
        for part in deferred_parts:
            try:
                await self._process_shard_message(state, json.loads(part))
            except json.JSONDecodeError:
                logger.warning("握手帧内非 JSON 消息: %s", part[:100])
            except Exception as exc:  # noqa: BLE001
                logger.warning("处理握手帧内消息失败: %s", exc)

        # 接收初始响应（Completion: type=3, result 包含 {code, data}），限时
        # _FIRST_RESPONSE_TIMEOUT（需覆盖快照时延，见常量注释）；
        # 一帧可能包含多条 \x1e 分隔的消息（Completion + 首批 push）
        raw = await asyncio.wait_for(state.ws.recv(), timeout=_FIRST_RESPONSE_TIMEOUT)
        for part in raw.split("\x1e"):
            if not part:
                continue
            try:
                initial = json.loads(part)
            except json.JSONDecodeError:
                continue
            await self._process_shard_message(state, initial)

        # 持续接收推送（一条 WebSocket 帧可能包含多条 \x1e 分隔的消息）
        # R09（2026-09-06 整改）：保鲜/心跳/停滞检查改为单调时钟 deadline 驱动，
        # 每轮循环到点执行，不再依赖 recv 超时分支（持续流量下也必须执行）；
        # recv 等待上限取 min(看门狗 30s, 距下一维护 deadline)，取消时无计时器残留。
        # 数据停滞看门狗：以片级 last_data_at（仅由本片接纳的数据推进）为准，
        # 超过 SIGNALR_STALL_TIMEOUT_SECONDS 主动断开重连（覆盖"WS 活着但上游
        # 停推"盲区）
        stall_timeout = float(settings.SIGNALR_STALL_TIMEOUT_SECONDS)
        while self._running and state.ws is not None:
            recv_timeout = self._recv_maintenance_timeout(state, stall_timeout)
            try:
                raw_message = await asyncio.wait_for(state.ws.recv(), timeout=recv_timeout)
            except TimeoutError:
                raw_message = None
            if raw_message is not None:
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
            # 周期维护（deadline 驱动，各检查内部自带节流，不风暴）：
            # 心跳保活 → 探活判死 → 低频信号保鲜 → 数据停滞检查
            if await self._maintenance_tick(state, stall_timeout):
                return

    def _recv_maintenance_timeout(self, state: _ShardState, stall_timeout: float) -> float:
        """计算 recv 等待上限：min(看门狗 30s, 距下一维护 deadline)（R09）.

        保证即便持续有流量（recv 一直立即返回）也不会跳过维护——每轮循环都
        执行维护检查；而空闲时不会睡过头错过 deadline（探活判死/保鲜/停滞）。
        """
        now = time.time()
        deadline = now + _WATCHDOG_RECV_TIMEOUT
        if state.ping_pending_since is not None:
            deadline = min(deadline, state.ping_pending_since + _PING_DEATH_TIMEOUT)
        else:
            deadline = min(deadline, state.last_ping_sent_at + _PING_KEEPALIVE_INTERVAL)
        try:
            resub_interval = float(settings.SIGNALR_RESUBSCRIBE_INTERVAL)
        except (TypeError, ValueError):  # pragma: no cover - 配置异常兜底
            resub_interval = 1800.0
        deadline = min(deadline, state.last_resubscribe_at + resub_interval)
        if state.last_data_at is not None:
            deadline = min(deadline, state.last_data_at + stall_timeout)
        return max(min(_WATCHDOG_RECV_TIMEOUT, deadline - now), 0.05)

    async def _maintenance_tick(self, state: _ShardState, stall_timeout: float) -> bool:
        """到点执行周期维护（R09 单调时钟 deadline 驱动）.

        各检查内部自带节流（心跳按 _PING_KEEPALIVE_INTERVAL、保鲜按
        SIGNALR_RESUBSCRIBE_INTERVAL 保持 30 分钟节流防风暴），持续流量与
        空闲两种形态下均按 deadline 到点执行。

        Returns:
            True 表示看门狗已触发（连接已关闭，调用方应退出接收循环）。
        """
        # 应用层心跳：到期发送（有数据流动的连接也照发——数据到达同样清 pending）
        await self._keepalive_tick(state)
        # 探活：待应答 Ping 超过判死阈值且期间无数据 → 连接死亡，立即重连
        if self._is_ping_dead(state):
            logger.warning(
                "分片 %d/%d 应用层心跳 %.0fs 无 Pong（期间无数据），判定连接死亡，主动重连",
                state.index + 1,
                state.total,
                time.time() - (state.ping_pending_since or 0.0),
            )
            await self._close_shard_ws(state)
            return True
        # 低频信号（SP/MODE/PID）保鲜：周期重订阅
        await self._resubscribe_tick(state)
        # 数据停滞看门狗：片级接收点只由本片接纳的数据推进（R09），
        # 仅 Pong/空推送不能解除业务停滞
        if state.last_data_at is not None:
            idle = time.time() - state.last_data_at
            if idle >= stall_timeout:
                logger.warning(
                    "分片 %d/%d 数据停滞看门狗触发：%.0fs 无数据（阈值 %.0fs），主动断开重连",
                    state.index + 1,
                    state.total,
                    idle,
                    stall_timeout,
                )
                await self._close_shard_ws(state)
                return True
        return False

    async def _process_shard_message(self, state: _ShardState, msg: dict) -> None:
        """分片消息统一入口：type=6 心跳在片内处理，其余交共享处理器.

        R09：片级接收点（``state.last_data_at``）只由**本片实际接纳的数据消息**
        推进（``_handle_signalr_message`` 返回接纳点数，>0 才推进）——空
        Completion/空推送/Pong 不算业务数据，也**不借用其他片的全局接收点**
        （原实现把全局 ``_last_data_at`` 复制到本片，健康片会掩盖故障片停滞）。
        """
        if msg.get("type") == 6:
            await self._handle_ping_frame(state)
            # 数据未到但 Pong 已到：pending 已清，无碍后续心跳
            return
        accepted = await self._handle_signalr_message(msg)
        if accepted > 0:
            state.last_data_at = time.time()
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
        """应用层心跳：到期发送 SignalR type=6 Ping（内部按间隔节流）.

        R09：由 ``_maintenance_tick`` 每轮到点调用——持续流量的连接也照常保活
        （数据到达同样清 pending，不影响判活）；空闲连接语义不变。
        上一发 Ping 未获 Pong 前不重复发送。
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

    async def _handle_signalr_message(self, msg: dict) -> int:
        """统一处理 SignalR JSON 协议消息，返回本消息接纳的数据点数（R09）.

        支持的消息类型：
        - type=3 Completion: SubscribeAsync 的返回值，result 包含 {code, data}，
          data 为所有订阅 Tag 的当前值（含 SP/MODE/PID 等低频信号）
        - type=1 Invocation (target=updateRealValues): 服务端推送的实时值变化
        - 自定义格式 {code:200, data:[...]}: 兼容非标准 SignalR 响应

        R03：消息内 item 循环逐项 try/except——单项失败不中断后续项；
        返回值供片级接收点判断"本片是否实际接纳了业务数据"（>0 才推进）。

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
                self._incr("msgs_received")
                accepted = await self._process_items(data)
                if accepted:
                    logger.debug(
                        "Completion 响应: %d 个 Tag 当前值已缓存（含 SP/MODE/PID）",
                        accepted,
                    )
                return accepted
            return 0

        # type=1 Invocation — updateRealValues 推送
        if target == "updateRealValues":
            data = msg.get("data") or msg.get("arguments", [[]])[0]
            if isinstance(data, list):
                self._incr("msgs_received")
                return await self._process_items(data)
            return 0

        # 兼容自定义格式（非标准 SignalR: 顶层 code=200）
        if msg.get("code") == 200:
            data = msg.get("data") or []
            if isinstance(data, list):
                self._incr("msgs_received")
                return await self._process_items(data)
            return 0
        return 0

    async def _process_items(self, data: list) -> int:
        """逐项处理消息内的数据点（R03：单项失败隔离，不中断后续项）."""
        accepted = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                if await self._cache_value(item):
                    accepted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("处理实时数据项失败（跳过该项，不影响后续项）: %s", exc)
        return accepted

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

            # 落库映射缓存主动清空（不等 TTL），下个 flush 节拍重建；
            # R11：重建（_refresh_loop_meta_cache）时按新绑定比对 epoch 推进并
            # 清除 last_known/buffer 中旧来源条目（见 _apply_binding_generations）
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

    async def _cache_value(self, item: dict) -> bool:
        """处理单条实时值：① 轻量校验 → ② 历史/写回缓冲（同步段）→ ③ 显示快照待发.

        R03/R06（2026-09-06 整改）重排——原实现先逐点 await Redis（SETEX+PUBLISH）
        成功后才进内存缓冲，Redis 故障会阻断采集落库；现改为：

        1. 轻量校验（``app.core.numeric`` 共享契约）：无效值不丢消息——计
           ``points_invalid``，value 置 None，但 quality/collectTime 照常记录
           （数值有效性与质量相互独立）；
        2. 同步段（不 await Redis）写入 ``_buffer``/``_last_known``；
        3. 显示快照（SETEX+PUBLISH）仅进入有界"每 tag 最新值"待发字典，由
           ``_display_flush_loop`` 批量发送——Redis 故障只影响显示新鲜度。

        Returns:
            该条是否被接纳（tagCode 非空且处理完成；被解绑/改绑丢弃的历史缓冲
            也算接纳——消息本身已处理、片级活性应推进）。
        """
        tag_code = str(item.get("tagCode", "") or "")
        if not tag_code:
            return False
        self._incr("points_received")
        recv_at = time.time()
        self._last_data_at = recv_at

        raw_value = item.get("value", "")
        raw_quality = item.get("quality")
        collect_time = item.get("collectTime", "")
        # ① 轻量校验（R06）：非法/非有限字面量（"-1.#QNAN0"/"nan"/"Infinity"/"1e999"）
        # → value 置 None（绝不折算 0），不丢消息、质量与时间照常记录
        value_valid = parse_finite_float(raw_value) is not None
        if value_valid:
            stored_value: Any = raw_value
        else:
            self._incr("points_invalid")
            stored_value = None
        quality = parse_mode_int(raw_quality)

        # ③ 显示快照进入待发字典（同步段；有界 ≤ 活跃 tag 数，每 tag 仅存最新值）。
        # 载荷在既有 4 字段基础上增量加入可选 valueValid/recvAt/stale（S3 消费侧
        # 对缺省字段容错）；JSON 序列化 allow_nan=False + 出口守卫杜绝非有限数
        payload = {
            "tagCode": tag_code,
            "value": _json_safe_value(raw_value),
            "quality": raw_quality,
            "collectTime": collect_time,
            "valueValid": value_valid,
            "recvAt": datetime.fromtimestamp(recv_at, UTC).isoformat(),
            "stale": False,
        }
        payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        async with self._display_pending_lock:
            self._display_pending[tag_code] = (f"{_REDIS_KEY_PREFIX}{tag_code}", payload_json)

        # ② 历史/写回缓冲（同步段，不 await Redis）
        loop_part, role = await self._parse_tag_code(tag_code)
        if loop_part:
            # R11 双向校验：消息来源 tag 必须就是该 (loop, role) 的当前绑定源
            if tag_code in self._tag_role_cache and (
                self._loop_role_tags.get(loop_part, {}).get(role) != tag_code
            ):
                self._incr("unbound_tag_msgs")
                loop_part = ""
        elif self._tag_role_cache:
            # R11：映射权威存在但 tag 未命中（已解绑/改名/旧代次在途消息）→
            # 丢弃不入历史缓冲（Redis 显示缓存不受影响，多推无害）
            self._incr("unbound_tag_msgs")
        if not loop_part:
            return True

        role_payload = {
            "value": stored_value,
            "quality": quality,
            "ts": collect_time,
            "recvAt": recv_at,
            "tag": tag_code,
            "epoch": self._loop_epochs.get(loop_part, 0),
        }
        async with self._buffer_lock:
            # R05 乱序/迟到拒绝（S0 契约 §4.1，逐角色确定性规则）：仅
            # sourceTime > 已存，或 == 且 recvAt ≥ 已存 才接受；否则拒绝计数
            # （late_rejected）、不回退已存状态（显示快照不受历史状态门控）。
            # 未知新 sourceTime 不得回退已知 sourceTime（回退即状态损失）
            existing = self._last_known.get(loop_part, {}).get(role)
            if existing is not None and not self._update_is_newer(existing, role_payload):
                self._incr("late_rejected")
            else:
                self._buffer.setdefault(loop_part, {})[role] = role_payload
                # 同步更新跨flush持久缓存：保留每个角色最近已知值，
                # 供 flush 时合并进完整行，避免低频角色（SP/MODE/PID_*）写NULL
                self._last_known.setdefault(loop_part, {})[role] = role_payload

        # MODE 变化时主动失效回路统计缓存（loop:stats:type:*），确保监控页
        # 自动/手动/自控率卡片下次查询拿到最新值，而非等 60s TTL 自然过期。
        # MODE 低频变化（小时级），失效代价低。
        # 注意：经 _spawn_bg 保持引用，防 GC 中途回收（asyncio 任务弱引用陷阱）。
        if role == "MODE":
            self._spawn_bg(self._invalidate_loop_stats_cache())
        return True

    async def _display_flush_loop(self) -> None:
        """显示快照批量发送循环（R03）：独立任务，接收路径绝不因 Redis 阻塞.

        ≤200ms 节拍（连续失败时按退避延长），把待发字典按 ≤256 命令组批
        pipeline 发送，逐项异常隔离并计数 ``cache_write_failed``。
        """
        while self._running:
            try:
                await asyncio.sleep(max(_DISPLAY_FLUSH_INTERVAL, self._display_flush_backoff))
                await self._flush_display_pending()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("显示快照批量发送异常: %s", exc)

    async def _flush_display_pending(self) -> None:
        """把待发显示快照按批 pipeline 发送（R03）.

        - 单批 ≤ ``_DISPLAY_BATCH_MAX_ITEMS`` 项（每项 SETEX+PUBLISH 共 2 命令）；
        - pipeline 整体失败时逐项隔离重试：失败项计 ``cache_write_failed`` 并
          回并待发字典（有界 ≤ 活跃 tag 数，恢复后由更新值自然收敛），
          健康项照常送达；
        - 连续失败按指数退避延长节拍（上限 2s），避免 Redis 故障期打连接风暴。
        """
        async with self._display_pending_lock:
            if not self._display_pending:
                return
            pending = self._display_pending
            self._display_pending = {}
        items = list(pending.items())
        any_failed = False
        for i in range(0, len(items), _DISPLAY_BATCH_MAX_ITEMS):
            chunk = items[i : i + _DISPLAY_BATCH_MAX_ITEMS]
            try:
                pipe = redis_client.pipeline()
                for _tag, (key, payload_json) in chunk:
                    pipe.setex(key, _REDIS_TTL, payload_json)
                    pipe.publish(_PUBSUB_CHANNEL, payload_json)
                await pipe.execute()
                continue
            except Exception:  # noqa: BLE001
                pass
            # pipeline 失败（多为连接级）：逐项隔离重试，定位并放过健康项
            for tag, (key, payload_json) in chunk:
                try:
                    pipe = redis_client.pipeline()
                    pipe.setex(key, _REDIS_TTL, payload_json)
                    pipe.publish(_PUBSUB_CHANNEL, payload_json)
                    await pipe.execute()
                except Exception as exc:  # noqa: BLE001
                    any_failed = True
                    self._incr("cache_write_failed")
                    logger.debug(
                        "显示快照写入失败（待发回并，恢复后收敛）: tag=%s error=%s",
                        tag,
                        exc,
                    )
                    async with self._display_pending_lock:
                        # setdefault：不覆盖期间新到的更新值
                        self._display_pending.setdefault(tag, (key, payload_json))
        if any_failed:
            backoff = (
                _DISPLAY_FLUSH_INTERVAL
                if not self._display_flush_backoff
                else (self._display_flush_backoff * 2)
            )
            self._display_flush_backoff = min(backoff, _DISPLAY_FLUSH_MAX_BACKOFF)
        else:
            self._display_flush_backoff = 0.0

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

        R11（2026-09-06 整改）：映射权威（``_tag_role_cache`` 非空）存在但本 tag
        未命中（已解绑/改名/删除 tag 的旧代次在途消息）→ 返回 ("", "")，
        **不再按点号兜底进历史缓冲**（原行为会让解绑后的旧来源值继续入库）；
        无映射权威（仿真/未配置场景，缓存为空）时保留点号风格兜底。
        """
        # 缓存过期时刷新（miss 且距上次刷新超过最小间隔，防止 DB 故障时每条消息打库）
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
        if self._tag_role_cache:
            # 映射权威存在但未命中：已解绑/改名/旧代次在途消息，不入历史缓冲
            return "", ""
        if "." in tag_code:
            loop_part, role = tag_code.rsplit(".", 1)
            return loop_part, role.upper()
        return "", ""

    # ------------------------------------------------------------------
    # 断点续传（Gap Backfill）
    # ------------------------------------------------------------------

    async def _load_checkpoint(self) -> None:
        """启动时从 Redis 恢复最后落库时间 checkpoint 与 per-loop 水位（进程重启场景）.

        落库点（``_last_flushed_at``）从 Redis 恢复；接收点（``_last_data_at``）
        初始化为落库点（进程刚重启，尚未收到新消息）。R08：同时恢复 per-loop
        已确认落库水位——缺口检测优先使用 per-loop 水位，未覆盖回路（从未收到
        数据/水位尚未写入）以全局 checkpoint 起点做兜底窗口，避免漏检。
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
        await self._load_loop_watermarks()

    async def _load_loop_watermarks(self) -> None:
        """从 Redis 恢复 per-loop 已确认落库水位（loop_part → 行 ts epoch 秒）."""
        self._loop_wm_loaded = True
        try:
            raw = await redis_client.hgetall(_GAP_LOOP_WM_KEY)
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取回路水位失败（忽略，缺口检测回退全局 checkpoint）: %s", exc)
            return
        for loop_part, val in (raw or {}).items():
            try:
                self._loop_watermarks[str(loop_part)] = max(
                    self._loop_watermarks.get(str(loop_part), 0.0), float(val)
                )
            except (TypeError, ValueError):
                continue

    async def _ensure_loop_watermarks(self) -> None:
        """确保 per-loop 水位已加载（懒加载一次，缺口检测前调用）."""
        if not self._loop_wm_loaded:
            await self._load_loop_watermarks()

    def _advance_loop_watermark(self, loop_part: str, row_ts_epoch: float) -> None:
        """推进回路已确认落库水位（只增不减：该回路行进入成功批的最大行 ts）."""
        try:
            ts = float(row_ts_epoch)
        except (TypeError, ValueError):
            return
        if ts > self._loop_watermarks.get(loop_part, float("-inf")):
            self._loop_watermarks[loop_part] = ts

    async def _maybe_save_loop_watermarks(self, *, force: bool = False) -> None:
        """将 per-loop 水位持久化到 Redis hash（节流 30s，与 checkpoint 同风格）."""
        if not self._loop_watermarks:
            return
        now = time.monotonic()
        if not force and now - self._last_wm_write < _GAP_CHECKPOINT_WRITE_INTERVAL:
            return
        try:
            await redis_client.hset(
                _GAP_LOOP_WM_KEY,
                mapping={lp: f"{ts:.3f}" for lp, ts in self._loop_watermarks.items()},
            )
            self._last_wm_write = now
        except Exception as exc:  # noqa: BLE001
            logger.debug("写回路水位失败（可忽略，下个节拍重试）: %s", exc)

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

    async def _maybe_trigger_gap_backfill(self, state: _ShardState | None = None) -> None:
        """检测数据缺口并登记/触发断点续传（R08：每次分片建连成功后调用）.

        - **每次重连都核对**（含同代池内重连、reshard 重建后）——原"每代一次"
          标记会让健康片推进全局接收点、掩盖故障片缺口；
        - 稳定来源身份 = **loop_part**（不按分片物理编号存进度，重建分片不串位）；
          对本分片覆盖的回路逐个核对 per-loop 水位，``now - 水位 > MIN_GAP`` 的
          回路集合产生缺口窗口（起点 = 最小水位，终点 = now - 余量）；
        - per-loop 水位优先，尚无水位的回路以全局落库点 checkpoint 兜底；分片
          映射未加载（进程重启首连）时回退全局兜底窗口，避免水位尚未加载时漏检；
        - 缺口窗口登记到持久待补列表（Redis list，重叠合并去重）；gap 开关
          **关闭 → 仅登记不调用远端**（计数+日志），开启 → 经 ``_run_gap_backfill``
          （skip 策略，SETNX 锁/任务登记/失败重试定时器复用）消费。
        """
        now = time.time()
        try:
            min_gap = float(settings.GAP_BACKFILL_MIN_GAP_SECONDS)
        except (TypeError, ValueError):  # pragma: no cover - 配置异常兜底
            min_gap = 600.0
        loops: list[str] | None
        baseline: float | None
        if state is not None:
            shard_loops = self._shard_loop_parts(state)
            if not shard_loops and state.tags and not self._tag_role_cache:
                # 首连场景：绑定映射尚未加载，无法按回路核对 → 加载后重取
                # （按 _LOOP_META_MISS_REFRESH_MIN_INTERVAL 节流，DB 故障时
                # 不会每次重连都打库）
                if (
                    time.monotonic() - self._gap_meta_refresh_at
                    >= _LOOP_META_MISS_REFRESH_MIN_INTERVAL
                ):
                    self._gap_meta_refresh_at = time.monotonic()
                    try:
                        await self._refresh_loop_meta_cache()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("缺口检测前刷新绑定映射失败（回退全局兜底）: %s", exc)
                    shard_loops = self._shard_loop_parts(state)
            if shard_loops:
                await self._ensure_loop_watermarks()
                stale: dict[str, float] = {}
                for loop_part in shard_loops:
                    wm = self._loop_watermarks.get(loop_part, self._last_flushed_at)
                    if wm is not None and now - wm > min_gap:
                        stale[loop_part] = wm
                if not stale:
                    await self._try_consume_pending_gaps()
                    return
                loops, baseline = sorted(stale), min(stale.values())
            else:
                # 无回路身份可得（未配置映射）：全局 checkpoint 兜底
                loops, baseline = None, self._last_flushed_at
        else:
            # 全局兜底口径（兼容直接调用/无分片上下文）
            loops, baseline = None, self._last_flushed_at
        if baseline is None or now - baseline < min_gap:
            # 无缺口也尝试消费在途待补（进程重启后未完成的持久缺口由此恢复）
            await self._try_consume_pending_gaps()
            return
        await self._register_gap_window(loops, baseline, now)
        if settings.GAP_BACKFILL_ENABLED:
            await self._try_consume_pending_gaps()

    def _shard_loop_parts(self, state: _ShardState) -> set[str]:
        """解析分片覆盖的回路集合（loop_part 身份，与分片物理编号无关）.

        优先走 ``_tag_role_cache``（绑定映射权威）；映射未命中的点号风格 tag
        （仿真/未配置场景）按 ``rsplit('.', 1)`` 兜底，与 ``_parse_tag_code``
        口径一致。
        """
        loops: set[str] = set()
        for tag in state.tags:
            hit = self._tag_role_cache.get(tag)
            if hit:
                loops.add(hit[0])
            elif "." in tag:
                loops.add(tag.rsplit(".", 1)[0])
        return loops

    # ------------------------------------------------------------------
    # R08：持久待补缺口列表（Redis list realtime:gap:pending）
    # ------------------------------------------------------------------

    async def _load_pending_gaps(self) -> list[dict[str, Any]]:
        """读取持久待补缺口列表（JSON 损坏条目跳过）."""
        try:
            raw_list = await redis_client.lrange(_GAP_PENDING_KEY, 0, -1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取持久待补缺口列表失败（按空处理）: %s", exc)
            return []
        entries: list[dict[str, Any]] = []
        for raw in raw_list or []:
            try:
                entry = json.loads(raw)
                if isinstance(entry, dict) and "start" in entry and "end" in entry:
                    entries.append(entry)
            except (json.JSONDecodeError, TypeError):
                continue
        return entries

    async def _save_pending_gaps(self, entries: list[dict[str, Any]]) -> None:
        """整体重写持久待补缺口列表（Leader 单写者；pipeline 失败整体保留旧值）."""
        try:
            pipe = redis_client.pipeline()
            pipe.delete(_GAP_PENDING_KEY)
            for entry in entries:
                pipe.lpush(_GAP_PENDING_KEY, json.dumps(entry))
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("写持久待补缺口列表失败（保留旧值）: %s", exc)

    @staticmethod
    def _same_gap_entry(a: dict[str, Any], b: dict[str, Any]) -> bool:
        """待补条目身份判定（registeredAt + start + loops，合并保序可追溯）."""
        return (
            a.get("registeredAt") == b.get("registeredAt")
            and a.get("start") == b.get("start")
            and a.get("loops") == b.get("loops")
        )

    @staticmethod
    def _merge_gap_loops(a: list[str] | None, b: list[str] | None) -> list[str] | None:
        """合并条目回路集合：None 表示全量口径（吞并任何子集）."""
        if a is None or b is None:
            return None
        return sorted(set(a) | set(b))

    @staticmethod
    def _gap_windows_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
        """两待补窗口是否重叠：回路集合相交 且 时间区间相交."""
        la, lb = a.get("loops"), b.get("loops")
        loops_overlap = la is None or lb is None or bool(set(la) & set(lb))
        ranges_overlap = float(a.get("start") or 0) <= float(b.get("end") or 0) and float(
            b.get("start") or 0
        ) <= float(a.get("end") or 0)
        return loops_overlap and ranges_overlap

    async def _register_gap_window(self, loops: list[str] | None, start: float, now: float) -> None:
        """登记缺口窗口到持久待补列表（复用截断/余量规则；重叠合并去重）."""
        try:
            max_seconds = float(settings.GAP_BACKFILL_MAX_HOURS) * 3600
        except (TypeError, ValueError):  # pragma: no cover - 配置异常兜底
            max_seconds = 24.0 * 3600
        if now - start > max_seconds:
            logger.warning(
                "数据缺口 %.1fh 超过单次补数上限（%dh），仅登记最近 %dh；"
                "更早数据请通过「数据管理→历史数据导入」手工补齐",
                (now - start) / 3600,
                settings.GAP_BACKFILL_MAX_HOURS,
                settings.GAP_BACKFILL_MAX_HOURS,
            )
            start = now - max_seconds
        # 末端留余量，避免与正在写入的实时行撞时间戳
        end = now - _GAP_BACKFILL_END_MARGIN
        if end <= start:
            return
        entry: dict[str, Any] = {
            "loops": loops,
            "start": start,
            "end": end,
            "registeredAt": time.time(),
        }
        entries = await self._load_pending_gaps()
        inflight = self._inflight_gap_entry
        merged = False
        for existing in entries:
            if inflight is not None and self._same_gap_entry(existing, inflight):
                # 在途消费中的窗口不合并（执行快照不可变；扩展范围由水位
                # 覆盖检查在后续消费时收敛，重复补数经 skip 幂等无害）
                continue
            if self._gap_windows_overlap(existing, entry):
                existing["start"] = min(float(existing["start"]), start)
                existing["end"] = max(float(existing["end"]), end)
                existing["loops"] = self._merge_gap_loops(existing.get("loops"), loops)
                merged = True
        if not merged:
            entries.append(entry)
        await self._save_pending_gaps(entries)
        self._incr("gap_windows_registered")
        if not settings.GAP_BACKFILL_ENABLED:
            logger.warning(
                "GAP_BACKFILL_ENABLED=False：缺口仅登记不调用远端（当前待补窗口 %d 条，"
                "loops=%s, range=%s~%s）",
                len(entries),
                "ALL" if loops is None else loops,
                datetime.fromtimestamp(start, UTC).isoformat(),
                datetime.fromtimestamp(end, UTC).isoformat(),
            )

    async def _remove_pending_gap(self, entry: dict[str, Any]) -> None:
        """补数成功后移除对应待补条目（按身份匹配）."""
        entries = await self._load_pending_gaps()
        remaining = [e for e in entries if not self._same_gap_entry(e, entry)]
        if len(remaining) != len(entries):
            await self._save_pending_gaps(remaining)

    async def _try_consume_pending_gaps(self) -> None:
        """消费持久待补缺口（单实例守卫；仅 gap 开关开启时由调用方触达）.

        - 先丢弃已被当前水位完全覆盖的条目（在途补数成功后残留的合并条目
          由此收敛，不重复调远端）；
        - 选最早登记的条目执行 ``_run_gap_backfill``（skip 策略，SETNX 锁/
          任务登记/失败重试定时器复用）；失败条目保留在列表中，由重试定时器
          或下次重连再消费——补数失败后重启仍可见。
        """
        if self._backfill_task is not None and not self._backfill_task.done():
            return
        entries = await self._load_pending_gaps()
        if not entries:
            return
        kept: list[dict[str, Any]] = []
        changed = False
        for entry in entries:
            loops = entry.get("loops")
            if loops is not None and all(
                self._loop_watermarks.get(lp, 0.0) >= float(entry.get("end") or 0.0) for lp in loops
            ):
                changed = True  # 水位已越过窗口末端：已补齐（或实时已覆盖）
                continue
            kept.append(entry)
        if changed:
            await self._save_pending_gaps(kept)
        if not kept:
            return
        entry = min(
            kept,
            key=lambda e: (float(e.get("registeredAt") or 0.0), float(e.get("start") or 0.0)),
        )
        self._inflight_gap_entry = entry
        self._backfill_task = asyncio.create_task(
            self._run_gap_backfill(
                float(entry["start"]),
                float(entry["end"]),
                loop_parts=entry.get("loops"),
                pending_entry=entry,
            )
        )

    async def _run_gap_backfill(
        self,
        gap_start: float,
        gap_end: float,
        *,
        loop_parts: list[str] | None = None,
        pending_entry: dict[str, Any] | None = None,
    ) -> None:
        """执行断点续传：经远端历史数据接口补全缺口窗口，并触发 KPI 回算.

        - SETNX 分布式锁：多副本部署时防重复补数（TTL =
          ``GAP_BACKFILL_LOCK_TTL_SECONDS``），未抢到锁则跳过
        - checkpoint 条件推进：仅全部回路成功（failed==0）才推进落库点
          ``_last_flushed_at``；部分失败/异常时缺口保留，启动延迟重试定时器
          （指数退避，连接在线也生效）
        - R08：``loop_parts`` 限定本次补数只覆盖指定回路（per-loop 缺口窗口，
          不因一片故障触发无边界全量补数）；成功后推进这些回路的 per-loop
          水位并移除持久待补条目（全局落库点不受 per-loop 窗口影响）。
          空返回≠完整：failed==0 即推进（远端确无数据的窗口推进口径已登记），
          但全窗口 0 行时计数 ``backfill_empty_windows`` 供观测
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
                    select(LoopLedger.id, LoopLedger.tag_name).where(LoopLedger.is_active.is_(True))
                )
                rows = result.all()
                loop_ids = [str(row[0]) for row in rows]
                if loop_parts is not None:
                    # R08：限定 per-loop 缺口窗口覆盖的回路（tag_name = loop_part）
                    wanted = set(loop_parts)
                    loop_ids = [str(row[0]) for row in rows if str(row[1] or "") in wanted]
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
                if pending_entry is not None:
                    # 无可补回路：条目出队，避免每次重连空转重试
                    await self._remove_pending_gap(pending_entry)
                return

            # 任务登记：补数进任务列表（来源标记 auto-backfill，系统任务不通知个人）
            _SHANGHAI = timezone(timedelta(hours=8))
            scope = "全部回路" if loop_parts is None else f"{len(loop_parts)} 个回路"
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
                # R08 空返回≠完整：failed==0 即推进（最简口径，已登记）；
                # 全窗口 0 行计数 backfill_empty_windows 供观测（远端确无数据）
                coverage = import_result.get("loopCoverage") or []
                if coverage and all(not int(c.get("importedPoints") or 0) for c in coverage):
                    self._incr("backfill_empty_windows")
                if loop_parts is not None:
                    # per-loop 窗口：只推进覆盖回路的 per-loop 水位；
                    # 全局落库点/已确认边界不受影响（其他回路口径不变）
                    for loop_part in loop_parts:
                        self._advance_loop_watermark(loop_part, gap_end)
                    await self._maybe_save_loop_watermarks(force=True)
                else:
                    # 全量窗口：落库点推进到窗口末端，避免下次重连重复补
                    # （接收点 _last_data_at 不推进——补数不等于收到新实时数据）
                    self._last_flushed_at = max(self._last_flushed_at or 0.0, gap_end)
                    # R07：同步推进已确认边界，防止后续 flush 用旧边界回退落库点
                    self._confirmed_boundary = max(self._confirmed_boundary or 0.0, gap_end)
                if pending_entry is not None:
                    await self._remove_pending_gap(pending_entry)
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
                    "断点续传完成: range=%s~%s, %s, loops=%d/%d, 耗时=%.1fs",
                    ts_start,
                    ts_end,
                    scope,
                    succeeded,
                    total,
                    time.monotonic() - started,
                )
                return

            # 部分失败：checkpoint 不推进，缺口保留待重试（持久待补条目同样保留）
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
            # 消费中的待补条目快照释放（条目本身按成败已移除/保留）
            if pending_entry is not None and self._inflight_gap_entry is pending_entry:
                self._inflight_gap_entry = None
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

        R08：先消费持久待补列表（per-loop 缺口窗口优先——失败条目仍在列表中，
        重启后同样由此路径恢复）；无待补条目时回落到全局重试窗口。
        重试窗口末端取触发时刻（而非原窗口末端），覆盖等待期间新产生的缺口；
        定时器触发时若已有补数在执行（如重连触发），按同延迟原地重排兜底——
        执行中的补数失败时会自行重排定时器，成功且覆盖本窗口时会清除重试状态。
        """
        try:
            await asyncio.sleep(delay)
            if not self._running:
                return
            await self._try_consume_pending_gaps()
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
        """从 Redis 获取过去 1 小时的缓存数据（按时间升序返回）.

        R05（审查 §3.3）：不再只 reverse 到达顺序——乱序上游/重连旧快照会
        破坏时间序，返回前按 ts **排序 + 同 ts 去重（保留后写值）**。Redis
        LPUSH 序即写入时间降序（index 0 最新），同 ts 先见者为后写值。
        """
        key = f"{_REDIS_KEY_PREFIX}history:{loop_part}"
        raw_list = await redis_client.lrange(key, 0, -1)
        if not raw_list:
            return []

        dedup: dict[str, dict] = {}
        for raw in raw_list:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                # setdefault：同 ts 保留先见者（= 后写值，LPUSH 序最新在前）
                dedup.setdefault(str(row.get("ts") or ""), row)

        def _sort_key(ts: str) -> tuple[int, datetime]:
            dt = _parse_known_ts(ts)
            # 不可解析 ts 排末尾（稳定，不与可解析时刻比较）
            return (0, dt) if dt is not None else (1, datetime.min.replace(tzinfo=_TARGET_TZ))

        return [dedup[ts] for ts in sorted(dedup, key=_sort_key)]

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
        """从数据库重建绑定映射缓存（仅含活跃且有 tag 映射的回路）.

        - ``_loop_meta_cache``: loop_part(=回路台账 tag_name) → (loop_id, unit_id)
        - ``_tag_role_cache``: 测点 tag_name → (loop_part, role)
        - ``_loop_role_tags`` / ``_loop_epochs``: R11 绑定代次——loop_part →
          {role → 绑定源 tag} 反查与绑定版本号；绑定变化的 loop epoch+1 并清除
          last_known/buffer 中来自旧绑定的条目（新绑定无值 → 角色 NULL，
          不得写 0、不得沿用旧来源值）。

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

        # 测点 tag_name → (loop_part, role)；loop_part → {role → 绑定源 tag}（R11 反查）
        tag_role_cache: dict[str, tuple[str, str]] = {}
        loop_role_tags: dict[str, dict[str, str]] = {}
        for lid, tag_role, tag_name in role_rows:
            loop_name = lid_to_loop_name.get(str(lid))
            if loop_name and tag_name:
                role_upper = str(tag_role).upper()
                tag_role_cache[tag_name] = (loop_name, role_upper)
                loop_role_tags.setdefault(loop_name, {})[role_upper] = tag_name

        # R11：比对新旧绑定 → epoch 推进 + 清除旧代次状态（在缓存整体换血前执行）
        self._apply_binding_generations(loop_role_tags, set(meta_cache))

        self._loop_meta_cache = meta_cache
        self._tag_role_cache = tag_role_cache
        self._loop_role_tags = loop_role_tags
        self._loop_meta_cache_at = time.monotonic()

    def _apply_binding_generations(
        self, new_role_tags: dict[str, dict[str, str]], active_loop_parts: set[str]
    ) -> None:
        """绑定代次维护（R11，S0 契约 §4.3）.

        - 新旧 ``loop_part → {role → tag}`` 不一致的 loop：epoch+1；
        - 清除 ``_last_known``/``_buffer`` 中来源 tag 已不再绑定该角色的条目
          （改绑/解绑/角色删除/整回路删除停用——不在活跃集合的 loop 全清）；
        - ``_loop_epochs`` 随活跃集合收敛（已删除回路移出，防内存无界增长）；
        - 新绑定无值 → 角色自然为 NULL（不写 0、不沿用旧来源值）。
        """
        changed: list[str] = []
        for loop_part, new_roles in new_role_tags.items():
            if self._loop_role_tags.get(loop_part) != new_roles:
                self._loop_epochs[loop_part] = self._loop_epochs.get(loop_part, 0) + 1
                changed.append(loop_part)
        # 清理 last_known/buffer 中已失效的来源条目（按条目 tag 与新绑定比对）
        for store in (self._last_known, self._buffer):
            for loop_part in list(store):
                roles_map = store[loop_part]
                new_roles = new_role_tags.get(loop_part, {})
                for role in list(roles_map):
                    entry = roles_map[role]
                    if not isinstance(entry, dict) or new_roles.get(role) != entry.get("tag"):
                        del roles_map[role]
                if not roles_map:
                    del store[loop_part]
        # epoch 表收敛：已删除/停用回路移出
        for loop_part in [lp for lp in self._loop_epochs if lp not in active_loop_parts]:
            self._loop_epochs.pop(loop_part, None)
        if changed:
            logger.info("绑定代次推进（epoch+1，旧来源条目已清除）: %s", changed)

    async def _flush_loop(self) -> None:
        """按配置间隔 flush 缓冲区到 TDengine（默认 1 秒）+ 周期输出链路计数."""
        next_metrics_log = time.monotonic() + 300.0
        while self._running:
            try:
                await asyncio.sleep(settings.TDENGINE_FLUSH_INTERVAL)
                await self._flush_buffer()
                if time.monotonic() >= next_metrics_log:
                    next_metrics_log = time.monotonic() + 300.0
                    logger.info("实时采集链路计数（S0 契约 §8）: %s", dict(self._metrics))
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("TDengine flush 异常: %s", exc)

    async def _flush_buffer(self) -> None:
        """将缓冲区数据批量写入 Redis 1 小时缓存（及可选的 TDengine）.

        R07（2026-09-06 整改，S0 契约 §4.2）：
        - 先重写上一拍失败窗口（有界重试缓冲，优先于新批）；
        - ``_buffer_lock`` 内**原子截取** batch + ``_last_known`` 深拷贝快照 +
          接收边界（batch_boundary = max(批内 recvAt)）；await 期间新数据进下一批，
          不混入本批、也不被本批的成功水位覆盖；
        - checkpoint（``_last_flushed_at``）只推进到已确认成功批的
          batch_boundary（不再取 ``_last_data_at``），且不越过未确认窗口；
        - TD 批次按 ≤ ``_TD_BATCH_MAX_ROWS`` 行拆分，分块成功独立记录；
          失败行进入有界重试缓冲并登记未确认窗口（后续实时批成功不得擦掉
          旧失败窗口，仅该窗口自身重写成功才确认移除）；
        - ``_build_row`` 在 try 保护内，单行构造失败不影响其他行。
        """
        # 1) 上一拍失败窗口优先重写（有界重试缓冲）
        await self._retry_unconfirmed_windows()

        # 2) 原子截取新批（await 期间的新数据进入下一批）
        async with self._buffer_lock:
            if not self._buffer:
                self._metrics["buffer_rows_pending"] = 0
                return
            batch = self._buffer
            self._buffer = {}
            last_known_snapshot = {
                lp: {role: dict(entry) for role, entry in roles.items()}
                for lp, roles in self._last_known.items()
            }
            recv_ats = [
                float(entry.get("recvAt") or 0.0)
                for roles in batch.values()
                for entry in roles.values()
            ]
            batch_boundary = max(recv_ats) if recv_ats else None
            boundary_prev = self._last_flushed_at

        # 实时写回需携带真实 loop_id/unit_id（TDengine USING TAGS 仅子表首次创建
        # 生效，实时先行创建的子表 TAG 必须正确，否则永远为空且无法后续补写）
        loop_meta: dict[str, tuple[str, str]] = {}
        if self._writeback_enabled:
            loop_meta = await self._get_loop_meta_map(list(batch))

        # 3) 构造行（单行失败隔离，R07）——历史缓存条目 + TD 表数据
        history_entries: list[tuple[str, str, str]] = []  # (key, row_json, row_ts)
        td_tables: list[dict[str, Any]] = []
        loop_row_ts: dict[str, float] = {}  # loop_part → 行 ts epoch（R08 水位推进用）
        for loop_part, roles_data in batch.items():
            # 合并跨flush持久缓存快照：本tick buffer优先（新值覆盖旧值），
            # 未出现在本tick中的低频角色（SP/MODE/PID_*）取最近已知值，避免写NULL
            merged_roles: dict[str, Any] = dict(last_known_snapshot.get(loop_part, {}))
            merged_roles.update(roles_data)
            try:
                row = self._build_row(merged_roles)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "构造回路行失败（跳过该行，不影响其他行）: loop_part=%s error=%s",
                    loop_part,
                    exc,
                )
                continue
            if row is None:
                # R05：整行无任何已知 sourceTime → 不落 TD/历史缓存（不伪造 now()）
                self._incr("rows_dropped_no_ts")
                continue
            # R05 自描述行：roleTs（各角色 sourceTime，与行 ts 同口径归一）+
            # roleQuality——下游可区分"新测量"（roleTs == 行 ts）与"携带的
            # last-known"（roleTs < 行 ts）；TD 宽表不加列（S0 决策 1，边界登记）
            role_ts = {
                role: _format_target_ts(dt)
                for role, dt in _collect_role_source_times(merged_roles).items()
            }
            role_quality = {
                role: entry.get("quality")
                for role, entry in merged_roles.items()
                if isinstance(entry, dict) and entry.get("quality") is not None
            }
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
                "roleTs": role_ts,
                "roleQuality": role_quality,
            }
            row_dt = _parse_known_ts(row[0])
            if row_dt is not None:  # pragma: no branch - 行 ts 恒可解析（_build_row 保证）
                loop_row_ts[loop_part] = row_dt.timestamp()
            key = f"{_HISTORY_KEY_PREFIX}{loop_part}"
            history_entries.append((key, json.dumps(row_dict, allow_nan=False), row[0]))
            if self._writeback_enabled:
                subtable = make_subtable_name(loop_part)
                loop_id, unit_id = loop_meta.get(loop_part, ("", ""))
                td_tables.append(
                    {
                        "subtable": subtable,
                        "loop_id": loop_id,
                        "unit_id": unit_id,
                        "rows": [row],
                    }
                )

        # 4) 写入 Redis 1 小时缓存（R02 三重限制；pipeline 事务性：失败即整批
        #    未写、可整批重试）
        redis_ok = True
        if history_entries:
            try:
                await self._push_history_entries(history_entries)
            except Exception as exc:  # noqa: BLE001
                redis_ok = False
                logger.warning("Redis 历史数据写入失败: %s", exc)

        # 5) 批量写入 TDengine（分块 ≤500 行，分块成功独立记录）
        failed_td_tables = await self._write_td_chunks(td_tables)

        self._metrics["buffer_rows_pending"] = len(self._buffer)

        # 6) 水位推进 / 失败窗口登记
        if redis_ok and not failed_td_tables:
            # 成功批：推进已确认边界；仅当无未确认窗口挂起时才推进持久化 checkpoint
            # （不越过旧失败数据，实时批成功不得擦掉旧失败窗口）
            if batch_boundary is not None:
                self._confirmed_boundary = max(self._confirmed_boundary or 0.0, batch_boundary)
            if not self._unconfirmed_windows and self._confirmed_boundary is not None:
                self._last_flushed_at = max(self._last_flushed_at or 0.0, self._confirmed_boundary)
            # R08：成功批内各回路推进 per-loop 已确认落库水位（最大行 ts）
            for loop_part, row_ts in loop_row_ts.items():
                self._advance_loop_watermark(loop_part, row_ts)
        else:
            failed_history = [] if redis_ok else history_entries
            # R08：部分成功批中，持久化成功的回路照常推进水位——写回开启时以
            # TD 写入结果为准（历史缓存缺失由 R13 完整性校验回源 TD 兜底）；
            # 写回关闭时历史缓存是唯一落库形态，以 redis_ok 为准
            failed_subtables = {t["subtable"] for t in failed_td_tables}
            failed_loop_row_ts: dict[str, float] = {}
            for loop_part, row_ts in loop_row_ts.items():
                persisted = (
                    (make_subtable_name(loop_part) not in failed_subtables)
                    if self._writeback_enabled
                    else redis_ok
                )
                if persisted:
                    self._advance_loop_watermark(loop_part, row_ts)
                else:
                    failed_loop_row_ts[loop_part] = row_ts
            self._register_unconfirmed_window(
                boundary_prev,
                batch_boundary,
                failed_td_tables,
                failed_history,
                failed_loop_row_ts,
            )

        # 断点续传 checkpoint 持久化（节流，进程重启后据此恢复缺口起点）
        await self._maybe_save_checkpoint()
        # R08：per-loop 水位持久化（节流 30s，与 checkpoint 同风格）
        await self._maybe_save_loop_watermarks()

    async def _push_history_entries(self, history_entries: list[tuple[str, str, str]]) -> None:
        """把 (key, row_json, row_ts) 列表写入 Redis 历史缓存（R02 三重限制）.

        - **每回路上限**：``LTRIM(0, REALTIME_HISTORY_MAX_POINTS_PER_LOOP-1)``。
          与 R13 完整性命中（10% 容差）的配合：1Hz 写入节奏下 1200 点 ≈ 20 分钟，
          超过 ~20 分钟的窗口缓存天然未命中回源本地 TD 宽表——这是整改计划
          允许的压力退化路径（"压力下允许 history 缓存退化为未命中"），换取
          961 回路满载 JSON 载荷 ≤ 全局预算（64MiB）而非 585MiB+；
        - **写入前去重**：行 ts ≤ 该回路最近已推送行 ts → 跳过 LPUSH +
          计 ``history_dup_dropped``（Leader 单写者内存水位，重启后首次 flush
          经 LINDEX 0 懒加载回填；R05 修复后同 ts 重复行已大幅减少，此为兜底）；
        - **全局字节预算**：近似跟踪全部 history 键 JSON 载荷字节（写入累加、
          LTRIM 裁剪按均值扣减、TTL 过期按内存过期模型清扫——误差登记于 S2
          报告），超 ``REALTIME_HISTORY_GLOBAL_BUDGET_BYTES`` → 停止为"尚无
          history 键的回路"新建历史缓存 + 计 ``history_budget_exceeded``；
          已活跃键继续正常 LTRIM 收敛；最新值缓存（realtime:{tag}）与 TD
          写回不受影响；
        - pipeline 整体失败 → 去重水位/预算跟踪不推进（失败批进入未确认窗口
          重试，重写幂等）。
        """
        max_points, budget = self._history_limits()
        self._sweep_expired_history_keys()
        push_plan: list[tuple[str, str, str]] = []
        for key, row_json, row_ts in history_entries:
            loop_part = key[len(_HISTORY_KEY_PREFIX) :]
            last_ts = await self._last_pushed_row_ts(key, loop_part)
            if row_ts and last_ts and row_ts <= last_ts:
                self._incr("history_dup_dropped")
                continue
            if (
                loop_part not in self._history_key_bytes
                and self._history_bytes_total + len(row_json) > budget
            ):
                self._incr("history_budget_exceeded")
                continue
            push_plan.append((key, row_json, row_ts))
        if not push_plan:
            return
        pipe = redis_client.pipeline()
        for key, row_json, _ts in push_plan:
            pipe.lpush(key, row_json)
            pipe.ltrim(key, 0, max_points - 1)
            pipe.expire(key, _HISTORY_TTL_SECONDS)
        await pipe.execute()
        # 成功后才推进去重水位与预算跟踪（失败批进未确认窗口重试，状态不冒进）
        for key, row_json, row_ts in push_plan:
            loop_part = key[len(_HISTORY_KEY_PREFIX) :]
            self._track_history_push(loop_part, len(row_json), max_points)
            self._last_pushed_row_ts_map[loop_part] = row_ts

    def _history_limits(self) -> tuple[int, int]:
        """读取历史缓存限制配置（每回路上限, 全局字节预算）.

        对 MagicMock 型 settings（部分单测仅 patch 个别属性）回退到
        config.py 的默认值（1200 点 / 64MiB），避免 int(MagicMock)=1 的假值。
        """
        raw_points = getattr(settings, "REALTIME_HISTORY_MAX_POINTS_PER_LOOP", None)
        raw_budget = getattr(settings, "REALTIME_HISTORY_GLOBAL_BUDGET_BYTES", None)
        max_points = raw_points if isinstance(raw_points, int) and raw_points > 0 else 1200
        budget = raw_budget if isinstance(raw_budget, int) and raw_budget >= 0 else 64 * 1024 * 1024
        return max_points, budget

    async def _last_pushed_row_ts(self, key: str, loop_part: str) -> str | None:
        """该回路最近已推送历史行 ts（规范化字符串，字典序即时间序）.

        重启后首次 flush 经 ``LINDEX key 0``（最新行）懒加载回填；键不存在
        → None（无历史，任意 ts 可写）。
        """
        if loop_part not in self._last_pushed_loaded:
            self._last_pushed_loaded.add(loop_part)
            try:
                raw = await redis_client.lindex(key, 0)
            except Exception as exc:  # noqa: BLE001
                raw = None
                logger.debug("LINDEX 历史缓存最新行失败（按无历史处理）: %s", exc)
            if raw:
                try:
                    self._last_pushed_row_ts_map[loop_part] = json.loads(raw).get("ts") or None
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass
        return self._last_pushed_row_ts_map.get(loop_part)

    def _track_history_push(self, loop_part: str, nbytes: int, max_points: int) -> None:
        """成功推送后推进全局字节预算近似跟踪（Leader 单写者，进程内有效）.

        LTRIM 裁剪按"该键当前平均行字节"近似扣减一条（不实际读回被裁行，
        误差登记）；整键 TTL 过期由 ``_sweep_expired_history_keys`` 按内存
        过期模型扣减。
        """
        self._history_bytes_total += nbytes
        if loop_part not in self._history_key_bytes:
            self._history_key_bytes[loop_part] = nbytes
            self._history_key_rows[loop_part] = 1
        else:
            rows = self._history_key_rows[loop_part]
            if rows >= max_points:
                # 已达每回路上限：本次 LPUSH 会裁掉最老一行 → 按均值近似扣减
                avg = self._history_key_bytes[loop_part] / rows
                self._history_key_bytes[loop_part] += nbytes - avg
                self._history_bytes_total -= int(avg)
            else:
                self._history_key_bytes[loop_part] += nbytes
                self._history_key_rows[loop_part] = rows + 1
        # 与 Redis EXPIRE 语义一致：每次写入刷新整键 TTL
        self._history_key_expire_at[loop_part] = time.time() + _HISTORY_TTL_SECONDS

    def _sweep_expired_history_keys(self) -> None:
        """按内存过期模型清扫已过 TTL 的 history 键跟踪（节流 60s）.

        键过期后该回路回到"尚无 history 键"状态：预算门重新适用、去重水位
        重置（键已不存在，下次推送重建）。
        """
        now = time.time()
        if now - self._last_history_sweep < _HISTORY_SWEEP_INTERVAL:
            return
        self._last_history_sweep = now
        expired = [lp for lp, exp in self._history_key_expire_at.items() if exp <= now]
        for loop_part in expired:
            self._history_bytes_total -= self._history_key_bytes.pop(loop_part, 0)
            self._history_key_rows.pop(loop_part, None)
            self._history_key_expire_at.pop(loop_part, None)
            self._last_pushed_loaded.discard(loop_part)
            self._last_pushed_row_ts_map.pop(loop_part, None)
        # 近似扣减的舍入误差可能累积为负 → 钳回 0
        self._history_bytes_total = max(self._history_bytes_total, 0)

    async def _write_td_chunks(self, tables_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """TDengine 分块写入（R07：≤ ``_TD_BATCH_MAX_ROWS`` 行/批，分块独立重试）.

        Returns:
          成功写入的行计入 ``rows_written``；最终失败的分块原样返回，由调用方
          登记进未确认窗口重试缓冲（``rows_failed``）。
        """
        if not tables_rows:
            return []
        chunks: list[list[dict[str, Any]]] = []
        chunk: list[dict[str, Any]] = []
        chunk_rows = 0
        for table in tables_rows:
            rows = len(table.get("rows", []))
            if chunk and chunk_rows + rows > _TD_BATCH_MAX_ROWS:
                chunks.append(chunk)
                chunk, chunk_rows = [], 0
            chunk.append(table)
            chunk_rows += rows
        if chunk:
            chunks.append(chunk)

        failed: list[dict[str, Any]] = []
        for chunk in chunks:
            ok = False
            for attempt in range(3):
                try:
                    count = await batch_insert_multi(chunk)
                    self._incr("rows_written", count)
                    logger.debug("批量写入 %d 行到 %d 个子表", count, len(chunk))
                    ok = True
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    if attempt < 2:
                        wait = 0.5 * (2**attempt)  # 0.5s, 1s
                        logger.warning(
                            "批量写入失败 (尝试 %d/3): %s，%gs 后重试", attempt + 1, exc, wait
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.error(
                            "批量写入最终失败（%d 个子表进入重试缓冲）: %s", len(chunk), exc
                        )
            if not ok:
                failed.extend(chunk)
        if failed:
            self._incr("rows_failed", sum(len(t.get("rows", [])) for t in failed))
        return failed

    def _register_unconfirmed_window(
        self,
        boundary_prev: float | None,
        batch_boundary: float | None,
        failed_td_tables: list[dict[str, Any]],
        failed_history: list[tuple[str, str, str]],
        failed_loop_row_ts: dict[str, float] | None = None,
    ) -> None:
        """登记未确认窗口（R07）：失败批行进入有界重试缓冲，窗口记录进程内保留.

        后续实时批次成功不得擦掉旧失败窗口；仅该窗口自身重写成功
        （``_retry_unconfirmed_windows``）才确认移除并允许水位推进。
        缓冲有界：窗口数或行数超限时保留窗口记录、丢弃重试载荷（数据损失
        显式告警，不静默、不无限占用内存）。
        R08：``failed_loop_row_ts`` 登记该窗口内**持久化失败**回路的行 ts，
        窗口确认成功后据此推进 per-loop 水位。
        """
        window: dict[str, Any] = {
            "start": boundary_prev if boundary_prev is not None else batch_boundary,
            "end": batch_boundary,
            "td_tables": failed_td_tables,
            "history": failed_history,
            "loop_row_ts": dict(failed_loop_row_ts or {}),
        }
        total_td_rows = sum(len(t.get("rows", [])) for t in failed_td_tables)
        if (
            len(self._unconfirmed_windows) >= _MAX_UNCONFIRMED_WINDOWS
            or total_td_rows > _MAX_RETRY_ROWS_PER_WINDOW
        ):
            logger.error(
                "未确认窗口重试缓冲已满（%d 窗口/单窗口 %d 行上限），新失败批仅登记"
                "窗口不再重试（数据缺口以 gap backfill 兜底）: window=[%s, %s] rows=%d",
                _MAX_UNCONFIRMED_WINDOWS,
                _MAX_RETRY_ROWS_PER_WINDOW,
                window["start"],
                window["end"],
                total_td_rows,
            )
            window["td_tables"] = []
            window["history"] = []
        self._unconfirmed_windows.append(window)
        self._incr("unconfirmed_windows")

    async def _retry_unconfirmed_windows(self) -> None:
        """重写历史失败窗口（R07：优先于新批；窗口确认成功才移除）.

        窗口内 Redis 历史与 TD 行独立重试；全部成功 → 窗口确认移除并推进
        已确认边界，随后（若不再有挂起窗口）推进落库点；部分失败 → 窗口保留，
        失败载荷留待下一拍。同 ts 重写幂等（TDengine 覆盖语义 / 事务性 pipeline）。
        R08：窗口确认成功后推进其登记回路的 per-loop 水位（行 ts 口径）。
        """
        if not self._unconfirmed_windows:
            return
        remaining: list[dict[str, Any]] = []
        for window in self._unconfirmed_windows:
            confirmed = True
            history = window.get("history") or []
            if history:
                try:
                    await self._push_history_entries(history)
                except Exception as exc:  # noqa: BLE001
                    confirmed = False
                    logger.warning("失败窗口 Redis 历史重写仍失败: %s", exc)
            td_tables = window.get("td_tables") or []
            if td_tables:
                failed = await self._write_td_chunks(td_tables)
                if failed:
                    confirmed = False
                    window["td_tables"] = failed
            if confirmed:
                end = window.get("end")
                if end is not None:
                    self._confirmed_boundary = max(self._confirmed_boundary or 0.0, end)
                for loop_part, row_ts in (window.get("loop_row_ts") or {}).items():
                    self._advance_loop_watermark(loop_part, float(row_ts))
                logger.info(
                    "未确认窗口 [%s, %s] 重写成功，已确认", window.get("start"), window.get("end")
                )
            else:
                remaining.append(window)
        self._unconfirmed_windows = remaining
        if not remaining and self._confirmed_boundary is not None:
            self._last_flushed_at = max(self._last_flushed_at or 0.0, self._confirmed_boundary)

    def _build_row(self, roles_data: dict[str, dict]) -> tuple | None:
        """构造单行数据；整行无任何已知 sourceTime 时返回 None.

        列顺序: ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality
        对应 st_loop_data 超级表 schema。

        时间戳经显式 astimezone 到目标时区（Asia/Shanghai），
        消除 naive datetime 在 TDengine 侧的 8h 偏移风险。

        R06（2026-09-06 整改）：数值解析统一走 ``app.core.numeric`` 共享契约——
        无效/非有限字面量（NaN/Infinity/1e999/工业异常串）→ None（NULL），
        绝不折算为 0；MODE 经 ``parse_mode_int``（"Infinity" 不再 OverflowError，
        不会炸掉整个批次）；出口经 ``finite_or_none`` 守卫，行值不含非有限数。

        R05（2026-09-06 整改，S0 契约 §4.1）：行 ts = 合并进该行的**所有角色
        sourceTime 的最大值**（经归一化）——10:00:00 PV=5/SP=6 已落行后，
        10:00:10 仅 SP=9 → 新行 ts=10:00:10（PV 取 last-known 5），旧时刻行
        SP 仍为 6，不再用旧 PV 时间承载新角色事件改写旧行。整行无任何已知
        sourceTime → 返回 None（不落 TD/历史缓存，调用方计 ``rows_dropped_no_ts``），
        **不伪造 now()**。
        """
        known_source_ts = _collect_role_source_times(roles_data)
        if not known_source_ts:
            return None
        ts_str = _format_target_ts(max(known_source_ts.values()))

        def _role_float(role: str) -> float | None:
            return finite_or_none(parse_finite_float(roles_data.get(role, {}).get("value")))

        # 提取各角色值（缺失/无效值用 None，_format_row 会转为 NULL）
        pv_val = _role_float("PV")
        sp_val = _role_float("SP")
        op_val = _role_float("OP")
        mode_val = parse_mode_int(roles_data.get("MODE", {}).get("value"))
        pid_p_val = _role_float("PID_P")
        pid_i_val = _role_float("PID_I")
        pid_d_val = _role_float("PID_D")
        pv_quality_val = parse_mode_int(roles_data.get("PV", {}).get("quality"))

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
