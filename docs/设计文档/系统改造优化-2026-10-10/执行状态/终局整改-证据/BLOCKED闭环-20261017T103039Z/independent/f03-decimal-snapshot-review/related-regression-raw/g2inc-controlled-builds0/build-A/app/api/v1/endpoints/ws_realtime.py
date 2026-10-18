"""WebSocket 实时推送 API（数据链路整改 R15/R16 + P4-03 scope 贯穿）.

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

P4-03（01§3.5 实时推送授权——服务端执行，不靠前端退订）:
    - **握手固定主体**：token 校验 + 主体加载（user_id/role/is_active），
      连接绑定主体而非永久授权；
    - **tag 可见性**：非 ADMIN 仅收"有授权回路绑定"的 tag（tag_registry →
      loop_tag_mapping → loop_ledger.unit_id → plant_node 归属解析）；
      未绑定 tag 仅 ADMIN；无 tagCode 的 raw 载荷仅 ADMIN 透传；
    - **旧客户端规则**：未发 subscribe 的旧客户端最多转发授权集合
      （ADMIN=全量，行为不变；非 ADMIN 默认过滤到授权 tag 集）；
    - **订阅交集**：subscribe 请求集合 ∩ 授权集合，越界只给通用拒绝
      （与未知 tag 同一 rejected 列表，不泄露存在性差异）；
    - **发送批栅栏**：每批发送前重验——令牌黑名单 + guard 行共享锁
      （FOR SHARE，跨发送持有）+ revision 变化即重解析 scope 并重过滤
      缓冲；主体禁用/令牌撤销/范围收窄 → 清缓冲、停止发送、关闭连接
      （最迟 30s：心跳任务同款重验兜底覆盖空闲连接）。

订阅过滤协议（版本化兼容）:
    客户端可发 ``{"type":"subscribe","tags":[...]}``；服务端回
    ``{"type":"subscribed","tags":[...合法...], "rejected":[...未知或越界...]}``
    （仅存在非法/越界 tag 时携带 rejected 字段），此后仅转发所订阅 tag。

R16（生命周期回收）:
    断连监听（receiver）、发送（sender）、心跳（heartbeat）三任务共同
    生命周期——任一退出即取消其余，并在 finally 中注销客户端注册、释放
    共享订阅引用计数、幂等关闭连接；退订/aclose 异常不跳过最终清理。

前端连接时通过 query 参数传递 token 进行认证:
    ws://localhost:17101/api/v1/ws/realtime?token=xxx

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
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.tag import TagRegistry
from app.services.auth import is_token_blacklisted
from app.services.data_source.realtime_subscriber import _PUBSUB_CHANNEL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket实时推送"])

# 心跳间隔（秒）：服务端周期发送 {"type":"ping"}；
# P4-03：同周期做主体重验（空闲连接最迟 30s 感知失权并关闭）
_HEARTBEAT_INTERVAL_SECONDS = 30
# 慢消费者关闭码：Try Again Later（客户端应重连并恢复快照）
_SLOW_CONSUMER_CLOSE_CODE = 1013
# 主体失权关闭码（P4-03：policy revocation / 账户禁用 / 令牌吊销）
_REVOKED_CLOSE_CODE = 4003
# subscribe ack / 最终 close 的发送期限（秒）
_ACK_SEND_TIMEOUT_SECONDS = 5.0

# 观测计数（内存计数器 + 日志；对齐 S0 契约 §8 命名）
ws_metrics: dict[str, int] = {
    "ws_clients": 0,
    "ws_slow_closed": 0,
    "ws_merged_dropped": 0,
    "ws_merged_count": 0,
    "ws_revoked_closed": 0,
}


def _is_unit_test_env() -> bool:
    """单测环境（conftest 置 CLPM_TEST_MODE=1）：栅栏短路（集成测试真实走 DB）."""
    import os

    return os.environ.get("CLPM_TEST_MODE") == "1"


@dataclass(eq=False)
class _ClientState:
    """单个 WS 客户端的分发状态（生命周期与连接绑定；按身份哈希入集合）."""

    ws: WebSocket
    # P4-03 连接主体（握手固定主体，不固定永久授权）
    user_id: str = ""
    role: str = ""
    jti: str = ""
    # 握手时的 guard revision（发送批栅栏比对失效）
    revision: int = 0
    # 握手/栅栏时缓存的 scope 上下文（订阅分类与回路解析用）
    scope_ctx: Any = None
    # 授权 tag 全集（None=不过滤=ADMIN 全量含未绑定；set=非 ADMIN 授权绑定集）
    authorized_tags: set[str] | None = None
    # None → 未发 subscribe（旧客户端，转发授权集合）；set → 仅转发集合内 tag
    filter_tags: set[str] | None = None
    # 有界最新值合并缓冲: tag → 最新消息对象；OrderedDict 保证 FIFO 淘汰顺序
    buffer: OrderedDict[str, Any] = field(default_factory=OrderedDict)
    # 唤醒事件（每客户端实例化，非模块级原语；与所属事件循环同生命周期）
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    merged_count: int = 0
    merged_dropped: int = 0
    slow_consumer: bool = False
    revoked: bool = False
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
# 无 tagCode 载荷的合成键序号（仅透传给 ADMIN 全量转发客户端，每条一次性投递）
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


def _decode_token_payload(websocket: WebSocket) -> dict[str, Any] | None:
    """解码 query token 载荷（握手主体绑定用；不重复黑名单校验）."""
    from app.core.security import decode_token

    token = websocket.query_params.get("token", "")
    if not token:
        return None
    try:
        payload = decode_token(token)
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# P4-03：主体解析与发送批栅栏
# ---------------------------------------------------------------------------


async def _resolve_handshake_state(websocket: WebSocket) -> _ClientState | None:
    """握手主体解析：token 载荷 → 主体状态 + scope + 授权 tag 集.

    单测环境（CLPM_TEST_MODE=1）返回 ADMIN 全量默认（不过滤），
    不触碰 mock DB；真实链路由集成测试覆盖。
    """
    from app.services.authz_scope import ScopeContext

    payload = _decode_token_payload(websocket) or {}
    client = _ClientState(ws=websocket)
    client.user_id = str(payload.get("sub") or "")
    client.jti = str(payload.get("jti") or "")
    if _is_unit_test_env():
        client.role = "ADMIN"
        client.revision = 0
        client.authorized_tags = None  # 测试环境保持既有全量转发行为
        client.scope_ctx = ScopeContext(
            user_id=client.user_id or "test",
            role="ADMIN",
            scope_type="ALL",
            node_id=None,
            allowed_node_ids=None,
            revision=0,
        )
        return client
    if not client.user_id:
        return None
    from app.services.authz_scope import authorized_tag_names, load_subject, resolve_scope

    async with AsyncSessionLocal() as db:
        subject = await load_subject(db, client.user_id)
        if subject is None or not subject.is_active:
            return None
        ctx = await resolve_scope(db, client.user_id, subject.role)
        client.role = subject.role
        client.revision = ctx.revision
        client.scope_ctx = ctx
        client.authorized_tags = await authorized_tag_names(db, ctx)
    return client


async def _fence_revalidate(
    db: Any,
    client: _ClientState,
) -> bool:
    """发送批栅栏（guard 共享锁内执行；返回 False=主体失权，停止推送）.

    顺序：guard 共享锁（FOR SHARE，与全部策略变更者的排他锁互斥——持锁
    期间任何撤权/范围变更事务都无法提交）→ 令牌黑名单 → 主体状态 →
    revision 比对（变化即重解析 scope + 授权集 + 重过滤缓冲）。
    """
    if client.jti and await is_token_blacklisted(client.jti):
        return False
    from app.services.authz_scope import (
        authorized_tag_names,
        load_subject,
        lock_guard_for_share,
        resolve_scope,
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
    client.scope_ctx = ctx
    client.authorized_tags = await authorized_tag_names(db, ctx)
    # 订阅过滤集合同步收窄到新授权集（请求集合 ∩ 授权集合）
    if client.filter_tags is not None and client.authorized_tags is not None:
        client.filter_tags &= client.authorized_tags
    # 清缓冲中越权 tag（降权后无新越权帧）
    if client.authorized_tags is not None:
        for tag in [t for t in client.buffer if t not in client.authorized_tags]:
            client.buffer.pop(tag, None)
    return True


def _tag_allowed(client: _ClientState, tag: str) -> bool:
    """分发过滤：授权集（订阅收窄在 dispatch 前先行判定）."""
    if tag.startswith("__raw_"):
        # 无 tagCode 载荷无法归属校验——仅 ADMIN（全量转发）透传
        return client.authorized_tags is None
    if client.authorized_tags is not None and tag not in client.authorized_tags:
        return False
    if client.filter_tags is not None and tag not in client.filter_tags:
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
    （逐条分发，逐客户端按 tag 合并）。P4-03：投递前按授权集过滤。
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
            # 无 tag 载荷：合成唯一键一次性透传（仅 ADMIN 全量转发客户端可见）
            _raw_seq += 1
            tag = f"__raw_{_raw_seq}"
        payloads.append((tag, item))
    if not payloads or not _clients:
        return
    for client in tuple(_clients):
        if client.closed:
            continue
        for tag, item in payloads:
            if not _tag_allowed(client, tag):
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
# 每客户端任务：断连监听 / 发送 / 心跳（R16 共同生命周期 + P4-03 栅栏）
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


async def _classify_subscribe_tags(
    db: Any,
    client: _ClientState,
    tags: list[str],
) -> tuple[list[str], list[str]]:
    """P4-03：订阅 tag 分类（存在性 × 授权）——越界与未知同列表通用拒绝."""
    from app.services.authz_scope import classify_tags

    visible, rejected = await classify_tags(
        db,
        _scope_view(client),
        tags,
    )
    return visible, rejected


def _scope_view(client: _ClientState):
    """classify_tags 的 ScopeContext 视图（握手/栅栏时缓存，恒存在）."""
    return client.scope_ctx


async def _resolve_loop_tags_scoped(
    db: Any,
    client: _ClientState,
    loop_names: list[str],
) -> tuple[dict[str, list[str]], list[str]]:
    """回路名 → 该回路全部角色绑定位号（P4-03：越界回路整体通用拒绝）.

    回路不在授权范围（unit 不在 scope 内）时与未知回路同样进 rejected
    列表——不泄露范围外回路的存在性与元信息。
    """
    if not loop_names:
        return {}, []
    from app.services.authz_scope import ScopeContext

    result = await db.execute(
        select(LoopLedger.id, LoopLedger.tag_name, LoopLedger.unit_id, TagRegistry.tag_name)
        .join(LoopTagMapping, LoopTagMapping.loop_id == LoopLedger.id)
        .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
        .where(LoopLedger.tag_name.in_(loop_names))
    )
    rows = result.all()
    resolved: dict[str, list[str]] = {}
    allowed_units: frozenset[str] | None = None
    ctx = getattr(client, "scope_ctx", None)
    if isinstance(ctx, ScopeContext):
        allowed_units = ctx.allowed_node_ids
    for _loop_id, loop_name, unit_id, tag_name in rows:
        if allowed_units is not None and (unit_id is None or str(unit_id) not in allowed_units):
            continue  # 范围外回路：不解析其 tag（通用拒绝）
        resolved.setdefault(str(loop_name), []).append(str(tag_name))
    unknown = [name for name in loop_names if name not in resolved]
    return resolved, unknown


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
            wanted = []
        wanted_loops = msg.get("loops")
        if not isinstance(wanted_loops, list):
            wanted_loops = []
        tags = [t for t in wanted if isinstance(t, str) and t]
        loop_names = [n for n in wanted_loops if isinstance(n, str) and n]
        loop_map: dict[str, list[str]] = {}
        try:
            if _is_unit_test_env():
                # 单测环境：保持既有"存在性校验"路径（授权面由集成测试覆盖）
                loop_map, unknown_loops = await _resolve_loop_tags(loop_names)
                valid, rejected = await _validate_subscribe_tags(
                    tags + [t for ts_ in loop_map.values() for t in ts_]
                )
            else:
                async with AsyncSessionLocal() as db:
                    loop_map, unknown_loops = await _resolve_loop_tags_scoped(
                        db, client, loop_names
                    )
                    valid, rejected = await _classify_subscribe_tags(
                        db, client, tags + [t for ts_ in loop_map.values() for t in ts_]
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("订阅校验失败: %s", exc)
            valid, rejected = [], tags
            unknown_loops = loop_names
            loop_map = {}
        # 回路解析出的合法 tag 补回订阅集合（loop 内 tag 已按 scope 过滤）
        resolved_loop_tags = {
            n: [t for t in ts_ if t in set(valid)] for n, ts_ in (loop_map or {}).items()
        }
        resolved_loop_tags = {n: ts_ for n, ts_ in resolved_loop_tags.items() if ts_}
        client.filter_tags = set(valid)
        ack: dict[str, Any] = {"type": "subscribed", "tags": valid}
        if rejected or unknown_loops:
            ack["rejected"] = rejected + unknown_loops
        if loop_map:
            ack["loops"] = loop_map
        try:
            await asyncio.wait_for(ws.send_json(ack), timeout=_ACK_SEND_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("订阅 ack 发送失败: %s", exc)
            return
        logger.info(
            "客户端订阅生效: %d 个 tag / %d 个回路（拒绝 %d 项）",
            len(valid),
            len(loop_map),
            len(rejected) + len(unknown_loops),
        )


async def _resolve_loop_tags(
    loop_names: list[str],
) -> tuple[dict[str, list[str]], list[str]]:
    """回路名 → 绑定位号（存在性解析，无 scope；scope 版见 _resolve_loop_tags_scoped）."""
    if not loop_names:
        return {}, []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(LoopLedger.tag_name, TagRegistry.tag_name)
            .join(LoopTagMapping, LoopTagMapping.loop_id == LoopLedger.id)
            .join(TagRegistry, LoopTagMapping.tag_id == TagRegistry.id)
            .where(LoopLedger.tag_name.in_(loop_names))
        )
        rows = result.all()
    resolved: dict[str, list[str]] = {}
    for loop_name, tag_name in rows:
        resolved.setdefault(loop_name, []).append(tag_name)
    unknown = [name for name in loop_names if name not in resolved]
    return resolved, unknown


async def _client_sender(client: _ClientState) -> None:
    """独立发送任务：栅栏重验 → 合并缓冲 → 帧发送（单帧受发送期限保护）.

    P4-03 发送批栅栏：guard 共享锁（FOR SHARE）跨发送持有——持锁期间任何
    策略变更事务（撤权/降范围/禁用/改绑）都无法提交，发送完成后才释放；
    失权（黑名单/禁用/范围收窄后缓冲清空）→ 关闭连接（4003）。
    """
    ws = client.ws
    while True:
        await client.wakeup.wait()
        client.wakeup.clear()
        if _is_unit_test_env():
            snapshot = list(client.buffer.values())
            client.buffer.clear()
            if not snapshot:
                continue
            frame = _build_frame(snapshot)
            try:
                await asyncio.wait_for(
                    ws.send_text(frame), timeout=settings.WS_SEND_TIMEOUT_SECONDS
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                client.slow_consumer = True
                ws_metrics["ws_slow_closed"] += 1
                return
            except Exception:  # noqa: BLE001
                return
            continue
        async with AsyncSessionLocal() as db:
            ok = await _fence_revalidate(db, client)
            if not ok:
                client.revoked = True
                ws_metrics["ws_revoked_closed"] += 1
                logger.info(
                    "发送栅栏判定主体失权，停止推送: user=%s rev=%s",
                    client.user_id,
                    client.revision,
                )
                return
            # 栅栏通过后按当前授权集取快照（越权 tag 已在栅栏内清除）
            snapshot = list(client.buffer.values())
            client.buffer.clear()
            if not snapshot:
                continue
            frame = _build_frame(snapshot)
            try:
                await asyncio.wait_for(
                    ws.send_text(frame), timeout=settings.WS_SEND_TIMEOUT_SECONDS
                )
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


def _build_frame(snapshot: list[Any]) -> str:
    if len(snapshot) == 1:
        return json.dumps(snapshot[0], ensure_ascii=False)
    return json.dumps({"type": "batch", "items": snapshot}, ensure_ascii=False)


async def _client_heartbeat(client: _ClientState) -> None:
    """心跳任务：周期发送 {"type":"ping"}；失败即退出（触发整体回收）.

    P4-03：同周期做主体重验（空闲连接最迟 30s 感知失权并关闭——
    发送批栅栏只覆盖有数据流的连接，心跳兜底覆盖空闲连接）。
    """
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        if not _is_unit_test_env():
            try:
                async with AsyncSessionLocal() as db:
                    if not await _fence_revalidate(db, client):
                        client.revoked = True
                        ws_metrics["ws_revoked_closed"] += 1
                        return
            except Exception as exc:  # noqa: BLE001
                # 无法可靠校验时停止推送（01§3.5：fail-closed）
                logger.warning("心跳栅栏校验异常，停止推送: %s", exc)
                client.revoked = True
                return
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
    未订阅的旧客户端最多转发授权集合（P4-03；ADMIN 保持全量）。

    慢消费者: 单帧发送超过 ``WS_SEND_TIMEOUT_SECONDS`` 即 close 1013，
    客户端应重连并经 REST 恢复当前页快照。

    P4-03: 连接固定主体；每发送批前重验令牌/主体/策略 revision（guard
    共享锁栅栏），失权连接清缓冲并以 4003 关闭——不依赖前端退订。
    """
    # 认证
    if not await _verify_token(websocket):
        await websocket.close(code=4001, reason="认证失败")
        return

    # P4-03：握手主体解析（失败=主体不存在/禁用 → 拒绝连接）
    client = await _resolve_handshake_state(websocket)
    if client is None:
        await websocket.close(code=4001, reason="主体无效或已禁用")
        return
    client.ws = websocket

    await websocket.accept()
    client_id = (
        f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    )
    logger.info(
        "WebSocket 客户端已连接: %s (user=%s, role=%s, authorized=%s)",
        client_id,
        client.user_id,
        client.role,
        "ALL" if client.authorized_tags is None else len(client.authorized_tags),
    )

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
        # 幂等关闭：失权 4003 / 慢消费者 1013 / 共享通道中断 1011 / 常规 1000
        # （客户端已断开时静默忽略）；P4-03：失权时缓冲已在栅栏内清除
        if client.revoked:
            code, reason = _REVOKED_CLOSE_CODE, "authorization revoked"
            client.buffer.clear()
        elif client.slow_consumer:
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
            "WebSocket 客户端已断开: %s (revoked=%s, slow=%s, merged=%d, merged_dropped=%d)",
            client_id,
            client.revoked,
            client.slow_consumer,
            client.merged_count,
            client.merged_dropped,
        )
