"""Rate limiting middleware tests（Phase 3 健壮性整改）.

覆盖：
- PUT /api/v1/auth/password 限流生效（修复方法匹配：原为仅 POST 检查导致永不生效）
- X-Forwarded-For 下不同来源 IP 独立计数（限流 key 与 security.get_client_ip 口径统一）
- 登录接口 IP + 用户名双维度限流（与 5 次失败锁定 15 分钟互补）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.middleware import rate_limit as rl
from tests.conftest import TEST_USERS, make_db_execute_return, mock_current_user

LOGIN_URL = "/api/v1/auth/login"
PASSWORD_URL = "/api/v1/auth/password"
REFRESH_URL = "/api/v1/auth/refresh"

_CHANGE_PASSWORD_BODY = {"oldPassword": "Admin@123", "newPassword": "NewPass@123"}


class TestPasswordRateLimit:
    """PUT /api/v1/auth/password 限流（方法匹配修复）."""

    def test_put_password_rate_limited(self, client, mock_db) -> None:
        """PUT 改密 5 次/分钟，第 6 次返回 429 ERR_RATE_LIMITED."""
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.api.v1.endpoints.auth.change_password", new=AsyncMock()),
        ):
            for _ in range(5):
                resp = client.put(PASSWORD_URL, json=_CHANGE_PASSWORD_BODY)
                assert resp.status_code == 200

            resp = client.put(PASSWORD_URL, json=_CHANGE_PASSWORD_BODY)
            assert resp.status_code == 429
            assert resp.json()["code"] == "ERR_RATE_LIMITED"
            assert resp.headers["Retry-After"] == "60"


class TestClientIpConsistency:
    """限流 key 与 get_client_ip 口径统一（X-Forwarded-For 优先）."""

    def test_xff_independent_ip_counting(self, client, monkeypatch) -> None:
        """不同来源 IP 独立计数，互不影响."""
        monkeypatch.setitem(rl.RATE_LIMITS, REFRESH_URL, ("POST", 2, 60))
        body = {"refreshToken": "invalid-token"}

        # IP 1.1.1.1：前 2 次放行，第 3 次限流
        for _ in range(2):
            resp = client.post(REFRESH_URL, json=body, headers={"X-Forwarded-For": "1.1.1.1"})
            assert resp.status_code != 429
        resp = client.post(REFRESH_URL, json=body, headers={"X-Forwarded-For": "1.1.1.1"})
        assert resp.status_code == 429
        assert resp.json()["code"] == "ERR_RATE_LIMITED"

        # IP 2.2.2.2：独立计数，不受 1.1.1.1 超限影响
        resp = client.post(REFRESH_URL, json=body, headers={"X-Forwarded-For": "2.2.2.2"})
        assert resp.status_code != 429

    def test_xff_multi_hop_takes_first_ip(self, client, monkeypatch) -> None:
        """X-Forwarded-For 多跳时取第一个 IP（与 get_client_ip 一致）."""
        monkeypatch.setitem(rl.RATE_LIMITS, REFRESH_URL, ("POST", 1, 60))
        body = {"refreshToken": "invalid-token"}

        resp = client.post(REFRESH_URL, json=body, headers={"X-Forwarded-For": "1.1.1.1"})
        assert resp.status_code != 429
        # 同一客户端经不同代理链路（多跳 XFF），仍按第一个 IP 计入同一计数器
        resp = client.post(REFRESH_URL, json=body, headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.1"})
        assert resp.status_code == 429


class TestLoginDualDimension:
    """登录接口 IP + 用户名双维度限流."""

    def test_login_sixth_request_per_minute_429(self, client, mock_db, monkeypatch) -> None:
        """IP 维度 5 次/分钟时，第 6 次登录返回 429."""
        monkeypatch.setitem(rl.RATE_LIMITS, rl.LOGIN_PATH, ("POST", 5, 60))
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))

        for _ in range(5):
            resp = client.post(LOGIN_URL, json={"username": "admin", "password": "wrong"})
            assert resp.status_code == 400

        resp = client.post(LOGIN_URL, json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 429
        assert resp.json()["code"] == "ERR_RATE_LIMITED"

    def test_login_username_dimension_across_ips(self, client, mock_db, monkeypatch) -> None:
        """用户名维度限流：同一账号换 IP 仍被限，其他账号不受影响."""
        monkeypatch.setattr(rl, "LOGIN_USER_LIMIT", (2, 60))
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))

        # 同一账号从不同 IP 登录 2 次（均放行，由端点返回 400）
        for i in range(2):
            resp = client.post(
                LOGIN_URL,
                json={"username": "admin", "password": "wrong"},
                headers={"X-Forwarded-For": f"10.0.0.{i + 1}"},
            )
            assert resp.status_code == 400

        # 第 3 次换 IP 仍触发用户名维度限流（防分布式撞库）
        resp = client.post(
            LOGIN_URL,
            json={"username": "admin", "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.3"},
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "ERR_RATE_LIMITED"

        # 其他账号不受 admin 超限影响
        resp = client.post(
            LOGIN_URL,
            json={"username": "expert", "password": "wrong"},
            headers={"X-Forwarded-For": "10.0.0.4"},
        )
        assert resp.status_code != 429

    def test_login_username_dimension_shared_ip(self, client, mock_db, monkeypatch) -> None:
        """同一 IP 下不同账号各自独立计数（IP 维度之外互不影响）."""
        monkeypatch.setattr(rl, "LOGIN_USER_LIMIT", (1, 60))
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))

        resp = client.post(LOGIN_URL, json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 400
        # admin 账号维度超限
        resp = client.post(LOGIN_URL, json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 429
        # 同 IP 换账号仍可到达端点
        resp = client.post(LOGIN_URL, json={"username": "expert", "password": "wrong"})
        assert resp.status_code == 400
