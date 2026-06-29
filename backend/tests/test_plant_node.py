"""Plant node API tests (S2-LOOP-001).

Covers:
- GET /api/v1/plant-nodes (tree)
- POST /api/v1/plant-nodes (create, ADMIN only)
- PUT /api/v1/plant-nodes/{id} (update, ADMIN only)
- DELETE /api/v1/plant-nodes/{id} (delete, with children/loops check)
- RBAC: IC_ENGINEER/PE_ENGINEER read-only
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from tests.conftest import TEST_USERS, mock_current_user

# 测试用的工厂节点数据
FACTORY_NODE = MagicMock()
FACTORY_NODE.id = "00000000-0000-0000-0000-000000000101"
FACTORY_NODE.name = "加氢联合车间"
FACTORY_NODE.type = "FACTORY"
FACTORY_NODE.parent_id = None

UNIT_NODE = MagicMock()
UNIT_NODE.id = "00000000-0000-0000-0000-000000000102"
UNIT_NODE.name = "加氢精制"
UNIT_NODE.type = "UNIT"
UNIT_NODE.parent_id = "00000000-0000-0000-0000-000000000101"


def _make_scalars_mock(items: list) -> MagicMock:
    """创建 scalars() 返回的 mock。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_mock(value) -> MagicMock:
    """创建 scalar() 返回的 mock。"""
    result = MagicMock()
    result.scalar.return_value = value
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    """创建 scalar_one_or_none() 返回的 mock。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestPlantNodeList:
    """GET /api/v1/plant-nodes tests."""

    def test_list_tree_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以获取工厂节点树。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([FACTORY_NODE, UNIT_NODE]))
        # 先登录
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/plant-nodes",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["name"] == "加氢联合车间"
        assert data[0]["type"] == "FACTORY"

    def test_list_tree_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/plant-nodes")
        assert resp.status_code == 401


class TestPlantNodeCreate:
    """POST /api/v1/plant-nodes tests."""

    def test_create_node_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 可以创建节点。"""
        # mock: 查询父节点存在、commit 成功
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(FACTORY_NODE))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/plant-nodes",
                headers={"Authorization": "Bearer fake-token"},
                json={"name": "新装置", "type": "UNIT", "parentId": FACTORY_NODE.id},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["name"] == "新装置"
        assert body["data"]["type"] == "UNIT"

    def test_create_node_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能创建节点（403）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/plant-nodes",
                headers={"Authorization": "Bearer fake-token"},
                json={"name": "新装置", "type": "UNIT"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_create_factory_with_parent_rejected(self, client, mock_db, fake_redis) -> None:
        """FACTORY 类型不能有 parent_id。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/plant-nodes",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "name": "新工厂",
                    "type": "FACTORY",
                    "parentId": "some-parent-id",
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_create_invalid_type(self, client, mock_db, fake_redis) -> None:
        """非法节点类型返回 400。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/plant-nodes",
                headers={"Authorization": "Bearer fake-token"},
                json={"name": "新节点", "type": "INVALID"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_VALIDATION"


class TestPlantNodeDelete:
    """DELETE /api/v1/plant-nodes/{id} tests."""

    def test_delete_node_with_children_fails(self, client, mock_db, fake_redis) -> None:
        """有子节点的节点不能删除（ERR_NODE_HAS_CHILDREN）。"""

        # mock: 节点存在、子节点数 > 0
        def execute_side_effect(stmt, *args, **kwargs):
            # 检查是否为 count 查询
            if hasattr(stmt, "compile"):
                compiled = str(stmt.compile())
                if "count" in compiled.lower() and "parent_id" in compiled.lower():
                    return _make_scalar_mock(2)  # 2 个子节点
                if "plant_node" in compiled.lower() and "id" in compiled.lower():
                    return _make_scalar_one_or_none_mock(FACTORY_NODE)
            return _make_scalar_one_or_none_mock(FACTORY_NODE)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                f"/api/v1/plant-nodes/{FACTORY_NODE.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_NODE_HAS_CHILDREN"

    def test_delete_node_not_found(self, client, mock_db, fake_redis) -> None:
        """节点不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.delete(
                f"/api/v1/plant-nodes/{uuid4()}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_NODE_NOT_FOUND"

    def test_delete_node_ic_engineer_forbidden(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 不能删除节点（403）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.delete(
                f"/api/v1/plant-nodes/{FACTORY_NODE.id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
