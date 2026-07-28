"""add_sys_user_must_change_password: sys_user 新增首次登录强制改密标志.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-28

变更内容（S5-AUTH P1 安全基线）：
- sys_user 新增 must_change_password 列（NOT NULL DEFAULT FALSE）
- 存量数据迁移：仅对仍使用种子默认密码 admin123 的 5 个账户置 True
  （按 db/postgresql/02_seed_data.sql 中的 bcrypt 哈希精确匹配，
  已改密账户不受影响）
- 应用层配合：登录响应带 mustChangePassword 标志；标志为 True 时
  get_current_user 拒绝除改密/登出外的写操作（读端点放行）；
  change_password 成功后清除标志
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | None = None
depends_on: str | None = None

# db/postgresql/02_seed_data.sql 中 5 个种子用户的 admin123 bcrypt 哈希
_SEED_PASSWORD_HASHES = (
    "$2b$12$EmVQ8NwGlB/O8L4vJ0XSluBfxYOlTwBer7vnNFuVL/0qmhSXlfy/u",  # admin
    "$2b$12$3KxnNHH3KmxeEE6AUmQOeuFEccnBLlHxaDBX5BIWCwvKPq1gqLrxy",  # ic_engineer
    "$2b$12$dLInICVCCkfdsIfs6jJnqeJfR0HDzFbv7yqBWboZQSLRknlQuhOKG",  # pe_engineer
    "$2b$12$lpgnpJwE956RFjcYb4hyOubgVYhf0IDWs0xlBzbCU1RMuT1cmR0sC",  # sponsor
    "$2b$12$ai8B75As3GLsuFBHayAq2ufsMMmzezF.E9tg.058I/a30V7nTuiTG",  # expert
)


def upgrade() -> None:
    """升级：新增 must_change_password 列 + 种子账户置 True."""
    op.add_column(
        "sys_user",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )

    # 仅标记仍使用种子默认密码的账户（哈希精确匹配，不误伤已改密账户）
    hashes = ", ".join(f"'{h}'" for h in _SEED_PASSWORD_HASHES)
    op.execute(f"UPDATE sys_user SET must_change_password = TRUE WHERE password_hash IN ({hashes})")


def downgrade() -> None:
    """降级：删除 must_change_password 列."""
    op.drop_column("sys_user", "must_change_password")
