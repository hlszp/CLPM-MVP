"""WebSocket 实时推送认证测试（P2 WS 认证加固）.

Covers:
- 合法 access token 正常连接
- refresh token 连接被拒绝（type 校验）
- 已吊销（黑名单）access token 连接被拒绝
- 未携带 token 连接被拒绝

前端仅支持 query 传 token（浏览器原生 WebSocket 无自定义头），
服务端保留 query 方式，校验口径与 ``get_current_user`` 对齐。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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
