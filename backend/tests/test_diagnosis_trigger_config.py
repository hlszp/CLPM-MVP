"""诊断触发条件配置接口测试（整改计划 C6）.

覆盖：
- GET 默认值（未配置时返回 score_threshold=60 等 4 项默认）
- PUT → GET 往返：存储 + 运行时缓存刷新 + 审计日志
- 二次 PUT 走 update 分支
- 越界校验拒绝（score_threshold>100、concurrency<1、min_data_points<8）
- 权限：非 ADMIN 拒绝 PUT；IC_ENGINEER / PE_ENGINEER 可读 GET

存储走 sys_config JSON（diagnosis_trigger.current），测试用内存 store 模拟。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.audit import SysAuditLog
from app.models.sys_config import SysConfig
from app.services import diagnosis_trigger_config as svc
from tests.conftest import TEST_USERS, mock_current_user

pytestmark = pytest.mark.skip(reason="MVP: diagnosis/tuning/AAS/tracker module disabled")

# ---------------------------------------------------------------------------
# 运行时缓存隔离：每个用例结束后重置缓存为默认值
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_trigger_cache():
    yield
    svc.apply_runtime(None)


# ---------------------------------------------------------------------------
# 服务层：parse_stored / apply_runtime / get_trigger_config
# ---------------------------------------------------------------------------


class TestTriggerConfigService:
    """服务层解析与应用逻辑。"""

    def test_parse_stored_none_when_empty(self) -> None:
        assert svc.parse_stored(None) is None
        assert svc.parse_stored("") is None

    def test_parse_stored_none_when_corrupt_json(self) -> None:
        assert svc.parse_stored("{bad json") is None

    def test_parse_stored_none_when_not_object(self) -> None:
        assert svc.parse_stored("[1, 2, 3]") is None

    def test_apply_runtime_uses_defaults_when_none(self) -> None:
        svc.apply_runtime(None)
        cfg = svc.get_trigger_config()
        assert cfg.score_threshold == 60.0
        assert cfg.concurrency == 5
        assert cfg.min_data_points == 32
        assert cfg.checkup_enabled is True

    def test_apply_runtime_partial_uses_defaults_for_missing(self) -> None:
        svc.apply_runtime({"scoreThreshold": 75})
        cfg = svc.get_trigger_config()
        assert cfg.score_threshold == 75.0
        assert cfg.concurrency == 5  # 缺失回落默认
        assert cfg.min_data_points == 32
        assert cfg.checkup_enabled is True

    def test_apply_runtime_full_config(self) -> None:
        svc.apply_runtime(
            {
                "scoreThreshold": 70,
                "concurrency": 10,
                "minDataPoints": 64,
                "checkupEnabled": False,
                "updatedAt": "2026-07-22T00:00:00Z",
                "updatedBy": "admin",
            }
        )
        cfg = svc.get_trigger_config()
        assert cfg.score_threshold == 70.0
        assert cfg.concurrency == 10
        assert cfg.min_data_points == 64
        assert cfg.checkup_enabled is False
        assert cfg.updated_by == "admin"

    def test_apply_runtime_falls_back_on_bad_types(self) -> None:
        svc.apply_runtime({"scoreThreshold": "not-a-number"})
        cfg = svc.get_trigger_config()
        # 损坏时回落默认
        assert cfg.score_threshold == 60.0

    def test_build_stored_payload_camel_case(self) -> None:
        from app.schemas.config import DiagnosisTriggerSaveRequest

        req = DiagnosisTriggerSaveRequest(
            score_threshold=65,
            concurrency=8,
            min_data_points=48,
            checkup_enabled=False,
        )
        payload = svc.build_stored_payload(req, "admin")
        assert payload["scoreThreshold"] == 65
        assert payload["concurrency"] == 8
        assert payload["minDataPoints"] == 48
        assert payload["checkupEnabled"] is False
        assert payload["updatedBy"] == "admin"
        assert "updatedAt" in payload


# ---------------------------------------------------------------------------
# API 往返（内存 store 模拟 sys_config）
# ---------------------------------------------------------------------------


class _CfgRow:
    """模拟 ORM 行：value setter 写回 store（模拟脏检查 flush）。"""

    def __init__(self, store: dict, key: str) -> None:
        self._store = store
        self.key = key
        self.description: str | None = None
        self.updated_by: str | None = None
        self.updated_at: object | None = None

    @property
    def value(self):
        return self._store.get(self.key)

    @value.setter
    def value(self, v):
        self._store[self.key] = v


@pytest.fixture
def sys_config_store() -> dict:
    return {}


@pytest.fixture
def bound_store(mock_db, sys_config_store):
    """将 mock_db 的 execute/add 绑定到内存 sys_config store，返回 add 调用记录。"""
    store = sys_config_store
    added: list = []

    async def execute_side_effect(stmt, *args, **kwargs):
        key = svc.SYS_CONFIG_KEY
        row = _CfgRow(store, key) if key in store else None
        result = MagicMock()
        result.scalar_one_or_none.return_value = row
        return result

    def add_side_effect(obj):
        if isinstance(obj, SysConfig):
            store[obj.key] = obj.value or ""
        added.append(obj)

    mock_db.execute = AsyncMock(side_effect=execute_side_effect)
    mock_db.add = MagicMock(side_effect=add_side_effect)
    return added


_URL = "/api/v1/configs/diagnosis-trigger"
_HEADERS = {"Authorization": "Bearer fake-token"}


class TestDiagnosisTriggerApi:
    """GET/PUT /configs/diagnosis-trigger 端点测试。"""

    def test_get_defaults_when_unconfigured(self, client, mock_db, bound_store) -> None:
        """未配置时 GET 返回默认值。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(_URL, headers=_HEADERS)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scoreThreshold"] == 60
        assert data["concurrency"] == 5
        assert data["minDataPoints"] == 32
        assert data["checkupEnabled"] is True
        assert data["updatedAt"] is None

    def test_put_get_roundtrip(self, client, mock_db, bound_store, sys_config_store) -> None:
        """PUT 覆盖 → GET 反映配置 + 运行时缓存生效 + 审计写入。"""
        payload = {
            "scoreThreshold": 70,
            "concurrency": 10,
            "minDataPoints": 48,
            "checkupEnabled": False,
        }
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(_URL, json=payload, headers=_HEADERS)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["scoreThreshold"] == 70
        assert data["concurrency"] == 10
        assert data["minDataPoints"] == 48
        assert data["checkupEnabled"] is False
        assert data["updatedBy"] == "admin"
        assert data["updatedAt"]

        # 运行时进程内缓存已刷新（热路径立即生效）
        cfg = svc.get_trigger_config()
        assert cfg.score_threshold == 70.0
        assert cfg.concurrency == 10
        assert cfg.min_data_points == 48
        assert cfg.checkup_enabled is False

        # sys_config 已写入
        stored = json.loads(sys_config_store[svc.SYS_CONFIG_KEY])
        assert stored["scoreThreshold"] == 70
        assert stored["concurrency"] == 10
        assert stored["minDataPoints"] == 48
        assert stored["checkupEnabled"] is False
        assert stored["updatedBy"] == "admin"

        # 审计日志已写入
        audits = [o for o in bound_store if isinstance(o, SysAuditLog)]
        assert len(audits) == 1
        assert audits[0].operation_type == "DIAGNOSIS_TRIGGER_UPDATE"
        assert audits[0].target_id == svc.SYS_CONFIG_KEY
        assert audits[0].operator == "admin"

        # 再次 GET：从 store 读出，与 PUT 响应一致
        with mock_current_user(TEST_USERS["admin"]):
            resp2 = client.get(_URL, headers=_HEADERS)
        assert resp2.status_code == 200
        data2 = resp2.json()["data"]
        assert data2["scoreThreshold"] == 70
        assert data2["concurrency"] == 10
        assert data2["checkupEnabled"] is False

    def test_put_twice_update_branch(self, client, mock_db, bound_store, sys_config_store) -> None:
        """第二次 PUT 走 update 分支（已有 sys_config 行）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp1 = client.put(
                _URL,
                json={
                    "scoreThreshold": 65,
                    "concurrency": 5,
                    "minDataPoints": 32,
                    "checkupEnabled": True,
                },
                headers=_HEADERS,
            )
            assert resp1.status_code == 200
            resp2 = client.put(
                _URL,
                json={
                    "scoreThreshold": 80,
                    "concurrency": 8,
                    "minDataPoints": 64,
                    "checkupEnabled": False,
                },
                headers=_HEADERS,
            )
        assert resp2.status_code == 200
        stored = json.loads(sys_config_store[svc.SYS_CONFIG_KEY])
        assert stored["scoreThreshold"] == 80
        assert stored["concurrency"] == 8
        assert stored["minDataPoints"] == 64
        assert stored["checkupEnabled"] is False

        # 运行时缓存反映最新值
        cfg = svc.get_trigger_config()
        assert cfg.score_threshold == 80.0
        assert cfg.concurrency == 8
        assert cfg.checkup_enabled is False

    @pytest.mark.parametrize(
        "payload",
        [
            {"scoreThreshold": 101, "concurrency": 5, "minDataPoints": 32, "checkupEnabled": True},
            {"scoreThreshold": -1, "concurrency": 5, "minDataPoints": 32, "checkupEnabled": True},
            {"scoreThreshold": 60, "concurrency": 0, "minDataPoints": 32, "checkupEnabled": True},
            {"scoreThreshold": 60, "concurrency": 51, "minDataPoints": 32, "checkupEnabled": True},
            {"scoreThreshold": 60, "concurrency": 5, "minDataPoints": 7, "checkupEnabled": True},
        ],
    )
    def test_put_validation_rejected(
        self, client, mock_db, bound_store, sys_config_store, payload
    ) -> None:
        """越界输入被 422 拒绝，且不写入 store。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(_URL, json=payload, headers=_HEADERS)
        assert resp.status_code == 422
        assert svc.SYS_CONFIG_KEY not in sys_config_store

    def test_put_forbidden_for_non_admin(self, client, mock_db, bound_store) -> None:
        """非 ADMIN 角色 PUT 返回 403。"""
        payload = {
            "scoreThreshold": 70,
            "concurrency": 5,
            "minDataPoints": 32,
            "checkupEnabled": True,
        }
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(_URL, json=payload, headers=_HEADERS)
        assert resp.status_code == 403

    def test_get_allowed_for_engineer(self, client, mock_db, bound_store) -> None:
        """IC_ENGINEER / PE_ENGINEER 可读 GET。"""
        for username in ("ic_engineer", "pe_engineer"):
            with mock_current_user(TEST_USERS[username]):
                resp = client.get(_URL, headers=_HEADERS)
            assert resp.status_code == 200
