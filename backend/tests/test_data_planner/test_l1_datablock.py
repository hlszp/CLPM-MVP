"""L1 DataBlock 缓存单元测试.

测试要点：
    - zstd 压缩/解压正确性（往返一致）
    - Pipeline 批量写入（set_many）
    - 分层 TTL（BASE=3600s, HF=300s）
    - 压缩率 ≥ 60%（压缩后 ≤ 原始 40%）
    - get_or_set 命中/未命中行为

设计依据：ADS §10.7, FDS §5.3.9
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.contracts.data_types import TagGroup
from app.services.cache.l1_datablock import (
    L1DataBlockCache,
    compute_compression_ratio,
    time_window_hash,
)

from .conftest import FakeCacheRedis, build_data_block

# ---------------------------------------------------------------------------
# zstd 压缩/解压
# ---------------------------------------------------------------------------


class TestZstdCompression:
    """zstd 压缩与解压正确性."""

    @pytest.mark.asyncio
    async def test_set_get_roundtrip(self, fake_redis: FakeCacheRedis) -> None:
        """写入后读取应返回等价的 DataBlock（往返一致）."""
        cache = L1DataBlockCache(fake_redis)
        original = build_data_block(
            loop_id="L001",
            tag_group=TagGroup.BASE,
            n=100,
            valid_rate=0.95,
        )

        await cache.set(original)
        # 验证 Key 已写入 Redis
        assert len(fake_redis.keys) == 1

        # 读取
        key = fake_redis.keys[0]
        restored = await cache.get(key)

        assert restored is not None
        assert restored.data_block_id == original.data_block_id
        assert restored.loop_id == original.loop_id
        assert restored.tag_group == original.tag_group
        assert restored.sampling_freq == original.sampling_freq
        assert restored.point_count == original.point_count
        assert len(restored.timestamps) == len(original.timestamps)
        assert restored.timestamps[0] == original.timestamps[0]
        assert restored.signals["pv"] == original.signals["pv"]
        assert restored.validity["pv_valid"] == original.validity["pv_valid"]
        assert restored.quality_summary.valid_rate == original.quality_summary.valid_rate
        assert restored.consecutive_segments == original.consecutive_segments
        assert restored.config_version == original.config_version
        assert restored.preprocess_version == original.preprocess_version

    @pytest.mark.asyncio
    async def test_get_miss_returns_none(self, fake_redis: FakeCacheRedis) -> None:
        """未命中的 Key 应返回 None."""
        cache = L1DataBlockCache(fake_redis)
        result = await cache.get("pdb:nonexistent:key")
        assert result is None

    @pytest.mark.asyncio
    async def test_deserialize_corrupted_key_deletes_it(self, fake_redis: FakeCacheRedis) -> None:
        """脏数据应被丢弃并删除 Key."""
        cache = L1DataBlockCache(fake_redis)
        fake_redis._store["pdb:bad"] = "!!!not-base64!!!"
        result = await cache.get("pdb:bad")
        assert result is None
        # 脏数据应被删除
        assert "pdb:bad" not in fake_redis._store

    def test_compression_ratio_above_60_pct(self) -> None:
        """zstd+base64 压缩率应 ≥ 60%（压缩后 ≤ 原始 40%）."""
        # 1000 点工业时序数据（重复度高，压缩效果好）
        block = build_data_block(n=1000, valid_rate=0.95)
        ratio_pct = compute_compression_ratio(block)
        # ratio_pct = 压缩后/原始 × 100，需 ≤ 40 才算 ≥ 60% 压缩率
        assert ratio_pct <= 40.0, f"压缩率不足 60%: ratio={ratio_pct:.1f}% (需 ≤40%)"

    def test_compression_ratio_large_block(self) -> None:
        """大块数据（3600 点）压缩率应更优."""
        block = build_data_block(n=3600, valid_rate=0.98)
        ratio_pct = compute_compression_ratio(block)
        assert ratio_pct <= 35.0, f"大块压缩率不足: ratio={ratio_pct:.1f}%"


# ---------------------------------------------------------------------------
# 分层 TTL
# ---------------------------------------------------------------------------


class TestTieredTTL:
    """分层 TTL 测试."""

    def test_base_ttl_is_3600(self) -> None:
        """BASE tagGroup TTL 应为 3600 秒（1 小时）."""
        assert L1DataBlockCache.get_ttl(TagGroup.BASE.value) == 3600

    def test_hf_ttl_is_300(self) -> None:
        """高频 tagGroup（OP_HF/PVOP_HF/MODE_HF/QUALITY_HF）TTL 应为 300 秒."""
        assert L1DataBlockCache.get_ttl(TagGroup.OP_HF.value) == 300
        assert L1DataBlockCache.get_ttl(TagGroup.PVOP_HF.value) == 300
        assert L1DataBlockCache.get_ttl(TagGroup.MODE_HF.value) == 300
        assert L1DataBlockCache.get_ttl(TagGroup.QUALITY_HF.value) == 300

    @pytest.mark.asyncio
    async def test_set_uses_tiered_ttl(self, fake_redis: FakeCacheRedis) -> None:
        """写入时自动按 tagGroup 选择分层 TTL."""
        cache = L1DataBlockCache(fake_redis)

        base_block = build_data_block(tag_group=TagGroup.BASE, n=10)
        op_block = build_data_block(tag_group=TagGroup.OP_HF, n=10)

        await cache.set(base_block)
        await cache.set(op_block)

        base_key = [k for k in fake_redis.keys if ":BASE:" in k][0]
        op_key = [k for k in fake_redis.keys if ":OP_HF:" in k][0]

        assert fake_redis._ttls[base_key] == 3600
        assert fake_redis._ttls[op_key] == 300

    @pytest.mark.asyncio
    async def test_set_with_explicit_ttl_overrides_default(
        self, fake_redis: FakeCacheRedis
    ) -> None:
        """显式 TTL 覆盖分层默认值."""
        cache = L1DataBlockCache(fake_redis)
        block = build_data_block(tag_group=TagGroup.BASE, n=10)
        await cache.set(block, ttl=999)
        key = fake_redis.keys[0]
        assert fake_redis._ttls[key] == 999


# ---------------------------------------------------------------------------
# Pipeline 批量写入
# ---------------------------------------------------------------------------


class TestPipelineBatchWrite:
    """Pipeline 批量写入测试."""

    @pytest.mark.asyncio
    async def test_set_many_writes_all_blocks(self, fake_redis: FakeCacheRedis) -> None:
        """set_many 应通过 Pipeline 批量写入所有 DataBlock."""
        cache = L1DataBlockCache(fake_redis)
        blocks = [
            build_data_block(loop_id="L001", tag_group=TagGroup.BASE, n=50),
            build_data_block(loop_id="L001", tag_group=TagGroup.OP_HF, n=50),
            build_data_block(loop_id="L001", tag_group=TagGroup.PVOP_HF, n=50),
        ]

        written = await cache.set_many(blocks)
        assert written == 3
        assert len(fake_redis.keys) == 3
        # 应仅调用一次 pipeline（批量）
        assert fake_redis.pipeline_calls == 1

    @pytest.mark.asyncio
    async def test_set_many_empty_list(self, fake_redis: FakeCacheRedis) -> None:
        """空列表应返回 0 且不调用 pipeline."""
        cache = L1DataBlockCache(fake_redis)
        written = await cache.set_many([])
        assert written == 0
        assert fake_redis.pipeline_calls == 0

    @pytest.mark.asyncio
    async def test_set_many_applies_tiered_ttl(self, fake_redis: FakeCacheRedis) -> None:
        """set_many 应为不同 tagGroup 应用分层 TTL."""
        cache = L1DataBlockCache(fake_redis)
        blocks = [
            build_data_block(tag_group=TagGroup.BASE, n=10),
            build_data_block(tag_group=TagGroup.OP_HF, n=10),
        ]
        await cache.set_many(blocks)

        base_key = [k for k in fake_redis.keys if ":BASE:" in k][0]
        op_key = [k for k in fake_redis.keys if ":OP_HF:" in k][0]
        assert fake_redis._ttls[base_key] == 3600
        assert fake_redis._ttls[op_key] == 300


# ---------------------------------------------------------------------------
# get_or_set
# ---------------------------------------------------------------------------


class TestGetOrSet:
    """get_or_set 命中/未命中行为."""

    @pytest.mark.asyncio
    async def test_get_or_set_miss_calls_factory(self, fake_redis: FakeCacheRedis) -> None:
        """未命中时应调用 factory 生成并写入缓存."""
        cache = L1DataBlockCache(fake_redis)
        block = build_data_block(n=20)
        key = cache.build_key_from_block(block)

        factory_called = {"count": 0}

        async def factory():
            factory_called["count"] += 1
            return block

        result = await cache.get_or_set(key, factory)
        assert result is block
        assert factory_called["count"] == 1
        # 应写入缓存
        assert len(fake_redis.keys) == 1

    @pytest.mark.asyncio
    async def test_get_or_set_hit_skips_factory(self, fake_redis: FakeCacheRedis) -> None:
        """命中时不应调用 factory."""
        cache = L1DataBlockCache(fake_redis)
        block = build_data_block(n=20)
        await cache.set(block)
        key = fake_redis.keys[0]

        factory_called = {"count": 0}

        async def factory():
            factory_called["count"] += 1
            return build_data_block(n=20)  # 不同的 block

        result = await cache.get_or_set(key, factory)
        assert factory_called["count"] == 0
        # 应返回缓存的 block（而非 factory 生成的）
        assert result.data_block_id == block.data_block_id


# ---------------------------------------------------------------------------
# Key 生成
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    """缓存 Key 生成测试."""

    def test_build_key_contains_all_fields(self) -> None:
        """Key 应包含所有必需字段."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        key = L1DataBlockCache.build_key(
            loop_id="TC101",
            tag_group="BASE",
            time_window_start=start,
            time_window_end=end,
            sampling_freq="5s",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            pre_version="pre_v1",
            cfg_version="cfg_12",
        )
        # Key 格式: pdb:{loopId}:{tagGroup}:{start}:{end}:{freq}:{qualityPolicy}:{preVer}:{cfgVer}
        parts = key.split(":")
        assert parts[0] == "pdb"
        assert parts[1] == "TC101"
        assert parts[2] == "BASE"
        assert parts[5] == "5s"
        assert parts[6] == "KEEP_ALL_WITH_VALIDITY"
        assert parts[7] == "pre_v1"
        assert parts[8] == "cfg_12"

    def test_build_key_different_windows_differ(self) -> None:
        """不同时间窗口的 Key 应不同."""
        start1 = datetime(2024, 1, 1, 10, 0, 0)
        start2 = datetime(2024, 1, 1, 11, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 0)
        k1 = L1DataBlockCache.build_key(
            "L1", "BASE", start1, end, "1s", "KEEP_ALL_WITH_VALIDITY", "pre_v1", "cfg_1"
        )
        k2 = L1DataBlockCache.build_key(
            "L1", "BASE", start2, end, "1s", "KEEP_ALL_WITH_VALIDITY", "pre_v1", "cfg_1"
        )
        assert k1 != k2

    def test_build_key_different_config_version_differ(self) -> None:
        """不同配置版本的 Key 应不同（支持配置变更失效）."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        k1 = L1DataBlockCache.build_key(
            "L1", "BASE", start, end, "1s", "KEEP_ALL_WITH_VALIDITY", "pre_v1", "cfg_1"
        )
        k2 = L1DataBlockCache.build_key(
            "L1", "BASE", start, end, "1s", "KEEP_ALL_WITH_VALIDITY", "pre_v1", "cfg_2"
        )
        assert k1 != k2

    def test_time_window_hash_stable(self) -> None:
        """相同时间窗口的哈希应一致."""
        start = datetime(2024, 1, 1, 10, 0, 0)
        end = datetime(2024, 1, 1, 11, 0, 0)
        h1 = time_window_hash(start, end)
        h2 = time_window_hash(start, end)
        assert h1 == h2
        assert len(h1) == 8
