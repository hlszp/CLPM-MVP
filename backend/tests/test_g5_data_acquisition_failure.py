"""Resource failures are operational errors, never data eligibility evidence.

All storage/provider boundaries are mocked. No Worker, Beat, DB or TDengine is used.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.async_tasks import gather_settled
from app.tasks import kpi_calc


def _loop() -> MagicMock:
    loop = MagicMock()
    loop.id = "c0da7fcb-eadf-4068-840a-bc71a8b7985f"
    loop.tag_name = "PERF-T0892"
    loop.loop_type = "TEMPERATURE"
    return loop


async def _calculate(planner):
    start = datetime(2026, 10, 16, 2)
    return await kpi_calc._calculate_loop_kpi(
        db=AsyncMock(),
        loop=_loop(),
        metric_configs={},
        ts_start=start,
        ts_end=start + timedelta(hours=1),
        data_planner=planner,
        runtime_overrides={},
    )


@pytest.mark.parametrize(
    "error",
    [RuntimeError("[0x073a]: Query memory exhausted"), RuntimeError("invalid SQL")],
)
async def test_provider_failure_propagates_without_inconclusive_snapshot(monkeypatch, error):
    planner = MagicMock()
    planner.request_bundles = AsyncMock(side_effect=error)
    persist = AsyncMock()
    monkeypatch.setattr(kpi_calc, "_persist_snapshot", persist)

    with pytest.raises(RuntimeError) as caught:
        await _calculate(planner)

    assert caught.value is error
    persist.assert_not_awaited()


async def test_real_empty_bundle_still_means_data_insufficient(monkeypatch):
    planner = MagicMock()
    planner.request_bundles = AsyncMock(return_value={})
    persist = AsyncMock(return_value={"status": "INCONCLUSIVE"})
    monkeypatch.setattr(kpi_calc, "_persist_snapshot", persist)

    result = await _calculate(planner)

    assert result["status"] == "INCONCLUSIVE"
    kwargs = persist.await_args.kwargs
    assert kwargs["status"] == "INCONCLUSIVE"
    assert kwargs["fitness_tags"] == {"tags": ["DATA_INSUFFICIENT"]}
    assert kwargs["fitness_detail"]["reason"] == "空Bundle，无可用数据"


@pytest.fixture
def batch_boundaries(monkeypatch):
    sessions = []

    def new_session():
        session = AsyncMock()
        sessions.append(session)
        return session

    monkeypatch.setattr("app.core.db.AsyncSessionLocal", new_session)
    monkeypatch.setattr("app.core.db.close_session_guarded", AsyncMock())
    monkeypatch.setattr(
        "app.services.data_planner.resolve_data_version", AsyncMock(return_value="f02")
    )
    monkeypatch.setattr(kpi_calc, "_build_data_planner", MagicMock())
    append_failed = AsyncMock()
    monkeypatch.setattr(kpi_calc.result_ledger, "append_failed_result_record", append_failed)
    sleep = AsyncMock()
    monkeypatch.setattr(kpi_calc.asyncio, "sleep", sleep)
    return sessions, append_failed, sleep


async def _batch(*, final_attempt=True, on_completed=None):
    loop = _loop()
    start = datetime(2026, 10, 16, 2)
    return await kpi_calc._run_batch_loop_calculations(
        loops=[loop],
        loop_configs={str(loop.id): {"op_lower": None, "op_upper": None}},
        metric_configs={},
        ts_start=start,
        ts_end=start + timedelta(hours=1),
        type_weights=None,
        logical_run_id="4886b5f5-d41f-4269-bb2c-9e1ab0ccc62a",
        runtime_overrides={},
        final_attempt=final_attempt,
        on_completed=on_completed,
    )


@pytest.mark.parametrize(
    "message", ["[0x073a]: Query memory exhausted", "sorry, too many clients already"]
)
async def test_resource_retry_recovers_without_failed_record(
    monkeypatch, batch_boundaries, message
):
    sessions, append_failed, sleep = batch_boundaries
    error = RuntimeError(message)
    calc = AsyncMock(side_effect=[error, {"status": "SUCCESS"}])
    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calc)
    completed = AsyncMock()

    assert await _batch(on_completed=completed) == [{"status": "SUCCESS"}]
    assert calc.await_count == 2
    assert len(sessions) == 2
    sessions[0].rollback.assert_awaited_once()
    sessions[1].commit.assert_awaited_once()
    append_failed.assert_not_awaited()
    sleep.assert_awaited_once_with(2)
    completed.assert_awaited_once_with(str(_loop().id))


async def test_resource_retry_exhaustion_writes_one_terminal_failure(monkeypatch, batch_boundaries):
    sessions, append_failed, sleep = batch_boundaries
    error = RuntimeError("[0x073a]: Query memory exhausted")
    calc = AsyncMock(side_effect=error)
    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calc)
    completed = AsyncMock()

    with pytest.raises(kpi_calc.BatchResourceFailure) as caught:
        await _batch(on_completed=completed)
    assert caught.value.__cause__ is error
    assert calc.await_count == 3
    assert [call.args for call in sleep.await_args_list] == [(2,), (4,)]
    append_failed.assert_awaited_once()
    assert append_failed.await_args.kwargs["error"] is error
    assert append_failed.await_args.kwargs["algorithm_version"] == kpi_calc.ALGORITHM_VERSION
    assert (
        append_failed.await_args.kwargs["logical_run_id"] == "4886b5f5-d41f-4269-bb2c-9e1ab0ccc62a"
    )
    assert len(sessions) == 3
    for session in sessions:
        session.rollback.assert_awaited_once()
    sessions[-1].commit.assert_awaited_once()
    completed.assert_awaited_once_with(str(_loop().id))


async def test_nonterminal_resource_exhaustion_waits_for_celery_retry(
    monkeypatch, batch_boundaries
):
    _, append_failed, _ = batch_boundaries
    error = RuntimeError("[0x073a]: Query memory exhausted")
    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", AsyncMock(side_effect=error))

    with pytest.raises(kpi_calc.BatchResourceFailure) as caught:
        await _batch(final_attempt=False)
    assert caught.value.__cause__ is error
    append_failed.assert_not_awaited()


@pytest.mark.parametrize(
    "message", ["invalid SQL", "network timeout", "query memory usage warning"]
)
async def test_unknown_error_is_not_locally_retried(monkeypatch, batch_boundaries, message):
    _, append_failed, sleep = batch_boundaries
    error = RuntimeError(message)
    calc = AsyncMock(side_effect=error)
    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calc)

    assert await _batch() == [error]
    calc.assert_awaited_once()
    sleep.assert_not_awaited()
    append_failed.assert_awaited_once()


async def test_cancelled_provider_is_neither_retried_nor_failed_record(
    monkeypatch, batch_boundaries
):
    _, append_failed, sleep = batch_boundaries
    calc = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calc)

    result = await _batch()

    assert len(result) == 1 and isinstance(result[0], asyncio.CancelledError)
    calc.assert_awaited_once()
    sleep.assert_not_awaited()
    append_failed.assert_not_awaited()


async def test_cancel_during_resource_backoff_does_not_retry(monkeypatch, batch_boundaries):
    _, append_failed, sleep = batch_boundaries
    calc = AsyncMock(side_effect=RuntimeError("[0x073a]: Query memory exhausted"))
    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calc)
    sleep.side_effect = asyncio.CancelledError()

    result = await _batch()

    assert len(result) == 1 and isinstance(result[0], asyncio.CancelledError)
    calc.assert_awaited_once()
    append_failed.assert_not_awaited()


@pytest.mark.parametrize("path", ["planner", "point_builder"])
async def test_sibling_queries_settle_before_local_retry(monkeypatch, batch_boundaries, path):
    """Exercise both production fan-out sites through the batch retry boundary."""
    _, append_failed, _ = batch_boundaries
    error = RuntimeError("[0x073a]: Query memory exhausted")
    failed = asyncio.Event()
    release = asyncio.Event()
    settled = asyncio.Event()

    async def query(fails):
        if fails:
            failed.set()
            raise error
        await release.wait()
        settled.set()
        return MagicMock(point_count=0)

    start = datetime(2026, 10, 16, 2, tzinfo=UTC)
    if path == "planner":
        from app.contracts.data_types import ControlType, TagGroup, TimeWindow
        from app.services.data_planner import DataPlanner, QueryTask

        planner = DataPlanner.__new__(DataPlanner)
        planner._cache = None

        async def preprocess(**kwargs):
            return await query(kwargs["task"].tag_group == TagGroup.BASE)

        monkeypatch.setattr(planner, "_query_and_preprocess", preprocess)

        async def fanout():
            return await planner._execute_query_plan(
                [QueryTask(TagGroup.BASE, [], ["pv"], 1), QueryTask(TagGroup.OP_HF, [], ["op"], 1)],
                str(_loop().id),
                TimeWindow(start=start, end=start + timedelta(hours=1)),
                ControlType.TEMPERATURE,
                SimpleNamespace(config_version="f02"),
            )

    else:
        from app.services.data_source import logical_wide_builder as builder

        segments = {
            "PV": [SimpleNamespace(point_id="point-a", initial_check=start)],
            "OP": [SimpleNamespace(point_id="point-b", initial_check=start)],
        }
        monkeypatch.setattr(builder, "_resolve_role_segments", AsyncMock(return_value=segments))

        async def events(points, *_args, **_kwargs):
            result = await query(points[0] == "point-a")
            return {points[0]: result}

        monkeypatch.setattr(builder.repo, "read_events", events)

        async def fanout():
            return await builder.build_logical_wide(
                None, str(_loop().id), ["pv", "op"], start, start + timedelta(hours=1)
            )

    attempts = 0

    async def calc(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return await fanout()
        assert settled.is_set(), "retry must not overlap an abandoned sibling query"
        return {"status": "SUCCESS"}

    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calc)
    job = asyncio.create_task(_batch())
    try:
        await asyncio.wait_for(failed.wait(), 1)
        for _ in range(3):
            # batch fixture mocks sleep for retry backoff; wait with loop callbacks instead.
            turn = asyncio.get_running_loop().create_future()
            asyncio.get_running_loop().call_soon(turn.set_result, None)
            await turn
        assert not job.done()
        assert attempts == 1
    finally:
        release.set()
        result = await job
    assert result == [{"status": "SUCCESS"}]
    assert attempts == 2
    append_failed.assert_not_awaited()


async def test_settled_gather_preserves_result_order():
    done = asyncio.Event()

    async def first():
        await done.wait()
        return "first"

    async def second():
        done.set()
        return "second"

    assert await gather_settled(first(), second()) == ["first", "second"]


async def test_settled_gather_preserves_first_observed_error_identity():
    earlier = RuntimeError("earlier second input")
    later = RuntimeError("later first input")
    started = asyncio.Event()

    async def first():
        await started.wait()
        raise later

    async def second():
        started.set()
        raise earlier

    with pytest.raises(RuntimeError) as caught:
        await gather_settled(first(), second())
    assert caught.value is earlier


async def test_settled_gather_waits_for_sibling_after_child_cancel():
    cancelled = asyncio.CancelledError("provider cancelled")
    settled = asyncio.Event()

    async def first():
        raise cancelled

    async def sibling():
        settled.set()
        return "done"

    with pytest.raises(asyncio.CancelledError) as caught:
        await gather_settled(first(), sibling())
    assert caught.value is cancelled
    assert settled.is_set()


async def test_caller_cancel_waits_for_async_sibling_cleanup():
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned = asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release_cleanup.wait()
            cleaned.set()

    job = asyncio.create_task(gather_settled(operation()))
    await asyncio.wait_for(started.wait(), 1)
    job.cancel()
    await asyncio.wait_for(cleaning.wait(), 1)
    try:
        assert not job.done()
    finally:
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await job
    assert cleaned.is_set()


@pytest.mark.parametrize(
    "message", ["[0x073a]: Query memory exhausted", "too many clients already"]
)
def test_one_resource_failure_in_thousand_cannot_be_success(message):
    error = RuntimeError(message)
    results = [{"status": "SUCCESS"}] * 999 + [error]
    with pytest.raises(kpi_calc.BatchResourceFailure, match="1/1000") as caught:
        kpi_calc._assert_batch_resources(results)
    assert caught.value.__cause__ is error
    # G29's general business-error ratio is independent and unchanged.
    kpi_calc._assert_batch_health({"success": 999, "failed": 1}, 1000)


def test_genuine_inconclusive_and_unknown_business_error_keep_existing_policy():
    kpi_calc._assert_batch_resources([{"status": "SUCCESS"}] * 999 + [{"status": "INCONCLUSIVE"}])
    kpi_calc._assert_batch_resources([{"status": "SUCCESS"}] * 999 + [ValueError("bad rule")])
