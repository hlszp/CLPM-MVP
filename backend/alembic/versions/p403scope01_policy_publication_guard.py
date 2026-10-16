"""policy publication guard + scope ledger operations (P4-03)

Revision ID: p403scope01
Revises: p305evd001
Create Date: 2026-10-12

P4-03 scope 贯穿（任务书 P4末批【一】节 / 03§7 P4-03 卡 / 01§3.5 末段）：

1. 新表 ``policy_publication_guard``（单行 id=1）：统一序列化全部授权相关
   变更——变更事务取该行排他锁并递增 revision；人工任务提交/WS 发送批取
   同一行共享锁再读主体策略（提交/发送栅栏）。种子 revision = 当前账本
   max(revision)（迁移前后口径连续）。
2. ``ck_sys_authz_policy_operation`` 扩枚举：NODE_REBIND（节点归属/回路
   unit 改挂）/ TAG_REBIND（tag 改绑）/ USER_STATE（用户禁用/启用）/
   TOKEN_REVOKE（令牌撤销）——域外策略变更者入账本，线性化可审计。

与引导 SQL（db/postgresql/01_schema.sql §9 / 02_seed_data.sql §15）同批
同内容。downgrade 幂等反向（guard 行回种为账本 max）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p403scope01"
down_revision: str | None = "p305evd001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GUARD_TABLE = "policy_publication_guard"


def upgrade() -> None:
    op.create_table(
        _GUARD_TABLE,
        sa.Column("id", sa.Integer(), autoincrement=False, primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # 单行种子：revision 对齐账本当前 max（空账本=0，与 authz_runtime 回退口径一致）
    op.execute(
        "INSERT INTO policy_publication_guard (id, revision, updated_at) "
        "SELECT 1, COALESCE((SELECT max(revision) FROM sys_authz_policy), 0), now()\n"
        "WHERE NOT EXISTS (SELECT 1 FROM policy_publication_guard WHERE id = 1)"
    )
    # 账本操作枚举扩展（域外策略变更者入账）
    op.drop_constraint("ck_sys_authz_policy_operation", "sys_authz_policy", type_="check")
    op.create_check_constraint(
        "ck_sys_authz_policy_operation",
        "sys_authz_policy",
        "operation IN ('SEED', 'ROLE_GRANT', 'USER_SCOPE', 'ROLLBACK', 'CATALOG_EXTENSION', "
        "'NODE_REBIND', 'TAG_REBIND', 'USER_STATE', 'TOKEN_REVOKE')",
    )


def downgrade() -> None:
    # 先回退枚举（guard 行的 revision 不回退——账本不可变，guard 重新对齐 max）
    op.drop_constraint("ck_sys_authz_policy_operation", "sys_authz_policy", type_="check")
    op.create_check_constraint(
        "ck_sys_authz_policy_operation",
        "sys_authz_policy",
        "operation IN ('SEED', 'ROLE_GRANT', 'USER_SCOPE', 'ROLLBACK', 'CATALOG_EXTENSION')",
    )
    op.execute(
        "UPDATE policy_publication_guard SET revision = "
        "COALESCE((SELECT max(revision) FROM sys_authz_policy "
        "WHERE operation IN ('SEED','ROLE_GRANT','USER_SCOPE','ROLLBACK','CATALOG_EXTENSION')), "
        "revision) WHERE id = 1"
    )
    op.drop_table(_GUARD_TABLE)
