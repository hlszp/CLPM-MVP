"""authz catalog tail extension: data:import action code (P4-02 尾批)

Revision ID: p402authz03
Revises: p302task01
Create Date: 2026-10-12

P4-02 尾批（loop_data/data:manage 种子缺口补齐，任务书第七波【二】2）：

loop_data.py（/loops/data-import 6 端点）活链 require_roles 白名单为
{ADMIN, IC_ENGINEER, PE_ENGINEER}，而 data:manage 种子仅 {ADMIN}（对应
datasource/dataplanner/factory_sync 的 A-only 基线）。扩 data:manage 种子
会放大那 13 个已等价替换端点（违反"五角色矩阵语义不扩大"），故按 P4-01
"动作码种子=活链真实白名单"原则补独立动作码：

1. 目录新增 ``data:import``（ACTION，requires config:view，module_key=config）；
2. 矩阵增量：ADMIN（目录全码）+ IC_ENGINEER + PE_ENGINEER（== loop_data
   活链白名单；SPONSOR/EXPERT 不授，零写红线与只读语义不扩大）；
3. ``data:manage`` description 更新（"历史导入"移入 data:import，纯展示
   文案，无授权语义）；
4. 账本追加 ``revision=3, operation=CATALOG_EXTENSION`` 行（目录扩展=系统
   基线变更，不可回退；推进 revision 使各进程运行时矩阵缓存下一请求重载）。

与引导 SQL（db/postgresql/02_seed_data.sql P4-02t 增量）同批同内容；
三方一致（迁移 == 引导 SQL == services.authz_catalog.CATALOG/ROLE_SEED）
由 tests/test_p4_02t_authz_tail.py 钉死。

downgrade：删除增量矩阵行/目录行/账本行并还原 description（幂等）。
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p402authz03"
down_revision: str | None = "p302task01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 目录增量：历史数据导入独立动作码（loop_data.py 6 端点等价替换载体）
    op.execute(
        "INSERT INTO sys_permission (code, kind, parent_code, requires_codes, module_key, "
        "is_protected, description, sort_order) VALUES"
        "('data:import', 'ACTION', NULL, '[\"config:view\"]'::jsonb, 'config', "
        "false, '历史数据导入/KPI 回算（ADMIN/IC/PE）', 1606)"
        "\nON CONFLICT (code) DO NOTHING;"
    )

    # 2. data:manage description 更新（"历史导入"移入 data:import，纯展示文案）
    op.execute(
        "UPDATE sys_permission SET description = '数据源配置/DataPlanner/工厂同步' "
        "WHERE code = 'data:manage'"
    )

    # 3. 矩阵增量：ADMIN + IC + PE（== loop_data 活链白名单 A/I/P，不扩大）
    op.execute(
        "INSERT INTO sys_role_permission (role, permission_code) VALUES"
        "('ADMIN', 'data:import'),"
        "('IC_ENGINEER', 'data:import'),"
        "('PE_ENGINEER', 'data:import')"
        "\nON CONFLICT (role, permission_code) DO NOTHING;"
    )

    # 4. 账本行：目录扩展版本（不可回退）；推进 revision 触发运行时缓存重载
    _grants = json.dumps(
        {
            "ADMIN": ["data:import"],
            "IC_ENGINEER": ["data:import"],
            "PE_ENGINEER": ["data:import"],
        }
    )
    _reason = (
        "P4-02 尾批：历史数据导入补独立动作码 data:import（ADMIN/IC/PE == "
        "loop_data 活链白名单；data:manage 种子不扩防放大 datasource 等 13 端点）"
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO sys_authz_policy (revision, operation, role, user_id, reason, "
            "operator, before_value, after_value, rollback_of, created_at) VALUES"
            "(3, 'CATALOG_EXTENSION', NULL, NULL, :reason, 'system:migration', NULL, "
            "jsonb_build_object('addedCodes', CAST(:added AS jsonb), "
            "'grants', CAST(:grants AS jsonb), 'source', 'p402authz03'), NULL, now())\n"
            "ON CONFLICT (revision) DO NOTHING"
        ),
        {
            "reason": _reason,
            "added": '["data:import"]',
            "grants": _grants,
        },
    )


def downgrade() -> None:
    op.execute("DELETE FROM sys_role_permission WHERE permission_code = 'data:import'")
    op.execute(
        "DELETE FROM sys_authz_policy WHERE revision = 3 AND operation = 'CATALOG_EXTENSION'"
    )
    op.execute("DELETE FROM sys_permission WHERE code = 'data:import'")
    op.execute(
        "UPDATE sys_permission SET description = '数据源配置/历史导入/DataPlanner/工厂同步' "
        "WHERE code = 'data:manage'"
    )
