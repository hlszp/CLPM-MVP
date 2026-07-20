"""Async database session management (SQLAlchemy 2.0 + asyncpg)."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Async engine — created once at module import time.
# 使用 NullPool：Celery AsyncTask 为每个任务创建新 event loop，
# 连接池会跨 loop 复用连接导致 "Future attached to a different loop" 错误。
# NullPool 每次创建新连接，避免跨 loop 问题；localhost PG 建连开销 < 1ms。
engine = create_async_engine(
    settings.postgres_dsn,
    echo=settings.DEBUG,
    poolclass=NullPool,
    connect_args={"server_settings": {"application_name": "clpm-api"}},
)

# Session factory — use ``async with AsyncSessionLocal() as session:``.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session.

    The session is automatically closed when the request finishes, even if an
    exception was raised.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def dispose_engine() -> None:
    """Dispose of the engine connection pool (call on application shutdown)."""
    await engine.dispose()
