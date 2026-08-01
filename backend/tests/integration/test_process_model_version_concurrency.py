"""V62-P3-004 process_model_version 并发一致性集成测试.

验证"同一回路至多一个 CURRENT"的三层防护：
1. 服务层 ``publish_model_version`` 原子退役旧 CURRENT + 发布新 CURRENT；
2. 数据库部分唯一索引 ``uk_process_model_version_current`` 拒绝双 CURRENT；
3. ``create_candidate_version`` version 号串行化分配。

测试前提：本地开发 PG 可达（TEST_DATABASE_URL 或 app settings）。
CI 默认 skip（-m "not integration"）。

运行方式：
    cd backend && uv run pytest tests/integration/test_process_model_version_concurrency.py \
        -v -m integration --no-header
"""

from __future__ import annotations

import os
import socket
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.loop import LoopLedger
from app.models.process_model_version import ProcessModelVersion
from app.services.process_model_version import (
    create_candidate_version,
    get_current_version,
    publish_model_version,
    retire_model_version,
)


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    from app.core.config import settings

    return settings.postgres_dsn


def _pg_reachable() -> bool:
    url = _database_url()
    host = url.split("@")[-1].split("/")[0].split(":")[0]
    try:
        port = int(url.split("@")[-1].split("/")[0].split(":")[1])
    except (IndexError, ValueError):
        return False
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.integration


@pytest.fixture
async def engine():
    if not _pg_reachable():
        pytest.skip("本地 PG 不可达，跳过并发一致性集成测试")
    eng = create_async_engine(_database_url(), echo=False)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def test_loop(session_factory):
    """创建临时测试回路，测试结束清理."""
    loop_id = str(uuid4())
    async with session_factory() as db:
        db.add(
            LoopLedger(
                id=loop_id,
                tag_name=f"TEST-PMV-{loop_id[:8]}",
                status="READY",
                importance_level=2,
                include_in_evaluation=True,
            )
        )
        await db.commit()
    yield loop_id
    # 清理：删除回路会级联删除 process_model_version（ON DELETE CASCADE）
    async with session_factory() as db:
        await db.execute(text("DELETE FROM loop_ledger WHERE id = :lid"), {"lid": loop_id})
        await db.commit()


async def test_create_candidate_assigns_monotonic_version(session_factory, test_loop):
    """create_candidate_version 应分配单回路单调递增的 version 号."""
    async with session_factory() as db:
        v1 = await create_candidate_version(
            db,
            loop_id=test_loop,
            model_type="FOPDT",
            model_params={"K": 1.0, "tau": 10.0, "theta": 2.0},
            identify_method="HISTORICAL_ARX",
            created_by="test",
        )
        v2 = await create_candidate_version(
            db,
            loop_id=test_loop,
            model_type="FOPDT",
            model_params={"K": 1.1, "tau": 11.0, "theta": 3.0},
            identify_method="HISTORICAL_ARX",
            created_by="test",
        )
        await db.commit()
        assert v1.version == 1
        assert v2.version == 2
        assert v1.status == "CANDIDATE"
        assert v2.status == "CANDIDATE"


async def test_publish_retires_old_current_and_sets_supersedes(session_factory, test_loop):
    """publish 应原子退役旧 CURRENT + 发布新 CURRENT + 回填 supersedes 链."""
    async with session_factory() as db:
        v1 = await create_candidate_version(
            db,
            loop_id=test_loop,
            model_type="FOPDT",
            model_params={"K": 1.0, "tau": 10.0, "theta": 2.0},
            created_by="test",
        )
        await db.commit()

        # 发布 v1 为 CURRENT
        published_v1 = await publish_model_version(
            db, version_id=str(v1.id), published_by="engineer_a"
        )
        await db.commit()
        assert published_v1.status == "CURRENT"
        assert published_v1.published_by == "engineer_a"
        assert published_v1.published_at is not None

        # 创建并发布 v2，应退役 v1
        v2 = await create_candidate_version(
            db,
            loop_id=test_loop,
            model_type="FOPDT",
            model_params={"K": 1.1, "tau": 11.0, "theta": 3.0},
            created_by="test",
        )
        await db.commit()

        published_v2 = await publish_model_version(
            db, version_id=str(v2.id), published_by="engineer_b"
        )
        await db.commit()

        assert published_v2.status == "CURRENT"
        assert published_v2.supersedes_version_id == str(v1.id)

        # v1 应已退役
        await db.refresh(published_v1)
        assert published_v1.status == "RETIRED"
        assert published_v1.retired_by == "engineer_b"
        assert published_v1.retired_at is not None
        assert "superseded by v2" in (published_v1.retired_reason or "")


async def test_partial_unique_index_rejects_double_current(session_factory, test_loop):
    """部分唯一索引应拒绝同回路双 CURRENT（数据库层最后防线）."""
    async with session_factory() as db:
        # 直接 INSERT 两条 CURRENT（绕过服务层）
        for i in (1, 2):
            db.add(
                ProcessModelVersion(
                    id=str(uuid4()),
                    loop_id=test_loop,
                    version=i,
                    status="CURRENT",
                    model_type="FOPDT",
                    model_params={"K": 1.0, "tau": 10.0, "theta": 2.0},
                )
            )
        with pytest.raises(IntegrityError) as exc_info:
            await db.commit()
        # PostgreSQL 唯一约束违反
        assert "uk_process_model_version_current" in str(exc_info.value).lower() or (
            "unique" in str(exc_info.value).lower()
        )


async def test_publish_non_candidate_rejected(session_factory, test_loop):
    """publish 非 CANDIDATE 版本应报错（已发布版本不能重复发布）."""
    async with session_factory() as db:
        v1 = await create_candidate_version(
            db,
            loop_id=test_loop,
            model_type="FOPDT",
            model_params={"K": 1.0, "tau": 10.0, "theta": 2.0},
            created_by="test",
        )
        await db.commit()
        await publish_model_version(db, version_id=str(v1.id), published_by="eng")
        await db.commit()

        # 重复发布已 CURRENT 的版本
        with pytest.raises(Exception) as exc_info:
            await publish_model_version(db, version_id=str(v1.id), published_by="eng2")
        await db.rollback()
        assert "NOT_CANDIDATE" in str(exc_info.value) or "CANDIDATE" in str(exc_info.value)


async def test_get_current_version_returns_at_most_one(session_factory, test_loop):
    """get_current_version 应返回至多一个 CURRENT."""
    async with session_factory() as db:
        # 无 CURRENT 时返回 None
        assert await get_current_version(db, test_loop) is None

        v1 = await create_candidate_version(
            db,
            loop_id=test_loop,
            model_type="FOPDT",
            model_params={"K": 1.0, "tau": 10.0, "theta": 2.0},
            created_by="test",
        )
        await db.commit()
        await publish_model_version(db, version_id=str(v1.id), published_by="eng")
        await db.commit()

        current = await get_current_version(db, test_loop)
        assert current is not None
        assert str(current.id) == str(v1.id)
        assert current.status == "CURRENT"


async def test_retire_current_without_replacement(session_factory, test_loop):
    """retire_model_version 应将 CURRENT 退役，回路进入无 CURRENT 状态."""
    async with session_factory() as db:
        v1 = await create_candidate_version(
            db,
            loop_id=test_loop,
            model_type="FOPDT",
            model_params={"K": 1.0, "tau": 10.0, "theta": 2.0},
            created_by="test",
        )
        await db.commit()
        await publish_model_version(db, version_id=str(v1.id), published_by="eng")
        await db.commit()

        retired = await retire_model_version(
            db, version_id=str(v1.id), reason="模型失效，待重新辨识", retired_by="eng"
        )
        await db.commit()
        assert retired.status == "RETIRED"
        assert retired.retired_reason == "模型失效，待重新辨识"
        assert await get_current_version(db, test_loop) is None


async def test_publish_different_loops_independent(session_factory, test_loop):
    """不同回路的 CURRENT 互不影响（部分唯一索引按 loop_id 分组）."""
    loop2_id = str(uuid4())
    async with session_factory() as db:
        # 创建第二个测试回路
        db.add(
            LoopLedger(
                id=loop2_id,
                tag_name=f"TEST-PMV2-{loop2_id[:8]}",
                status="READY",
                importance_level=2,
                include_in_evaluation=True,
            )
        )
        await db.commit()
        try:
            v1 = await create_candidate_version(
                db,
                loop_id=test_loop,
                model_type="FOPDT",
                model_params={"K": 1.0, "tau": 10.0, "theta": 2.0},
                created_by="test",
            )
            v2 = await create_candidate_version(
                db,
                loop_id=loop2_id,
                model_type="FOPDT",
                model_params={"K": 2.0, "tau": 20.0, "theta": 4.0},
                created_by="test",
            )
            await db.commit()

            # 两个回路各自发布 CURRENT，互不冲突
            await publish_model_version(db, version_id=str(v1.id), published_by="eng")
            await publish_model_version(db, version_id=str(v2.id), published_by="eng")
            await db.commit()

            assert (await get_current_version(db, test_loop)) is not None
            assert (await get_current_version(db, loop2_id)) is not None
        finally:
            await db.execute(text("DELETE FROM loop_ledger WHERE id = :lid"), {"lid": loop2_id})
            await db.commit()
