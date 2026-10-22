"""真实 Redis：发布前接管、失锁清理、解锁交接与报告盘失败的反例回归。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import LockError

from app.core.config import settings
from app.services import ten_minute_control as control
from app.services.result_ledger import canonical_json
from app.tasks import ten_minute_shadow as tasks
from tests.integration.test_ten_minute_shadow_real import isolated_redis  # noqa: F401

pytestmark = pytest.mark.integration


async def seed(redis, monkeypatch, tmp_path, *, loop_count=1):
    loops, owner = [str(uuid4()) for _ in range(loop_count)], str(uuid4())
    now = datetime.now(UTC)
    state = {
        "id": owner,
        "kind": "MANUAL_SHADOW",
        "sourceTaskId": "publication-fence",
        "loopIds": loops,
        "cursor": 0,
        "start": now.isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
        "createdAt": now.isoformat(),
        "status": "QUEUED",
        "reports": [],
    }
    raw = canonical_json(state)
    await redis.set(control.ACTIVE_KEY, raw, ex=900)
    monkeypatch.setattr(tasks, "redis_client", redis)
    monkeypatch.setattr(settings, "CLPM_KPI_SHADOW_REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        control, "read_config", AsyncMock(return_value={"enabled": True, "loopIds": loops})
    )
    control.save_report(owner, state)
    return owner, state, raw


async def test_takeover_during_final_configuration_read_cannot_publish_equal(
    isolated_redis, monkeypatch, tmp_path
):
    owner, state, raw = await seed(isolated_redis, monkeypatch, tmp_path)
    count = 0

    async def read_config():
        nonlocal count
        count += 1
        if count == 2:
            await isolated_redis.set(control.LEASE_KEY, "successor-token", ex=60)
        return {"enabled": True, "loopIds": state["loopIds"]}

    monkeypatch.setattr(control, "read_config", read_config)
    monkeypatch.setattr(tasks, "_work", AsyncMock(return_value={"status": "EQUAL"}))
    with pytest.raises(LockError):
        await tasks.run_step(owner, 0)
    assert await isolated_redis.get(control.ACTIVE_KEY) == raw
    assert await isolated_redis.get(control.LEASE_KEY) == "successor-token"
    reports = [json.loads(p.read_text()) for p in tmp_path.glob("*.json")]
    assert not any(r.get("status") == "EQUAL" for r in reports)
    assert json.loads(control.report_path(owner).read_text()) == state


async def test_failed_renewal_does_not_delete_same_cohort_successor_state(
    isolated_redis, monkeypatch, tmp_path
):
    owner, state, raw = await seed(isolated_redis, monkeypatch, tmp_path)

    async def work(_state):
        await asyncio.Event().wait()

    async def takeover(*_):
        await isolated_redis.set(control.LEASE_KEY, "successor-token", ex=60)
        raise LockError("injected renewal takeover")

    monkeypatch.setattr(tasks, "_work", work)
    monkeypatch.setattr(tasks, "_renew", takeover)
    with pytest.raises(LockError, match="injected renewal takeover"):
        await tasks.run_step(owner, 0)
    assert await isolated_redis.get(control.ACTIVE_KEY) == raw
    assert await isolated_redis.get(control.LEASE_KEY) == "successor-token"
    assert json.loads(control.report_path(owner).read_text()) == state


async def test_next_step_is_dispatched_after_execution_lease_release(
    isolated_redis, monkeypatch, tmp_path
):
    owner, _, _ = await seed(isolated_redis, monkeypatch, tmp_path, loop_count=2)
    monkeypatch.setattr(tasks, "_work", AsyncMock(return_value={"status": "EQUAL"}))
    event_loop = asyncio.get_running_loop()
    observations = []

    def dispatch(next_owner, cursor):
        # apply_async 的线程中读真实 Redis；事件循环继续服务该读命令。
        lease = asyncio.run_coroutine_threadsafe(
            isolated_redis.get(control.LEASE_KEY), event_loop
        ).result(timeout=5)
        observations.append((next_owner, cursor, lease))

    monkeypatch.setattr(control, "dispatch", dispatch)
    report = await tasks.run_step(owner, 0)
    assert report["status"] == "EQUAL"
    assert observations == [(owner, 1, None)]
    state = json.loads(await isolated_redis.get(control.ACTIVE_KEY))
    assert state["cursor"] == 1 and state["status"] == "QUEUED"


async def test_report_disk_failure_releases_own_state_and_keeps_original_error(
    isolated_redis, monkeypatch, tmp_path
):
    owner, _, _ = await seed(isolated_redis, monkeypatch, tmp_path)
    monkeypatch.setattr(tasks, "_work", AsyncMock(return_value={"status": "EQUAL"}))
    disk_error = OSError("injected full report volume")

    def fail_report(*_):
        raise disk_error

    monkeypatch.setattr(control, "save_report", fail_report)
    with pytest.raises(OSError) as raised:
        await tasks.run_step(owner, 0)
    assert raised.value is disk_error
    assert await isolated_redis.get(control.ACTIVE_KEY) is None
    assert await isolated_redis.get(control.LEASE_KEY) is None


async def test_terminal_report_failure_preserves_successor_state_and_owner_report(
    isolated_redis, monkeypatch, tmp_path
):
    owner, _, _ = await seed(isolated_redis, monkeypatch, tmp_path, loop_count=2)
    monkeypatch.setattr(tasks, "_work", AsyncMock(return_value={"status": "EQUAL"}))
    actual_fence, actual_save = control.fenced_step, control.save_report
    successor = {}
    disk_error = OSError("terminal report volume failed after atomic handoff")

    async def commit_then_take_over(redis, old, token, new=None, *, renew=False):
        committed = await actual_fence(redis, old, token, new, renew=renew)
        if committed and new is not None and not renew:
            successor.update(new)
            await isolated_redis.set(control.LEASE_KEY, "successor-token", ex=60)
            actual_save(owner, successor)
        return committed

    def fail_terminal(report_id, report):
        if report.get("status") == "EQUAL":
            raise disk_error
        return actual_save(report_id, report)

    monkeypatch.setattr(control, "fenced_step", commit_then_take_over)
    monkeypatch.setattr(control, "save_report", fail_terminal)
    with pytest.raises(OSError) as raised:
        await tasks.run_step(owner, 0)
    assert raised.value is disk_error
    assert await isolated_redis.get(control.ACTIVE_KEY) == canonical_json(successor)
    assert await isolated_redis.get(control.LEASE_KEY) == "successor-token"
    assert json.loads(control.report_path(owner).read_text()) == successor
    attempts = [json.loads(p.read_text()) for p in tmp_path.glob("*.json") if p.stem != owner]
    assert attempts and all(r["status"] == "PENDING" for r in attempts)


async def test_renewal_failure_cancels_initial_configuration_query(
    isolated_redis, monkeypatch, tmp_path
):
    owner, _, _ = await seed(isolated_redis, monkeypatch, tmp_path)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def config():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def failed_renewal(*_):
        await started.wait()
        raise LockError("renewal failed during initial configuration query")

    work = AsyncMock(return_value={"status": "EQUAL"})
    monkeypatch.setattr(control, "read_config", config)
    monkeypatch.setattr(tasks, "_renew", failed_renewal)
    monkeypatch.setattr(tasks, "_work", work)
    with pytest.raises(LockError, match="initial configuration query"):
        await asyncio.wait_for(tasks.run_step(owner, 0), timeout=2)
    assert cancelled.is_set()
    work.assert_not_awaited()
    assert await isolated_redis.get(control.ACTIVE_KEY) is None
    assert await isolated_redis.get(control.LEASE_KEY) is None


async def test_cancellation_during_initial_configuration_query_drains_and_releases(
    isolated_redis, monkeypatch, tmp_path
):
    owner, _, _ = await seed(isolated_redis, monkeypatch, tmp_path)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def config():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(control, "read_config", config)
    running = asyncio.create_task(tasks.run_step(owner, 0))
    await asyncio.wait_for(started.wait(), timeout=2)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=2)
    assert cancelled.is_set()
    assert await isolated_redis.get(control.ACTIVE_KEY) is None
    assert await isolated_redis.get(control.LEASE_KEY) is None


async def test_initial_dispatch_unknown_outcome_cannot_delete_acquired_step(
    isolated_redis, monkeypatch, tmp_path
):
    _, state, _ = await seed(isolated_redis, monkeypatch, tmp_path)
    await isolated_redis.delete(control.ACTIVE_KEY)
    event_loop = asyncio.get_running_loop()
    accepted = {}
    broker_error = OSError("broker accepted delivery but reply was lost")

    async def acquire_delivered_step():
        accepted["raw"] = await isolated_redis.get(control.ACTIVE_KEY)
        lock = isolated_redis.lock(control.LEASE_KEY, timeout=60, blocking=False)
        assert await lock.acquire()
        accepted["token"] = await isolated_redis.get(control.LEASE_KEY)

    def dispatch(*_):
        asyncio.run_coroutine_threadsafe(acquire_delivered_step(), event_loop).result(timeout=5)
        raise broker_error

    monkeypatch.setattr(control, "dispatch", dispatch)
    with pytest.raises(OSError) as raised:
        await control.enqueue(
            isolated_redis,
            loop_ids=state["loopIds"],
            start=datetime.fromisoformat(state["start"]),
            end=datetime.fromisoformat(state["end"]),
            kind="MANUAL_SHADOW",
        )
    assert raised.value is broker_error
    assert await isolated_redis.get(control.ACTIVE_KEY) == accepted["raw"]
    assert await isolated_redis.get(control.LEASE_KEY) == accepted["token"]
