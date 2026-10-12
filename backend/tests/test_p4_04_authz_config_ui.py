"""P4-04 配置 UI / 在线变更后端支撑面测试.

- GET /auth/effective-access（01§3.5 建议接口，P4-04 在线生效轮询锚点）：
  policyRevision / 码集合（与 require_perms 同源）/ scope 视图 / 模块标注；
- allowlist 移除后 system/authorization 8 端点契约（前端消费已接通——
  见 scripts/api-contract-allowlist.json 与 check_api_contract.py 对账）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services import authz_catalog as az
from app.services.authz_runtime import get_role_codes

from .conftest import TEST_USERS, mock_current_user


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


class TestEffectiveAccessEndpoint:
    """/auth/effective-access：轮询锚 + 与实际 403 同源的有效权限解释。"""

    def test_requires_authentication(self, client) -> None:
        resp = client.get("/api/v1/auth/effective-access")
        assert resp.status_code in (401, 403)

    def test_ic_view_matches_runtime_codes_and_default_all_scope(self, client, mock_db) -> None:
        """IC 视图：码集合与 authz_runtime.get_role_codes 同源；无 scope 行按 ALL.

        CLPM_TEST_MODE 下 get_role_codes 回退 ROLE_SEED（与迁移种子逐码一致，
        P4-01 集成钉死）——解释器与 require_perms 判定走同一函数，403 一致性
        由同源性结构性保证。
        """
        import asyncio

        # mock DB：revision 查询返回 None（=0，未播种），scope 查询返回 None
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(None)])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/auth/effective-access",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["policyRevision"] == 0
        assert data["role"] == "IC_ENGINEER"
        assert data["scope"]["scopeType"] == "ALL"
        assert data["scope"]["nodeId"] is None

        # 与实际 403 判定同源：effective-access.actions == get_role_codes 闭包展开
        expected = az.get_effective_access(
            "IC_ENGINEER",
            set(asyncio.run(get_role_codes(mock_db, "IC_ENGINEER"))),
            enabled_modules=None,
        )
        # 端点带 enabled_modules（模块标注），闭包展开的动作全集应一致
        assert set(data["actions"]) == set(expected["actions"])
        # 页面按显式授权展示（不闭包展开）
        assert set(data["pages"]) == set(expected["pages"])

    def test_node_subtree_scope_echoed(self, client, mock_db) -> None:
        """NODE_SUBTREE 行回显 scopeType/nodeId（无行默认 ALL 的对侧）。"""
        scope_row = MagicMock()
        scope_row.scope_type = "NODE_SUBTREE"
        scope_row.node_id = "node-0001"
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(7), _scalar_result(scope_row)])
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.get(
                "/api/v1/auth/effective-access",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["policyRevision"] == 7
        assert data["scope"]["scopeType"] == "NODE_SUBTREE"
        assert data["scope"]["nodeId"] == "node-0001"

    def test_sponsor_zero_write_structural(self, client, mock_db) -> None:
        """SPONSOR 有效动作不含写码（零写红线在解释视图中同样成立）。"""
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(None)])
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/auth/effective-access",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        actions = set(resp.json()["data"]["actions"])
        from app.services.authz_runtime import READ_ONLY_ACTION_CODES

        write_codes = actions - READ_ONLY_ACTION_CODES
        assert not write_codes, f"SPONSOR 解释视图出现写码: {sorted(write_codes)}"

    def test_admin_pages_contain_permissions_page(self, client, mock_db) -> None:
        """ADMIN 页面码含授权配置页（page:system.permissions，protected）。"""
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(None)])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/auth/effective-access",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        pages = set(resp.json()["data"]["pages"])
        assert "page:system.permissions" in pages
        assert "page:system.users" in pages


class TestAllowlistAuthzEntriesRemoved:
    """P4-04 前端消费接通后，allowlist 的 system/authorization 8 条移除登记."""

    AUTHZ_PREFIXES = (
        "/system/authorization/catalog",
        "/system/authorization/roles",
        "/system/authorization/roles/*",
        "/system/authorization/users",
        "/system/authorization/users/*/scope",
        "/system/authorization/revisions",
        "/system/authorization/revisions/*/rollback",
    )

    def test_no_authz_entries_in_allowlist(self) -> None:
        import json
        from pathlib import Path

        allowlist = json.loads(
            (
                Path(__file__).resolve().parents[2] / "scripts" / "api-contract-allowlist.json"
            ).read_text(encoding="utf-8")
        )
        leftover = [
            r for r in allowlist.get("backendUnused", []) if r.startswith("/system/authorization")
        ]
        assert not leftover, (
            f"system/authorization 端点已由 P4-04 前端接通消费，allowlist 不应残留: {leftover}"
        )
        reasons = allowlist.get("backendUnusedReasons", {})
        stale_reasons = [
            r
            for r in reasons
            if r.startswith("/system/authorization") and r not in allowlist.get("backendUnused", [])
        ]
        assert not stale_reasons, f"原因表残留已移除条目: {stale_reasons}"
