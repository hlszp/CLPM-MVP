"""缓存失效模块单元测试.

测试要点：
    - 回路配置变更失效（invalidate_loop）：删除该回路所有旧版本缓存
    - 指标配置变更失效（invalidate_metric_config）：全量清理
    - tagGroup 精准失效（invalidate_tag_group）
    - 全量清理（invalidate_all）
    - 使用 SCAN 而非 KEYS（不阻塞）
    - keep_version 保留新版本缓存

设计依据：ADS §10.7.3, 数据流程图 §7.4
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import TagGroup
from app.services.cache.invalidation import CacheInvalidator
from app.services.cache.l1_datablock import L1DataBlockCache

from .conftest import FakeCacheRedis, build_data_block

# ---------------------------------------------------------------------------
# 辅助：填充测试缓存
# ---------------------------------------------------------------------------


async def _populate_cache(
    redis: FakeCacheRedis,
    loop_id: str = "TC101",
    tag_groups: list[TagGroup] | None = None,
    config_version: str = "cfg_12",
) -> list[str]:
    """向 FakeRedis 写入若干 DataBlock，返回写入的 Key 列表."""
    if tag_groups is None:
        tag_groups = [TagGroup.BASE, TagGroup.OP_HF, TagGroup.PVOP_HF, TagGroup.QUALITY_HF]
    cache = L1DataBlockCache(redis)
    keys: list[str] = []
    for tg in tag_groups:
        block = build_data_block(
            loop_id=loop_id,
            tag_group=tg,
            n=10,
            config_version=config_version,
        )
        await cache.set(block)
        keys.extend(redis.keys)
    return keys


# ---------------------------------------------------------------------------
# 回路配置变更失效
# ---------------------------------------------------------------------------


class TestInvalidateLoop:
    """回路配置变更失效测试."""

    @pytest.mark.asyncio
    async def test_invalidate_loop_deletes_all_keys(self, fake_redis: FakeCacheRedis) -> None:
        """回路配置变更应删除该回路全部缓存."""
        await _populate_cache(fake_redis, loop_id="TC101")
        assert len(fake_redis.keys) == 4

        invalidator = CacheInvalidator(fake_redis)
        deleted = await invalidator.invalidate_loop("TC101")

        assert deleted == 4
        assert len(fake_redis.keys) == 0

    @pytest.mark.asyncio
    async def test_invalidate_loop_keeps_new_version(self, fake_redis: FakeCacheRedis) -> None:
        """指定 keep_version 时应保留新版本缓存，仅删旧版本."""
        # 写入旧版本 cfg_12
        await _populate_cache(fake_redis, loop_id="TC101", config_version="cfg_12")
        # 写入新版本 cfg_13
        await _populate_cache(fake_redis, loop_id="TC101", config_version="cfg_13")
        assert len(fake_redis.keys) == 8

        invalidator = CacheInvalidator(fake_redis)
        deleted = await invalidator.invalidate_loop("TC101", config_version="cfg_13")

        # 应删除 4 个旧版本，保留 4 个新版本
        assert deleted == 4
        assert len(fake_redis.keys) == 4
        # 剩余的应都是 cfg_13
        for key in fake_redis.keys:
            assert "cfg_13" in key

    @pytest.mark.asyncio
    async def test_invalidate_loop_only_targets_specified_loop(
        self, fake_redis: FakeCacheRedis
    ) -> None:
        """失效应只影响指定回路，不影响其他回路."""
        await _populate_cache(fake_redis, loop_id="TC101")
        await _populate_cache(fake_redis, loop_id="FC201")
        assert len(fake_redis.keys) == 8

        invalidator = CacheInvalidator(fake_redis)
        deleted = await invalidator.invalidate_loop("TC101")

        assert deleted == 4
        assert len(fake_redis.keys) == 4
        # 剩余的应都是 FC201
        for key in fake_redis.keys:
            assert "FC201" in key


# ---------------------------------------------------------------------------
# 指标配置变更失效
# ---------------------------------------------------------------------------


class TestInvalidateMetricConfig:
    """指标配置变更失效测试."""

    @pytest.mark.asyncio
    async def test_invalidate_metric_config_clears_all(self, fake_redis: FakeCacheRedis) -> None:
        """指标配置变更应清空全部 L1 缓存（保守策略）."""
        await _populate_cache(fake_redis, loop_id="TC101")
        await _populate_cache(fake_redis, loop_id="FC201")
        assert len(fake_redis.keys) == 8

        invalidator = CacheInvalidator(fake_redis)
        deleted = await invalidator.invalidate_metric_config("accuracy_rate")

        assert deleted == 8
        assert len(fake_redis.keys) == 0


# ---------------------------------------------------------------------------
# tagGroup 精准失效
# ---------------------------------------------------------------------------


class TestInvalidateTagGroup:
    """tagGroup 精准失效测试."""

    @pytest.mark.asyncio
    async def test_invalidate_tag_group_only_deletes_matching(
        self, fake_redis: FakeCacheRedis
    ) -> None:
        """应只删除指定回路指定 tagGroup 的缓存."""
        await _populate_cache(fake_redis, loop_id="TC101")
        assert len(fake_redis.keys) == 4

        invalidator = CacheInvalidator(fake_redis)
        deleted = await invalidator.invalidate_tag_group("TC101", TagGroup.OP_HF.value)

        assert deleted == 1
        assert len(fake_redis.keys) == 3
        for key in fake_redis.keys:
            assert ":OP_HF:" not in key


# ---------------------------------------------------------------------------
# 全量清理
# ---------------------------------------------------------------------------


class TestInvalidateAll:
    """全量清理测试."""

    @pytest.mark.asyncio
    async def test_invalidate_all_clears_everything(self, fake_redis: FakeCacheRedis) -> None:
        """全量清理应删除所有 pdb: 前缀的 Key."""
        await _populate_cache(fake_redis, loop_id="TC101")
        # 写入一个非 pdb 前缀的 Key（应不受影响）
        fake_redis._store["other:key"] = "value"

        invalidator = CacheInvalidator(fake_redis)
        deleted = await invalidator.invalidate_all()

        assert deleted == 4
        assert "other:key" in fake_redis._store
        assert len([k for k in fake_redis.keys if k.startswith("pdb:")]) == 0


# ---------------------------------------------------------------------------
# SCAN 行为验证
# ---------------------------------------------------------------------------


class TestScanBehavior:
    """验证使用 SCAN 而非 KEYS."""

    @pytest.mark.asyncio
    async def test_scan_handles_large_keyspace(self, fake_redis: FakeCacheRedis) -> None:
        """SCAN 应能处理大量 Key（分批扫描）."""
        cache = L1DataBlockCache(fake_redis)
        # 写入 50 个 DataBlock（不同 loop_id）
        for i in range(50):
            block = build_data_block(
                loop_id=f"L{i:03d}",
                tag_group=TagGroup.BASE,
                n=5,
                config_version="cfg_1",
            )
            await cache.set(block)
        assert len(fake_redis.keys) == 50

        invalidator = CacheInvalidator(fake_redis)
        deleted = await invalidator.invalidate_all()
        assert deleted == 50
        assert len(fake_redis.keys) == 0
