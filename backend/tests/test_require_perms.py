"""P2 D5：服务端权限码落地（require_perms）测试。

背景：ROLE_PERMISSIONS（services/auth.py）此前只在登录响应下发给前端，
服务端 0 处校验，读端点一律 get_current_user 放行，SPONSOR/EXPERT 可读
全部回路详情/任务列表/诊断数据。修复后 deps.require_perms 复用同一映射 +
通配匹配做服务端校验（与前端 v-permission 口径一致，ADMIN "*" 全通）。

覆盖：
- _perm_matches / has_perms 通配匹配规则（* / 模块:* / 精确匹配）
- 敏感读端点门控（先读端点后写端点、先敏感后全面）：
  - SPONSOR 访问回路列表/详情/Tag 关联、整定任务列表被拒（403）
  - PE_ENGINEER 访问整定任务列表被拒（403，无 tuning 权限码）
  - ADMIN 全通不被误伤（E2E 以 admin 登录）
  - PE_ENGINEER（loop:view）可读回路列表；IC_ENGINEER（tuning:*）可读整定任务
  - SPONSOR/EXPERT（diagnosis:view）仍可读诊断列表（汇总视图口径）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.api.deps import _perm_matches, has_perms
from tests.conftest import TEST_USERS, mock_current_user

# 测试用 loop_id（合法 UUID 字符串）
_LOOP_ID = "00000000-0000-0000-0000-000000000201"

_AUTH = {"Authorization": "Bearer fake-token"}


# ===========================================================================
# 通配匹配单元测试
# ===========================================================================


class TestPermMatches:
    """_perm_matches：'*' 全通 / '模块:*' 模块通配 / 精确匹配。"""

    def test_star_matches_everything(self) -> None:
        assert _perm_matches("*", "loop:view")
        assert _perm_matches("*", "anything:at-all")

    def test_module_wildcard_matches_same_module(self) -> None:
        assert _perm_matches("loop:*", "loop:view")
        assert _perm_matches("loop:*", "loop:delete")
        assert _perm_matches("tracker:*", "tracker:review")

    def test_module_wildcard_does_not_cross_module(self) -> None:
        assert not _perm_matches("loop:*", "metric:view")
        assert not _perm_matches("tracker:*", "loop:view")

    def test_exact_match(self) -> None:
        assert _perm_matches("loop:view", "loop:view")
        assert not _perm_matches("loop:view", "loop:edit")

    def test_required_code_is_not_wildcard_expanded(self) -> None:
        # 授权为具体码时，不反向通配
        assert not _perm_matches("loop:view", "loop:*")


class TestHasPerms:
    """has_perms：基于 ROLE_PERMISSIONS 的角色判定。"""

    def test_admin_all_pass(self) -> None:
        assert has_perms("ADMIN", "loop:view", "tuning:view", "diagnosis:view")

    def test_sponsor_loop_denied_diagnosis_allowed(self) -> None:
        assert not has_perms("SPONSOR", "loop:view")
        assert not has_perms("SPONSOR", "tuning:view")
        assert has_perms("SPONSOR", "diagnosis:view")
        assert has_perms("SPONSOR", "metric:view")

    def test_pe_engineer_loop_view_allowed_tuning_denied(self) -> None:
        assert has_perms("PE_ENGINEER", "loop:view")
        assert not has_perms("PE_ENGINEER", "tuning:view")

    def test_ic_engineer_module_wildcards(self) -> None:
        assert has_perms("IC_ENGINEER", "loop:view", "loop:import", "tuning:view")

    def test_expert_diagnosis_allowed(self) -> None:
        assert has_perms("EXPERT", "diagnosis:view")
        assert has_perms("EXPERT", "tracker:review")
        # 实现契约 §5：EXPERT 可查看整定相关页面
        assert has_perms("EXPERT", "tuning:view")
        assert not has_perms("EXPERT", "loop:view")

    def test_unknown_role_denied(self) -> None:
        assert not has_perms("NO_SUCH_ROLE", "loop:view")

    def test_multiple_codes_require_all(self) -> None:
        assert not has_perms("SPONSOR", "diagnosis:view", "loop:view")


# ===========================================================================
# 回路读端点门控（loop:view）
# ===========================================================================


class TestLoopReadGuard:
    """SPONSOR 无 loop:view → 403；ADMIN/PE_ENGINEER 正常。"""

    def test_sponsor_list_loops_forbidden(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get("/api/v1/loops", headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_loop_detail_forbidden(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(f"/api/v1/loops/{_LOOP_ID}", headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_loop_tags_forbidden(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(f"/api/v1/loops/{_LOOP_ID}/tags", headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_loop_monitor_list_forbidden(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get("/api/v1/loops/monitor", headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_admin_list_loops_allowed(self, client, mock_db, fake_redis) -> None:
        """ADMIN "*" 全通（E2E 以 admin 登录，不得误伤）。"""
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.loops.list_loops",
                new=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "pageSize": 20}),
            ),
        ):
            resp = client.get("/api/v1/loops", headers=_AUTH)
        assert resp.status_code == 200, f"ADMIN 读回路列表被误拦: {resp.json()}"

    def test_pe_engineer_list_loops_allowed(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 持有 loop:view，可读回路列表。"""
        with (
            mock_current_user(TEST_USERS["pe_engineer"]),
            patch(
                "app.api.v1.endpoints.loops.list_loops",
                new=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "pageSize": 20}),
            ),
        ):
            resp = client.get("/api/v1/loops", headers=_AUTH)
        assert resp.status_code == 200, f"PE_ENGINEER 读回路列表被误拦: {resp.json()}"

    def test_admin_loop_detail_allowed(self, client, mock_db, fake_redis) -> None:
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.loops.get_loop_detail",
                new=AsyncMock(return_value={"basicInfo": {}}),
            ),
        ):
            resp = client.get(f"/api/v1/loops/{_LOOP_ID}", headers=_AUTH)
        assert resp.status_code == 200, f"ADMIN 读回路详情被误拦: {resp.json()}"


# ===========================================================================
# 整定读端点门控（tuning:view）
# ===========================================================================


class TestTuningReadGuard:
    """SPONSOR/PE_ENGINEER 无 tuning 权限码 → 403；ADMIN/IC_ENGINEER 正常。"""

    def test_sponsor_tuning_tasks_forbidden(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get("/api/v1/tuning/tasks", headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_pe_engineer_tuning_tasks_forbidden(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.get("/api/v1/tuning/tasks", headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_tuning_history_forbidden(self, client, mock_db, fake_redis) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get("/api/v1/tuning/history", headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_ic_engineer_tuning_tasks_allowed(self, client, mock_db, fake_redis) -> None:
        """IC_ENGINEER 持有 tuning:*（通配覆盖 tuning:view）。"""
        with (
            mock_current_user(TEST_USERS["ic_engineer"]),
            patch(
                "app.api.v1.endpoints.tuning.list_tuning_tasks",
                new=AsyncMock(return_value={"items": [], "total": 0}),
            ),
        ):
            resp = client.get("/api/v1/tuning/tasks", headers=_AUTH)
        assert resp.status_code == 200, f"IC_ENGINEER 读整定任务被误拦: {resp.json()}"

    def test_expert_tuning_tasks_allowed(self, client, mock_db, fake_redis) -> None:
        """EXPERT 持有 tuning:view（契约 §5：可查看整定相关页面）。"""
        with (
            mock_current_user(TEST_USERS["expert"]),
            patch(
                "app.api.v1.endpoints.tuning.list_tuning_tasks",
                new=AsyncMock(return_value={"items": [], "total": 0}),
            ),
        ):
            resp = client.get("/api/v1/tuning/tasks", headers=_AUTH)
        assert resp.status_code == 200, f"EXPERT 读整定任务被误拦: {resp.json()}"

    def test_admin_tuning_tasks_allowed(self, client, mock_db, fake_redis) -> None:
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.tuning.list_tuning_tasks",
                new=AsyncMock(return_value={"items": [], "total": 0}),
            ),
        ):
            resp = client.get("/api/v1/tuning/tasks", headers=_AUTH)
        assert resp.status_code == 200, f"ADMIN 读整定任务被误拦: {resp.json()}"


# ===========================================================================
# 诊断读端点门控（diagnosis:view）
# ===========================================================================


class TestDiagnosisReadGuard:
    """SPONSOR/EXPERT 持有 diagnosis:view（汇总视图口径），诊断列表放行。"""

    def test_sponsor_diagnosis_list_allowed(self, client, mock_db, fake_redis) -> None:
        with (
            mock_current_user(TEST_USERS["sponsor"]),
            patch(
                "app.api.v1.endpoints.diagnosis.list_diagnosis",
                new=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "pageSize": 20}),
            ),
        ):
            resp = client.get("/api/v1/diagnosis/list", headers=_AUTH)
        assert resp.status_code == 200, f"SPONSOR 读诊断列表被误拦: {resp.json()}"

    def test_expert_diagnosis_list_allowed(self, client, mock_db, fake_redis) -> None:
        with (
            mock_current_user(TEST_USERS["expert"]),
            patch(
                "app.api.v1.endpoints.diagnosis.list_diagnosis",
                new=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "pageSize": 20}),
            ),
        ):
            resp = client.get("/api/v1/diagnosis/list", headers=_AUTH)
        assert resp.status_code == 200, f"EXPERT 读诊断列表被误拦: {resp.json()}"

    def test_sponsor_diagnosis_tasks_allowed(self, client, mock_db, fake_redis) -> None:
        """诊断任务列表属 diagnosis:view 口径，SPONSOR 放行（与前端 v-permission 一致）。"""
        with (
            mock_current_user(TEST_USERS["sponsor"]),
            patch(
                "app.api.v1.endpoints.diagnosis.list_diagnosis_tasks",
                new=AsyncMock(return_value={"items": [], "total": 0, "page": 1, "pageSize": 20}),
            ),
        ):
            resp = client.get("/api/v1/diagnosis/tasks", headers=_AUTH)
        assert resp.status_code != 403, f"SPONSOR 读诊断任务被误拦: {resp.json()}"
