"""实时订阅器活性管理单元测试（2026-09-07 修正后语义）.

背景（2026-09-07 对照实测）：AAS 网关收到客户端 type=6 Ping 即优雅关闭
连接（close 1000 OK），而非返回 Pong——原"应用层 ping 作保活流量"的假设
被证伪（对照 signalrcore 官方客户端与独立 ping 探针，官方客户端不发此
ping 且同网关零断连）。修复：停发 type=6、判死恒 False，活性改由数据
停滞看门狗（SIGNALR_STALL_TIMEOUT_SECONDS）兜底。本文件覆盖：
- _split_shards：位号切分（保序、覆盖、末片不满）
- _keepalive_tick：**恒不发 type=6 Ping**（修复后语义）
- _handle_ping_frame：服务端主动 Ping 仍回 Pong（被动应答保留，防互答风暴）
- _is_ping_dead：恒 False（停用主动 ping 后不再误杀）
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from app.services.data_source.realtime_subscriber import (
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
    """构造仅用于分片/活性纯逻辑的订阅器实例（不启动后台任务）."""
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


# ---- _keepalive_tick：2026-09-07 停发 type=6 ----


async def test_keepalive_tick_never_sends_type6_ping() -> None:
    """修复后语义：即使到期也不发 type=6（AAS 收 ping 即关连接）."""
    sub = _make_subscriber()
    st = _make_state()
    st.last_ping_sent_at = time.time() - 3600  # 远超任何间隔

    await sub._keepalive_tick(st)

    assert st.ws.sent == []
    assert st.ping_pending_since is None


async def test_keepalive_tick_idempotent_under_pending() -> None:
    """即使存在历史 pending（旧状态残留）也保持不发、不误置新 pending."""
    sub = _make_subscriber()
    st = _make_state()
    st.last_ping_sent_at = 0.0
    st.ping_pending_since = time.time() - 100

    await sub._keepalive_tick(st)

    assert st.ws.sent == []


# ---- _handle_ping_frame：服务端主动 Ping 仍回 Pong ----


async def test_server_initiated_ping_gets_pong_reply() -> None:
    """服务端主动发 type=6 → 客户端回 Pong（被动应答保留）."""
    sub = _make_subscriber()
    st = _make_state()

    await sub._handle_ping_frame(st)

    assert len(st.ws.sent) == 1
    assert '"type"' in st.ws.sent[0]


async def test_process_shard_message_routes_ping_and_data() -> None:
    """片内消息入口：type=6 走被动应答；真正缓存了值才推进片级接收点."""
    sub = _make_subscriber()
    st = _make_state()

    # 服务端主动 Ping → 回 Pong（被动应答），不影响片级接收点
    await sub._process_shard_message(st, {"type": 6})
    assert len(st.ws.sent) == 1

    # 空推送（无数据项）不推进片级接收点
    await sub._process_shard_message(
        st,
        {"type": 1, "target": "updateRealValues", "arguments": [[]]},
    )
    assert st.last_data_at is None

    # 带数据项的推送 → _cache_value 接纳 → 片级接收点推进
    async def fake_cache(item):  # noqa: ARG001
        sub._last_data_at = time.time()
        return True

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


# ---- _is_ping_dead：停用主动 ping 后恒 False ----


async def test_ping_dead_always_false_no_pending() -> None:
    sub = _make_subscriber()
    st = _make_state()
    assert sub._is_ping_dead(st) is False


async def test_ping_dead_always_false_even_stale_pending() -> None:
    """即使有远超阈值的陈旧 pending 也不再判死（活性交停滞看门狗）."""
    sub = _make_subscriber()
    st = _make_state()
    st.ping_pending_since = time.time() - 3600
    st.last_data_at = None
    assert sub._is_ping_dead(st) is False
