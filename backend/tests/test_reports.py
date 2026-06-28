"""Report configuration API tests (S5-SYS-003).

Covers:
- GET /api/v1/reports/configs (list)
- POST /api/v1/reports/configs (create)
- PUT /api/v1/reports/configs/{id} (update)
- POST /api/v1/reports/generate (trigger generation, returns taskId)
- RBAC: only ADMIN can access; other roles get 403
- Key error branches: config not found
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_report_config(
    config_id: str = "00000000-0000-0000-0000-000000000b01",
    name: str = "日报配置",
    report_period: str = "DAILY",
    recipients: str = '["00000000-0000-0000-0000-000000000001"]',
    is_enabled: bool = True,
) -> MagicMock:
    c = MagicMock()
    c.id = config_id
    c.name = name
    c.report_period = report_period
    c.recipients = recipients
    c.content_template = '{"sections": ["summary"]}'
    c.is_enabled = is_enabled
    c.created_by = "admin"
    c.updated_by = "admin"
    c.created_at = datetime.now(UTC)
    c.updated_at = datetime.now(UTC)
    return c


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# GET /api/v1/reports/configs — list
# ---------------------------------------------------------------------------


class TestListConfigs:
    """GET /api/v1/reports/configs tests."""

    def test_list_configs_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can list report configs."""
        configs = [_make_report_config(), _make_report_config(config_id="id2", name="周报")]
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock(configs))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert len(body["data"]) == 2
        assert body["data"][0]["name"] == "日报配置"
        assert body["data"][0]["reportPeriod"] == "DAILY"
        assert isinstance(body["data"][0]["recipients"], list)

    def test_list_configs_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER cannot list report configs (403)."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_list_configs_no_token(self, client) -> None:
        """No token returns 401."""
        resp = client.get("/api/v1/reports/configs")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/reports/configs — create
# ---------------------------------------------------------------------------


class TestCreateConfig:
    """POST /api/v1/reports/configs tests."""

    def test_create_config_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can create a report config."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "name": "日报配置",
                    "reportPeriod": "DAILY",
                    "recipients": ["00000000-0000-0000-0000-000000000001"],
                    "contentTemplate": {"sections": ["summary"]},
                    "isEnabled": True,
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["name"] == "日报配置"
        assert body["data"]["reportPeriod"] == "DAILY"
        assert body["data"]["recipients"] == ["00000000-0000-0000-0000-000000000001"]
        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_create_config_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR cannot create report configs (403)."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "name": "日报配置",
                    "reportPeriod": "DAILY",
                    "recipients": ["id1"],
                },
            )
        assert resp.status_code == 403

    def test_create_config_invalid_period(self, client, mock_db, fake_redis) -> None:
        """Invalid report period is rejected (422)."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "name": "配置",
                    "reportPeriod": "INVALID",
                    "recipients": ["id1"],
                },
            )
        assert resp.status_code == 422

    def test_create_config_empty_recipients(self, client, mock_db, fake_redis) -> None:
        """Empty recipients list is rejected (422)."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/reports/configs",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "name": "配置",
                    "reportPeriod": "DAILY",
                    "recipients": [],
                },
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/v1/reports/configs/{id} — update
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    """PUT /api/v1/reports/configs/{id} tests."""

    def test_update_config_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can update a report config."""
        config = _make_report_config()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(config))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/reports/configs/{config.id}",
                headers={"Authorization": "Bearer fake-token"},
                json={"name": "更新配置", "isEnabled": False},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["name"] == "更新配置"
        assert body["data"]["isEnabled"] is False

    def test_update_config_not_found(self, client, mock_db, fake_redis) -> None:
        """Non-existent config returns ERR_REPORT_CONFIG_NOT_FOUND (404)."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/reports/configs/00000000-0000-0000-0000-000000000999",
                headers={"Authorization": "Bearer fake-token"},
                json={"name": "更新"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_REPORT_CONFIG_NOT_FOUND"

    def test_update_config_expert_forbidden(self, client, mock_db, fake_redis) -> None:
        """EXPERT cannot update report configs (403)."""
        with mock_current_user(TEST_USERS["expert"]):
            resp = client.put(
                "/api/v1/reports/configs/some-id",
                headers={"Authorization": "Bearer fake-token"},
                json={"name": "更新"},
            )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/reports/generate — trigger generation
# ---------------------------------------------------------------------------


class TestGenerateReport:
    """POST /api/v1/reports/generate tests."""

    def test_generate_report_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN can trigger report generation, returns taskId."""
        # Mock the Celery task's .delay() method
        with patch("app.tasks.report_generator.generate_report_task") as mock_task:
            mock_task.delay = MagicMock(return_value=MagicMock(id="celery-task-id"))
            with mock_current_user(TEST_USERS["admin"]):
                resp = client.post(
                    "/api/v1/reports/generate",
                    headers={"Authorization": "Bearer fake-token"},
                    json={"reportPeriod": "DAILY"},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert "taskId" in body["data"]
        assert body["data"]["taskType"] == "REPORT_GENERATE"
        assert body["data"]["status"] == "PROCESSING"
        assert body["data"]["checkUrl"] is not None
        mock_db.add.assert_called()  # audit log written
        mock_db.commit.assert_called()

    def test_generate_report_with_config_id(self, client, mock_db, fake_redis) -> None:
        """Trigger generation with a valid config_id."""
        config = _make_report_config()
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(config))
        with patch("app.tasks.report_generator.generate_report_task") as mock_task:
            mock_task.delay = MagicMock(return_value=MagicMock(id="celery-task-id"))
            with mock_current_user(TEST_USERS["admin"]):
                resp = client.post(
                    "/api/v1/reports/generate",
                    headers={"Authorization": "Bearer fake-token"},
                    json={"configId": config.id},
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["taskType"] == "REPORT_GENERATE"

    def test_generate_report_config_not_found(self, client, mock_db, fake_redis) -> None:
        """Non-existent config_id returns ERR_REPORT_CONFIG_NOT_FOUND (404)."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/reports/generate",
                headers={"Authorization": "Bearer fake-token"},
                json={"configId": "nonexistent"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_REPORT_CONFIG_NOT_FOUND"

    def test_generate_report_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER cannot trigger report generation (403)."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/reports/generate",
                headers={"Authorization": "Bearer fake-token"},
                json={"reportPeriod": "DAILY"},
            )
        assert resp.status_code == 403

    def test_generate_report_no_token(self, client) -> None:
        """No token returns 401."""
        resp = client.post(
            "/api/v1/reports/generate",
            json={"reportPeriod": "DAILY"},
        )
        assert resp.status_code == 401
