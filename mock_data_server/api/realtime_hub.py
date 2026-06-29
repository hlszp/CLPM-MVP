"""模拟远端数据服务 — WebSocket Hub 端点.

模拟 SignalR Hub ``/signalr/realValueForClpmHub``，使用原生 WebSocket 实现。

消息协议遵循 RealDATA_API.md：
- 客户端→服务端: {"method": "SubscribeAsync", "args": [["TAG001", "TAG002"]]}
- 服务端→客户端: {"event": "updateRealValues", "data": [...]}
- 初始订阅返回: {"code": 200, "message": "success", "data": [...]}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from mock_data_server.config import config
from mock_data_server.services.realtime_generator import get_generator

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """WebSocket 连接管理器."""

    def __init__(self) -> None:
        self._subscriptions: dict[WebSocket, set[str]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._subscriptions[websocket] = set()
        logger.info("WebSocket 客户端已连接，当前连接数: %d", len(self._subscriptions))

    def disconnect(self, websocket: WebSocket) -> None:
        self._subscriptions.pop(websocket, None)
        logger.info("WebSocket 客户端已断开，当前连接数: %d", len(self._subscriptions))

    def subscribe(self, websocket: WebSocket, tag_codes: list[str]) -> list[dict]:
        """订阅 tag，返回当前实时值."""
        self._subscriptions.setdefault(websocket, set()).update(tag_codes)
        generator = get_generator()
        return generator.generate_batch(tag_codes)

    def unsubscribe(self, websocket: WebSocket, tag_codes: list[str]) -> None:
        """取消订阅 tag."""
        sub = self._subscriptions.get(websocket, set())
        sub.difference_update(tag_codes)

    def unsubscribe_all(self, websocket: WebSocket) -> None:
        """取消全部订阅."""
        self._subscriptions[websocket] = set()

    async def broadcast_updates(self) -> None:
        """定时推送实时数据更新给所有连接的客户端."""
        while True:
            await asyncio.sleep(config.REALTIME_INTERVAL)

            for websocket, tag_codes in list(self._subscriptions.items()):
                if not tag_codes:
                    continue
                try:
                    generator = get_generator()
                    updates = generator.generate_batch(list(tag_codes))
                    message = json.dumps({"event": "updateRealValues", "data": updates})
                    await websocket.send_text(message)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("推送实时数据失败: %s", exc)
                    self._subscriptions.pop(websocket, None)


manager = ConnectionManager()


@router.websocket("/signalr/realValueForClpmHub")
async def realtime_hub(websocket: WebSocket) -> None:
    """SignalR Hub 模拟端点.

    消息格式：
    - 订阅: {"method": "SubscribeAsync", "args": [["TAG001", "TAG002"]]}
    - 取消订阅: {"method": "UnsubscribeAsync", "args": [["TAG001"]]}
    - 取消全部: {"method": "UnsubscribeAllAsync", "args": []}
    """
    await manager.connect(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(
                    json.dumps({"code": 400, "message": "Invalid JSON"})
                )
                continue

            method = msg.get("method", "")
            args = msg.get("args", [])

            if method == "SubscribeAsync":
                tag_codes = args[0] if args else []
                initial_data = manager.subscribe(websocket, tag_codes)
                response = {
                    "code": 200,
                    "message": "success",
                    "data": initial_data,
                }
                await websocket.send_text(json.dumps(response))
                logger.info("客户端订阅 %d 个 tag", len(tag_codes))

            elif method == "UnsubscribeAsync":
                tag_codes = args[0] if args else []
                manager.unsubscribe(websocket, tag_codes)
                response = {"code": 200, "message": "success"}
                await websocket.send_text(json.dumps(response))

            elif method == "UnsubscribeAllAsync":
                manager.unsubscribe_all(websocket)
                response = {"code": 200, "message": "success"}
                await websocket.send_text(json.dumps(response))

            else:
                await websocket.send_text(
                    json.dumps({"code": 400, "message": f"Unknown method: {method}"})
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebSocket 异常: %s", exc)
        manager.disconnect(websocket)


async def start_broadcast_task() -> None:
    """启动实时数据推送后台任务（应用启动时调用）."""
    asyncio.create_task(manager.broadcast_updates())
    logger.info("实时数据推送任务已启动（间隔 %.1fs）", config.REALTIME_INTERVAL)
