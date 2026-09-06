"""WebSocket 实时推送 API（数据链路整改 R15/R16）.

提供 ``WS /api/v1/ws/realtime`` 端点，通过 Redis Pub/Sub 接收
RealtimeSubscriber 广播的实时数据，推送给已连接的前端客户端。

数据流:
    mock_server → RealtimeSubscriber._cache_value
      → Redis Pub/Sub("realtime:updates")
      → 本端点【进程内唯一共享消费任务】 → 按客户端分发 → WebSocket 推送

R15（共享订阅 + 慢消费者治理）:
    - 每进程仅一条共享 Pub/Sub 消费任务（首个客户端连接时懒启动，
      引用计数归零时关闭）；N 个客户端不再产生 N 条上游订阅。
    - 每客户端持有有界"最新值合并缓冲"（``dict{tag: payload}``，容量
      ``settings.WS_CLIENT_QUEUE_MAX``）：同 tag 仅保留最新值；容量满时
      FIFO 丢弃最旧 tag 并计入 ``merged_dropped``。
    - 每客户端独立发送任务，单帧发送受 ``settings.WS_SEND_TIMEOUT_SECONDS``
      期限保护；超时判定慢消费者 → close 1013（客户端应重连并恢复快照）。
    - 高频积压时合并为批量帧 ``{"type":"batch","items":[...]}``（增量兼容，
      旧客户端忽略未知 type 即可）。

订阅过滤协议（版本化兼容）:
    客户端可发 ``{"type":"subscribe","tags":[...]}``；服务端回
    ``{"type":"subscribed","tags":[...合法...], "rejected":[...未知...]}``
    （仅存在非法 tag 时携带 rejected 字段），此后仅转发所订阅 tag。
    tag 必须存在于 tag_registry，未知 tag 拒绝并在 ack 中列出。
    **未发 subscribe 的旧客户端保持全量转发**（行为不变）。

R16（生命周期回收）:
    断连监听（receiver）、发送（sender）、心跳（heartbeat）三任务共同
    生命周期——任一退出即取消其余，并在 finally 中注销客户端注册、释放
    共享订阅引用计数、幂等关闭连接；退订/aclose 异常不跳过最终清理。

前端连接时通过 query 参数传递 token 进行认证:
    ws://localhost:17101/api/v1/ws/realtime?token=xxx

注: 浏览器原生 WebSocket API 不支持自定义请求头，前端
``src/utils/realtime-ws.ts`` 仅支持 query 传参，故保留 query 方式；
服务端侧与 HTTP 接口对齐，校验 token 类型（必须 access）与黑名单状态。

模块级状态纪律: 共享任务/句柄存模块级**普通变量**（非 asyncio 原语），
懒启动与关闭均发生在同一事件循环内（uvicorn 单 worker 单 loop），
不违反"禁止模块级 asyncio.Lock/Semaphore/Event"红线。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.redis import redis_client
from app.models.tag import TagRegistry
from app.services.auth import is_token_blacklisted
from app.services.data_source.realtime_subscriber import _PUBSUB_CHANNEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket实时推送"])

# 心跳间隔（秒）：服务端周期发送 {"type":"ping"}
_HEARTBEAT_INTERVAL_SECONDS = 30
# 慢消费者关闭码：Try Again Later（客户端应重连并恢复快照）
_SLOW_CONSUMER_CLOSE_CODE = 1013
# subscribe ack / 最终 close 的发送期限（秒）
_ACK_SEND_TIMEOUT_SECONDS = 5.0

# 观测计数（内存计数器 + 日志；对齐 S0 契约 §8 命名）
ws_metrics: dict[str, int] = {
    "ws_clients": 0,
    "ws_slow_closed": 0,
    "ws_merged_dropped": 0,
    "ws_merged_count": 0,
}


@dataclass(eq=False)
class _ClientState:
    """单个 WS 客户端的分发状态（生命周期与连接绑定；按身份哈希入集合）."""

    ws: WebSocket
    # None → 未发 subscribe（旧客户端，全量转发）；set → 仅转发集合内 tag
    filter_tags: set[str] | None = None
    # 有界最新值合并缓冲: tag → 最新消息对象；OrderedDict 保证 FIFO 淘汰顺序
    buffer: OrderedDict[str, Any] = field(default_factory=OrderedDict)
    # 唤醒事件（每客户端实例化，非模块级原语；与所属事件循环同生命周期）
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    merged_count: int = 0
    merged_dropped: int = 0
    slow_consumer: bool = False
    closed: bool = False


@dataclass
class _SharedPubSub:
    """进程内共享 Pub/Sub 消费任务的句柄（引用计数生命周期）."""

    pubsub: Any
    task: asyncio.Task
    refcount: int


# 模块级普通变量（懒启动任务/句柄；同 loop 内操作）
_shared: _SharedPubSub | None = None
_clients: set[_ClientState] = set()
# 无 tagCode 载荷的合成键序号（仅透传给全量转发客户端，每条一次性投递）
_raw_seq: int = 0


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


# ---------------------------------------------------------------------------
# 共享 Pub/Sub 消费任务（R15）
# ---------------------------------------------------------------------------


async def _acquire_shared_pubsub() -> None:
    """获取共享 Pub/Sub 引用；首个客户端懒启动消费任务."""
    global _shared
    if _shared is None or _shared.task.done():
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(_PUBSUB_CHANNEL)
        task = asyncio.create_task(_pubsub_loop(pubsub), name="ws-shared-pubsub")
        _shared = _SharedPubSub(pubsub=pubsub, task=task, refcount=0)
        logger.info("共享 Pub/Sub 消费任务已启动 (channel=%s)", _PUBSUB_CHANNEL)
    _shared.refcount += 1


async def _release_shared_pubsub() -> None:
    """释放共享 Pub/Sub 引用；引用计数归零时关闭（最后一个客户端退出才关）."""
    global _shared
    state = _shared
    if state is None:
        return
    state.refcount -= 1
    if state.refcount > 0:
        return
    _shared = None
    await _shutdown_shared_task(state)
    logger.info("共享 Pub/Sub 引用计数归零，已关闭")


async def _shutdown_shared_task(state: _SharedPubSub) -> None:
    """取消并回收共享消费任务；退订/关闭异常不相互跳过（R16）."""
    if not state.task.done():
        state.task.cancel()
    try:
        await state.task
    except asyncio.CancelledError:
        pass
    except RuntimeError:
        # 任务属于已关闭的其他事件循环（仅测试场景可能出现）：任务已随旧循环终止
        logger.warning("共享 Pub/Sub 任务不在当前事件循环，跳过等待")
    except Exception as exc:  # noqa: BLE001
        logger.warning("共享 Pub/Sub 任务回收异常: %s", exc)


async def _reset_shared_pubsub() -> None:
    """测试钩子：模拟 API shutdown 强制关闭共享订阅（生产由引用计数管理）."""
    global _shared
    state = _shared
    if state is None:
        return
    _shared = None
    for client in tuple(_clients):
        client.closed = True
    _clients.clear()
    ws_metrics["ws_clients"] = 0
    await _shutdown_shared_task(state)


async def _pubsub_loop(pubsub: Any) -> None:
    """共享消费任务：读取 Pub/Sub 消息并按客户端分发."""
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            _dispatch_published(data)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("共享 Pub/Sub 消费任务异常退出: %s", exc)
    finally:
        # 退订失败不得跳过最终 aclose（R16）
        try:
            await pubsub.unsubscribe(_PUBSUB_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pub/Sub 退订失败（继续关闭）: %s", exc)
        try:
            await pubsub.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pub/Sub 关闭失败: %s", exc)


def _dispatch_published(data: str) -> None:
    """将一条 Pub/Sub 载荷解析并投递到各客户端合并缓冲.

    兼容发布侧两种格式：单对象 ``{tagCode,...}`` 与批量数组 ``[{...},...]``
    （逐条分发，逐客户端按 tag 合并）。
    """
    global _raw_seq
    try:
        parsed = json.loads(data)
    except (TypeError, ValueError):
        logger.debug("Pub/Sub 载荷非 JSON，忽略: %.100s", data)
        return
    items = parsed if isinstance(parsed, list) else [parsed]
    payloads: list[tuple[str, Any]] = []
    for item in items:
        tag = item.get("tagCode") if isinstance(item, dict) else None
        if not isinstance(tag, str) or not tag:
            # 无 tag 载荷：合成唯一键一次性透传（仅全量转发客户端可见）
            _raw_seq += 1
            tag = f"__raw_{_raw_seq}"
        payloads.append((tag, item))
    if not payloads or not _clients:
        return
    for client in tuple(_clients):
        if client.closed:
            continue
        for tag, item in payloads:
            if tag.startswith("__raw_"):
                # 无 tagCode 载荷仅对全量转发的旧客户端透传（订阅过滤客户端不接收）
                if client.filter_tags is not None:
                    continue
            elif client.filter_tags is not None and tag not in client.filter_tags:
                continue
            _enqueue(client, tag, item)


def _enqueue(client: _ClientState, tag: str, item: Any) -> None:
    """按 tag 合并最新值入队；容量满时 FIFO 丢弃最旧 tag."""
    buf = client.buffer
    if tag in buf:
        # 同 tag 合并：仅保留最新值（显示通道语义，历史继续查本地存储）
        client.merged_count += 1
        ws_metrics["ws_merged_count"] += 1
    elif len(buf) >= settings.WS_CLIENT_QUEUE_MAX:
        oldest_key, _ = buf.popitem(last=False)
        client.merged_dropped += 1
        ws_metrics["ws_merged_dropped"] += 1
        logger.debug(
            "客户端队列溢出，FIFO 丢弃最旧 tag=%s（merged_dropped=%d）",
            oldest_key,
            client.merged_dropped,
        )
    buf[tag] = item
    client.wakeup.set()


# ---------------------------------------------------------------------------
# 每客户端任务：断连监听 / 发送 / 心跳（R16 共同生命周期）
# ---------------------------------------------------------------------------


async def _validate_subscribe_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """校验订阅 tag 是否存在于 tag_registry（短会话，不占用长连接资源）.

    Returns:
        (合法 tag 列表按请求顺序去重, 未知 tag 列表)
    """
    if not tags:
        return [], []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(TagRegistry.tag_name).where(TagRegistry.tag_name.in_(tags))
        )
        known = {row[0] for row in result}
    valid: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        (valid if tag in known else rejected).append(tag)
    return valid, rejected


async def _client_receiver(client: _ClientState) -> None:
    """断连监听 + 客户端控制消息处理（subscribe 过滤协议）."""
    ws = client.ws
    while True:
        try:
            text = await ws.receive_text()
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("客户端接收异常，断开: %s", exc)
            return
        try:
            msg = json.loads(text)
        except (TypeError, ValueError):
            continue
        if not isinstance(msg, dict) or msg.get("type") != "subscribe":
            # pong 等其他消息忽略
            continue
        wanted = msg.get("tags")
        if not isinstance(wanted, list):
            continue
        tags = [t for t in wanted if isinstance(t, str) and t]
        try:
            valid, rejected = await _validate_subscribe_tags(tags)
        except Exception as exc:  # noqa: BLE001
            logger.warning("订阅 tag 校验失败: %s", exc)
            valid, rejected = [], tags
        client.filter_tags = set(valid)
        ack: dict[str, Any] = {"type": "subscribed", "tags": valid}
        if rejected:
            ack["rejected"] = rejected
        try:
            await asyncio.wait_for(ws.send_json(ack), timeout=_ACK_SEND_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("订阅 ack 发送失败: %s", exc)
            return
        logger.info("客户端订阅生效: %d 个 tag（拒绝 %d 个）", len(valid), len(rejected))


async def _client_sender(client: _ClientState) -> None:
    """独立发送任务：合并缓冲 → 帧发送（单帧受发送期限保护）."""
    ws = client.ws
    while True:
        await client.wakeup.wait()
        client.wakeup.clear()
        snapshot = list(client.buffer.values())
        client.buffer.clear()
        if not snapshot:
            continue
        if len(snapshot) == 1:
            frame = json.dumps(snapshot[0], ensure_ascii=False)
        else:
            frame = json.dumps({"type": "batch", "items": snapshot}, ensure_ascii=False)
        try:
            await asyncio.wait_for(ws.send_text(frame), timeout=settings.WS_SEND_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            client.slow_consumer = True
            ws_metrics["ws_slow_closed"] += 1
            logger.info(
                "慢消费者：单帧发送超时（%.1fs），将以 %d 关闭",
                settings.WS_SEND_TIMEOUT_SECONDS,
                _SLOW_CONSUMER_CLOSE_CODE,
            )
            return
        except Exception:  # noqa: BLE001
            # 发送失败：客户端已不可达，交由 supervisor 统一清理
            return


async def _client_heartbeat(client: _ClientState) -> None:
    """心跳任务：周期发送 {"type":"ping"}；失败即退出（触发整体回收）."""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        try:
            await asyncio.wait_for(
                client.ws.send_json({"type": "ping"}),
                timeout=settings.WS_SEND_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("心跳发送失败，判定客户端失联: %s", exc)
            return


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.websocket("/realtime")
async def realtime_websocket(websocket: WebSocket) -> None:
    """WebSocket 端点：实时推送 Tag 值更新.

    连接后，客户端持续收到 JSON 格式的实时数据（单条为发布侧原样对象，
    高频积压时合并为批量帧）:
    {"tagCode": "80FIC11906_PIDA.PV", "value": "14.218", "quality": 1, "collectTime": "..."}
    {"type": "batch", "items": [<上述对象>, ...]}

    心跳: 服务端每 30 秒发送 {"type":"ping"}，客户端可回复 {"type":"pong"}。

    订阅过滤: 客户端可发 {"type":"subscribe","tags":[...]} 收窄转发集合；
    未订阅的旧客户端保持全量转发。

    慢消费者: 单帧发送超过 ``WS_SEND_TIMEOUT_SECONDS`` 即 close 1013，
    客户端应重连并经 REST 恢复当前页快照。
    """
    # 认证
    if not await _verify_token(websocket):
        await websocket.close(code=4001, reason="认证失败")
        return

    await websocket.accept()
    client_id = (
        f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    )
    logger.info("WebSocket 客户端已连接: %s", client_id)

    client = _ClientState(ws=websocket)

    # 订阅共享 Pub/Sub（首个客户端懒启动；初始化失败同样走关闭路径）
    try:
        await _acquire_shared_pubsub()
    except Exception as exc:  # noqa: BLE001
        logger.warning("共享 Pub/Sub 初始化失败: %s", exc)
        try:
            await websocket.close(code=1011, reason="实时订阅初始化失败")
        except Exception:  # noqa: BLE001
            pass
        return
    shared_state = _shared
    assert shared_state is not None  # 刚获取成功，简化类型收窄

    _clients.add(client)
    ws_metrics["ws_clients"] += 1

    tasks = [
        asyncio.create_task(_client_receiver(client), name=f"ws-recv-{client_id}"),
        asyncio.create_task(_client_sender(client), name=f"ws-send-{client_id}"),
        asyncio.create_task(_client_heartbeat(client), name=f"ws-hb-{client_id}"),
    ]
    try:
        # R16：断连监听/发送/心跳共同生命周期，任一退出即取消其余；
        # 共享消费任务终止（Redis 故障等）同样触发回收——客户端重连后
        # 由 _acquire_shared_pubsub 重建上游订阅。
        await asyncio.wait([*tasks, shared_state.task], return_when=asyncio.FIRST_COMPLETED)
    finally:
        client.closed = True
        for task in tasks:
            if not task.done():
                task.cancel()
        # 取消必须可传播：等待全部任务真正结束，不留残余协程
        await asyncio.gather(*tasks, return_exceptions=True)
        _clients.discard(client)
        ws_metrics["ws_clients"] -= 1
        await _release_shared_pubsub()
        # 幂等关闭：慢消费者 1013 / 共享通道中断 1011 / 常规 1000
        # （客户端已断开时静默忽略）
        if client.slow_consumer:
            code, reason = _SLOW_CONSUMER_CLOSE_CODE, "slow consumer"
        elif shared_state.task.done():
            code, reason = 1011, "实时订阅通道已断开"
        else:
            code, reason = 1000, "server closing"
        try:
            await asyncio.wait_for(
                websocket.close(code=code, reason=reason),
                timeout=settings.WS_SEND_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001
            pass
        logger.info(
            "WebSocket 客户端已断开: %s (slow=%s, merged=%d, merged_dropped=%d)",
            client_id,
            client.slow_consumer,
            client.merged_count,
            client.merged_dropped,
        )
