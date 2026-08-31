"""drop_sys_user_must_change_password: sys_user 删除首次登录强制改密标志.

Revision ID: g7b8c9d0e1f2
Revises: f5a6b7c8d9e0
Create Date: 2026-08-31

变更内容：
- sys_user 删除 must_change_password 列
- 背景：首次登录强制改密功能整体下线（2026-08-31 决策），系统以默认密码
  运行，用户可经个人中心自愿改密；登录响应 mustChangePassword 字段与
  get_current_user 写操作 403 拦截同步移除
- downgrade 恢复列定义（Boolean NOT NULL DEFAULT FALSE），不回填
  种子账户 True 标志（功能已废弃，恢复列仅为结构回滚）
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7b8c9d0e1f2"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """升级：删除 must_change_password 列."""
    op.drop_column("sys_user", "must_change_password")


def downgrade() -> None:
    """降级：恢复 must_change_password 列（默认 FALSE）."""
    op.add_column(
        "sys_user",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
