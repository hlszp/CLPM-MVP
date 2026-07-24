"""PID 结构模板 HTTP 层接口测试 (P5).

回归背景：服务层 ``_pid_structure_to_dict`` 返回 snake_case dict，响应模型
``DcsPidStructureItem``（继承 CamelModel）字段名须保持 snake_case 才能被
``populate_by_name`` 校验通过，再由 ``alias_generator=to_camel`` 序列化为
camelCase JSON。早期版本误将字段名写成 camelCase，导致服务返回的 snake_case
dict 既不匹配字段名也不匹配别名，FastAPI 响应序列化抛 ResponseValidationError（500）。
服务层单测因直接断言 dict 而漏掉此路径，故补 HTTP 层测试防回归。

测试覆盖：
- PUT  /api/v1/dcs/models/{id}/pid-structure — upsert，响应 camelCase
- GET  /api/v1/dcs/models/{id}/pid-structure — 单条，未配置返回 null
- GET  /api/v1/dcs/pid-structures           — 列表，响应 camelCase
- DELETE /api/v1/dcs/models/{id}/pid-structure

设计依据：app/api/v1/endpoints/dcs.py、app/schemas/dcs_config.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from tests.conftest import TEST_USERS, mock_current_user

_MODEL_ID = "d2141c39-1aef-48f5-a535-94f41fc5f01b"


def _snake_case_structure() -> dict:
    """构造服务层 _pid_structure_to_dict 的真实返回形态（snake_case）。"""
    return {
        "id": "aedec52b-1c14-4cc7-b112-15e6deec3ad9",
        "dcs_model_id": _MODEL_ID,
        "model_code": "hollysys-macs",
        "model_name": "MACS 系统",
        "p_type": "PROPORTION_BAND",
        "i_unit": "MINUTES",
        "d_unit": "SECONDS",
        "d_filter_enabled": True,
        "d_filter_unit": "SECONDS",
        "d_filter_multiplier": False,
        "description": "冒烟测试",
        "created_at": "2026-07-24T13:10:31.208160",
        "updated_at": "2026-07-24T13:10:40.479341",
    }


class TestPidStructureHttpSerialization:
    """HTTP 响应序列化：snake_case 服务 dict → camelCase JSON。"""

    def test_put_upsert_returns_camel_case(self, client, mock_db, fake_redis) -> None:
        """PUT upsert 响应须为 camelCase（回归：曾因字段名 camelCase 导致 500）。"""
        with (
            patch(
                "app.api.v1.endpoints.dcs.svc_upsert_pid_structure",
                new=AsyncMock(return_value=_snake_case_structure()),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                f"/api/v1/dcs/models/{_MODEL_ID}/pid-structure",
                json={
                    "pType": "PROPORTION_BAND",
                    "iUnit": "MINUTES",
                    "dUnit": "SECONDS",
                    "dFilterEnabled": True,
                    "dFilterUnit": "SECONDS",
                    "dFilterMultiplier": False,
                    "description": "冒烟测试",
                },
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        # camelCase 别名必须存在（响应序列化走 alias_generator）
        assert data["dcsModelId"] == _MODEL_ID
        assert data["modelCode"] == "hollysys-macs"
        assert data["modelName"] == "MACS 系统"
        assert data["pType"] == "PROPORTION_BAND"
        assert data["iUnit"] == "MINUTES"
        assert data["dUnit"] == "SECONDS"
        assert data["dFilterEnabled"] is True
        assert data["dFilterUnit"] == "SECONDS"
        assert data["dFilterMultiplier"] is False
        assert data["createdAt"] == "2026-07-24T13:10:31.208160"
        assert data["updatedAt"] == "2026-07-24T13:10:40.479341"
        # snake_case 键不应出现在 JSON 输出中（仅 alias 输出）
        assert "dcs_model_id" not in data
        assert "p_type" not in data

    def test_put_accepts_camel_case_body(self, client, mock_db, fake_redis) -> None:
        """前端以 camelCase 提交，alias_generator + populate_by_name 须正确解析。"""
        captured: dict = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return _snake_case_structure()

        with (
            patch("app.api.v1.endpoints.dcs.svc_upsert_pid_structure", new=_capture),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                f"/api/v1/dcs/models/{_MODEL_ID}/pid-structure",
                json={
                    "pType": "PROPORTION_BAND",
                    "iUnit": "MINUTES",
                    "dUnit": "SECONDS",
                    "dFilterEnabled": True,
                    "dFilterUnit": "SECONDS",
                    "dFilterMultiplier": True,
                },
            )
        assert resp.status_code == 200, resp.text
        # 服务层收到的关键字参数须为 snake_case（schema 字段名）
        assert captured["p_type"] == "PROPORTION_BAND"
        assert captured["i_unit"] == "MINUTES"
        assert captured["d_unit"] == "SECONDS"
        assert captured["d_filter_enabled"] is True
        assert captured["d_filter_unit"] == "SECONDS"
        assert captured["d_filter_multiplier"] is True

    def test_get_single_returns_null_when_not_configured(self, client, mock_db, fake_redis) -> None:
        """GET 单条未配置时 data=null。"""
        with (
            patch(
                "app.api.v1.endpoints.dcs.svc_get_pid_structure",
                new=AsyncMock(return_value=None),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(f"/api/v1/dcs/models/{_MODEL_ID}/pid-structure")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] is None

    def test_get_single_returns_camel_case(self, client, mock_db, fake_redis) -> None:
        """GET 单条已配置时响应 camelCase。"""
        with (
            patch(
                "app.api.v1.endpoints.dcs.svc_get_pid_structure",
                new=AsyncMock(return_value=_snake_case_structure()),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get(f"/api/v1/dcs/models/{_MODEL_ID}/pid-structure")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["dcsModelId"] == _MODEL_ID
        assert data["pType"] == "PROPORTION_BAND"
        assert data["dFilterEnabled"] is True

    def test_list_returns_camel_case(self, client, mock_db, fake_redis) -> None:
        """GET 列表响应 camelCase。"""
        with (
            patch(
                "app.api.v1.endpoints.dcs.svc_list_pid_structures",
                new=AsyncMock(return_value=[_snake_case_structure()]),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.get("/api/v1/dcs/pid-structures")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["data"]
        assert len(rows) == 1
        assert rows[0]["dcsModelId"] == _MODEL_ID
        assert rows[0]["pType"] == "PROPORTION_BAND"

    def test_delete_returns_success(self, client, mock_db, fake_redis) -> None:
        """DELETE 调用服务并返回成功。"""
        with (
            patch(
                "app.api.v1.endpoints.dcs.svc_delete_pid_structure",
                new=AsyncMock(return_value=None),
            ),
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.delete(f"/api/v1/dcs/models/{_MODEL_ID}/pid-structure")
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["deleted"] is True

    def test_put_requires_admin(self, client, mock_db, fake_redis) -> None:
        """非 ADMIN 角色无权 upsert（403）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                f"/api/v1/dcs/models/{_MODEL_ID}/pid-structure",
                json={"pType": "PROPORTION", "iUnit": "SECONDS", "dUnit": "SECONDS"},
            )
        assert resp.status_code == 403

    def test_put_rejects_filter_enabled_without_unit(self, client, mock_db, fake_redis) -> None:
        """dFilterEnabled=True 但缺 dFilterUnit 时 422（与 DB CHECK 一致）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/dcs/models/{_MODEL_ID}/pid-structure",
                json={
                    "pType": "PROPORTION",
                    "iUnit": "SECONDS",
                    "dUnit": "SECONDS",
                    "dFilterEnabled": True,
                    "dFilterMultiplier": False,
                },
            )
        assert resp.status_code == 422
