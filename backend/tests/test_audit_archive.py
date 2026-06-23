"""审计日志归档任务测试 (S4-E2)。

测试覆盖：
- 基本归档功能（mock DB）
- 批量归档（多批次循环）
- 失败处理（异常不抛出，返回 error 字段）
- 归档表自动创建
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.audit_archive import (
    ARCHIVE_BATCH_SIZE,
    DEFAULT_RETENTION_DAYS,
    _archive_batch,
    _ensure_archive_table,
    archive_audit_logs,
)

# ===========================================================================
# 辅助函数：构造 mock session
# ===========================================================================


def _make_mock_session(rowcount: int = 0) -> MagicMock:
    """构造 mock async session，execute 返回指定 rowcount。"""
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = rowcount
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ===========================================================================
# _archive_batch 单元测试
# ===========================================================================


class TestArchiveBatch:
    """测试 _archive_batch() 归档逻辑。"""

    @pytest.mark.asyncio
    async def test_archive_batch_returns_rowcount(self) -> None:
        """归档一批日志，返回正确的 rowcount。"""
        session = _make_mock_session(rowcount=500)
        count = await _archive_batch(session, retention_days=90)
        assert count == 500
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_archive_batch_zero_rows(self) -> None:
        """无待归档数据时返回 0。"""
        session = _make_mock_session(rowcount=0)
        count = await _archive_batch(session, retention_days=90)
        assert count == 0

    @pytest.mark.asyncio
    async def test_archive_batch_full_batch(self) -> None:
        """满批次返回 ARCHIVE_BATCH_SIZE。"""
        session = _make_mock_session(rowcount=ARCHIVE_BATCH_SIZE)
        count = await _archive_batch(session, retention_days=90)
        assert count == ARCHIVE_BATCH_SIZE

    @pytest.mark.asyncio
    async def test_archive_batch_uses_retention_days(self) -> None:
        """验证 retention_days 参数传递到 SQL。"""
        session = _make_mock_session(rowcount=0)
        await _archive_batch(session, retention_days=30)
        # 检查 execute 被调用，且参数包含 cutoff
        call_args = session.execute.call_args
        assert call_args is not None
        # 第二个位置参数是参数字典
        params = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs
        assert "cutoff" in params


# ===========================================================================
# _ensure_archive_table 单元测试
# ===========================================================================


class TestEnsureArchiveTable:
    """测试 _ensure_archive_table()。"""

    @pytest.mark.asyncio
    async def test_ensure_archive_table_executes(self) -> None:
        """确保归档表创建 SQL 被执行。"""
        session = _make_mock_session(rowcount=0)
        await _ensure_archive_table(session)
        session.execute.assert_awaited_once()


# ===========================================================================
# archive_audit_logs 任务测试
# ===========================================================================


class TestArchiveAuditLogsTask:
    """测试 archive_audit_logs Celery 任务。"""

    def test_task_basic_archive(self) -> None:
        """基本归档：单批归档 500 条后结束。"""
        mock_session = _make_mock_session(rowcount=500)

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = archive_audit_logs.run(
                retention_days=DEFAULT_RETENTION_DAYS
            )

        assert result["archived"] == 500
        assert result["retention_days"] == DEFAULT_RETENTION_DAYS
        assert "elapsed_seconds" in result

    def test_task_batch_archive_multiple_rounds(self) -> None:
        """批量归档：第一批满 1000，第二批 300，总计 1300。"""
        # 每轮循环调用 2 次 execute：ensure_archive_table + archive_batch
        # 第 1 轮：ensure(0) + archive(1000)，满批次继续
        # 第 2 轮：ensure(0) + archive(300)，不满批次结束
        results = [
            MagicMock(rowcount=0),  # ensure_archive_table 第 1 轮
            MagicMock(rowcount=ARCHIVE_BATCH_SIZE),  # archive_batch 第 1 轮
            MagicMock(rowcount=0),  # ensure_archive_table 第 2 轮
            MagicMock(rowcount=300),  # archive_batch 第 2 轮
        ]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=results)
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = archive_audit_logs.run(
                retention_days=DEFAULT_RETENTION_DAYS
            )

        assert result["archived"] == 1300
        # execute 被调用 4 次：2 次 ensure_archive_table + 2 次 archive_batch
        assert mock_session.execute.await_count == 4

    def test_task_no_data_to_archive(self) -> None:
        """无待归档数据时返回 0。"""
        mock_session = _make_mock_session(rowcount=0)

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = archive_audit_logs.run(
                retention_days=DEFAULT_RETENTION_DAYS
            )

        assert result["archived"] == 0
        assert result["retention_days"] == DEFAULT_RETENTION_DAYS

    def test_task_failure_returns_error_no_raise(self) -> None:
        """归档失败时不抛出异常，返回 error 字段。"""
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB 连接失败"))
        mock_session.commit = AsyncMock()

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            # 不应抛出异常
            result = archive_audit_logs.run(
                retention_days=DEFAULT_RETENTION_DAYS
            )

        assert result["archived"] == 0
        assert "error" in result
        assert "DB 连接失败" in result["error"]

    def test_task_custom_retention_days(self) -> None:
        """自定义保留天数。"""
        mock_session = _make_mock_session(rowcount=100)

        with patch("app.core.db.AsyncSessionLocal") as mock_factory:
            mock_factory.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

            result = archive_audit_logs.run(retention_days=30)

        assert result["retention_days"] == 30
        assert result["archived"] == 100


# ===========================================================================
# Beat 调度配置测试
# ===========================================================================


class TestBeatSchedule:
    """测试 Celery Beat 调度配置。"""

    def test_beat_schedule_contains_audit_archive(self) -> None:
        """Beat 调度中包含审计归档任务。"""
        from app.tasks.celery_app import celery_app

        beat_schedule = celery_app.conf.beat_schedule
        assert "audit-archive-daily-3am" in beat_schedule
        entry = beat_schedule["audit-archive-daily-3am"]
        assert entry["task"] == "audit_archive"

    def test_task_registered(self) -> None:
        """任务已注册到 Celery。"""
        from app.tasks.celery_app import celery_app

        assert "audit_archive" in celery_app.tasks
