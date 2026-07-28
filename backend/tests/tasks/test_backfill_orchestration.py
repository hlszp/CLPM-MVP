"""Deterministic tests for non-blocking KPI backfill orchestration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def _task() -> MagicMock:
    task = MagicMock()
    task.request.id = "dispatcher-id"
    task.run_async.side_effect = asyncio.run
    return task


def test_backfill_dispatcher_never_waits_for_children() -> None:
    from app.tasks.kpi_calc import backfill_kpi_range

    task = _task()
    dispatch = {
        "total_windows": 2,
        "child_task_ids": ["child-1"],
        "callback_task_id": "callback-1",
    }
    with (
        patch(
            "app.tasks.kpi_calc._dispatch_backfill_chord",
            return_value=dispatch,
        ) as mock_dispatch,
        patch("app.tasks.kpi_calc._update_task_running", new_callable=AsyncMock),
        patch(
            "app.services.task_tracker.reserve_backfill_dispatch",
            new_callable=AsyncMock,
            return_value=("CLAIMED", ["child-1"], "callback-1"),
        ),
        patch(
            "app.services.task_tracker.complete_backfill_dispatch",
            new_callable=AsyncMock,
            return_value=True,
        ) as complete_dispatch,
    ):
        result = backfill_kpi_range.run.__func__(
            task,
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            loop_ids=[str(uuid4())],
            task_id="task-1",
        )

    mock_dispatch.assert_called_once()
    complete_dispatch.assert_awaited_once()
    assert result["status"] == "DISPATCHED"
    assert result["callback_task_id"] == "callback-1"


def test_dispatch_recovers_reserve_before_publish_with_same_canvas_ids() -> None:
    from app.tasks.kpi_calc import backfill_kpi_range

    task = _task()
    dispatch = {
        "total_windows": 2,
        "child_task_ids": ["stable-child"],
        "callback_task_id": "stable-callback",
    }
    with (
        patch("app.tasks.kpi_calc._update_task_running", new_callable=AsyncMock),
        patch(
            "app.services.task_tracker.reserve_backfill_dispatch",
            new_callable=AsyncMock,
            return_value=("RECOVER", ["stable-child"], "stable-callback"),
        ),
        patch(
            "app.tasks.kpi_calc._dispatch_backfill_chord",
            return_value=dispatch,
        ) as publish,
        patch(
            "app.services.task_tracker.complete_backfill_dispatch",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = backfill_kpi_range.run.__func__(
            task,
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            task_id="task-1",
        )

    assert result["child_task_ids"] == ["stable-child"]
    assert publish.call_args.kwargs["child_task_ids"] == ["stable-child"]
    assert publish.call_args.kwargs["callback_task_id"] == "stable-callback"


def test_dispatch_same_task_id_after_mark_publishes_only_once() -> None:
    from app.tasks.kpi_calc import backfill_kpi_range

    task = _task()
    dispatch = {
        "total_windows": 2,
        "child_task_ids": ["stable-child"],
        "callback_task_id": "stable-callback",
    }
    with (
        patch("app.tasks.kpi_calc._update_task_running", new_callable=AsyncMock),
        patch(
            "app.services.task_tracker.reserve_backfill_dispatch",
            new_callable=AsyncMock,
            side_effect=[
                ("CLAIMED", ["stable-child"], "stable-callback"),
                ("EXISTING", ["stable-child"], "stable-callback"),
            ],
        ),
        patch(
            "app.tasks.kpi_calc._dispatch_backfill_chord",
            return_value=dispatch,
        ) as publish,
        patch(
            "app.services.task_tracker.complete_backfill_dispatch",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        first = backfill_kpi_range.run.__func__(
            task,
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            task_id="task-1",
        )
        second = backfill_kpi_range.run.__func__(
            task,
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            task_id="task-1",
        )

    publish.assert_called_once()
    assert first == second


def test_dispatch_recovers_publish_before_mark_by_republishing_same_ids() -> None:
    from app.tasks.kpi_calc import backfill_kpi_range

    task = _task()
    dispatch = {
        "total_windows": 2,
        "child_task_ids": ["stable-child"],
        "callback_task_id": "stable-callback",
    }
    with (
        patch("app.tasks.kpi_calc._update_task_running", new_callable=AsyncMock),
        patch(
            "app.services.task_tracker.reserve_backfill_dispatch",
            new_callable=AsyncMock,
            side_effect=[
                ("CLAIMED", ["stable-child"], "stable-callback"),
                ("RECOVER", ["stable-child"], "stable-callback"),
            ],
        ),
        patch(
            "app.tasks.kpi_calc._dispatch_backfill_chord",
            return_value=dispatch,
        ) as publish,
        patch(
            "app.services.task_tracker.complete_backfill_dispatch",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ),
    ):
        for _ in range(2):
            backfill_kpi_range.run.__func__(
                task,
                "2026-07-04T00:00:00Z",
                "2026-07-04T02:00:00Z",
                task_id="task-1",
            )

    assert publish.call_count == 2
    assert all(
        call.kwargs["child_task_ids"] == ["stable-child"]
        and call.kwargs["callback_task_id"] == "stable-callback"
        for call in publish.call_args_list
    )


def test_backfill_child_busy_retries_without_finishing_chord() -> None:
    from app.tasks.kpi_calc import _backfill_window_batch

    task = _task()
    task.request.id = "stable-child"
    task.retry.side_effect = RuntimeError("retry scheduled")
    with patch(
        "app.services.task_tracker.claim_backfill_batch",
        new_callable=AsyncMock,
        return_value=("BUSY", None),
    ):
        with pytest.raises(RuntimeError, match="retry scheduled"):
            _backfill_window_batch.run.__func__(
                task,
                ["2026-07-04T00:00:00"],
                task_id="task-1",
            )

    task.retry.assert_called_once_with(countdown=10)
    assert _backfill_window_batch.max_retries is None


def test_backfill_child_done_reuses_cached_result() -> None:
    from app.tasks.kpi_calc import _backfill_window_batch

    task = _task()
    task.request.id = "stable-child"
    cached = {"success": 2, "failed": 0, "failed_windows": []}
    with patch(
        "app.services.task_tracker.claim_backfill_batch",
        new_callable=AsyncMock,
        return_value=("DONE", cached),
    ):
        result = _backfill_window_batch.run.__func__(
            task,
            ["2026-07-04T00:00:00"],
            task_id="task-1",
        )

    assert result == cached


@pytest.mark.asyncio
async def test_finalize_backfill_success() -> None:
    from app.tasks.kpi_calc import _do_finalize_backfill

    batches = [
        {
            "success": 2,
            "inconclusive": 1,
            "failed": 0,
            "node_success": 3,
            "failed_windows": [],
        }
    ]
    with (
        patch("app.tasks.kpi_calc._is_task_cancelled", new_callable=AsyncMock, return_value=False),
        patch("app.tasks.kpi_calc._update_task_success", new_callable=AsyncMock) as update,
        patch("app.tasks.kpi_calc._invalidate_backfill_cache", new_callable=AsyncMock),
    ):
        result = await _do_finalize_backfill(
            batches,
            "2026-07-04T00:00:00Z",
            "2026-07-04T01:00:00Z",
            total_windows=1,
            task_id="task-1",
        )

    assert result["loop_success"] == 2
    assert result["node_success"] == 3
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_backfill_failure_sets_failed_terminal_state() -> None:
    from app.tasks.kpi_calc import _do_finalize_backfill

    with (
        patch("app.tasks.kpi_calc._is_task_cancelled", new_callable=AsyncMock, return_value=False),
        patch("app.tasks.kpi_calc._update_task_failed", new_callable=AsyncMock) as update,
        patch("app.tasks.kpi_calc._invalidate_backfill_cache", new_callable=AsyncMock),
    ):
        result = await _do_finalize_backfill(
            [{"success": 0, "inconclusive": 0, "failed": 1, "failed_windows": ["w1"]}],
            "2026-07-04T00:00:00Z",
            "2026-07-04T01:00:00Z",
            total_windows=1,
            task_id="task-1",
        )

    assert result["status"] == "FAILED"
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_backfill_preserves_cancelled_state() -> None:
    from app.tasks.kpi_calc import _do_finalize_backfill

    with (
        patch("app.tasks.kpi_calc._is_task_cancelled", new_callable=AsyncMock, return_value=True),
        patch("app.tasks.kpi_calc._update_task_success", new_callable=AsyncMock) as success,
        patch("app.tasks.kpi_calc._update_task_failed", new_callable=AsyncMock) as failed,
        patch(
            "app.tasks.kpi_calc._invalidate_backfill_cache", new_callable=AsyncMock
        ) as invalidate,
    ):
        result = await _do_finalize_backfill(
            [],
            "2026-07-04T00:00:00Z",
            "2026-07-04T01:00:00Z",
            total_windows=1,
            task_id="task-1",
        )

    assert result["cancelled"] is True
    success.assert_not_awaited()
    failed.assert_not_awaited()
    invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_backfill_invalidates_loop_cache_on_success() -> None:
    """回填成功后应对涉及回路触发缓存失效（清除「先算后导」负缓存）."""
    from app.tasks.kpi_calc import _do_finalize_backfill

    batches = [
        {"success": 2, "inconclusive": 0, "failed": 0, "node_success": 1, "failed_windows": []}
    ]
    with (
        patch("app.tasks.kpi_calc._is_task_cancelled", new_callable=AsyncMock, return_value=False),
        patch("app.tasks.kpi_calc._update_task_success", new_callable=AsyncMock),
        patch(
            "app.tasks.kpi_calc._invalidate_backfill_cache", new_callable=AsyncMock
        ) as invalidate,
    ):
        result = await _do_finalize_backfill(
            batches,
            "2026-07-04T00:00:00Z",
            "2026-07-04T01:00:00Z",
            total_windows=1,
            task_id="task-1",
            loop_ids=["L1", "L2"],
        )

    assert result["status"] == "SUCCESS"
    invalidate.assert_awaited_once_with(["L1", "L2"])


@pytest.mark.asyncio
async def test_invalidate_backfill_cache_per_loop() -> None:
    """指定 loop_ids 时应逐回路失效（invalidate_loop 覆盖 pdb:/pdb_l2:/pdb_l3:）."""
    from app.tasks.kpi_calc import _invalidate_backfill_cache

    with patch("app.services.cache.invalidation.CacheInvalidator") as mock_invalidator_cls:
        inv = mock_invalidator_cls.return_value
        inv.invalidate_loop = AsyncMock(return_value=3)
        inv.invalidate_all = AsyncMock(return_value=99)

        deleted = await _invalidate_backfill_cache(["L1", "L2"])

    assert deleted == 6
    assert inv.invalidate_loop.await_count == 2
    inv.invalidate_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_backfill_cache_all_when_loop_ids_none() -> None:
    """loop_ids=None（全量回填）时应退化为全量清理."""
    from app.tasks.kpi_calc import _invalidate_backfill_cache

    with patch("app.services.cache.invalidation.CacheInvalidator") as mock_invalidator_cls:
        inv = mock_invalidator_cls.return_value
        inv.invalidate_loop = AsyncMock(return_value=3)
        inv.invalidate_all = AsyncMock(return_value=99)

        deleted = await _invalidate_backfill_cache(None)

    assert deleted == 99
    inv.invalidate_all.assert_awaited_once()
    inv.invalidate_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_backfill_cache_failure_returns_zero() -> None:
    """缓存失效异常不应阻断回填主流程，返回 0."""
    from app.tasks.kpi_calc import _invalidate_backfill_cache

    with patch(
        "app.services.cache.invalidation.CacheInvalidator",
        side_effect=RuntimeError("redis down"),
    ):
        deleted = await _invalidate_backfill_cache(["L1"])

    assert deleted == 0


@pytest.mark.asyncio
async def test_backfill_empty_loop_ids_is_noop() -> None:
    from app.tasks.kpi_calc import _do_backfill

    result = await _do_backfill(
        "2026-07-04T00:00:00Z",
        "2026-07-04T02:00:00Z",
        loop_ids=[],
    )
    assert result["total_windows"] == 2
    assert result["loop_success"] == 0


def test_backfill_child_uses_dedicated_concurrency_limit() -> None:
    from app.tasks.kpi_calc import _BACKFILL_LOOP_CONCURRENCY, _backfill_window_batch

    loop = MagicMock(id=uuid4())
    loop_result = MagicMock()
    loop_result.scalars.return_value.all.return_value = [loop]
    metric_result = MagicMock()
    metric_result.scalars.return_value.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[loop_result, metric_result])
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    task = MagicMock()
    task.run_async.side_effect = asyncio.run

    with (
        patch("app.core.db.AsyncSessionLocal", return_value=session),
        patch(
            "app.services.loop_config.get_loop_type_weights_map",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "app.tasks.kpi_calc._batch_load_loop_configs",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "app.tasks.kpi_calc._run_batch_loop_calculations",
            new_callable=AsyncMock,
            return_value=[],
        ) as calculate,
        patch(
            "app.tasks.kpi_calc._do_backfill_node_aggregation",
            new_callable=AsyncMock,
            return_value=2,
        ) as aggregate_nodes,
    ):
        _backfill_window_batch.run.__func__(
            task,
            ["2026-07-04T00:00:00"],
            loop_ids=[str(loop.id)],
        )

    assert _BACKFILL_LOOP_CONCURRENCY == 4
    assert calculate.await_args.kwargs["concurrency"] == 4
    aggregate_nodes.assert_awaited_once()


def test_dispatch_builds_partitioned_canvas_with_persistable_ids() -> None:
    from app.tasks.kpi_calc import _dispatch_backfill_chord

    canvas = MagicMock()
    with patch("app.tasks.kpi_calc.chord", return_value=canvas) as chord_factory:
        result = _dispatch_backfill_chord(
            "2026-07-04T00:00:00Z",
            "2026-07-04T07:00:00Z",
            loop_ids=[str(uuid4())],
            task_id="task-1",
        )

    header, callback = chord_factory.call_args.args
    # _BACKFILL_BATCH_SIZE=1：7 个窗口 → 7 个子任务，offset 逐窗口递增
    assert len(header) == 7
    assert [sig.kwargs["window_offset"] for sig in header] == [0, 1, 2, 3, 4, 5, 6]
    assert all(sig.kwargs["total_windows"] == 7 for sig in header)
    assert [sig.options["task_id"] for sig in header] == result["child_task_ids"]
    assert all(sig.options.get("link_error") for sig in header)
    assert callback.options["task_id"] == result["callback_task_id"]
    canvas.apply_async.assert_called_once_with()


def test_revoke_errback_does_not_overwrite_cancelled() -> None:
    from app.tasks.kpi_calc import _backfill_chord_error

    task = _task()
    with (
        patch("app.tasks.kpi_calc._is_task_cancelled", new_callable=AsyncMock, return_value=True),
        patch("app.services.task_tracker.update_status", new_callable=AsyncMock) as update,
    ):
        result = _backfill_chord_error.run.__func__(
            task,
            "revoked",
            task_id="task-cancelled",
        )

    assert result["status"] == "FAILED"
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_progress_uses_atomic_counter() -> None:
    from app.tasks.kpi_calc import _increment_backfill_progress

    with patch(
        "app.services.task_tracker.record_backfill_progress_once",
        new_callable=AsyncMock,
        return_value=(True, 3, 3 / 8),
    ) as record:
        await _increment_backfill_progress("task-1", 2, 4, 2, event_id="w2:loop-1")

    record.assert_awaited_once_with(
        "task-1",
        event_id="w2:loop-1",
        total_work_items=8,
        current_stage="回填计算 窗口[2/4]",
    )


@pytest.mark.asyncio
async def test_task_tracker_terminal_state_cannot_be_overwritten() -> None:
    from app.schemas.task import TaskStatus
    from app.services import task_tracker

    cancelled = {"task_id": "task-1", "status": "CANCELLED"}
    redis = MagicMock()
    redis.eval = AsyncMock(return_value=["BLOCKED", "CANCELLED"])
    redis.hgetall = AsyncMock(return_value=cancelled)
    with (
        patch("app.services.task_tracker.redis_client", redis),
    ):
        result = await task_tracker.update_status("task-1", TaskStatus.FAILED)

    assert result["status"] == "CANCELLED"
    assert redis.eval.await_count == 1
