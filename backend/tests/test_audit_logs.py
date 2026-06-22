"""Audit log API tests (S5-SYS-002).

Covers:
- GET /api/v1/audit-logs (list, filters, pagination)
- RBAC: only ADMIN can access; other roles get 403
- No token returns 401
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_audit_log(
    log_id: str = "00000000-0000-0000-0000-000000000a01",
    operator: str = "admin",
    operation_type: str = "USER_CREATE",
    target_type: str = "sys_user",
    target_id: str = "00000000-0000-0000-0000-000000000101",
) -> MagicMock:
    log = MagicMock()
    log.id = log_id
    log.operator = operator
    log.operation_type = operation_type
    log.target_type = target_type
    log.target_id = target_id
    log.before_value = None
    log.after_value = '{"username": "newuser"}'
    log.operated_at = datetime.now(UTC)
    return log


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_count_mock(count: int) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = count
    return result


# ---------------------------------------------------------------------------
# GET /api/v1/audit-logs — list
# ---------------------------------------------------------------------------


class TestListAuditLogs:
    """GET /api/v1/audit-logs tests."""

    def test_list_audit_logs_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can list audit logs."""
        logs = [_make_audit_log(), _make_audit_log(log_id="id2", operation_type="USER_UPDATE")]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(2)
            return _make_scalars_mock(logs)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/audit-logs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 2
        assert len(body["data"]["items"]) == 2
        item = body["data"]["items"][0]
        assert "logId" in item
        assert "operator" in item
        assert "operationType" in item
        assert "targetType" in item
        assert "beforeValue" in item
        assert "afterValue" in item
        assert "operatedAt" in item
        assert "clientIp" in item

    def test_list_audit_logs_with_filters(self, client, mock_db, fake_redis) -> None:
        """List audit logs with operator/operationType/time filters."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(1)
            return _make_scalars_mock([_make_audit_log()])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/audit-logs?operator=admin&operationType=USER_CREATE"
                "&startTime=2026-01-01T00:00:00Z&endTime=2026-12-31T23:59:59Z"
                "&page=1&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 1
        assert body["data"]["pageSize"] == 10

    def test_list_audit_logs_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER cannot view audit logs (403)."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/audit-logs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_list_audit_logs_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR cannot view audit logs (403)."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/audit-logs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_list_audit_logs_no_token(self, client) -> None:
        """No token returns 401."""
        resp = client.get("/api/v1/audit-logs")
        assert resp.status_code == 401

    def test_list_audit_logs_empty(self, client, mock_db, fake_redis) -> None:
        """Empty audit log list returns empty items."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/audit-logs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["total"] == 0
        assert body["data"]["items"] == []
