"""Loop ledger API tests (S2-LOOP-004).

Covers:
- GET /api/v1/loops (list)
- POST /api/v1/loops (create, ERR_LOOP_DUPLICATE check)
- GET /api/v1/loops/{id} (detail)
- PUT /api/v1/loops/{id} (update)
- DELETE /api/v1/loops/{id} (delete, ERR_LOOP_HAS_TAGS check)
- Status derivation: READY/PARTIAL/INACTIVE
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user

# 测试用的回路数据
LOOP_001 = MagicMock()
LOOP_001.id = "00000000-0000-0000-0000-000000000201"
LOOP_001.tag_name = "HDS-RX-TIC-101"
LOOP_001.description = "R-101 反应器入口温度调节回路"
LOOP_001.unit_id = "00000000-0000-0000-0000-000000000111"
LOOP_001.score_weight = None
LOOP_001.is_active = True
LOOP_001.last_aas_sync_at = None
LOOP_001.status = "READY"
LOOP_001.created_at = MagicMock()
LOOP_001.created_at.isoformat.return_value = "2026-06-20T10:00:00"
LOOP_001.updated_at = MagicMock()
LOOP_001.updated_at.isoformat.return_value = "2026-06-20T10:00:00"
LOOP_001.created_by = "admin"
LOOP_001.updated_by = None
LOOP_001.score_weights = None
LOOP_001.remark = None
LOOP_001.loop_type = "TEMPERATURE"
LOOP_001.level = 3


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


class TestLoopList:
    """GET /api/v1/loops tests."""

    def test_list_loops_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取回路列表。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            compiled = str(stmt.compile()).lower()
            if "count" in compiled:
                return _make_scalar_mock(1)
            return _make_scalars_mock([LOOP_001])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["tagName"] == "HDS-RX-TIC-101"

    def test_list_loops_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/loops")
        assert resp.status_code == 401


class TestLoopCreate:
    """POST /api/v1/loops tests."""

    def test_create_loop_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN/IC_ENGINEER 可以创建回路。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tagName": "NEW-LOOP-001",
                    "description": "测试回路",
                    "isActive": True,
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["tagName"] == "NEW-LOOP-001"
        assert body["data"]["status"] == "PARTIAL"

    def test_create_loop_duplicate(self, client, mock_db, fake_redis) -> None:
        """tag_name 重复返回 ERR_LOOP_DUPLICATE。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_001))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={"tagName": "HDS-RX-TIC-101", "isActive": True},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_LOOP_DUPLICATE"

    def test_create_loop_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能创建回路（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={"tagName": "NEW-LOOP", "isActive": True},
            )
        assert resp.status_code == 403

    def test_create_loop_valid_weight_sum(self, client, mock_db, fake_redis) -> None:
        """评分权重总和为 100 应该成功。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tagName": "NEW-LOOP-002",
                    "isActive": True,
                    "scoreWeights": {
                        "auto_mode_rate": 50,
                        "steady_rate": 0,
                        "accuracy_rate": 0,
                        "fast_response_rate": 50,
                        "oscillation_rate": 0,
                        "saturation_rate": 0,
                    },
                },
            )
        assert resp.status_code == 201


class TestLoopDetail:
    """GET /api/v1/loops/{id} tests."""

    def test_get_loop_detail_success(self, client, mock_db, fake_redis) -> None:
        """获取回路详情成功。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_001))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/loops/{LOOP_001.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["basicInfo"]["tagName"] == "HDS-RX-TIC-101"
        assert "tagMapping" in data
        assert "runtimeParams" in data
        assert "aasSyncStatus" in data

    def test_get_loop_detail_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/nonexistent",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"


class TestLoopDelete:
    """DELETE /api/v1/loops/{id} tests."""

    def test_delete_loop_with_tags_fails(self, client, mock_db, fake_redis) -> None:
        """有关联 Tag 的回路不能删除（ERR_LOOP_HAS_TAGS）。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(LOOP_001)
            return _make_scalar_mock(7)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                f"/api/v1/loops/{LOOP_001.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_LOOP_HAS_TAGS"

    def test_delete_loop_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能删除回路（403，仅 ADMIN）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                f"/api/v1/loops/{LOOP_001.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403


class TestStatusDerivation:
    """状态推导逻辑单元测试。"""

    async def test_status_inactive_when_not_active(self) -> None:
        """is_active=False → INACTIVE。"""
        from app.services.loop import derive_loop_status

        loop = MagicMock()
        loop.is_active = False
        db = AsyncMock()
        status = await derive_loop_status(db, loop, mappings={})
        assert status == "INACTIVE"

    async def test_status_partial_when_missing_required(self) -> None:
        """is_active=True 但缺必填 Tag → PARTIAL。"""
        from app.services.loop import derive_loop_status

        loop = MagicMock()
        loop.is_active = True
        db = AsyncMock()
        mappings = {"PV": MagicMock()}
        status = await derive_loop_status(db, loop, mappings=mappings)
        assert status == "PARTIAL"

    async def test_status_ready_when_all_required(self) -> None:
        """is_active=True 且 4 个必填 Tag 齐全 → READY。"""
        from app.services.loop import derive_loop_status

        loop = MagicMock()
        loop.is_active = True
        db = AsyncMock()
        mappings = {role: MagicMock() for role in ("PV", "SP", "OP", "MODE")}
        status = await derive_loop_status(db, loop, mappings=mappings)
        assert status == "READY"
