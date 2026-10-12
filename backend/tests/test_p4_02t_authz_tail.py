"""P4-02 尾批测试：统一授权消费收尾（tuning/diagnosis 端点动作码接入 + 目录增量）.

覆盖（任务书第七波【二】节核验项）：

1. **种子等价**（主体批 D1 教训纪律：逐端点核"纯码语义 vs 基线角色集"）——
   尾批铺开的动作码种子角色集 == 迁移前活链 require_roles 白名单：
   - tuning:identify/tune/simulate/task-create/task-manage == {A,I,P,E}；
   - diagnosis:trigger/review/task-manage == {A,I,P,E}（diagnosis_v2 四角色）；
   - diagnosis-config:manage == {A}（configs /diagnosis 与旧诊断配置写）；
   - data:import == {A,I,P}（loop_data 活链白名单；data:manage 种子 {A}
     不扩——扩则放大 datasource/dataplanner/factory_sync 13 端点）。
2. **端点接线结构**：纯码等价替换项 / 角色锚+码双门项（种子 ⊃ 基线时锚保留）
   / 读码铺开项（原仅认证 GET 挂五角色读码，零行为变化）逐文件断言。
3. **SPONSOR 零写 + 五角色矩阵不扩大**：SPONSOR/EXPERT 调 loop_data 写端点
   403；IC 持 data:import 过守卫（业务 400 可区分）。
4. **L0/L1 fitness 门禁端到端负例**（module+fitness+授权三重 AND）：
   PE 持 tuning:identify 种子码仍被 L0/L1 fitness 拒（400
   ERR_TUNING_FITNESS_INSUFFICIENT）；L2 放行；SPONSOR 在授权层先行 403
   （授权与 fitness 恒 AND，授权只收紧不放宽）；tuning 模块禁用时码进
   unavailableByModule。
5. **目录增量三方一致**：迁移 p402authz03 == 引导 SQL P4-02t 增量 ==
   services.authz_catalog.CATALOG/ROLE_SEED（data:import）。
6. **legacy diagnosis.py 死代码不接码**：四 router 未挂载（main.py 注释），
   尾批零改动（diagnosis-config:manage 只接 configs.py 活链）。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services import authz_catalog as az
from app.services.loop_fitness import LoopFitnessLatest
from tests.conftest import TEST_USERS, mock_current_user

_AUTH = {"Authorization": "Bearer fake-token"}
_BACKEND = Path(__file__).resolve().parents[1]

ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "SPONSOR", "EXPERT")


def _seed_roles(code: str) -> set[str]:
    return {r for r in ROLES if code in az.ROLE_SEED[r]}


# ===========================================================================
# 1. 种子等价（码种子角色集 == 原活链 require_roles 白名单）
# ===========================================================================


class TestTailSeedParity:
    FOUR = {"ADMIN", "IC_ENGINEER", "PE_ENGINEER", "EXPERT"}

    def test_tuning_codes_match_legacy_whitelist(self) -> None:
        """tuning.py 全部写端点原白名单 {A,I,P,E} —— 五码种子逐一相等（等价替换）。"""
        for code in (
            "tuning:identify",
            "tuning:tune",
            "tuning:simulate",
            "tuning:task-create",
            "tuning:task-manage",
        ):
            assert _seed_roles(code) == self.FOUR, code

    def test_diagnosis_codes_match_v2_trigger_roles(self) -> None:
        """diagnosis_v2 四角色触发/复核/任务管理与 actions 端点种子 == {A,I,P,E}。"""
        for code in ("diagnosis:trigger", "diagnosis:review", "diagnosis:task-manage"):
            assert _seed_roles(code) == self.FOUR, code

    def test_diagnosis_config_manage_admin_only(self) -> None:
        """configs /diagnosis 写端点原白名单 {ADMIN} == diagnosis-config:manage 种子。"""
        assert _seed_roles("diagnosis-config:manage") == {"ADMIN"}

    def test_data_import_seed_matches_loop_data_chain(self) -> None:
        """loop_data 活链 _IMPORT_ROLES {A,I,P} == data:import 种子；
        data:manage 种子保持 {ADMIN}（对应 datasource/dataplanner/factory_sync
        A-only 基线——扩 data:manage 会放大那 13 个端点，违反语义不扩大）。"""
        assert _seed_roles("data:import") == {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"}
        assert _seed_roles("data:manage") == {"ADMIN"}

    def test_data_import_dependency_closure(self) -> None:
        """data:import requires config:view——IC/PE 均持 config:view（闭包成立）。"""
        assert az.CATALOG["data:import"].requires == ("config:view",)
        for role in ("IC_ENGINEER", "PE_ENGINEER"):
            assert "config:view" in az.ROLE_SEED[role]

    def test_sponsor_still_zero_write_on_tail_codes(self) -> None:
        """SPONSOR 零写红线：尾批接入的写码全部不在 SPONSOR 种子。"""
        for code in (
            "tuning:identify",
            "tuning:tune",
            "tuning:simulate",
            "tuning:task-create",
            "tuning:task-manage",
            "diagnosis:trigger",
            "diagnosis:review",
            "diagnosis:task-manage",
            "diagnosis-config:manage",
            "data:import",
        ):
            assert code not in az.ROLE_SEED["SPONSOR"], code


# ===========================================================================
# 2. 端点接线结构（纯码等价 / 锚+码双门 / 读码铺开）
# ===========================================================================


class TestTailEndpointWiring:
    def _text(self, rel: str) -> str:
        return (_BACKEND / f"app/api/v1/endpoints/{rel}").read_text()

    def test_tuning_pure_code_replacement(self) -> None:
        """tuning.py 10 写端点纯码等价替换（P3-07 增 /tune/robustness；无 require_roles 残留）+
        verification/data GET 挂 tuning:view（原 get_current_user 五角色 → 零变化）。"""
        src = self._text("tuning.py")
        assert 'require_perms("tuning:identify")' in src
        assert src.count('require_perms("tuning:identify")') == 3  # identify/history/segments
        # P3-07 REWORK 授权更新（验收 D1-②）：新增 /tune/robustness 端点接入
        # tuning:tune 权限（整定语义正当扩展），计数 2→3
        assert src.count('require_perms("tuning:tune")') == 3  # tune/matrix/robustness
        assert src.count('require_perms("tuning:simulate")') == 2  # simulate + compare
        assert 'require_perms("tuning:task-create")' in src
        assert 'require_perms("tuning:task-manage")' in src
        assert 'require_perms("tuning:view")' in src  # verification/data
        assert "Depends(require_roles" not in src
        assert "get_current_user" not in src

    def test_diagnosis_v2_wiring(self) -> None:
        """diagnosis_v2：写端点纯码（trigger/review/task-manage 种子==四角色）；
        review-feedback ADMIN 锚保留 + diagnosis:view 双门（读端点挂读码）。"""
        src = self._text("diagnosis_v2.py")
        assert src.count('require_perms("diagnosis:trigger")') == 1
        assert src.count('require_perms("diagnosis:review")') == 4  # review + actions×3
        assert 'require_perms("diagnosis:task-manage")' in src  # batch-delete
        m = re.search(r"async def get_review_feedback\(.*?\) -> ", src, re.S)
        assert m, "get_review_feedback signature not found"
        assert 'require_roles("ADMIN")' in m.group(0)  # 原白名单 {ADMIN} 锚保留
        assert 'require_perms("diagnosis:view")' in m.group(0)
        assert "get_current_user" not in src  # 11 读端点全部挂 diagnosis:view

    def test_configs_diagnosis_wiring(self) -> None:
        """configs /diagnosis 6 端点：GET 挂 diagnosis:view（五角色零变化）；
        PUT/POST/DELETE/rollback 纯码 diagnosis-config:manage（{ADMIN} 等价）；
        history ADMIN 锚 + config:view 双门（主体批 config history 同款）。"""
        src = self._text("configs.py")
        assert src.count('require_perms("diagnosis-config:manage")') == 4
        m = re.search(r"async def get_diagnosis_config_history\(.*?require_module", src, re.S)
        assert m, "history signature not found"
        assert 'require_roles("ADMIN")' in m.group(0)
        assert 'require_perms("config:view")' in m.group(0)
        assert 'require_module("diagnosis")' in src  # 模块门禁不受授权铺开影响

    def test_algorithms_wiring(self) -> None:
        """algorithms：tuning/calculate 纯码（{A,I,P,E} 等价）；kpi/calculate
        ADMIN 锚+metric:task-run 双门（纯码会放大到 I/P）；tasks 状态锚+metric:view。"""
        src = self._text("algorithms.py")
        m = re.search(r"async def calculate_kpi\(.*?\) -> ", src, re.S)
        assert m and "require_roles(*_KPI_DIAG_ROLES)" in m.group(0)
        assert 'require_perms("metric:task-run")' in m.group(0)
        m = re.search(r"async def calculate_tuning\(.*?\) -> ", src, re.S)
        assert m and 'require_perms("tuning:tune")' in m.group(0)
        assert "require_roles" not in m.group(0)
        m = re.search(r"async def get_algorithm_task_status\(.*?\) -> ", src, re.S)
        assert m and "require_roles(*_KPI_DIAG_ROLES, *_TUNING_ROLES)" in m.group(0)
        assert 'require_perms("metric:view")' in m.group(0)

    def test_workbench_tail_endpoints(self) -> None:
        """workbench BFF 尾批三端点：diagnosis→diagnosis:view、tuning/
        tuning-scatters→tuning:view（原 get_current_user 五角色 → 种子亦五角色零变化）。"""
        src = self._text("workbench.py")
        m = re.search(r'@router\.get\(\s*"/diagnosis".*?async def get_diagnosis\(', src, re.S)
        assert m and 'require_perms("diagnosis:view")' in m.group(0)
        for route in ('"/tuning"', '"/tuning-scatters"'):
            m = re.search(rf"@router\.get\(\s*{re.escape(route)}.*?async def", src, re.S)
            assert m, route
            assert 'require_perms("tuning:view")' in m.group(0), route

    def test_loop_data_pure_code(self) -> None:
        """loop_data 6 端点纯码 data:import（种子 {A,I,P} == 活链白名单）。"""
        src = self._text("loop_data.py")
        assert src.count('require_perms("data:import")') == 6
        assert "Depends(require_roles" not in src

    def test_cross_domain_no_gate_gets_coded(self) -> None:
        """无门跨域 GET 补码（原 get_current_user → 五角色读码，零行为变化）：
        cockpit/dashboard/site → portal:view；plant/dcs/tags/dicts → loop:view。"""
        cases = {
            "cockpit.py": ("portal:view", 3),
            "dashboard.py": ("portal:view", 6),
            "site.py": ("portal:view", 1),
            "plant_nodes.py": ("loop:view", 2),
            "dcs.py": ("loop:view", 7),
            "tags.py": ("loop:view", 6),
            "dicts.py": ("loop:view", 2),
        }
        for rel, (code, count) in cases.items():
            src = self._text(rel)
            assert src.count(f'require_perms("{code}")') >= count, rel
            assert "get_current_user" not in src, rel

    def test_cross_domain_codes_are_five_role_seeds(self) -> None:
        """补码选择的读码种子必须五角色全持（否则收紧而非零变化）。"""
        for code in ("portal:view", "loop:view", "diagnosis:view", "tuning:view", "metric:view"):
            assert _seed_roles(code) == set(ROLES), code

    def test_legacy_diagnosis_module_not_mounted(self) -> None:
        """legacy diagnosis.py 四 router 未挂载（死代码链登记不删）——尾批不接码。"""
        main_src = (_BACKEND / "app/main.py").read_text()
        for mount in (
            "v1_router.include_router(diagnosis.router)",
            "v1_router.include_router(diagnosis.tracker_router)",
            "v1_router.include_router(diagnosis.timeseries_router)",
            "v1_router.include_router(diagnosis.tags_router)",
        ):
            assert f"# {mount}" in main_src, mount  # 注释状态=未挂载
        assert "v1_router.include_router(diagnosis_v2.router)" in main_src  # v2 活链
        legacy = self._text("diagnosis.py")
        for code in ("diagnosis-config:manage", "diagnosis:trigger", "diagnosis:review"):
            assert code not in legacy, code  # 死代码不接动作码


# ===========================================================================
# 3. SPONSOR 零写 + 五角色矩阵不扩大（运行时）
# ===========================================================================


class TestSponsorAndRoleMatrixTail:
    def test_sponsor_tuning_identify_403(self, client) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/tuning/identify",
                json={
                    "loopId": "loop-a",
                    "startTime": "2026-01-01T00:00:00Z",
                    "endTime": "2026-01-01T01:00:00Z",
                    "modelType": "FOPDT",
                },
                headers=_AUTH,
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_data_import_403(self, client) -> None:
        """SPONSOR/EXPERT 不持 data:import（零写/只读语义不扩大）→ 403 先于业务校验。"""
        for key in ("sponsor", "expert"):
            with mock_current_user(TEST_USERS[key]):
                resp = client.post(
                    "/api/v1/loops/data-import/start",
                    json={
                        "loopIds": ["loop-a"],
                        "tsStart": "2026-01-01T00:00:00Z",
                        "tsEnd": "2026-01-02T00:00:00Z",
                    },
                    headers=_AUTH,
                )
            assert resp.status_code == 403, key
            assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_ic_data_import_passes_guard(self, client) -> None:
        """IC 持 data:import（种子==活链 A/I/P）→ 守卫放行，进入业务校验
        （时间窗倒置 400 ERR_INVALID_TIME_RANGE 与 403 可区分）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/loops/data-import/start",
                json={
                    "loopIds": ["loop-a"],
                    "tsStart": "2026-01-02T00:00:00Z",
                    "tsEnd": "2026-01-01T00:00:00Z",
                },
                headers=_AUTH,
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_INVALID_TIME_RANGE"

    def test_sponsor_diagnosis_v2_run_403(self, client) -> None:
        """SPONSOR 对诊断 v2 触发 403（diagnosis:trigger 种子四角色不含 SPONSOR）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/diagnosis/run",
                json={"loopIds": ["00000000-0000-0000-0000-0000000000aa"]},
                headers=_AUTH,
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_ic_diagnosis_v2_run_passes_guard(self, client) -> None:
        """IC 持 diagnosis:trigger → 守卫放行，空 loopIds 业务 400 可区分。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post("/api/v1/diagnosis/run", json={"loopIds": []}, headers=_AUTH)
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_ic_configs_diagnosis_write_403(self, client) -> None:
        """IC 无 diagnosis-config:manage（种子 {ADMIN}）→ 配置写 403 不因尾批放宽。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put("/api/v1/configs/diagnosis", json={"items": []}, headers=_AUTH)
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"


# ===========================================================================
# 4. L0/L1 fitness 门禁端到端负例（module+fitness+授权三重 AND）
# ===========================================================================


def _fitness(level: str) -> dict:
    return {
        "loop-a": LoopFitnessLatest(
            loop_id="loop-a", level=level, tags=["MANUAL_DOMINANT"], detail=None
        )
    }


class TestFitnessGateEndToEnd:
    _IDENTIFY_BODY = {
        "loopId": "loop-a",
        "startTime": "2026-07-29T00:00:00Z",
        "endTime": "2026-07-29T01:00:00Z",
        "modelType": "FOPDT",
    }

    def test_pe_with_code_blocked_by_fitness_l0_l1(self, client, mock_db) -> None:
        """PE 持 tuning:identify 种子码（授权层通过）仍被 fitness L0/L1 拒——
        三重 AND：授权不放宽业务门禁（ERR_TUNING_FITNESS_INSUFFICIENT 400）。"""
        for level in ("L0", "L1"):
            with (
                patch(
                    "app.api.v1.endpoints.tuning.get_latest_fitness_per_loop",
                    AsyncMock(return_value=_fitness(level)),
                ),
                mock_current_user(TEST_USERS["pe_engineer"]),
            ):
                resp = client.post(
                    "/api/v1/tuning/identify", json=self._IDENTIFY_BODY, headers=_AUTH
                )
            assert resp.status_code == 400, level
            body = resp.json()
            assert body["code"] == "ERR_TUNING_FITNESS_INSUFFICIENT", level
            assert body["data"]["fitnessLevel"] == level

    def test_pe_l2_passes_fitness_gate(self, client, mock_db) -> None:
        """L2 放行（2026-10-01 裁决：L2 从阻断改为放行）→ 进入辨识业务层（200）。
        PE 端到端正例：授权（种子码）+ fitness（L2）双过。"""
        identify_result = {
            "modelType": "FOPDT",
            "params": {"k": 1.0, "tau": 10.0, "theta": 2.0},
            "fittingScore": 92.0,
            "stepValidationPassed": True,
            "algorithmVersion": "TUNE_ENGINE_v1.0",
            "dataPoints": 120,
            "fittedCurve": None,
        }
        with (
            patch(
                "app.api.v1.endpoints.tuning.get_latest_fitness_per_loop",
                AsyncMock(return_value=_fitness("L2")),
            ),
            patch(
                "app.api.v1.endpoints.tuning.identify_model",
                AsyncMock(return_value=identify_result),
            ),
            patch(
                "app.api.v1.endpoints.tuning.persist_step_identification_record",
                AsyncMock(return_value="rec-1"),
            ),
            mock_current_user(TEST_USERS["pe_engineer"]),
        ):
            resp = client.post("/api/v1/tuning/identify", json=self._IDENTIFY_BODY, headers=_AUTH)
        assert resp.status_code == 200

    def test_sponsor_denied_by_auth_before_fitness(self, client, mock_db) -> None:
        """SPONSOR（无 tuning:identify）→ 403 先于 fitness（授权与业务门禁恒 AND，
        层序：授权先行收紧；fitness 门禁不因角色放行而被绕过）。"""
        with (
            patch(
                "app.api.v1.endpoints.tuning.get_latest_fitness_per_loop",
                AsyncMock(return_value=_fitness("L0")),
            ) as mock_fit,
            mock_current_user(TEST_USERS["sponsor"]),
        ):
            resp = client.post("/api/v1/tuning/identify", json=self._IDENTIFY_BODY, headers=_AUTH)
        assert resp.status_code == 403
        assert not mock_fit.await_count  # 守卫先行，fitness 未被查询

    def test_module_disabled_and_authorized_code_unavailable(self) -> None:
        """module 维度 AND：tuning 模块禁用时 tuning:identify 进
        unavailableByModule（授权与模块开关恒 AND，只收紧不放宽）。"""
        view = az.get_effective_access(
            "PE_ENGINEER", az.ROLE_SEED["PE_ENGINEER"], enabled_modules=set()
        )
        assert "tuning:identify" in view["unavailableByModule"]["actions"]
        assert "diagnosis:trigger" in view["unavailableByModule"]["actions"]
        # data:import 挂 config 模块键（config:manage 域），同受模块开关约束
        assert "data:import" in view["unavailableByModule"]["actions"]

    def test_tuning_router_mounted_under_module_gate(self) -> None:
        """tuning router 挂载受 is_module_enabled("tuning") 约束（结构断言）——
        模块关闭时整组端点不注册（授权层之外的独立 AND 维度）。"""
        main_src = (_BACKEND / "app/main.py").read_text()
        m = re.search(
            r'if is_module_enabled\("tuning"\):.*?v1_router\.include_router\(tuning\.router\)',
            main_src,
            re.S,
        )
        assert m
        m = re.search(
            r'if is_module_enabled\("diagnosis"\):.*?'
            r"v1_router\.include_router\(diagnosis_v2\.router\)",
            main_src,
            re.S,
        )
        assert m


# ===========================================================================
# 5. 目录增量三方一致（迁移 == 引导 SQL == CATALOG/ROLE_SEED）
# ===========================================================================


class TestCatalogTailExtensionParity:
    def test_data_import_in_catalog(self) -> None:
        d = az.CATALOG["data:import"]
        assert d.kind == "ACTION"
        assert d.module_key == "config"
        assert d.requires == ("config:view",)
        assert not d.protected

    def test_seed_grants_data_import_to_aip_only(self) -> None:
        assert _seed_roles("data:import") == {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"}
        for role in ("SPONSOR", "EXPERT"):
            assert "data:import" not in az.ROLE_SEED[role]

    def test_migration_matches_catalog(self) -> None:
        sql = (_BACKEND / "alembic/versions/p402authz03_authz_tail_extension.py").read_text()
        # 目录行（迁移源码内嵌转义，校验稳定子串 + 属性组合）
        assert "('data:import', 'ACTION'" in sql
        assert "config:view" in sql
        for role in ("ADMIN", "IC_ENGINEER", "PE_ENGINEER"):
            assert f"('{role}', 'data:import')" in sql, role
        for role in ("SPONSOR", "EXPERT"):
            assert f"('{role}', 'data:import')" not in sql, role
        assert "(3, 'CATALOG_EXTENSION'" in sql.replace('"', "'")
        assert "p302task01" in sql  # 迁移链接在现行 head 之后
        # data:manage 描述收口（"历史导入"移入 data:import）
        assert "数据源配置/DataPlanner/工厂同步" in sql

    def test_bootstrap_sql_matches_catalog(self) -> None:
        seed_sql = (_BACKEND.parent / "db/postgresql/02_seed_data.sql").read_text()
        assert "('data:import', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', " in seed_sql
        for role in ("ADMIN", "IC_ENGINEER", "PE_ENGINEER"):
            assert f"('{role}', 'data:import')" in seed_sql, role
        assert "(3, 'CATALOG_EXTENSION'" in seed_sql.replace('"', "'")
        # data:manage 基线描述与新目录一致（三方一致含展示文案）
        assert "数据源配置/DataPlanner/工厂同步" in seed_sql
        assert "数据源配置/历史导入/DataPlanner/工厂同步" not in seed_sql

    def test_catalog_description_matches_bootstrap(self) -> None:
        assert az.CATALOG["data:manage"].description == "数据源配置/DataPlanner/工厂同步"

    def test_admin_seed_is_full_catalog(self) -> None:
        """ADMIN 种子仍为全目录动作码（含 data:import）。"""
        expected = frozenset(c for c in az.CATALOG if az.CATALOG[c].kind != "PAGE_GROUP")
        assert az.ROLE_SEED["ADMIN"] == expected
