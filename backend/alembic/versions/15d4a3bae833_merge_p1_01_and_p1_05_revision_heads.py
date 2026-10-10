"""merge p1-01 and p1-05 revision heads

Revision ID: 15d4a3bae833
Revises: p105resledger01, a1b2c3d4e5f7
Create Date: 2026-10-11 06:41:42.815114

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "15d4a3bae833"
down_revision: str | Sequence[str] | None = ("p105resledger01", "a1b2c3d4e5f7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
