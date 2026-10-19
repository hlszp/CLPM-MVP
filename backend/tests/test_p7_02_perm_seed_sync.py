"""P7-02 D-1 修复测试：page:my-work 系 + page:config.eng-templates 种子三方一致.

E2E 报告（P7-02-E2E报告.md §6 D-1）事实：代码 CATALOG/ROLE_SEED 已含三码
（127 码）而 DB 目录 124 码——p401/p402 冻结种子后无后续同步迁移，
authz_runtime 以 DB 矩阵优先且 revision≠0 不回退 ROLE_SEED，导致 /my-work
与 /config/eng-templates 两页不可达 + IC/PE/EXPERT 默认首页 404。

本文件钉死修复后的三方一致（迁移 p702perm01 == 引导 SQL 02_seed_data.sql
增量 == services.authz_catalog.CATALOG/ROLE_SEED），形态同
test_p4_02t_authz_tail.py::TestCatalogTailExtensionParity。

真实 PG 上的两路径（新装/存量升级）迁移实跑由
tests/integration/test_p4_01_real_pg.py（三方对账断言动态取 CATALOG/ROLE_SEED
规模，本批增量自动纳入）覆盖。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.services import authz_catalog as az

_BACKEND = Path(__file__).resolve().parents[1]
_MIGRATION = _BACKEND / "alembic/versions/p702perm01_authz_page_seed_sync.py"
_SEED_SQL = _BACKEND.parent / "db/postgresql/02_seed_data.sql"


def _py_sql_source(path: Path) -> str:
    """迁移源码归一：合并相邻字符串字面量（跨行拼接的 SQL 按单行匹配）."""
    return re.sub(r'"\s*\n\s*"', "", path.read_text(encoding="utf-8"))


ROLES = ("ADMIN", "IC_ENGINEER", "PE_ENGINEER", "SPONSOR", "EXPERT")
_MY_WORK_CODES = ("page:my-work", "page:my-work.main")
_ENG_TPL = "page:config.eng-templates"


def _seed_roles(code: str) -> set[str]:
    return {r for r in ROLES if code in az.ROLE_SEED[r]}


# ===========================================================================
# 1. 代码侧事实源（CATALOG/ROLE_SEED）——修复所对齐的口径
# ===========================================================================


class TestCatalogSourceOfTruth:
    def test_catalog_total_128_codes(self) -> None:
        """目录总数 128（+期报 page:reports.period）."""
        assert len(az.CATALOG) == 128

    def test_my_work_group_definition(self) -> None:
        d = az.CATALOG["page:my-work"]
        assert d.kind == "PAGE_GROUP"
        assert d.parent is None
        assert d.module_key is None
        assert d.order == -1

    def test_my_work_main_definition(self) -> None:
        d = az.CATALOG["page:my-work.main"]
        assert d.kind == "PAGE"
        assert d.parent == "page:my-work"
        assert d.module_key is None
        assert not d.protected

    def test_eng_templates_definition(self) -> None:
        d = az.CATALOG[_ENG_TPL]
        assert d.kind == "PAGE"
        assert d.parent == "page:config"
        assert d.module_key == "config"
        assert not d.protected
        assert d.order == 65

    def test_my_work_main_seed_all_five_roles(self) -> None:
        """my-work.main 五角色（_MY_WORK_PAGES ∈ IC/PE/SPONSOR/EXPERT + ADMIN 全码）."""
        assert _seed_roles("page:my-work.main") == set(ROLES)

    def test_eng_templates_seed_admin_ic_pe(self) -> None:
        """eng-templates ∈ _CONFIG_PAGES_ENG（ADMIN/IC/PE；SPONSOR/EXPERT 不授）."""
        assert _seed_roles(_ENG_TPL) == {"ADMIN", "IC_ENGINEER", "PE_ENGINEER"}
        for role in ("SPONSOR", "EXPERT"):
            assert _ENG_TPL not in az.ROLE_SEED[role]

    def test_group_codes_never_in_any_seed(self) -> None:
        """PAGE_GROUP 不参与角色矩阵（p401 种子口径；组码零角色关联）."""
        for code in ("page:my-work", "page:cockpit", "page:config"):
            assert _seed_roles(code) == set(), code

    def test_admin_seed_is_full_catalog_non_group(self) -> None:
        """ADMIN 种子仍为全目录非组码（含三新码）."""
        expected = frozenset(c for c in az.CATALOG if az.CATALOG[c].kind != "PAGE_GROUP")
        assert az.ROLE_SEED["ADMIN"] == expected


# ===========================================================================
# 2. 迁移 p702perm01 三方一致（源码形态断言，同 test_p4_02t 形态）
# ===========================================================================


class TestMigrationParity:
    def test_migration_chain_attached_to_current_head(self) -> None:
        sql = _py_sql_source(_MIGRATION)
        assert 'revision: str = "p702perm01"' in sql
        assert 'down_revision: str | None = "p605invest01"' in sql

    def test_migration_catalog_rows(self) -> None:
        sql = _py_sql_source(_MIGRATION)
        assert "('page:my-work', 'PAGE_GROUP', NULL, '[]'::jsonb, NULL, false," in sql
        assert "('page:my-work.main', 'PAGE', 'page:my-work', '[]'::jsonb, NULL, false," in sql
        assert "('page:config.eng-templates', 'PAGE', 'page:config', '[]'::jsonb, 'config'," in sql
        assert "ON CONFLICT (code) DO NOTHING" in sql

    def test_migration_matrix_grants_match_role_seed(self) -> None:
        sql = _py_sql_source(_MIGRATION)
        for role in ROLES:
            assert f"('{role}', 'page:my-work.main')" in sql, role
        for role in ("ADMIN", "IC_ENGINEER", "PE_ENGINEER"):
            assert f"('{role}', 'page:config.eng-templates')" in sql, role
        # 组码不进矩阵
        assert "('ADMIN', 'page:my-work')" not in sql

    def test_migration_ledger_and_guard(self) -> None:
        """账本 CATALOG_EXTENSION 行（动态 revision）+ guard 对齐推进（缓存重载）."""
        sql = _MIGRATION.read_text(encoding="utf-8")
        assert "'CATALOG_EXTENSION'" in sql
        # 动态 revision：存量库运行时已推进（E2E revision=25），固定号会主键冲突
        assert "COALESCE((SELECT max(revision) FROM sys_authz_policy), 0) + 1" in sql
        assert "'source', 'p702perm01'" in sql
        # 幂等判重以 source 标记
        assert "WHERE NOT EXISTS (SELECT 1 FROM sys_authz_policy" in sql
        assert "WHERE after_value->>'source' = 'p702perm01')" in sql
        # guard 推进（authz_runtime 按 guard revision 缓存矩阵）
        assert "UPDATE policy_publication_guard SET revision = GREATEST(revision," in sql

    def test_migration_downgrade_removes_only_increment(self) -> None:
        sql = _py_sql_source(_MIGRATION)
        assert (
            "DELETE FROM sys_role_permission WHERE permission_code IN "
            "('page:my-work.main', 'page:config.eng-templates')" in sql
        )
        # 子码先于组码删（parent FK RESTRICT）
        assert sql.index("DELETE FROM sys_permission WHERE code IN") < sql.index(
            "DELETE FROM sys_permission WHERE code = 'page:my-work'"
        )


# ===========================================================================
# 3. 引导 SQL（02_seed_data.sql）三方一致
# ===========================================================================


class TestBootstrapSqlParity:
    def test_bootstrap_catalog_rows(self) -> None:
        sql = _SEED_SQL.read_text(encoding="utf-8")
        assert (
            "('page:my-work', 'PAGE_GROUP', NULL, '[]'::jsonb, NULL, false, "
            "'我的工作（工程师个人首页：责任/建议/关注/接续）', -1)" in sql
        )
        assert "('page:my-work.main', 'PAGE', 'page:my-work', '[]'::jsonb, NULL, false," in sql
        assert (
            "('page:config.eng-templates', 'PAGE', 'page:config', '[]'::jsonb, "
            "'config', false," in sql
        )

    def test_bootstrap_matrix_grants_match_role_seed(self) -> None:
        sql = _SEED_SQL.read_text(encoding="utf-8")
        for role in ROLES:
            assert f"('{role}', 'page:my-work.main')" in sql, role
        for role in ("ADMIN", "IC_ENGINEER", "PE_ENGINEER"):
            assert f"('{role}', 'page:config.eng-templates')" in sql, role
        assert "('SPONSOR', 'page:config.eng-templates')" not in sql
        assert "('EXPERT', 'page:config.eng-templates')" not in sql

    def test_bootstrap_ledger_row(self) -> None:
        """新装账本链 revision=4 CATALOG_EXTENSION（source 标记同迁移）."""
        sql = _SEED_SQL.read_text(encoding="utf-8")
        assert "(4, 'CATALOG_EXTENSION'" in sql
        assert '"source": "p702perm01"}\'::jsonb' in sql

    def test_bootstrap_guard_seeds_from_ledger_max(self) -> None:
        """guard 行种子取账本 max（含 revision=4，无需逐批改）."""
        sql = _SEED_SQL.read_text(encoding="utf-8")
        assert "SELECT 1, COALESCE((SELECT max(revision) FROM sys_authz_policy), 0), now()" in sql
