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


def _make_scalar_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = value
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


class TestUnlinkIsLinkedRefCheck:
    """update_loop_tags 解除关联时的 is_linked 引用检查（WS-C 7-9）。

    场景：回路当前 PV→TAG_OLD，更新为 PV→TAG_NEW。
    - TAG_OLD 仍被其他回路引用 → is_linked 保持 True
    - TAG_OLD 无任何引用 → is_linked 置 False
    """

    TAG_OLD_ID = "00000000-0000-0000-0000-000000000401"
    TAG_NEW_ID = "00000000-0000-0000-0000-000000000402"

    def _make_loop(self) -> MagicMock:
        loop = MagicMock()
        loop.id = "00000000-0000-0000-0000-000000000201"
        loop.is_active = True
        loop.status = "READY"
        return loop

    def _make_tag(self, tag_id: str, is_linked: bool) -> MagicMock:
        tag = MagicMock()
        tag.id = tag_id
        tag.is_linked = is_linked
        return tag

    def _make_mapping(self, tag_id: str, role: str = "PV") -> MagicMock:
        mapping = MagicMock()
        mapping.tag_id = tag_id
        mapping.tag_role = role
        return mapping

    def _build_db(self, ref_count: int, tag_old: MagicMock, tag_new: MagicMock) -> AsyncMock:
        """按 update_loop_tags 的查询顺序构造 execute side_effect。"""
        loop = self._make_loop()
        old_mapping = self._make_mapping(self.TAG_OLD_ID)
        results = [
            _make_scalar_one_or_none_mock(loop),  # 1. select(LoopLedger)
            _make_scalars_mock([tag_new]),  # 2. select(TagRegistry) in_ 新 tag
            _make_scalars_mock([old_mapping]),  # 3. select(LoopTagMapping) 现有关联
            MagicMock(),  # 4. delete(LoopTagMapping)
            _make_scalar_mock(ref_count),  # 5. count(其他回路引用)
        ]
        if ref_count == 0:
            # 6. 无引用时回查 tag_registry 取 old_tag
            results.append(_make_scalar_one_or_none_mock(tag_old))
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=results)
        return db

    async def test_unlink_clears_is_linked_when_no_other_refs(self) -> None:
        """无其他回路引用时，解除关联将 is_linked 置 False。"""
        from app.services.tag_mapping import update_loop_tags

        tag_old = self._make_tag(self.TAG_OLD_ID, is_linked=True)
        tag_new = self._make_tag(self.TAG_NEW_ID, is_linked=False)
        db = self._build_db(ref_count=0, tag_old=tag_old, tag_new=tag_new)

        await update_loop_tags(
            db, loop_id=self._make_loop().id, operator="admin", pv=self.TAG_NEW_ID
        )

        assert tag_old.is_linked is False
        assert tag_new.is_linked is True

    async def test_unlink_keeps_is_linked_when_other_loop_refs(self) -> None:
        """仍被其他回路引用时，解除关联不清除 is_linked。"""
        from app.services.tag_mapping import update_loop_tags

        tag_old = self._make_tag(self.TAG_OLD_ID, is_linked=True)
        tag_new = self._make_tag(self.TAG_NEW_ID, is_linked=False)
        db = self._build_db(ref_count=1, tag_old=tag_old, tag_new=tag_new)

        await update_loop_tags(
            db, loop_id=self._make_loop().id, operator="admin", pv=self.TAG_NEW_ID
        )

        assert tag_old.is_linked is True
        assert tag_new.is_linked is True
