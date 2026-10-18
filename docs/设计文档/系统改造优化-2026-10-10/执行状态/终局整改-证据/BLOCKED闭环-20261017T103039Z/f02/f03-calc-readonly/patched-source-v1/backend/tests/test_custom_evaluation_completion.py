"""Real F03 regressions: typed input provenance, processed counts and dispatch order.

No live PG/TD/Redis or Celery services; real orchestration/tracker/NPZ functions
run against the existing in-memory Redis and independent IO boundaries.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.api.v1.endpoints import tasks
from app.schemas.task import CustomTaskCreate
from app.services import calc_snapshot as cs
from app.services import task_tracker
from app.tasks import kpi_calc
from tests.conftest import TEST_USERS
from tests.test_api_tasks import _CUSTOM_BODY, FakeTaskRedis
from tests.test_calc_snapshot import _make_kpi_bundles


@pytest.fixture
def task_store(monkeypatch):
    store = FakeTaskRedis()
    store.exists = AsyncMock(side_effect=lambda key: int(key in store._hashes))
    monkeypatch.setattr(tasks, "redis_client", store)
    monkeypatch.setattr(task_tracker, "redis_client", store)
    return store


@pytest.mark.parametrize(
    ("source", "provenance"),
    [("LOOP_FIELD", "ACTUAL"), ("TAG_RANGE", "ACTUAL"), ("DEFAULT_0_100", "DEFAULT")],
)
def test_npz_roundtrip_keeps_limit_provenance(source, provenance):
    bundles = _make_kpi_bundles(n_points=4)[:1]
    signals = bundles[0].data_block.signals
    signals["op_limits_source"] = [source]
    signals["op_limits_provenance"] = [provenance, None]
    signals["pv"][1] = None
    arrays, metadata = cs.serialize_data_blocks(bundles)
    assert metadata[0]["signalMetadata"] == {
        "op_limits_source": [source],
        "op_limits_provenance": [provenance, None],
    }
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    with np.load(io.BytesIO(buffer.getvalue()), allow_pickle=False) as archive:
        assert all(archive[key].dtype != object for key in archive.files)
        restored, codes = cs.deserialize_data_blocks(dict(archive), metadata)
    assert restored[0].data_block.signals == signals
    assert codes == [bundles[0].metric_code]


def test_metadata_changes_input_identity_without_changing_numeric_arrays():
    bundles = _make_kpi_bundles(n_points=4)[:1]
    signals = bundles[0].data_block.signals
    signals["op_limits_source"] = ["LOOP_FIELD"]
    signals["op_limits_provenance"] = ["ACTUAL"]
    arrays1, meta1 = cs.serialize_data_blocks(bundles)
    signals["op_limits_source"] = ["TAG_RANGE"]
    arrays2, meta2 = cs.serialize_data_blocks(bundles)
    assert all(np.array_equal(arrays1[key], arrays2[key], equal_nan=True) for key in arrays1)
    assert cs.compute_input_hash({"blocks": meta1}, arrays1) != cs.compute_input_hash(
        {"blocks": meta2}, arrays2
    )


def test_old_numeric_blocks_without_new_metadata_still_replay():
    bundles = _make_kpi_bundles(n_points=4)[:1]
    arrays, metadata = cs.serialize_data_blocks(bundles)
    assert "signalMetadata" not in metadata[0]
    restored, _ = cs.deserialize_data_blocks(arrays, metadata)
    assert restored[0].data_block.signals == bundles[0].data_block.signals


def test_non_numeric_value_in_actual_numeric_signal_still_fails():
    bundles = _make_kpi_bundles(n_points=4)[:1]
    bundles[0].data_block.signals["pv"][0] = "LOOP_FIELD"
    with pytest.raises(ValueError, match="LOOP_FIELD"):
        cs.serialize_data_blocks(bundles)


def test_non_text_provenance_is_rejected_during_serialization():
    bundles = _make_kpi_bundles(n_points=4)[:1]
    bundles[0].data_block.signals["op_limits_source"] = [12]
    with pytest.raises(ValueError, match="信号元数据"):
        cs.serialize_data_blocks(bundles)


@pytest.mark.parametrize("metadata", [{"op_limits_source": [12]}, {"pv": ["LOOP_FIELD"]}])
def test_invalid_or_colliding_replay_metadata_is_rejected(metadata):
    arrays, blocks = cs.serialize_data_blocks(_make_kpi_bundles(n_points=4)[:1])
    blocks[0]["signalMetadata"] = metadata
    with pytest.raises(ValueError, match="无效信号元数据"):
        cs.deserialize_data_blocks(arrays, blocks)


def _prepare_batch(monkeypatch, task_store, statuses):
    task_id = "custom-completion-test"
    loops = [SimpleNamespace(id=f"loop-{i}", tag_name=f"SYNTH-{i}") for i in range(len(statuses))]
    db = AsyncMock()
    db.__aenter__.return_value = db
    rows, metrics = MagicMock(), MagicMock()
    rows.scalars.return_value.all.return_value = loops
    metrics.scalars.return_value.all.return_value = []
    db.execute.side_effect = [rows, metrics]
    monkeypatch.setattr("app.core.db.AsyncSessionLocal", lambda: db)
    monkeypatch.setattr("app.core.db.close_session_guarded", AsyncMock())
    monkeypatch.setattr(
        "app.services.data_planner.resolve_data_version", AsyncMock(return_value="test-input")
    )
    monkeypatch.setattr(
        "app.services.loop_config.get_loop_type_weights_map", AsyncMock(return_value={})
    )
    for name, value in [
        ("_batch_load_loop_configs", {}),
        ("_pin_task_config", None),
        ("_resolve_task_release_context", None),
        ("_load_batch_runtime_overrides", {}),
        ("_batch_load_template_keys", {}),
        ("_prewarm_cache_for_loops", {"total": len(loops), "succeeded": len(loops), "failed": 0}),
    ]:
        monkeypatch.setattr(kpi_calc, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        kpi_calc.result_ledger, "loop_ids_with_failed_records", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(kpi_calc, "_get_shared_bundle_cache", lambda: False)
    monkeypatch.setattr(kpi_calc, "_build_data_planner", MagicMock())
    results = {
        str(loop.id): {"status": status} for loop, status in zip(loops, statuses, strict=True)
    }

    async def calculate(**kwargs):
        return results[str(kwargs["loop"].id)]

    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calculate)
    task_store._hashes[f"task:{task_id}"] = {
        "task_id": task_id,
        "task_type": "CUSTOM",
        "status": "PENDING",
        "progress": "0",
        "loops_total": str(len(loops)),
        "loops_done": "0",
        "current_stage": "取数",
        "celery_task_id": "custom-celery-test",
        "created_by_id": "unit-test-user",
    }
    return task_id, loops, db


async def test_custom_processed_counts_include_inconclusive_and_survive_lazy_sync(
    monkeypatch, task_store
):
    task_id, loops, db = _prepare_batch(monkeypatch, task_store, ["SUCCESS", "INCONCLUSIVE"])
    summary = await kpi_calc._do_calculate_custom_batch(
        task_id, [loop.id for loop in loops], "2026-10-17T11:00:00", "2026-10-17T12:00:00"
    )
    assert (summary["total"], summary["success"], summary["inconclusive"], summary["failed"]) == (
        2,
        1,
        1,
        0,
    )
    assert db.commit.await_count == 2
    observed = await task_tracker.get_task(task_id)
    assert observed["loops_done"] == "2"
    assert observed["progress"] == "1.0"
    assert observed["current_stage"] == "完成"
    monkeypatch.setattr(
        "celery.result.AsyncResult", lambda *a, **kw: SimpleNamespace(state="SUCCESS")
    )
    await tasks._sync_task_status(task_id, observed)
    assert observed["status"] == "SUCCESS"
    assert observed["loops_done"] == "2"
    assert observed["current_stage"] == "完成"


async def test_retried_custom_attempt_resets_prior_progress_before_actual_work(
    monkeypatch, task_store
):
    task_id, loops, _ = _prepare_batch(monkeypatch, task_store, ["INCONCLUSIVE"])
    task_store._hashes[f"task:{task_id}"].update(progress="1.0", loops_done="9")

    async def attempt(run, **kwargs):
        current = await task_tracker.get_task(task_id)
        assert current["loops_done"] == "0"
        assert current["progress"] == "0"
        return await run()

    monkeypatch.setattr(kpi_calc, "_attempt_log_wrap", attempt)
    result = await kpi_calc._do_calculate_custom_batch(
        task_id, [loops[0].id], "2026-10-17T11:00:00", "2026-10-17T12:00:00", attempt_no=2
    )
    assert result["inconclusive"] == 1
    assert (await task_tracker.get_task(task_id))["loops_done"] == "1"


def _custom_db():
    db = AsyncMock()
    rows = MagicMock()
    rows.all.return_value = []
    db.execute.return_value = rows
    return db


async def test_custom_record_exists_before_fast_consumer_and_is_not_overwritten(
    monkeypatch, task_store
):
    def publish(*, args, priority, task_id=None):
        assert f"task:{args[0]}" in task_store._hashes, "consumer started before task record existed"
        stored = task_store._hashes[f"task:{args[0]}"]
        assert task_id is not None
        assert stored["celery_task_id"] == task_id
        assert task_store._zsets["task:index"][args[0]] > 0
        stored.update(status="SUCCESS", progress="1.0", loops_done="1", current_stage="完成")
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(kpi_calc.calculate_custom_batch_kpi, "apply_async", publish)
    response = await tasks.trigger_custom_evaluation(
        CustomTaskCreate(**_CUSTOM_BODY), _custom_db(), TEST_USERS["ic_engineer"]
    )
    task_id = response["data"]["taskId"]
    assert task_store._hashes[f"task:{task_id}"]["loops_done"] == "1"
    assert task_store._hashes[f"task:{task_id}"]["status"] == "SUCCESS"


async def test_custom_publish_failure_is_failed_and_releases_slot_once(monkeypatch, task_store):
    monkeypatch.setattr(
        kpi_calc.calculate_custom_batch_kpi,
        "apply_async",
        MagicMock(side_effect=RuntimeError("broker down")),
    )
    with pytest.raises(RuntimeError, match="broker down"):
        await tasks.trigger_custom_evaluation(
            CustomTaskCreate(**_CUSTOM_BODY), _custom_db(), TEST_USERS["ic_engineer"]
        )
    assert task_store._zsets.get("task:index"), "publish failure must retain its failed task record"
    task_id = next(iter(task_store._zsets["task:index"]))
    stored = await tasks._get_task(task_id)
    assert stored["status"] == "FAILED"
    assert stored["current_stage"] == "投递失败"
    assert "broker down" in stored["error_message"]
    assert stored["finished_at"]
    assert stored["slot_released"] == "1"
    assert all(value == "0" for value in task_store._strings.values())
    await tasks._release_concurrency_slot(task_id, stored)
    assert all(value == "0" for value in task_store._strings.values())
