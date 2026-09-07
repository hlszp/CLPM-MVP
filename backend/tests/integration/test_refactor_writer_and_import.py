"""P2 集成测试：PointHistoryWriter 与导入点级路径（真实 TDengine + PostgreSQL）.

运行（隔离环境）：
    cd backend && uv run pytest tests/integration/ -k writer_and_import -m integration -q

覆盖：
- writer 端到端：事件落 st_point_data_v1、覆盖段 confirmed、gap 段登记；
- V07：同 ts 不同 payload → 冲突登记（PG）+ 既有事实保留（TD）；
- 导入点级：shadow 双写（宽表+点表）、point+overwrite 显式拒绝；
- 计数单位：imported_count = 时间槽（不随布局变化）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.models.point_history import HistoryCoverageSegment, HistoryPointConflict
from app.services.data_source import point_history_repository as repo
from app.services.data_source import point_history_writer as pw

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 9, tzinfo=UTC)


@pytest.fixture
async def point_in_pg():
    """每测试独立的点身份（避免跨测试数据污染）。"""
    pid = str(uuid.uuid4())
    from app.core.db import AsyncSessionLocal
    from app.models.tag import TagRegistry

    async with AsyncSessionLocal() as db:
        db.add(
            TagRegistry(
                id=pid,
                tag_name=f"REF_W_{pid[:8]}",
                tag_type="PV",
                last_sync_at=datetime(2026, 9, 9),
                is_linked=True,
            )
        )
        await db.commit()
    yield pid
    async with AsyncSessionLocal() as db:
        await db.execute(delete(HistoryPointConflict).where(HistoryPointConflict.point_id == pid))
        await db.execute(
            delete(HistoryCoverageSegment).where(HistoryCoverageSegment.point_id == pid)
        )
        await db.execute(delete(TagRegistry).where(TagRegistry.id == pid))
        await db.commit()


@pytest.fixture(autouse=True)
async def td_ready():
    await repo.ensure_schema()


def _event(pid: str, ts: datetime, value, qclass=1, qraw=1) -> repo.PointEvent:
    return repo.PointEvent(
        point_id=pid,
        ts=ts,
        value=value,
        quality_class=qclass,
        quality_raw=qraw,
        quality_schema=1,
        source_kind=1,
        received_at=T0,
        source_id="it",
    )


class TestWriterEndToEnd:
    async def test_events_written_and_coverage_confirmed(self, point_in_pg):
        pid = point_in_pg
        tag = f"REF_W_{pid[:8]}"
        w = pw.PointHistoryWriter(source_id="it-writer", coverage_flush_interval=0)
        w._tag_points[tag] = pid
        for i in range(5):
            w.submit_raw(tag, float(i), 1, (T0 + timedelta(seconds=i)).isoformat())
        await w._flush_once(final=True)
        await w._maybe_flush_coverage(force=True)

        rows = await repo.read_events([pid], T0 - timedelta(seconds=1), T0 + timedelta(seconds=10))
        assert len(rows[pid]) == 5
        assert [r["value"] for r in rows[pid]] == [0.0, 1.0, 2.0, 3.0, 4.0]
        assert w.metrics["events_written"] == 5

        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            segs = (
                (
                    await db.execute(
                        select(HistoryCoverageSegment).where(
                            HistoryCoverageSegment.session_id == "it-writer"
                        )
                    )
                )
                .scalars()
                .all()
            )
            confirmed = [s for s in segs if s.status == "confirmed"]
            assert confirmed, f"覆盖段未登记: {[(s.status, s.seg_start, s.seg_end) for s in segs]}"
            seg = confirmed[0]
            assert seg.seg_start <= T0
            assert seg.seg_end > T0 + timedelta(seconds=4)
            for s in segs:
                await db.delete(s)
            await db.commit()

    async def test_gap_window_registered_not_filled(self, point_in_pg):
        pid = point_in_pg
        tag = f"REF_W_{pid[:8]}"
        w = pw.PointHistoryWriter(source_id="it-gap", coverage_flush_interval=0, max_queue=3)
        w._tag_points[tag] = pid
        for i in range(3):
            w.submit_raw(tag, float(i), 1, (T0 + timedelta(seconds=i)).isoformat())
        dropped_ts = T0 + timedelta(seconds=9)
        assert not w.submit_raw(tag, 9.0, 1, dropped_ts.isoformat())  # 队列满
        await w._flush_once(final=True)
        await w._maybe_flush_coverage(force=True)

        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            segs = (
                (
                    await db.execute(
                        select(HistoryCoverageSegment).where(
                            HistoryCoverageSegment.session_id == "it-gap"
                        )
                    )
                )
                .scalars()
                .all()
            )
            gaps = [s for s in segs if s.status == "gap"]
            assert gaps, f"队列满缺口未登记为 gap 段: {[(s.status, s.seg_start) for s in segs]}"
            assert gaps[0].seg_start == dropped_ts  # 缺口按源时间登记
            for s in segs:
                await db.delete(s)
            await db.commit()

    async def test_same_ts_conflict_registered_existing_kept(self, point_in_pg):
        pid = point_in_pg
        r1 = await repo.write_events([_event(pid, T0, 1.0)])
        assert r1.inserted == 1

        from app.core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            r2 = await repo.write_events([_event(pid, T0, 2.0)], db_session=db)
            assert r2.inserted == 0
            assert len(r2.conflicts) == 1
            await db.commit()
            conflicts = (
                (
                    await db.execute(
                        select(HistoryPointConflict).where(HistoryPointConflict.point_id == pid)
                    )
                )
                .scalars()
                .all()
            )
            assert len(conflicts) == 1
            assert conflicts[0].status == "resolved_skip"
        rows = await repo.read_events([pid], T0 - timedelta(seconds=1), T0 + timedelta(seconds=1))
        assert rows[pid][0]["value"] == 1.0  # 既有事实未被覆盖


class TestImportPointPath:
    async def test_shadow_dual_write_and_slot_count(self, point_in_pg, monkeypatch):
        pid = point_in_pg
        tag = f"REF_W_{pid[:8]}"
        from app.core.db import AsyncSessionLocal
        from app.models.loop import LoopLedger, LoopTagMapping
        from app.services import data_import as di

        loop_id = str(uuid.uuid4())
        wide_tag = f"REF-SHADOW-{loop_id[:6]}"
        async with AsyncSessionLocal() as db:
            db.add(
                LoopLedger(
                    id=loop_id,
                    tag_name=wide_tag,
                    status="READY",
                    control_type="FC",
                    loop_type="FC",
                    is_active=True,
                )
            )
            await db.flush()
            db.add(LoopTagMapping(loop_id=loop_id, tag_id=pid, tag_role="PV", is_required=True))
            await db.commit()

        fake_ts = [(T0 + timedelta(seconds=i)).isoformat().replace("+00:00", "Z") for i in range(3)]
        fake_raw = (fake_ts, {tag: {"values": [1.0, 2.0, 3.0], "qualities": [1, 1, 1]}})

        async def fake_fetch(tag_codes, start_time, end_time, interval):  # noqa: ARG001
            return fake_raw

        async def noop(*a, **kw):  # noqa: ARG001
            return None

        async def not_cancelled(tid):  # noqa: ARG001
            return False

        monkeypatch.setattr(di, "_fetch_remote_history", fake_fetch)
        monkeypatch.setattr(di, "_is_task_cancelled", not_cancelled)
        monkeypatch.setattr(di, "_invalidate_loop_caches", noop)
        monkeypatch.setattr(di, "_trigger_kpi_backfill", noop)

        try:
            count, failed, cancelled = await di._import_single_loop(
                loop_id=loop_id,
                start_dt=T0.replace(tzinfo=None),
                end_dt=(T0 + timedelta(seconds=3)).replace(tzinfo=None),
                interval=1,
                conflict_strategy="skip",
                subtable=di.make_subtable_name(wide_tag),
                unit_id="",
                role_tag_map={"PV": tag},
                chunk_hours=1,
                role_point_map={"PV": (tag, pid)},
                storage_mode="shadow",
            )
            assert cancelled is False and failed == []
            assert count == 3  # 时间槽计数（对外单位不变）

            from app.core.tdengine_native import query_wide_table_native

            wide_rows = await query_wide_table_native(
                di.make_subtable_name(wide_tag),
                (T0 - timedelta(seconds=1)).isoformat(),
                (T0 + timedelta(seconds=10)).isoformat(),
            )
            assert len(wide_rows) == 3  # 宽表照旧

            point_rows = await repo.read_events(
                [pid], T0 - timedelta(seconds=1), T0 + timedelta(seconds=10)
            )
            assert len(point_rows[pid]) == 3  # 点表新增
            assert all(r["source_kind"] == 4 for r in point_rows[pid])  # REMOTE_GRID

            async with AsyncSessionLocal() as db:
                segs = (
                    (
                        await db.execute(
                            select(HistoryCoverageSegment).where(
                                HistoryCoverageSegment.point_id == pid
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert segs and all(s.status == "confirmed" for s in segs)
        finally:
            from app.core.tdengine_native import execute_native_effective

            await execute_native_effective(
                f"DROP TABLE IF EXISTS {repo.settings.TDENGINE_DB}."
                f"{di.make_subtable_name(wide_tag)}"
            )
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(HistoryCoverageSegment).where(HistoryCoverageSegment.point_id == pid)
                )
                await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
                await db.execute(delete(LoopLedger).where(LoopLedger.id == loop_id))
                await db.commit()

    async def test_point_mode_rejects_overwrite(self):
        from app.services.data_import import HistoryDataSourceError, _import_single_loop

        with pytest.raises(HistoryDataSourceError, match="点级替换协议未实现"):
            await _import_single_loop(
                loop_id=str(uuid.uuid4()),
                start_dt=datetime(2026, 9, 9),
                end_dt=datetime(2026, 9, 9, 1),
                interval=1,
                conflict_strategy="overwrite",
                role_tag_map={"PV": "X"},
                storage_mode="point",
            )
