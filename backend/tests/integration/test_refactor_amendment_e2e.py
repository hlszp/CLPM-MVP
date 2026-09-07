"""补充方案 §6 用例 1/9 端到端：三条真实入口经 point 布局 + L1 冷热缓存.

运行（隔离环境）：
    cd backend && uv run pytest tests/integration/test_refactor_amendment_e2e.py -m integration -q

- 用例 1：密集参考→COV→逻辑宽表→评估（DataPlanner）/诊断（编排器）/
  整定（identify_model_from_history）三条真实入口，无 gap+质量正确+配置一致时
  与 legacy 对照等价（不能只比 Provider 输出）；
- 用例 9：L1 冷/热缓存往返——上下文/control_type/validity 一致，
  FAST/SLOW 参数选择不丢（I06 曾实测往返 FAST→None）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete

from tests.refactor import reference_data as rd

pytestmark = pytest.mark.integration

ALL_ROLES = ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]


@pytest.fixture(scope="module")
def ds() -> rd.ReferenceDataset:
    # 与 P0 golden 同参（hours=2.0 同种子——数据不同则指标必然不同）
    return rd.build_reference_dataset(hours=2.0)


@pytest.fixture(scope="module")
async def seeded(ds):
    from tests.integration.test_refactor_provider_differential import _seed_pg, _seed_td

    await _seed_pg(ds)
    await _seed_td(ds)
    yield ds


@pytest.fixture
async def point_manifest():
    from app.core.db import AsyncSessionLocal
    from app.models.point_history import HistoryLayoutManifest
    from app.services.data_source import point_history_metadata as meta
    from app.services.data_source.history_layout_router import invalidate_manifest_cache

    async with AsyncSessionLocal() as db:
        entry = await meta.set_layout(
            db,
            layout="point",
            valid_from=datetime(2026, 9, 5, tzinfo=UTC),
            scope_type="global",
            basis="amendment-e2e",
        )
        await db.commit()
    invalidate_manifest_cache()
    yield entry.id
    async with AsyncSessionLocal() as db:
        await db.execute(delete(HistoryLayoutManifest))
        await db.commit()
    invalidate_manifest_cache()


def _win(ds, off_start, off_end):
    base = datetime.fromtimestamp(ds.start_s, UTC)
    return base + timedelta(seconds=off_start), base + timedelta(seconds=off_end)


class TestThreeRealChains:
    async def test_evaluation_chain_point_equals_legacy_golden(self, ds, seeded, point_manifest):
        """用例 1a：评估链（DataPlanner→计算器）point 结果与 legacy golden 全等."""

        from sqlalchemy import text

        from app.contracts.data_types import ControlType, TimeWindow
        from app.core.db import AsyncSessionLocal
        from app.services.data_planner import DataPlanner
        from app.services.data_source.tdengine_provider import TDengineProvider
        from app.services.metric_calculator import get_calculator
        from app.services.metric_data_bundle import MetricDataBundleAssembler

        golden = __import__("json").loads(
            (
                Path(__file__).resolve().parents[1] / "golden" / "refactor_algorithm_baseline.json"
            ).read_text()
        )
        loop_id, loop = next(iter(ds.loops.items()))
        start, end = _win(ds, 600, 600 + 3599)

        async with AsyncSessionLocal() as db:
            codes = sorted(
                str(r)
                for r in (
                    await db.execute(
                        text("SELECT DISTINCT metric_code FROM clpm_metric_data_requirement")
                    )
                )
                .scalars()
                .all()
            )
            provider = TDengineProvider()
            planner = DataPlanner(
                cache=None,
                tdengine_query_fn=provider.make_query_fn(db),
                assembler=MetricDataBundleAssembler(),
                db=db,
            )
            bundles = await planner.request_bundles(
                loop_id,
                codes,
                TimeWindow(start=start, end=end),
                ControlType(loop.control_type),
            )
            await provider.close()
        exp = golden["results"][loop.tag_name]
        matched = 0
        for bundle in bundles:
            calc = get_calculator(bundle.metric_code)
            if calc is None:
                continue
            res = calc.calculate(bundle)
            expected = exp.get(bundle.metric_code)
            if expected is None:
                continue
            matched += 1
            assert res.confidence_level == expected["confidence"], (
                f"{bundle.metric_code}: {expected['confidence']} → {res.confidence_level}"
            )
            ev, gv = expected["value"], res.value
            if ev is None or gv is None:
                assert ev == gv
            else:
                assert abs(gv - ev) <= max(1e-9, abs(ev) * 1e-9)
        assert matched >= 20  # 全部可计算指标覆盖

    async def test_tuning_chain_point_success_and_full_rate(self, ds, seeded, point_manifest):
        """用例 1c：整定链真实入口——point 同轴直通辨识成功；可信度=全窗口径."""
        from app.core.db import AsyncSessionLocal
        from app.services.tuning import identify_model_from_history

        loop_id = next(iter(ds.loops))
        start, end = _win(ds, 100, 4600)
        async with AsyncSessionLocal() as db:
            result = await identify_model_from_history(
                db, str(loop_id), start.isoformat(), end.isoformat()
            )
        assert result.get("success") is True, result
        # 全窗口径：数据无 gap/坏质量 → 1.0（不是选段后的冒充值）
        assert result.get("validRate") == 1.0
        assert result.get("modelType") in ("FOPDT", "SOPDT")

    async def test_diagnosis_chain_point_gate_passes(self, ds, seeded, point_manifest):
        """用例 1b：诊断链真实入口——point 网格门禁通过且算子同轴执行."""
        from app.core.db import AsyncSessionLocal
        from app.services.diagnosis_orchestrator import run_diagnosis_for_loop

        loop_id, loop = next(iter(ds.loops.items()))
        start, end = _win(ds, 100, 3700)
        async with AsyncSessionLocal() as db:
            task_id = f"e2e-{uuid.uuid4().hex[:8]}"
            run = await run_diagnosis_for_loop(
                db,
                loop_id,
                task_id=task_id,
                start=start.replace(tzinfo=None),
                end=end.replace(tzinfo=None),
                triggered_by="e2e",
                trigger_type="MANUAL",
            )
            await db.rollback()  # 不落库（e2e 只验链路）
        assert run is not None
        gate = run.data_gate
        assert gate["passed"] is True, gate
        assert gate["expectedPoints"] == 3601  # 网格 N（上下文）
        assert gate["pointCount"] >= 3600  # 可用有效样本≈全窗
        assert run.status in ("SUCCESS", "PARTIAL")


class TestL1ColdHotCache:
    async def test_control_type_and_context_survive_roundtrip(self, ds, seeded, point_manifest):
        """用例 9：L1 冷（写）/热（读）——control_type（I06 曾丢）与上下文不丢."""
        from app.contracts.data_types import ControlType, TimeWindow
        from app.core.db import AsyncSessionLocal
        from app.core.redis import redis_client
        from app.services.cache.invalidation import CacheInvalidator
        from app.services.cache.l1_datablock import L1DataBlockCache
        from app.services.data_planner import DataPlanner
        from app.services.data_source.tdengine_provider import TDengineProvider
        from app.services.metric_data_bundle import MetricDataBundleAssembler

        loop_id, loop = next(iter(ds.loops.items()))
        start, end = _win(ds, 600, 600 + 3599)
        # 清 L1（冷起点）
        await CacheInvalidator(redis_client).invalidate_all()

        provider = TDengineProvider()
        metrics = ["valve_linearity", "error_mean", "auto_mode_rate"]

        async def _request() -> list:
            async with AsyncSessionLocal() as db:
                planner = DataPlanner(
                    cache=L1DataBlockCache(redis_client),
                    tdengine_query_fn=provider.make_query_fn(db),
                    assembler=MetricDataBundleAssembler(),
                    db=db,
                )
                bundles = await planner.request_bundles(
                    loop_id,
                    metrics,
                    TimeWindow(start=start, end=end),
                    ControlType(loop.control_type),
                )
                blocks = {b.data_block.tag_group: b.data_block for b in bundles}
                return [
                    blocks.get("BASE"),
                    blocks.get("PVOP_HF"),
                ]

        cold_base, cold_pvop = await _request()
        assert cold_base is not None and cold_pvop is not None
        assert cold_base.control_type is not None  # 冷路径不丢
        assert cold_base.series_context is not None and cold_base.series_context.is_point

        hot_base, hot_pvop = await _request()  # 热路径（L1 命中）
        assert hot_base is not None and hot_pvop is not None
        # I06 修复验证：热命中后 control_type 不再是 None
        assert hot_base.control_type == cold_base.control_type
        # AD02：上下文冷热一致（数据/网格/覆盖语义相同）
        assert hot_base.series_context is not None
        assert hot_base.series_context.layout == cold_base.series_context.layout
        assert hot_base.series_context.dataset_ref == cold_base.series_context.dataset_ref
        assert hot_base.series_context.expected_slots == cold_base.series_context.expected_slots
        assert hot_base.validity.keys() == cold_base.validity.keys()
        await provider.close()
        await CacheInvalidator(redis_client).invalidate_all()
