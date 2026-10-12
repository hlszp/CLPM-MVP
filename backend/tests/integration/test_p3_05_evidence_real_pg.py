"""P3-05 真实 PG 集成回归：实施记录与验证证据 schema 落库语义（DEC-06）.

与 mock 单测（tests/test_p3_05_implementation_evidence.py）的区别：本文件在
真实 PostgreSQL 上验证模型/迁移落库行为——

1. 迁移列存在性：implemented_at / closure_evidence / actual_rollback_confirmed /
   rollback_confirmed_at / rollback_confirmed_by / rollback_note + CHECK 约束
   （p305evd001，与单测 create_all 路径互补——本文件直走迁移后的表）；
2. 闭环证据三分类 CHECK 反例：非法 closure_evidence 值被 PG 拒绝
   （三分类以外的值（含旧两分类设想）不能落库）；
3. actual_rollback_confirmed 服务端默认 false（未确认不冒称已回退）；
4. 存量行零回填语义：仅按旧必填列插入 → 新列 NULL/false（历史记录"未知/
   未确认"，不伪填）。

安全边界：**必须显式提供 ``TEST_DATABASE_URL``**（指向一次性 scratch 库），
否则跳过——避免误写共享 dev 库。

运行方式（实验/验收时）：
    cd backend
    TEST_DATABASE_URL=postgresql+asyncpg://clpm:...@localhost:17102/clpm_p305_scratch \
        uv run pytest tests/integration/test_p3_05_evidence_real_pg.py -v -m integration

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import os
import socket
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger


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


async def _make_loop(db: AsyncSession) -> str:
    loop = LoopLedger(
        id=str(uuid4()),
        tag_name=f"TIC-P305-{uuid4().hex[:8]}",
        description="P3-05 集成测试回路",
    )
    db.add(loop)
    await db.flush()
    return str(loop.id)


async def _insert_order(
    db: AsyncSession,
    *,
    loop_id: str,
    closure_evidence: str | None,
) -> None:
    db.add(
        HandlingOrder(
            id=str(uuid4()),
            order_no=f"HD-P305-{uuid4().hex[:12]}",
            loop_id=loop_id,
            source="MANUAL",
            suggestion_ids=None,
            title="P3-05 集成测试工单",
            action_type="TUNING",
            implemented_at=datetime(2026, 8, 11, 6, 0, 0) if closure_evidence else None,
            closure_evidence=closure_evidence,
            status="CLOSED" if closure_evidence else "PENDING",
        )
    )
    await db.flush()


class TestP305EvidenceSchemaRealPg:
    async def test_columns_and_defaults_present_after_migration(self) -> None:
        """迁移后 6 列 + CHECK 存在；旧行插入新列零回填（NULL/false）。"""
        engine = create_async_engine(_database_url())
        try:
            async with engine.begin() as conn:
                cols = {
                    r[0]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name = 'handling_order'"
                            )
                        )
                    ).all()
                }
                expected = {
                    "implemented_at",
                    "closure_evidence",
                    "actual_rollback_confirmed",
                    "rollback_confirmed_at",
                    "rollback_confirmed_by",
                    "rollback_note",
                }
                assert expected <= cols, f"缺列: {expected - cols}"
                cons = {
                    r[0]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT conname FROM pg_constraint "
                                "WHERE conrelid = 'handling_order'::regclass"
                            )
                        )
                    ).all()
                }
                assert "ck_handling_order_closure_evidence" in cons
                # 服务端默认 false
                default = (
                    await conn.execute(
                        text(
                            "SELECT column_default FROM information_schema.columns "
                            "WHERE table_name='handling_order' "
                            "AND column_name='actual_rollback_confirmed'"
                        )
                    )
                ).scalar()
                assert default is not None and "false" in default
        finally:
            await engine.dispose()

    async def test_legacy_row_insert_leaves_new_columns_honest(self) -> None:
        """存量形态插入：新列 NULL/false——历史记录"未知/未确认"，不伪填。"""
        engine = create_async_engine(_database_url())
        try:
            async with AsyncSession(engine) as db:
                loop_id = await _make_loop(db)
                order_id = str(uuid4())
                await db.execute(
                    text(
                        "INSERT INTO handling_order (id, order_no, loop_id, source, "
                        "title, action_type, status, created_at, updated_at) "
                        "VALUES (:id, :no, :loop, 'MANUAL', '旧形态工单', 'TUNING', "
                        "'CLOSED', NOW(), NOW())"
                    ),
                    {"id": order_id, "no": f"HD-P305-{uuid4().hex[:12]}", "loop": loop_id},
                )
                row = (
                    await db.execute(
                        text(
                            "SELECT implemented_at, closure_evidence, "
                            "actual_rollback_confirmed FROM handling_order "
                            "WHERE id = :id"
                        ),
                        {"id": order_id},
                    )
                ).one()
                assert row.implemented_at is None
                assert row.closure_evidence is None
                assert row.actual_rollback_confirmed is False
                await db.rollback()  # scratch 零残留
        finally:
            await engine.dispose()

    async def test_closure_evidence_check_rejects_invalid_values(self) -> None:
        """三分类 CHECK 反例：非法值（旧两分类设想 'MANUAL'/'DATA'）落库被拒。"""
        from sqlalchemy.exc import IntegrityError

        engine = create_async_engine(_database_url())
        try:
            async with AsyncSession(engine) as db:
                loop_id = await _make_loop(db)
                await db.commit()  # 先落库回路——回滚坏行时 FK 依赖不丢失
                for bad in ("MANUAL", "DATA", "AUTO_VERIFIED"):
                    with pytest.raises(IntegrityError, match="ck_handling_order_closure_evidence"):
                        await _insert_order(db, loop_id=loop_id, closure_evidence=bad)
                        await db.commit()
                    await db.rollback()
                # 三分类合法值全部可落
                for good in (
                    "MANUAL_CONFIRMATION",
                    "DATA_VERIFIED",
                    "COMPARABLE_CONDITION_VERIFIED",
                ):
                    await _insert_order(db, loop_id=loop_id, closure_evidence=good)
                    await db.commit()
        finally:
            await engine.dispose()
