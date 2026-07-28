"""DataPlanner 核心集成测试.

测试要点：
    - 查询计划合并（5 指标 → 4 tagGroup）
    - 缓存命中时不查 TDengine
    - 缓存未命中时查 TDengine + 预处理 + 写缓存
    - tagGroup 复用（存在 BASE 组时所有控制类型仅 1 次 TDengine 查询）
    - MetricDataBundle 正确组装

设计依据：数据流程图 §7.1, 算法说明 §3.5.2
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

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
    FakeCacheRedis,
    build_data_block,
    build_raw_timeseries,
    build_requirement,
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
        call_log.append({"loop_id": loop_id, "tags": list(tag_roles), "interval_s": interval_s})
        return build_raw_timeseries(n=return_n, interval_s=float(interval_s), tags=tag_roles)

    return query_fn


def _five_metrics_requirements() -> list:
    """构造 5 指标的契约（覆盖 4 个 tagGroup）."""
    return [
        build_requirement(
            "accuracy_rate",
            TagGroup.BASE,
            ["pv", "sp"],
            mask_expression="pv_valid && sp_valid",
        ),
        build_requirement(
            "stability_rate",
            TagGroup.BASE,
            ["pv", "sp"],
            mask_expression="pv_valid && sp_valid",
        ),
        build_requirement(
            "output_trip_index",
            TagGroup.OP_HF,
            ["op"],
            mask_expression="op_valid && consecutive_valid",
            sampling_strategy="FIXED_1S",
        ),
        build_requirement(
            "stiction_index",
            TagGroup.PVOP_HF,
            ["pv", "op"],
            mask_expression="pv_valid && op_valid",
            sampling_strategy="FIXED_1S",
        ),
        build_requirement(
            "good_value_rate",
            TagGroup.QUALITY_HF,
            ["pv_quality"],
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
        """5 指标（accuracy+stability 同属 BASE）应合并为 4 个 tagGroup，仅 1 次查询."""
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

        # 存在 BASE 组时所有控制类型复用 BASE：HF 组派生 → 仅 1 次查询
        assert len(query_log) == 1
        # BASE 查询的 tags 为所有组并集（含 HF 需要的 op / pv_quality）
        queried_tags = set(query_log[0]["tags"])
        assert {"pv", "sp", "op", "pv_quality"} <= queried_tags
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

        # BASE 命中缓存；OP_HF/PVOP_HF/QUALITY_HF 复用 BASE 派生 → 0 次查询
        assert len(query_log) == 0
        assert len(bundles) == 5


# ---------------------------------------------------------------------------
# tagGroup 复用测试（流量回路）
# ---------------------------------------------------------------------------


class TestTagGroupReuse:
    """tagGroup 复用测试：存在 BASE 组时所有控制类型复用 BASE（算法说明 §3.5.2）."""

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
    async def test_non_flow_loop_reuses_base(self) -> None:
        """非流量回路（如温度 TC）同样复用 BASE：仅 1 次查询，HF 组从 BASE 派生."""
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

        # 温度回路 BASE=5s（非 1s）→ 同样仅 1 次 BASE 查询（interval 保持控制类型口径）
        assert len(query_log) == 1
        assert query_log[0]["interval_s"] == 5
        # 派生的 HF 组与 BASE 共享时间戳，5 个 Bundle 全部生成
        assert len(bundles) == 5
        base_bundle = next(b for b in bundles if b.metric_code == "accuracy_rate")
        op_bundle = next(b for b in bundles if b.metric_code == "output_trip_index")
        assert base_bundle.data_block.timestamps == op_bundle.data_block.timestamps

    @pytest.mark.asyncio
    async def test_hf_only_metrics_without_base_query_independently(self) -> None:
        """计划中无 BASE 组时（如波形接口按单 tagGroup 取数），HF 组保持独立查询."""
        requirements = [
            build_requirement(
                "good_value_rate",
                TagGroup.QUALITY_HF,
                ["pv_quality"],
                mask_expression=None,
                sampling_strategy="FIXED_1S",
                quality_policy="KEEP_ALL",
            ),
        ]
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
            metrics=["good_value_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # 无 BASE 组可派生 → QUALITY_HF 独立查询（固定 1s），行为与之前一致
        assert len(query_log) == 1
        assert query_log[0]["interval_s"] == 1
        assert len(bundles) == 1


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

        # 复用 BASE 后仅 BASE 1 个未命中 DataBlock 需要写入 → 1 次 Pipeline 调用
        assert redis.pipeline_calls == 1
        assert len(redis.keys) == 1


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
                "accuracy_rate",
                TagGroup.BASE,
                ["pv", "sp"],
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
                "accuracy_rate",
                TagGroup.BASE,
                ["pv", "sp"],
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
        bundles = await planner.request_bundles("L001", [], _time_window(), ControlType.TEMPERATURE)
        assert bundles == []

    @pytest.mark.asyncio
    async def test_tdengine_empty_data_returns_empty_bundle(self) -> None:
        """TDengine 返回空数据时应返回空 DataBlock 的 Bundle."""
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)

        async def empty_query_fn(loop_id, tag_roles, start, end, interval_s):
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
    async def test_empty_data_block_not_written_to_l1(self) -> None:
        """TDengine 返回空数据时，空 DataBlock 不应写入 L1（禁止负缓存）.

        修复问题：「先算后导」场景下空块进 L1（TTL 3600s），
        backfill 补齐数据后最长 1h 仍命中空块。
        """
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)
        redis = FakeCacheRedis()
        query_log: list = []

        async def empty_query_fn(loop_id, tag_roles, start, end, interval_s):
            from app.contracts.data_types import RawTimeSeries

            query_log.append({"loop_id": loop_id, "tags": list(tag_roles)})
            return RawTimeSeries(timestamps=[], signals={})

        planner = DataPlanner(
            cache=L1DataBlockCache(redis),
            tdengine_query_fn=empty_query_fn,
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(),
        )

        bundles = await planner.request_bundles(
            "L001", ["accuracy_rate"], _time_window(), ControlType.TEMPERATURE
        )
        # 空 DataBlock 仍返回 Bundle（INCONCLUSIVE 口径不变）
        assert len(bundles) == 1
        assert bundles[0].data_block.point_count == 0
        # 空块不写入 L1（无负缓存）
        assert len(redis.keys) == 0

        # 第二次请求：无负缓存兜底，应重新回源查询
        await planner.request_bundles(
            "L001", ["accuracy_rate"], _time_window(), ControlType.TEMPERATURE
        )
        assert len(query_log) == 2
        assert len(redis.keys) == 0

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


# ---------------------------------------------------------------------------
# P3 #56: KPI 计算路径不进行 LTTB 降采样
# ---------------------------------------------------------------------------


class TestKpiPathNoLttbDownsampling:
    """验证 KPI 计算路径使用控制类型阈值决定采样率，不调用 LTTB 降采样.

    设计依据：data_planner.py 模块 docstring「采样策略（P3 #56 文档对齐）」，
    AGENTS.md §性能边界 "LTTB 降采样 maxPoints=2000" 仅约束波形展示路径
    （monitor.py::lttb_downsample + waveform.py::lttb_downsample_multi_series）。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "control_type, expected_base_interval",
        [
            (ControlType.FLOW, 1),
            (ControlType.PRESSURE, 2),
            (ControlType.TEMPERATURE, 5),
            (ControlType.LEVEL, 5),
            (ControlType.COMPOSITION, 10),
        ],
    )
    async def test_kpi_base_interval_from_control_type(
        self, control_type: ControlType, expected_base_interval: int
    ) -> None:
        """KPI 路径 BASE tagGroup 的 interval_s 来自 ``get_threshold(control_type)``.

        非 LTTB 降采样推导出的阈值（如固定 2000 点）。验证 5 种控制类型均按
        阈值表使用 1/2/5/5/10 秒。
        """
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)
        query_log: list = []
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(control_type),
        )

        await planner.request_bundles(
            loop_id="L001",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=control_type,
        )

        assert len(query_log) == 1
        # interval_s 来自控制类型阈值表，而非 LTTB 降采样推导
        assert query_log[0]["interval_s"] == expected_base_interval

    @pytest.mark.asyncio
    async def test_kpi_hf_tag_group_always_1s(self) -> None:
        """非 FC 回路的查询计划也只产生 1 个非复用 QueryTask（BASE），HF 组派生.

        改动后所有控制类型复用 BASE：HF tagGroup（OP_HF/PVOP_HF/MODE_HF/
        QUALITY_HF）一律 ``reused_from=BASE``，不再独立查询；派生组
        ``interval_s`` 固定 1s（与原独立 HF 查询取值一致，仅为元数据），
        BASE 组保持控制类型采样率（TC=5s）。不进入 LTTB 降采样路径。
        """
        requirements = {r.metric_code: r for r in _five_metrics_requirements()}
        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=_make_query_fn([]),
            assembler=MetricDataBundleAssembler(),
            db=None,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        plan = planner._build_query_plan(requirements, ControlType.TEMPERATURE)  # noqa: SLF001

        # 仅 1 个非复用 QueryTask：BASE，interval 保持控制类型口径（5s），tags 为并集
        non_reuse = [t for t in plan if t.reused_from is None]
        assert len(non_reuse) == 1
        base_task = non_reuse[0]
        assert base_task.tag_group == TagGroup.BASE
        assert base_task.interval_s == 5
        assert set(base_task.tag_roles) == {"pv", "sp", "op", "pv_quality"}
        # 3 个 HF 组全部标记 reused_from=BASE，interval_s 固定 1s
        reuse = [t for t in plan if t.reused_from is not None]
        assert len(reuse) == 3
        for task in reuse:
            assert task.reused_from == TagGroup.BASE
            assert task.interval_s == 1
        assert {t.tag_group for t in reuse} == {
            TagGroup.OP_HF,
            TagGroup.PVOP_HF,
            TagGroup.QUALITY_HF,
        }

    @pytest.mark.asyncio
    async def test_kpi_large_window_no_lttb_threshold(self) -> None:
        """KPI 路径在大时间窗下不触发 LTTB 阈值（10000 点）.

        即使时间窗内数据点数远超 monitor.py LTTB_THRESHOLD=10000，KPI 路径
        仍按控制类型 interval_s 查询全量数据，不做降采样。
        """
        # 1 小时 × 1Hz = 3600 点；扩展为 4 小时 = 14400 点（> LTTB_THRESHOLD=10000）
        tw = TimeWindow(
            start=datetime(2024, 1, 1, 10, 0, 0),
            end=datetime(2024, 1, 1, 14, 0, 0),
        )
        requirements = [
            build_requirement("accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"),
        ]
        db = _make_db(requirements)

        # 记录 query_fn 收到的数据量（mock 返回 14400 点）
        captured_interval: list[int] = []

        async def query_fn(loop_id, tag_roles, start, end, interval_s):
            captured_interval.append(interval_s)
            # 返回 14400 点（> LTTB_THRESHOLD=10000）
            return build_raw_timeseries(n=14400, interval_s=float(interval_s), tags=tag_roles)

        planner = DataPlanner(
            cache=L1DataBlockCache(FakeCacheRedis()),
            tdengine_query_fn=query_fn,
            assembler=MetricDataBundleAssembler(),
            db=db,
            config_loader=_make_config_loader(ControlType.TEMPERATURE),
        )

        bundles = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=tw,
            control_type=ControlType.TEMPERATURE,
        )

        # KPI 路径未触发 LTTB 降采样：interval_s 仍为控制类型阈值（5s）
        assert captured_interval == [5]
        # Bundle 保留全量数据点（14400 点未降采样）
        assert bundles[0].data_block.point_count == 14400

    @pytest.mark.asyncio
    async def test_kpi_path_does_not_import_lttb(self) -> None:
        """DataPlanner 模块不应导入 LTTB 降采样函数（防回归）.

        确保未来修改不会误将 monitor.py/waveform.py 的 LTTB 函数引入 KPI 计算路径。
        """
        import app.services.data_planner as dp_module

        # 检查模块源码不含 lttb_downsample 调用
        source_lines = [line for line in dp_module.__doc__.splitlines() if "lttb" in line.lower()]
        # docstring 中应仅作为说明提及（不调用函数）
        assert not any(
            "import" in line.lower() and "lttb" in line.lower() for line in source_lines
        ), "DataPlanner docstring 不应包含 lttb import 语句"

        # 模块不应有 lttb_downsample 函数引用
        assert not hasattr(dp_module, "lttb_downsample"), (
            "DataPlanner 模块不应定义/导入 lttb_downsample 函数"
        )
        assert not hasattr(dp_module, "lttb_downsample_multi_series"), (
            "DataPlanner 模块不应定义/导入 lttb_downsample_multi_series 函数"
        )


class TestRequirementCodeAliases:
    """DB 列名 → 契约 metric_code 别名解析（_filter_requirements）.

    回归背景：快照表列名 steady_rate 与契约表 metric_code stability_rate
    命名不一致，按 DB 列名请求时契约查询为空，stability 指标静默跳过，
    快照只剩 PARTIAL（2026-07-19 定位）。
    """

    def test_db_column_name_resolves_to_contract_code(self) -> None:
        from app.services.data_planner import _filter_requirements

        stability_row = MagicMock(metric_code="stability_rate")
        with patch.dict(
            "app.services.data_planner._REQUIREMENTS_CACHE",
            {"stability_rate": stability_row},
            clear=True,
        ):
            result = _filter_requirements(["steady_rate", "good_value_rate"])

        # steady_rate 命中 stability_rate 契约行，且以请求方代码为键
        assert result == {"steady_rate": stability_row}

    def test_contract_code_still_works(self) -> None:
        """直接按契约代码请求不受影响."""
        from app.services.data_planner import _filter_requirements

        stability_row = MagicMock(metric_code="stability_rate")
        with patch.dict(
            "app.services.data_planner._REQUIREMENTS_CACHE",
            {"stability_rate": stability_row},
            clear=True,
        ):
            result = _filter_requirements(["stability_rate"])

        assert result == {"stability_rate": stability_row}

    def test_unknown_code_filtered_out(self) -> None:
        from app.services.data_planner import _filter_requirements

        with patch.dict("app.services.data_planner._REQUIREMENTS_CACHE", {}, clear=True):
            assert _filter_requirements(["steady_rate"]) == {}
