"""Additional non-author offline resource/cancellation tests. No DB/TD I/O.
Uses production orchestration and a test-only storage fixture, never a service.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.tasks import kpi_calc
from tests.test_g5_data_acquisition_failure import _batch, batch_boundaries


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Query memory exhausted", "[0x073a] parse error"])
async def test_td_partial_signature_does_not_activate_resource_retry(monkeypatch, batch_boundaries, message):
    _, failed, sleep = batch_boundaries
    error = RuntimeError(message)
    calc = AsyncMock(side_effect=error)
    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calc)
    assert await _batch() == [error]
    calc.assert_awaited_once()
    sleep.assert_not_awaited()
    failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_three_attempts_close_session_before_backoff_and_progress_once(monkeypatch, batch_boundaries):
    sessions, failed, _ = batch_boundaries
    events = []
    error = RuntimeError("[0x073a]: Query memory exhausted")
    async def calculate(**kwargs):
        events.append(("calculate", len(sessions)))
        raise error
    async def close(session, **kwargs):
        events.append(("close", sessions.index(session)+1))
    async def sleep(delay):
        events.append(("backoff", delay))
    progress = AsyncMock()
    monkeypatch.setattr(kpi_calc,"_calculate_loop_kpi",calculate)
    monkeypatch.setattr("app.core.db.close_session_guarded",close)
    monkeypatch.setattr(kpi_calc.asyncio,"sleep",sleep)
    with pytest.raises(kpi_calc.BatchResourceFailure):
        await _batch(final_attempt=True,on_completed=progress)
    assert events == [("calculate",1),("close",1),("backoff",2),
                      ("calculate",2),("close",2),("backoff",4),
                      ("calculate",3),("close",3)]
    progress.assert_awaited_once()
    failed.assert_awaited_once()
    assert failed.await_args.kwargs['error'] is error


@pytest.mark.asyncio
async def test_parent_cancellation_propagates_after_session_close_without_retry(monkeypatch,batch_boundaries):
    _, failed, sleep = batch_boundaries
    started = asyncio.Event()
    closed = asyncio.Event()
    calls = 0
    async def calculate(**kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Event().wait()
    async def close(*args,**kwargs):
        closed.set()
    monkeypatch.setattr(kpi_calc,"_calculate_loop_kpi",calculate)
    monkeypatch.setattr("app.core.db.close_session_guarded",close)
    job=asyncio.create_task(_batch())
    await asyncio.wait_for(started.wait(),1)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job,1)
    assert closed.is_set() and calls == 1
    sleep.assert_not_awaited()
    failed.assert_not_awaited()


def test_unknown_errors_leave_existing_g29_threshold_intact():
    assert kpi_calc._BATCH_FAILURE_ABORT_RATIO == 0.5
    kpi_calc._assert_batch_resources([ValueError("unknown SQL")])
    kpi_calc._assert_batch_health({"failed":499},1000)
    with pytest.raises(kpi_calc.BatchSystemicFailure):
        kpi_calc._assert_batch_health({"failed":500},1000)
