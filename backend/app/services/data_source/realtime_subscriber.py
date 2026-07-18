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
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import websockets

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.redis import redis_client
from app.core.tdengine import make_subtable_name
from app.core.tdengine_native import batch_insert_multi
from app.models.tag import TagRegistry

logger = logging.getLogger(__name__)

# Redis 缓存 key 前缀
_REDIS_KEY_PREFIX = "realtime:"
_REDIS_TTL = 3600  # 秒（1 小时），确保页面刷新时能从缓存读取实时值
_PUBSUB_CHANNEL = "realtime:updates"  # Pub/Sub 频道，供 WebSocket 端点订阅

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
        self._ws: Any = None
        self._running = False
        self._subscribed_tags: set[str] = set()
        self._buffer: dict[
            str, dict[str, Any]
        ] = {}  # {loop_part: {role: {"value": ..., "quality": ..., "ts": ...}}}
        self._buffer_lock = asyncio.Lock()

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
        self._task = asyncio.create_task(self._run())
        self._flush_task = asyncio.create_task(self._flush_loop())
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
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        # 停止前 flush 剩余数据
        await self._flush_buffer()
        logger.info("实时数据订阅已停止")

    async def _run(self) -> None:
        """主循环：连接 → 订阅 → 接收 → 重连."""
        while self._running:
            try:
                await self._connect_and_subscribe()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "实时数据订阅异常: %s，%ds 后重连", exc, settings.SIGNALR_RECONNECT_INTERVAL
                )
                await asyncio.sleep(settings.SIGNALR_RECONNECT_INTERVAL)
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

        self._ws = await websockets.connect(settings.SIGNALR_HUB_URL)
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
        subscribe_msg = (
            json.dumps({"type": 1, "target": "SubscribeAsync", "arguments": [tag_codes]}) + "\x1e"
        )
        await self._ws.send(subscribe_msg)
        logger.info("已订阅 %d 个 Tag", len(tag_codes))
        self._subscribed_tags = set(tag_codes)

        # 接收初始响应（可能包含多条 \x1e 分隔的消息）
        raw = await self._ws.recv()
        for part in raw.split("\x1e"):
            if not part:
                continue
            initial = json.loads(part)
            # 兼容两种格式：标准 SignalR 或自定义
            data = initial.get("data") or initial.get("arguments", [None])[0]
            if initial.get("code") == 200 and data:
                for item in data:
                    await self._cache_value(item)

        # 持续接收推送（一条 WebSocket 帧可能包含多条 \x1e 分隔的消息）
        async for raw_message in self._ws:
            for part in raw_message.split("\x1e"):
                if not part:
                    continue
                try:
                    msg = json.loads(part)
                    # 兼容标准 SignalR（target/arguments）和自定义（event/data）两种格式
                    target = msg.get("target") or msg.get("event")
                    if target == "updateRealValues":
                        data = msg.get("data") or msg.get("arguments", [[]])[0]
                        for item in data:
                            await self._cache_value(item)
                except json.JSONDecodeError:
                    logger.warning("收到非 JSON 消息: %s", part[:100])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("处理实时数据消息失败: %s", exc)

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

    def _parse_tag_code(self, tag_code: str) -> tuple[str, str]:
        """解析 tagCode 为回路部分和角色。

        示例: "LIC-101.PV" → ("LIC-101", "PV")
              "LIC-101" → ("LIC-101", "PV")
        """
        if "." in tag_code:
            loop_part, role = tag_code.rsplit(".", 1)
            return loop_part, role.upper()
        return tag_code, "PV"

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
        """将缓冲区数据批量写入 Redis 1 小时缓存（及可选的 TDengine）。"""
        async with self._buffer_lock:
            if not self._buffer:
                return
            buffer_copy = dict(self._buffer)
            self._buffer.clear()

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
                tables_rows.append(
                    {
                        "subtable": subtable,
                        "loop_id": "",
                        "unit_id": "",
                        "rows": [row],
                    }
                )

        try:
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 历史数据写入失败: %s", exc)

        # 2. 批量写入 TDengine (如果启用)
        if not tables_rows:
            return

        # 重试逻辑（3 次，指数退避）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                count = await batch_insert_multi(tables_rows)
                logger.debug("批量写入 %d 行到 %d 个子表", count, len(tables_rows))
                return
            except Exception as exc:  # noqa: BLE001
                if attempt < max_retries - 1:
                    wait = 0.5 * (2**attempt)  # 0.5s, 1s, 2s
                    logger.warning(
                        "批量写入失败 (尝试 %d/%d): %s，%ds 后重试",
                        attempt + 1,
                        max_retries,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error("批量写入最终失败 (%d 个子表): %s", len(tables_rows), exc)

    def _build_row(self, roles_data: dict[str, dict]) -> tuple:
        """构造单行数据。

        列顺序: ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality
        对应 st_loop_data 超级表 schema。
        """
        # 获取时间戳（取任意一个角色的时间戳）
        ts_str = ""
        for role_data in roles_data.values():
            ts_str = role_data.get("ts", "")
            if ts_str:
                break
        if not ts_str:
            ts_str = datetime.now(UTC).isoformat()

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
