"""本机 PG 回滚事务：批量锚点先筛选适用性，再取各点最新状态。"""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import engine
from app.models.point_history import PointStateAnchor
from app.models.tag import TagRegistry
from app.services.data_source import point_history_metadata as metadata

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("TEST_LOCAL_PG") != "1", reason="需 TEST_LOCAL_PG=1"),
]


@pytest.mark.parametrize("case", ["future", "expired", "right_boundary", "at_window_start"])
async def test_batch_anchor_matches_single_point_causal_selection(monkeypatch, case):
    assert engine.url.host in ("127.0.0.1", "localhost"), "只允许本机 PG 回滚事务"
    point_id = str(uuid4())
    start = datetime(2026, 10, 21, 1, tzinfo=UTC)
    previous = start - timedelta(hours=1)
    latest = start - timedelta(minutes=10)
    cases = {
        "future": [(previous, None), (start + timedelta(hours=1), None)],
        "expired": [(previous, None), (latest, start - timedelta(seconds=1))],
        "right_boundary": [(latest, start)],
        "at_window_start": [(previous, None), (start, None)],
    }
    expected = {
        "future": previous,
        "expired": previous,
        "right_boundary": None,
        "at_window_start": start,
    }[case]
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                db.add(
                    TagRegistry(
                        id=point_id,
                        tag_name=f"QA-ANCHOR-{point_id}",
                        tag_type="PV",
                        last_sync_at=start.replace(tzinfo=None),
                    )
                )
                await db.flush()
                db.add_all(
                    PointStateAnchor(
                        point_id=point_id,
                        anchor_ts=at,
                        value=float(i),
                        quality_class=1,
                        coverage_until=until,
                    )
                    for i, (at, until) in enumerate(cases[case])
                )
                await db.flush()
                # 只替换会话工厂；被测函数仍执行真实 DISTINCT ON SQL。
                own_sessions = []

                def short_session():
                    session = AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    )
                    own_sessions.append(session)
                    return session

                monkeypatch.setattr("app.core.db.AsyncSessionLocal", short_session)
                result = await metadata.applicable_anchors_batch(
                    db, point_ids=[point_id], window_start=start
                )
                single = await metadata.applicable_anchor(db, point_id=point_id, window_start=start)
                actual = result.get(point_id)
                assert len(own_sessions) == 1
                assert (actual.anchor_ts if actual else None) == expected
                assert (single.anchor_ts if single else None) == expected
        finally:
            await transaction.rollback()
