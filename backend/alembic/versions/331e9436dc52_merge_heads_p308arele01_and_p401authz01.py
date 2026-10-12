"""merge heads p308arele01 and p401authz01

Revision ID: 331e9436dc52
Revises: p308arele01, p401authz01
Create Date: 2026-10-11 15:43:39.480382

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "331e9436dc52"
down_revision: str | Sequence[str] | None = ("p308arele01", "p401authz01")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
