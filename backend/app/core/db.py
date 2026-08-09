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
#
# 加固（2026-08-09 后端宕死排查）：
# - echo 强制 False：DEBUG 下 echo 会绕过 logger 双写全部 SQL
#   （dev 日志 26 万行/天），日志噪音由 logging 级别统一控制；
# - command_timeout=60：单条 SQL 执行超 60s 报错而非无限挂起，
#   防止慢查询把请求任务永久钉在 socket 上（0% CPU 静默挂死诱因之一）。
engine = create_async_engine(
    settings.postgres_dsn,
    echo=False,
    poolclass=NullPool,
    connect_args={
        "server_settings": {"application_name": "clpm-api"},
        "command_timeout": 60,
    },
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
