"""整轮巡检互斥：重叠跳过、异常释放、续租失效时停止工作。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ConnectionError, LockNotOwnedError

from app.tasks import alert_patrol


@pytest.fixture
def patrol_deps(monkeypatch):
    lock = MagicMock()
    lock.acquire = AsyncMock(return_value=True)
    lock.extend = AsyncMock(return_value=True)
    lock.release = AsyncMock()
    redis = MagicMock()
    redis.lock.return_value = lock
    monkeypatch.setattr("app.core.redis.redis_client", redis)
    monkeypatch.setattr("app.core.db.AsyncSessionLocal", MagicMock())
    monkeypatch.setattr("app.core.db.close_session_guarded", AsyncMock())
    return lock


@pytest.mark.asyncio
async def test_overlapping_round_skips_before_loading_loops(monkeypatch, patrol_deps):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def switch(_db):
        entered.set()
        await finish.wait()
        return False

    enabled = AsyncMock(side_effect=switch)
    monkeypatch.setattr("app.services.alert_rule_engine.service.get_global_switch", enabled)
    patrol_deps.acquire.side_effect = [True, False]
    first = asyncio.create_task(alert_patrol._do_patrol())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        second = await asyncio.wait_for(alert_patrol._do_patrol(), 0.1)
        assert second == {"skipped": True, "reason": "already_running"}
        assert enabled.await_count == 1
    finally:
        finish.set()
        await first
    patrol_deps.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_patrol_exception_releases_owned_lock(monkeypatch, patrol_deps):
    monkeypatch.setattr(
        "app.services.alert_rule_engine.service.get_global_switch",
        AsyncMock(side_effect=ValueError("database unavailable")),
    )
    with pytest.raises(ValueError, match="database unavailable"):
        await alert_patrol._do_patrol()
    patrol_deps.release.assert_awaited_once()


@pytest.mark.asyncio
async def test_renewal_failure_cancels_patrol(monkeypatch, patrol_deps):
    stopped = asyncio.Event()

    async def switch(_db):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr("app.services.alert_rule_engine.service.get_global_switch", switch)
    monkeypatch.setattr(alert_patrol, "_PATROL_RENEW_SECONDS", 0.001, raising=False)
    patrol_deps.extend.side_effect = LockNotOwnedError("lease lost")
    with pytest.raises(LockNotOwnedError, match="lease lost"):
        await asyncio.wait_for(alert_patrol._do_patrol(), 1)
    assert stopped.is_set()
    patrol_deps.release.assert_awaited_once()


def test_periodic_patrol_expires_instead_of_replaying_old_ticks():
    entry = alert_patrol.celery_app.conf.beat_schedule["alert-patrol"]
    assert entry["options"]["expires"] == 60


@pytest.mark.asyncio
async def test_lock_connection_failure_does_not_start_patrol(monkeypatch, patrol_deps):
    enabled = AsyncMock()
    monkeypatch.setattr("app.services.alert_rule_engine.service.get_global_switch", enabled)
    patrol_deps.acquire.side_effect = ConnectionError("redis unavailable")
    with pytest.raises(ConnectionError, match="redis unavailable"):
        await alert_patrol._do_patrol()
    enabled.assert_not_awaited()
    patrol_deps.release.assert_not_awaited()
