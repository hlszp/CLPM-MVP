"""导入真实编排路径回归；所有外部 IO 隔离，验证并发、资源及完成屏障。"""

import asyncio
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.services import data_import as di
from app.services.cache.invalidation import CacheInvalidator
from app.services.data_source.remote_api_provider import RemoteApiProvider
from tests.test_services.test_data_import import _FakeRedisImport

START = datetime(2026, 10, 1)
POINT = str(UUID(int=1))


@pytest.fixture
def chunk_io():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    batches = []

    async def create(*args, **kwargs):
        batch = SimpleNamespace(batch_id=f"b{len(batches)}")
        batches.append(batch)
        await asyncio.sleep(0)
        return batch

    async def write_points(*args, **kwargs):
        kwargs["on_point_complete"](POINT)
        return 1

    with ExitStack() as stack:
        stack.enter_context(patch("app.core.db.AsyncSessionLocal", return_value=session))
        stack.enter_context(
            patch(
                "app.services.data_source.point_history_metadata.create_batch", side_effect=create
            )
        )
        confirm = stack.enter_context(
            patch("app.services.data_source.point_history_metadata.confirm_batch", new=AsyncMock())
        )
        fetch = stack.enter_context(
            patch.object(
                di,
                "_fetch_remote_history",
                new=AsyncMock(
                    return_value=(
                        ["2026-10-01T00:00:00"],
                        {"PV": {"values": [1.0], "qualities": [192]}},
                    )
                ),
            )
        )
        write = stack.enter_context(
            patch.object(di, "_write_points_bulk", new=AsyncMock(side_effect=write_points))
        )
        yield SimpleNamespace(
            session=session, batches=batches, confirm=confirm, fetch=fetch, write=write
        )


async def run_chunks(**kwargs):
    return await di._import_single_loop(
        "loop-1",
        START,
        START + timedelta(hours=4),
        1,
        role_tag_map={"PV": "PV"},
        role_point_map={"PV": ("PV", POINT)},
        chunk_hours=1,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_parallel_chunks_create_one_batch_and_register_after_write(chunk_io):
    count, failed, cancelled = await run_chunks(chunk_concurrency=4)
    assert (count, failed, cancelled) == (4, [], False)
    assert len(chunk_io.batches) == 1
    assert chunk_io.confirm.await_count == 1
    # 覆盖登记按批 flush，而非每个分块单独一次数据库往返。
    assert chunk_io.session.flush.await_count <= 1


@pytest.mark.asyncio
async def test_failed_write_has_no_confirmed_coverage(chunk_io):
    chunk_io.write.side_effect = RuntimeError("TD unavailable")
    count, failed, _ = await run_chunks()
    assert count == 0 and len(failed) == 4
    assert chunk_io.session.add.call_count == 0
    chunk_io.session.add_all.assert_not_called()
    chunk_io.confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_after_fetch_prevents_write(chunk_io):
    cancelled = False

    async def fetch(*args):
        nonlocal cancelled
        cancelled = True
        return ["2026-10-01T00:00:00"], {"PV": {"values": [1.0]}}

    chunk_io.fetch.side_effect = fetch
    with patch.object(di, "_is_task_cancelled", side_effect=lambda _: cancelled):
        count, failed, stopped = await run_chunks(task_id="t")
    assert (count, failed, stopped) == (0, [], True)
    chunk_io.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_config_session_closed_after_last_query():
    events = []
    session = MagicMock()

    async def execute(*args):
        events.append("query")
        return SimpleNamespace(all=lambda: [])

    async def close():
        events.append("close")

    session.execute = execute
    session.close = close
    with (
        patch("app.core.db.AsyncSessionLocal", return_value=session),
        patch.object(di, "_probe_remote_history_api", new=AsyncMock()),
        patch.object(di, "_batch_get_loop_data", new=AsyncMock(return_value={})),
        patch.object(di, "_get_remote_guard", return_value=MagicMock()),
    ):
        await di.import_history_data(
            ["missing"], START.isoformat(), (START + timedelta(hours=1)).isoformat()
        )
    assert events == ["query", "close"]


@pytest.mark.asyncio
async def test_remote_limit_shrink_preserves_inflight_accounting():
    provider = RemoteApiProvider()
    provider.ensure_remote_concurrency(3)
    limiter = provider._get_semaphore()
    holders = [asyncio.Event() for _ in range(3)]
    entered = []

    async def hold(i):
        async with provider._get_semaphore():
            entered.append(i)
            await holders[i].wait()

    tasks = [asyncio.create_task(hold(i)) for i in range(3)]
    await asyncio.sleep(0)
    assert entered == [0, 1, 2]
    provider.ensure_remote_concurrency(1)
    assert provider._get_semaphore() is limiter
    waiter = asyncio.create_task(limiter.__aenter__())
    for i in range(2):
        holders[i].set()
        await tasks[i]
        assert not waiter.done()
    holders[2].set()
    await tasks[2]
    await asyncio.wait_for(waiter, 1)
    await limiter.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_remote_limiter_cancelled_waiter_does_not_leak_capacity():
    provider = RemoteApiProvider()
    provider.ensure_remote_concurrency(1)
    limiter = provider._get_semaphore()
    async with limiter:
        waiting = asyncio.create_task(limiter.__aenter__())
        await asyncio.sleep(0)
        assert not waiting.done()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    async with asyncio.timeout(1):
        async with limiter:
            pass


@pytest.mark.asyncio
async def test_batch_invalidation_scans_once_and_keeps_other_loops():
    redis = SimpleNamespace(
        scan=AsyncMock(
            side_effect=[
                (1, ["pdb:a:x", "pdb_l2:b:x", "pdb_l3:c:x"]),
                (0, ["pdb_l3:a:x", "unrelated:a:x"]),
            ]
        ),
        delete=AsyncMock(return_value=1),
    )
    await CacheInvalidator(redis).invalidate_loops({"a", "b"})
    assert redis.scan.await_count == 2
    deleted = {key for call in redis.delete.call_args_list for key in call.args}
    assert deleted == {"pdb:a:x", "pdb_l2:b:x", "pdb_l3:a:x"}


@pytest.mark.asyncio
async def test_progress_waits_for_cleanup_and_mixed_result_is_not_success():
    fake = _FakeRedisImport()
    task_id = await _create_task(fake)
    session = MagicMock()
    session.close = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    loops = {x: {"role_tag_map": {"PV": x}, "unit_id": "", "subtable": ""} for x in ("a", "b")}
    observed = []

    async def single(loop_id, on_chunk_complete, **kwargs):
        await on_chunk_complete()
        return (
            (1, [], False)
            if loop_id == "a"
            else (0, [{"start": "s", "end": "e", "error": "failed"}], False)
        )

    async def cleanup(*args):
        data = await di.get_import_task(task_id)
        observed.append((data["status"], data["progress"]))

    with (
        patch.object(di, "redis_client", fake),
        patch("app.core.db.AsyncSessionLocal", return_value=session),
        patch.object(di, "_probe_remote_history_api", new=AsyncMock()),
        patch.object(di, "_get_remote_guard", return_value=MagicMock()),
        patch.object(di, "_batch_get_loop_data", new=AsyncMock(return_value=loops)),
        patch.object(di, "_import_single_loop", side_effect=single),
        patch.object(di, "_invalidate_import_caches", side_effect=cleanup),
    ):
        result = await di.import_history_data(
            ["a", "b"], START.isoformat(), (START + timedelta(hours=1)).isoformat(), task_id=task_id
        )
        task = await di.get_import_task(task_id)
    assert result["succeeded"] == 1 and result["failed"] == 1
    assert observed and all(status == "RUNNING" and progress < 1 for status, progress in observed)
    assert task["status"] == "FAILED"
    assert task["progress"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cache", "backfill"])
async def test_finalization_failure_is_visible_and_never_success(failure):
    fake = _FakeRedisImport()
    task_id = await _create_task(fake)
    session = MagicMock(close=AsyncMock())
    backfill = AsyncMock(side_effect=RuntimeError("queue down") if failure == "backfill" else None)
    cleanup = AsyncMock(side_effect=RuntimeError("cache down") if failure == "cache" else None)
    with (
        patch.object(di, "redis_client", fake),
        patch("app.core.db.AsyncSessionLocal", return_value=session),
        patch.object(di, "_read_import_concurrency", new=AsyncMock(return_value=(4, 1, 4))),
        patch.object(di, "_probe_remote_history_api", new=AsyncMock()),
        patch.object(di, "_get_remote_guard", return_value=MagicMock()),
        patch.object(
            di,
            "_batch_get_loop_data",
            new=AsyncMock(
                return_value={"a": {"role_tag_map": {"PV": "a"}, "unit_id": "", "subtable": ""}}
            ),
        ),
        patch.object(di, "_import_single_loop", new=AsyncMock(return_value=(1, [], False))),
        patch.object(di, "_invalidate_import_caches", cleanup),
        patch.object(di, "_trigger_kpi_backfill", backfill),
    ):
        await di.import_history_data(
            ["a"],
            START.isoformat(),
            (START + timedelta(hours=1)).isoformat(),
            trigger_backfill=True,
            task_id=task_id,
        )
        task = await di.get_import_task(task_id)
    assert task["status"] == "FAILED"
    assert ("缓存刷新失败" if failure == "cache" else "提交 KPI 回算失败") in task["errorMessage"]
    assert backfill.await_count == (0 if failure == "cache" else 1)


async def _create_task(fake):
    with patch.object(di, "redis_client", fake):
        return await di.create_import_task(
            loop_ids=["a", "b"],
            ts_start=START.isoformat(),
            ts_end=(START + timedelta(hours=1)).isoformat(),
            trigger_backfill=False,
            created_by="test",
            celery_task_id="test",
        )


@pytest.mark.asyncio
async def test_coverage_uses_actual_points_and_utc_windows(chunk_io):
    await run_chunks()
    segments = chunk_io.session.add_all.call_args.args[0]
    assert len(segments) == 4
    assert {s.point_id for s in segments} == {POINT}
    assert min(s.seg_start for s in segments) == datetime(2026, 9, 30, 16, tzinfo=UTC)
    assert max(s.seg_end for s in segments) == datetime(2026, 9, 30, 20, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [True, False])
async def test_partial_write_counts_survive_failure_or_cancel(chunk_io, cancelled):
    async def partial(*args, **kwargs):
        await kwargs["on_batch_written"](5000)
        raise di._ImportCancelled() if cancelled else RuntimeError("write failed")

    chunk_io.write.side_effect = partial
    count, failed, stopped = await run_chunks()
    assert count == 20_000
    assert stopped == cancelled
    assert len(failed) == (0 if cancelled else 4)
    chunk_io.session.add_all.assert_not_called()
    chunk_io.confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_failure_waits_for_other_chunks(chunk_io):
    completed = []

    async def write(*args, **kwargs):
        await asyncio.sleep(0.01)
        completed.append(1)
        return 1

    chunk_io.write.side_effect = write
    count, failed, _ = await run_chunks(
        chunk_concurrency=4, on_chunk_complete=AsyncMock(side_effect=RuntimeError("progress down"))
    )
    assert len(completed) == count == len(failed) == 4


@pytest.mark.asyncio
async def test_writer_skips_empty_points_and_stops_before_next_sql():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    timestamps = [(start + timedelta(seconds=i)).isoformat() for i in range(5001)]
    raw = (timestamps, {"PV": {"values": list(range(5001)), "qualities": [192] * 5001}})
    written, complete = [], []
    cancelled = False

    async def before():
        if cancelled:
            raise di._ImportCancelled()

    async def after(n):
        nonlocal cancelled
        written.append(n)
        cancelled = True

    sink = AsyncMock()
    with patch("app.core.tdengine_native.execute_native_effective", sink):
        with pytest.raises(di._ImportCancelled):
            await di._write_points_bulk(
                {"PV": ("PV", POINT), "SP": ("empty", str(UUID(int=2)))},
                raw,
                source_task="test",
                before_batch=before,
                on_batch_written=after,
                on_point_complete=complete.append,
            )
        assert written == [5000] and sink.await_count == 1 and complete == []
        await di._write_points_bulk(
            {"PV": ("PV", POINT), "SP": ("empty", str(UUID(int=2)))},
            raw,
            source_task="test",
            on_point_complete=complete.append,
        )
    assert complete == [POINT]


@pytest.mark.asyncio
async def test_cancel_is_cooperative_and_late_progress_cannot_revive_task():
    fake = _FakeRedisImport()
    task_id = await _create_task(fake)
    with (
        patch.object(di, "redis_client", fake),
        patch("app.tasks.celery_app.celery_app.control.revoke") as revoke,
    ):
        await di._update_task(task_id, progress=0.2, current_stage="写入")
        result = await di.cancel_import_task(task_id)
        revoke.assert_called_once_with("test", terminate=False)
        await di._update_task(task_id, progress=0.8, current_stage="迟到的进度")
        task = await di.get_import_task(task_id)
        assert await di.delete_import_task(task_id)
        assert await di.get_import_task(task_id) is None
        assert await di._is_task_cancelled(task_id)
    assert result["status"] == task["status"] == "CANCELLED"
    assert task["progress"] == 0.2
    assert task["currentStage"] != "迟到的进度"


@pytest.mark.asyncio
async def test_pagination_pipelines_metadata_and_only_reads_page_details():
    class Redis:
        def __init__(self):
            self.ids = [str(i) for i in range(1000)]
            self.expired = {"2", "4"}
            self.details = []
            self.executions = 0

        async def zrevrange(self, *args):
            return self.ids.copy()

        async def zrem(self, key, *ids):
            self.ids = [i for i in self.ids if i not in ids]

        def pipeline(self):
            owner = self

            class Pipe:
                def __init__(self):
                    self.ops = []

                def exists(self, key):
                    self.ops.append(("exists", key.split(":")[-1]))

                def hgetall(self, key):
                    self.ops.append(("get", key.split(":")[-1]))

                async def execute(self):
                    owner.executions += 1
                    result = []
                    for op, tid in self.ops:
                        if op == "exists":
                            result.append(tid not in owner.expired)
                        else:
                            owner.details.append(tid)
                            result.append({"task_id": tid, "status": "SUCCESS"})
                    return result

            return Pipe()

    fake = Redis()
    with patch.object(di, "redis_client", fake):
        result = await di.list_import_tasks(page=2, page_size=10)
    assert result["total"] == 998
    assert [t["taskId"] for t in result["items"]] == [str(i) for i in range(12, 22)]
    assert len(fake.details) == 10
    assert fake.executions == 3
