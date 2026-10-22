"""真实本机 PG 事务验证同窗两任务各有结果，结束时回滚全部测试写入。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.data_types import RawTimeSeries
from app.core.db import engine
from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger
from app.services import diagnosis_orchestrator as orchestrator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("TEST_LOCAL_PG") != "1", reason="需显式 TEST_LOCAL_PG=1"),
]


async def test_same_window_independent_tasks_remain_queryable(monkeypatch):
    assert engine.url.host in ("127.0.0.1", "localhost"), "只允许本机 PG 回滚事务"
    end = datetime.now(UTC).replace(tzinfo=None)
    start = end - timedelta(hours=1)
    n = 3600
    raw = RawTimeSeries(
        timestamps=[start + timedelta(seconds=i) for i in range(n)],
        signals={"pv": [50.0] * n, "sp": [50.0] * n, "op": [50.0] * n, "mode": [1] * n},
        quality_codes={"pv_quality": [1] * n},
    )

    class Provider:
        def make_query_fn(self, db):
            return AsyncMock(return_value=raw)

    monkeypatch.setattr(orchestrator, "get_provider", Provider)
    # 本项只验证持久结果归属；外部事务不能回滚文件，故不写测试快照文件。
    monkeypatch.setattr(orchestrator, "_save_diagnosis_raw_snapshot", AsyncMock(return_value=None))
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            ) as session:
                loop = (
                    await session.execute(select(LoopLedger).where(LoopLedger.is_active).limit(1))
                ).scalar_one()
                task_ids = [str(uuid4()), str(uuid4())]
                runs = []
                for task_id in task_ids:
                    runs.append(
                        await orchestrator.run_diagnosis_for_loop(
                            session,
                            str(loop.id),
                            start=start,
                            end=end,
                            task_id=task_id,
                            triggered_by="qa1043",
                        )
                    )
                assert runs[0].id != runs[1].id
                assert [run.task_id for run in runs] == task_ids
                for task_id, expected in zip(task_ids, runs, strict=True):
                    # 与结果列表接口相同的 task_id 精确条件，必须各返回自己的行。
                    actual = (
                        await session.execute(
                            select(DiagnosisRun).where(DiagnosisRun.task_id == task_id)
                        )
                    ).scalar_one()
                    assert actual.id == expected.id
        finally:
            await transaction.rollback()
