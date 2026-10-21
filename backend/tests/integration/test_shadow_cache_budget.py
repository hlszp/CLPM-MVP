"""真实 Redis 验证 v2 缓存计费/LRU；所有键均使用独立测试命名空间。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services import ten_minute_blocks as blocks
from tests.integration.test_ten_minute_shadow_real import isolated_redis  # noqa: F401

pytestmark = pytest.mark.integration
T = datetime(2026, 10, 18, tzinfo=UTC)


async def assert_budget(redis, *, exact=True):
    client, prefix = redis.client, redis.prefix
    index = set(await client.zrange(prefix + blocks.INDEX_KEY, 0, -1))
    sizes = await client.hgetall(prefix + blocks.SIZES_KEY)
    heads = await client.hgetall(prefix + blocks.HEADS_KEY)
    charged = int(await client.get(prefix + blocks.BYTES_KEY) or 0)
    actual = {
        k: await client.strlen(k) async for k in client.scan_iter(prefix + "shadow10:events:v2:*")
    }
    assert len(index) <= blocks.MAX_BLOCKS
    assert set(sizes) == index
    assert set(actual) <= index
    assert len(heads) <= len(index)
    assert sum(int(n) for n in sizes.values()) == charged
    assert sum(actual.values()) <= charged <= blocks.MAX_TOTAL_PACKED
    if exact:
        assert sum(actual.values()) == charged
    for key in (
        blocks.INDEX_KEY,
        blocks.SIZES_KEY,
        blocks.BYTES_KEY,
        blocks.CLOCK_KEY,
        blocks.HEADS_KEY,
    ):
        assert 0 < await client.ttl(prefix + key) <= blocks.TTL
    return index, charged


def make_reader(rows):
    async def read(points, start, end, **kwargs):
        assert kwargs == {"full_columns": False}
        return {p: [dict(r) for r in rows if start <= r["ts"] <= end] for p in points}

    return read


async def test_atomic_lru_uses_last_access_and_keeps_new_historical_block(
    isolated_redis, monkeypatch
):
    monkeypatch.setattr(blocks, "MAX_BLOCKS", 2)
    point = str(uuid4())
    cache = blocks.TenMinuteBlocks(isolated_redis, reader=make_reader([]))
    first, second = T + 2 * blocks.BLOCK, T + 3 * blocks.BLOCK
    for start in (first, second):
        await cache.prepare(point, start)
    revision = blocks.event_digest([])
    await cache._cache_update(point, first, revision)  # 命中访问刷新 LRU，非源时间排序。
    await cache.prepare(point, T)
    index, _ = await assert_budget(isolated_redis)
    assert index == {
        isolated_redis.prefix + f"{cache.prefix(point, start)}:{revision}" for start in (T, first)
    }
    assert await isolated_redis.hget(blocks.HEADS_KEY, cache.prefix(point, second)) is None


async def test_atomic_total_bytes_and_concurrent_duplicate_puts_do_not_drift(
    isolated_redis, monkeypatch
):
    monkeypatch.setattr(blocks, "MAX_TOTAL_PACKED", 512)
    cache = blocks.TenMinuteBlocks(isolated_redis)
    points = [str(uuid4()) for _ in range(8)]
    rows = [{"ts": T, "value": 42.0, "quality_class": 1}]
    revision = blocks.event_digest(rows)
    # 同键重放和不同键抢占并发；Lua 必须按真实 value 大小而非调用次数计费。
    await asyncio.gather(*(cache._put(p, T, revision, rows) for _ in range(8) for p in points))
    index, charged = await assert_budget(isolated_redis)
    assert 0 < charged <= 512 and 0 < len(index) < len(points)
    # 随机改值的新内容键与旧版本共存，也不能突破 payload 或元数据预算。
    await asyncio.gather(
        *(
            cache._put(p, T, blocks.event_digest(changed), changed)
            for value in range(5)
            for p in points
            for changed in [[{**rows[0], "value": float(value)}]]
        )
    )
    await assert_budget(isolated_redis)


async def test_expired_and_corrupt_block_rebuild_conserves_real_bytes(isolated_redis):
    point = str(uuid4())
    rows = [{"ts": T, "value": 42.0, "quality_class": 1}]
    reader = make_reader(rows)
    cache = blocks.TenMinuteBlocks(isolated_redis, reader=reader)
    revision = await cache.prepare(point, T)
    key = f"{cache.prefix(point, T)}:{revision}"
    _, initial = await assert_budget(isolated_redis)
    # 真实 TTL 只使 payload 过期；索引计费可暂时保守保留。
    await isolated_redis.client.pexpire(isolated_redis.prefix + key, 1)
    await asyncio.sleep(0.02)
    assert await isolated_redis.get(key) is None
    _, charged = await assert_budget(isolated_redis, exact=False)
    assert charged == initial
    reference = blocks.ReferenceEvents(reader)
    await reference([point], T, T + blocks.BLOCK, full_columns=False)
    candidate = blocks.TenMinuteBlocks(isolated_redis, reference, reader)
    assert await candidate._block(point, T) == rows
    assert candidate.stats["misses"] == 1
    assert (await assert_budget(isolated_redis))[1] == initial
    await isolated_redis.set(key, "broken")
    candidate = blocks.TenMinuteBlocks(isolated_redis, reference, reader)
    assert await candidate._block(point, T) == rows
    assert candidate.stats["corrupt"] == 1
    assert (await assert_budget(isolated_redis))[1] == initial


async def test_lua_rejects_single_value_exceeding_total_before_any_write(
    isolated_redis, monkeypatch
):
    monkeypatch.setattr(blocks, "MAX_TOTAL_PACKED", 1)
    with pytest.raises(ValueError, match="总缓存字节预算"):
        await blocks.TenMinuteBlocks(isolated_redis, reader=make_reader([])).prepare(
            str(uuid4()), T
        )
    assert [k async for k in isolated_redis.client.scan_iter(isolated_redis.prefix + "*")] == []
