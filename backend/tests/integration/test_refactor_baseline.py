"""P0-6 集成基线：真实 TDengine/PostgreSQL 上的 legacy 取数与算法结果基线.

运行方式（隔离环境，见 deploy/docker/docker-compose.refactor.yml）：

    cd backend && uv run pytest tests/integration/test_refactor_baseline.py -m integration -s

内容：
1. 环境自检：TD 版本 / 库 / PG 版本 / 种子回路数（写入基线报告的原始证据）；
2. legacy 宽表取数基线：参考数据（tests/refactor/reference_data，2h × 4 回路）
   种入 clpm_ts_ref.st_loop_data，经生产 TDengineProvider 取回，
   与参考真值比对（FLOAT 32 位舍入容差），记录冷/热时延与 SQL 次数；
3. 算法结果基线：DataPlanner（L1/L2 关闭，PG 契约表在环）+ 全部已装配指标
   计算器在参考窗口的输出，固化 golden（tests/golden/refactor_algorithm_baseline.json）。

golden 更新（仅基线建立/用户授权时）：
    REFACTOR_UPDATE_GOLDEN=1 uv run pytest tests/integration/ \
        -m integration   # （生成 golden 后去掉该环境变量复跑比对）
"""

from __future__ import annotations

import json
import os
import statistics
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.tdengine_native import batch_insert, execute_native
from tests.refactor import reference_data as rd

pytestmark = pytest.mark.integration

_GOLDEN_ALGO = Path(__file__).resolve().parents[1] / "golden" / "refactor_algorithm_baseline.json"
_GOLDEN_QUERY = Path(__file__).resolve().parents[1] / "golden" / "refactor_query_baseline.json"

_DB = settings.TDENGINE_DB


def _f32(v: float | None) -> float | None:
    """模拟 legacy 宽表 FLOAT(32) 列的存取舍入（预期侧对齐口径）."""
    if v is None:
        return None
    return struct.unpack("f", struct.pack("f", float(v)))[0]


@pytest.fixture(scope="module")
def ds() -> rd.ReferenceDataset:
    return rd.build_reference_dataset(hours=2.0)


@pytest.fixture(scope="module")
def td_ready(ds: rd.ReferenceDataset) -> dict[str, str]:
    """建库建表 + 种入参考数据（幂等），返回 loop_id → tag_name."""
    execute_native_sync(f"CREATE DATABASE IF NOT EXISTS {_DB} KEEP 365 DURATION 10 PRECISION 'ms'")
    execute_native_sync(
        f"""
        CREATE STABLE IF NOT EXISTS {_DB}.st_loop_data (
            ts TIMESTAMP, pv FLOAT, sp FLOAT, op FLOAT, mode TINYINT,
            pid_p FLOAT, pid_i FLOAT, pid_d FLOAT, pv_quality TINYINT
        ) TAGS (loop_id BINARY(36), unit_id BINARY(36))
        """
    )
    for loop_id, loop in ds.loops.items():
        rows = rd.wide_rows_from_dense(ds, loop_id)
        wrote = 0
        for i in range(0, len(rows), 1000):
            wrote += _run(
                batch_insert(
                    f"d_loop_{loop.tag_name.lower().replace('-', '_').replace('.', '_')}",
                    rows[i : i + 1000],
                    loop_id=loop_id,
                    unit_id=loop.unit_id,
                )
            )
        assert wrote == len(rows), f"种入 {loop.tag_name} 失败: {wrote}/{len(rows)}"
    return {lid: loop.tag_name for lid, loop in ds.loops.items()}


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def execute_native_sync(sql: str) -> list[dict]:
    return _run(execute_native(sql))


@pytest.fixture(scope="module")
def pg_ready(ds: rd.ReferenceDataset) -> list[str]:
    """种入参考回路/测点/映射（幂等：先删后插），返回 loop_id 列表."""
    import asyncio

    from sqlalchemy import delete

    async def _seed() -> list[str]:
        from app.core.db import AsyncSessionLocal
        from app.models.loop import LoopLedger, LoopTagMapping
        from app.models.tag import TagRegistry

        loop_ids = list(ds.loops.keys())
        all_point_ids = {p.point_id for loop in ds.loops.values() for p in loop.points.values()}
        async with AsyncSessionLocal() as session:
            # unit_id 受 plant_node 外键约束：复用种子装置节点（按名排序取前二，确定性）
            from sqlalchemy import text

            unit_rows = (
                (await session.execute(text("SELECT id FROM plant_node ORDER BY name LIMIT 2")))
                .scalars()
                .all()
            )
            unit_ids = [str(u) for u in unit_rows] or [None, None]
            unit_by_loop = {
                "REF-FIC-101": unit_ids[0],
                "REF-PIC-201": unit_ids[0],
                "REF-TIC-301": unit_ids[-1],
                "REF-LIC-401": unit_ids[-1],
            }
            await session.execute(
                delete(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids))
            )
            # 绑定历史先于 tag_registry 删除（FK RESTRICT；其它测试初始化产生）
            from app.models.point_history import LoopTagBindingHistory

            await session.execute(
                delete(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id.in_(loop_ids))
            )
            await session.execute(
                delete(TagRegistry).where(TagRegistry.id.in_(list(all_point_ids)))
            )
            await session.execute(delete(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
            for loop in ds.loops.values():
                session.add(
                    LoopLedger(
                        id=loop.loop_id,
                        tag_name=loop.tag_name,
                        unit_id=unit_by_loop.get(loop.tag_name),
                        control_type=loop.control_type,
                        loop_type=loop.control_type,
                        status="READY",
                        is_active=True,
                        include_in_evaluation=True,
                        description=f"refactor-baseline {loop.tag_name}",
                    )
                )
            await session.flush()
            seen_point_ids: set[str] = set()
            for loop in ds.loops.values():
                for role, point in loop.points.items():
                    if point.point_id in seen_point_ids:
                        continue  # 共享点只插一行 tag_registry
                    seen_point_ids.add(point.point_id)
                    session.add(
                        TagRegistry(
                            id=point.point_id,
                            tag_name=point.tag_name,
                            tag_type=role,
                            last_sync_at=datetime(2026, 9, 6),
                            is_linked=True,
                            range_min=loop.range_min if role == "PV" else None,
                            range_max=loop.range_max if role == "PV" else None,
                        )
                    )
            await session.flush()
            for loop in ds.loops.values():
                for role, point in loop.points.items():
                    session.add(
                        LoopTagMapping(
                            loop_id=loop.loop_id,
                            tag_id=point.point_id,
                            tag_role=role,
                            is_required=role in ("PV", "SP", "OP"),
                        )
                    )
            await session.commit()
        return loop_ids

    loop_ids = asyncio.run(_seed())
    yield loop_ids
    # teardown：清除本固件写入的回路（共享集成测试库不被 REF 回路污染，
    # 其它按种子数据断言的集成测试不受影响）

    async def _cleanup():
        from app.core.db import AsyncSessionLocal
        from app.models.loop import LoopLedger, LoopTagMapping
        from app.models.point_history import LoopTagBindingHistory
        from app.models.tag import TagRegistry

        ds_points = list({pt.point_id for lp in ds.loops.values() for pt in lp.points.values()})
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id.in_(loop_ids))
            )
            await session.execute(
                delete(LoopTagMapping).where(LoopTagMapping.loop_id.in_(loop_ids))
            )
            await session.execute(
                delete(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id.in_(loop_ids))
            )
            await session.execute(delete(TagRegistry).where(TagRegistry.id.in_(ds_points)))
            await session.execute(delete(LoopLedger).where(LoopLedger.id.in_(loop_ids)))
            await session.commit()

    asyncio.run(_cleanup())


class TestEnvInventory:
    def test_environment_recorded(self, td_ready, pg_ready):
        """P0-1/P0-6 环境证据：TD 版本、库表、PG 版本、回路数."""
        ver = execute_native_sync("SELECT SERVER_VERSION()")
        dbs = execute_native_sync("SHOW DATABASES")
        stable = execute_native_sync(f"SELECT COUNT(*) AS c FROM {_DB}.st_loop_data")
        loops = execute_native_sync(
            f"SELECT COUNT(*) AS c FROM (SELECT DISTINCT TBNAME AS t FROM {_DB}.st_loop_data)"
        )
        record = {
            "td_server_version": ver[0].get("server_version()"),
            "td_databases": sorted(r.get("name") for r in dbs),
            "stable_rows": stable[0].get("c"),
            "subtables": loops[0].get("c"),
            "loops_seeded": len(pg_ready),
            "captured_at": datetime.utcnow().isoformat(),
        }
        print("ENV:", json.dumps(record, ensure_ascii=False))


class TestLegacyQueryBaseline:
    def test_provider_query_matches_reference_and_measure(self, ds, td_ready, pg_ready):
        """生产 Provider 在 legacy 宽表上的取数正确性 + 冷/热时延 + SQL 次数."""
        import asyncio

        from app.core.db import AsyncSessionLocal
        from app.services.data_source.tdengine_provider import TDengineProvider

        loop_ids = pg_ready
        window_start = datetime(2026, 9, 6) + timedelta(seconds=600)
        window_end = window_start + timedelta(seconds=3599)  # 3600 点
        measurements: dict[str, dict] = {}
        sql_counts: dict[str, int] = {"wide": 0, "last": 0}

        async def _one_loop(db, provider, loop_id: str, tag_name: str):
            qfn = provider.make_query_fn(db)
            import app.core.tdengine_native as native

            orig_exec = native.execute_native

            async def _count_exec(sql: str) -> list[dict]:
                if sql.lstrip().upper().startswith("SELECT TS, PV"):
                    sql_counts["wide"] += 1
                elif "LAST(" in sql.upper():
                    sql_counts["last"] += 1
                return await orig_exec(sql)

            native.execute_native = _count_exec
            try:
                import tracemalloc

                tracemalloc.start()
                t0 = time.perf_counter()
                raw = await qfn(
                    loop_id,
                    ["pv", "sp", "op", "mode", "pid_p", "pid_i", "pid_d"],
                    window_start,
                    window_end,
                    1,
                )
                cold_ms = (time.perf_counter() - t0) * 1000
                _, peak_mem_kb = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                # 热查询（同窗重复，TDengine 页缓存命中路径）
                hot_ms_list = []
                for _ in range(5):
                    t0 = time.perf_counter()
                    await qfn(
                        loop_id,
                        ["pv", "sp", "op", "mode", "pid_p", "pid_i", "pid_d"],
                        window_start,
                        window_end,
                        1,
                    )
                    hot_ms_list.append((time.perf_counter() - t0) * 1000)
            finally:
                native.execute_native = orig_exec

            # 正确性：与参考真值逐点比对（FLOAT32 舍入容差）
            expected = rd.build_reference_raw_series(
                ds,
                loop_id,
                ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"],
                window_start,
                window_end,
            )
            assert len(raw.timestamps) == 3600, f"{tag_name}: 点数 {len(raw.timestamps)}"
            bad = 0
            for i in range(3600):
                for role in ("pv", "sp", "op"):
                    got = raw.signals[role][i]
                    exp = _f32(expected.signals[role][i])
                    if got is None and exp is None:
                        continue
                    if got is None or exp is None or abs(got - exp) > max(1e-4, abs(exp) * 1e-6):
                        bad += 1
                assert raw.signals["mode"][i] == expected.signals["mode"][i]
                assert raw.quality_codes["pv_quality"][i] == expected.quality_codes["pv_quality"][i]
            assert bad == 0, f"{tag_name}: {bad} 个数值点超出 FLOAT32 舍入容差"
            measurements[tag_name] = {
                "cold_ms": round(cold_ms, 2),
                "hot_ms_median": round(statistics.median(hot_ms_list), 2),
                "hot_ms_p95": round(sorted(hot_ms_list)[-1], 2),
                "peak_mem_kb_1h_query": round(peak_mem_kb / 1024, 1),
            }

        async def _main():
            provider = TDengineProvider()
            async with AsyncSessionLocal() as db:
                for loop_id in loop_ids:
                    await _one_loop(db, provider, loop_id, td_ready[loop_id])
            await provider.close()

        asyncio.run(_main())

        report = {
            "window": [window_start.isoformat(), window_end.isoformat()],
            "sql_per_loop_query": dict(sql_counts),
            "loops": measurements,
            "note": "cold=种入后首次读（页缓存可能已热）；hot=同窗重复 5 次取中位/最大",
        }
        print("QUERY-BASELINE:", json.dumps(report, ensure_ascii=False))
        _write_golden(_GOLDEN_QUERY, report)
        # 网格契约：每回路每次查询恰 2 条 SQL（宽表 + COV 初值）；
        # 计数含 cold(1) + hot(5) = 6 次/回路 × 4 回路 = 24
        assert sql_counts == {"wide": 24, "last": 24}


class TestAlgorithmBaseline:
    def test_algorithm_results_golden(self, ds, td_ready, pg_ready):
        """DataPlanner + 全部可装配指标计算器的结果基线（golden 固化）."""
        import asyncio

        from sqlalchemy import text

        from app.contracts.data_types import ControlType, TimeWindow
        from app.core.db import AsyncSessionLocal
        from app.services.data_planner import DataPlanner
        from app.services.metric_calculator import get_calculator
        from app.services.metric_data_bundle import MetricDataBundleAssembler

        window_start = datetime(2026, 9, 6) + timedelta(seconds=600)
        window_end = window_start + timedelta(seconds=3599)

        async def _run_baseline() -> dict:
            async with AsyncSessionLocal() as db:
                sql = "SELECT DISTINCT metric_code FROM clpm_metric_data_requirement"
                rows = (await db.execute(text(sql))).scalars().all()
                return sorted(str(r) for r in rows)

        metric_codes = asyncio.run(_run_baseline())
        results: dict[str, dict] = {}

        async def _compute():
            provider_ok = True
            from app.services.data_source.tdengine_provider import TDengineProvider

            provider = TDengineProvider()
            assembler = MetricDataBundleAssembler()
            async with AsyncSessionLocal() as db:
                for loop_id, tag_name in td_ready.items():
                    loop = ds.loop(loop_id)
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
                    results[tag_name] = per_loop
            if provider_ok:
                await provider.close()

        asyncio.run(_compute())

        summary = {
            "metric_codes": metric_codes,
            "window": [window_start.isoformat(), window_end.isoformat()],
            "results": results,
        }
        print("ALGO-BASELINE loops:", {k: len(v) for k, v in results.items()})
        assert all(len(v) > 0 for v in results.values()), "存在回路未产出任何指标"

        if os.environ.get("REFACTOR_UPDATE_GOLDEN") == "1":
            _write_golden(_GOLDEN_ALGO, summary)
            return
        assert _GOLDEN_ALGO.exists(), "先以 REFACTOR_UPDATE_GOLDEN=1 生成算法基线"
        baseline = json.loads(_GOLDEN_ALGO.read_text())
        _assert_algo_equal(baseline, summary)


def _assert_algo_equal(baseline: dict, current: dict) -> None:
    assert set(baseline["results"]) == set(current["results"]), "回路集合漂移"
    for loop, metrics in baseline["results"].items():
        cur = current["results"][loop]
        assert set(metrics) == set(cur), f"{loop}: 指标集合漂移 {set(metrics) ^ set(cur)}"
        for code, exp in metrics.items():
            got = cur[code]
            assert got["confidence"] == exp["confidence"], (
                f"{loop}/{code}: 可信度 {exp['confidence']} → {got['confidence']}"
            )
            ev, gv = exp["value"], got["value"]
            if ev is None or gv is None:
                assert ev == gv, f"{loop}/{code}: None 漂移 {ev} → {gv}"
            else:
                assert abs(gv - ev) <= max(1e-9, abs(ev) * 1e-9), f"{loop}/{code}: {ev} → {gv}"


def _write_golden(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
