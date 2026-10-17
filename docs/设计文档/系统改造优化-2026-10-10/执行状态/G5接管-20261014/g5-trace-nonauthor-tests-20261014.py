"""Independent dynamic probes; all external boundaries stubbed, network denied by plugin."""
import asyncio
import io
import json
import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import g5_trace


def rows(caplog):
    return [json.loads(r.getMessage().removeprefix("G5_TRACE ")) for r in caplog.records if r.getMessage().startswith("G5_TRACE ")]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_default_off_and_on_value_identity_exception_and_call_count(monkeypatch, caplog, explicit):
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    sentinel, problem = object(), ValueError("PRIVATE_SECRET_VALUE")
    calls = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        for enabled in (None, "1"):
            if enabled is None:
                monkeypatch.delenv("CLPM_G5_TRACE", raising=False)
                assert not g5_trace.enabled()
            else:
                monkeypatch.setenv("CLPM_G5_TRACE", enabled)
            caplog.clear()

            def success():
                calls.append("value")
                return sentinel

            def fail():
                calls.append("error")
                raise problem

            with g5_trace.span("root", taskId="same", loopId="L", windowStart="0", windowEnd="1"):
                assert await g5_trace.threaded("value", success, executor=executor if explicit else None) is sentinel
                with pytest.raises(ValueError) as caught:
                    await g5_trace.threaded("failure", fail, executor=executor if explicit else None)
                assert caught.value is problem
            assert bool(rows(caplog)) is (enabled == "1")
            assert "PRIVATE_SECRET_VALUE" not in caplog.text
            assert g5_trace._context.get() is None
    assert calls == ["value", "error", "value", "error"]


@pytest.mark.asyncio
async def test_independent_parallel_task_loop_window_scope_real_executor(monkeypatch, caplog):
    monkeypatch.setenv("CLPM_G5_TRACE", "1")
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    with ThreadPoolExecutor(max_workers=2) as executor:
        async def one(i):
            task, loop, window = f"T{i}", f"L{i}", f"W{i}"
            with g5_trace.span("independent.root", taskId=task, loopId=loop, windowStart=window, windowEnd=window + "END"):
                def work():
                    state = g5_trace._context.get()
                    assert (state["taskId"], state["loopId"], state["windowStart"]) == (task, loop, window)
                    return i
                return await g5_trace.threaded("independent.job", work, executor=executor)
        assert await asyncio.gather(*(one(i) for i in range(8))) == list(range(8))
    records = rows(caplog)
    jobs = [r for r in records if r["stage"] == "independent.job"]
    assert len({r["spanId"] for r in records}) == len(records)
    for job in jobs:
        i = int(job["loopId"][1:])
        assert job["taskId"] == f"T{i}" and job["windowStart"] == f"W{i}"
        assert job["submitMonoNs"] <= job["threadStartMonoNs"] <= job["threadEndMonoNs"] <= job["awaitReturnMonoNs"]
        execute = next(r for r in records if r["stage"] == "independent.job.execute" and r["loopId"] == job["loopId"])
        assert execute["parentSpanId"] == job["spanId"]
        root = next(r for r in records if r["stage"] == "independent.root" and r["loopId"] == job["loopId"])
        assert job["parentSpanId"] == root["spanId"]
    assert g5_trace._context.get() is None


@pytest.mark.asyncio
async def test_queued_cancel_has_no_thread_completion(monkeypatch, caplog):
    monkeypatch.setenv("CLPM_G5_TRACE", "1")
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    blocking, entered = threading.Event(), threading.Event()
    calls = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        def occupy():
            entered.set()
            blocking.wait(timeout=2)
        executor.submit(occupy)
        assert entered.wait(timeout=1)
        with g5_trace.span("root", taskId="cancel-queued", loopId="L", windowStart="0", windowEnd="1"):
            pending = asyncio.create_task(g5_trace.threaded("queued", lambda: calls.append("unexpected"), executor=executor))
            await asyncio.sleep(0.01)
            pending.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await pending
                row = next(r for r in rows(caplog) if r["stage"] == "queued")
                assert row["outcome"] == "CancelledError"
                assert row["executionMs"] is row["resumeMs"] is row["queueMs"] is None
                assert "threadStartMonoNs" not in row and "threadEndMonoNs" not in row
            finally:
                blocking.set()
    assert calls == []


@pytest.mark.asyncio
async def test_running_cancel_preserves_late_thread_scope_and_no_fake_finish(monkeypatch, caplog):
    monkeypatch.setenv("CLPM_G5_TRACE", "1")
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        def running():
            entered.set()
            release.wait(timeout=2)
            assert g5_trace._context.get()["taskId"] == "cancel-running"
            done.set()
            return 7
        with g5_trace.span("root", taskId="cancel-running", loopId="LC", windowStart="0", windowEnd="1"):
            pending = asyncio.create_task(g5_trace.threaded("running", running, executor=executor))
            while not entered.is_set():
                await asyncio.sleep(0)
            pending.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await pending
                row = next(r for r in rows(caplog) if r["stage"] == "running")
                assert row["outcome"] == "CancelledError"
                assert row["executionMs"] is row["resumeMs"] is None
                assert "threadEndMonoNs" not in row
            finally:
                release.set()
    assert done.is_set()
    execution = next(r for r in rows(caplog) if r["stage"] == "running.execute")
    assert execution["taskId"] == "cancel-running" and execution["loopId"] == "LC"
    assert execution["outcome"] == "ok"
    assert g5_trace._context.get() is None


@pytest.mark.asyncio
async def test_logger_failure_preserves_business_exception_and_debug_json(monkeypatch):
    from app.core.logging import _DebugFormatter
    monkeypatch.setenv("CLPM_G5_TRACE", "1")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_DebugFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logger = g5_trace.logger
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        @g5_trace.traced("decorated")
        async def decorated(loop_id, logical_run_id, start, end, secret=None):
            return 42
        assert await decorated("LD", "TD", "S", "E", secret="DO_NOT_LOG_SECRET") == 42
        body = json.loads(stream.getvalue().split("G5_TRACE ", 1)[1])
        assert (body["taskId"], body["loopId"], body["windowStart"], body["windowEnd"]) == ("TD", "LD", "S", "E")
        assert "DO_NOT_LOG_SECRET" not in stream.getvalue()
    finally:
        logger.removeHandler(handler)
    problem = RuntimeError("PRIVATE_EXCEPTION_TEXT")
    monkeypatch.setattr(logger, "info", lambda *a, **kw: (_ for _ in ()).throw(OSError("LOGGER_BROKEN")))
    with pytest.raises(RuntimeError) as caught:
        with g5_trace.span("logging_failure"):
            raise problem
    assert caught.value is problem and g5_trace._context.get() is None


@pytest.mark.asyncio
async def test_td_native_off_on_same_rows_errors_calls_no_sql_log(monkeypatch, caplog):
    from app.core import tdengine_native as td
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    counts = Counter()
    issue = ValueError("PRIVATE_TD_ERROR")
    fail_mode = False
    class Cursor:
        description = [("value", "DOUBLE", 8)]
        def execute(self, sql):
            counts["execute"] += 1
            assert sql == "SELECT PRIVATE_SQL_TOKEN"
            if fail_mode:
                raise issue
        def fetchall(self):
            counts["fetch"] += 1
            return [(42,)]
        def close(self):
            counts["close"] += 1
    @contextmanager
    def connection():
        counts["connection"] += 1
        yield SimpleNamespace(cursor=lambda: Cursor())
    monkeypatch.setattr(td.TDengineConnectionPool, "get_connection", connection)
    for switch in ("0", "1"):
        monkeypatch.setenv("CLPM_G5_TRACE", switch)
        before = counts.copy()
        assert await td.execute_native("SELECT PRIVATE_SQL_TOKEN") == [{"value": 42}]
        assert counts - before == Counter(connection=1, execute=1, fetch=1, close=1)
    fail_mode = True
    for switch in ("0", "1"):
        monkeypatch.setenv("CLPM_G5_TRACE", switch)
        with pytest.raises(ValueError) as caught:
            await td.execute_native("SELECT PRIVATE_SQL_TOKEN")
        assert caught.value is issue
    assert "PRIVATE_SQL_TOKEN" not in caplog.text and "PRIVATE_TD_ERROR" not in caplog.text
    assert counts == Counter(connection=4, execute=4, fetch=2, close=4)


@pytest.mark.asyncio
async def test_builder_two_point_gather_equivalence_and_count(monkeypatch, caplog):
    from app.services.data_source import logical_wide_builder as lwb
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    begin = datetime(2026, 10, 14, tzinfo=UTC)
    counts = Counter()
    async def roles(db, loop_id, role_names, start, end):
        counts["roles"] += 1
        return {r.upper(): [SimpleNamespace(point_id=r, start=start, end=end + timedelta(seconds=1), initial_check=start)] for r in role_names}
    async def events(pids, start, end, **kwargs):
        counts["events"] += 1
        return {p: [{"ts": begin + timedelta(seconds=s), "value": 50 + s * 0.1, "quality_class": 1} for s in range(4)] for p in pids}
    async def initial(*args, **kwargs):
        counts["initial"] += 1
        return {}
    async def anchors(*args, **kwargs):
        counts["anchors"] += 1
        return {}
    monkeypatch.setattr(lwb, "_resolve_role_segments", roles)
    monkeypatch.setattr(lwb.repo, "read_events", events)
    monkeypatch.setattr(lwb.repo, "read_last_states_before", initial)
    monkeypatch.setattr(lwb.meta, "applicable_anchors_batch", anchors)
    monkeypatch.setattr(lwb, "_load_gap_windows", AsyncMock(return_value=[(begin + timedelta(seconds=1), begin + timedelta(seconds=2))]))
    monkeypatch.setattr(lwb, "_held_limits_s", AsyncMock(return_value={"fast": 60, "slow": 7200}))
    outputs = []
    for switch in ("0", "1"):
        monkeypatch.setenv("CLPM_G5_TRACE", switch)
        before = counts.copy()
        with g5_trace.span("root", taskId="TB", loopId="LB", windowStart=begin, windowEnd=begin + timedelta(seconds=3)):
            outputs.append(await lwb.build_logical_wide(None, "LB", ["pv", "op"], begin, begin + timedelta(seconds=3), 1))
        assert counts - before == Counter(roles=1, events=2, initial=1, anchors=1)
    assert outputs[0] == outputs[1]
    measured = [r for r in rows(caplog) if r["stage"] == "builder.events"]
    assert len(measured) == 2 and {r["pointId"] for r in measured} == {"pv", "op"}
    assert all(r["taskId"] == "TB" and r["loopId"] == "LB" for r in measured)


@pytest.mark.asyncio
async def test_real_preprocessing_same_data_query_count_off_on(monkeypatch, caplog):
    from app.contracts.data_types import ControlType, LoopPreprocessConfig, RawTimeSeries, TagGroup, TimeWindow
    from app.services.data_planner import DataPlanner
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    begin = datetime(2026, 10, 14)
    raw = RawTimeSeries(timestamps=[begin + timedelta(seconds=i) for i in range(20)], signals={"pv": [50 + i * .02 for i in range(20)], "sp": [50] * 20, "op": [10] * 20, "mode": [1] * 20}, quality_codes={"pv_quality": [1] * 20})
    query = AsyncMock(return_value=raw)
    planner = DataPlanner(cache=None, tdengine_query_fn=query, assembler=object(), db=None)
    task = SimpleNamespace(tag_group=TagGroup.BASE, tag_roles=["pv", "sp", "op", "mode"], interval_s=1)
    config = LoopPreprocessConfig(loop_id="LP", control_type=ControlType.FLOW, range_min=0, range_max=100)
    window = TimeWindow(begin, begin + timedelta(seconds=19))
    outputs = []
    for switch in ("0", "1"):
        monkeypatch.setenv("CLPM_G5_TRACE", switch)
        with g5_trace.span("root", taskId="TP", loopId="LP", windowStart=begin, windowEnd=window.end):
            outputs.append(await planner._query_and_preprocess("LP", task, window, config))
    assert outputs[0] == outputs[1] and query.await_count == 2
    measured = [r for r in rows(caplog) if r["stage"] == "preprocess.executor"]
    assert len(measured) == 1 and measured[0]["taskId"] == "TP"


@pytest.mark.asyncio
@pytest.mark.parametrize("body_fails", [False, True])
async def test_batch_commit_close_and_semaphore_boundaries(monkeypatch, caplog, body_fails):
    from app.core import db as db_core
    from app.tasks import kpi_calc as kpi
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    calls = []
    issue = ValueError("PRIVATE_KPI_BODY")
    class Session:
        async def commit(self):
            calls.append("commit")
        async def rollback(self):
            calls.append("rollback")
    async def close(*args, **kwargs):
        calls.append("close")
    @g5_trace.traced("independent.kpi_body")
    async def body(db, loop, ts_start, ts_end, logical_run_id=None, **kwargs):
        calls.append("body")
        await asyncio.sleep(0.005)
        if body_fails:
            raise issue
        return {"loopId": str(loop.id), "status": "SUCCESS"}
    monkeypatch.setattr(db_core, "AsyncSessionLocal", Session)
    monkeypatch.setattr(db_core, "close_session_guarded", close)
    monkeypatch.setattr(kpi, "_build_data_planner", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(kpi, "_get_shared_bundle_cache", lambda: None)
    monkeypatch.setattr(kpi, "_calculate_loop_kpi", body)
    start = datetime(2026, 10, 14)
    loop_list = [SimpleNamespace(id="L1", tag_name="tag1"), SimpleNamespace(id="L2", tag_name="tag2")]
    observed = []
    for switch in ("0", "1"):
        monkeypatch.setenv("CLPM_G5_TRACE", switch)
        before = len(calls)
        results = await kpi._run_batch_loop_calculations(loops=loop_list, loop_configs={}, metric_configs={}, ts_start=start, ts_end=start + timedelta(hours=1), type_weights={}, logical_run_id="TASK", concurrency=1)
        observed.append(calls[before:])
        if body_fails:
            assert all(result is issue for result in results)
        else:
            assert [r["loopId"] for r in results] == ["L1", "L2"]
    assert observed[0] == observed[1] == ["body", "rollback" if body_fails else "commit", "close"] * 2
    recorded = rows(caplog)
    closes = [r for r in recorded if r["stage"] == "pg.close"]
    waits = [r for r in recorded if r["stage"] == "kpi.semaphore_wait"]
    assert len(closes) == len(waits) == 2
    assert all(r["taskId"] == "TASK" and r["loopId"] in {"L1", "L2"} for r in closes + waits)
    if not body_fails:
        commits = [r for r in recorded if r["stage"] == "pg.commit"]
        assert len(commits) == 2
        assert max(r["elapsedMs"] for r in waits) >= 1
    assert "PRIVATE_KPI_BODY" not in caplog.text


@pytest.mark.asyncio
async def test_persist_same_three_sql_calls_and_separate_ledger_latest(monkeypatch, caplog):
    from app.tasks import kpi_calc as kpi
    caplog.set_level(logging.INFO, logger="app.core.g5_trace")
    execute_result = MagicMock()
    execute_result.first.return_value = ("snapshot",)
    execute_result.scalar_one_or_none.return_value = None
    db = SimpleNamespace(execute=AsyncMock(return_value=execute_result))
    ledger = AsyncMock(return_value=SimpleNamespace(id="record"))
    monkeypatch.setattr(kpi.result_ledger, "append_result_record", ledger)
    start = datetime(2026, 10, 14)
    outputs = []
    for switch in ("0", "1"):
        monkeypatch.setenv("CLPM_G5_TRACE", switch)
        before = db.execute.await_count
        outputs.append(await kpi._persist_snapshot(db, "L", start, start + timedelta(hours=1), "SUCCESS", logical_run_id="persist-parent"))
        assert db.execute.await_count - before == 3
    assert outputs[0] == outputs[1] and ledger.await_count == 2
    stages = [r["stage"] for r in rows(caplog)]
    assert stages.count("pg.persist_total") == stages.count("pg.ledger") == stages.count("pg.hourly_snapshot") == stages.count("pg.latest_projection") == 1
    assert "pg.commit" not in stages


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
        row = next(r for r in rows(caplog) if r["stage"] == "cancel_race")
        assert row["outcome"] == "CancelledError"
        assert row["threadEndMonoNs"] > row["awaitReturnMonoNs"]
        assert row["resumeMs"] is None, row
