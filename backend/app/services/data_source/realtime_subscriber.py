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


class RealtimeSubscriber:
    """实时数据订阅器.

    生命周期：
    1. ``start()`` — 启动后台任务，连接 WebSocket Hub
    2. 定期从数据库查询全部活跃 Tag，发送订阅请求
    3. 接收推送，更新 Redis 缓存
    4. 断线自动重连
    5. ``stop()`` — 停止订阅，关闭连接
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._ws: Any = None
        self._running = False
        self._subscribed_tags: set[str] = set()

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
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
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
        """将实时值缓存到 Redis."""
        tag_code = item.get("tagCode", "")
        if not tag_code:
            return
        key = f"{_REDIS_KEY_PREFIX}{tag_code}"
        value = json.dumps({
            "tagCode": tag_code,
            "value": item.get("value", ""),
            "quality": item.get("quality", 0),
            "collectTime": item.get("collectTime", ""),
        })
        await redis_client.setex(key, _REDIS_TTL, value)

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
