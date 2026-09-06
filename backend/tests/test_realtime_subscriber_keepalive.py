"""实时订阅器应用层心跳（type=6 Ping/Pong）与连接池分片单元测试.

背景（2026-09-06）：AAS 网关会在无流量数分钟后回收 WebSocket 会话，
协议级 ping 已禁用（AAS 不应答）；订阅器以 SignalR 应用层 ping 作
保活流量与快速探活。连接池化后每条分片连接独立持有心跳状态
（``_ShardState``）。本文件覆盖：
- _split_shards：位号切分（保序、覆盖、末片不满）
- _keepalive_tick：空闲到期发送 Ping / pending 未清不重复发 / 间隔节流
- _handle_ping_frame：我方 Ping 的 Pong 仅清 pending 不回显，
  服务端主动 Ping 才回 Pong（防 Pong 互答风暴）
- _is_ping_dead：pending 超时且期间无数据才判死；有数据流动不判死
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from app.services.data_source.realtime_subscriber import (
    _PING_DEATH_TIMEOUT,
    _PING_KEEPALIVE_INTERVAL,
    RealtimeSubscriber,
    _ShardState,
    _split_shards,
)


class _FakeWs:
    """记录 send 调用的假 WebSocket."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


def _make_subscriber() -> RealtimeSubscriber:
    """构造仅用于心跳/分片纯逻辑的订阅器实例（不启动后台任务）."""
    return RealtimeSubscriber()


def _make_state(**kwargs) -> _ShardState:
    """构造带假 WebSocket 的分片状态."""
    st = _ShardState(index=0, total=1, tags=["TAG_A_PV", "TAG_B_PV"], **kwargs)
    st.ws = _FakeWs()
    return st


# ---- _split_shards ----


def test_split_shards_even() -> None:
    shards = _split_shards(["a", "b", "c", "d"], 2)
    assert shards == [["a", "b"], ["c", "d"]]


def test_split_shards_uneven_tail() -> None:
    shards = _split_shards(["a", "b", "c"], 2)
    assert shards == [["a", "b"], ["c"]]


def test_split_shards_single_and_empty() -> None:
    assert _split_shards(["a"], 1000) == [["a"]]
    assert _split_shards([], 1000) == []


def test_split_shards_full_coverage_and_order() -> None:
    tags = [f"TAG_{i:05d}" for i in range(2500)]
    shards = _split_shards(tags, 1000)
    assert [len(s) for s in shards] == [1000, 1000, 500]
    assert [t for s in shards for t in s] == tags  # 保序且全覆盖、无重复


# ---- _keepalive_tick ----


async def test_keepalive_tick_sends_ping_when_due() -> None:
    sub = _make_subscriber()
    st = _make_state()
    st.last_ping_sent_at = time.time() - (_PING_KEEPALIVE_INTERVAL + 5)

    await sub._keepalive_tick(st)

    assert len(st.ws.sent) == 1
    assert '"type"' in st.ws.sent[0]
    assert st.ping_pending_since is not None
    assert st.last_ping_sent_at <= time.time()


async def test_keepalive_tick_skips_within_interval() -> None:
    sub = _make_subscriber()
    st = _make_state()
    st.last_ping_sent_at = time.time() - 5  # 间隔内

    await sub._keepalive_tick(st)

    assert st.ws.sent == []
    assert st.ping_pending_since is None


async def test_keepalive_tick_skips_when_pending() -> None:
    sub = _make_subscriber()
    st = _make_state()
    st.last_ping_sent_at = 0.0
    st.ping_pending_since = time.time() - 100  # 上一发 Ping 未获应答

    await sub._keepalive_tick(st)

    assert st.ws.sent == []


# ---- _handle_ping_frame ----


async def test_pong_clears_pending_without_echo() -> None:
    """我方 Ping 的 Pong：仅清 pending，不再回发 type=6（防互答风暴）."""
    sub = _make_subscriber()
    st = _make_state()
    st.ping_pending_since = time.time() - 5

    await sub._handle_ping_frame(st)

    assert st.ping_pending_since is None
    assert st.ws.sent == []  # 不回显 Pong


async def test_server_ping_gets_pong_reply() -> None:
    """无 pending 时收到 type=6 视为服务端主动 Ping，回复 Pong."""
    sub = _make_subscriber()
    st = _make_state()

    await sub._handle_ping_frame(st)

    assert len(st.ws.sent) == 1
    assert '"type"' in st.ws.sent[0]


async def test_process_shard_message_routes_ping_and_data() -> None:
    """片内消息入口：type=6 走心跳处理，真正缓存了值才推进片级接收点."""
    sub = _make_subscriber()
    st = _make_state()
    st.ping_pending_since = time.time() - 3

    # Pong → 清 pending，不进共享处理器
    await sub._process_shard_message(st, {"type": 6})
    assert st.ping_pending_since is None

    # 空推送（无数据项）不推进片级接收点
    await sub._process_shard_message(
        st,
        {"type": 1, "target": "updateRealValues", "arguments": [[]]},
    )
    assert st.last_data_at is None

    # 带数据项的推送 → _cache_value 接纳（返回 True）→ 片级接收点推进
    async def fake_cache(item):
        sub._last_data_at = time.time()
        return True  # R09：片级接收点由 _handle_signalr_message 的接纳计数推进

    with patch.object(sub, "_cache_value", new=AsyncMock(side_effect=fake_cache)):
        await sub._process_shard_message(
            st,
            {
                "type": 1,
                "target": "updateRealValues",
                "arguments": [[{"tagCode": "TAG_A_PV", "value": "1"}]],
            },
        )
    assert st.last_data_at is not None


# ---- _is_ping_dead ----


async def test_ping_dead_no_pending() -> None:
    sub = _make_subscriber()
    st = _make_state()
    assert sub._is_ping_dead(st) is False


async def test_ping_dead_pending_within_timeout() -> None:
    sub = _make_subscriber()
    st = _make_state()
    st.ping_pending_since = time.time() - 30  # < 判死阈值
    assert sub._is_ping_dead(st) is False


async def test_ping_dead_timeout_without_data() -> None:
    sub = _make_subscriber()
    st = _make_state()
    st.ping_pending_since = time.time() - (_PING_DEATH_TIMEOUT + 1)
    st.last_data_at = None  # 从未收到数据
    assert sub._is_ping_dead(st) is True


async def test_ping_dead_timeout_with_stale_data_only() -> None:
    sub = _make_subscriber()
    pending_at = time.time() - (_PING_DEATH_TIMEOUT + 1)
    st = _make_state()
    st.ping_pending_since = pending_at
    st.last_data_at = pending_at - 100  # 数据早于 Ping，Ping 后无数据
    assert sub._is_ping_dead(st) is True


async def test_ping_alive_when_data_flows_after_ping() -> None:
    """Ping 后仍有数据到达 → 连接存活，即使服务端不回 Pong."""
    sub = _make_subscriber()
    pending_at = time.time() - (_PING_DEATH_TIMEOUT + 1)
    st = _make_state()
    st.ping_pending_since = pending_at
    st.last_data_at = pending_at + 10  # Ping 之后有数据
    assert sub._is_ping_dead(st) is False
