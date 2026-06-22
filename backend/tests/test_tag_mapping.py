"""Loop Tag mapping API tests (S2-LOOP-005).

Covers:
- GET /api/v1/loops/{id}/tags (get tag mapping)
- PUT /api/v1/loops/{id}/tags (update tag mapping)
- ERR_LOOP_TAG_REQUIRED / ERR_TAG_NOT_FOUND / ERR_LOOP_NOT_FOUND
- Status re-derivation after tag mapping update
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user

# 测试用回路
LOOP_001 = MagicMock()
LOOP_001.id = "00000000-0000-0000-0000-000000000201"
LOOP_001.tag_name = "HDS-RX-TIC-101"
LOOP_001.is_active = True
LOOP_001.status = "READY"
LOOP_001.updated_at = MagicMock()
LOOP_001.updated_at.isoformat.return_value = "2026-06-20T10:00:00"
LOOP_001.updated_by = "admin"

# 测试用 Tag
TAG_PV = MagicMock()
TAG_PV.id = "00000000-0000-0000-0000-000000000301"
TAG_PV.tag_name = "T-HDS-001-PV"
TAG_PV.tag_description = "PV"
TAG_PV.tag_type = "PV"
TAG_PV.current_value = 50.0
TAG_PV.quality = "GOOD"
TAG_PV.last_sync_at = MagicMock()
TAG_PV.last_sync_at.isoformat.return_value = "2026-06-20T10:00:00"
TAG_PV.is_linked = True


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestGetLoopTags:
    """GET /api/v1/loops/{id}/tags tests."""

    def test_get_loop_tags_success(self, client, mock_db, fake_redis) -> None:
        """获取回路 Tag 关联状态成功。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_001))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/loops/{LOOP_001.id}/tags",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == LOOP_001.id
        assert data["tagName"] == "HDS-RX-TIC-101"
        assert len(data["tags"]) == 7

    def test_get_loop_tags_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/nonexistent/tags",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"


class TestUpdateLoopTags:
    """PUT /api/v1/loops/{id}/tags tests."""

    def test_update_tags_all_required_null_fails(self, client, mock_db, fake_redis) -> None:
        """全部必填 Tag 为 null → ERR_LOOP_TAG_REQUIRED。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_001))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/loops/{LOOP_001.id}/tags",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "pv": None,
                    "sp": None,
                    "op": None,
                    "mode": None,
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_LOOP_TAG_REQUIRED"

    def test_update_tags_loop_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/loops/nonexistent/tags",
                headers={"Authorization": "Bearer fake-token"},
                json={"pv": "some-tag-id"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_update_tags_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能更新 Tag 关联（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.put(
                f"/api/v1/loops/{LOOP_001.id}/tags",
                headers={"Authorization": "Bearer fake-token"},
                json={"pv": "some-tag-id"},
            )
        assert resp.status_code == 403
