"""P3-5 Provider 级差分：point 布局经生产 query_fn 与参考/legacy 全链路对账.

运行（隔离环境）：
    cd backend && uv run pytest tests/integration/ -k provider_differential -m integration -q

覆盖（V01 算法等价 / V14 布局切换 / V16 全链路）：
1. 参考数据双布局种入（宽表 + 点表同数据）；
2. manifest global point → 生产 make_query_fn 走 LogicalWideBuilder，
   与独立参考 RawTimeSeries 逐点全等（DOUBLE 无舍入）；
3. DataPlanner + 全部指标计算器（与 P0 基线同口径）结果与
   tests/golden/refactor_algorithm_baseline.json 全等（算法等价 §5.2）；
4. manifest 撤除 → legacy 路径原样工作（FLOAT32 容差）；
5. 跨切换边界窗口（V14）：T 归 point、legacy 仅 t<T、边界秒不重不漏。
"""

from __future__ import annotations

import json
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from tests.refactor import reference_data as rd

pytestmark = pytest.mark.integration

ALL_ROLES = ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]
_GOLDEN_ALGO = Path(__file__).resolve().parents[1] / "golden" / "refactor_algorithm_baseline.json"


def _f32(v):
    if v is None:
        return None
    return struct.unpack("f", struct.pack("f", float(v)))[0]


@pytest.fixture(scope="module")
def ds() -> rd.ReferenceDataset:
    return rd.build_reference_dataset(hours=2.0)


async def _seed_pg(ds: rd.ReferenceDataset) -> None:
    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger, LoopTagMapping
    from app.models.point_history import LoopTagBindingHistory
    from app.models.tag import TagRegistry

    loop_ids = list(ds.loops.keys())
    point_ids = {p.point_id for lp in ds.loops.values() for p in lp.points.values()}
    async with AsyncSessionLocal() as db:
        await db.execute(delete(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids)))
        await db.execute(
            delete(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id.in_(loop_ids))
        )
        await db.execute(delete(TagRegistry).where(TagRegistry.id.in_(list(point_ids))))
        await db.execute(delete(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
        await db.commit()
        from uuid import uuid4

        unit_node = (
            await db.execute(
                select(
                    __import__("app.models.plant_node", fromlist=["PlantNode"]).PlantNode.id
                ).limit(1)
            )
        ).scalar_one_or_none()
        for lp in ds.loops.values():
            db.add(
                LoopLedger(
                    id=lp.loop_id,
                    tag_name=lp.tag_name,
                    status="READY",
                    unit_id=str(unit_node) if unit_node else None,
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
                        range_min=lp.range_min if role == "PV" else None,
                        range_max=lp.range_max if role == "PV" else None,
                    )
                )
        await db.flush()
        for lp in ds.loops.values():
            for role, point in lp.points.items():
                db.add(
                    LoopTagMapping(
                        id=str(uuid4()),
                        loop_id=lp.loop_id,
                        tag_id=point.point_id,
                        tag_role=role,
                        is_required=role in ("PV", "SP", "OP"),
                    )
                )
        await db.commit()
        from app.services.data_source import point_history_metadata as meta

        await meta.initialize_binding_history(db, effective_at=datetime(2026, 9, 5, tzinfo=UTC))
        await db.commit()


async def _seed_td(ds: rd.ReferenceDataset) -> None:
    from app.core.config import settings
    from app.core.tdengine_native import batch_insert, execute_native_effective
    from app.services.data_source import point_history_repository as repo
    from app.services.data_source.point_history_repository import point_subtable

    await repo.ensure_schema()
    # 同 ID 数据集（1h/2h 同种子）互不污染：先清本数据集点子表
    for lp in ds.loops.values():
        for point in lp.points.values():
            await execute_native_effective(
                f"DROP TABLE IF EXISTS {settings.TDENGINE_DB}.{point_subtable(point.point_id)}"
            )
    for loop_id, loop in ds.loops.items():
        # 宽表（legacy 布局同数据）
        rows = rd.wide_rows_from_dense(ds, loop_id)
        sub = "d_loop_" + loop.tag_name.lower().replace("-", "_")
        for i in range(0, len(rows), 1000):
            await batch_insert(sub, rows[i : i + 1000], loop_id=loop_id, unit_id=loop.unit_id)
        # 点表（COV 事件流）
    all_events = []
    for pid, events in ds.events.items():
        for e in events:
            all_events.append(
                repo.PointEvent(
                    point_id=pid,
                    ts=datetime.fromtimestamp(e.ts_ms / 1000.0, UTC),
                    value=e.value,
                    quality_class=e.quality_class,
                    quality_raw=e.quality_raw,
                    quality_schema=rd.QSCHEMA_AAS,
                    source_kind=e.source_kind,
                    received_at=datetime(2026, 9, 6, tzinfo=UTC),
                    source_id="diff-seed",
                )
            )
    result = await repo.write_events(all_events)
    assert not result.failed, result.error


@pytest.fixture(scope="module")
async def seeded(ds):
    await _seed_pg(ds)
    await _seed_td(ds)
    yield ds


@pytest.fixture
async def point_manifest(ds):
    """global point 布局段（覆盖参考数据全窗）。"""
    from app.core.db import AsyncSessionLocal
    from app.services.data_source import point_history_metadata as meta
    from app.services.data_source.history_layout_router import invalidate_manifest_cache

    async with AsyncSessionLocal() as db:
        entry = await meta.set_layout(
            db,
            layout="point",
            valid_from=datetime(2026, 9, 5, tzinfo=UTC),
            scope_type="global",
            basis="diff-test",
        )
        await db.commit()
    invalidate_manifest_cache()
    yield entry.id
    async with AsyncSessionLocal() as db:
        from app.models.point_history import HistoryLayoutManifest

        await db.execute(delete(HistoryLayoutManifest).where(HistoryLayoutManifest.id == entry.id))
        await db.commit()
    invalidate_manifest_cache()


async def _query_via_provider(ds, loop_id: str, start_off: int, end_off: int, roles=ALL_ROLES):
    from app.core.db import AsyncSessionLocal
    from app.services.data_source.tdengine_provider import TDengineProvider

    base = datetime.fromtimestamp(ds.start_s, UTC)
    start = base + timedelta(seconds=start_off)
    end = base + timedelta(seconds=end_off)
    provider = TDengineProvider()
    async with AsyncSessionLocal() as db:
        qfn = provider.make_query_fn(db)
        raw = await qfn(loop_id, roles, start, end, 1)
    await provider.close()
    return raw, start, end


class TestProviderPointPath:
    async def test_point_layout_matches_reference_exactly(self, ds, seeded, point_manifest):
        """V01（Provider 级）：make_query_fn 走 point 路径与独立参考全等."""
        for loop_id, loop in ds.loops.items():
            raw, start, end = await _query_via_provider(ds, loop_id, 600, 4200)
            expected = rd.build_reference_raw_series(ds, loop_id, ALL_ROLES, start, end)
            assert raw.timestamps == expected.timestamps, loop.tag_name
            for role in [r.lower() for r in ALL_ROLES]:
                assert raw.signals[role] == expected.signals[role], f"{loop.tag_name}/{role}"
            assert raw.quality_codes["pv_quality"] == expected.quality_codes["pv_quality"]

    async def test_legacy_layout_still_works(self, ds, seeded):
        """无 manifest（legacy）→ 原宽表路径原样工作（FLOAT32 舍入容差）."""
        from app.services.data_source.history_layout_router import invalidate_manifest_cache

        invalidate_manifest_cache()
        loop_id = next(iter(ds.loops))
        raw, start, end = await _query_via_provider(ds, loop_id, 600, 1200)
        expected = rd.build_reference_raw_series(ds, loop_id, ALL_ROLES, start, end)
        assert len(raw.timestamps) == 601
        for i in range(601):
            for role in ("pv", "sp", "op"):
                got, exp = raw.signals[role][i], _f32(expected.signals[role][i])
                if got is None or exp is None:
                    assert got == exp
                else:
                    assert abs(got - exp) <= max(1e-4, abs(exp) * 1e-6)

    async def test_boundary_switch_window(self, ds, seeded):
        """V14：跨切换 T 的窗口——T 归 point、legacy 仅 t<T、边界秒唯一.

        参考[600,1200]窗，T=900：legacy [600,899]（稀疏行）+ point [900,1200]（网格）。
        """
        from app.core.db import AsyncSessionLocal
        from app.services.data_source import point_history_metadata as meta
        from app.services.data_source.history_layout_router import invalidate_manifest_cache

        base = datetime.fromtimestamp(ds.start_s, UTC)
        t_switch = base + timedelta(seconds=900)
        async with AsyncSessionLocal() as db:
            entry = await meta.set_layout(
                db, layout="point", valid_from=t_switch, scope_type="global", basis="V14"
            )
            await db.commit()
        invalidate_manifest_cache()
        try:
            loop_id = next(iter(ds.loops))
            raw, _s, _e = await _query_via_provider(ds, loop_id, 600, 1200)
            ts = raw.timestamps
            assert len(ts) == len(set(ts)), "边界秒重复"
            # 900s 起是 point 网格（值=参考 DOUBLE 精度）；899s 及以前 legacy（FLOAT32）
            idx_900 = ts.index((base + timedelta(seconds=900)).replace(tzinfo=None))
            expected = rd.build_reference_raw_series(
                ds,
                loop_id,
                ALL_ROLES,
                base + timedelta(seconds=900),
                base + timedelta(seconds=905),
            )
            for k in range(6):
                assert raw.signals["sp"][idx_900 + k] == expected.signals["sp"][k]
        finally:
            async with AsyncSessionLocal() as db:
                from app.models.point_history import HistoryLayoutManifest

                await db.execute(
                    delete(HistoryLayoutManifest).where(HistoryLayoutManifest.id == entry.id)
                )
                await db.commit()
            invalidate_manifest_cache()


class TestAlgorithmEquivalence:
    async def test_metrics_match_legacy_golden(self, ds, seeded, point_manifest):
        """算法等价（§5.2）：point 布局全链路指标结果与 P0 legacy golden 全等."""
        from sqlalchemy import text

        from app.contracts.data_types import ControlType, TimeWindow
        from app.core.db import AsyncSessionLocal
        from app.services.data_planner import DataPlanner
        from app.services.data_source.tdengine_provider import TDengineProvider
        from app.services.metric_calculator import get_calculator
        from app.services.metric_data_bundle import MetricDataBundleAssembler

        window_start = datetime(2026, 9, 6) + timedelta(seconds=600)
        window_end = window_start + timedelta(seconds=3599)

        async with AsyncSessionLocal() as db:
            rows = (
                (
                    await db.execute(
                        text("SELECT DISTINCT metric_code FROM clpm_metric_data_requirement")
                    )
                )
                .scalars()
                .all()
            )
            metric_codes = sorted(str(r) for r in rows)
        results: dict[str, dict] = {}

        async def _compute():
            provider = TDengineProvider()
            assembler = MetricDataBundleAssembler()
            async with AsyncSessionLocal() as db:
                for loop_id, loop in ds.loops.items():
                    planner = DataPlanner(
                        cache=None,
                        tdengine_query_fn=provider.make_query_fn(db),
                        assembler=assembler,
                        db=db,
                    )
                    bundles = await planner.request_bundles(
                        loop_id,
                        metric_codes,
                        TimeWindow(start=window_start, end=window_end),
                        ControlType(loop.control_type),
                    )
                    per_loop: dict[str, dict] = {}
                    for bundle in bundles:
                        calc = get_calculator(bundle.metric_code)
                        if calc is None:
                            continue
                        res = calc.calculate(bundle)
                        per_loop[bundle.metric_code] = {
                            "value": res.value,
                            "confidence": res.confidence_level,
                        }
                    results[loop.tag_name] = per_loop
            await provider.close()

        await _compute()

        baseline = json.loads(_GOLDEN_ALGO.read_text())
        for loop, metrics in baseline["results"].items():
            cur = results[loop]
            assert set(metrics) == set(cur), f"{loop}: 指标集合漂移"
            for code, exp in metrics.items():
                got = cur[code]
                assert got["confidence"] == exp["confidence"], (
                    f"{loop}/{code}: 可信度 {exp['confidence']} → {got['confidence']}"
                )
                ev, gv = exp["value"], got["value"]
                if ev is None or gv is None:
                    assert ev == gv, f"{loop}/{code}: None 漂移 {ev}→{gv}"
                else:
                    assert abs(gv - ev) <= max(1e-9, abs(ev) * 1e-9), f"{loop}/{code}: {ev} → {gv}"
