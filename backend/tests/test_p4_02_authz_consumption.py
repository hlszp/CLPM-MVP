"""P4-02 统一授权消费测试：deps.authorizer + 端点铺开 + 前端下发口.

覆盖（任务书【三】节核验项）：

1. **DB 矩阵驱动**：require_perms 读 sys_role_permission（经
   authz_runtime 的 revision 比对缓存）；移除一个授权 → 下一请求同步
   收紧（revision 变化即重载）；revision=0（迁移未应用）回退 ROLE_SEED。
2. **SPONSOR 零写结构红线**：矩阵即使授予写码，require_perms 对
   SPONSOR 仍结构性拒绝（P1-04 裁决，矩阵编辑不可绕过）。
3. **注册期未知码拒绝**：require_perms 引用未登记码在路由注册期
   ValueError（fail-fast）。
4. **目录增量三方一致**：迁移 p402authz02 种子 == 引导 SQL §15 增量 ==
   services.authz_catalog.CATALOG（alert:subscription-manage）。
5. **端点映射种子等价**：铺开的动作码种子角色集与迁移前活链
   require_roles 白名单一致（无语义扩大；backfill 等角色锚保留项对照）。
6. **module enabled / fitness 门禁不被授权覆盖**：授权层恒为 AND——
   模块禁用时码授权不产生可用性（get_effective_access 标注）+ 模块门禁
   端点（require_module）不受授权影响的结构断言。
7. **/auth/me 下发口**：DB 驱动 permissions（动作码显式集合）+ pageCodes
   （页面码，菜单可达页事实源）。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import authz_catalog as az
from app.services import authz_runtime as rt
from tests.conftest import TEST_USERS, mock_current_user

_AUTH = {"Authorization": "Bearer fake-token"}


# ---------------------------------------------------------------------------
# Helpers：脱离 CLPM_TEST_MODE 的 mock DB（DB 驱动路径直测）
# ---------------------------------------------------------------------------


def _rev_result(rev: int | None):
    m = MagicMock()
    m.scalar_one_or_none = MagicMock(return_value=rev)
    return m


def _matrix_result(pairs: list[tuple[str, str]]):
    m = MagicMock()
    m.fetchall = MagicMock(return_value=pairs)
    return m


@pytest.fixture()
def db_path_env(monkeypatch: pytest.MonkeyPatch):
    """移除单测标记 + 清运行时缓存，直测 DB 驱动路径。"""
    monkeypatch.delenv("CLPM_TEST_MODE", raising=False)
    rt.reset_runtime_cache()
    yield
    rt.reset_runtime_cache()
    import os

    os.environ.setdefault("CLPM_TEST_MODE", "1")


# ===========================================================================
# 1. DB 矩阵驱动（revision 比对缓存 / 移除即收紧 / 空账本回退种子）
# ===========================================================================


class TestDbDrivenAuthorizer:
    async def test_db_matrix_grant_allows(self, db_path_env) -> None:
        """DB 矩阵授码 → require_perms 判定通过（IC 持 loop:edit）。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _rev_result(3),
                _matrix_result([("IC_ENGINEER", "loop:view"), ("IC_ENGINEER", "loop:edit")]),
            ]
        )
        codes = await rt.get_role_codes(db, "IC_ENGINEER")  # type: ignore[arg-type]
        assert {"loop:view", "loop:edit"} <= codes

    async def test_db_matrix_missing_code_denies(self, db_path_env) -> None:
        """DB 矩阵未授码 → 拒绝（IC 无 loop:delete——ADMIN 专属删除不因铺开放宽）。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                _rev_result(3),
                _matrix_result([("IC_ENGINEER", "loop:view")]),
            ]
        )
        codes = await rt.get_role_codes(db, "IC_ENGINEER")  # type: ignore[arg-type]
        assert "loop:delete" not in codes

    async def test_revision_bump_reloads_matrix(self, db_path_env) -> None:
        """revision 变化（移除授权发布）→ 下一请求同步收紧（缓存重载）。"""
        db = AsyncMock()
        # 第一轮：IC 持 loop:edit（revision=5）
        db.execute = AsyncMock(
            side_effect=[
                _rev_result(5),
                _matrix_result([("IC_ENGINEER", "loop:view"), ("IC_ENGINEER", "loop:edit")]),
            ]
        )
        first = await rt.get_role_codes(db, "IC_ENGINEER")  # type: ignore[arg-type]
        assert "loop:edit" in first
        # 第二轮：revision 推进到 6（矩阵移除 loop:edit）→ 缓存失效重载
        db.execute = AsyncMock(
            side_effect=[
                _rev_result(6),
                _matrix_result([("IC_ENGINEER", "loop:view")]),
            ]
        )
        second = await rt.get_role_codes(db, "IC_ENGINEER")  # type: ignore[arg-type]
        assert "loop:edit" not in second
        assert "loop:view" in second

    async def test_revision_zero_falls_back_to_seed(self, db_path_env) -> None:
        """账本空（P4-01 迁移未应用）→ ROLE_SEED 兜底（行为等价旧静态链）。"""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_rev_result(None)])
        codes = await rt.get_role_codes(db, "IC_ENGINEER")  # type: ignore[arg-type]
        assert codes == az.ROLE_SEED["IC_ENGINEER"]

    async def test_malformed_revision_scalar_treated_as_empty(self, db_path_env) -> None:
        """revision 标量形态异常（不可转 int 的对象）→ 视为空账本回退种子。"""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_rev_result(object())])  # int() 必然 TypeError
        codes = await rt.get_role_codes(db, "IC_ENGINEER")  # type: ignore[arg-type]
        assert codes == az.ROLE_SEED["IC_ENGINEER"]

    async def test_effective_codes_split_actions_and_pages(self, db_path_env) -> None:
        """get_effective_codes：动作码/页面码两列下发（/auth/me 事实源）。"""
        db = AsyncMock()
        granted = sorted(az.ROLE_SEED["SPONSOR"])
        db.execute = AsyncMock(
            side_effect=[
                _rev_result(2),
                _matrix_result([("SPONSOR", c) for c in granted]),
            ]
        )
        effective = await rt.get_effective_codes(db, "SPONSOR")  # type: ignore[arg-type]
        assert set(effective["actions"]) == {
            c for c in az.ROLE_SEED["SPONSOR"] if az.CATALOG[c].kind == "ACTION"
        }
        assert "page:monitor.loops" in effective["pages"]
        assert all(c.startswith("page:") for c in effective["pages"])


# ===========================================================================
# 2. SPONSOR 零写结构红线（矩阵编辑不可绕过）
# ===========================================================================


class TestSponsorZeroWriteRedLine:
    def test_read_only_code_set_excludes_writes(self) -> None:
        assert "loop:view" in rt.READ_ONLY_ACTION_CODES
        assert "tracker:review" in rt.READ_ONLY_ACTION_CODES
        for write_code in ("loop:delete", "loop:edit", "config:publish", "tuning:tune"):
            assert write_code not in rt.READ_ONLY_ACTION_CODES

    async def test_sponsor_write_denied_even_if_matrix_grants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SPONSOR 结构性拒绝写码：模拟矩阵被异常编辑授予全码，require_perms
        仍在红线层先行拒绝（不依赖矩阵纪律）。"""
        from app.api.deps import require_perms
        from app.core.exceptions import BizError

        async def _granted_everything(db, role):  # noqa: ANN001, ARG001
            return frozenset(az.CATALOG.keys())  # 全码矩阵（含全部写码）

        monkeypatch.setattr(rt, "get_role_codes", _granted_everything)
        checker = require_perms("loop:create")
        with pytest.raises(BizError) as exc_info:
            await checker(user=TEST_USERS["sponsor"], db=AsyncMock())  # type: ignore[arg-type]
        assert exc_info.value.code == "ERR_PERMISSION_DENIED"
        assert "零写红线" in exc_info.value.message

    def test_sponsor_write_endpoint_403_under_seed(self, client, mock_db) -> None:
        """种子口径下 SPONSOR 调写端点（POST /loops）仍 403（零写不因铺开扩大）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post("/api/v1/loops", json={"tagName": "T-1"}, headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"


# ===========================================================================
# 3. 注册期未知码拒绝
# ===========================================================================


class TestUnknownCodeFailFast:
    def test_unknown_code_raises_at_registration(self) -> None:
        from app.api.deps import require_perms

        with pytest.raises(ValueError, match="未在授权目录登记"):
            require_perms("loop:nonexistent")

    def test_wildcard_code_rejected(self) -> None:
        from app.api.deps import require_perms

        with pytest.raises(ValueError):
            require_perms("loop:*")


# ===========================================================================
# 4. 目录增量三方一致（迁移 == 引导 SQL == CATALOG 常量）
# ===========================================================================

_BACKEND = Path(__file__).resolve().parents[1]


class TestCatalogExtensionParity:
    def test_subscription_code_in_catalog(self) -> None:
        d = az.CATALOG["alert:subscription-manage"]
        assert d.kind == "ACTION"
        assert d.module_key == "monitor"
        assert d.requires == ("alert:view",)

    def test_seed_grants_subscription_to_admin_ic_only(self) -> None:
        assert "alert:subscription-manage" in az.ROLE_SEED["ADMIN"]
        assert "alert:subscription-manage" in az.ROLE_SEED["IC_ENGINEER"]
        for role in ("PE_ENGINEER", "SPONSOR", "EXPERT"):
            assert "alert:subscription-manage" not in az.ROLE_SEED[role]

    def test_migration_seed_matches_catalog(self) -> None:
        sql = (_BACKEND / "alembic/versions/p402authz02_authz_catalog_extension.py").read_text()
        assert "alert:subscription-manage" in sql
        assert (
            '("ADMIN", "alert:subscription-manage")' in sql.replace("'", '"')
            or "('ADMIN', 'alert:subscription-manage')" in sql
        )
        # 检查约束扩展 + 账本行（revision 推进触发运行时缓存重载）
        assert "CATALOG_EXTENSION" in sql
        assert "(2, 'CATALOG_EXTENSION'" in sql.replace('"', "'") or "revision=2" in sql

    def test_bootstrap_sql_seed_matches_catalog(self) -> None:
        seed_sql = (_BACKEND.parent / "db/postgresql/02_seed_data.sql").read_text()
        assert "('alert:subscription-manage', 'ACTION'" in seed_sql
        assert "('ADMIN', 'alert:subscription-manage')" in seed_sql
        assert "('IC_ENGINEER', 'alert:subscription-manage')" in seed_sql
        assert "('PE_ENGINEER', 'alert:subscription-manage')" not in seed_sql
        schema_sql = (_BACKEND.parent / "db/postgresql/01_schema.sql").read_text()
        assert "CATALOG_EXTENSION" in schema_sql  # 检查约束扩入

    async def test_rollback_catalog_extension_rejected(self) -> None:
        """目录扩展版本不可回退（系统基线变更，无逆操作）。"""
        from app.core.exceptions import BizError
        from app.services.authz_catalog import rollback_revision

        row = MagicMock()
        row.operation = "CATALOG_EXTENSION"
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_rev_result(row))
        with pytest.raises(BizError) as exc_info:
            await rollback_revision(
                db,  # type: ignore[arg-type]
                revision=2,
                expected_revision=2,
                reason="r",
                operator="admin",
            )
        assert exc_info.value.code == "ERR_AUTHZ_PROTECTED_REVISION"


# ===========================================================================
# 5. 端点映射种子等价（五角色矩阵语义不扩大）
# ===========================================================================


class TestEndpointMappingSeedParity:
    """铺开的动作码种子角色集 == 迁移前活链 require_roles 白名单.

    等价替换项（种子集与原白名单完全一致 → DB 矩阵完全接管）；
    角色锚保留项（种子集 ⊃ 原白名单 → 双门：角色锚保留原限制 + 码可收紧）。
    """

    ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "SPONSOR", "EXPERT")

    def _seed_roles(self, code: str) -> set[str]:
        return {r for r in self.ROLES if code in az.ROLE_SEED[r]}

    def test_exact_parity_codes(self) -> None:
        cases = {
            # 码: 原白名单
            "loop:create": {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"},
            "loop:edit": {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"},
            "loop:export": {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"},
            "loop:import": {"ADMIN", "IC_ENGINEER"},
            "loop:batch-grouping": {"ADMIN", "IC_ENGINEER"},
            "loop:batch-config": {"ADMIN"},
            "loop:delete": {"ADMIN"},
            "alert:rule-manage": {"ADMIN"},
            "alert:dry-run": {"ADMIN", "IC_ENGINEER"},
            "alert:subscription-manage": {"ADMIN", "IC_ENGINEER"},
            "alert:event-handle": {"ADMIN", "IC_ENGINEER"},
            "alert:suppression": {"ADMIN", "IC_ENGINEER"},
            "handling:operate": {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"},
            "metric:task-run": {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"},
            "metric:task-manage": {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"},
            "metric:snapshot-manage": {"ADMIN", "IC_ENGINEER"},
            "metric-config:manage": {"ADMIN"},
            "config:publish": {"ADMIN"},
            "tag:manage": {"ADMIN", "IC_ENGINEER"},
            "plant:manage": {"ADMIN"},
            "dcs:manage": {"ADMIN"},
            "data:manage": {"ADMIN"},
            "ai:insight": {"ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT"},
            "system:report-config": {"ADMIN"},
            "system:dict-manage": {"ADMIN"},
            "system:basic-manage": {"ADMIN"},
        }
        for code, expected in cases.items():
            assert self._seed_roles(code) == expected, (
                f"{code} 种子角色集 {self._seed_roles(code)} != 原白名单 {expected}"
            )

    def test_role_anchored_endpoints_kept(self) -> None:
        """角色锚保留项：种子码集 ⊃ 原白名单的端点必须保留 require_roles 锚.

        - tags batch-delete / delete（ADMIN 专属；tag:manage 种子含 IC）
        - tasks backfill（A/I；metric:task-run 种子含 PE）
        - alert events archive（ADMIN；alert:event-handle 种子含 A/I）
        - loops health-flags/recompute（ADMIN；loop:edit 种子含 A/I/P）
        - monitor loops summary（A/I/P/E；loop:view 种子五角色）
        - users/audit/modules/authorization（protected + ADMIN 角色锚）
        """
        tags = (_BACKEND / "app/api/v1/endpoints/tags.py").read_text()
        assert 'Depends(require_roles("ADMIN"))' in tags  # ADMIN 专属 tag 删除锚
        assert 'require_perms("tag:manage")' in tags
        tasks = (_BACKEND / "app/api/v1/endpoints/tasks.py").read_text()
        m = re.search(r"async def trigger_backfill\(.*?\) -> ", tasks, re.S)
        assert m, "trigger_backfill signature not found"
        sig = m.group(0)
        assert 'require_roles("ADMIN", "IC_ENGINEER")' in sig  # A/I 角色锚（PE 不放开）
        assert 'require_perms("metric:task-run")' in sig
        # D1（REWORK）回归：start_task 启动 PENDING 的 BACKFILL 任务——历史重算
        # 同入口，必须与 backfill 同款双门（纯码替换曾放开 PE，测试漏覆盖）
        m = re.search(r"async def start_task\(.*?\) -> ", tasks, re.S)
        assert m, "start_task signature not found"
        sig = m.group(0)
        assert 'require_roles("ADMIN", "IC_ENGINEER")' in sig, (
            "start_task 缺 A/I 角色锚：PE 将经 metric:task-run 获得历史重算启动权（D1）"
        )
        assert 'require_perms("metric:task-run")' in sig
        alert = (_BACKEND / "app/api/v1/endpoints/alert.py").read_text()
        m = re.search(r"async def archive_event_endpoint\(.*?\) -> ", alert, re.S)
        assert m, "archive_event_endpoint signature not found"
        assert 'require_roles("ADMIN")' in m.group(0)
        assert 'require_perms("alert:event-handle")' in m.group(0)
        users = (_BACKEND / "app/api/v1/endpoints/users.py").read_text()
        assert 'require_roles("ADMIN")' in users
        assert 'require_perms("system:user-manage")' in users
        authorization = (_BACKEND / "app/api/v1/endpoints/system/authorization.py").read_text()
        assert 'require_roles("ADMIN")' in authorization  # P4-01 原样（protected 语义）

    def test_diagnosis_tuning_endpoints_untouched(self) -> None:
        """主体批边界复核：P4-02 主体批时 tuning/diagnosis/loop_data 端点禁碰。
        尾批（p402authz03 同批）已接入 loop_data（data:import）与
        diagnosis_v2（diagnosis:*/tuning:* 系动作码）——本断言更新为：
        ①legacy diagnosis.py（四 router 未挂载的死代码链）仍零铺开；
        ②尾批接入的等价性由 tests/test_p4_02t_authz_tail.py 独立钉死。
        """
        diagnosis_legacy = (_BACKEND / "app/api/v1/endpoints/diagnosis.py").read_text()
        assert "require_perms" in diagnosis_legacy  # P1-04 既有读码原样（死代码链）
        assert "diagnosis-config:manage" not in diagnosis_legacy  # 尾批不接死代码
        loop_data = (_BACKEND / "app/api/v1/endpoints/loop_data.py").read_text()
        assert 'require_perms("data:import")' in loop_data  # 尾批等价接入
        diagnosis_v2 = (_BACKEND / "app/api/v1/endpoints/diagnosis_v2.py").read_text()
        assert 'require_perms("diagnosis:trigger")' in diagnosis_v2


# ===========================================================================
# 6. module enabled / fitness 门禁不被授权覆盖
# ===========================================================================


class TestGatesNotRelaxed:
    def test_module_disabled_marks_codes_unavailable(self) -> None:
        """模块禁用：已授码进 unavailableByModule（AND 语义，只收紧不放宽）。"""
        view = az.get_effective_access(
            "IC_ENGINEER", az.ROLE_SEED["IC_ENGINEER"], enabled_modules=set()
        )
        assert "tuning:identify" in view["unavailableByModule"]["actions"]
        assert "page:tuning.overview" in view["unavailableByModule"]["pages"]
        # 全局码（portal:view）不受模块开关约束
        assert "portal:view" not in view["unavailableByModule"]["actions"]

    def test_module_enabled_keeps_codes_available(self) -> None:
        from app.core.modules import MODULES

        view = az.get_effective_access(
            "IC_ENGINEER", az.ROLE_SEED["IC_ENGINEER"], enabled_modules=set(MODULES)
        )
        assert not view["unavailableByModule"]["actions"]
        assert not view["unavailableByModule"]["pages"]

    def test_module_guard_endpoints_keep_require_module(self) -> None:
        """require_module 门禁端点（configs /diagnosis）不受授权铺开影响。"""
        configs = (_BACKEND / "app/api/v1/endpoints/configs.py").read_text()
        assert 'require_module("diagnosis")' in configs

    def test_fitness_gate_layer_untouched(self) -> None:
        """fitness L0/L1 阻断在 tuning 服务层（本批零触碰 tuning 系文件）——
        授权码 tuning:advanced 的 description 钉死门禁不因授权放宽。"""
        assert "fitness" in az.CATALOG["tuning:advanced"].description
        tuning_service = _BACKEND / "app/services/tuning.py"
        assert tuning_service.exists()  # 结构性存在；本批 diff 零触碰（交接清单核对）


# ===========================================================================
# 7. /auth/me 下发口（单测种子兜底路径）
# ===========================================================================


class TestMeEndpointContract:
    def test_me_returns_explicit_codes_and_page_codes(self, client, mock_db) -> None:
        """/auth/me：permissions=显式动作码（无通配），pageCodes=可达页码。"""
        from tests.conftest import TEST_USERS as _TU
        from tests.conftest import make_db_execute_return

        mock_db.execute = AsyncMock(return_value=make_db_execute_return(_TU["admin"]))
        from tests.conftest import login_as

        data = login_as(client, "admin")
        token = data["accessToken"]
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        user = resp.json()["data"]
        assert "*" not in user["permissions"]
        assert "loop:delete" in user["permissions"]
        assert "alert:subscription-manage" in user["permissions"]
        assert "page:system.permissions" in user["pageCodes"]
        # 动作码与页面码命名空间分离
        assert all(not c.startswith("page:") for c in user["permissions"])
        assert all(c.startswith("page:") for c in user["pageCodes"])

    def test_me_sponsor_readonly(self, client, mock_db) -> None:
        from tests.conftest import TEST_USERS as _TU
        from tests.conftest import make_db_execute_return

        mock_db.execute = AsyncMock(return_value=make_db_execute_return(_TU["sponsor"]))
        from tests.conftest import login_as

        data = login_as(client, "sponsor")
        token = data["accessToken"]
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        user = resp.json()["data"]
        assert user["permissions"] == [
            "alert:view",
            "diagnosis:view",
            "handling:view",
            "loop:view",
            "metric:view",
            "portal:view",
            "tuning:view",
        ]
        assert "page:system.users" not in user["pageCodes"]
