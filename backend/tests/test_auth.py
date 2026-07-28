"""Authentication API tests (S1-AUTH-001~004).

Covers:
- Login success / failure (user not found / wrong password / disabled / locked)
- Token refresh success / failure
- Logout + blacklist verification
- GET /auth/me
- Change password success / failure (wrong old / same new)
- RBAC permission checks
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (
    TEST_PASSWORD,
    TEST_USERS,
    make_db_execute_return,
)

# ===========================================================================
# S1-AUTH-001: Login
# ===========================================================================


class TestLogin:
    """POST /api/v1/auth/login tests."""

    def test_login_success_admin(self, client, mock_db, fake_redis) -> None:
        """Successful login returns tokens and user info."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["message"] == "success"
        data = body["data"]
        assert "accessToken" in data
        assert "refreshToken" in data
        assert data["tokenType"] == "Bearer"
        assert data["expiresIn"] == 1800
        user = data["user"]
        assert user["username"] == "admin"
        assert user["role"] == "ADMIN"
        assert user["permissions"] == ["*"]
        assert user["defaultHome"] == "/dashboard"

    def test_login_success_all_roles(self, client, mock_db, fake_redis) -> None:
        """Each role returns the correct permission list."""
        for username, user in TEST_USERS.items():
            mock_db.execute = AsyncMock(return_value=make_db_execute_return(user))
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": TEST_PASSWORD},
            )
            assert resp.status_code == 200, f"{username} login failed: {resp.json()}"
            perms = resp.json()["data"]["user"]["permissions"]
            if username == "admin":
                assert perms == ["*"]
            elif username == "ic_engineer":
                assert "loop:*" in perms
                assert "portal:view" in perms
            elif username == "pe_engineer":
                assert "tracker:*" in perms
                assert "loop:view" in perms
            elif username == "sponsor":
                assert perms == ["portal:view", "metric:view", "diagnosis:view"]
            elif username == "expert":
                assert "tracker:review" in perms

    def test_login_user_not_found(self, client, mock_db, fake_redis) -> None:
        """Non-existent user returns ERR_INVALID_CREDENTIALS (400).

        Unified error to prevent username enumeration.
        """
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(None))
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "ghost", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "ERR_INVALID_CREDENTIALS"

    def test_login_wrong_password(self, client, mock_db, fake_redis) -> None:
        """Wrong password returns ERR_INVALID_CREDENTIALS (400)."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrongpass"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_CREDENTIALS"

    def test_login_account_disabled(self, client, mock_db, fake_redis) -> None:
        """Disabled account returns ERR_ACCOUNT_DISABLED (403)."""
        disabled_user = TEST_USERS["admin"]
        disabled_user.is_active = False
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(disabled_user))
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_ACCOUNT_DISABLED"
        # Restore
        disabled_user.is_active = True

    def test_login_locked_after_5_failures(self, client, mock_db, fake_redis) -> None:
        """5 consecutive failures lock the account (ERR_TOO_MANY_ATTEMPTS)."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        # Fail 5 times with wrong password.
        for _ in range(5):
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrongpass"},
            )
            assert resp.status_code == 400
        # 6th attempt — even with correct password — should be locked.
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 429
        assert resp.json()["code"] == "ERR_TOO_MANY_ATTEMPTS"

    def test_login_success_clears_fail_count(self, client, mock_db, fake_redis) -> None:
        """A successful login clears the failure counter."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        # Fail twice.
        for _ in range(2):
            client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
        # Succeed.
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        # Fail counter should be cleared — 4 more failures should NOT lock.
        for _ in range(4):
            resp = client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "wrong"},
            )
            assert resp.status_code == 400

    def test_login_remember_me(self, client, mock_db, fake_redis) -> None:
        """rememberMe=true still returns valid tokens."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD, "rememberMe": True},
        )
        assert resp.status_code == 200
        assert "refreshToken" in resp.json()["data"]

    def test_login_validation_empty_username(self, client) -> None:
        """Empty username triggers validation error (422)."""
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 422


# ===========================================================================
# S1-AUTH-002: Refresh + Logout
# ===========================================================================


class TestRefresh:
    """POST /api/v1/auth/refresh tests."""

    def test_refresh_success(self, client, mock_db, fake_redis) -> None:
        """Valid refresh token returns a new token pair."""
        # Login first.
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        refresh_token = login_resp.json()["data"]["refreshToken"]

        # For refresh, the service opens its own DB session — patch it.
        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=make_db_execute_return(TEST_USERS["admin"])
            )
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.post(
                "/api/v1/auth/refresh",
                json={"refreshToken": refresh_token},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "accessToken" in data
        assert "refreshToken" in data
        assert data["tokenType"] == "Bearer"

    def test_refresh_invalid_token(self, client) -> None:
        """Invalid refresh token returns ERR_TOKEN_INVALID (401)."""
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refreshToken": "not.a.valid.token"},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] in ("ERR_TOKEN_INVALID", "ERR_TOKEN_EXPIRED")

    def test_refresh_access_token_rejected(self, client, mock_db, fake_redis) -> None:
        """Using an access token as refresh token fails."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refreshToken": access_token},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_INVALID"


class TestLogout:
    """POST /api/v1/auth/logout tests."""

    def test_logout_blacklists_token(self, client, mock_db, fake_redis) -> None:
        """After logout, the access token is blacklisted and can't be used."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        # /auth/me works before logout.
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 200

        # Logout.
        resp = client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == "0"

        # /auth/me fails after logout (token blacklisted).
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_INVALID"

    def test_logout_no_token(self, client) -> None:
        """Logout without a token returns 401 (authentication required)."""
        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 401

    def test_logout_revokes_paired_refresh_token(self, client, mock_db, fake_redis) -> None:
        """After logout, the paired refresh token is also revoked (P1 token lifecycle).

        Previously only the 30-min access token was blacklisted — the 7/30-day
        refresh token could keep minting new access tokens after "logout".
        """
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        data = login_resp.json()["data"]
        access_token = data["accessToken"]
        refresh_token = data["refreshToken"]

        resp = client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert resp.status_code == 200

        # 配套 refresh token 已吊销，无法再换新 access token。
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refreshToken": refresh_token},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_INVALID"

    def test_logout_does_not_revoke_other_sessions(self, client, mock_db, fake_redis) -> None:
        """Logout revokes only the current session's tokens, not other devices'."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        # Two logins = two independent sessions (e.g. PC + mobile).
        session_a = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        ).json()["data"]
        session_b = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        ).json()["data"]

        # Logout session A.
        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {session_a['accessToken']}"},
        )
        assert resp.status_code == 200

        # Session B's refresh token must still work.
        with patch("app.core.db.AsyncSessionLocal") as mock_session_local:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(
                return_value=make_db_execute_return(TEST_USERS["admin"])
            )
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

            resp = client.post(
                "/api/v1/auth/refresh",
                json={"refreshToken": session_b["refreshToken"]},
            )
        assert resp.status_code == 200
        assert "accessToken" in resp.json()["data"]


# ===========================================================================
# S1-AUTH-003: /auth/me + Change password
# ===========================================================================


class TestMe:
    """GET /api/v1/auth/me tests."""

    def test_me_success(self, client, mock_db, fake_redis) -> None:
        """Authenticated request returns current user info."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        # For /me, the DB is queried again via get_current_user.
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["username"] == "admin"
        assert data["role"] == "ADMIN"
        assert data["permissions"] == ["*"]
        assert data["defaultHome"] == "/dashboard"

    def test_me_no_token(self, client) -> None:
        """Request without token returns 401."""
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401
        assert resp.json()["code"] == "ERR_TOKEN_INVALID"

    def test_me_invalid_token(self, client) -> None:
        """Invalid token returns 401."""
        resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalidtoken"})
        assert resp.status_code == 401


class TestChangePassword:
    """PUT /api/v1/auth/password tests."""

    def test_change_password_success(self, client, mock_db, fake_redis) -> None:
        """Successful password change revokes all tokens."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.put(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"oldPassword": TEST_PASSWORD, "newPassword": "NewPass@2026"},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "密码修改成功，请重新登录"

        # Old token should be revoked.
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 401

    def test_change_password_blacklist_ttl_covers_remember_me(
        self, client, mock_db, fake_redis
    ) -> None:
        """Blacklist TTL must cover a remember-me refresh token's real lifetime (P2).

        remember-me refresh tokens live 30 days; a fixed 7-day blacklist TTL
        would let the revoked token resurrect on day 8.
        """
        from app.core.security import decode_token

        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD, "rememberMe": True},
        )
        data = login_resp.json()["data"]
        access_token = data["accessToken"]
        refresh_jti = decode_token(data["refreshToken"])["jti"]

        resp = client.put(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"oldPassword": TEST_PASSWORD, "newPassword": "NewPass@2026"},
        )
        assert resp.status_code == 200

        # 黑名单 TTL 必须覆盖 30 天剩余有效期（远超旧的固定 7 天）。
        ttl = fake_redis._ttls[f"token_blacklist:{refresh_jti}"]
        assert 7 * 24 * 3600 < ttl <= 30 * 24 * 3600

    def test_change_password_wrong_old(self, client, mock_db, fake_redis) -> None:
        """Wrong old password returns ERR_INVALID_CREDENTIALS."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.put(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"oldPassword": "wrongold", "newPassword": "NewPass@2026"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_CREDENTIALS"

    def test_change_password_same(self, client, mock_db, fake_redis) -> None:
        """Same new+old returns ERR_PASSWORD_SAME."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.put(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"oldPassword": TEST_PASSWORD, "newPassword": TEST_PASSWORD},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PASSWORD_SAME"

    def test_change_password_weak_new(self, client, mock_db, fake_redis) -> None:
        """New password without required complexity is rejected (422)."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        # 模拟生产环境（DEBUG=False）以测试完整密码策略
        with patch("app.core.config.settings.DEBUG", False):
            resp = client.put(
                "/api/v1/auth/password",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"oldPassword": TEST_PASSWORD, "newPassword": "12345678"},
            )
        assert resp.status_code == 422

    def test_change_password_too_short(self, client, mock_db, fake_redis) -> None:
        """New password < 8 chars is rejected (422)."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.put(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"oldPassword": TEST_PASSWORD, "newPassword": "Ab1"},
        )
        assert resp.status_code == 422


# ===========================================================================
# Token lifecycle unit tests (P1+P2: logout pairing / blacklist TTL)
# ===========================================================================


class TestRevokeAllUserTokens:
    """Unit tests for blacklist TTL semantics on batch revocation."""

    @pytest.mark.asyncio
    async def test_ttl_matches_each_tokens_remaining_lifetime(self, fake_redis) -> None:
        """Each token is blacklisted for its own remaining lifetime, not a fixed 7d."""
        from app.services import auth as auth_service

        with patch.object(auth_service, "redis_client", fake_redis):
            await auth_service._track_user_token("u-1", "jti-30d", 30 * 24 * 3600)
            await auth_service._track_user_token("u-1", "jti-30m", 30 * 60)
            await auth_service._revoke_all_user_tokens("u-1")

        # 30 天 remember-me refresh：TTL 远超旧的固定 7 天。
        assert fake_redis._ttls["token_blacklist:jti-30d"] > 7 * 24 * 3600
        # 30 分钟 access：TTL 按其自身剩余有效期，而非 7 天。
        assert fake_redis._ttls["token_blacklist:jti-30m"] <= 30 * 60
        # 跟踪集合已清空。
        assert await fake_redis.smembers("user_tokens:u-1") == set()

    @pytest.mark.asyncio
    async def test_legacy_member_without_exp_uses_max_ttl(self, fake_redis) -> None:
        """Legacy set members (plain jti, no exp suffix) fall back to the 30-day cap."""
        from app.services import auth as auth_service

        await fake_redis.sadd("user_tokens:u-2", "legacy-jti")
        with patch.object(auth_service, "redis_client", fake_redis):
            await auth_service._revoke_all_user_tokens("u-2")

        assert fake_redis._ttls["token_blacklist:legacy-jti"] == 30 * 24 * 3600


# ===========================================================================
# S1-AUTH-004: RBAC
# ===========================================================================


class TestRBAC:
    """Role-based access control tests."""

    def test_admin_can_access_rbac_test(self, client, mock_db, fake_redis) -> None:
        """ADMIN role can access the restricted endpoint."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.get(
            "/api/v1/auth/rbac-test", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["role"] == "ADMIN"

    def test_ic_engineer_denied_rbac_test(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER role is denied access to ADMIN-only endpoint."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["ic_engineer"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "ic_engineer", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.get(
            "/api/v1/auth/rbac-test", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_denied_rbac_test(self, client, mock_db, fake_redis) -> None:
        """SPONSOR role is denied access to ADMIN-only endpoint."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["sponsor"]))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "sponsor", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.get(
            "/api/v1/auth/rbac-test", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert resp.status_code == 403

    def test_rbac_test_no_token(self, client) -> None:
        """No token → 401."""
        resp = client.get("/api/v1/auth/rbac-test")
        assert resp.status_code == 401


# ===========================================================================
# Role-permission mapping unit tests
# ===========================================================================


class TestRolePermissions:
    """Verify the role → permission mapping (PRD §3)."""

    def test_admin_permissions(self) -> None:
        from app.services.auth import get_permissions

        assert get_permissions("ADMIN") == ["*"]

    def test_ic_engineer_permissions(self) -> None:
        from app.services.auth import get_permissions

        perms = get_permissions("IC_ENGINEER")
        assert "loop:*" in perms
        assert "metric:*" in perms
        assert "diagnosis:*" in perms
        assert "tuning:*" in perms
        assert "portal:view" in perms

    def test_pe_engineer_permissions(self) -> None:
        from app.services.auth import get_permissions

        perms = get_permissions("PE_ENGINEER")
        assert "loop:view" in perms
        assert "metric:view" in perms
        assert "diagnosis:view" in perms
        assert "portal:view" in perms
        assert "tracker:*" in perms
        # WS-D 性能#7 R1：放开回路配置入口（create/edit/export），对齐后端 require_roles
        assert "loop:create" in perms
        assert "loop:edit" in perms
        assert "loop:export" in perms
        # 不含 loop:delete（ADMIN 专属）、loop:import（IC_ENGINEER 专属）
        assert "loop:delete" not in perms
        assert "loop:import" not in perms

    def test_sponsor_permissions(self) -> None:
        from app.services.auth import get_permissions

        perms = get_permissions("SPONSOR")
        assert perms == ["portal:view", "metric:view", "diagnosis:view"]

    def test_expert_permissions(self) -> None:
        from app.services.auth import get_permissions

        perms = get_permissions("EXPERT")
        assert "portal:view" in perms
        assert "metric:view" in perms
        assert "diagnosis:view" in perms
        assert "tracker:review" in perms

    def test_default_home_all_roles(self) -> None:
        from app.services.auth import get_default_home

        for role in ("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "SPONSOR", "EXPERT"):
            assert get_default_home(role) == "/dashboard"


# ===========================================================================
# S5-AUTH P1: 首次登录强制改密（must_change_password）
# ===========================================================================


def _flagged_user() -> MagicMock:
    """构造 must_change_password=True 的测试用户（独立副本，不污染共享 TEST_USERS）。"""
    from tests.conftest import _make_user

    user = _make_user("admin", "ADMIN", user_id="00000000-0000-0000-0000-000000000001")
    user.must_change_password = True
    return user


class TestForceChangePassword:
    """强制改密全流程：登录带标志 → 写操作 403 → 改密 → 标志清除 → 正常."""

    def test_login_response_carries_flag(self, client, mock_db, fake_redis) -> None:
        """标志用户登录响应带 mustChangePassword=True；普通用户为 False."""
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(_flagged_user()))
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["user"]["mustChangePassword"] is True

        mock_db.execute = AsyncMock(return_value=make_db_execute_return(TEST_USERS["admin"]))
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["user"]["mustChangePassword"] is False

    def test_read_endpoints_allowed(self, client, mock_db, fake_redis) -> None:
        """标志用户的读端点（GET /me）放行，避免前端死锁."""
        user = _flagged_user()
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(user))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert resp.status_code == 200
        assert resp.json()["data"]["mustChangePassword"] is True

    def test_write_endpoint_rejected(self, client, mock_db, fake_redis) -> None:
        """标志用户的写操作端点一律 403 ERR_PASSWORD_CHANGE_REQUIRED."""
        user = _flagged_user()
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(user))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {access_token}"},
            json={},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PASSWORD_CHANGE_REQUIRED"

    def test_logout_exempt(self, client, mock_db, fake_redis) -> None:
        """登出端点豁免：标志用户可正常登出重新登录."""
        user = _flagged_user()
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(user))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200

    def test_change_password_exempt_and_clears_flag(self, client, mock_db, fake_redis) -> None:
        """改密端点豁免，且 UPDATE 语句同时清除 must_change_password 标志."""
        user = _flagged_user()
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(user))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.put(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"oldPassword": TEST_PASSWORD, "newPassword": "NewPass@2026"},
        )
        assert resp.status_code == 200

        # change_password 的 UPDATE 必须同时写 must_change_password=False
        update_stmt = str(mock_db.execute.call_args[0][0])
        assert "must_change_password" in update_stmt

    def test_after_flag_cleared_write_allowed(self, client, mock_db, fake_redis) -> None:
        """标志清除后写操作恢复：守卫放行，进入业务逻辑（旧密码错误 → 400 而非 403）."""
        user = _flagged_user()
        user.must_change_password = False  # 模拟改密成功后的状态
        mock_db.execute = AsyncMock(return_value=make_db_execute_return(user))
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": TEST_PASSWORD},
        )
        access_token = login_resp.json()["data"]["accessToken"]

        resp = client.put(
            "/api/v1/auth/password",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"oldPassword": "wrongold", "newPassword": "NewPass@2026"},
        )
        # 守卫放行后由业务逻辑返回 ERR_INVALID_CREDENTIALS（400），而非 403
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_CREDENTIALS"
