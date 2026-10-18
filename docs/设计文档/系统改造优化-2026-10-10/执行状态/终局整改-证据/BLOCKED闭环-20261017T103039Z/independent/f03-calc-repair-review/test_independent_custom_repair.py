"""Non-author contracts, isolated from services and all business records."""

from __future__ import annotations

import asyncio
import ast
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.api.v1.endpoints import tasks
from app.schemas.task import CustomTaskCreate
from app.services import calc_snapshot, data_planner, task_tracker
from app.tasks import kpi_calc
from tests.conftest import TEST_USERS
from tests.test_api_tasks import FakeTaskRedis, _CUSTOM_BODY
from tests.test_calc_snapshot import _make_kpi_bundles


@pytest.fixture
def store(monkeypatch):
    redis = FakeTaskRedis()
    redis.exists = AsyncMock(side_effect=lambda key: int(key in redis._hashes))
    monkeypatch.setattr(tasks, "redis_client", redis)
    monkeypatch.setattr(task_tracker, "redis_client", redis)
    return redis


@pytest.mark.parametrize("source", ["LOOP_FIELD", "TAG_RANGE", "DEFAULT_0_100"])
def test_actual_metadata_producer_roundtrip_without_pickle(source):
    bundles = _make_kpi_bundles(n_points=7)[:1]
    tree = ast.parse(Path(data_planner.__file__).read_bytes())
    producer = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_fill_limit_metadata")
    function = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), producer], type_ignores=[])
    ast.fix_missing_locations(function)
    namespace = {}
    exec(compile(function, data_planner.__file__, "exec"), namespace)
    namespace["_fill_limit_metadata"](bundles, source)
    original = {name: list(values) for name, values in bundles[0].data_block.signals.items()}
    arrays, meta = calc_snapshot.serialize_data_blocks(bundles)
    assert meta[0]["signalMetadata"]["op_limits_source"] == [source]
    before_hash = calc_snapshot.compute_input_hash({"blocks": meta}, arrays)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    with np.load(io.BytesIO(buffer.getvalue()), allow_pickle=False) as archive:
        assert all(archive[key].dtype != object for key in archive.files)
        restored, _ = calc_snapshot.deserialize_data_blocks(dict(archive), meta)
    assert restored[0].data_block.signals == original
    restored[0].data_block.signals["op_limits_provenance"] = [None]
    _, changed = calc_snapshot.serialize_data_blocks(restored)
    assert calc_snapshot.compute_input_hash({"blocks": changed}, arrays) != before_hash


def test_null_scalar_metadata_and_numeric_null_are_distinct_and_preserved():
    bundles = _make_kpi_bundles(n_points=7)[:1]
    signals = bundles[0].data_block.signals
    signals["op_limits_source"] = [None]
    signals["op_limits_provenance"] = [None, "ACTUAL"]
    signals["pv"][2] = None
    arrays, meta = calc_snapshot.serialize_data_blocks(bundles)
    assert arrays["blk0__null_pv"][2]
    restored, _ = calc_snapshot.deserialize_data_blocks(arrays, meta)
    assert restored[0].data_block.signals == signals


def test_legacy_numeric_package_and_invalid_numeric_signal_have_original_contract():
    bundles = _make_kpi_bundles(n_points=7)[:1]
    arrays, meta = calc_snapshot.serialize_data_blocks(bundles)
    assert "signalMetadata" not in meta[0]
    restored, _ = calc_snapshot.deserialize_data_blocks(arrays, meta)
    assert restored[0].data_block.signals == bundles[0].data_block.signals
    bundles[0].data_block.signals["pv"][0] = "LOOP_FIELD"
    with pytest.raises(ValueError, match="LOOP_FIELD"):
        calc_snapshot.serialize_data_blocks(bundles)


@pytest.mark.parametrize("metadata", [{"pv": ["x"]}, {"op_limits_source": [0]}, {"unknown_text": ["x"]}])
def test_text_escape_hatch_does_not_admit_numeric_or_unknown_names(metadata):
    arrays, blocks = calc_snapshot.serialize_data_blocks(_make_kpi_bundles(n_points=7)[:1])
    blocks[0]["signalMetadata"] = metadata
    with pytest.raises(ValueError):
        calc_snapshot.deserialize_data_blocks(arrays, blocks)


def configure_actual_batch(monkeypatch, store, statuses):
    task_id = "independent-custom-progress"
    loops = [SimpleNamespace(id=f"independent-loop-{i}", tag_name=f"SYNTH-{i}") for i in range(len(statuses))]
    outer = AsyncMock()
    outer.__aenter__.return_value = outer
    loop_rows, metric_rows = MagicMock(), MagicMock()
    loop_rows.scalars.return_value.all.return_value = loops
    metric_rows.scalars.return_value.all.return_value = []
    outer.execute.side_effect = [loop_rows, metric_rows]
    worker_sessions = []
    factory_calls = 0
    events = []

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            return outer
        session = AsyncMock()
        index = len(worker_sessions)

        async def commit():
            events.append(("commit", index))

        session.commit.side_effect = commit
        worker_sessions.append(session)
        return session

    monkeypatch.setattr("app.core.db.AsyncSessionLocal", factory)
    monkeypatch.setattr("app.core.db.close_session_guarded", AsyncMock())
    monkeypatch.setattr(data_planner, "resolve_data_version", AsyncMock(return_value="independent-input"))
    monkeypatch.setattr("app.services.loop_config.get_loop_type_weights_map", AsyncMock(return_value={}))
    for name, result in {
        "_batch_load_loop_configs": {}, "_pin_task_config": None,
        "_resolve_task_release_context": None, "_load_batch_runtime_overrides": {},
        "_batch_load_template_keys": {},
        "_prewarm_cache_for_loops": {"total": len(loops), "succeeded": len(loops), "failed": 0},
    }.items():
        monkeypatch.setattr(kpi_calc, name, AsyncMock(return_value=result))
    monkeypatch.setattr(kpi_calc.result_ledger, "loop_ids_with_failed_records", AsyncMock(return_value=set()))
    monkeypatch.setattr(kpi_calc, "_get_shared_bundle_cache", lambda: False)
    monkeypatch.setattr(kpi_calc, "_build_data_planner", MagicMock())

    async def calculate(**options):
        index = loops.index(options["loop"])
        await asyncio.sleep(0)
        return {"status": statuses[index]}

    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calculate)
    store._hashes[f"task:{task_id}"] = {
        "task_id": task_id, "task_type": "CUSTOM", "status": "PENDING",
        "progress": "0", "loops_total": str(len(loops)), "loops_done": "0",
        "current_stage": "取数", "celery_task_id": "independent-celery",
        "created_by_id": "independent-user",
    }
    return task_id, loops, events, worker_sessions


async def test_actual_worker_commits_and_processed_count_do_not_reclassify_inconclusive(monkeypatch, store):
    task_id, loops, commits, sessions = configure_actual_batch(monkeypatch, store, ["SUCCESS", "INCONCLUSIVE"])
    original_update = task_tracker.update_status
    progress = []

    async def observe_update(*args, **kwargs):
        if "loops_done" in kwargs:
            done = kwargs["loops_done"]
            assert len(commits) >= done, "progress advanced before actual unit DB commit"
            progress.append(done)
        return await original_update(*args, **kwargs)

    monkeypatch.setattr(task_tracker, "update_status", observe_update)
    result = await kpi_calc._do_calculate_custom_batch(task_id, [loop.id for loop in loops], "2026-10-17T11:00:00", "2026-10-17T12:00:00")
    assert (result["total"], result["success"], result["inconclusive"], result["failed"]) == (2, 1, 1, 0)
    assert len(commits) == len(sessions) == 2
    assert progress == [1, 2, 2]
    record = await task_tracker.get_task(task_id)
    assert (record["loops_done"], record["loops_total"], record["progress"], record["current_stage"]) == ("2", "2", "1.0", "完成")
    monkeypatch.setattr("celery.result.AsyncResult", lambda *args, **kwargs: SimpleNamespace(state="SUCCESS"))
    await tasks._sync_task_status(task_id, record)
    assert record["loops_done"] == "2" and record["status"] == "SUCCESS"


async def test_retry_drops_previous_attempt_count_instead_of_monotonic_carry(monkeypatch, store):
    task_id, loops, _, _ = configure_actual_batch(monkeypatch, store, ["INCONCLUSIVE"])
    store._hashes[f"task:{task_id}"].update(loops_done="9", progress="1.0", work_items_done="9")

    async def attempt(run, **kwargs):
        current = await task_tracker.get_task(task_id)
        assert current["loops_done"] == current["progress"] == "0"
        return await run()

    monkeypatch.setattr(kpi_calc, "_attempt_log_wrap", attempt)
    result = await kpi_calc._do_calculate_custom_batch(task_id, [loops[0].id], "2026-10-17T11:00:00", "2026-10-17T12:00:00", attempt_no=2)
    assert result["inconclusive"] == 1 and result["success"] == 0
    record = await task_tracker.get_task(task_id)
    assert record["loops_done"] == "1" and record["work_items_done"] == "1"


def custom_db():
    session = AsyncMock()
    rows = MagicMock()
    rows.all.return_value = []
    session.execute.return_value = rows
    return session


async def test_publish_sees_complete_task_and_index_before_fast_consumer(monkeypatch, store):
    published = []

    def consumer(*, args, priority, task_id=None):
        assert f"task:{args[0]}" in store._hashes, "publish occurred before task persistence"
        initial = store._hashes[f"task:{args[0]}"]
        assert store._zsets["task:index"][args[0]] > 0
        assert task_id is not None and task_id == initial["celery_task_id"]
        assert initial["loops_total"] == str(len(args[1]))
        published.append({"args": args, "celeryTaskId": task_id, "priority": priority})
        initial.update(loops_done="1", progress="1.0", status="SUCCESS", current_stage="完成")
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(kpi_calc.calculate_custom_batch_kpi, "apply_async", consumer)
    response = await tasks.trigger_custom_evaluation(CustomTaskCreate(**_CUSTOM_BODY), custom_db(), TEST_USERS["ic_engineer"])
    task_id = response["data"]["taskId"]
    assert len(published) == 1
    assert store._hashes[f"task:{task_id}"]["loops_done"] == "1"
    assert store._hashes[f"task:{task_id}"]["status"] == "SUCCESS"


async def test_publish_failure_retains_original_failure_and_releases_exactly_once(monkeypatch, store):
    releases = []
    original_release = tasks._release_slot_counters

    async def release(user):
        releases.append(user)
        await original_release(user)

    monkeypatch.setattr(tasks, "_release_slot_counters", release)
    monkeypatch.setattr(kpi_calc.calculate_custom_batch_kpi, "apply_async", MagicMock(side_effect=RuntimeError("independent-controlled-broker-error")))
    with pytest.raises(RuntimeError, match="independent-controlled-broker-error"):
        await tasks.trigger_custom_evaluation(CustomTaskCreate(**_CUSTOM_BODY), custom_db(), TEST_USERS["ic_engineer"])
    task_id = next(iter(store._zsets["task:index"]))
    record = await tasks._get_task(task_id)
    assert record["status"] == "FAILED" and record["finished_at"]
    assert "independent-controlled-broker-error" in record["error_message"]
    assert record["slot_released"] == "1" and len(releases) == 1
    await tasks._release_concurrency_slot(task_id, record)
    assert len(releases) == 1 and all(value == "0" for value in store._strings.values())
