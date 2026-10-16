"""P4-01 授权目录与持久策略：单元测试（mock DB）.

覆盖（方案 01 §3.5 / 03§7 P4-01 / 04 C23-C24；任务书【4】核验问题 2/4/5
的进程内行为面 + 核验问题 1 的代码级种子一致性证据）：

- 目录结构：无通配码、依赖指向读码、protected 集合、规模登记
- 未知码拒绝（含 ``loop:*`` 通配形态——无机械通配展开的入口防线）
- 类型不符拒绝（页面码混入 actionCodes / 动作码混入 pageCodes）
- 漏依赖拒绝（授 A 不授其前置读码 B）
- 管理员保护：protected 码从 ADMIN 移除→403（含"最后一名 ADMIN"语义：
  全体 ADMIN 共享矩阵，任何移除即全体失权）；非 ADMIN 角色不受该约束
- expectedRevision 失配 409（携带 currentRevision）；并发兜底
  IntegrityError→409
- 种子与 P1-04 裁决一致：IC 通配不展开出 loop:delete/batch-config；
  SPONSOR 零写；现行 require_perms 4 读码与 ROLE_PERMISSIONS 语义对齐；
  关键写端点角色白名单逐码对照
- module enabled / fitness 不被授权放宽：effective access 模块禁用标注；
  fitness 门禁说明在目录 description（业务层执行）
- 用户 scope 校验：NODE_SUBTREE 缺 nodeId / ALL 带 nodeId / 非法类型拒绝
- 回退校验链：SEED 不可回退、ROLLBACK 行不可再回退、未知 revision 404

真实 PG 两路径迁移一致 / 双会话并发 409 / downgrade 幂等见
tests/integration/test_p4_01_real_pg.py（integration 标记，门禁默认不跑）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.deps import has_perms
from app.core.exceptions import BizError
from app.services import authz_catalog as az
from app.services.auth import ROLE_PERMISSIONS
from tests.conftest import TEST_USERS, mock_current_user


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _grants_rows(codes: list[str]):
    result = MagicMock()
    result.fetchall.return_value = [(c,) for c in codes]
    return result


def _empty_rows():
    result = MagicMock()
    result.fetchall.return_value = []
    return result


# ---------------------------------------------------------------------------
# 目录结构
# ---------------------------------------------------------------------------


class TestCatalogStructure:
    def test_no_wildcard_codes_in_catalog(self) -> None:
        for code in az.CATALOG:
            assert "*" not in code, f"目录含通配码: {code}"

    def test_catalog_scale_registered(self) -> None:
        kinds = {d.kind for d in az.CATALOG.values()}
        assert kinds == {"PAGE_GROUP", "PAGE", "ACTION"}
        pages = sum(1 for d in az.CATALOG.values() if d.kind == "PAGE")
        groups = sum(1 for d in az.CATALOG.values() if d.kind == "PAGE_GROUP")
        actions = sum(1 for d in az.CATALOG.values() if d.kind == "ACTION")
        # 规模登记（P4-01 交接口径 + P4-02 目录增量 alert:subscription-manage
        # + P4-02 尾批 data:import + P6-06 我的工作 page:my-work 组与
        # page:my-work.main 页，2026-10-14 + P2-04 维护 UI page:config.eng-templates
        # 页，2026-10-14）：11 组 + 60 页面 + 56 动作 = 127 码
        assert (groups, pages, actions) == (11, 60, 56)
        assert len(az.CATALOG) == 127

    def test_requires_point_to_action_codes_only(self) -> None:
        for d in az.CATALOG.values():
            for req in d.requires:
                assert az.CATALOG[req].kind == "ACTION"
                assert az.CATALOG[req].requires == ()

    def test_protected_set(self) -> None:
        assert az.PROTECTED_CODES == frozenset(
            {
                "page:system.users",
                "page:system.audit",
                "page:system.permissions",
                "page:system.modules",
                "system:user-manage",
                "system:authorization-manage",
                "system:module-manage",
                "system:audit-view",
            }
        )

    def test_module_keys_valid(self) -> None:
        valid = {
            "monitor",
            "assess",
            "diagnosis",
            "tuning",
            "handling",
            "reports",
            "config",
            "system",
            None,
        }
        for code, d in az.CATALOG.items():
            assert d.module_key in valid, f"{code} 非法 module_key={d.module_key}"


# ---------------------------------------------------------------------------
# 核验问题 2：未知码/漏依赖/无通配展开
# ---------------------------------------------------------------------------


class TestUnknownAndWildcardRejection:
    def test_unknown_code_rejected(self) -> None:
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("IC_ENGINEER", ["page:monitor.loops"], ["tuning:nonexistent"])
        assert ei.value.code == "ERR_AUTHZ_UNKNOWN_CODE"
        assert ei.value.status_code == 400

    def test_wildcard_code_rejected_no_mechanical_expansion(self) -> None:
        """``loop:*`` 型通配码不在目录中——写入即未知码拒绝，无机械展开。"""
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("IC_ENGINEER", [], ["loop:*"])
        assert ei.value.code == "ERR_AUTHZ_UNKNOWN_CODE"
        assert "loop:*" in ei.value.message

    def test_global_star_rejected(self) -> None:
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("ADMIN", [], ["*"])
        assert ei.value.code == "ERR_AUTHZ_UNKNOWN_CODE"

    def test_kind_mismatch_rejected(self) -> None:
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("IC_ENGINEER", ["tuning:identify"], [])
        assert ei.value.code == "ERR_AUTHZ_CODE_KIND_MISMATCH"

    def test_kind_mismatch_action_in_pages_rejected(self) -> None:
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("IC_ENGINEER", ["page:monitor.loops"], ["page:tuning.records"])
        assert ei.value.code == "ERR_AUTHZ_CODE_KIND_MISMATCH"


class TestMissingDependencyRejection:
    def test_action_without_read_dependency_rejected(self) -> None:
        """授"发起整定"不授"读整定"→拒绝。"""
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("EXPERT", ["page:tuning.overview"], ["tuning:identify"])
        assert ei.value.code == "ERR_AUTHZ_MISSING_DEPENDENCY"
        assert "tuning:view" in ei.value.message

    def test_loop_delete_requires_loop_view(self) -> None:
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("ADMIN", [], ["loop:delete"])
        assert ei.value.code == "ERR_AUTHZ_MISSING_DEPENDENCY"

    def test_seed_grants_pass_dependency_closure(self) -> None:
        for role, codes in az.ROLE_SEED.items():
            assert az.validate_grants(role, codes) == {}


# ---------------------------------------------------------------------------
# 核验问题 4：管理员保护
# ---------------------------------------------------------------------------


class TestAdminProtection:
    def test_removing_protected_from_admin_rejected(self) -> None:
        """最后一名 ADMIN 失权保护：protected 码移除即全体 ADMIN 失权，任何移除拒绝。"""
        codes = set(az.ROLE_SEED["ADMIN"]) - {"system:user-manage"}
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix(
                "ADMIN",
                sorted(c for c in codes if az.CATALOG[c].kind != "ACTION"),
                sorted(c for c in codes if az.CATALOG[c].kind == "ACTION"),
            )
        assert ei.value.code == "ERR_AUTHZ_ADMIN_PROTECTED"
        assert ei.value.status_code == 403
        assert "system:user-manage" in ei.value.message
        assert "最后一名管理员失权保护" in ei.value.message

    def test_removing_protected_page_from_admin_rejected(self) -> None:
        codes = set(az.ROLE_SEED["ADMIN"]) - {"page:system.permissions"}
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix(
                "ADMIN",
                sorted(c for c in codes if az.CATALOG[c].kind != "ACTION"),
                sorted(c for c in codes if az.CATALOG[c].kind == "ACTION"),
            )
        assert ei.value.code == "ERR_AUTHZ_ADMIN_PROTECTED"

    def test_non_admin_role_unaffected_by_protection(self) -> None:
        merged = az.validate_role_matrix("SPONSOR", [], [])
        assert merged == frozenset()

    def test_unknown_role_404(self) -> None:
        with pytest.raises(BizError) as ei:
            az.validate_role_matrix("SUPER_ADMIN", [], [])
        assert ei.value.code == "ERR_AUTHZ_ROLE_NOT_FOUND"
        assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# expectedRevision 乐观锁（409）
# ---------------------------------------------------------------------------


class TestRevisionConflict:
    def _db(self, current: int) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(current)])
        return db

    def test_stale_revision_409_with_current(self) -> None:
        import asyncio

        db = self._db(7)
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.update_role_grants(
                    db,
                    role="SPONSOR",
                    expected_revision=6,
                    page_codes=["page:monitor.loops"],
                    action_codes=[],
                    reason="r",
                    operator="admin",
                )
            )
        assert ei.value.code == "ERR_AUTHZ_REVISION_CONFLICT"
        assert ei.value.status_code == 409
        assert ei.value.data == {"currentRevision": 7}

    def test_integrity_error_mapped_to_409(self) -> None:
        """并发兜底：commit 阶段 revision 主键冲突（另一方已提交）→409。"""
        import asyncio

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(3),  # lock_guard_for_update（P4-03 起替代裸读 revision）
                _grants_rows(["loop:view"]),  # get_role_grants before
                None,  # delete
                None,  # insert
                None,  # set_guard_revision UPDATE（P4-03）
            ]
        )
        db.commit = AsyncMock(
            side_effect=IntegrityError("stmt", {}, Exception("duplicate key revision"))
        )
        db.rollback = AsyncMock()
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.update_role_grants(
                    db,
                    role="SPONSOR",
                    expected_revision=3,
                    page_codes=[],
                    action_codes=["loop:view"],
                    reason="r",
                    operator="admin",
                )
            )
        assert ei.value.code == "ERR_AUTHZ_REVISION_CONFLICT"


# ---------------------------------------------------------------------------
# 核验问题 1（代码级证据）：种子矩阵与 P1-04 裁决一致
# ---------------------------------------------------------------------------


class TestSeedParityWithP1_04:
    """P1-04（2026-10-11 AUTH-02 止血后）实际裁决 vs 新目录种子。"""

    def test_ic_wildcard_not_mechanically_expanded(self) -> None:
        """IC 历史 ``loop:*`` 不机械展开：无 loop:delete / loop:batch-config。"""
        ic = az.ROLE_SEED["IC_ENGINEER"]
        assert "loop:delete" not in ic
        assert "loop:batch-config" not in ic
        # 按真实 require_roles 白名单展开的显式码
        for code in (
            "loop:view",
            "loop:create",
            "loop:edit",
            "loop:export",
            "loop:import",
            "loop:batch-grouping",
        ):
            assert code in ic

    def test_sponsor_zero_write(self) -> None:
        actions = {c for c in az.ROLE_SEED["SPONSOR"] if az.CATALOG[c].kind == "ACTION"}
        assert actions == set(ROLE_PERMISSIONS["SPONSOR"])
        assert all(c.endswith(":view") for c in actions)

    def test_require_perms_codes_parity(self) -> None:
        """现行活链 4 读码（loop/tuning/diagnosis/alert:view）：has_perms
        （ROLE_PERMISSIONS 通配语义）与新目录种子逐角色对齐。"""
        for code in ("loop:view", "tuning:view", "diagnosis:view", "alert:view"):
            for role in az.VALID_ROLES:
                in_seed = code in az.ROLE_SEED[role]
                assert has_perms(role, code) == in_seed, (
                    f"{role} x {code}: 旧链={has_perms(role, code)} 新目录={in_seed}"
                )

    def test_loop_delete_admin_only(self) -> None:
        holders = {r for r in az.VALID_ROLES if "loop:delete" in az.ROLE_SEED[r]}
        assert holders == {"ADMIN"}  # loops.py DELETE /{loop_id} require_roles("ADMIN")

    def test_tuning_advanced_admin_expert_only(self) -> None:
        holders = {r for r in az.VALID_ROLES if "tuning:advanced" in az.ROLE_SEED[r]}
        assert holders == {"ADMIN", "EXPERT"}  # P1-04/AUTH-06 _TUNING_ADVANCED_PARAM_ROLES

    def test_diagnosis_trigger_four_roles(self) -> None:
        holders = {r for r in az.VALID_ROLES if "diagnosis:trigger" in az.ROLE_SEED[r]}
        assert holders == {
            "ADMIN",
            "IC_ENGINEER",
            "PE_ENGINEER",
            "EXPERT",
        }  # _DIAGNOSIS_TRIGGER_ROLES

    def test_handling_operate_three_roles(self) -> None:
        holders = {r for r in az.VALID_ROLES if "handling:operate" in az.ROLE_SEED[r]}
        assert holders == {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"}  # _HANDLING_ROLES

    def test_metric_task_roles(self) -> None:
        holders = {r for r in az.VALID_ROLES if "metric:task-run" in az.ROLE_SEED[r]}
        assert holders == {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"}  # _TASK_CREATOR_ROLES

    def test_config_publish_admin_only(self) -> None:
        holders = {r for r in az.VALID_ROLES if "config:publish" in az.ROLE_SEED[r]}
        assert holders == {"ADMIN"}  # DEC-03

    def test_admin_seed_is_explicit_full_catalog(self) -> None:
        expected = frozenset(c for c in az.CATALOG if az.CATALOG[c].kind != "PAGE_GROUP")
        assert az.ROLE_SEED["ADMIN"] == expected  # 显式全码，无 ``*``

    def test_expert_has_workbench_pe_no_tuning_advanced(self) -> None:
        assert "page:workbench.main" in az.ROLE_SEED["EXPERT"]
        assert "tuning:advanced" not in az.ROLE_SEED["PE_ENGINEER"]
        assert "tuning:advanced" in az.ROLE_SEED["EXPERT"]

    def test_sponsor_pages_match_frontend_authority(self) -> None:
        """SPONSOR 页面种子对照前端 authority（无工作台/实时表格/评估任务/记录/任务详情）。"""
        s = az.ROLE_SEED["SPONSOR"]
        assert "page:workbench.main" not in s
        assert "page:monitor.table" not in s
        assert "page:assess.tasks" not in s
        assert "page:assess.history" not in s
        assert "page:task.detail" not in s
        assert "page:reports.benefit" in s


# ---------------------------------------------------------------------------
# 核验问题 5：module enabled / fitness 不被授权放宽
# ---------------------------------------------------------------------------


class TestModuleAndFitnessGateNotRelaxed:
    def test_module_disabled_marks_codes_unavailable(self) -> None:
        """模块禁用时，即使角色持有授权码也标记不可用（授权只收紧不放宽）。"""
        access = az.get_effective_access(
            "IC_ENGINEER",
            set(az.ROLE_SEED["IC_ENGINEER"]),
            enabled_modules={
                "monitor",
                "assess",
                "config",
                "system",
                "reports",
            },  # diagnosis/tuning/handling 禁用
        )
        assert "diagnosis:trigger" in access["unavailableByModule"]["actions"]
        assert "tuning:identify" in access["unavailableByModule"]["actions"]
        assert "handling:operate" in access["unavailableByModule"]["actions"]
        assert "loop:view" in access["actions"]  # monitor 启用
        assert "diagnosis:trigger" in access["actions"]  # 仍在授权集合（AND 语义在运行时）

    def test_effective_access_dependency_closure(self) -> None:
        """依赖闭包展开：授 tuning:identify 自动补齐展示层读码 tuning:view。"""
        access = az.get_effective_access("EXPERT", {"tuning:identify"}, enabled_modules=None)
        assert "tuning:identify" in access["actions"]
        assert "tuning:view" in access["actions"]

    def test_fitness_gate_documented_not_relaxed(self) -> None:
        """fitness L0/L1 门禁说明钉在目录 description（业务层执行，授权不放宽）。"""
        assert "fitness" in az.CATALOG["tuning:advanced"].description


# ---------------------------------------------------------------------------
# 用户 scope 校验
# ---------------------------------------------------------------------------


class TestUserScopeValidation:
    def _user_row(self, exists: bool = True):
        result = MagicMock()
        user = MagicMock() if exists else None
        result.scalar_one_or_none.return_value = user
        return result

    def test_invalid_scope_type_rejected(self) -> None:
        import asyncio

        db = AsyncMock()
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.update_user_scope(
                    db,
                    user_id="u1",
                    expected_revision=1,
                    scope_type="FACTORY",
                    node_id=None,
                    reason="r",
                    operator="admin",
                )
            )
        assert ei.value.code == "ERR_AUTHZ_SCOPE_INVALID"

    def test_all_with_node_rejected(self) -> None:
        import asyncio

        db = AsyncMock()
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.update_user_scope(
                    db,
                    user_id="u1",
                    expected_revision=1,
                    scope_type="ALL",
                    node_id="n1",
                    reason="r",
                    operator="admin",
                )
            )
        assert ei.value.code == "ERR_AUTHZ_SCOPE_INVALID"

    def test_subtree_without_node_rejected(self) -> None:
        import asyncio

        db = AsyncMock()
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.update_user_scope(
                    db,
                    user_id="u1",
                    expected_revision=1,
                    scope_type="NODE_SUBTREE",
                    node_id=None,
                    reason="r",
                    operator="admin",
                )
            )
        assert ei.value.code == "ERR_AUTHZ_SCOPE_INVALID"

    def test_unknown_user_404(self) -> None:
        import asyncio

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[self._user_row(exists=False)])
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.update_user_scope(
                    db,
                    user_id="u1",
                    expected_revision=1,
                    scope_type="ALL",
                    node_id=None,
                    reason="r",
                    operator="admin",
                )
            )
        assert ei.value.code == "ERR_AUTHZ_USER_NOT_FOUND"
        assert ei.value.status_code == 404

    def test_unknown_node_rejected(self) -> None:
        import asyncio

        user = MagicMock()
        user.username = "ic"
        db = AsyncMock()
        user_res = MagicMock()
        user_res.scalar_one_or_none.return_value = user
        node_res = MagicMock()
        node_res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[user_res, node_res])
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.update_user_scope(
                    db,
                    user_id="u1",
                    expected_revision=1,
                    scope_type="NODE_SUBTREE",
                    node_id="ghost",
                    reason="r",
                    operator="admin",
                )
            )
        assert ei.value.code == "ERR_AUTHZ_SCOPE_INVALID"


# ---------------------------------------------------------------------------
# 回退校验链
# ---------------------------------------------------------------------------


def _policy_row(revision: int, operation: str, **kw):
    row = MagicMock()
    row.revision = revision
    row.operation = operation
    row.role = kw.get("role")
    row.user_id = kw.get("user_id")
    row.before_value = kw.get("before_value")
    return row


class TestRollbackValidation:
    def test_unknown_revision_404(self) -> None:
        import asyncio

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(None)])
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.rollback_revision(
                    db, revision=99, expected_revision=1, reason="r", operator="admin"
                )
            )
        assert ei.value.code == "ERR_AUTHZ_REVISION_NOT_FOUND"
        assert ei.value.status_code == 404

    def test_seed_revision_not_rollbackable(self) -> None:
        import asyncio

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(_policy_row(1, "SEED"))])
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.rollback_revision(
                    db, revision=1, expected_revision=1, reason="r", operator="admin"
                )
            )
        assert ei.value.code == "ERR_AUTHZ_PROTECTED_REVISION"

    def test_double_rollback_rejected(self) -> None:
        import asyncio

        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(
                    _policy_row(
                        2, "ROLE_GRANT", role="SPONSOR", before_value={"pages": [], "actions": []}
                    )
                ),
                _scalar_result(5),  # already rolled back via revision 5
            ]
        )
        with pytest.raises(BizError) as ei:
            asyncio.run(
                az.rollback_revision(
                    db, revision=2, expected_revision=5, reason="r", operator="admin"
                )
            )
        assert ei.value.code == "ERR_AUTHZ_ALREADY_ROLLED_BACK"


# ---------------------------------------------------------------------------
# 端点（TestClient + mock DB）
# ---------------------------------------------------------------------------


class TestAuthorizationEndpoints:
    """PUT roles / rollback 的 HTTP 语义透传；非 ADMIN 403。"""

    def test_put_role_as_ic_403(self, client) -> None:
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/system/authorization/roles/SPONSOR",
                json={"expectedRevision": 1, "pageCodes": [], "actionCodes": [], "reason": "r"},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_put_role_stale_revision_409(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(9)])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/system/authorization/roles/SPONSOR",
                json={
                    "expectedRevision": 8,
                    "pageCodes": ["page:monitor.loops"],
                    "actionCodes": [],
                    "reason": "收紧演示",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "ERR_AUTHZ_REVISION_CONFLICT"
        assert body["data"]["currentRevision"] == 9

    def test_put_role_unknown_code_400(self, client, mock_db) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/system/authorization/roles/SPONSOR",
                json={
                    "expectedRevision": 1,
                    "pageCodes": ["page:nope"],
                    "actionCodes": [],
                    "reason": "r",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_AUTHZ_UNKNOWN_CODE"

    def test_put_role_missing_dependency_400(self, client, mock_db) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/system/authorization/roles/EXPERT",
                json={
                    "expectedRevision": 1,
                    "pageCodes": [],
                    "actionCodes": ["tuning:identify"],
                    "reason": "r",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_AUTHZ_MISSING_DEPENDENCY"

    def test_put_role_admin_protected_403(self, client, mock_db) -> None:
        """最后管理员失权保护的 HTTP 面：移除 protected 码→403+明确提示。"""
        codes = [c for c in sorted(az.ROLE_SEED["ADMIN"]) if c != "system:authorization-manage"]
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/system/authorization/roles/ADMIN",
                json={
                    "expectedRevision": 1,
                    "pageCodes": [c for c in codes if az.CATALOG[c].kind != "ACTION"],
                    "actionCodes": [c for c in codes if az.CATALOG[c].kind == "ACTION"],
                    "reason": "尝试移除授权管理能力",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "ERR_AUTHZ_ADMIN_PROTECTED"
        assert "system:authorization-manage" in body["message"]

    def test_put_scope_all_with_node_400(self, client, mock_db) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                "/api/v1/system/authorization/users/00000000-0000-0000-0000-000000000002/scope",
                json={"expectedRevision": 1, "scopeType": "ALL", "nodeId": "x", "reason": "r"},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_AUTHZ_SCOPE_INVALID"

    def test_rollback_seed_400(self, client, mock_db) -> None:
        seed_row = MagicMock()
        seed_row.operation = "SEED"
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(seed_row)])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/system/authorization/revisions/1/rollback",
                json={"expectedRevision": 1, "reason": "回退种子"},
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_AUTHZ_PROTECTED_REVISION"

    def test_catalog_requires_admin(self, client) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                "/api/v1/system/authorization/catalog",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_catalog_as_admin_ok(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=[_scalar_result(1)])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/system/authorization/catalog",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["revision"] == 1
        assert len(data["pages"]) == 71  # 11 组 + 60 页面（+P2-04 eng-templates）
        assert len(data["actions"]) == 56  # +alert:subscription-manage(P4-02)+data:import(尾批)

    def test_roles_effective_unknown_role_404(self, client, mock_db) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/system/authorization/roles/GUEST",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_roles_effective_marks_disabled_module(self, client, mock_db) -> None:
        mock_db.execute = AsyncMock(side_effect=[_grants_rows(sorted(az.ROLE_SEED["IC_ENGINEER"]))])
        with (
            mock_current_user(TEST_USERS["admin"]),
            patch(
                "app.api.v1.endpoints.system.authorization.get_enabled_modules",
                return_value={"monitor", "assess", "config", "system", "reports"},
            ),
        ):
            resp = client.get(
                "/api/v1/system/authorization/roles/IC_ENGINEER",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "diagnosis:trigger" in data["unavailableByModule"]["actions"]
        assert "page:diagnosis.overview" in data["unavailableByModule"]["pages"]
