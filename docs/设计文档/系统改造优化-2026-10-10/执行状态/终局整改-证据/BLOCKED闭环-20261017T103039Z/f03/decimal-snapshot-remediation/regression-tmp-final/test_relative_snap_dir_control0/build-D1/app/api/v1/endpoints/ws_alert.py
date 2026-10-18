"""WebSocket 预警实时推送 API（P4-03 scope 贯穿）.

提供 ``WS /api/v1/ws/alerts`` 端点，通过 Redis Pub/Sub 接收
规则引擎 dispatcher 发布的预警通知，推送给已连接的前端客户端。

数据流:
    Celery patrol / API → dispatcher._notify
      → Redis Pub/Sub("alert:notify")
      → 本端点订阅 → WebSocket 推送给前端

P4-03（01§3.5：预警按其关联 loop/node 过滤 + 发送前重验）:
    - 握手固定主体（token → user_id/role/is_active + scope）；
    - 每条预警发送前：载荷关联 loopId 按范围过滤（无 loopId 的预警仅
      ADMIN 可收）+ 发送栅栏（guard 共享锁重验令牌/主体/revision，
      revision 变化即重解析范围）；
    - 主体失权（禁用/令牌撤销/范围收窄）→ 停止推送并以 4003 关闭，
      不依赖前端退订。

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
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.db import AsyncSessionLocal
from app.core.redis import redis_client
from app.services.alert_rule_engine.dispatcher import NOTIFY_CHANNEL
from app.services.auth import is_token_blacklisted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket预警推送"])

# 主体失权关闭码（P4-03）
_REVOKED_CLOSE_CODE = 4003


def _is_unit_test_env() -> bool:
    """单测环境（conftest 置 CLPM_TEST_MODE=1）：scope 短路（集成测试真实走 DB）."""
    return os.environ.get("CLPM_TEST_MODE") == "1"


@dataclass
class _AlertClient:
    """预警连接主体状态（连接固定主体；范围随 revision 变化重解析）."""

    user_id: str = ""
    role: str = ""
    jti: str = ""
    revision: int = 0
    #: 范围内回路全集（None=ALL/ADMIN 不过滤；set=NODE_SUBTREE 范围内回路）
    allowed_loop_ids: set[str] | None = None


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


async def _resolve_alert_client(websocket: WebSocket) -> _AlertClient | None:
    """握手主体解析：token 载荷 → 主体 + 范围内回路集（单测环境 ADMIN 全量）."""
    from app.core.security import decode_token
    from app.services.authz_scope import load_subject, resolve_scope, scoped_loop_ids

    token = websocket.query_params.get("token", "")
    try:
        payload = decode_token(token)
    except Exception:  # noqa: BLE001
        payload = None
    payload = payload if isinstance(payload, dict) else {}
    client = _AlertClient(
        user_id=str(payload.get("sub") or ""),
        jti=str(payload.get("jti") or ""),
    )
    if _is_unit_test_env():
        client.role = "ADMIN"
        return client
    if not client.user_id:
        return None
    async with AsyncSessionLocal() as db:
        subject = await load_subject(db, client.user_id)
        if subject is None or not subject.is_active:
            return None
        ctx = await resolve_scope(db, client.user_id, subject.role)
        client.role = subject.role
        client.revision = ctx.revision
        client.allowed_loop_ids = (
            None if ctx.allowed_node_ids is None else set(await scoped_loop_ids(db, ctx))
        )
    return client


def _alert_allowed(client: _AlertClient, payload: dict[str, Any]) -> bool:
    """预警按其关联 loop/node 过滤（01§3.5）.

    - ADMIN / ALL 范围：全部预警；
    - NODE_SUBTREE：仅 loopId 在范围内回路集的预警；
    - 无 loopId 的预警：仅 ADMIN（无法归属校验时不放宽）。
    """
    if client.role == "ADMIN" or client.allowed_loop_ids is None:
        return True
    loop_id = payload.get("loopId")
    if not isinstance(loop_id, str) or not loop_id:
        return False
    return loop_id in client.allowed_loop_ids


async def _send_fence(db: Any, client: _AlertClient) -> bool:
    """发送栅栏（guard 共享锁内重验；返回 False=失权停止推送）."""
    if client.jti and await is_token_blacklisted(client.jti):
        return False
    from app.services.authz_scope import (
        load_subject,
        lock_guard_for_share,
        resolve_scope,
        scoped_loop_ids,
    )

    revision = await lock_guard_for_share(db)
    if revision == client.revision:
        return True
    subject = await load_subject(db, client.user_id)
    if subject is None or not subject.is_active:
        return False
    ctx = await resolve_scope(db, client.user_id, subject.role, revision=revision)
    client.role = subject.role
    client.revision = revision
    client.allowed_loop_ids = (
        None if ctx.allowed_node_ids is None else set(await scoped_loop_ids(db, ctx))
    )
    return True


@router.websocket("/alerts")
async def alerts_websocket(websocket: WebSocket) -> None:
    """WebSocket 端点：实时推送预警事件通知。

    连接后，客户端会持续收到 JSON 格式的预警通知:
    {"type": "alert", "ruleCode": "...", "severity": "WARN", ...}

    心跳: 服务端每 30 秒发送 {"type":"ping"}，客户端可回复 {"type":"pong"}。

    P4-03: 预警按关联 loop/node 过滤 + 每条发送前栅栏重验（失权 → 4003）。
    """
    # 认证
    if not await _verify_token(websocket):
        await websocket.close(code=4001, reason="认证失败")
        return

    # P4-03：握手主体解析（主体无效/禁用 → 拒绝连接）
    client = await _resolve_alert_client(websocket)
    if client is None:
        await websocket.close(code=4001, reason="主体无效或已禁用")
        return

    await websocket.accept()
    client_id = (
        f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    )
    logger.info(
        "预警 WebSocket 客户端已连接: %s (user=%s, role=%s)",
        client_id,
        client.user_id,
        client.role,
    )

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

    def _payload_of(data: str) -> dict[str, Any]:
        try:
            parsed = json.loads(data)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    try:
        # 主循环：从 Pub/Sub 读取消息并推送给客户端
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                # P4-03：按关联 loop/node 过滤（越权预警不进入发送链）
                if not _alert_allowed(client, _payload_of(data)):
                    continue
                # P4-03：发送栅栏——guard 共享锁重验令牌/主体/revision，
                # 共享锁跨发送持有（策略变更事务无法在中间提交）
                if not _is_unit_test_env():
                    async with AsyncSessionLocal() as db:
                        if not await _send_fence(db, client):
                            logger.info(
                                "预警发送栅栏判定主体失权，停止推送: user=%s", client.user_id
                            )
                            await websocket.close(
                                code=_REVOKED_CLOSE_CODE, reason="authorization revoked"
                            )
                            break
                        await websocket.send_text(data)
                else:
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
