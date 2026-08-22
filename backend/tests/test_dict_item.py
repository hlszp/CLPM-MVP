"""通用字典项（sys_dict_item）测试。

覆盖：
- normalize_by_dict：中文 label → code；code 大小写不敏感；无效值 None（fallback 路径）
- GET /dicts/{type}/items：登录可读，字典不可用时回退出厂默认
- POST /dicts/items：ADMIN 创建成功 / code 重复 400 / 非 ADMIN 403
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.dict_item import (
    DICT_MEASURE_TYPE,
    DICT_TAG_TYPE,
    get_dict_items,
    normalize_by_dict,
)
from tests.conftest import TEST_USERS, mock_current_user


def _raiseing_execute(*_args, **_kwargs):
    raise RuntimeError("mock db unavailable")


class TestNormalizeByDict:
    """normalize_by_dict 归一化（mock DB 异常 → 出厂默认 fallback）。"""

    async def test_chinese_label_to_code(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        assert await normalize_by_dict(mock_db, DICT_MEASURE_TYPE, "温度") == "TEMPERATURE"

    async def test_code_case_insensitive(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        assert await normalize_by_dict(mock_db, DICT_MEASURE_TYPE, "flow") == "FLOW"

    async def test_unknown_value_returns_none(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        assert await normalize_by_dict(mock_db, DICT_MEASURE_TYPE, "浓度") is None

    async def test_fallback_has_seven_types(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        items = await get_dict_items(mock_db, DICT_MEASURE_TYPE)
        assert len(items) == 7
        assert ("TEMPERATURE", "温度") in items


class TestTagTypeNormalize:
    """TAG_TYPE 参数类型归一化（mock DB 异常 → 出厂默认 fallback）。"""

    async def test_chinese_label_to_code(self, mock_db) -> None:
        """中文「测量值」→ PV（测点导入 Excel 中文列场景）。"""
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        assert await normalize_by_dict(mock_db, DICT_TAG_TYPE, "测量值") == "PV"

    async def test_pid_label_to_code(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        assert await normalize_by_dict(mock_db, DICT_TAG_TYPE, "比例（P）") == "PID_P"

    async def test_code_case_insensitive(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        assert await normalize_by_dict(mock_db, DICT_TAG_TYPE, "sp") == "SP"

    async def test_unknown_value_returns_none(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        assert await normalize_by_dict(mock_db, DICT_TAG_TYPE, "未知角色") is None

    async def test_fallback_has_eight_types(self, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        items = await get_dict_items(mock_db, DICT_TAG_TYPE)
        assert len(items) == 8
        assert ("PV", "测量值") in items


class TestDictItemsEndpoint:
    """GET /dicts/{dictType}/items 端点测试。"""

    def test_list_items_login_required(self, client) -> None:
        """未认证返回 401。"""
        resp = client.get(f"/api/v1/dicts/{DICT_MEASURE_TYPE}/items")
        assert resp.status_code == 401

    def test_list_items_fallback(self, client, mock_db, fake_redis) -> None:
        """字典不可用时返回出厂默认 7 项（登录可读）。"""
        mock_db.execute = AsyncMock(side_effect=_raiseing_execute)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/dicts/{DICT_MEASURE_TYPE}/items",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 7
        labels = {i["itemLabel"] for i in data}
        assert "温度" in labels


class TestDictItemCrud:
    """POST /dicts/items 端点测试。"""

    def test_create_item_admin_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 新建字典项成功（无重复 code）。"""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/dicts/items",
                json={
                    "dictType": DICT_MEASURE_TYPE,
                    "itemCode": "CONCENTRATION",
                    "itemLabel": "浓度",
                    "sortOrder": 80,
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["itemCode"] == "CONCENTRATION"
        assert body["data"]["itemLabel"] == "浓度"
        assert mock_db.commit.called

    def test_create_item_duplicate_code_rejected(self, client, mock_db, fake_redis) -> None:
        """同字典下 code 重复返回 400。"""
        existing = MagicMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: existing))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/dicts/items",
                json={
                    "dictType": DICT_MEASURE_TYPE,
                    "itemCode": "TEMPERATURE",
                    "itemLabel": "温度2",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_DICT_ITEM_DUPLICATED"
        assert not mock_db.commit.called

    def test_create_item_non_admin_forbidden(self, client, mock_db, fake_redis) -> None:
        """非 ADMIN 返回 403。"""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.post(
                "/api/v1/dicts/items",
                json={
                    "dictType": DICT_MEASURE_TYPE,
                    "itemCode": "X",
                    "itemLabel": "X",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
