"""审计日志归档任务 (S4-E2)。

定期将超过保留期的审计日志从主表 ``sys_audit_log`` 移动到归档表
``sys_audit_log_archive``，控制主表数据量。

设计要点：
- Celery Beat 定时任务（每天凌晨 3 点触发）
- 批量归档（每批 1000 条），避免长事务锁表
- 归档表不存在时自动创建（CREATE TABLE IF NOT EXISTS）
- 失败时记录 ERROR 日志但不抛出异常（不影响其他任务）
- 使用 ``operated_at`` 字段作为归档判定依据（对应 sys_audit_log 表结构）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

# 每批归档条数
ARCHIVE_BATCH_SIZE = 1000

# 默认保留天数（超过此天数的日志归档）
DEFAULT_RETENTION_DAYS = 90


async def _ensure_archive_table(session: AsyncSession) -> None:
    """确保归档表存在，不存在则自动创建。

    归档表结构与 ``sys_audit_log`` 一致，附加 ``archived_at`` 字段记录归档时间。
    """
    stmt = text("""
        CREATE TABLE IF NOT EXISTS sys_audit_log_archive (
            id              UUID            PRIMARY KEY,
            operator        VARCHAR(50)     NOT NULL,
            operation_type  VARCHAR(50)     NOT NULL,
            target_type     VARCHAR(50),
            target_id       VARCHAR(36),
            before_value    TEXT,
            after_value     TEXT,
            operated_at     TIMESTAMP       NOT NULL,
            archived_at     TIMESTAMP       NOT NULL DEFAULT NOW()
        )
    """)
    await session.execute(stmt)


async def _archive_batch(
    session: AsyncSession,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """归档一批审计日志。

    使用 ``WITH ... DELETE ... RETURNING ... INSERT`` 将超过保留期的日志
    从主表删除并插入归档表，单条 SQL 完成移动操作。

    Args:
        session: 异步数据库会话
        retention_days: 保留天数，超过此天数的日志将被归档

    Returns:
        本批归档的条数
    """
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    # 使用 CTE + DELETE ... RETURNING ... INSERT 实现原子化移动
    stmt = text("""
        WITH archived AS (
            DELETE FROM sys_audit_log
            WHERE operated_at < :cutoff
            RETURNING
                id, operator, operation_type,
                target_type, target_id,
                before_value, after_value, operated_at
        )
        INSERT INTO sys_audit_log_archive
            (id, operator, operation_type,
             target_type, target_id,
             before_value, after_value, operated_at)
        SELECT
            id, operator, operation_type,
            target_type, target_id,
            before_value, after_value, operated_at
        FROM archived
    """)
    result = await session.execute(stmt, {"cutoff": cutoff})
    return result.rowcount or 0


async def _do_archive(retention_days: int) -> int:
    """执行归档的实际 async 逻辑。

    循环批量归档，直到某批归档数量小于批次大小（表示已无更多数据）。
    """
    from app.core.db import AsyncSessionLocal

    total_archived = 0
    while True:
        async with AsyncSessionLocal() as session:
            # 首次执行时确保归档表存在
            await _ensure_archive_table(session)
            count = await _archive_batch(session, retention_days)
            await session.commit()
            total_archived += count
            # 本批数量小于批次大小，说明已无更多待归档数据
            if count < ARCHIVE_BATCH_SIZE:
                break
    return total_archived


@celery_app.task(name="audit_archive", bind=True, base=AsyncTask)
def archive_audit_logs(
    self: AsyncTask,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict:
    """归档审计日志。

    将超过 ``retention_days`` 天的审计日志从 ``sys_audit_log`` 移动到
    ``sys_audit_log_archive`` 表。

    Args:
        retention_days: 保留天数，默认 90 天

    Returns:
        归档结果字典，包含归档条数和保留天数
    """
    logger.info(
        "审计日志归档任务开始, task_id=%s, retention_days=%d",
        self.request.id,
        retention_days,
    )
    start_time = datetime.now(UTC)
    try:
        total = self.run_async(_do_archive(retention_days))
        elapsed = (datetime.now(UTC) - start_time).total_seconds()
        logger.info(
            "审计日志归档完成: %d 条, 耗时 %.2f 秒",
            total,
            elapsed,
        )
        return {
            "archived": total,
            "retention_days": retention_days,
            "elapsed_seconds": round(elapsed, 2),
        }
    except Exception as exc:
        elapsed = (datetime.now(UTC) - start_time).total_seconds()
        logger.error(
            "审计日志归档失败: %s, 耗时 %.2f 秒",
            exc,
            elapsed,
        )
        return {
            "archived": 0,
            "retention_days": retention_days,
            "elapsed_seconds": round(elapsed, 2),
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Celery Beat 调度配置：每天凌晨 3 点执行
# ---------------------------------------------------------------------------

from celery.schedules import crontab  # noqa: E402

# 追加方式注册 Beat 任务（避免覆盖其他模块的 beat_schedule）
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["audit-archive-daily-3am"] = {
    "task": "audit_archive",
    "schedule": crontab(hour=3, minute=0),
}
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


__all__ = ["archive_audit_logs"]
