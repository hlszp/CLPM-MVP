"""G5 observation must keep concurrency identity and original return/error semantics."""

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core import g5_trace


def events(caplog):
    return [
        json.loads(r.message.removeprefix("G5_TRACE "))
        for r in caplog.records
        if r.message.startswith("G5_TRACE ")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_executor", [False, True])
async def test_concurrent_identity_and_thread_clocks(monkeypatch, caplog, explicit_executor):
    monkeypatch.setenv("CLPM_G5_TRACE", "1")
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    with ThreadPoolExecutor(max_workers=1) as pool:

        async def one(i):
            with g5_trace.span("loop", taskId="parent", loopId=str(i), windowStart="t0"):
                return await g5_trace.threaded(
                    "preprocess", lambda: i, executor=pool if explicit_executor else None
                )

        assert await asyncio.gather(*(one(i) for i in range(3))) == [0, 1, 2]
    records = events(caplog)
    timings = [r for r in records if r["stage"] == "preprocess"]
    assert len({r["spanId"] for r in timings}) == 3
    assert {r["loopId"] for r in timings} == {"0", "1", "2"}
    for row in timings:
        assert row["taskId"] == "parent" and row["windowStart"] == "t0"
        assert (
            row["submitMonoNs"]
            <= row["threadStartMonoNs"]
            <= row["threadEndMonoNs"]
            <= row["awaitReturnMonoNs"]
        )
        assert row["queueMs"] >= 0 and row["executionMs"] >= 0 and row["resumeMs"] >= 0
        execution = next(
            r
            for r in records
            if r["stage"] == "preprocess.execute" and r["loopId"] == row["loopId"]
        )
        assert execution["parentSpanId"] == row["spanId"]
    assert g5_trace._context.get() is None


@pytest.mark.asyncio
async def test_cancelled_await_does_not_claim_thread_completion(monkeypatch, caplog):
    monkeypatch.setenv("CLPM_G5_TRACE", "1")
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    started, release = threading.Event(), threading.Event()

    def work():
        started.set()
        release.wait(timeout=3)
        return 7

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = asyncio.create_task(g5_trace.threaded("cancel", work, executor=pool))
        try:
            while not started.is_set():
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            row = next(r for r in events(caplog) if r["stage"] == "cancel")
            assert row["outcome"] == "CancelledError"
            assert row["executionMs"] is None and row["resumeMs"] is None
            assert "threadEndMonoNs" not in row
        finally:
            release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_on", [False, True])
async def test_same_exception_and_off_no_logs(monkeypatch, caplog, trace_on):
    monkeypatch.setenv("CLPM_G5_TRACE", "1" if trace_on else "0")
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    error = ValueError("private input must not be logged")

    def fail():
        raise error

    with pytest.raises(ValueError) as caught:
        with g5_trace.span("loop", loopId="failed"):
            await g5_trace.threaded("work", fail)
    assert caught.value is error
    assert g5_trace._context.get() is None
    records = events(caplog)
    assert bool(records) is trace_on
    assert "private input" not in caplog.text
    if trace_on:
        assert all(r["outcome"] == "ValueError" for r in records)


@pytest.mark.asyncio
async def test_decorator_and_logging_failure_preserve_result(monkeypatch):
    monkeypatch.setenv("CLPM_G5_TRACE", "1")

    def broken_logger(*args, **kwargs):
        raise RuntimeError("log unavailable")

    monkeypatch.setattr(g5_trace.logger, "info", broken_logger)

    @g5_trace.traced("calculate")
    async def calculate(loop_id, logical_run_id, start, end, secret=None):
        assert g5_trace._context.get()["loopId"] == loop_id
        return 42

    assert await calculate("loop", "parent", "t0", "t1", secret="hidden") == 42
    assert g5_trace._context.get() is None


@pytest.mark.asyncio
async def test_cancel_end_race_cannot_report_negative_resume(monkeypatch, caplog):
    """Thread finishes between caller-return timestamp and clocks read: valid interleaving."""
    monkeypatch.setenv("CLPM_G5_TRACE", "1")
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    entered, release, job_complete = threading.Event(), threading.Event(), threading.Event()
    main_id = threading.get_ident()
    actual_clock = g5_trace.time.perf_counter_ns
    main_reads = 0
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:

        def submit(pool, fn, *args):
            future = pool.submit(fn, *args)
            future.add_done_callback(lambda _future: job_complete.set())
            return asyncio.wrap_future(future, loop=loop)

        monkeypatch.setattr(loop, "run_in_executor", submit)

        def measured_clock():
            nonlocal main_reads
            stamp = actual_clock()
            if threading.get_ident() == main_id:
                main_reads += 1
                if main_reads == 2:
                    release.set()
                    assert job_complete.wait(timeout=1)
            return stamp

        monkeypatch.setattr(g5_trace.time, "perf_counter_ns", measured_clock)

        def work():
            entered.set()
            assert release.wait(timeout=1)

        pending = asyncio.create_task(g5_trace.threaded("cancel_race", work, executor=executor))
        while not entered.is_set():
            await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        row = next(r for r in events(caplog) if r["stage"] == "cancel_race")
        assert row["outcome"] == "CancelledError"
        assert row["threadEndMonoNs"] > row["awaitReturnMonoNs"]
        assert row["resumeMs"] is None, row
