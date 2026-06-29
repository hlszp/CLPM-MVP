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
from typing import Any

import websockets

from app.core.config import settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

# Redis 缓存 key 前缀
_REDIS_KEY_PREFIX = "realtime:"
_REDIS_TTL = 60  # 秒
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
        self._buffer: dict[str, dict[str, Any]] = {}  # {loop_part: {role: {"value": ..., "quality": ..., "ts": ...}}}
        self._buffer_lock = asyncio.Lock()

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
        logger.info("实时数据订阅任务已启动 (hub=%s)", settings.SIGNALR_HUB_URL)

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
                logger.warning("实时数据订阅异常: %s，%ds 后重连", exc, settings.SIGNALR_RECONNECT_INTERVAL)
                await asyncio.sleep(settings.SIGNALR_RECONNECT_INTERVAL)

    async def _connect_and_subscribe(self) -> None:
        """连接 Hub 并订阅数据."""
        self._ws = await websockets.connect(settings.SIGNALR_HUB_URL)
        logger.info("已连接实时数据 Hub: %s", settings.SIGNALR_HUB_URL)

        # 查询全部活跃 Tag 并订阅
        tag_codes = await self._get_active_tags()
        if not tag_codes:
            logger.info("无活跃 Tag，等待数据...")
            await asyncio.sleep(30)
            return

        # 发送订阅请求
        subscribe_msg = json.dumps({
            "method": "SubscribeAsync",
            "args": [tag_codes],
        })
        await self._ws.send(subscribe_msg)
        logger.info("已订阅 %d 个 Tag", len(tag_codes))
        self._subscribed_tags = set(tag_codes)

        # 接收初始响应
        raw = await self._ws.recv()
        initial = json.loads(raw)
        if initial.get("code") == 200 and initial.get("data"):
            for item in initial["data"]:
                await self._cache_value(item)

        # 持续接收推送
        async for raw_message in self._ws:
            try:
                msg = json.loads(raw_message)
                if msg.get("event") == "updateRealValues":
                    for item in msg.get("data", []):
                        await self._cache_value(item)
            except json.JSONDecodeError:
                logger.warning("收到非 JSON 消息: %s", raw_message[:100])
            except Exception as exc:  # noqa: BLE001
                logger.warning("处理实时数据消息失败: %s", exc)

    async def _get_active_tags(self) -> list[str]:
        """查询数据库获取全部活跃 Tag 的 tag_name."""
        try:
            from sqlalchemy import select

            from app.core.db import AsyncSessionLocal
            from app.models.tag import TagRegistry

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(TagRegistry.tag_name).where(TagRegistry.is_linked.is_(True))
                )
                return [row[0] for row in result.all()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询活跃 Tag 失败: %s", exc)
            return []

    async def _cache_value(self, item: dict) -> None:
        """将实时值缓存到 Redis + 放入 TDengine 写入缓冲区 + Pub/Sub 广播."""
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

        # 放入 TDengine 写入缓冲区
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
        for tc, val in zip(tag_codes, values, strict=False):
            if val:
                try:
                    result.append(json.loads(val))
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    async def _flush_loop(self) -> None:
        """每秒 flush 一次缓冲区到 TDengine."""
        while self._running:
            try:
                await asyncio.sleep(1.0)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("TDengine flush 异常: %s", exc)

    async def _flush_buffer(self) -> None:
        """将缓冲区数据写入 TDengine."""
        async with self._buffer_lock:
            if not self._buffer:
                return
            buffer_copy = dict(self._buffer)
            self._buffer.clear()

        for loop_part, roles_data in buffer_copy.items():
            await self._write_loop_data(loop_part, roles_data)

    async def _write_loop_data(self, loop_part: str, roles_data: dict[str, dict]) -> None:
        """将单回路数据写入 TDengine.

        TDengine 表结构（st_loop_data）：
            ts TIMESTAMP, pv FLOAT, sp FLOAT, op FLOAT, mode TINYINT,
            pid_p FLOAT, pid_i FLOAT, pid_d FLOAT, pv_quality TINYINT
        """
        try:
            from app.core.tdengine import execute_sql
            from app.core.config import settings

            # 子表命名: d_loop_<位号小写连字符转下划线>
            import re
            subtable = "d_loop_" + loop_part.lower().replace("-", "_").replace(".", "_")
            subtable = re.sub(r"_+", "_", subtable)

            # 获取时间戳（取任意一个角色的时间戳）
            ts_str = ""
            for role_data in roles_data.values():
                ts_str = role_data.get("ts", "")
                if ts_str:
                    break
            if not ts_str:
                from datetime import datetime, timezone
                ts_str = datetime.now(timezone.utc).isoformat()

            # 构建 INSERT 语句：缺失值用 NULL
            # 列顺序: ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality
            pv_val = roles_data.get("PV", {}).get("value")
            sp_val = roles_data.get("SP", {}).get("value")
            op_val = roles_data.get("OP", {}).get("value")
            mode_val = roles_data.get("MODE", {}).get("value")
            pid_p_val = roles_data.get("PID_P", {}).get("value")
            pid_i_val = roles_data.get("PID_I", {}).get("value")
            pid_d_val = roles_data.get("PID_D", {}).get("value")
            pv_quality_val = roles_data.get("PV", {}).get("quality")

            def fmt_val(v: Any) -> str:
                if v is None or v == "":
                    return "NULL"
                try:
                    # 数值类型：去掉引号
                    return str(float(v))
                except (ValueError, TypeError):
                    # 非数值（如 MODE 可能是字符串）：加引号
                    return f"'{str(v)}'"

            sql = (
                f"INSERT INTO {settings.TDENGINE_DB}.{subtable} VALUES "
                f"('{ts_str}', {fmt_val(pv_val)}, {fmt_val(sp_val)}, {fmt_val(op_val)}, "
                f"{fmt_val(mode_val)}, {fmt_val(pid_p_val)}, {fmt_val(pid_i_val)}, "
                f"{fmt_val(pid_d_val)}, {fmt_val(pv_quality_val)})"
            )

            result = await execute_sql(sql)
            if not result:
                logger.debug("TDengine 写入 %s 成功", loop_part)
            else:
                logger.warning("TDengine 写入 %s 返回: %s", loop_part, result[:50])

        except Exception as exc:  # noqa: BLE001
            logger.warning("写入 TDengine 失败 (%s): %s", loop_part, exc)


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
