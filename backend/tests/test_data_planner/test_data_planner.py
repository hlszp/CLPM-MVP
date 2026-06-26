"""DataPlanner 核心集成测试.

测试要点：
    - 查询计划合并（5 指标 → 4 tagGroup）
    - 缓存命中时不查 TDengine
    - 缓存未命中时查 TDengine + 预处理 + 写缓存
    - tagGroup 复用（流量回路仅 1 次 TDengine 查询）
    - MetricDataBundle 正确组装

设计依据：数据流程图 §7.1, 算法说明 §3.5.2
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.contracts.data_types import (
    ControlType,
    LoopPreprocessConfig,
    TagGroup,
    TimeWindow,
)
from app.services.cache.l1_datablock import L1DataBlockCache
from app.services.data_planner import DataPlanner
from app.services.metric_data_bundle import MetricDataBundleAssembler
from app.services.preprocessing.pipeline import PREPROCESS_VERSION

from .conftest import (
    build_data_block,
    build_raw_timeseries,
    build_requirement,
    FakeCacheRedis,
)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_db(requirements: list) -> AsyncMock:
    """构造 mock db，execute 返回 requirements 列表."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = requirements
    db.execute = AsyncMock(return_value=result)
    return db


def _make_config_loader(
    control_type: ControlType = ControlType.TEMPERATURE,
    range_min: float = 0.0,
    range_max: float = 100.0,
    config_version: str = "cfg_1000",
):
    """构造 mock config_loader."""

    async def loader(loop_id: str, ctrl: ControlType) -> LoopPreprocessConfig:
        return LoopPreprocessConfig(
            loop_id=loop_id,
            control_type=ctrl,
            range_min=range_min,
            range_max=range_max,
            config_version=config_version,
        )

    return loader


def _make_query_fn(call_log: list, return_n: int = 100):
    """构造 mock TDengine 查询函数，记录调用参数."""

    async def query_fn(loop_id, tag_roles, start, end, interval_s):
        call_log.append(
            {"loop_id": loop_id, "tags": list(tag_roles), "interval_s": interval_s}
        )
        return build_raw_timeseries(n=return_n, interval_s=float(interval_s), tags=tag_roles)

    return query_fn


def _five_metrics_requirements() -> list:
    """构造 5 指标的契约（覆盖 4 个 tagGroup）."""
    return [
        build_requirement(
            "accuracy_rate", TagGroup.BASE, ["pv", "sp"],
            mask_expression="pv_valid && sp_valid",
        ),
        build_requirement(
            "stability_rate", TagGroup.BASE, ["pv", "sp"],
            mask_expression="pv_valid && sp_valid",
        ),
        build_requirement(
            "output_trip_index", TagGroup.OP_HF, ["op"],
            mask_expression="op_valid && consecutive_valid",
            sampling_strategy="FIXED_1S",
        ),
        build_requirement(
            "stiction_index", TagGroup.PVOP_HF, ["pv", "op"],
            mask_expression="pv_valid && op_valid",
            sampling_strategy="FIXED_1S",
        ),
        build_requirement(
            "good_value_rate", TagGroup.QUALITY_HF, ["pv_quality"],
            mask_expression=None,
            sampling_strategy="FIXED_1S",
            quality_policy="KEEP_ALL",
        ),
    ]


def _time_window() -> TimeWindow:
    return TimeWindow(
        start=datetime(2024, 1, 1, 10, 0, 0),
        end=datetime(2024, 1, 1, 11, 0, 0),
    )


def _base_cache_key(
    loop_id: str = "TC101",
    sampling_freq: str = "5s",
    cfg_version: str = "cfg_1000",
    tw: TimeWindow | None = None,
) -> str:
    """构造与 DataPlanner._execute_query_plan 一致的 BASE 缓存 Key.

    用于测试预写缓存时确保 get/set Key 一致。
    """
    if tw is None:
        tw = _time_window()
    return L1DataBlockCache.build_key(
        loop_id=loop_id,
        tag_group=TagGroup.BASE.value,
        time_window_start=tw.start,
        time_window_end=tw.end,
        sampling_freq=sampling_freq,
        quality_policy="KEEP_ALL_WITH_VALIDITY",
        pre_version=PREPROCESS_VERSION,
        cfg_version=cfg_version,
    )


# ---------------------------------------------------------------------------
# 查询计划合并测试
# ---------------------------------------------------------------------------


class TestQueryPlanMerge:
    """查询计划合并（5 指标 → 4 tagGroup）."""

    @pytest.mark.asyncio
    async def test_five_metrics_merge_to_four_tag_groups(self) -> None:
        """5 指标（accuracy+stability 同属 BASE）应合并为 4 个 tagGroup 查询."""
        requirements = _five_metrics_requirements()
        db = _make_db(requirements)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=[r.metric_code for r in requirements],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # 温度回路（BASE=5s，非 1s），不复用 → 4 次查询
        assert len(query_log) == 4
        # 5 个指标 → 5 个 Bundle（CONFIG 不在其中）
        assert len(bundles) == 5
        # accuracy 和 stability 共享 BASE
        accuracy = next(b for b in bundles if b.metric_code == "accuracy_rate")
        stability = next(b for b in bundles if b.metric_code == "stability_rate")
        assert accuracy.data_block is stability.data_block

    @pytest.mark.asyncio
    async def test_config_metric_skipped(self) -> None:
        """CONFIG tagGroup 指标应被跳过（不生成 Bundle）."""
        reqs = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
            build_requirement("ideal_settling_time", TagGroup.CONFIG, [], None),
        ]
        db = _make_db(reqs)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=[r.metric_code for r in reqs],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # CONFIG 跳过，只 1 个 Bundle
        assert len(bundles) == 1
        assert bundles[0].metric_code == "accuracy_rate"
        # 只查 BASE 1 次
        assert len(query_log) == 1


# ---------------------------------------------------------------------------
# 缓存命中/未命中测试
# ---------------------------------------------------------------------------


class TestCacheHitMiss:
    """缓存命中与未命中行为."""

    @pytest.mark.asyncio
    async def test_cache_miss_queries_tdengine_and_writes_cache(self) -> None:
        """缓存未命中时应查 TDengine + 预处理 + 写缓存."""
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)
        redis = FakeCacheRedis()
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(redis),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        assert len(bundles) == 1
        # 未命中 → 查了 TDengine
        assert len(query_log) == 1
        # 写入缓存
        assert len(redis.keys) == 1

    @pytest.mark.asyncio
    async def test_cache_hit_skips_tdengine(self) -> None:
        """缓存命中时不应查 TDengine."""
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)
        redis = FakeCacheRedis()
        cache = L1DataBlockCache(redis)

        # 预先写入缓存（使用与 _execute_query_plan 一致的 Key）
        pre_block = build_data_block(
            loop_id="TC101",
            tag_group=TagGroup.BASE,
            n=100,
            sampling_freq="5s",
            valid_rate=0.95,
            config_version="cfg_1000",
        )
        await cache.set(pre_block, key=_base_cache_key())

        query_log: list = []
        planner = DataPlanner(
            cache=cache,
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        assert len(bundles) == 1
        # 命中 → 不查 TDengine
        assert len(query_log) == 0
        # 缓存 Key 数不变（命中不写）
        assert len(redis.keys) == 1

    @pytest.mark.asyncio
    async def test_partial_cache_hit_only_queries_missed(self) -> None:
        """部分缓存命中时只查未命中的 tagGroup."""
        requirements = _five_metrics_requirements()
        db = _make_db(requirements)
        redis = FakeCacheRedis()
        cache = L1DataBlockCache(redis)

        # 预先写入 BASE 缓存（命中，使用与 _execute_query_plan 一致的 Key）
        base_block = build_data_block(
            loop_id="TC101",
            tag_group=TagGroup.BASE,
            n=100,
            sampling_freq="5s",
            valid_rate=0.95,
            config_version="cfg_1000",
        )
        await cache.set(base_block, key=_base_cache_key())

        query_log: list = []
        planner = DataPlanner(
            cache=cache,
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=[r.metric_code for r in requirements],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # BASE 命中，OP_HF/PVOP_HF/QUALITY_HF 未命中 → 3 次查询
        assert len(query_log) == 3
        assert len(bundles) == 5


# ---------------------------------------------------------------------------
# tagGroup 复用测试（流量回路）
# ---------------------------------------------------------------------------


class TestTagGroupReuse:
    """流量回路 tagGroup 复用测试（算法说明 §3.5.2）."""

    @pytest.mark.asyncio
    async def test_flow_loop_single_query_reuses_base(self) -> None:
        """流量回路（FC，BASE=1s）应仅查 1 次 TDengine，HF tagGroup 复用 BASE."""
        requirements = _five_metrics_requirements()
        db = _make_db(requirements)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.FLOW),
        )

        bundles = await planner.request_bundles(
            loop_id="FC201",
            metrics=[r.metric_code for r in requirements],
            time_window=_time_window(),
            control_type=ControlType.FLOW,
        )

        # 流量回路 BASE=1s → 全部 HF tagGroup 复用 BASE → 仅 1 次查询
        assert len(query_log) == 1
        # 查询的 tags 应包含所有 HF 需要的 tag（pv, sp, op, pv_quality）
        queried_tags = set(query_log[0]["tags"])
        assert "pv" in queried_tags
        assert "sp" in queried_tags
        assert "op" in queried_tags
        # 5 个 Bundle 都应生成
        assert len(bundles) == 5

    @pytest.mark.asyncio
    async def test_flow_loop_derived_blocks_share_timestamps(self) -> None:
        """流量回路派生的 DataBlock 应与 BASE 共享时间戳."""
        requirements = _five_metrics_requirements()
        db = _make_db(requirements)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log, return_n=50),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.FLOW),
        )

        bundles = await planner.request_bundles(
            loop_id="FC201",
            metrics=[r.metric_code for r in requirements],
            time_window=_time_window(),
            control_type=ControlType.FLOW,
        )

        base_bundle = next(b for b in bundles if b.metric_code == "accuracy_rate")
        op_bundle = next(b for b in bundles if b.metric_code == "output_trip_index")
        # 派生的 OP_HF block 应与 BASE block 共享时间戳
        assert base_bundle.data_block.timestamps == op_bundle.data_block.timestamps
        assert base_bundle.data_block.point_count == op_bundle.data_block.point_count
        # tag_group 不同
        assert base_bundle.data_block.tag_group == "BASE"
        assert op_bundle.data_block.tag_group == "OP_HF"

    @pytest.mark.asyncio
    async def test_non_flow_loop_no_reuse(self) -> None:
        """非流量回路（如温度 TC，BASE=5s）不应复用，每个 HF tagGroup 独立查询."""
        requirements = _five_metrics_requirements()
        db = _make_db(requirements)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        await planner.request_bundles(
            loop_id="TC101",
            metrics=[r.metric_code for r in requirements],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # 温度回路 BASE=5s（非 1s）→ 4 次查询
        assert len(query_log) == 4
        # BASE 用 5s 采样，HF 用 1s
        base_query = next(q for q in query_log if q["interval_s"] == 5)
        hf_queries = [q for q in query_log if q["interval_s"] == 1]
        assert base_query is not None
        assert len(hf_queries) == 3


# ---------------------------------------------------------------------------
# Pipeline 批量写入测试
# ---------------------------------------------------------------------------


class TestPipelineBatchWrite:
    """DataPlanner 通过 Pipeline 批量写入未命中的 DataBlock."""

    @pytest.mark.asyncio
    async def test_multiple_misses_use_single_pipeline(self) -> None:
        """多个未命中的 DataBlock 应通过一次 Pipeline 批量写入."""
        requirements = _five_metrics_requirements()
        db = _make_db(requirements)
        redis = FakeCacheRedis()
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(redis),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        await planner.request_bundles(
            loop_id="TC101",
            metrics=[r.metric_code for r in requirements],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # 4 个未命中 DataBlock → 1 次 Pipeline 调用
        assert redis.pipeline_calls == 1
        assert len(redis.keys) == 4


# ---------------------------------------------------------------------------
# Bundle 组装验证
# ---------------------------------------------------------------------------


class TestBundleAssembly:
    """MetricDataBundle 组装正确性."""

    @pytest.mark.asyncio
    async def test_bundle_lineage_complete(self) -> None:
        """Bundle 应包含完整的数据血缘."""
        requirements = [
            build_requirement(
                "accuracy_rate", TagGroup.BASE, ["pv", "sp"],
                "pv_valid && sp_valid",
                aggregation_policy="LAST",
                quality_policy="KEEP_ALL_WITH_VALIDITY",
            ),
        ]
        db = _make_db(requirements)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log, return_n=100),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        assert len(bundles) == 1
        bundle = bundles[0]
        lineage = bundle.lineage
        assert lineage.tag_group == "BASE"
        assert lineage.sampling_freq == "5s"
        assert lineage.aggregation_policy == "LAST"
        assert lineage.quality_policy == "KEEP_ALL_WITH_VALIDITY"
        assert lineage.algorithm_version == "KPI_CALC_v2.0"
        assert len(lineage.data_block_ids) == 1

    @pytest.mark.asyncio
    async def test_bundle_mask_applied(self) -> None:
        """Bundle 的 masked_indices 应正确应用 mask_expression."""
        requirements = [
            build_requirement(
                "accuracy_rate", TagGroup.BASE, ["pv", "sp"],
                "pv_valid && sp_valid",
            ),
        ]
        db = _make_db(requirements)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log, return_n=100),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        bundle = bundles[0]
        # mask 应已应用，masked_indices 非空
        assert len(bundle.masked_indices) > 0
        assert bundle.mask_expression == "pv_valid && sp_valid"


# ---------------------------------------------------------------------------
# 边界与异常测试
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界与异常场景."""

    @pytest.mark.asyncio
    async def test_empty_metrics_returns_empty(self) -> None:
        """空指标列表应返回空 Bundle 列表."""
        db = _make_db([])
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn([]),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(),
        )
        bundles = await planner.request_bundles(
            "L001", [], _time_window(), ControlType.TEMPERATURE
        )
        assert bundles == []

    @pytest.mark.asyncio
    async def test_tdengine_empty_data_returns_empty_bundle(self) -> None:
        """TDengine 返回空数据时应返回空 DataBlock 的 Bundle."""
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)

        async def empty_query_fn(loop_id, tags, start, end, interval_s):
            from app.contracts.data_types import RawTimeSeries
            return RawTimeSeries(timestamps=[], signals={})

        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=empty_query_fn,
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(),
        )

        bundles = await planner.request_bundles(
            "L001", ["accuracy_rate"], _time_window(), ControlType.TEMPERATURE
        )
        # 空 DataBlock 仍生成 Bundle（mask 为空）
        assert len(bundles) == 1
        assert bundles[0].data_block.point_count == 0
        assert bundles[0].masked_indices == []

    @pytest.mark.asyncio
    async def test_no_db_returns_empty_when_no_config_loader(self) -> None:
        """无 db 且无 config_loader 时，_load_requirements 返回空."""
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn([]),
            assembler=MetricDataBundleAssembler(),
            db=None,
        )
        bundles = await planner.request_bundles(
            "L001", ["accuracy_rate"], _time_window(), ControlType.TEMPERATURE
        )
        assert bundles == []

    @pytest.mark.asyncio
    async def test_repeated_request_uses_cache(self) -> None:
        """相同请求第二次应命中缓存，不查 TDengine."""
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)
        redis = FakeCacheRedis()
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(redis),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        tw = _time_window()
        # 第一次请求（未命中）
        await planner.request_bundles("TC101", ["accuracy_rate"], tw, ControlType.TEMPERATURE)
        first_queries = len(query_log)
        assert first_queries == 1

        # 第二次请求（应命中缓存）
        await planner.request_bundles("TC101", ["accuracy_rate"], tw, ControlType.TEMPERATURE)
        assert len(query_log) == first_queries  # 无新增查询
