"""authz catalog extension: alert subscription action code (P4-02)

Revision ID: p402authz02
Revises: 331e9436dc52
Create Date: 2026-10-11

P4-02 观察项①（P4-01 验收报告）：alert 订阅 CRUD（create/delete_subscription，
require_roles A/I）此前与规则管理混域、无独立动作码。本迁移补登：

1. 目录新增 ``alert:subscription-manage``（ACTION，requires alert:view，
   module_key=monitor）；
2. 矩阵增量：ADMIN（目录全码）+ IC_ENGINEER（与活链 require_roles 白名单
   一致——PE/SPONSOR/EXPERT 不授，五角色矩阵语义不扩大）；
3. 账本追加 ``revision=2, operation=CATALOG_EXTENSION`` 行：目录扩展是
   系统基线变更（随迁移发布，不可回退），同时推进 revision 使各进程的
   运行时矩阵缓存（authz_runtime）在下一请求即重载失效；
4. ``ck_sys_authz_policy_operation`` 检查约束扩入 CATALOG_EXTENSION。

与引导 SQL（db/postgresql/02_seed_data.sql §15 P4-02 增量）同批同内容；
三方一致（迁移 == 引导 SQL == services.authz_catalog.CATALOG）由单测
test_p4_02_authz_consumption.py 钉死。

downgrade：删除增量矩阵行/目录行/账本行并还原检查约束（幂等）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p402authz02"
down_revision: str | None = "331e9436dc52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 账本操作枚举扩入 CATALOG_EXTENSION（先改约束再落账本行）
    op.execute(
        "ALTER TABLE sys_authz_policy DROP CONSTRAINT IF EXISTS ck_sys_authz_policy_operation"
    )
    op.execute(
        "ALTER TABLE sys_authz_policy ADD CONSTRAINT ck_sys_authz_policy_operation "
        "CHECK (operation IN ('SEED', 'ROLE_GRANT', 'USER_SCOPE', 'ROLLBACK', 'CATALOG_EXTENSION'))"
    )

    # 2. 目录增量：alert 订阅独立动作码
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected, description, sort_order) VALUES"
        "('alert:subscription-manage', 'ACTION', NULL, '[\"alert:view\"]'::jsonb, 'monitor', "
        "false, '预警订阅创建/删除（ADMIN/IC）', 1504)"
        "\nON CONFLICT (code) DO NOTHING;"
    )

    # 3. 矩阵增量：ADMIN + IC（活链 require_roles 白名单一致，不扩大）
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'alert:subscription-manage'),"
        "('IC_ENGINEER', 'alert:subscription-manage')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )

    # 4. 账本行：目录扩展版本（不可回退）；推进 revision 触发运行时缓存重载
    op.execute(
        "INSERT INTO sys_authz_policy (revision, operation, role, user_id, reason, operator,"
        "before_value, after_value, rollback_of, created_at) VALUES"
        "(2, 'CATALOG_EXTENSION', NULL, NULL, "
        "'P4-02 观察项①：alert 订阅 CRUD 补独立动作码 alert:subscription-manage"
        "（ADMIN/IC）', 'system:migration', NULL, "
        "jsonb_build_object('addedCodes', '[\"alert:subscription-manage\"]'::jsonb, "
        '\'grants\', \'{"ADMIN": ["alert:subscription-manage"], '
        '"IC_ENGINEER": ["alert:subscription-manage"]}\'::jsonb, '
        "'source', 'p402authz02'), NULL, now())"
        "\nON CONFLICT (revision) DO NOTHING;"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM sys_role_permission WHERE permission_code = 'alert:subscription-manage'"
    )
    op.execute(
        "DELETE FROM sys_authz_policy WHERE revision = 2 AND operation = 'CATALOG_EXTENSION'"
    )
    op.execute("DELETE FROM sys_permission WHERE code = 'alert:subscription-manage'")
    op.execute(
        "ALTER TABLE sys_authz_policy DROP CONSTRAINT IF EXISTS ck_sys_authz_policy_operation"
    )
    op.execute(
        "ALTER TABLE sys_authz_policy ADD CONSTRAINT ck_sys_authz_policy_operation "
        "CHECK (operation IN ('SEED', 'ROLE_GRANT', 'USER_SCOPE', 'ROLLBACK'))"
    )
