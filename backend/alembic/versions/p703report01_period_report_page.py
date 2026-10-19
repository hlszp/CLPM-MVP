"""期报页权限码（统计分析期报体系方案 2026-10-19 P0-5）.

新增 ``page:reports.period``（五角色可见），与目录 ``ROLE_SEED`` 一致。
幂等：ON CONFLICT DO NOTHING；策略账本记 CATALOG_EXTENSION（动态 revision）。
"""

from __future__ import annotations

from alembic import op

revision: str = "p703report01"
down_revision: str | None = "p702perm01"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected, description, sort_order) VALUES"
        "('page:reports.period', 'PAGE', 'page:reports', '[]'::jsonb, 'reports', false, "
        "'期报', 5)"
        "\nON CONFLICT (code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'page:reports.period'),"
        "('IC_ENGINEER', 'page:reports.period'),"
        "('PE_ENGINEER', 'page:reports.period'),"
        "('SPONSOR', 'page:reports.period'),"
        "('EXPERT', 'page:reports.period')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )
    op.execute(
        "INSERT INTO sys_authz_policy (revision, operation, operator, before_value, after_value) "
        "SELECT COALESCE((SELECT max(revision) FROM sys_authz_policy), 0) + 1, "
        "'CATALOG_EXTENSION', 'system', "
        "'{}'::jsonb, "
        "jsonb_build_object('source', 'p703report01', 'code', 'page:reports.period') "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM sys_authz_policy WHERE after_value->>'source' = 'p703report01'"
        ");"
    )
    op.execute(
        "UPDATE policy_publication_guard SET revision = GREATEST("
        "  revision, COALESCE((SELECT max(revision) FROM sys_authz_policy), revision)"
        ");"
    )


def downgrade() -> None:
    op.execute("DELETE FROM sys_role_permission WHERE permission_code = 'page:reports.period';")
    op.execute("DELETE FROM sys_permission WHERE code = 'page:reports.period';")
    op.execute("DELETE FROM sys_authz_policy WHERE after_value->>'source' = 'p703report01';")
