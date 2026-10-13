"""Async database session management (SQLAlchemy 2.0 + asyncpg)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger(__name__)

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
        "server_settings": {
            "application_name": "clpm-api",
            # 会话时区固定 UTC（2026-09-24 修复）：
            # 应用写入的所有时间列都是 naive UTC（datetime.now(UTC).replace(tzinfo=None)），
            # 而 PG 集群默认 TimeZone=Asia/Shanghai。裸 func.now() / date_trunc('day', now())
            # 与被当作 +08 解释的 naive 列比较时整体偏移 8 小时（已实测）：
            #   - planned_at 在未来 8 小时内的工单被判「已超过计划时间」；
            #   - 24h 验证 SLA 实际 16h 即触发；
            #   - date_trunc('day', now()) 的日界落在 08:00 +08，凌晨时段「昨日基线」取到当天快照。
            # 会话时区设为 UTC 后，naive 列即按 UTC 解释，与写入口径一致。
            "timezone": "UTC",
            # 0930 泄漏兜底：idle in transaction 超过 5 分钟由服务端断开
            # （应用事务均为毫秒级；泄漏连接从「挂数小时」变成「5 分钟自清理」）
            "idle_in_transaction_session_timeout": "300000",
        },
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


# ---------------------------------------------------------------------------
# 毒化 session 关闭兜底（PG 连接风暴止血①，2026-10-13）
#
# 背景（诊断报告 PG连接风暴-诊断报告 §0.2/§5.1）：session 被并发误用后
# （SQLAlchemy PROVISIONING_CONNECTION 态 / asyncpg 在途操作未完成），
# 异常退出路径上的 rollback()/close() 会依次抛 InvalidRequestError
# （"Method 'close()' can't be called here; method '_connection_for_bind()'
# is already in progress"）。若就此放弃 session 对象，NullPool 下底层
# asyncpg 连接**永不归还** → 泄漏连接 idle-in-transaction 挂满
# max_connections（现场 100/100 全占满，每小时整点复现，PG 拒绝一切
# 新建连接 ~90s）。
#
# close 失败的根因之一是 asyncio.gather 异常上抛后兄弟协程仍在后台驱动
# 同一 session（gather 不撤销兄弟任务），句柄只存在于被弃协程中——延迟
# 重试等在途操作自然完成后 close 即可成功归还连接；重试耗尽则直接
# terminate 底层 asyncpg 连接（同步硬断 socket，服务端 backend 立即退出，
# 宁可显式断连也不泄漏）。注意红线：不使用任何模块级 asyncio 原语。
# ---------------------------------------------------------------------------
_SESSION_CLOSE_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0, 8.0)


def _extract_raw_asyncpg_connection(session: AsyncSession):
    """尽力从 AsyncSession 挖出底层 raw asyncpg Connection（无则 None）。

    链路：AsyncSession → sync_session.connection()（sync Connection）→
    .connection（pool 代理）→ .dbapi_connection（AsyncAdapt_asyncpg_connection）
    → ._connection（raw asyncpg Connection）。任何一环取不到都返回 None。
    """
    try:
        proxy = session.sync_session.connection().connection
        adapted = getattr(proxy, "dbapi_connection", None)
        raw = getattr(adapted, "_connection", None)
        return raw if raw is not None and hasattr(raw, "terminate") else None
    except Exception:  # noqa: BLE001 — 挖不到句柄属预期（如 provisioning 未完成）
        return None


async def close_session_guarded(session: AsyncSession, *, label: str = "session") -> None:
    """关闭 session，对"毒化 session"（close/rollback 抛错）做兜底回收。

    依次升级，全程不抛出（不掩盖调用方原始异常）：
    1. 立即 close（正常路径零额外开销）；
    2. 短延迟重试 close——gather 兄弟协程的在途查询通常 1~2s 内自然完成，
       之后 close 可正常归还连接（NullPool 即关闭）；
    3. 重试耗尽仍失败：terminate 底层 asyncpg 连接（同步硬断）；连句柄都
       取不到时仅记日志（该连接由服务端 idle-in-transaction 超时 300s /
       进程 GC 兜底回收——最后防线，不再无限等待）。
    """
    last_exc: Exception | None = None
    for delay in (0.0, *_SESSION_CLOSE_RETRY_DELAYS):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await session.close()
            if last_exc is not None:
                logger.info(
                    "毒化 %s 延迟重试 close 成功（连接已归还），此前失败: %s",
                    label,
                    last_exc,
                )
            return
        except Exception as exc:  # noqa: BLE001 — 兜底路径绝不掩盖原始异常
            last_exc = exc
    raw = _extract_raw_asyncpg_connection(session)
    if raw is not None:
        try:
            raw.terminate()
            logger.warning(
                "毒化 %s close 重试耗尽，已强制 terminate 底层连接（断连不泄漏），"
                "close 失败原因: %s",
                label,
                last_exc,
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception("毒化 %s 底层连接 terminate 失败", label)
    logger.warning(
        "毒化 %s close 重试耗尽且无法取得底层连接句柄（provisioning 从未完成，"
        "句柄在被弃协程中），该连接依赖服务端 idle 超时/GC 回收。close 失败原因: %s",
        label,
        last_exc,
    )
