"""P3-02 真实 PG 集成回归：批次幂等唯一约束 + checkpoint + 取消审计 + 终结统计.

与 mock 单测（tests/test_p3_02_task_idempotency.py）的区别：本文件在真实
PostgreSQL 上验证——

1. **唯一约束**：同 (task_id, loop_id) 第二条插入被数据库拒绝
   （IntegrityError）——重试/重投递不重复 run 的硬保证；
2. **迁移去重 SQL 语义**：存量同键多行按 created_at 最新保留、较早行
   task_id 置 NULL（迁移 p302task01 同款 SQL）；
3. **checkpoint 真实链路**：l1 已 SUCCESS 跳过（不重复 run）、l2 遗留
   FAILED 脱链重做，任务名下每回路恰一条挂链 run；
4. **取消审计行 + 终结统计**：取消审计行 FAILED+cancelled 元信息落库，
   summarize_task 按真实行算出六类守恒。

安全边界：**必须显式提供 ``TEST_DATABASE_URL``**（指向一次性 scratch 库），
否则跳过——避免误写共享 dev 库（P4-02 教训：双确认 POSTGRES_PORT/DB）。

运行方式（实验/验收时）：
    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_p302_scratch \
    uv run pytest tests/integration/test_p3_02_task_idempotency_real_pg.py -v -m integration
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.diagnosis_run import DiagnosisRun
from app.models.loop import LoopLedger
from app.services.diagnosis_batch_accounting import summarize_task


def _database_url() -> str:
    return os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable() -> bool:
    if not _database_url():
        return False
    try:
        with socket.create_connection(("localhost", 17102), timeout=3):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="未显式提供 TEST_DATABASE_URL（scratch PG）"),
]


@pytest.fixture
async def pg_context():
    """真实 PG（scratch 库）：建表 + 两条测试回路。收尾清行。"""
    import app.models  # noqa: F401 —— 注册全部 ORM 表
    from app.models.base import Base

    engine = create_async_engine(_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    loop_ids: list[str] = []
    async with session_factory() as session:
        for _ in range(2):
            raw = uuid4()
            session.add(
                LoopLedger(
                    id=raw,
                    tag_name=f"TIC-P302-{uuid4().hex[:6]}",
                    loop_type="FLOW",
                    is_active=True,
                )
            )
            loop_ids.append(str(raw))
            # 注：逐行提交——asyncpg 多行 insertmanyvalues 与 UUID(as_uuid=False)
            # 主键哨兵不兼容（KeyError），单行插入不走该路径
            await session.commit()

    try:
        yield {"engine": engine, "session_factory": session_factory, "loop_ids": loop_ids}
    finally:
        async with session_factory() as session:
            runs = (await session.execute(select(DiagnosisRun))).scalars().all()
            for r in runs:
                await session.delete(r)
            await session.flush()
            for lid in loop_ids:
                row = await session.get(LoopLedger, lid)
                if row is not None:
                    await session.delete(row)
            await session.commit()
        await engine.dispose()


def _meta(judgement: str | None = None, *, cancelled: bool = False) -> dict:
    meta: dict = {}
    if judgement:
        meta["judgementStatus"] = judgement
    if cancelled:
        meta["cancelled"] = True
    return meta


def _new_run(task_id: str, loop_id: str, status: str, meta: dict | None = None) -> DiagnosisRun:
    now = datetime.utcnow()
    return DiagnosisRun(
        id=str(uuid4()),
        task_id=task_id,
        loop_id=loop_id,
        triggered_by="tester",
        trigger_type="MANUAL",
        time_window_start=now - timedelta(hours=1),
        time_window_end=now,
        status=status,
        assessment_meta=meta,
        algorithm_version="MVP_DIAG_V2_v1.0",
        started_at=now,
        finished_at=now,
        duration_ms=1,
    )


class TestUniqueConstraint:
    async def test_duplicate_task_loop_rejected(self, pg_context) -> None:
        """同 (task_id, loop_id) 第二条插入被 DB 拒绝（幂等硬保证）。"""
        sf = pg_context["session_factory"]
        task_id, loop_id = str(uuid4()), pg_context["loop_ids"][0]
        async with sf() as session:
            session.add(_new_run(task_id, loop_id, "SUCCESS", _meta("VALID_NEGATIVE")))
            await session.commit()
        async with sf() as session:
            session.add(_new_run(task_id, loop_id, "FAILED"))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
        # task_id=NULL 的历史行不受约束（PG 唯一约束 NULL 互不相等）
        async with sf() as session:
            session.add(_new_run(None, loop_id, "FAILED"))
            await session.commit()

    async def test_migration_dedup_sql_semantics(self, pg_context) -> None:
        """迁移 p302task01 同款去重 SQL：保留最新、较早行脱链（不删证据）。

        scratch 建表自带新唯一约束；为模拟「迁移前存量重复」，先落约束→
        插重复→跑迁移同款去重 SQL→重建约束（去重成功则重建通过）。
        """
        sf = pg_context["session_factory"]
        engine = pg_context["engine"]
        task_id, loop_id = str(uuid4()), pg_context["loop_ids"][1]
        old = _new_run(task_id, loop_id, "SUCCESS")
        old.created_at = datetime(2026, 10, 1, 0, 0, 0)
        new = _new_run(task_id, loop_id, "SUCCESS")
        new.created_at = datetime(2026, 10, 10, 0, 0, 0)
        async with engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(
                text("ALTER TABLE diagnosis_run DROP CONSTRAINT uq_diagnosis_run_task_loop")
            )
        async with sf() as session:
            session.add(old)
            await session.commit()  # 逐行提交（asyncpg insertmanyvalues 哨兵兼容）
            session.add(new)
            await session.commit()

        # 与迁移 upgrade() 中的去重 SQL 完全一致
        async with engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(
                text(
                    """
                    UPDATE diagnosis_run AS dr
                    SET task_id = NULL
                    WHERE dr.task_id IS NOT NULL
                      AND EXISTS (
                        SELECT 1 FROM diagnosis_run AS newer
                        WHERE newer.task_id = dr.task_id
                          AND newer.loop_id = dr.loop_id
                          AND (newer.created_at, newer.id) > (dr.created_at, dr.id)
                      )
                    """
                )
            )
            # 去重后重建唯一约束（迁移同款）；若仍有重复将在此失败
            await conn.execute(
                text(
                    "ALTER TABLE diagnosis_run "
                    "ADD CONSTRAINT uq_diagnosis_run_task_loop "
                    "UNIQUE (task_id, loop_id)"
                )
            )
        async with sf() as session:
            rows = (
                (
                    await session.execute(
                        select(DiagnosisRun).where(
                            DiagnosisRun.task_id == task_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1  # 仅最新行挂链
            assert rows[0].created_at == new.created_at


class TestCheckpointRealPg:
    async def test_retry_checkpoint_and_redo(self, pg_context) -> None:
        """重投递重跑：l1 已 SUCCESS 跳过；l2 FAILED 脱链重做；每回路恰一条挂链 run。"""
        from app.tasks import diagnosis_v2 as dv

        sf = pg_context["session_factory"]
        l1, l2 = pg_context["loop_ids"]
        task_id = str(uuid4())
        # 上一尝试遗留：l1 SUCCESS（已完成）、l2 FAILED（异常留痕）
        async with sf() as session:
            session.add(_new_run(task_id, l1, "SUCCESS", _meta("VALID_NEGATIVE")))
            await session.commit()  # 逐行提交（asyncpg insertmanyvalues 哨兵兼容）
            session.add(_new_run(task_id, l2, "FAILED"))
            await session.commit()

        async def _fake_diag(db, loop_id, **kw):
            assert loop_id == l2  # checkpoint 只放行 l2
            run = _new_run(kw["task_id"], loop_id, "SUCCESS", _meta("VALID_NEGATIVE"))
            db.add(run)
            await db.commit()
            return run

        task_record = {
            "task_id": task_id,
            "status": "SUCCESS",
            "loop_ids": json.dumps([l1, l2]),
        }
        with (
            patch.object(dv, "AsyncSessionLocal", sf),
            patch.object(dv, "run_diagnosis_for_loop", AsyncMock(side_effect=_fake_diag)),
            patch.object(dv, "update_status", AsyncMock()),
            patch.object(dv, "get_task_status", AsyncMock(return_value="RUNNING")),
            patch.object(dv, "_converge_running_placeholders", AsyncMock()),
            patch.object(dv, "get_task", AsyncMock(return_value=task_record)),
            patch.object(dv, "set_terminal_stats", AsyncMock()),
        ):
            result = await dv._do_run_batch(
                [l1, l2],
                "2026-10-11T00:00:00",
                "2026-10-11T01:00:00",
                task_id,
                "full",
                "user",
            )

        assert result["ok"] == 1
        assert result["resumedDone"] == 1
        # 每回路恰一条挂链 run；旧 FAILED 行脱链保留（不删证据）
        async with sf() as session:
            attached = (
                (await session.execute(select(DiagnosisRun).where(DiagnosisRun.task_id == task_id)))
                .scalars()
                .all()
            )
            assert sorted(r.loop_id for r in attached) == sorted([l1, l2])
            detached = (
                (
                    await session.execute(
                        select(DiagnosisRun).where(
                            DiagnosisRun.loop_id == l2, DiagnosisRun.task_id.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(detached) == 1 and detached[0].status == "FAILED"
        # 终结统计（真实行）：2 可判定，六类守恒
        stats = result.get("terminalStats") or {}
        assert stats["terminal"]["determinable"] == 2
        assert stats["invariantObjects"] is True


class TestCancelledAuditRealPg:
    async def test_cancelled_audit_row_and_accounting(self, pg_context) -> None:
        """取消审计行落库（FAILED+cancelled+NOT_ASSESSED）+ 真实行终结统计。"""
        from app.tasks import diagnosis_v2 as dv

        sf = pg_context["session_factory"]
        l1, l2 = pg_context["loop_ids"]
        task_id = str(uuid4())
        gen_actions = AsyncMock()
        with (
            patch.object(dv, "AsyncSessionLocal", sf),
            patch.object(dv, "generate_system_actions_best_effort", gen_actions),
        ):
            await dv._record_cancelled_run(
                l1, datetime(2026, 10, 11), datetime(2026, 10, 11, 1), task_id, "user"
            )
        gen_actions.assert_not_awaited()  # 取消审计行不生成建议
        async with sf() as session:
            rows = (
                (await session.execute(select(DiagnosisRun).where(DiagnosisRun.task_id == task_id)))
                .scalars()
                .all()
            )
            assert len(rows) == 1
            row = rows[0]
            assert row.status == "FAILED"
            assert row.assessment_meta["cancelled"] is True
            assert row.assessment_meta["judgementStatus"] == "NOT_ASSESSED"

        # 终结统计（真实 session 读行）：l1 取消（审计行）+ l2 未开始
        # （任务 CANCELLED 兜底）→ cancelled=2，不计失败
        async with sf() as session:
            stats = await summarize_task(
                session,
                task_record={
                    "task_id": task_id,
                    "status": "CANCELLED",
                    "loop_ids": json.dumps([l1, l2]),
                },
            )
        assert stats["terminal"]["cancelled"] == 2
        assert stats["terminal"]["failed"] == 0  # 取消不计失败一次
        assert stats["invariantObjects"] is True
