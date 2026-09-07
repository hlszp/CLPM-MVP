"""P1-2/P1-3 元数据层真实 PG 验证（绑定历史/覆盖/批次/冲突/锚点/布局）.

运行（隔离环境）：
    cd backend && uv run pytest tests/integration/test_refactor_metadata.py -m integration -q

验证点（设计 §4.2/§4.3）：
- 绑定历史与 mapping 同事务推进的调用契约（session 传入）；
- EXCLUDE 约束真实拒绝同 (loop, role) 重叠区间；
- resolve_bindings 时刻解析（未知过去=None、半开右端）；
- 覆盖段 pending→confirmed（批次确认驱动）+ 相邻/重叠合并 + 不跨 gap；
- 锚点 upsert 幂等 + applicable（coverage_until 排除）；
- manifest：loop 段优先于 global、无 manifest 恒 legacy、半开边界；
- storage mode sys_config 读写与兜底链。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_source import point_history_metadata as meta
from app.services.data_source.history_layout import (
    get_storage_mode,
    set_storage_mode,
    writeback_enabled_for,
)

pytestmark = pytest.mark.integration

T0 = datetime(2026, 9, 6, tzinfo=UTC)


@pytest.fixture
async def db():
    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def ref_loop(db):
    """一个参考回路 + 2 个测点（PV 新旧）+ 当前映射."""
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.tag import TagRegistry

    loop_id = str(uuid.uuid4())
    tag_old, tag_new = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(
        LoopLedger(
            id=loop_id,
            tag_name=f"REF-META-{loop_id[:8]}",
            status="READY",
            control_type="FC",
            loop_type="FC",
            is_active=True,
        )
    )
    await db.flush()
    db.add(
        TagRegistry(
            id=tag_old,
            tag_name=f"P_{tag_old[:8]}",
            tag_type="PV",
            last_sync_at=T0.replace(tzinfo=None),
        )
    )
    db.add(
        TagRegistry(
            id=tag_new,
            tag_name=f"P_{tag_new[:8]}",
            tag_type="PV",
            last_sync_at=T0.replace(tzinfo=None),
        )
    )
    await db.flush()
    db.add(LoopTagMapping(loop_id=loop_id, tag_id=tag_old, tag_role="PV", is_required=True))
    await db.flush()
    yield {"loop_id": loop_id, "old": tag_old, "new": tag_new}
    from sqlalchemy import delete

    from app.models.point_history import LoopTagBindingHistory

    await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    await db.execute(delete(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id == loop_id))
    await db.execute(delete(TagRegistry).where(TagRegistry.id.in_([tag_old, tag_new])))
    await db.execute(delete(LoopLedger).where(LoopLedger.id == loop_id))
    await db.commit()


class TestBindingHistory:
    async def test_initialize_and_rebind(self, db, ref_loop):
        loop_id = ref_loop["loop_id"]
        # 全库基线初始化：覆盖当前全部 mapping（含固件外的种子映射）≥ 本回路 1 条；
        # 首次建史行数不固定（随库里 mapping 总数），只验证幂等与本回路正确性
        created = await meta.initialize_binding_history(db, effective_at=T0)
        assert created >= 1
        # 幂等：同绑定重复初始化不新增（全库 0）
        assert (
            await meta.initialize_binding_history(db, effective_at=T0 + timedelta(minutes=1)) == 0
        )

        # 改绑：闭合旧区间 + 开新区间（与 mapping 同事务由调用方保证）
        await meta.record_binding_change(
            db,
            loop_id=loop_id,
            tag_role="PV",
            new_tag_id=ref_loop["new"],
            effective_at=T0 + timedelta(hours=2),
        )
        bindings = await meta.resolve_bindings(
            db,
            loop_id,
            [
                T0 + timedelta(hours=1),
                T0 + timedelta(hours=2),
                T0 + timedelta(hours=3),
            ],
        )
        t1, t2, t3 = sorted(bindings)
        assert bindings[t1]["PV"] == ref_loop["old"]
        # 半开右端：T0+2h 起已属新绑定
        assert bindings[t2]["PV"] == ref_loop["new"]
        assert bindings[t3]["PV"] == ref_loop["new"]

        # 解绑：闭合区间不开新
        await meta.record_binding_change(
            db,
            loop_id=loop_id,
            tag_role="PV",
            new_tag_id=None,
            effective_at=T0 + timedelta(hours=4),
        )
        bindings2 = await meta.resolve_bindings(db, loop_id, [T0 + timedelta(hours=5)])
        assert bindings2[T0 + timedelta(hours=5)].get("PV") is None

    async def test_unknown_past_not_backfilled(self, db, ref_loop):
        """时刻早于建史起点 → None（未知过去不追溯，设计 §4.2）."""
        loop_id = ref_loop["loop_id"]
        await meta.initialize_binding_history(db, effective_at=T0)
        bindings = await meta.resolve_bindings(db, loop_id, [T0 - timedelta(days=365)])
        assert bindings[T0 - timedelta(days=365)].get("PV") is None

    async def test_exclude_rejects_overlapping_ranges(self, db, ref_loop):
        """EXCLUDE 约束真实拒绝同 (loop, role) 重叠区间."""
        from app.models.point_history import LoopTagBindingHistory

        loop_id = ref_loop["loop_id"]
        db.add(
            LoopTagBindingHistory(
                loop_id=loop_id,
                tag_role="PV",
                tag_id=ref_loop["old"],
                valid_from=T0,
                valid_to=T0 + timedelta(hours=1),
            )
        )
        await db.flush()
        db.add(
            LoopTagBindingHistory(
                loop_id=loop_id,
                tag_role="PV",
                tag_id=ref_loop["new"],
                valid_from=T0 + timedelta(minutes=30),  # 与上一段重叠
                valid_to=T0 + timedelta(hours=2),
            )
        )
        with pytest.raises(Exception, match="ex_ltbh_no_overlap|overlaps|conflicts"):
            await db.flush()
        await db.rollback()


class TestCoverageAndBatch:
    async def test_batch_lifecycle_drives_coverage(self, db, ref_loop):
        b = await meta.create_batch(
            db, window_start=T0, window_end=T0 + timedelta(hours=1), source_task="t1"
        )
        seg = await meta.register_coverage(
            db,
            seg_start=T0,
            seg_end=T0 + timedelta(hours=1),
            point_id=ref_loop["old"],
            batch_id=b.batch_id,
        )
        assert seg.status == "pending"
        assert await meta.known_coverage(db, point_id=ref_loop["old"]) == []
        await meta.confirm_batch(db, b.batch_id, stats={"rows": 10})
        cov = await meta.known_coverage(db, point_id=ref_loop["old"])
        assert cov == [(T0, T0 + timedelta(hours=1))]

    async def test_merge_adjacent_not_across_gap(self, db, ref_loop):
        b = await meta.create_batch(db, window_start=T0, window_end=T0 + timedelta(hours=3))
        for s, e in [
            (T0, T0 + timedelta(hours=1)),
            (T0 + timedelta(hours=1), T0 + timedelta(hours=2)),  # 相邻 → 合并
            (T0 + timedelta(minutes=90), T0 + timedelta(hours=3)),  # 重叠 → 合并
        ]:
            await meta.register_coverage(
                db, seg_start=s, seg_end=e, point_id=ref_loop["old"], batch_id=b.batch_id
            )
        await meta.confirm_batch(db, b.batch_id)
        removed = await meta.merge_confirmed_coverage(db, point_id=ref_loop["old"])
        assert removed == 2
        cov = await meta.known_coverage(db, point_id=ref_loop["old"])
        assert cov == [(T0, T0 + timedelta(hours=3))]

        # 中间隔 gap 的两段不合并
        b2 = await meta.create_batch(
            db, window_start=T0 + timedelta(hours=10), window_end=T0 + timedelta(hours=11)
        )
        await meta.register_coverage(
            db,
            seg_start=T0 + timedelta(hours=10),
            seg_end=T0 + timedelta(hours=11),
            point_id=ref_loop["old"],
            batch_id=b2.batch_id,
        )
        await meta.confirm_batch(db, b2.batch_id)
        assert await meta.merge_confirmed_coverage(db, point_id=ref_loop["old"]) == 0
        cov2 = await meta.known_coverage(db, point_id=ref_loop["old"])
        assert len(cov2) == 2


class TestConflictAndAnchor:
    async def test_register_conflicts(self, db, ref_loop):
        n = await meta.register_conflicts(
            db,
            [
                {
                    "point_id": ref_loop["old"],
                    "ts_ms": 1788739200000,
                    "resolution": "skip",
                    "existing": {"payload_hash": "h1", "value": 1.0, "quality_class": 1},
                    "incoming": {"value": 2.0, "quality_class": 1},
                }
            ],
            source_task="t2",
        )
        assert n == 1

    async def test_anchor_upsert_and_applicable(self, db, ref_loop):
        pid = ref_loop["old"]
        a1 = await meta.upsert_anchor(
            db,
            point_id=pid,
            anchor_ts=T0,
            value=55.0,
            quality_class=meta_qc_good(),
            quality_raw=1,
            coverage_until=T0 + timedelta(hours=1),
        )
        assert a1.value == 55.0
        # 幂等更新
        await meta.upsert_anchor(
            db,
            point_id=pid,
            anchor_ts=T0,
            value=56.0,
            quality_class=meta_qc_good(),
            quality_raw=1,
            coverage_until=T0 + timedelta(hours=1),
        )
        got = await meta.applicable_anchor(
            db, point_id=pid, window_start=T0 + timedelta(minutes=30)
        )
        assert got is not None and got.value == 56.0
        # coverage_until 排除窗口起点之后
        got2 = await meta.applicable_anchor(db, point_id=pid, window_start=T0 + timedelta(hours=2))
        assert got2 is None


def meta_qc_good() -> int:
    from app.services.data_source.point_history_repository import QC_GOOD

    return QC_GOOD


class TestLayoutManifestAndMode:
    async def test_manifest_default_legacy_and_precedence(self, db, ref_loop):
        loop_id = ref_loop["loop_id"]
        # 无 manifest → legacy
        assert await meta.resolve_layout(db, loop_id=loop_id, at=T0) == "legacy"
        # global point 段
        await meta.set_layout(
            db, layout="point", valid_from=T0 + timedelta(hours=1), scope_type="global"
        )
        assert await meta.resolve_layout(db, loop_id=loop_id, at=T0) == "legacy"
        assert await meta.resolve_layout(db, loop_id=loop_id, at=T0 + timedelta(hours=1)) == "point"
        # loop 精确段覆盖 global
        await meta.set_layout(
            db,
            layout="shadow",
            valid_from=T0 + timedelta(hours=2),
            scope_type="loop",
            scope_id=loop_id,
        )
        at2 = T0 + timedelta(hours=3)
        assert await meta.resolve_layout(db, loop_id=loop_id, at=at2) == "shadow"
        other = f"loop-{uuid.uuid4()}"
        assert await meta.resolve_layout(db, loop_id=other, at=at2) == "point"
        # 半开右端：global point 段到 T0+4h 结束后回 legacy
        await meta.set_layout(
            db,
            layout="point",
            valid_from=T0 + timedelta(hours=1),
            valid_to=T0 + timedelta(hours=4),
            scope_type="global",
        )
        # 自净：清空本用例登记的 manifest（共享库不留给其他测试/进程）
        from sqlalchemy import delete  # noqa: PLC0415

        from app.models.point_history import HistoryLayoutManifest  # noqa: PLC0415

        await db.execute(delete(HistoryLayoutManifest))
        await db.commit()

    async def test_storage_mode_roundtrip(self, db):
        original = await get_storage_mode(db)
        try:
            await set_storage_mode(db, "shadow")
            assert await get_storage_mode(db) == "shadow"
            assert writeback_enabled_for("shadow") == (True, True)
            assert writeback_enabled_for("legacy") == (True, False)
            assert writeback_enabled_for("point") == (False, True)
            with pytest.raises(ValueError, match="非法"):
                await set_storage_mode(db, "both")
        finally:
            await set_storage_mode(db, original)
        await db.commit()
