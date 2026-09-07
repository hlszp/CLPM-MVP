"""P3 等价性测试：密集参考 → COV 点表 → LogicalWideBuilder 重建（V01~V10）.

运行（隔离环境）：
    cd backend && uv run pytest tests/integration/ -k builder_equivalence -m integration -q

证据链（计划 §5.2-1）：参考 RawTimeSeries 由 tests/refactor/reference_data
**独立**构造（平凡因果保持），不调用被测 builder；点表事件经生产仓储
（repo.write_events）写入真实 TDengine。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.services.data_source import point_history_metadata as meta
from app.services.data_source import point_history_repository as repo
from app.services.data_source.logical_wide_builder import build_logical_wide
from tests.refactor import reference_data as rd

pytestmark = pytest.mark.integration

ALL_ROLES = ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]


@pytest.fixture(scope="module")
def dense_ds() -> rd.ReferenceDataset:
    return rd.build_reference_dataset(hours=1.0)


@pytest.fixture(scope="module")
def cx_ds() -> rd.ReferenceDataset:
    return rd.build_counterexamples()


async def _seed_pg_and_points(ds: rd.ReferenceDataset) -> list[str]:
    """种 PG（回路/点/映射/绑定历史）+ TD 点事件；返回 loop_id 列表."""
    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.point_history import LoopTagBindingHistory
    from app.models.tag import TagRegistry

    loop_ids = list(ds.loops.keys())
    point_ids = {p.point_id for lp in ds.loops.values() for p in lp.points.values()}
    point_ids.update(ds.extra_points.keys())
    async with AsyncSessionLocal() as db:
        await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids)))
        await db.execute(
            delete(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id.in_(loop_ids))
        )
        await db.execute(delete(TagRegistry).where(TagRegistry.id.in_(list(point_ids))))
        await db.execute(delete(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
        await db.commit()

        for lp in ds.loops.values():
            db.add(
                LoopLedger(
                    id=lp.loop_id,
                    tag_name=lp.tag_name,
                    status="READY",
                    control_type=lp.control_type,
                    loop_type=lp.control_type,
                    is_active=True,
                    include_in_evaluation=True,
                )
            )
        await db.flush()
        seen: set[str] = set()
        for lp in ds.loops.values():
            for role, point in lp.points.items():
                if point.point_id in seen:
                    continue
                seen.add(point.point_id)
                db.add(
                    TagRegistry(
                        id=point.point_id,
                        tag_name=point.tag_name,
                        tag_type=role,
                        last_sync_at=datetime(2026, 9, 6),
                        is_linked=True,
                    )
                )
        for point in ds.extra_points.values():
            if point.point_id not in seen:
                seen.add(point.point_id)
                db.add(
                    TagRegistry(
                        id=point.point_id,
                        tag_name=point.tag_name,
                        tag_type="PV",
                        last_sync_at=datetime(2026, 9, 6),
                        is_linked=False,
                    )
                )
        await db.flush()
        for lp in ds.loops.values():
            for role, point in lp.points.items():
                if point is None:
                    continue
                db.add(
                    LoopTagMapping(
                        loop_id=lp.loop_id,
                        tag_id=point.point_id,
                        tag_role=role,
                        is_required=role in ("PV", "SP", "OP"),
                    )
                )
        await db.commit()
        await meta.initialize_binding_history(db, effective_at=datetime(2026, 9, 5, tzinfo=UTC))
        await db.commit()

    # TD 点事件（经生产仓储）；先 DROP 本数据集点子表——同 ID 不同参数的
    # 数据集（1h/2h 同种子）互不污染
    from app.core.config import settings
    from app.core.tdengine_native import execute_native_effective
    from app.services.data_source.point_history_repository import point_subtable

    await repo.ensure_schema()
    for lp in ds.loops.values():
        for point in lp.points.values():
            if point is None:
                continue
            await execute_native_effective(
                f"DROP TABLE IF EXISTS {settings.TDENGINE_DB}.{point_subtable(point.point_id)}"
            )
    for point in ds.extra_points.values():
        await execute_native_effective(
            f"DROP TABLE IF EXISTS {settings.TDENGINE_DB}.{point_subtable(point.point_id)}"
        )
    for pid, events in ds.events.items():
        pevents = [
            repo.PointEvent(
                point_id=pid,
                ts=datetime.fromtimestamp(e.ts_ms / 1000.0, UTC),
                value=e.value,
                quality_class=e.quality_class,
                quality_raw=e.quality_raw,
                quality_schema=rd.QSCHEMA_AAS,
                source_kind=e.source_kind,
                received_at=datetime(2026, 9, 6, tzinfo=UTC),
                source_id="equiv-seed",
            )
            for e in events
        ]
        result = await repo.write_events(pevents)
        assert not result.failed, f"点事件种入失败 {pid}: {result.error}"
    return loop_ids


@pytest.fixture(scope="module")
async def dense_seeded(dense_ds):
    yield await _seed_pg_and_points(dense_ds)


@pytest.fixture(scope="module")
async def cx_seeded(cx_ds):
    # 反例：绑定历史需要登记改绑（PV 在 start+2400 换新点）
    await _seed_pg_and_points(cx_ds)
    from app.core.db import AsyncSessionLocal

    lid = next(iter(cx_ds.loops.keys()))
    loop = cx_ds.loop(lid)
    new_pid = loop.rebinding["PV"][1][1]
    async with AsyncSessionLocal() as db:
        await meta.record_binding_change(
            db,
            loop_id=lid,
            tag_role="PV",
            new_tag_id=new_pid,
            effective_at=datetime.fromtimestamp(cx_ds.start_s + 2400, UTC),
            basis="REBIND",
        )
        await db.commit()
    yield cx_ds


def _win(ds: rd.ReferenceDataset, off_start: int, off_end: int):
    base = datetime.fromtimestamp(ds.start_s, UTC)
    return base + timedelta(seconds=off_start), base + timedelta(seconds=off_end)


class TestDenseEquivalence:
    """V01/V02：密集→COV→重建 与独立参考逐点全等（DOUBLE 精度无舍入）。"""

    async def test_full_window_exact_match_all_loops(self, dense_ds, dense_seeded):
        from app.core.db import AsyncSessionLocal

        for lid, loop in dense_ds.loops.items():
            start, end = _win(dense_ds, 0, dense_ds.end_s - dense_ds.start_s)
            expected = rd.build_reference_raw_series(dense_ds, lid, ALL_ROLES, start, end)
            async with AsyncSessionLocal() as db:
                got = await build_logical_wide(db, lid, ALL_ROLES, start, end)
            assert got.timestamps == expected.timestamps, f"{loop.tag_name}: 时间轴不一致"
            for role in [r.lower() for r in ALL_ROLES]:
                assert got.signals[role] == expected.signals[role], (
                    f"{loop.tag_name}/{role}: 值不一致"
                )
            assert got.quality_codes["pv_quality"] == expected.quality_codes["pv_quality"]

    async def test_subwindow_and_boundary_seconds(self, dense_ds, dense_seeded):
        """子窗 + 整秒边界端点（V08 部分）：首尾秒恰为窗端."""
        from app.core.db import AsyncSessionLocal

        lid = next(iter(dense_ds.loops))
        start, end = _win(dense_ds, 600, 1200)
        expected = rd.build_reference_raw_series(dense_ds, lid, ALL_ROLES, start, end)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ALL_ROLES, start, end)
        assert len(got.timestamps) == 601
        assert got.signals["sp"] == expected.signals["sp"]
        # 非整秒窗：不扩大（ceil/floor）
        s2 = start + timedelta(milliseconds=500)
        e2 = end + timedelta(milliseconds=500)
        async with AsyncSessionLocal() as db:
            got2 = await build_logical_wide(db, lid, ALL_ROLES, s2, e2)
        assert got2.timestamps[0] == (start + timedelta(seconds=1)).replace(tzinfo=None)
        assert got2.timestamps[-1] == end.replace(tzinfo=None)

    async def test_mode_int_semantics(self, dense_ds, dense_seeded):
        from app.core.db import AsyncSessionLocal

        lid = next(iter(dense_ds.loops))
        start, end = _win(dense_ds, 0, 3600)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ALL_ROLES, start, end)
        assert all(v is None or isinstance(v, int) for v in got.signals["mode"])

    async def test_constant_loop_full_coverage(self, dense_ds, dense_seeded):
        """V02：常值回路（LIC-401）1s 逻辑覆盖连续，物理事件少不判缺失."""
        from app.core.db import AsyncSessionLocal

        lid = next(lp.loop_id for lp in dense_ds.loops.values() if lp.tag_name == "REF-LIC-401")
        start, end = _win(dense_ds, 0, 3600)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ALL_ROLES, start, end)
        n = 3601
        assert len(got.timestamps) == n
        assert all(v is not None for v in got.signals["pv"])  # 连续覆盖
        assert all(q == 1 for q in got.quality_codes["pv_quality"])
        assert len(dense_ds.events[dense_ds.loops[lid].points["PV"].point_id]) < 10  # 物理事件稀疏


class TestCounterexamples:
    """V03/V04/V05/V06/V09：缺口/坏质量/无初值/改绑 不得填成正常."""

    async def test_no_initial_value_prefix_unknown(self, cx_ds, cx_seeded):
        """V03：PV 首 300s 无事件无初值 → None + quality -1（禁止后向填充）."""
        from app.core.db import AsyncSessionLocal

        lid = next(iter(cx_ds.loops))
        start, end = _win(cx_ds, 0, 600)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ["PV"], start, end)
        assert len(got.timestamps) == 601
        # 前 300 秒未知
        assert all(v is None for v in got.signals["pv"][:300])
        assert all(q == -1 for q in got.quality_codes["pv_quality"][:300])
        # 300s 起有真值
        assert got.signals["pv"][300] is not None
        assert got.quality_codes["pv_quality"][300] == 1

    async def test_bad_quality_persists_not_skipped(self, cx_ds, cx_seeded):
        """V04：值不变的 BAD 段沿用坏质量，不得跳过沿用旧 Good."""
        from app.core.db import AsyncSessionLocal

        lid = next(iter(cx_ds.loops))
        start, end = _win(cx_ds, 1150, 1550)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ["PV"], start, end)
        # 1150~1199 Good；1200~1349 BAD（0）；1350~1499 Uncertain→UNKNOWN(-1)；
        # 1500+ Good
        assert all(q == 1 for q in got.quality_codes["pv_quality"][:50])
        assert all(q == 0 for q in got.quality_codes["pv_quality"][50:200])
        assert all(q == -1 for q in got.quality_codes["pv_quality"][200:350])
        assert got.quality_codes["pv_quality"][350] == 1
        # 值在 Bad 段保持（52.0）
        assert all(abs(v - 52.0) < 1e-9 for v in got.signals["pv"][50:200])

    async def test_gap_unknown_recovery_snapshot_only(self, cx_ds, cx_seeded):
        """V06：断线窗口（600-900）真值即未知（无事件）；恢复后从恢复时刻起有值."""
        from app.core.db import AsyncSessionLocal

        lid = next(iter(cx_ds.loops))
        start, end = _win(cx_ds, 500, 1000)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ["PV"], start, end)
        # 600~899 无任何事件 → 因果保持沿用 599 前状态？——
        # 反例语义：该窗口**真值未知**（断线），但事件流无 gap 段登记时，
        # builder 的保守行为是按 COV 契约保持（无变化≠缺失）。真值未知
        # 需要 gap 覆盖段佐证（见 test_gap_segment_forces_unknown）。
        # 本用例固定现状：无 gap 段时保持值（正常 COV 契约）。
        assert all(v is not None for v in got.signals["pv"][100:])

    async def test_gap_segment_forces_unknown(self, cx_ds, cx_seeded):
        """V06（gap 段路径）：登记 gap 覆盖段后该窗口强制未知，恢复边界后恢复."""
        from app.core.db import AsyncSessionLocal
        from app.models.point_history import HistoryCoverageSegment

        lid = next(iter(cx_ds.loops))
        gs = datetime.fromtimestamp(cx_ds.start_s + 600, UTC)
        ge = datetime.fromtimestamp(cx_ds.start_s + 900, UTC)
        async with AsyncSessionLocal() as db:
            seg = await meta.register_coverage(
                db, seg_start=gs, seg_end=ge, session_id="equiv-gap", source_task="test"
            )
            seg.status = "gap"
            await db.commit()
            try:
                start, end = _win(cx_ds, 500, 1000)
                got = await build_logical_wide(db, lid, ["PV"], start, end)
                assert all(v is None for v in got.signals["pv"][100:400])  # 600~899
                assert all(q == -1 for q in got.quality_codes["pv_quality"][100:400])
                assert got.signals["pv"][400] is not None  # 900 恢复
            finally:
                await db.execute(
                    delete(HistoryCoverageSegment).where(HistoryCoverageSegment.id == seg.id)
                )
                await db.commit()

    async def test_rebinding_segmented_no_carryover(self, cx_ds, cx_seeded):
        """V09：改绑后新点无 anchor → 未知直到新点首事件；不沿用旧点值."""
        from app.core.db import AsyncSessionLocal

        lid = next(iter(cx_ds.loops))
        start, end = _win(cx_ds, 2300, 2800)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ["PV"], start, end)
        # 2400~2699 未知（新点无事件）；2700 起新点真值
        assert all(v is None for v in got.signals["pv"][100:400])
        assert all(q == -1 for q in got.quality_codes["pv_quality"][100:400])
        assert got.signals["pv"][400] is not None
        # 2399 前仍是旧点值（Good）
        assert got.quality_codes["pv_quality"][99] == 1

    async def test_op_no_initial_value(self, cx_ds, cx_seeded):
        """V03：OP 首事件 2100s，此前未知."""
        from app.core.db import AsyncSessionLocal

        lid = next(iter(cx_ds.loops))
        start, end = _win(cx_ds, 2000, 2200)
        async with AsyncSessionLocal() as db:
            got = await build_logical_wide(db, lid, ["OP"], start, end)
        assert all(v is None for v in got.signals["op"][:100])
        assert got.signals["op"][100] is not None

    async def test_anchor_extends_initial_state(self, cx_ds, cx_seeded):
        """V10-lite：窗口前无事件但有适用锚点 → 以锚点状态保持（不伪造变化）."""
        from app.core.db import AsyncSessionLocal

        lid = next(iter(cx_ds.loops))
        loop = cx_ds.loop(lid)
        # 锚点：SP 在窗口前（300s）值 50.0，覆盖到 800s——查询 [400, 600] 的 SP：
        # SP 全程本有事件（每角色都有 seed 事件），故用 OP 点验证：
        op_point = loop.points["OP"].point_id
        anchor_ts = datetime.fromtimestamp(cx_ds.start_s + 500, UTC)
        async with AsyncSessionLocal() as db:
            await meta.upsert_anchor(
                db,
                point_id=op_point,
                anchor_ts=anchor_ts,
                value=44.0,
                quality_class=1,
                quality_raw=1,
                coverage_until=datetime.fromtimestamp(cx_ds.start_s + 800, UTC),
            )
            await db.commit()
            try:
                # 查询 OP [550, 600]：无 OP 事件（首事件 2100s），锚点应生效
                start, end = _win(cx_ds, 550, 600)
                got = await build_logical_wide(db, lid, ["OP"], start, end)
                # 锚点时刻 500 ≤ 窗口 550 → 状态保持 44.0
                assert all(abs(v - 44.0) < 1e-9 for v in got.signals["op"])
            finally:
                from app.models.point_history import PointStateAnchor

                await db.execute(
                    delete(PointStateAnchor).where(PointStateAnchor.point_id == op_point)
                )
                await db.commit()
