"""add site.basic_info to sys_config

Revision ID: c8f9a0b1d2e3
Revises: b7e8f9a0c1d2
Create Date: 2026-08-26

灌入站点基础信息默认值（公司简称/全称/LOGO/联系/授权/部署等 12 字段 JSON）。
存储于 sys_config 单条 key=site.basic_info，由 endpoints/site.py 读写。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f9a0b1d2e3"
down_revision: str | Sequence[str] | None = "b7e8f9a0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: 灌入 site.basic_info 默认 JSON."""
    # 默认值与 endpoints/site.py _DEFAULTS 保持一致
    default_json = (
        '{"companyFullName":"致联化工科技有限公司",'
        '"companyShortName":"致联化工",'
        '"logoUrl":"",'
        '"contactPerson":"",'
        '"contactPhone":"",'
        '"contactEmail":"",'
        '"address":"",'
        '"authorizedLoopCount":null,'
        '"licenseExpireDate":null,'
        '"systemDeployId":"",'
        '"systemDeployDate":null,'
        '"serviceProvider":""}'
    )
    op.execute(
        sa.text(
            "INSERT INTO sys_config (key, value, description, updated_by, updated_at) "
            "VALUES (:key, :value, :desc, 'system', NOW()) "
            "ON CONFLICT (key) DO NOTHING"
        ).bindparams(
            sa.bindparam("key", "site.basic_info"),
            sa.bindparam("value", default_json),
            sa.bindparam("desc", "站点基础信息（JSON：公司/联系/授权/部署等）"),
        )
    )


def downgrade() -> None:
    """Downgrade schema: 删除 site.basic_info 配置行."""
    op.execute(
        sa.text("DELETE FROM sys_config WHERE key = :key").bindparams(
            sa.bindparam("key", "site.basic_info")
        )
    )
