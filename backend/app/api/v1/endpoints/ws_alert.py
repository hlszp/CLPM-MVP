"""WebSocket 预警实时推送 API.

提供 ``WS /api/v1/ws/alerts`` 端点，通过 Redis Pub/Sub 接收
规则引擎 dispatcher 发布的预警通知，推送给已连接的前端客户端。

数据流:
    Celery patrol / API → dispatcher._notify
      → Redis Pub/Sub("alert:notify")
      → 本端点订阅 → WebSocket 推送给前端

前端连接时通过 query 参数传递 token 进行认证:
    ws://localhost:7101/api/v1/ws/alerts?token=xxx

推送消息格式:
    {"type": "alert", "ruleCode": "...", "ruleName": "...",
     "loopId": "...", "severity": "WARN",
     "triggeredValue": 150.0, "triggeredAt": "...",
     "snapshot": {...}}

心跳: 服务端每 30 秒发送 {"type":"ping"}，客户端可回复 {"type":"pong"}。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.redis import redis_client
from app.services.alert_rule_engine.dispatcher import NOTIFY_CHANNEL
from app.services.auth import is_token_blacklisted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket预警推送"])


async def _verify_token(websocket: WebSocket) -> bool:
    """从 query 参数验证 JWT token.

    与 ``app.api.deps.get_current_user`` 的校验口径对齐：
    - 签名/有效期合法
    - ``type == "access"``（拒绝 refresh token 直连）
    - jti 未在黑名单中（拒绝已吊销 token）
    """
    from app.core.security import decode_token

    token = websocket.query_params.get("token", "")
    if not token:
        return False
    try:
        payload = decode_token(token)
    except Exception:  # noqa: BLE001
        return False
    if not payload or payload.get("sub") is None:
        return False
    if payload.get("type") != "access":
        return False
    jti = payload.get("jti", "")
    if jti and await is_token_blacklisted(jti):
        return False
    return True


@router.websocket("/alerts")
async def alerts_websocket(websocket: WebSocket) -> None:
    """WebSocket 端点：实时推送预警事件通知。

    连接后，客户端会持续收到 JSON 格式的预警通知:
    {"type": "alert", "ruleCode": "...", "severity": "WARN", ...}

    心跳: 服务端每 30 秒发送 {"type":"ping"}，客户端可回复 {"type":"pong"}。
    """
    # 认证
    if not await _verify_token(websocket):
        await websocket.close(code=4001, reason="认证失败")
        return

    await websocket.accept()
    client_id = (
        f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    )
    logger.info("预警 WebSocket 客户端已连接: %s", client_id)

    # 创建 Redis Pub/Sub 订阅
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(NOTIFY_CHANNEL)

    # 心跳任务
    async def _heartbeat() -> None:
        while True:
            try:
                await asyncio.sleep(30)
                await websocket.send_json({"type": "ping"})
            except Exception:  # noqa: BLE001
                break

    heartbeat_task = asyncio.create_task(_heartbeat())

    try:
        # 主循环：从 Pub/Sub 读取消息并推送给客户端
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                await websocket.send_text(data)
            except WebSocketDisconnect:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("预警 WebSocket 推送失败: %s", exc)
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("预警 WebSocket 异常: %s", exc)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await pubsub.unsubscribe(NOTIFY_CHANNEL)
        await pubsub.aclose()
        logger.info("预警 WebSocket 客户端已断开: %s", client_id)
