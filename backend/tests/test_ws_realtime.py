"""WebSocket 实时推送测试（认证加固 + 数据链路整改 R15/R16）.

认证（P2 WS 认证加固）:
- 合法 access token 正常连接
- refresh token 连接被拒绝（type 校验）
- 已吊销（黑名单）access token 连接被拒绝
- 未携带 token 连接被拒绝

R15 共享 Pub/Sub + 慢消费者治理:
- N 客户端仅 1 条上游 Pub/Sub 订阅（计数 pubsub 实例）
- 正常客户端持续收、慢端（send 阻塞）被 1013 关闭且不影响他人
- 订阅过滤协议：合法 tag 生效、未知 tag 在 ack 中拒绝
- 未发 subscribe 的旧客户端保持全量转发（单对象帧格式不变）
- 高频积压合并为 batch 帧；容量满 FIFO 丢弃最旧 tag 并计数

R16 生命周期回收:
- 无推送时客户端断开 → 无残留任务、共享订阅随引用计数关闭
- 心跳发送失败 → 整体回收
- 退订抛错 → 不跳过最终 aclose
- subscribe 校验失败 → ack 列出全部拒绝且过滤生效
- API shutdown（reset 钩子）→ 共享任务与客户端注册清空
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.v1.endpoints import ws_realtime as ws_module
from tests.conftest import (
    TEST_PASSWORD,
    TEST_USERS,
    FakeRedis,
    make_db_execute_return,
)


class _FakePubSub:
    """立即结束的空 Pub/Sub 订阅（accept 后主循环即刻退出，不阻塞测试）."""

    async def subscribe(self, channel: str) -> None:
        pass

    async def unsubscribe(self, channel: str) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        for _ in ():
            yield _


def _mock_ws_redis() -> MagicMock:
    """替换 ws_realtime 模块内的 redis_client，提供空 Pub/Sub."""
    mock = MagicMock()
    mock.pubsub = MagicMock(return_value=_FakePubSub())
    return mock


def _login(client: TestClient, mock_db: AsyncMock, username: str = "admin") -> dict:
    """登录并返回 tokens 数据."""
    mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS[username]))
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, f"Login failed: {resp.json()}"
    return resp.json()["data"]


class TestWsRealtimeAuth:
    """WS /api/v1/ws/realtime 认证校验."""

    def test_valid_access_token_connects(
        self, client: TestClient, mock_db: AsyncMock, fake_redis: FakeRedis
    ) -> None:
        """合法 access token 通过认证，不被 4001 拒绝."""
        data = _login(client, mock_db)
        with (
            patch("app.api.v1.endpoints.ws_realtime.redis_client", _mock_ws_redis()),
            client.websocket_connect(f"/api/v1/ws/realtime?token={data['accessToken']}"),
        ):
            pass  # 成功进入上下文即说明服务端 accept 而非 4001 关闭

    def test_refresh_token_rejected(
        self, client: TestClient, mock_db: AsyncMock, fake_redis: FakeRedis
    ) -> None:
        """refresh token（type != access）不得用于 WS 连接."""
        data = _login(client, mock_db)
        with (
            patch("app.api.v1.endpoints.ws_realtime.redis_client", _mock_ws_redis()),
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(f"/api/v1/ws/realtime?token={data['refreshToken']}"),
        ):
            pass
        assert exc_info.value.code == 4001

    def test_blacklisted_token_rejected(
        self, client: TestClient, mock_db: AsyncMock, fake_redis: FakeRedis
    ) -> None:
        """已吊销（logout 黑名单）的 access token 不得用于 WS 连接."""
        data = _login(client, mock_db)
        # logout 将 access token 写入黑名单。
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {data['accessToken']}"},
        )
        assert resp.status_code == 200

        with (
            patch("app.api.v1.endpoints.ws_realtime.redis_client", _mock_ws_redis()),
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect(f"/api/v1/ws/realtime?token={data['accessToken']}"),
        ):
            pass
        assert exc_info.value.code == 4001

    def test_missing_token_rejected(self, client: TestClient) -> None:
        """未携带 token 直接拒绝."""
        with (
            patch("app.api.v1.endpoints.ws_realtime.redis_client", _mock_ws_redis()),
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/v1/ws/realtime"),
        ):
            pass
        assert exc_info.value.code == 4001

    def test_invalid_token_rejected(self, client: TestClient) -> None:
        """伪造 token 直接拒绝."""
        with (
            patch("app.api.v1.endpoints.ws_realtime.redis_client", _mock_ws_redis()),
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/v1/ws/realtime?token=not.a.token"),
        ):
            pass
        assert exc_info.value.code == 4001


# ---------------------------------------------------------------------------
# R15/R16 行为测试：直接调用端点协程 + 内存假件（可控收发时序）
# ---------------------------------------------------------------------------


class ScriptablePubSub:
    """可编程 Pub/Sub 假件：listen 挂起等待队列消息，支持注入退订异常."""

    instances: list[ScriptablePubSub] = []

    def __init__(self) -> None:
        ScriptablePubSub.instances.append(self)
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False
        self.unsubscribe_error: Exception | None = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        if self.unsubscribe_error:
            raise self.unsubscribe_error
        self.unsubscribed.append(channel)

    async def aclose(self) -> None:
        self.closed = True

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            msg = await self._queue.get()
            if msg is None:
                return  # 模拟上游订阅终止（如 Redis 断开）
            yield msg

    async def push(self, data: str) -> None:
        await self._queue.put({"type": "message", "data": data})

    async def push_close(self) -> None:
        await self._queue.put(None)


class FakeWebSocket:
    """可控 WS 假件：send 可编程阻塞/抛错，receive 由队列驱动."""

    def __init__(self, *, send_delay: float = 0.0) -> None:
        self.sent_frames: list[str] = []
        self.close_calls: list[tuple[int, str | None]] = []
        self.send_delay = send_delay
        self.send_error: Exception | None = None
        self._recv_queue: asyncio.Queue[Any] = asyncio.Queue()
        self.client = SimpleNamespace(host="127.0.0.1", port=50000)
        self.query_params: dict[str, str] = {"token": "fake"}

    async def accept(self) -> None:
        pass

    async def send_text(self, data: str) -> None:
        if self.send_delay > 0:
            await asyncio.sleep(self.send_delay)
        if self.send_error:
            raise self.send_error
        self.sent_frames.append(data)

    async def send_json(self, data: Any) -> None:
        await self.send_text(json.dumps(data, ensure_ascii=False))

    async def receive_text(self) -> str:
        item = await self._recv_queue.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_calls.append((code, reason))

    async def client_says(self, text: str) -> None:
        await self._recv_queue.put(text)

    async def client_disconnect(self) -> None:
        await self._recv_queue.put(WebSocketDisconnect())

    def parsed_frames(self) -> list[Any]:
        return [json.loads(f) for f in self.sent_frames]


def _make_tag_payload(tag: str, value: str = "1.0") -> str:
    return json.dumps(
        {
            "tagCode": tag,
            "value": value,
            "quality": 1,
            "collectTime": "2026-09-06T10:00:00Z",
        }
    )


@pytest.fixture()
def isolated_ws_state(monkeypatch: pytest.MonkeyPatch):
    """每测试隔离模块级共享状态，并注入可控 fake Redis.

    - 清空共享订阅/客户端注册/计数
    - redis_client → 每次调用新建 ScriptablePubSub 并计数
    - 发送期限收紧到 0.3s；心跳默认关闭（个别用例单独调小）
    """
    ScriptablePubSub.instances.clear()
    ws_module._shared = None  # noqa: SLF001
    ws_module._clients.clear()  # noqa: SLF001
    for key in ws_module.ws_metrics:
        ws_module.ws_metrics[key] = 0

    monkeypatch.setattr(ws_module.settings, "WS_SEND_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(ws_module.settings, "WS_CLIENT_QUEUE_MAX", 500)
    monkeypatch.setattr(ws_module, "_HEARTBEAT_INTERVAL_SECONDS", 3600.0)

    def _pubsub_factory() -> ScriptablePubSub:
        return ScriptablePubSub()

    fake_redis = SimpleNamespace(pubsub=_pubsub_factory)
    monkeypatch.setattr(ws_module, "redis_client", fake_redis)
    yield ws_module
    # 兜底清理（失败用例不向后续用例泄漏模块状态）
    ws_module._shared = None  # noqa: SLF001
    ws_module._clients.clear()  # noqa: SLF001


async def _start_endpoint(ws: FakeWebSocket, monkeypatch: pytest.MonkeyPatch) -> asyncio.Task:
    """以通过认证的假 token 直接运行端点协程，返回其任务.

    认证 patch 经 monkeypatch 持续到测试结束（create_task 不会立即执行
    协程体，瞬时 with patch 会在任务首步前被撤销）.
    """
    monkeypatch.setattr(ws_module, "_verify_token", AsyncMock(return_value=True))
    return asyncio.create_task(ws_module.realtime_websocket(ws))


async def _finish(task: asyncio.Task, timeout: float = 5.0) -> None:
    """等待端点任务结束（异常向上抛出，便于定位问题）."""
    await asyncio.wait_for(task, timeout=timeout)


async def _wait_until(predicate, timeout: float = 3.0) -> None:
    """轮询等待条件成立（默认 3s 超时，避免测试挂死）."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met in time")
        await asyncio.sleep(0.01)


class TestSharedPubSubFanout:
    """R15：共享订阅与扇出."""

    async def test_n_clients_single_upstream_subscription(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N=5 客户端仅建立 1 条上游 Pub/Sub 订阅，且全部收到消息."""
        clients = [FakeWebSocket() for _ in range(5)]
        tasks = [await _start_endpoint(ws, monkeypatch) for ws in clients]
        try:
            await _wait_until(lambda: len(ws_module._clients) == 5)
            assert len(ScriptablePubSub.instances) == 1
            pubsub = ScriptablePubSub.instances[0]
            await pubsub.push(_make_tag_payload("TAG_A"))
            for ws in clients:
                await _wait_until(lambda ws=ws: len(ws.sent_frames) >= 1)
                frame = ws.parsed_frames()[0]
                assert frame["tagCode"] == "TAG_A"
        finally:
            for ws in clients:
                await ws.client_disconnect()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5.0)
        # 全部断开后引用计数归零，共享订阅关闭
        assert ws_module._shared is None
        pubsub = ScriptablePubSub.instances[0]
        assert pubsub.closed is True
        assert ws_module.ws_metrics["ws_clients"] == 0

    async def test_shared_subscription_survives_partial_disconnect(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """某客户端断开不关闭其他使用者共享的订阅（引用计数）."""
        ws1, ws2 = FakeWebSocket(), FakeWebSocket()
        t1 = await _start_endpoint(ws1, monkeypatch)
        t2 = await _start_endpoint(ws2, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 2)
            pubsub = ScriptablePubSub.instances[0]
            await ws1.client_disconnect()
            await _finish(t1)
            await _wait_until(lambda: len(ws_module._clients) == 1)
            assert ws_module._shared is not None
            assert pubsub.closed is False
            await pubsub.push(_make_tag_payload("TAG_B"))
            await _wait_until(lambda: len(ws2.sent_frames) >= 1)
            assert ws2.parsed_frames()[0]["tagCode"] == "TAG_B"
        finally:
            await ws2.client_disconnect()
            await asyncio.wait_for(asyncio.gather(t2, return_exceptions=True), timeout=5.0)

    async def test_slow_consumer_closed_1013_others_unaffected(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """50 正常 + 1 慢端：正常端持续收，慢端被 1013 关闭."""
        normal = [FakeWebSocket() for _ in range(50)]
        slow = FakeWebSocket(send_delay=10.0)  # 每次发送阻塞 10s（远超 0.3s 期限）
        tasks = [await _start_endpoint(ws, monkeypatch) for ws in normal]
        slow_task = await _start_endpoint(slow, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 51)
            pubsub = ScriptablePubSub.instances[0]
            # 持续推送：正常端应不断收到，慢端阻塞在首帧
            for i in range(4):
                await pubsub.push(_make_tag_payload(f"TAG_{i}"))
            await _wait_until(lambda: all(len(ws.sent_frames) >= 1 for ws in normal))
            # 慢端发送期限（0.3s）到 → 1013 关闭
            await _finish(slow_task)
            assert slow.close_calls[0][0] == 1013
            assert ws_module.ws_metrics["ws_slow_closed"] == 1
            # 慢端关闭后正常端继续收到后续推送
            await pubsub.push(_make_tag_payload("TAG_AFTER"))
            await _wait_until(
                lambda: all(
                    any(
                        isinstance(f, dict) and f.get("tagCode") == "TAG_AFTER"
                        for f in ws.parsed_frames()
                    )
                    for ws in normal
                )
            )
        finally:
            for ws in normal:
                await ws.client_disconnect()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5.0)

    async def test_legacy_client_full_forward_single_object_frame(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未发 subscribe 的旧客户端保持全量转发，单条消息为原样单对象帧."""
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 1)
            pubsub = ScriptablePubSub.instances[0]
            await pubsub.push(_make_tag_payload("ANY_TAG", "42.5"))
            await _wait_until(lambda: len(ws.sent_frames) >= 1)
            frame = ws.parsed_frames()[0]
            assert frame == {
                "tagCode": "ANY_TAG",
                "value": "42.5",
                "quality": 1,
                "collectTime": "2026-09-06T10:00:00Z",
            }
            # 不含 type 字段（区别于 batch/ping 控制帧）
            assert "type" not in frame
        finally:
            await ws.client_disconnect()
            await _finish(task)

    async def test_burst_merged_into_batch_frame(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """高频积压合并为单一 batch 帧（发送被门控期间积压 3 个 tag）."""
        ws = FakeWebSocket()
        gate = asyncio.Event()
        original_send = ws.send_text

        async def _gated_send(data: str) -> None:
            await gate.wait()
            await original_send(data)

        ws.send_text = _gated_send  # type: ignore[method-assign]
        task = await _start_endpoint(ws, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 1)
            pubsub = ScriptablePubSub.instances[0]
            for tag in ("T1", "T2", "T3"):
                await pubsub.push(_make_tag_payload(tag))
            await asyncio.sleep(0.1)  # 让共享任务完成入队（发送仍被门控）
            gate.set()
            await _wait_until(lambda: len(ws.sent_frames) >= 1)
            frames = ws.parsed_frames()
            assert len(frames) == 1
            batch = frames[0]
            assert batch["type"] == "batch"
            assert [item["tagCode"] for item in batch["items"]] == ["T1", "T2", "T3"]
        finally:
            await ws.client_disconnect()
            await _finish(task)

    async def test_same_tag_merged_latest_wins(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同 tag 连续更新仅保留最新值（merged_count 计数）."""
        ws = FakeWebSocket()
        gate = asyncio.Event()
        original_send = ws.send_text

        async def _gated_send(data: str) -> None:
            await gate.wait()
            await original_send(data)

        ws.send_text = _gated_send  # type: ignore[method-assign]
        task = await _start_endpoint(ws, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 1)
            pubsub = ScriptablePubSub.instances[0]
            await pubsub.push(_make_tag_payload("T1", "1"))
            await pubsub.push(_make_tag_payload("T1", "2"))
            await pubsub.push(_make_tag_payload("T1", "3"))
            await asyncio.sleep(0.1)
            gate.set()
            await _wait_until(lambda: len(ws.sent_frames) >= 1)
            frames = ws.parsed_frames()
            assert len(frames) == 1
            frame = frames[0]
            assert frame["tagCode"] == "T1"
            assert frame["value"] == "3"
        finally:
            await ws.client_disconnect()
            await _finish(task)
            assert ws_module.ws_metrics["ws_merged_count"] >= 2

    async def test_queue_overflow_fifo_drop_oldest(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """容量满时 FIFO 丢弃最旧 tag 并计入 merged_dropped."""
        monkeypatch.setattr(ws_module.settings, "WS_CLIENT_QUEUE_MAX", 2)
        ws = FakeWebSocket()
        gate = asyncio.Event()
        original_send = ws.send_text

        async def _gated_send(data: str) -> None:
            await gate.wait()
            await original_send(data)

        ws.send_text = _gated_send  # type: ignore[method-assign]
        task = await _start_endpoint(ws, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 1)
            pubsub = ScriptablePubSub.instances[0]
            for tag in ("T1", "T2", "T3"):
                await pubsub.push(_make_tag_payload(tag))
            await asyncio.sleep(0.1)
            gate.set()
            await _wait_until(lambda: len(ws.sent_frames) >= 1)
            batch = ws.parsed_frames()[0]
            assert batch["type"] == "batch"
            assert [item["tagCode"] for item in batch["items"]] == ["T2", "T3"]
            assert ws_module.ws_metrics["ws_merged_dropped"] == 1
        finally:
            await ws.client_disconnect()
            await _finish(task)


class TestSubscribeProtocol:
    """R15：订阅过滤协议（版本化兼容）."""

    async def test_subscribe_filters_and_rejects_unknown_tags(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """合法 tag 生效、未知 tag 在 ack 中拒绝且不转发."""
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 1)

            async def _fake_validate(tags: list[str]) -> tuple[list[str], list[str]]:
                valid = [t for t in tags if t != "UNKNOWN_TAG"]
                rejected = [t for t in tags if t == "UNKNOWN_TAG"]
                return valid, rejected

            with patch.object(ws_module, "_validate_subscribe_tags", _fake_validate):
                await ws.client_says(
                    json.dumps({"type": "subscribe", "tags": ["TAG_A", "UNKNOWN_TAG"]})
                )
                await _wait_until(
                    lambda: any(
                        isinstance(f, dict) and f.get("type") == "subscribed"
                        for f in ws.parsed_frames()
                    )
                )
            ack = next(f for f in ws.parsed_frames() if f.get("type") == "subscribed")
            assert ack["tags"] == ["TAG_A"]
            assert ack["rejected"] == ["UNKNOWN_TAG"]

            pubsub = ScriptablePubSub.instances[0]
            # 订阅内的 tag 转发
            await pubsub.push(_make_tag_payload("TAG_A"))
            await _wait_until(
                lambda: len([f for f in ws.parsed_frames() if f.get("type") != "subscribed"]) >= 1
            )
            data_frames = [f for f in ws.parsed_frames() if f.get("type") != "subscribed"]
            assert data_frames[0]["tagCode"] == "TAG_A"
            # 订阅外/未知 tag 不转发
            await pubsub.push(_make_tag_payload("TAG_OTHER"))
            await asyncio.sleep(0.15)
            data_frames = [f for f in ws.parsed_frames() if f.get("type") != "subscribed"]
            assert len(data_frames) == 1
        finally:
            await ws.client_disconnect()
            await _finish(task)

    async def test_subscribe_validation_failure_all_rejected(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """校验异常（如 DB 不可用）→ ack 全部拒绝，连接保持不崩."""
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        try:
            await _wait_until(lambda: len(ws_module._clients) == 1)

            async def _boom(tags: list[str]) -> tuple[list[str], list[str]]:
                raise RuntimeError("db down")

            with patch.object(ws_module, "_validate_subscribe_tags", _boom):
                await ws.client_says(json.dumps({"type": "subscribe", "tags": ["TAG_A"]}))
                await _wait_until(
                    lambda: any(
                        isinstance(f, dict) and f.get("type") == "subscribed"
                        for f in ws.parsed_frames()
                    )
                )
            ack = next(f for f in ws.parsed_frames() if f.get("type") == "subscribed")
            assert ack["tags"] == []
            assert ack["rejected"] == ["TAG_A"]
            # 连接仍存活（任务未退出）
            assert not task.done()
        finally:
            await ws.client_disconnect()
            await _finish(task)


class TestLifecycleRecovery:
    """R16：生命周期回收."""

    async def test_disconnect_without_push_releases_everything(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无任何 Pub/Sub 消息时客户端断开 → 5s 内无残留、订阅关闭."""
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        await _wait_until(lambda: len(ws_module._clients) == 1)
        pubsub = ScriptablePubSub.instances[0]
        await ws.client_disconnect()
        await _finish(task)
        assert task.done()
        assert ws_module._shared is None
        assert pubsub.closed is True
        assert pubsub.unsubscribed == [ws_module._PUBSUB_CHANNEL]
        assert len(ws_module._clients) == 0

    async def test_heartbeat_failure_triggers_full_cleanup(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """心跳发送失败（客户端失联）→ 三任务整体回收、订阅关闭."""
        monkeypatch.setattr(ws_module, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        await _wait_until(lambda: len(ws_module._clients) == 1)
        pubsub = ScriptablePubSub.instances[0]
        # 心跳间隔 0.05s，此后所有 send 抛错
        ws.send_error = RuntimeError("client gone")
        await _finish(task)
        assert task.done()
        assert ws_module._shared is None
        assert pubsub.closed is True
        assert len(ws_module._clients) == 0

    async def test_unsubscribe_error_does_not_skip_aclose(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """退订抛错不跳过最终 aclose（引用计数归零路径）."""
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        await _wait_until(lambda: len(ws_module._clients) == 1)
        pubsub = ScriptablePubSub.instances[0]
        pubsub.unsubscribe_error = RuntimeError("redis down")
        await ws.client_disconnect()
        await _finish(task)
        assert pubsub.closed is True

    async def test_api_shutdown_reset_hook(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """API shutdown（reset 钩子）：共享任务与客户端注册清空."""
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        await _wait_until(lambda: len(ws_module._clients) == 1)
        pubsub = ScriptablePubSub.instances[0]
        await ws_module._reset_shared_pubsub()
        assert ws_module._shared is None
        assert pubsub.closed is True
        assert len(ws_module._clients) == 0
        # 客户端后续断开也能正常退出（幂等清理）
        await ws.client_disconnect()
        await _finish(task)

    async def test_shared_consumer_death_closes_client_with_1011(
        self, isolated_ws_state, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """共享消费任务终止（如 Redis 断开）→ 客户端被 1011 关闭并可重连重建."""
        ws = FakeWebSocket()
        task = await _start_endpoint(ws, monkeypatch)
        await _wait_until(lambda: len(ws_module._clients) == 1)
        pubsub = ScriptablePubSub.instances[0]
        await pubsub.push_close()
        await _finish(task)
        assert ws.close_calls[0][0] == 1011
        assert pubsub.closed is True
        assert ws_module._shared is None
