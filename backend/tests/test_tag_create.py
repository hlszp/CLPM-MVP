"""POST /api/v1/tags 新建测点端点测试。

验证：
- ADMIN/IC_ENGINEER 可创建，返回 200 + TAG_CREATE 审计落库
- 位号重复返回 ERR_TAG_ALREADY_EXISTS
- 非法 measureType/tagType 返回 400 校验错误
- PE_ENGINEER 只读角色返回 403
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user


def _make_existing_tag(tag_name: str) -> MagicMock:
    """构造一个已存在的 mock TagRegistry（用于位号重复场景）。"""
    tag = MagicMock()
    tag.tag_name = tag_name
    return tag


class TestCreateTag:
    """POST /api/v1/tags 端点测试。"""

    def test_create_tag_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 新建测点成功。"""
        # 默认 mock_db.execute 返回 scalar_one_or_none()=None（无重复位号）
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tags",
                json={
                    "tagName": "80FIC10001_PV",
                    "tagDescription": "新增加热炉流量测点",
                    "measureType": "FLOW",
                    "tagType": "PV",
                    "rangeMin": 0,
                    "rangeMax": 100,
                    "unit": "t/h",
                },
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["message"] == "测点创建成功"
        data = body["data"]
        assert data["tagName"] == "80FIC10001_PV"
        assert data["tagType"] == "PV"
        assert data["measureType"] == "FLOW"
        assert data["isLinked"] is False
        # 落库 + 审计写入
        assert mock_db.add.called
        assert mock_db.commit.called

    def test_create_tag_duplicate_name_rejected(self, client, mock_db, fake_redis) -> None:
        """位号已存在返回 ERR_TAG_ALREADY_EXISTS（400）。"""
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: _make_existing_tag("80FIC10001_PV"))
        )

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tags",
                json={"tagName": "80FIC10001_PV"},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "ERR_TAG_ALREADY_EXISTS"
        # 失败路径不应提交
        assert not mock_db.commit.called

    def test_create_tag_invalid_tag_type(self, client, mock_db, fake_redis) -> None:
        """非法参数类型返回 ERR_TAG_TYPE_INVALID（400）。"""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tags",
                json={"tagName": "80FIC10002_XX", "tagType": "INVALID_TYPE"},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "ERR_TAG_TYPE_INVALID"

    def test_create_tag_invalid_measure_type(self, client, mock_db, fake_redis) -> None:
        """非法测点类型返回 ERR_MEASURE_TYPE_INVALID（400）。"""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tags",
                json={"tagName": "80FIC10003_PV", "measureType": "NOT_A_TYPE"},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "ERR_MEASURE_TYPE_INVALID"

    def test_create_tag_forbidden_for_readonly_role(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 只读角色返回 403。"""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.post(
                "/api/v1/tags",
                json={"tagName": "80FIC10004_PV"},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 403

    def test_create_tag_chinese_measure_type_normalized(self, client, mock_db, fake_redis) -> None:
        """中文测点类型（温度）归一化为 TEMPERATURE 落库。"""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tags",
                json={"tagName": "80TI10005_PV", "measureType": "温度"},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["measureType"] == "TEMPERATURE"

    def test_create_tag_empty_name_rejected_by_schema(self, client, mock_db, fake_redis) -> None:
        """空位号被 schema pattern 拦截（422）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/tags",
                json={"tagName": ""},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 422
