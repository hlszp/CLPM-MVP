"""需显式 TEST_REDIS_URL 的隔离 Redis 回归；不启动额外 Worker/Beat。"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from celery._state import _task_stack
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError

from app.schemas.task import TaskStatus
from app.services import task_tracker
from app.tasks import alert_patrol
from app.tasks.celery_app import PRIORITY_EVENT, PRIORITY_MANUAL, PRIORITY_NIGHTLY, celery_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="需提供隔离 TEST_REDIS_URL"),
]


@pytest.mark.parametrize("parent_priority", [PRIORITY_EVENT, PRIORITY_MANUAL, PRIORITY_NIGHTLY])
def test_child_task_inherits_parent_priority_without_overriding_explicit(parent_priority):
    """真实 Redis 消息验证人工子任务不掉到后台桶；显式后台等级仍保留。"""
    queue = f"qa-ops1043-{uuid4().hex}"
    options = dict(celery_app.conf.broker_transport_options)
    options["global_keyprefix"] = queue + ":"
    parent = alert_patrol.run_alert_patrol
    with celery_app.connection_for_write(
        os.environ["TEST_REDIS_URL"], transport_options=options
    ) as connection:
        messages = connection.SimpleQueue(queue)
        parent.push_request(
            id=str(uuid4()),
            called_directly=False,
            delivery_info={"priority": parent_priority},
        )
        _task_stack.push(parent)
        try:
            producer = connection.Producer()
            parent.apply_async(queue=queue, producer=producer, ignore_result=True)
            inherited = messages.get(block=True, timeout=3)
            assert inherited.properties["priority"] == parent_priority
            inherited.ack()
            parent.apply_async(
                queue=queue, producer=producer, ignore_result=True, priority=PRIORITY_NIGHTLY
            )
            explicit = messages.get(block=True, timeout=3)
            assert explicit.properties["priority"] == PRIORITY_NIGHTLY
            explicit.ack()
        finally:
            _task_stack.pop()
            parent.pop_request()
            messages.clear()
            messages.close()
            connection.default_channel.queue_delete(queue)


@pytest.fixture
async def scratch_redis(monkeypatch):
    client = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    prefix = f"qa:ops1042:{uuid4().hex}:"
    monkeypatch.setattr("app.core.redis.redis_client", client)
    monkeypatch.setattr(task_tracker, "redis_client", client)
    monkeypatch.setattr(alert_patrol, "_PATROL_LOCK_KEY", prefix + "patrol-lock")
    try:
        await client.ping()
        yield client, prefix
    finally:
        keys = [key async for key in client.scan_iter(match=prefix + "*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()


@pytest.mark.parametrize("blocking", [False, True], ids=["get", "brpop"])
def test_redis_consumes_event_then_manual_then_background(blocking):
    """经真实 Celery 发布、Kombu 取出，覆盖配置默认值与两条 Redis 消费路径。"""
    queue = f"qa-ops1042-{uuid4().hex}"
    options = dict(celery_app.conf.broker_transport_options)
    options["global_keyprefix"] = f"{queue}:"
    with celery_app.connection_for_write(
        os.environ["TEST_REDIS_URL"], transport_options=options
    ) as connection:
        messages = connection.SimpleQueue(queue)
        try:
            producer = connection.Producer()
            for label, priority in (
                ("default-background", None),
                ("nightly", PRIORITY_NIGHTLY),
                ("manual", PRIORITY_MANUAL),
                ("event", PRIORITY_EVENT),
            ):
                kwargs = {"priority": priority} if priority is not None else {}
                alert_patrol.run_alert_patrol.apply_async(
                    queue=queue,
                    producer=producer,
                    headers={"qa-label": label},
                    ignore_result=True,
                    **kwargs,
                )
            received = []
            for _ in range(4):
                message = messages.get(block=blocking, timeout=3)
                received.append(message.headers["qa-label"])
                message.ack()
            assert received == ["event", "manual", "default-background", "nightly"]
        finally:
            messages.clear()
            messages.close()
            connection.default_channel.queue_delete(queue)


async def test_overlapping_patrols_share_one_round(scratch_redis, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def body():
        nonlocal calls
        calls += 1
        entered.set()
        await finish.wait()
        return {"total_loops": 1}

    monkeypatch.setattr(alert_patrol, "_patrol_loops", body)
    first = asyncio.create_task(alert_patrol._do_patrol())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        duplicates = await asyncio.gather(*(alert_patrol._do_patrol() for _ in range(6)))
        assert all(item == {"skipped": True, "reason": "already_running"} for item in duplicates)
        assert calls == 1
    finally:
        finish.set()
        await first
    client, _ = scratch_redis
    assert await client.get(alert_patrol._PATROL_LOCK_KEY) is None


async def test_long_patrol_renews_lease(scratch_redis, monkeypatch):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def body():
        entered.set()
        await finish.wait()
        return {"total_loops": 1}

    monkeypatch.setattr(alert_patrol, "_PATROL_LEASE_SECONDS", 0.3)
    monkeypatch.setattr(alert_patrol, "_PATROL_RENEW_SECONDS", 0.05)
    monkeypatch.setattr(alert_patrol, "_patrol_loops", body)
    first = asyncio.create_task(alert_patrol._do_patrol())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0.5)  # 超过最初 TTL；没有续租会错误启动第二轮。
        assert await alert_patrol._do_patrol() == {"skipped": True, "reason": "already_running"}
    finally:
        finish.set()
        await first


async def test_lost_lease_stops_work_without_deleting_new_owner(scratch_redis, monkeypatch):
    client, _ = scratch_redis
    entered, stopped = asyncio.Event(), asyncio.Event()

    async def body():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(alert_patrol, "_PATROL_RENEW_SECONDS", 0.05)
    monkeypatch.setattr(alert_patrol, "_patrol_loops", body)
    first = asyncio.create_task(alert_patrol._do_patrol())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await client.set(alert_patrol._PATROL_LOCK_KEY, "replacement-owner", ex=5)
        with pytest.raises(LockNotOwnedError):
            await first
        assert stopped.is_set()
        assert await client.get(alert_patrol._PATROL_LOCK_KEY) == "replacement-owner"
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


async def test_abandoned_lock_expires_and_next_round_can_run(scratch_redis, monkeypatch):
    client, _ = scratch_redis
    await client.set(alert_patrol._PATROL_LOCK_KEY, "dead-worker", px=50)
    assert await alert_patrol._do_patrol() == {"skipped": True, "reason": "already_running"}
    await asyncio.sleep(0.1)

    async def body():
        return {"total_loops": 0}

    monkeypatch.setattr(alert_patrol, "_patrol_loops", body)
    assert await alert_patrol._do_patrol() == {"total_loops": 0}


async def test_retry_and_late_status_sync_preserve_execution_clock(scratch_redis):
    client, prefix = scratch_redis
    task_id = uuid4().hex
    key = task_tracker._task_key(task_id)
    start = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    end = start + timedelta(seconds=9)
    await client.hset(key, mapping={"task_id": task_id, "status": "PENDING"})
    try:
        await task_tracker.update_status(task_id, TaskStatus.RUNNING, started_at=start.isoformat())
        await task_tracker.update_status(
            task_id, TaskStatus.RUNNING, started_at=(start + timedelta(seconds=3)).isoformat()
        )
        await task_tracker.update_status(task_id, TaskStatus.SUCCESS, finished_at=end.isoformat())
        await task_tracker.update_status(
            task_id, TaskStatus.SUCCESS, finished_at=(end + timedelta(hours=1)).isoformat()
        )
        data = await task_tracker.get_task(task_id)
        assert data["started_at"] == start.isoformat()
        assert data["finished_at"] == end.isoformat()
        await task_tracker.update_status(task_id, TaskStatus.RUNNING, progress=0.1)
        assert (await task_tracker.get_task(task_id))["status"] == "SUCCESS"
    finally:
        await client.delete(key)
