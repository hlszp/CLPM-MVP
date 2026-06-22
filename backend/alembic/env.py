"""Alembic migration environment.

Configuration:
- Database URL is read dynamically from ``app.core.config.settings`` (asyncpg).
- ``target_metadata`` is ``app.models.Base.metadata`` (all 14 tables).
- Both offline (SQL emission) and online (async execution) modes are supported.
- ``compare_type`` and ``compare_server_default`` are enabled so that
  autogenerate detects type/server-default drift.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import application settings and model metadata so Alembic can introspect.
from app.core.config import settings
from app.models import Base  # noqa: F401  (ensures all models are registered)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata used by autogenerate.
target_metadata = Base.metadata

# Inject the async PostgreSQL URL into the Alembic config so that any
# helper that reads ``config.get_main_option("sqlalchemy.url")`` also works.
config.set_main_option("sqlalchemy.url", settings.postgres_dsn)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Emits SQL to stdout without requiring a live DBAPI connection. The URL
    is taken from application settings.
    """

    context.configure(
        url=settings.postgres_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure context and run migrations within a live connection."""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using an async engine."""

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online mode — delegates to the async runner."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
