"""authz page-code seed sync: my-work group/main + config.eng-templates (P7-02 D-1)

Revision ID: p702perm01
Revises: p605invest01
Create Date: 2026-10-16

P7-02 E2E 缺陷 D-1（P1）：``page:my-work`` / ``page:my-work.main`` /
``page:config.eng-templates`` 三码已在代码侧 ``services.authz_catalog``
CATALOG/ROLE_SEED 落地（P6-06 / P6-M），但未随任何迁移/种子下发——DB 目录
124 码 vs 代码 127 码，``authz_runtime`` 以 DB 矩阵优先且 revision≠0 不回退
ROLE_SEED，导致：

- /my-work（P6-G 四视图）全角色菜单隐藏 + 直达 404；
- IC/PE/EXPERT 默认首页 /my-work → 登录直接落 404 fallback（P6-06 已切
  首页链而权限未随批）；
- /config/eng-templates（P2-04/P6-M 工程模板维护）不可达；
- 权限配置页「我的工作」列五角色全「—」。

本迁移为纯数据增量（结构零变更）：

1. 目录 +3 码（角色口径 == 代码 ROLE_SEED，PAGE_GROUP 不参与角色矩阵）：
   - ``page:my-work``（PAGE_GROUP，order -1）：组码，无角色关联；
   - ``page:my-work.main``（PAGE）：五角色（_MY_WORK_PAGES ∈ IC/PE/SPONSOR/
     EXPERT 页集 + ADMIN 全 PAGE 码）；
   - ``page:config.eng-templates``（PAGE，module_key=config）：ADMIN/IC/PE
     （_CONFIG_PAGES_ENG ⊂ IC/PE 页集 + ADMIN 全码）。
2. 账本追加 ``CATALOG_EXTENSION`` 行（revision 动态取 max+1——存量库
   revision 已被运行时 ROLE_GRANT 推进至 25，固定号会主键冲突；目录扩展=
   系统基线变更，不可回退）。
3. guard revision 对齐账本新 max（authz_runtime 按 guard revision 缓存
   矩阵，推进后各进程下一请求重载——否则补码不生效）。

与引导 SQL（db/postgresql/02_seed_data.sql §15 增量段）同批同内容；三方
一致（迁移 == 引导 SQL == CATALOG/ROLE_SEED）由 tests/test_p7_02_perm_seed_sync.py
钉死（存量库/全新库皆中——结构性缺口，环境无关）。

downgrade：删增量矩阵行/账本行/目录行并回退 guard（幂等；子码先于组码删，
FK RESTRICT）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p702perm01"
down_revision: str | None = "p605invest01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 目录增量：3 码（形态同 p401/p402 冻结种子；角色口径 == ROLE_SEED）
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected, description, sort_order) VALUES"
        "('page:my-work', 'PAGE_GROUP', NULL, '[]'::jsonb, NULL, false, "
        "'我的工作（工程师个人首页：责任/建议/关注/接续）', -1),"
        "('page:my-work.main', 'PAGE', 'page:my-work', '[]'::jsonb, NULL, false, "
        "'我的工作｜五角色', 10),"
        "('page:config.eng-templates', 'PAGE', 'page:config', '[]'::jsonb, 'config', "
        "false, '工程模板｜ADMIN/IC/PE', 65)"
        "\nON CONFLICT (code) DO NOTHING;"
    )

    # 2. 矩阵增量：my-work.main 五角色 + eng-templates A/I/P
    #    （PAGE_GROUP 组码不参与角色矩阵，与 p401 种子口径一致）
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:my-work.main'),"
        "('IC_ENGINEER', 'page:my-work.main'),"
        "('PE_ENGINEER', 'page:my-work.main'),"
        "('SPONSOR', 'page:my-work.main'),"
        "('EXPERT', 'page:my-work.main'),"
        "('ADMIN', 'page:config.eng-templates'),"
        "('IC_ENGINEER', 'page:config.eng-templates'),"
        "('PE_ENGINEER', 'page:config.eng-templates')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )

    # 3. 账本行：目录扩展版本（不可回退）。revision 动态取 max+1（存量库
    #    运行时已推进至 25+，固定号会主键冲突；新装路径 max=3 → 4 与引导
    #    SQL 固定 4 等价）。幂等以 after_value.source 标记判重。
    op.execute(
        "INSERT INTO sys_authz_policy (revision, operation, role, user_id, reason, "
        "operator, before_value, after_value, rollback_of, created_at)\n"
        "SELECT COALESCE((SELECT max(revision) FROM sys_authz_policy), 0) + 1, "
        "'CATALOG_EXTENSION', NULL, NULL, "
        "'P7-02 D-1：补下发 page:my-work 组/page:my-work.main/page:config.eng-templates"
        "（代码 CATALOG/ROLE_SEED 已含而种子未随批；角色口径 == ROLE_SEED）', "
        "'system:migration', NULL, "
        "jsonb_build_object("
        '\'addedCodes\', \'["page:my-work", "page:my-work.main", '
        '"page:config.eng-templates"]\'::jsonb, '
        '\'grants\', \'{"ADMIN": ["page:my-work.main", "page:config.eng-templates"], '
        '"IC_ENGINEER": ["page:my-work.main", "page:config.eng-templates"], '
        '"PE_ENGINEER": ["page:my-work.main", "page:config.eng-templates"], '
        '"SPONSOR": ["page:my-work.main"], '
        '"EXPERT": ["page:my-work.main"]}\'::jsonb, '
        "'source', 'p702perm01'), NULL, now()\n"
        "WHERE NOT EXISTS (SELECT 1 FROM sys_authz_policy "
        "WHERE after_value->>'source' = 'p702perm01')"
    )

    # 4. guard revision 对齐账本新 max（authz_runtime 按 guard 缓存矩阵，
    #    不推进则补码不生效；GREATEST 防倒退）
    op.execute(
        "UPDATE policy_publication_guard SET revision = GREATEST(revision, "
        "(SELECT COALESCE(max(revision), 0) FROM sys_authz_policy)), "
        "updated_at = now() WHERE id = 1"
    )


def downgrade() -> None:
    # 矩阵增量（组码无矩阵行，仅两 PAGE 码）
    op.execute(
        "DELETE FROM sys_role_permission WHERE permission_code IN "
        "('page:my-work.main', 'page:config.eng-templates')"
    )
    # 账本行（按 source 标记定位，幂等）
    op.execute(
        "DELETE FROM sys_authz_policy WHERE operation = 'CATALOG_EXTENSION' "
        "AND after_value->>'source' = 'p702perm01'"
    )
    # guard 回退对齐账本剩余 max（不倒退口径与 p403scope01 downgrade 一致）
    op.execute(
        "UPDATE policy_publication_guard SET revision = "
        "COALESCE((SELECT max(revision) FROM sys_authz_policy), revision), "
        "updated_at = now() WHERE id = 1"
    )
    # 目录增量：子码先于组码删（parent FK RESTRICT）
    op.execute(
        "DELETE FROM sys_permission WHERE code IN "
        "('page:my-work.main', 'page:config.eng-templates')"
    )
    op.execute("DELETE FROM sys_permission WHERE code = 'page:my-work'")
