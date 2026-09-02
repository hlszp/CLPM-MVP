"""POST /api/v1/datasource/refresh-subscription 端点测试.

覆盖：
- ADMIN 触发刷新：publish 刷新指令（mock redis publish 模拟 Leader 回写结果 key）
  → 轮询到结果 → 返回统一包装的结果 JSON
- Leader 侧执行失败（error 字段非空）→ 200 + data.error 透传
- SIGNALR_ENABLED=False → 400 ERR_SIGNALR_DISABLED（不发布指令）
- 非 ADMIN 角色 → 403；未认证 → 401
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app.services.data_source.realtime_subscriber import _REFRESH_RESULT_KEY
from tests.conftest import TEST_USERS, mock_current_user


def _leader_result(request_id: str | None, *, error: str | None = None) -> dict:
    return {
        "requestId": request_id,
        "requestedAt": "2026-09-02T00:00:00+00:00",
        "finishedAt": "2026-09-02T00:00:01+00:00",
        "source": "manual-api",
        "total": 3,
        "added": ["LIC-103.PV"],
        "removed": ["LIC-102.PV"],
        "invocationId": "manual_refresh_7",
        "leaderPid": 12345,
        "error": error,
    }


def _make_leader_answer(fake_redis, *, error: str | None = None):
    """构造 publish 替身：按指令 requestId 预置结果 key（模拟 Leader 回写）."""

    async def _impl(channel: str, message: str) -> int:
        payload = json.loads(message)
        result = _leader_result(payload["requestId"], error=error)
        await fake_redis.set(_REFRESH_RESULT_KEY, json.dumps(result), ex=60)
        return 1

    return _impl


class TestRefreshSubscriptionEndpoint:
    """POST /api/v1/datasource/refresh-subscription."""

    def test_success(self, client, fake_redis) -> None:
        """ADMIN 触发刷新：返回 Leader 执行结果（total/added/removed/invocationId/leaderPid）."""
        fake_redis.publish = _make_leader_answer(fake_redis)
        with (
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=SimpleNamespace(_running=True),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            mock_s.SIGNALR_ENABLED = True
            resp = client.post(
                "/api/v1/datasource/refresh-subscription",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert "订阅刷新成功" in body["message"]
        data = body["data"]
        assert data["total"] == 3
        assert data["added"] == ["LIC-103.PV"]
        assert data["removed"] == ["LIC-102.PV"]
        assert data["invocationId"] == "manual_refresh_7"
        assert data["leaderPid"] == 12345
        assert data["error"] is None
        assert data["requestId"]  # requestId 透传匹配

    def test_leader_error_passthrough(self, client, fake_redis) -> None:
        """Leader 侧执行失败（WS 未连接等）：200 + data.error 携带原因."""
        fake_redis.publish = _make_leader_answer(fake_redis, error="WebSocket 未连接")
        with (
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=SimpleNamespace(_running=True),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            mock_s.SIGNALR_ENABLED = True
            resp = client.post(
                "/api/v1/datasource/refresh-subscription",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["error"] == "WebSocket 未连接"
        assert "未完成" in body["message"]

    def test_signalr_disabled_returns_400(self, client, fake_redis) -> None:
        """SIGNALR_ENABLED=False：400 + 明确错误码，不发布指令."""
        with (
            patch("app.services.data_source.realtime_subscriber.settings") as mock_s,
            mock_current_user(TEST_USERS["admin"]),
        ):
            mock_s.SIGNALR_ENABLED = False
            resp = client.post(
                "/api/v1/datasource/refresh-subscription",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "ERR_SIGNALR_DISABLED"
        assert _REFRESH_RESULT_KEY not in fake_redis._strings

    def test_forbidden_for_non_admin(self, client, fake_redis) -> None:
        """非 ADMIN 角色无权刷新（与 datasource 其他写端点一致）."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/datasource/refresh-subscription",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_unauthenticated_returns_401(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.post("/api/v1/datasource/refresh-subscription")
        assert resp.status_code == 401
