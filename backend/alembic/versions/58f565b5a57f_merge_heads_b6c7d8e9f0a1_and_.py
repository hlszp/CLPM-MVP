"""merge heads b6c7d8e9f0a1 and c3bee6758850 (fitness fields)

Revision ID: 58f565b5a57f
Revises: b6c7d8e9f0a1, c3bee6758850
Create Date: 2026-08-22 16:33:24.637833

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58f565b5a57f"
down_revision: str | Sequence[str] | None = ("b6c7d8e9f0a1", "c3bee6758850")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
