"""P7-02 D-2 修复测试：/loops/{loop_id} 族路径参数 UUID 守卫（非法输入 400 非 500）.

E2E 报告（P7-02-E2E报告.md §6 D-2）事实：``GET /api/v1/loops/not-a-real-loop-id``
→ HTTP 500 且响应体为 Python traceback 原文（asyncpg invalid UUID → sqlalchemy
链 + 服务器文件路径），workbench360 错误块把整段堆栈渲染给用户。

修复口径：loops.py ``{loop_id}`$ 族 8 路由入口统一 ``_require_loop_uuid``
（G-N2 同形态：400 ERR_PARAM 清洁信封 + 合法 UUID 归一小写连字符形态）。
/me/ 系（personal_workspace.py）已有 ``_normalize_loop_id`` 守卫（404
ERR_LOOP_NOT_FOUND），不在本修复范围。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from tests.conftest import TEST_USERS, mock_current_user

_AUTH = {"Authorization": "Bearer fake-token"}

# 非 UUID 复现值（E2E 实证；串含连字符与数字，避免被误判为纯符号输入）
_BAD = "not-a-real-loop-id"

# {loop_id} 族全量路由（loops.py；方法/路径/可选 body）
_FAMILY = [
    ("GET", f"/api/v1/loops/{_BAD}", None),
    ("GET", f"/api/v1/loops/{_BAD}/engineering-facts", None),
    ("PUT", f"/api/v1/loops/{_BAD}", {}),
    ("DELETE", f"/api/v1/loops/{_BAD}", None),
    ("GET", f"/api/v1/loops/{_BAD}/tags", None),
    ("PUT", f"/api/v1/loops/{_BAD}/tags", {}),
    ("GET", f"/api/v1/loops/{_BAD}/monitor", None),
    ("GET", f"/api/v1/loops/{_BAD}/confidence-latest", None),
]


class TestLoopIdUuidGuard:
    def test_report_repro_get_loop_not_uuid_400_not_500(self, client, mock_db, fake_redis) -> None:
        """报告复现路径：GET /loops/not-a-real-loop-id → 400 ERR_PARAM 清洁信封.

        修复前为 500 + traceback 裸透；断言非 5xx 且错误体为 JSON code 信封。
        """
        mock_db.execute = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(f"/api/v1/loops/{_BAD}", headers=_AUTH)
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "ERR_PARAM"
        assert "UUID" in body["message"]
        # 守卫先于 DB：非法形态不得触碰数据库
        assert not mock_db.execute.await_count

    def test_whole_family_not_uuid_400_not_500(self, client, mock_db, fake_redis) -> None:
        """同族 8 路由泛化：非 UUID 一律 4xx 清洁信封，绝无 500 裸透."""
        mock_db.execute = AsyncMock()
        with mock_current_user(TEST_USERS["admin"]):
            for method, url, body in _FAMILY:
                resp = client.request(method, url, json=body, headers=_AUTH)
                assert resp.status_code == 400, (method, url, resp.status_code)
                assert resp.json()["code"] == "ERR_PARAM", (method, url)
        assert not mock_db.execute.await_count

    def test_valid_uuid_uppercase_normalized_and_passes_guard(
        self, client, mock_db, fake_redis
    ) -> None:
        """合法 UUID（含大写等形态）过守卫 → 进入业务层（守卫不拦截合法值）."""
        upper = "00000000-0000-0000-0000-00000000ABCD"
        mock_db.execute = AsyncMock(return_value=_scalar_none())
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(f"/api/v1/loops/{upper}", headers=_AUTH)
        # 守卫放行 → 进入业务层（回路不存在 → 404 ERR_LOOP_NOT_FOUND，非 400 守卫）
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"

    def test_helper_normalization_contract(self) -> None:
        from app.api.v1.endpoints.loops import _require_loop_uuid

        assert _require_loop_uuid("00000000-0000-0000-0000-00000000ABCD") == (
            "00000000-0000-0000-0000-00000000abcd"
        )
        assert _require_loop_uuid(" 00000000-0000-0000-0000-00000000abcd ") == (
            "00000000-0000-0000-0000-00000000abcd"
        )


def _scalar_none():
    """scalar_one_or_none() 返回 None 的 execute 结果 mock."""

    class _R:
        def scalar_one_or_none(self):
            return None

    return _R()
