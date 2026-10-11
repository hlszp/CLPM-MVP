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
    """POST /api/v1/reports/generate tests.

    IA-12（2026-10-10 诚实化）：占位生成实现已停用——无真实文件不得 COMPLETED，
    手动触发端点显式拒绝（503 ERR_REPORT_GENERATION_NOT_AVAILABLE），
    不再产出"占位路径 + COMPLETED"的假完成记录。
    """

    def test_generate_report_rejected_not_available(self, client, mock_db, fake_redis) -> None:
        """ADMIN 触发生成也明确拒绝（占位实现停用，UI 维持预配置提示不变）。"""
        # Celery 任务不应被派发（占位链路整体收口）
        with patch("app.tasks.report_generator.generate_report_task") as mock_task:
            with mock_current_user(TEST_USERS["admin"]):
                resp = client.post(
                    "/api/v1/reports/generate",
                    headers={"Authorization": "Bearer fake-token"},
                    json={"reportPeriod": "DAILY"},
                )
        assert resp.status_code == 503
        body = resp.json()
        assert body["code"] == "ERR_REPORT_GENERATION_NOT_AVAILABLE"
        assert "未开放" in body["message"]
        mock_task.delay.assert_not_called()
        mock_db.add.assert_not_called()  # 不再写审计/任务档案

    def test_generate_report_with_config_id_also_rejected(
        self, client, mock_db, fake_redis
    ) -> None:
        """带 config_id 同样拒绝（生成能力整体停用，与配置存在与否无关）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/reports/generate",
                headers={"Authorization": "Bearer fake-token"},
                json={"configId": "00000000-0000-0000-0000-000000000b01"},
            )
        assert resp.status_code == 503
        assert resp.json()["code"] == "ERR_REPORT_GENERATION_NOT_AVAILABLE"

    def test_generate_report_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER cannot trigger report generation (403, 角色门禁先于拒绝语义)."""
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
