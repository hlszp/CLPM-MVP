"""merge heads p3d608ds01 and p402authz03

Revision ID: 170fddd7af99
Revises: p3d608ds01, p402authz03
Create Date: 2026-10-12 04:07:34.264396

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "170fddd7af99"
down_revision: str | Sequence[str] | None = ("p3d608ds01", "p402authz03")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
