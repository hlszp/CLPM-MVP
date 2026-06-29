"""L2/L3 缓存层单元测试.

测试要点：
    - L2 MetricDataBundle 缓存命中/未命中
    - L2 序列化/反序列化正确性（含 DataBlock、DataLineage）
    - L2 TTL 设置（默认 600s，自定义覆盖）
    - L2 get_or_set 命中/未命中行为
    - L2 脏数据丢弃
    - L3 特征缓存命中/未命中
    - L3 序列化/反序列化正确性（含浮点/嵌套结构）
    - L3 TTL 设置（默认 1800s，自定义覆盖）
    - L3 set_many Pipeline 批量写入
    - L3 get_or_set 命中/未命中行为
    - L3 脏数据丢弃
    - DataPlanner L2 集成（命中跳过组装，未命中写入缓存）

设计依据：ADS §10.7, FDS §5.3.9, 任务规范
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.contracts.data_types import (
    ControlType,
    MetricDataBundle,
    TagGroup,
    TimeWindow,
)
from app.services.cache.l1_datablock import L1DataBlockCache
from app.services.cache.l2_bundle import L2BundleCache
from app.services.cache.l3_feature import L3FeatureCache
from app.services.data_planner import DataPlanner
from app.services.metric_data_bundle import MetricDataBundleAssembler
from tests.test_data_planner.conftest import (
    FakeCacheRedis,
    build_data_block,
    build_raw_timeseries,
    build_requirement,
)

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


def _make_bundle(
    metric_code: str = "accuracy_rate",
    loop_id: str = "L001",
    tag_group: TagGroup = TagGroup.BASE,
    n: int = 50,
    mask_expression: str = "pv_valid && sp_valid",
) -> MetricDataBundle:
    """构造 MetricDataBundle（含 DataBlock 与 DataLineage）."""
    block = build_data_block(loop_id=loop_id, tag_group=tag_group, n=n)
    assembler = MetricDataBundleAssembler()
    return assembler.assemble(
        metric_code=metric_code,
        data_block=block,
        mask_expression=mask_expression,
        requirement=None,
    )


def _make_bundles() -> list[MetricDataBundle]:
    """构造多个 MetricDataBundle（覆盖不同 tagGroup）."""
    return [
        _make_bundle("accuracy_rate", tag_group=TagGroup.BASE),
        _make_bundle("output_trip_index", tag_group=TagGroup.OP_HF),
    ]


def _time_window() -> TimeWindow:
    return TimeWindow(
        start=datetime(2024, 1, 1, 10, 0, 0),
        end=datetime(2024, 1, 1, 11, 0, 0),
    )


def _make_db(requirements: list) -> AsyncMock:
    """构造 mock db，execute 返回 requirements 列表."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = requirements
    db.execute = AsyncMock(return_value=result)
    return db


def _make_config_loader(
    control_type: ControlType = ControlType.TEMPERATURE,
    config_version: str = "cfg_1000",
):
    async def loader(loop_id: str, ctrl: ControlType):
        from app.contracts.data_types import LoopPreprocessConfig

        return LoopPreprocessConfig(
            loop_id=loop_id,
            control_type=ctrl,
            range_min=0.0,
            range_max=100.0,
            config_version=config_version,
        )

    return loader


def _make_query_fn(call_log: list, return_n: int = 100):
    async def query_fn(loop_id, tag_roles, start, end, interval_s):
        call_log.append({"loop_id": loop_id, "tags": list(tag_roles), "interval_s": interval_s})
        return build_raw_timeseries(n=return_n, interval_s=float(interval_s), tags=tag_roles)

    return query_fn


# ---------------------------------------------------------------------------
# L2 序列化 / 反序列化
# ---------------------------------------------------------------------------


class TestL2Serialization:
    """L2 MetricDataBundle 序列化/反序列化正确性."""

    @pytest.mark.asyncio
    async def test_set_get_roundtrip(self) -> None:
        """写入后读取应返回等价的 Bundle 列表（往返一致）."""
        cache = L2BundleCache(FakeCacheRedis())
        original_bundles = _make_bundles()

        key = L2BundleCache.build_key(
            loop_id="L001",
            metrics=["accuracy_rate", "output_trip_index"],
            time_window_start=datetime(2024, 1, 1, 10, 0, 0),
            time_window_end=datetime(2024, 1, 1, 11, 0, 0),
            control_type="TC",
        )
        await cache.set(key, original_bundles)
        restored = await cache.get(key)

        assert restored is not None
        assert len(restored) == len(original_bundles)
        for orig, rest in zip(original_bundles, restored, strict=False):
            assert rest.metric_code == orig.metric_code
            assert rest.mask_expression == orig.mask_expression
            assert list(rest.masked_indices) == list(orig.masked_indices)
            # DataBlock 等价性
            assert rest.data_block.data_block_id == orig.data_block.data_block_id
            assert rest.data_block.loop_id == orig.data_block.loop_id
            assert rest.data_block.tag_group == orig.data_block.tag_group
            assert rest.data_block.point_count == orig.data_block.point_count
            assert rest.data_block.signals["pv"] == orig.data_block.signals["pv"]
            assert rest.data_block.validity["pv_valid"] == orig.data_block.validity["pv_valid"]
            assert (
                rest.data_block.quality_summary.valid_rate
                == orig.data_block.quality_summary.valid_rate
            )
            assert rest.data_block.consecutive_segments == (orig.data_block.consecutive_segments)
            # DataLineage 等价性
            assert rest.lineage.sampling_freq == orig.lineage.sampling_freq
            assert rest.lineage.tag_group == orig.lineage.tag_group
            assert rest.lineage.valid_rate == orig.lineage.valid_rate
            assert rest.lineage.data_policy_version == (orig.lineage.data_policy_version)

    @pytest.mark.asyncio
    async def test_roundtrip_preserves_timestamps(self) -> None:
        """反序列化后的时间戳应与原始一致（datetime ↔ isoformat 往返）."""
        cache = L2BundleCache(FakeCacheRedis())
        bundle = _make_bundle(n=10)
        key = "pdb_l2:test:abc:def:TC"
        await cache.set(key, [bundle])
        restored = await cache.get(key)
        assert restored is not None
        assert len(restored[0].data_block.timestamps) == len(bundle.data_block.timestamps)
        assert restored[0].data_block.timestamps[0] == bundle.data_block.timestamps[0]
        assert restored[0].data_block.timestamps[-1] == bundle.data_block.timestamps[-1]


# ---------------------------------------------------------------------------
# L2 命中/未命中
# ---------------------------------------------------------------------------


class TestL2CacheHitMiss:
    """L2 缓存命中/未命中行为."""

    @pytest.mark.asyncio
    async def test_get_miss_returns_none(self) -> None:
        """未命中的 Key 应返回 None."""
        cache = L2BundleCache(FakeCacheRedis())
        result = await cache.get("pdb_l2:nonexistent:key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_corrupted_data_returns_none_and_deletes(self) -> None:
        """脏数据应被丢弃并删除 Key."""
        redis = FakeCacheRedis()
        cache = L2BundleCache(redis)
        redis._store["pdb_l2:bad"] = "!!!not-base64!!!"
        result = await cache.get("pdb_l2:bad")
        assert result is None
        assert "pdb_l2:bad" not in redis._store

    @pytest.mark.asyncio
    async def test_get_or_set_miss_calls_factory(self) -> None:
        """未命中时应调用 factory 生成并写入缓存."""
        redis = FakeCacheRedis()
        cache = L2BundleCache(redis)
        bundles = _make_bundles()
        factory_called = {"count": 0}

        async def factory():
            factory_called["count"] += 1
            return bundles

        result = await cache.get_or_set("pdb_l2:test:abc:def:TC", factory)
        assert result is bundles
        assert factory_called["count"] == 1
        assert len(redis.keys) == 1

    @pytest.mark.asyncio
    async def test_get_or_set_hit_skips_factory(self) -> None:
        """命中时不应调用 factory."""
        redis = FakeCacheRedis()
        cache = L2BundleCache(redis)
        bundles = _make_bundles()
        key = "pdb_l2:test:abc:def:TC"
        await cache.set(key, bundles)

        factory_called = {"count": 0}

        async def factory():
            factory_called["count"] += 1
            return _make_bundles()  # 不同的 bundle

        result = await cache.get_or_set(key, factory)
        assert factory_called["count"] == 0
        assert len(result) == len(bundles)
        assert result[0].metric_code == bundles[0].metric_code

    @pytest.mark.asyncio
    async def test_get_or_set_empty_bundles_not_cached(self) -> None:
        """空 Bundle 列表不应写入缓存（避免缓存空结果）."""
        redis = FakeCacheRedis()
        cache = L2BundleCache(redis)

        async def factory():
            return []

        result = await cache.get_or_set("pdb_l2:test:abc:def:TC", factory)
        assert result == []
        assert len(redis.keys) == 0


# ---------------------------------------------------------------------------
# L2 TTL
# ---------------------------------------------------------------------------


class TestL2TTL:
    """L2 TTL 设置测试."""

    @pytest.mark.asyncio
    async def test_default_ttl_is_600(self) -> None:
        """默认 TTL 应为 600 秒（10 分钟）."""
        redis = FakeCacheRedis()
        cache = L2BundleCache(redis)
        bundles = _make_bundles()
        key = "pdb_l2:test:abc:def:TC"
        await cache.set(key, bundles)
        assert redis._ttls[key] == 600

    @pytest.mark.asyncio
    async def test_custom_ttl_overrides_default(self) -> None:
        """显式 TTL 应覆盖默认值."""
        redis = FakeCacheRedis()
        cache = L2BundleCache(redis)
        bundles = _make_bundles()
        key = "pdb_l2:test:abc:def:TC"
        await cache.set(key, bundles, ttl=999)
        assert redis._ttls[key] == 999

    def test_default_ttl_constant(self) -> None:
        """DEFAULT_TTL 常量应为 600."""
        from app.services.cache.l2_bundle import DEFAULT_TTL as L2_TTL

        assert L2_TTL == 600


# ---------------------------------------------------------------------------
# L2 Key 生成
# ---------------------------------------------------------------------------


class TestL2KeyGeneration:
    """L2 缓存 Key 生成测试."""

    def test_build_key_format(self) -> None:
        """Key 应符合 pdb_l2:{loopId}:{metrics_hash}:{window_hash}:{control_type}."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        key = L2BundleCache.build_key(
            loop_id="TC101",
            metrics=["accuracy_rate", "stability_rate"],
            time_window_start=start,
            time_window_end=end,
            control_type="TC",
        )
        parts = key.split(":")
        assert parts[0] == "pdb_l2"
        assert parts[1] == "TC101"
        assert len(parts[2]) == 8  # metrics_hash 8 位
        assert len(parts[3]) == 8  # window_hash 8 位
        assert parts[4] == "TC"

    def test_build_key_metrics_order_invariant(self) -> None:
        """相同指标集合（不同顺序）应生成相同 Key."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        k1 = L2BundleCache.build_key("L1", ["a", "b", "c"], start, end, "TC")
        k2 = L2BundleCache.build_key("L1", ["c", "a", "b"], start, end, "TC")
        assert k1 == k2

    def test_build_key_different_metrics_differ(self) -> None:
        """不同指标集合应生成不同 Key."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        k1 = L2BundleCache.build_key("L1", ["a", "b"], start, end, "TC")
        k2 = L2BundleCache.build_key("L1", ["a", "c"], start, end, "TC")
        assert k1 != k2

    def test_build_key_different_window_differ(self) -> None:
        """不同时间窗口应生成不同 Key."""
        start1 = datetime(2024, 1, 1, 10, 0, 0)
        start2 = datetime(2024, 1, 1, 11, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 0)
        k1 = L2BundleCache.build_key("L1", ["a"], start1, end, "TC")
        k2 = L2BundleCache.build_key("L1", ["a"], start2, end, "TC")
        assert k1 != k2

    def test_build_key_different_control_type_differ(self) -> None:
        """不同控制类型应生成不同 Key."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        k1 = L2BundleCache.build_key("L1", ["a"], start, end, "TC")
        k2 = L2BundleCache.build_key("L1", ["a"], start, end, "FC")
        assert k1 != k2


# ---------------------------------------------------------------------------
# L3 序列化 / 反序列化
# ---------------------------------------------------------------------------


class TestL3Serialization:
    """L3 特征值序列化/反序列化正确性."""

    @pytest.mark.asyncio
    async def test_set_get_roundtrip_simple(self) -> None:
        """简单特征值字典的往返一致性."""
        cache = L3FeatureCache(FakeCacheRedis())
        feature = {
            "sum": 1234.56,
            "sum_sq": 1523478.90,
            "count": 3600,
            "delta_op": 0.05,
        }
        key = L3FeatureCache.build_key(
            loop_id="L001",
            metric_code="fast_rate",
            feature_name="arma_params",
            time_window_start=datetime(2024, 1, 1, 10, 0, 0),
            time_window_end=datetime(2024, 1, 1, 11, 0, 0),
        )
        await cache.set(key, feature)
        restored = await cache.get(key)

        assert restored is not None
        assert restored["sum"] == pytest.approx(feature["sum"])
        assert restored["sum_sq"] == pytest.approx(feature["sum_sq"])
        assert restored["count"] == feature["count"]
        assert restored["delta_op"] == pytest.approx(feature["delta_op"])

    @pytest.mark.asyncio
    async def test_set_get_roundtrip_nested(self) -> None:
        """嵌套结构（list/dict）的往返一致性."""
        cache = L3FeatureCache(FakeCacheRedis())
        feature = {
            "arma_p": [0.1, 0.2, 0.3],
            "arma_q": [0.05, -0.1],
            "iae": 12.34,
            "metadata": {"model": "ARMA(2,1)", "iterations": 25},
        }
        key = "pdb_l3:L001:fast_rate:arma_params:abcdef12"
        await cache.set(key, feature)
        restored = await cache.get(key)

        assert restored is not None
        assert restored["arma_p"] == feature["arma_p"]
        assert restored["arma_q"] == feature["arma_q"]
        assert restored["iae"] == pytest.approx(feature["iae"])
        assert restored["metadata"] == feature["metadata"]

    @pytest.mark.asyncio
    async def test_roundtrip_preserves_int_and_float(self) -> None:
        """整型与浮点型应分别保留（不发生隐式转换）."""
        cache = L3FeatureCache(FakeCacheRedis())
        feature = {"int_val": 100, "float_val": 3.14}
        key = "pdb_l3:test:metric:feature:abcdef12"
        await cache.set(key, feature)
        restored = await cache.get(key)
        assert restored is not None
        assert restored["int_val"] == 100
        assert isinstance(restored["int_val"], int)
        assert restored["float_val"] == pytest.approx(3.14)
        assert isinstance(restored["float_val"], float)


# ---------------------------------------------------------------------------
# L3 命中/未命中
# ---------------------------------------------------------------------------


class TestL3CacheHitMiss:
    """L3 缓存命中/未命中行为."""

    @pytest.mark.asyncio
    async def test_get_miss_returns_none(self) -> None:
        """未命中的 Key 应返回 None."""
        cache = L3FeatureCache(FakeCacheRedis())
        result = await cache.get("pdb_l3:nonexistent:key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_corrupted_data_returns_none_and_deletes(self) -> None:
        """脏数据应被丢弃并删除 Key."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        redis._store["pdb_l3:bad"] = "!!!not-base64!!!"
        result = await cache.get("pdb_l3:bad")
        assert result is None
        assert "pdb_l3:bad" not in redis._store

    @pytest.mark.asyncio
    async def test_get_or_set_miss_calls_factory(self) -> None:
        """未命中时应调用 factory 生成并写入缓存."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        feature = {"sum": 100.0, "count": 1000}
        factory_called = {"count": 0}

        async def factory():
            factory_called["count"] += 1
            return feature

        result = await cache.get_or_set("pdb_l3:test:m:f:abcdef12", factory)
        assert result is feature
        assert factory_called["count"] == 1
        assert len(redis.keys) == 1

    @pytest.mark.asyncio
    async def test_get_or_set_hit_skips_factory(self) -> None:
        """命中时不应调用 factory."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        feature = {"sum": 100.0}
        key = "pdb_l3:test:m:f:abcdef12"
        await cache.set(key, feature)

        factory_called = {"count": 0}

        async def factory():
            factory_called["count"] += 1
            return {"sum": 999.0}

        result = await cache.get_or_set(key, factory)
        assert factory_called["count"] == 0
        assert result["sum"] == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_get_or_set_empty_feature_not_cached(self) -> None:
        """空特征值字典不应写入缓存."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)

        async def factory():
            return {}

        result = await cache.get_or_set("pdb_l3:test:m:f:abcdef12", factory)
        assert result == {}
        assert len(redis.keys) == 0


# ---------------------------------------------------------------------------
# L3 TTL
# ---------------------------------------------------------------------------


class TestL3TTL:
    """L3 TTL 设置测试."""

    @pytest.mark.asyncio
    async def test_default_ttl_is_1800(self) -> None:
        """默认 TTL 应为 1800 秒（30 分钟）."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        key = "pdb_l3:test:m:f:abcdef12"
        await cache.set(key, {"sum": 1.0})
        assert redis._ttls[key] == 1800

    @pytest.mark.asyncio
    async def test_custom_ttl_overrides_default(self) -> None:
        """显式 TTL 应覆盖默认值."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        key = "pdb_l3:test:m:f:abcdef12"
        await cache.set(key, {"sum": 1.0}, ttl=999)
        assert redis._ttls[key] == 999

    def test_default_ttl_constant(self) -> None:
        """DEFAULT_TTL 常量应为 1800."""
        from app.services.cache.l3_feature import DEFAULT_TTL as L3_TTL

        assert L3_TTL == 1800


# ---------------------------------------------------------------------------
# L3 Pipeline 批量写入
# ---------------------------------------------------------------------------


class TestL3PipelineBatchWrite:
    """L3 set_many Pipeline 批量写入测试."""

    @pytest.mark.asyncio
    async def test_set_many_writes_all_items(self) -> None:
        """set_many 应通过 Pipeline 批量写入所有特征."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        items = [
            ("pdb_l3:L1:m:f1:abcdef12", {"sum": 1.0}),
            ("pdb_l3:L1:m:f2:abcdef12", {"sum": 2.0}),
            ("pdb_l3:L1:m:f3:abcdef12", {"sum": 3.0}),
        ]
        written = await cache.set_many(items)
        assert written == 3
        assert len(redis.keys) == 3
        assert redis.pipeline_calls == 1

    @pytest.mark.asyncio
    async def test_set_many_empty_list(self) -> None:
        """空列表应返回 0 且不调用 pipeline."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        written = await cache.set_many([])
        assert written == 0
        assert redis.pipeline_calls == 0

    @pytest.mark.asyncio
    async def test_set_many_applies_default_ttl(self) -> None:
        """set_many 应为所有条目应用默认 TTL（1800s）."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        items = [
            ("pdb_l3:L1:m:f1:abcdef12", {"sum": 1.0}),
            ("pdb_l3:L1:m:f2:abcdef12", {"sum": 2.0}),
        ]
        await cache.set_many(items)
        for key in redis.keys:
            assert redis._ttls[key] == 1800

    @pytest.mark.asyncio
    async def test_set_many_custom_ttl(self) -> None:
        """set_many 支持自定义 TTL."""
        redis = FakeCacheRedis()
        cache = L3FeatureCache(redis)
        items = [("pdb_l3:L1:m:f1:abcdef12", {"sum": 1.0})]
        await cache.set_many(items, ttl=600)
        assert redis._ttls["pdb_l3:L1:m:f1:abcdef12"] == 600


# ---------------------------------------------------------------------------
# L3 Key 生成
# ---------------------------------------------------------------------------


class TestL3KeyGeneration:
    """L3 缓存 Key 生成测试."""

    def test_build_key_format(self) -> None:
        """Key 应符合 pdb_l3:{loopId}:{metric_code}:{feature_name}:{window_hash}."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        key = L3FeatureCache.build_key(
            loop_id="TC101",
            metric_code="fast_rate",
            feature_name="arma_params",
            time_window_start=start,
            time_window_end=end,
        )
        parts = key.split(":")
        assert parts[0] == "pdb_l3"
        assert parts[1] == "TC101"
        assert parts[2] == "fast_rate"
        assert parts[3] == "arma_params"
        assert len(parts[4]) == 8  # window_hash 8 位

    def test_build_key_different_window_differ(self) -> None:
        """不同时间窗口应生成不同 Key."""
        start1 = datetime(2024, 1, 1, 10, 0, 0)
        start2 = datetime(2024, 1, 1, 11, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 0)
        k1 = L3FeatureCache.build_key("L1", "m", "f", start1, end)
        k2 = L3FeatureCache.build_key("L1", "m", "f", start2, end)
        assert k1 != k2

    def test_build_key_different_metric_differ(self) -> None:
        """不同指标应生成不同 Key."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        k1 = L3FeatureCache.build_key("L1", "fast_rate", "f", start, end)
        k2 = L3FeatureCache.build_key("L1", "stiction", "f", start, end)
        assert k1 != k2

    def test_build_key_different_feature_differ(self) -> None:
        """不同特征名应生成不同 Key."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        k1 = L3FeatureCache.build_key("L1", "m", "arma_p", start, end)
        k2 = L3FeatureCache.build_key("L1", "m", "arma_q", start, end)
        assert k1 != k2


# ---------------------------------------------------------------------------
# DataPlanner L2 集成
# ---------------------------------------------------------------------------


class TestDataPlannerL2Integration:
    """DataPlanner L2 缓存集成测试."""

    @pytest.mark.asyncio
    async def test_l2_hit_skips_query_and_assembly(self) -> None:
        """L2 命中时应跳过 TDengine 查询与 Bundle 组装."""
        redis = FakeCacheRedis()
        l1_cache = L1DataBlockCache(redis)
        l2_cache = L2BundleCache(redis)
        query_log: list = []
        planner = DataPlanner(
            cache=l1_cache,
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=None,
            config_loader=_make_config_loader(),
            bundle_cache=l2_cache,
        )

        # 预写 L2 缓存（模拟命中）
        bundles = _make_bundles()
        l2_key = L2BundleCache.build_key(
            loop_id="TC101",
            metrics=["accuracy_rate", "output_trip_index"],
            time_window_start=_time_window().start,
            time_window_end=_time_window().end,
            control_type=ControlType.TEMPERATURE.value,
        )
        await l2_cache.set(l2_key, bundles)

        result = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate", "output_trip_index"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # L2 命中：不应查询 TDengine
        assert len(query_log) == 0
        # 应返回 L2 缓存的 bundles
        assert len(result) == len(bundles)
        assert result[0].metric_code == bundles[0].metric_code
        assert result[1].metric_code == bundles[1].metric_code

    @pytest.mark.asyncio
    async def test_l2_miss_writes_cache_after_assembly(self) -> None:
        """L2 未命中时应执行完整流程并写入 L2 缓存."""
        redis = FakeCacheRedis()
        l1_cache = L1DataBlockCache(redis)
        l2_cache = L2BundleCache(redis)
        query_log: list = []
        requirements = [
            build_requirement(
                "accuracy_rate",
                TagGroup.BASE,
                ["pv", "sp"],
                mask_expression="pv_valid && sp_valid",
            ),
        ]
        planner = DataPlanner(
            cache=l1_cache,
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=_make_db(requirements),
            config_loader=_make_config_loader(),
            bundle_cache=l2_cache,
        )

        result = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # 应查询 TDengine（L2 未命中）
        assert len(query_log) == 1
        # 应返回 1 个 bundle
        assert len(result) == 1
        assert result[0].metric_code == "accuracy_rate"

        # L2 缓存应被写入（Key 前缀 pdb_l2）
        l2_keys = [k for k in redis.keys if k.startswith("pdb_l2:")]
        assert len(l2_keys) == 1
        # 第二次请求应命中 L2
        query_log.clear()
        result2 = await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )
        assert len(query_log) == 0  # L2 命中，不再查 TDengine
        assert len(result2) == 1
        assert result2[0].metric_code == "accuracy_rate"

    @pytest.mark.asyncio
    async def test_l2_disabled_when_bundle_cache_none(self) -> None:
        """bundle_cache=None 时应禁用 L2（行为与原版一致）."""
        redis = FakeCacheRedis()
        l1_cache = L1DataBlockCache(redis)
        query_log: list = []
        requirements = [
            build_requirement(
                "accuracy_rate",
                TagGroup.BASE,
                ["pv", "sp"],
                mask_expression="pv_valid && sp_valid",
            ),
        ]
        planner = DataPlanner(
            cache=l1_cache,
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=_make_db(requirements),
            config_loader=_make_config_loader(),
            bundle_cache=None,  # 禁用 L2
        )

        await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        # 应查询 TDengine
        assert len(query_log) == 1
        # 不应写入任何 L2 缓存
        l2_keys = [k for k in redis.keys if k.startswith("pdb_l2:")]
        assert len(l2_keys) == 0

    @pytest.mark.asyncio
    async def test_l2_cache_uses_600s_ttl(self) -> None:
        """DataPlanner 写入 L2 缓存时应使用 600s TTL."""
        redis = FakeCacheRedis()
        l1_cache = L1DataBlockCache(redis)
        l2_cache = L2BundleCache(redis)
        query_log: list = []
        requirements = [
            build_requirement(
                "accuracy_rate",
                TagGroup.BASE,
                ["pv", "sp"],
                mask_expression="pv_valid && sp_valid",
            ),
        ]
        planner = DataPlanner(
            cache=l1_cache,
            tdengine_query_fn=_make_query_fn(query_log),
            assembler=MetricDataBundleAssembler(),
            db=_make_db(requirements),
            config_loader=_make_config_loader(),
            bundle_cache=l2_cache,
        )

        await planner.request_bundles(
            loop_id="TC101",
            metrics=["accuracy_rate"],
            time_window=_time_window(),
            control_type=ControlType.TEMPERATURE,
        )

        l2_keys = [k for k in redis.keys if k.startswith("pdb_l2:")]
        assert len(l2_keys) == 1
        assert redis._ttls[l2_keys[0]] == 600
