"""本机真实 Redis Lua/租约、TDengine 块失效、PG+TD 完整评估三遍对照。"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import text

from app.core.config import settings
from app.services import ten_minute_blocks as blocks
from app.services import ten_minute_control as control
from app.services.ten_minute_shadow import validate_loop
from app.tasks import ten_minute_shadow as tasks
from tests.integration.test_history_native_1043 import scratch_td  # noqa: F401

pytestmark = pytest.mark.integration


class IsolatedRedis:
    """真实 Lua 全程执行；仅给测试自己的 key 加独立命名空间。"""

    def __init__(self, client):
        self.client = client
        self.prefix = f"qa-shadow:{uuid4()}:"

    async def get(self, key):
        return await self.client.get(self.prefix + key)

    async def set(self, key, *args, **kwargs):
        return await self.client.set(self.prefix + key, *args, **kwargs)

    async def delete(self, key):
        return await self.client.delete(self.prefix + key)

    async def eval(self, script, n, *args):
        keys = [self.prefix + str(key) for key in args[:n]]
        return await self.client.eval(script, n, *keys, *args[n:])

    def lock(self, key, **kwargs):
        return self.client.lock(self.prefix + key, **kwargs)


@pytest.fixture
async def isolated_redis():
    target = os.getenv("TEST_REDIS_URL")
    if not target:
        pytest.skip("需要显式 TEST_REDIS_URL")
    assert urlparse(target).hostname in ("127.0.0.1", "localhost")
    client = Redis.from_url(target, decode_responses=True)
    wrapper = IsolatedRedis(client)
    try:
        yield wrapper
    finally:
        keys = [k async for k in client.scan_iter(wrapper.prefix + "*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def test_native_blocks_backfill_and_atomic_cache_budget(
    scratch_td, isolated_redis, monkeypatch
):
    repo = blocks.repo
    origin, point = datetime(2026, 10, 18, tzinfo=UTC), str(uuid4())
    events = [
        repo.PointEvent(
            point_id=point,
            ts=origin + timedelta(seconds=i),
            value=float(i),
            quality_class=1,
            quality_raw=1,
            quality_schema=repo.QSCHEMA_AAS,
            source_kind=repo.SOURCE_KIND_HISTORY,
            source_id="shadow-test",
            received_at=origin,
        )
        for i in range(0, 3601, 2)
    ]
    assert (await repo.write_events(events, dedup=False)).inserted == 1801
    cache = blocks.TenMinuteBlocks(isolated_redis)
    for i in range(6):
        await cache.prepare(point, origin + i * blocks.BLOCK)
    reference = blocks.ReferenceEvents()
    end = origin + timedelta(hours=1)
    direct = await reference([point], origin, end, full_columns=False)
    cached = blocks.TenMinuteBlocks(isolated_redis, reference)
    assert await cached([point], origin, end, full_columns=False) == direct
    assert cached.stats["hits"] == 6 and cached.stats["sourceQueries"] == 1
    # 相同时间戳相同 payload 重放不增加物理输入，也不改变缓存身份。
    await repo.write_events(events, dedup=False)
    assert await repo.read_events([point], origin, end, full_columns=False) == direct
    # 实际迟到补点（奇数秒，旧缓存不存在该事件）及质量变更。
    late = repo.PointEvent(
        point_id=point,
        ts=origin + timedelta(seconds=7),
        value=None,
        quality_class=0,
        quality_raw=0,
        quality_schema=repo.QSCHEMA_AAS,
        source_kind=repo.SOURCE_KIND_HISTORY,
        source_id="shadow-late",
        received_at=end,
    )
    assert (await repo.write_events([late], dedup=False)).inserted == 1
    fresh = blocks.ReferenceEvents()
    changed = await fresh([point], origin, end, full_columns=False)
    updated = blocks.TenMinuteBlocks(isolated_redis, fresh)
    assert await updated([point], origin, end, full_columns=False) == changed
    assert updated.stats["invalidated"] == 1 and updated.stats["hits"] == 5
    monkeypatch.setattr(blocks, "MAX_BLOCKS", 2)
    await cache.prepare(point, origin + 7 * blocks.BLOCK)
    assert await isolated_redis.client.zcard(isolated_redis.prefix + "shadow10:block-index") == 2
    assert (
        len(
            [
                k
                async for k in isolated_redis.client.scan_iter(
                    isolated_redis.prefix + "shadow10:events:*"
                )
                if not k.endswith(":head")
            ]
        )
        == 2
    )


@pytest.mark.skipif(os.getenv("TEST_LOCAL_PG") != "1", reason="需要显式 TEST_LOCAL_PG=1")
async def test_real_full_window_has_identical_inputs_and_all_semantics(isolated_redis, tmp_path):
    from app.core.db import engine

    assert engine.url.host in ("localhost", "127.0.0.1")
    async with engine.connect() as c:
        row = (
            await c.execute(
                text("""
            SELECT loop_id,ts_start,ts_end FROM kpi_snapshot_hourly
            WHERE status='SUCCESS' AND ts_end-ts_start=interval '1 hour'
            ORDER BY ts_end DESC LIMIT 1
        """)
            )
        ).one()
    report = await validate_loop(isolated_redis, str(row[0]), row[1], row[2])
    (tmp_path / "real-report.json").write_text(json.dumps(report, default=str, indent=2))
    assert report["status"] == "EQUAL", report
    assert report["cache"]["prepared"] > 0
    assert report["baseline"]["pointCount"] > 0
    warm = await validate_loop(isolated_redis, str(row[0]), row[1], row[2])
    assert warm["status"] == "EQUAL", warm
    assert warm["cache"]["hits"] > 0
    # 所有 shadow DB 会话均 SET TRANSACTION READ ONLY，正式落库会直接失败。
    print(
        json.dumps(
            {
                "loopId": str(row[0]),
                "start": row[1].isoformat(),
                "end": row[2].isoformat(),
                "cold": report,
                "warm": warm,
            },
            default=str,
        )
    )


async def test_real_lock_duplicate_continuation_cancel_and_lease_loss(
    isolated_redis, monkeypatch, tmp_path
):
    point, second = str(uuid4()), str(uuid4())
    cfg = {"enabled": True, "loopIds": [point, second]}
    monkeypatch.setattr(tasks, "redis_client", isolated_redis)
    monkeypatch.setattr(control, "read_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(control, "dispatch", lambda *args: sent.append(args))
    origin = datetime.now(UTC)
    request = await control.enqueue(
        isolated_redis,
        loop_ids=[point, second],
        start=origin,
        end=origin + timedelta(hours=1),
        kind="MANUAL_SHADOW",
    )
    owner = request["id"]
    started, release = asyncio.Event(), asyncio.Event()

    async def work(state):
        started.set()
        await release.wait()
        return {"status": "EQUAL"}

    monkeypatch.setattr(tasks, "_work", work)
    first = asyncio.create_task(tasks.run_step(owner, 0))
    await started.wait()
    assert (await tasks.run_step(owner, 0))["status"] == "BUSY"
    release.set()
    assert (await first)["status"] == "EQUAL"
    assert (await tasks.run_step(owner, 0))["status"] == "DUPLICATE_OR_STALE"
    assert sent == [(owner, 0), (owner, 1)]
    first_report = json.loads(control.report_path(owner).read_text())
    assert first_report["cursor"] == 1 and first_report["status"] == "QUEUED"

    async def lost(*_args):
        await isolated_redis.set(control.LEASE_KEY, "successor-token", ex=60)
        raise LockError("injected lease loss")

    monkeypatch.setattr(tasks, "_renew", lost)
    with pytest.raises(LockError, match="injected|租约"):
        await tasks.run_step(owner, 1)
    assert await isolated_redis.get(control.ACTIVE_KEY) is None
    assert json.loads(control.report_path(owner).read_text())["status"] == "ERROR"
    assert await isolated_redis.get(control.LEASE_KEY) == "successor-token"
    await isolated_redis.delete(control.LEASE_KEY)

    # 取消后收束工作和租约，不留下可被误报通过的活跃 cohort。
    release.clear()
    started.clear()
    monkeypatch.undo()
    monkeypatch.setattr(tasks, "redis_client", isolated_redis)
    monkeypatch.setattr(control, "read_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(control, "dispatch", lambda *args: None)
    monkeypatch.setattr(tasks, "_work", work)
    req = await control.enqueue(
        isolated_redis,
        loop_ids=[point],
        start=origin,
        end=origin + timedelta(hours=1),
        kind="ASSESS:AUTO",
    )
    active = asyncio.create_task(tasks.run_step(req["id"], 0))
    await started.wait()
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert await isolated_redis.get(control.ACTIVE_KEY) is None
    assert await isolated_redis.get(control.LEASE_KEY) is None
