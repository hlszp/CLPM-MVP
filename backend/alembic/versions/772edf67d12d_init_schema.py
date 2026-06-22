"""init schema

Baseline migration for CLPM. The 14-table schema was already applied to the
database via ``db/postgresql/01_schema.sql`` (executed automatically by the
PostgreSQL container's ``docker-entrypoint-initdb.d``). This migration is
intentionally empty and is stamped (``alembic stamp head``) to mark the
current database state as the starting point for future migrations.

Revision ID: 772edf67d12d
Revises:
Create Date: 2026-06-22 09:01:52.573493

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "772edf67d12d"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema — no-op (schema already applied via DDL)."""
    pass


def downgrade() -> None:
    """Downgrade schema — no-op (cannot reverse initial DDL)."""
    pass
