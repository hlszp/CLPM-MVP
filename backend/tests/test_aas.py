"""AAS API tests (S2-LOOP-002, S2-LOOP-003).

Covers:
- GET /api/v1/aas/config (ADMIN only)
- PUT /api/v1/aas/config (ADMIN only)
- POST /api/v1/aas/config/test (ADMIN only)
- GET /api/v1/aas/tags (paginated list)
- POST /api/v1/aas/sync (trigger sync)
- MockAasProvider generates ~50 tags
- LTTB downsampling algorithm
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import TEST_USERS, mock_current_user

# 测试用 Tag 数据
TAG_001 = MagicMock()
TAG_001.id = "00000000-0000-0000-0000-000000000301"
TAG_001.tag_name = "T-HDS-001-PV"
TAG_001.tag_description = "R-101 反应器入口温度 PV"
TAG_001.tag_type = "PV"
TAG_001.current_value = 358.50
TAG_001.quality = "GOOD"
TAG_001.last_sync_at = MagicMock()
TAG_001.last_sync_at.isoformat.return_value = "2026-06-20T10:00:00"
TAG_001.is_linked = True


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalar_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = value
    return result


class TestAasConfig:
    """AAS Config API tests."""

    def test_get_config_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以获取 AAS 配置。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert "endpoint" in data
        assert "syncIntervalSeconds" in data
        assert "enabled" in data
        assert "mockMode" in data

    def test_get_config_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能获取 AAS 配置（403）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_update_config_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以更新 AAS 配置。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/aas/config",
                headers={"Authorization": "Bearer fake-token"},
                json={"endpoint": "opc.tcp://new-host:4840", "enabled": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"

    def test_test_connection_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以测试 AAS 连接。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/aas/config/test",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert "success" in body["data"]


class TestAasTags:
    """GET /api/v1/aas/tags tests."""

    def test_list_tags_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取 Tag 列表。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(1)
            return _make_scalars_mock([TAG_001])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/aas/tags",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["tagName"] == "T-HDS-001-PV"

    def test_list_tags_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/aas/tags")
        assert resp.status_code == 401


class TestAasSync:
    """POST /api/v1/aas/sync tests."""

    def test_trigger_sync_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 触发同步返回 task_id。"""
        mock_task = MagicMock()
        mock_task.id = "task-uuid-xxx"

        with (
            mock_current_user(TEST_USERS["admin"]),
            patch("app.tasks.aas_sync.trigger_sync") as mock_trigger,
        ):
            mock_trigger.delay.return_value = mock_task
            resp = client.post(
                "/api/v1/aas/sync",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["taskId"] == "task-uuid-xxx"
        assert body["data"]["status"] == "PROCESSING"


class TestMockAasProvider:
    """MockAasProvider 单元测试。"""

    async def test_mock_provider_generates_tags(self) -> None:
        """MockAasProvider 生成约 50 条 Tag。"""
        from app.services.aas_sync import MockAasProvider

        provider = MockAasProvider()
        tags = await provider.read_all_tags()
        # 7 个回路 × 7 个 Tag + 1 条 OTHER = 50 条
        assert len(tags) == 50

    async def test_mock_provider_tag_types(self) -> None:
        """MockAasProvider 生成的 Tag 覆盖所有类型。"""
        from app.services.aas_sync import MockAasProvider

        provider = MockAasProvider()
        tags = await provider.read_all_tags()
        tag_types = {t["tag_type"] for t in tags}
        assert "PV" in tag_types
        assert "SP" in tag_types
        assert "OP" in tag_types
        assert "MODE" in tag_types
        assert "PID_P" in tag_types
        assert "PID_I" in tag_types
        assert "PID_D" in tag_types
        assert "OTHER" in tag_types

    async def test_mock_provider_pv_has_quality(self) -> None:
        """PV 类型 Tag 携带质量码。"""
        from app.services.aas_sync import MockAasProvider

        provider = MockAasProvider()
        tags = await provider.read_all_tags()
        pv_tags = [t for t in tags if t["tag_type"] == "PV"]
        assert len(pv_tags) > 0
        for pv in pv_tags:
            assert pv["quality"] in ("GOOD", "BAD", "UNCERTAIN")


class TestLTTB:
    """LTTB 降采样算法单元测试。"""

    def test_lttb_no_downsample_below_threshold(self) -> None:
        """数据点数低于阈值时不降采样。"""
        from app.services.monitor import lttb_downsample

        data = [{"ts": i, "value": float(i), "quality": "GOOD"} for i in range(100)]
        result = lttb_downsample(data)
        assert len(result) == 100

    def test_lttb_downsample_above_threshold(self) -> None:
        """数据点数超过阈值时降采样到 2000 点。"""
        from app.services.monitor import lttb_downsample

        data = [{"ts": i, "value": float(i) * 0.1, "quality": "GOOD"} for i in range(15000)]
        result = lttb_downsample(data)
        assert len(result) == 2000

    def test_lttb_preserves_endpoints(self) -> None:
        """降采样后保留首尾两个点。"""
        from app.services.monitor import lttb_downsample

        data = [{"ts": i, "value": float(i), "quality": "GOOD"} for i in range(15000)]
        result = lttb_downsample(data)
        assert result[0]["ts"] == 0
        assert result[-1]["ts"] == 14999

    def test_lttb_empty_data(self) -> None:
        """空数据返回空列表。"""
        from app.services.monitor import lttb_downsample

        result = lttb_downsample([])
        assert result == []
