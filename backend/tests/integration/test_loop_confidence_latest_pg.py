"""loop_confidence_latest 真实 PG 集成测试.

验证 _persist_snapshot 在写 kpi_snapshot_hourly 的同时 UPSERT
loop_confidence_latest：两次写同一回路后表中只剩一条记录，且为最新值。

测试前提：
- 本地开发 PG 可达（默认读取 app settings 的 postgres_dsn，
  可用环境变量 TEST_DATABASE_URL 覆盖）

运行方式：
    cd backend && uv run pytest tests/integration/test_loop_confidence_latest_pg.py \
        -v -m integration --no-header

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.sql.dml import Insert

from app.models.metric import LoopConfidenceLatest
from app.services.result_ledger import append_result_record
from app.tasks.kpi_calc import ALGORITHM_VERSION, _persist_snapshot, _save_confidence_latest


def _database_url() -> str:
    """测试库连接串：优先 TEST_DATABASE_URL，否则取 app settings。"""
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    from app.core.config import settings

    return settings.postgres_dsn


def _pg_reachable() -> bool:
    """检查 PG 端口是否可达（socket 探测，不依赖额外驱动）。"""
    try:
        from app.core.config import settings

        with socket.create_connection((settings.POSTGRES_HOST, settings.POSTGRES_PORT), timeout=3):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="PG 不可达，跳过集成测试"),
]


class _InsertSignalSession:
    def __init__(self, session, event):
        self.session, self.event = session, event

    async def execute(self, statement):
        if isinstance(statement, Insert):
            self.event.set()
        return await self.session.execute(statement)


@pytest.fixture
async def pg_context():
    """真实 PG 会话：建表（checkfirst）+ 插入测试回路，用后清理。"""
    engine = create_async_engine(_database_url())
    loop_id = str(uuid4())
    tag_name = f"TEST-CONF-{loop_id[:8]}"
    async with engine.connect() as conn:
        await conn.run_sync(LoopConfidenceLatest.__table__.create, checkfirst=True)
        await conn.execute(
            text("INSERT INTO loop_ledger (id, tag_name, status) VALUES (:id, :tag_name, 'READY')"),
            {"id": loop_id, "tag_name": tag_name},
        )
        await conn.commit()

    session = AsyncSession(engine)
    try:
        yield session, loop_id
    finally:
        await session.close()
        async with engine.connect() as conn:
            await conn.execute(
                text("DELETE FROM calculation_result_record WHERE object_id = :id"),
                {"id": loop_id},
            )
            await conn.execute(
                text("DELETE FROM loop_confidence_latest WHERE loop_id = :id"),
                {"id": loop_id},
            )
            await conn.execute(
                text("DELETE FROM kpi_snapshot_hourly WHERE loop_id = :id"),
                {"id": loop_id},
            )
            await conn.execute(
                text("DELETE FROM loop_ledger WHERE id = :id"),
                {"id": loop_id},
            )
            await conn.commit()
        await engine.dispose()


class TestPersistSnapshotConfidenceLatestPg:
    """真实 PG：两次写同回路 → 只剩一条且为最新。"""

    @pytest.mark.parametrize("hours,expected_hours", [(0, 1), (1, 1), (2, 2)])
    async def test_atomic_window_guard(self, pg_context, hours, expected_hours):
        session, loop_id = pg_context
        start = datetime(2026, 10, 21)
        await _save_confidence_latest(
            session, loop_id, start, start + timedelta(hours=1), "SUCCESS", score=Decimal(10)
        )
        await session.commit()
        await _save_confidence_latest(
            session, loop_id, start, start + timedelta(hours=hours), "SUCCESS", score=Decimal(20)
        )
        await session.commit()
        row = (
            await session.execute(
                text("SELECT data_ts_end,score FROM loop_confidence_latest WHERE loop_id=:id"),
                {"id": loop_id},
            )
        ).one()
        assert row.data_ts_end == start + timedelta(hours=expected_hours)
        assert row.score == (Decimal(10) if hours == 0 else Decimal(20))

    async def test_older_writer_cannot_overwrite_concurrent_newer_commit(self, pg_context):
        """新窗持锁期间旧窗进入写入；提交后仍保留新窗，复现原两条 SQL 竞态。"""
        session, loop_id = pg_context
        start = datetime(2026, 10, 21)
        await _save_confidence_latest(
            session, loop_id, start, start + timedelta(hours=1), "SUCCESS", score=Decimal(10)
        )
        await session.commit()
        async with AsyncSession(session.bind) as newer, AsyncSession(session.bind) as older:
            await _save_confidence_latest(
                newer, loop_id, start, start + timedelta(hours=3), "SUCCESS", score=Decimal(30)
            )
            entering_insert = asyncio.Event()

            task = asyncio.create_task(
                _save_confidence_latest(
                    _InsertSignalSession(older, entering_insert),
                    loop_id,
                    start,
                    start + timedelta(hours=2),
                    "SUCCESS",
                    score=Decimal(20),
                )
            )
            try:
                await asyncio.wait_for(entering_insert.wait(), timeout=5)
                await newer.commit()
                await asyncio.wait_for(task, timeout=5)
                await older.commit()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        row = (
            await session.execute(
                text("SELECT data_ts_end,score FROM loop_confidence_latest WHERE loop_id=:id"),
                {"id": loop_id},
            )
        ).one()
        assert row.data_ts_end == start + timedelta(hours=3)
        assert row.score == Decimal(30)

    async def test_concurrent_ledger_retry_reuses_committed_record(self, pg_context):
        session, loop_id = pg_context
        kwargs = {
            "logical_run_id": str(uuid4()),
            "object_kind": "LOOP",
            "object_id": loop_id,
            "ts_start": datetime(2026, 10, 21),
            "ts_end": datetime(2026, 10, 21, 1),
            "algorithm_version": ALGORITHM_VERSION,
            "config_revision": "test:concurrent-retry",
        }
        async with AsyncSession(session.bind) as first, AsyncSession(session.bind) as retry:
            original = await append_result_record(first, **kwargs, payload={"score": 80})
            record_id = original.id
            entering_insert = asyncio.Event()
            task = asyncio.create_task(
                append_result_record(
                    _InsertSignalSession(retry, entering_insert), **kwargs, payload={"score": 99}
                )
            )
            try:
                await asyncio.wait_for(entering_insert.wait(), timeout=5)
                await first.commit()
                reused = await asyncio.wait_for(task, timeout=5)
                assert reused.id == record_id and reused.payload == {"score": 80}
                await retry.commit()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        count = (
            await session.execute(
                text("SELECT count(*) FROM calculation_result_record WHERE object_id=:id"),
                {"id": loop_id},
            )
        ).scalar_one()
        assert count == 1

    @pytest.mark.asyncio
    async def test_double_write_keeps_single_latest_row(self, pg_context) -> None:
        session, loop_id = pg_context
        ts_start = datetime(2026, 7, 4, 8, 0, 0, tzinfo=UTC).replace(tzinfo=None)
        ts_end = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC).replace(tzinfo=None)

        base_kwargs = {
            "db": session,
            "loop_id": loop_id,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "status": "SUCCESS",
            "good_value_rate": Decimal("96.80"),
            "auto_mode_rate": Decimal("90.00"),
            "steady_rate": Decimal("85.00"),
            "accuracy_rate": Decimal("80.00"),
            "algorithm_version": ALGORITHM_VERSION,
        }

        # 第一次写：score=80 / confidence=B
        await _persist_snapshot(
            **base_kwargs,
            score=Decimal("80.00"),
            confidence_level="B",
            valid_rate=Decimal("0.9000"),
            metrics_detail={"accuracy_rate": {"value": 80.0, "confidence": "B"}},
        )
        await session.commit()

        # 第二次写（同 loop_id 同窗口）：score=95 / confidence=A
        await _persist_snapshot(
            **base_kwargs,
            score=Decimal("95.00"),
            confidence_level="A",
            valid_rate=Decimal("0.9820"),
            metrics_detail={"accuracy_rate": {"value": 95.0, "confidence": "A"}},
        )
        await session.commit()

        # loop_confidence_latest 只剩一条，且为最新值
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT status, score, confidence_level, valid_rate, metrics, "
                        "data_ts_start, data_ts_end, algorithm_version "
                        "FROM loop_confidence_latest WHERE loop_id = :id"
                    ),
                    {"id": loop_id},
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "SUCCESS"
        assert float(row["score"]) == 95.0
        assert row["confidence_level"] == "A"
        assert abs(row["valid_rate"] - 0.982) < 1e-9
        assert row["metrics"] == {"accuracy_rate": {"value": 95.0, "confidence": "A"}}
        assert row["data_ts_start"] == ts_start
        assert row["data_ts_end"] == ts_end
        assert row["algorithm_version"] == ALGORITHM_VERSION

        # 主快照表同样只有一条（loop_id + ts_start 幂等覆盖）
        snap_count = (
            await session.execute(
                text("SELECT count(*) FROM kpi_snapshot_hourly WHERE loop_id = :id"),
                {"id": loop_id},
            )
        ).scalar_one()
        assert snap_count == 1

    @pytest.mark.asyncio
    async def test_inconclusive_without_metrics_detail(self, pg_context) -> None:
        """无子指标数据（INCONCLUSIVE）也写最新表，metrics 为空对象。"""
        session, loop_id = pg_context
        ts_start = datetime(2026, 7, 5, 8, 0, 0, tzinfo=UTC).replace(tzinfo=None)
        ts_end = datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC).replace(tzinfo=None)

        await _persist_snapshot(
            db=session,
            loop_id=loop_id,
            ts_start=ts_start,
            ts_end=ts_end,
            status="INCONCLUSIVE",
        )
        await session.commit()

        row = (
            (
                await session.execute(
                    text(
                        "SELECT status, score, metrics FROM loop_confidence_latest "
                        "WHERE loop_id = :id"
                    ),
                    {"id": loop_id},
                )
            )
            .mappings()
            .one()
        )
        assert row["status"] == "INCONCLUSIVE"
        assert row["score"] is None
        assert row["metrics"] == {}
