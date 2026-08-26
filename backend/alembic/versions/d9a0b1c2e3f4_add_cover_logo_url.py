"""add coverLogoUrl to site.basic_info

Revision ID: d9a0b1c2e3f4
Revises: c8f9a0b1d2e3
Create Date: 2026-08-26

为 site.basic_info JSON 补充 coverLogoUrl 字段（封面页横向 LOGO）。
若已有 site.basic_info 行，用 jsonb_set 补字段；若无则灌入完整默认值。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a0b1c2e3f4"
down_revision: str | Sequence[str] | None = "c8f9a0b1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: 为已有 site.basic_info 补 coverLogoUrl 字段."""
    # 1) 若行已存在，用 jsonb_set 补字段（不覆盖已有值）
    op.execute(
        sa.text(
            "UPDATE sys_config "
            "SET value = jsonb_set(value::jsonb, '{coverLogoUrl}', '\"\"', true)::text "
            "WHERE key = 'site.basic_info' "
            "AND value::jsonb ->> 'coverLogoUrl' IS NULL"
        )
    )
    # 2) 若行不存在，灌入完整默认值（含 coverLogoUrl）
    default_json = (
        '{"companyFullName":"致联化工科技有限公司",'
        '"companyShortName":"致联化工",'
        '"logoUrl":"",'
        '"coverLogoUrl":"",'
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
    """Downgrade schema: 移除 coverLogoUrl 字段."""
    op.execute(
        sa.text(
            "UPDATE sys_config "
            "SET value = (value::jsonb - 'coverLogoUrl')::text "
            "WHERE key = 'site.basic_info'"
        )
    )
