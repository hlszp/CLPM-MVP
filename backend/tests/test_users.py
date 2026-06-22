"""User management API tests (S5-SYS-001).

Covers:
- GET /api/v1/users (list, filters, pagination)
- POST /api/v1/users (create, duplicate check)
- PUT /api/v1/users/{id} (update)
- DELETE /api/v1/users/{id} (disable / soft delete)
- PUT /api/v1/users/{id}/reset-password (reset password)
- RBAC: only ADMIN can access; other roles get 403
- Key error branches: user not found, duplicate username
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_user(
    user_id: str = "00000000-0000-0000-0000-000000000101",
    username: str = "newuser",
    display_name: str = "新用户",
    role: str = "IC_ENGINEER",
    is_active: bool = True,
) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.username = username
    u.password_hash = "hashed"
    u.display_name = display_name
    u.email = f"{username}@clpm.local"
    u.role = role
    u.is_active = is_active
    u.last_login_at = datetime.now(UTC)
    u.created_at = datetime.now(UTC)
    u.updated_at = datetime.now(UTC)
    return u


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_count_mock(count: int) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = count
    return result


# ---------------------------------------------------------------------------
# GET /api/v1/users — list
# ---------------------------------------------------------------------------


class TestListUsers:
    """GET /api/v1/users tests."""

    def test_list_users_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can list users."""
        users = [_make_user(), _make_user(user_id="id2", username="user2")]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Count query
                return _make_count_mock(2)
            # List query
            return _make_scalars_mock(users)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 2
        assert len(body["data"]["items"]) == 2
        assert body["data"]["page"] == 1
        assert body["data"]["pageSize"] == 20

    def test_list_users_with_filters(self, client, mock_db, fake_redis) -> None:
        """List users with keyword/role/isActive filters."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(1)
            return _make_scalars_mock([_make_user()])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/users?keyword=new&role=IC_ENGINEER&isActive=true&page=1&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["pageSize"] == 10

    def test_list_users_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER cannot list users (403)."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_list_users_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR cannot list users (403)."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_list_users_no_token(self, client) -> None:
        """No token returns 401."""
        resp = client.get("/api/v1/users")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/users — create
# ---------------------------------------------------------------------------


class TestCreateUser:
    """POST /api/v1/users tests."""

    def test_create_user_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can create a new user."""
        # First execute: check username uniqueness (returns None)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "username": "newuser",
                    "password": "Pass1234",
                    "displayName": "新用户",
                    "email": "newuser@clpm.local",
                    "role": "IC_ENGINEER",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["username"] == "newuser"
        assert body["data"]["role"] == "IC_ENGINEER"
        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_user_duplicate(self, client, mock_db, fake_redis) -> None:
        """Duplicate username returns ERR_USER_DUPLICATE (409)."""
        existing = _make_user(username="existing")
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(existing))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "username": "existing",
                    "password": "Pass1234",
                    "displayName": "重复用户",
                    "role": "IC_ENGINEER",
                },
            )
        assert resp.status_code == 409
        assert resp.json()["code"] == "ERR_USER_DUPLICATE"

    def test_create_user_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER cannot create users (403)."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "username": "newuser",
                    "password": "Pass1234",
                    "displayName": "新用户",
                    "role": "IC_ENGINEER",
                },
            )
        assert resp.status_code == 403

    def test_create_user_weak_password(self, client, mock_db, fake_redis) -> None:
        """Password without letters+digits is rejected (422)."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "username": "newuser",
                    "password": "12345678",
                    "displayName": "新用户",
                    "role": "IC_ENGINEER",
                },
            )
        assert resp.status_code == 422

    def test_create_user_invalid_role(self, client, mock_db, fake_redis) -> None:
        """Invalid role is rejected (422)."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/users",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "username": "newuser",
                    "password": "Pass1234",
                    "displayName": "新用户",
                    "role": "INVALID_ROLE",
                },
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/v1/users/{id} — update
# ---------------------------------------------------------------------------


class TestUpdateUser:
    """PUT /api/v1/users/{id} tests."""

    def test_update_user_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can update user info."""
        user = _make_user()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(user))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/users/{user.id}",
                headers={"Authorization": "Bearer fake-token"},
                json={"displayName": "更新姓名", "role": "PE_ENGINEER"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["displayName"] == "更新姓名"
        assert body["data"]["role"] == "PE_ENGINEER"

    def test_update_user_not_found(self, client, mock_db, fake_redis) -> None:
        """Non-existent user returns ERR_USER_NOT_FOUND (404)."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/users/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
                json={"displayName": "更新"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_USER_NOT_FOUND"

    def test_update_user_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR cannot update users (403)."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.put(
                "/api/v1/users/some-id",
                headers={"Authorization": "Bearer fake-token"},
                json={"displayName": "更新"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/v1/users/{id} — disable (soft delete)
# ---------------------------------------------------------------------------


class TestDisableUser:
    """DELETE /api/v1/users/{id} tests."""

    def test_disable_user_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can disable a user (soft delete)."""
        user = _make_user(is_active=True)
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(user))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                f"/api/v1/users/{user.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["isActive"] is False

    def test_disable_user_not_found(self, client, mock_db, fake_redis) -> None:
        """Non-existent user returns ERR_USER_NOT_FOUND (404)."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                "/api/v1/users/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_USER_NOT_FOUND"

    def test_disable_user_expert_forbidden(self, client, mock_db, fake_redis) -> None:
        """EXPERT cannot disable users (403)."""
        with mock_current_user(TEST_USERS["expert"]):
            resp = client.delete(
                "/api/v1/users/some-id",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/v1/users/{id}/reset-password
# ---------------------------------------------------------------------------


class TestResetPassword:
    """PUT /api/v1/users/{id}/reset-password tests."""

    def test_reset_password_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can reset a user's password."""
        user = _make_user()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(user))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/users/{user.id}/reset-password",
                headers={"Authorization": "Bearer fake-token"},
                json={"newPassword": "NewPass2026"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["passwordChanged"] is True

    def test_reset_password_not_found(self, client, mock_db, fake_redis) -> None:
        """Non-existent user returns ERR_USER_NOT_FOUND (404)."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/users/nonexistent/reset-password",
                headers={"Authorization": "Bearer fake-token"},
                json={"newPassword": "NewPass2026"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_USER_NOT_FOUND"

    def test_reset_password_pe_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER cannot reset passwords (403)."""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.put(
                "/api/v1/users/some-id/reset-password",
                headers={"Authorization": "Bearer fake-token"},
                json={"newPassword": "NewPass2026"},
            )
        assert resp.status_code == 403

    def test_reset_password_weak(self, client, mock_db, fake_redis) -> None:
        """Weak password is rejected (422)."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/users/some-id/reset-password",
                headers={"Authorization": "Bearer fake-token"},
                json={"newPassword": "12345678"},
            )
        assert resp.status_code == 422
