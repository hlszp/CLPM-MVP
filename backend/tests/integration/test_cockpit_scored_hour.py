"""本机 PostgreSQL 回滚事务：驾驶舱选择最近有评分的小时快照。"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import engine
from app.models.loop import LoopLedger
from app.models.metric import KpiSnapshotHourly
from app.services import monitor

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("TEST_LOCAL_PG") != "1", reason="需 TEST_LOCAL_PG=1"),
]


@pytest.fixture
async def scored_hour_case(monkeypatch):
    assert engine.url.host in ("127.0.0.1", "localhost"), "只允许本机 PG 回滚事务"
    monkeypatch.setattr(
        monitor,
        "_load_aggregate_grading_thresholds",
        AsyncMock(return_value=monitor._DEFAULT_GRADE_THRESHOLDS),
    )
    prefix = f"QA-HOUR-{uuid4().hex[:10]}"
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                loops = {
                    name: LoopLedger(
                        id=str(uuid4()),
                        tag_name=f"{prefix}-{name}",
                        status="READY",
                        include_in_evaluation=name != "EXCLUDED",
                    )
                    for name in ("A", "ZERO", "EMPTY", "LONG", "EXCLUDED")
                }
                db.add_all(loops.values())
                await db.flush()

                def snap(name, end_hour, score, *, hours=1):
                    return KpiSnapshotHourly(
                        loop_id=loops[name].id,
                        ts_start=day + timedelta(hours=end_hour - hours),
                        ts_end=day + timedelta(hours=end_hour),
                        score=Decimal(str(score)) if score is not None else None,
                        status="SUCCESS" if score is not None else "INCONCLUSIVE",
                        confidence_level="A" if score is not None else "E",
                        steady_rate=Decimal("37.22") if name == "A" else Decimal("90"),
                        effective_auto_rate=Decimal("51.46"),
                    )

                db.add_all(
                    [
                        snap("A", -1, 80),
                        snap("A", 2, 7.18),
                        snap("A", 3, None),
                        snap("ZERO", -1, 40),
                        snap("ZERO", 3, 0),
                        snap("EMPTY", 3, None),
                        snap("LONG", -1, 50),
                        snap("LONG", 2, 65),
                        snap("LONG", 28, 99, hours=24),
                        snap("EXCLUDED", 3, 95),
                    ]
                )
                await db.flush()
                yield db, loops, prefix, day
        finally:
            await transaction.rollback()


async def test_latest_scored_hour_keeps_metrics_and_real_window(scored_hour_case):
    db, loops, prefix, day = scored_hour_case
    result = await monitor.list_loop_monitor(
        db,
        keyword=prefix,
        latest_scored_hour=True,
        with_aggregate=False,
    )
    items = {item["loopId"]: item for item in result["items"]}
    chosen = items[loops["A"].id]
    assert chosen["score"] == 7.18
    assert chosen["kpiSummary"]["steady_rate"] == 37.22
    assert chosen["confidenceLevel"] == "A"
    assert chosen["kpiSummary"]["tsStart"] == (day + timedelta(hours=1)).isoformat()
    assert chosen["kpiSummary"]["tsEnd"] == (day + timedelta(hours=2)).isoformat()
    assert items[loops["ZERO"].id]["score"] == 0
    assert items[loops["LONG"].id]["score"] == 65
    assert items[loops["EMPTY"].id]["kpiSummary"] is None
    assert items[loops["EXCLUDED"].id]["kpiSummary"] is None

    # 其他调用方不启用该选项时，仍按最新快照（即使无评分）展示。
    legacy = await monitor.list_loop_monitor(db, keyword=prefix, with_aggregate=False)
    old_item = next(item for item in legacy["items"] if item["loopId"] == loops["A"].id)
    assert old_item["score"] is None


async def test_scored_hour_filter_sort_and_focus_agree(scored_hour_case):
    db, loops, prefix, _ = scored_hour_case
    common = {"keyword": prefix, "latest_scored_hour": True, "with_aggregate": False}
    result = await monitor.list_loop_monitor(
        db,
        **common,
        sort_by="score",
        sort_order="desc",
        page_size=1,
        focus_loop_id=loops["ZERO"].id,
    )
    assert result["total"] == 5
    assert result["items"][0]["loopId"] == loops["LONG"].id
    assert (result["focusIndex"], result["focusPage"]) == (2, 3)
    poor = await monitor.list_loop_monitor(db, **common, grades="POOR")
    assert {item["loopId"] for item in poor["items"]} == {loops["A"].id, loops["ZERO"].id}
    zero = await monitor.list_loop_monitor(db, **common, min_score=0, max_score=1)
    assert [item["loopId"] for item in zero["items"]] == [loops["ZERO"].id]
    empty = await monitor.list_loop_monitor(db, **common, unscored=True)
    assert {item["loopId"] for item in empty["items"]} == {
        loops["EMPTY"].id,
        loops["EXCLUDED"].id,
    }
    degraded = await monitor.list_loop_monitor(db, **common, sort_by="scoreDelta")
    assert [item["loopId"] for item in degraded["items"][:3]] == [
        loops["A"].id,
        loops["ZERO"].id,
        loops["LONG"].id,
    ]


async def test_scored_hour_aggregate_uses_same_denominator(scored_hour_case):
    db, _, prefix, _ = scored_hour_case
    result = await monitor.list_loop_monitor(db, keyword=prefix, latest_scored_hour=True)
    aggregate = result["aggregate"]
    assert aggregate["scoredCount"] == 3
    assert aggregate["avgScore"] == 24.1
    assert aggregate["gradeCounts"]["POOR"] == 2
    assert aggregate["gradeCounts"]["INCONCLUSIVE"] == 2
    assert aggregate["worsenedCount"] == 2
