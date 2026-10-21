"""并发 TagGroup 只读复用：数值/质量不变、生命周期/失败/取消隔离。"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.data_source import logical_wide_builder as builder
from app.services.data_source import tdengine_provider as provider
from app.services.data_source.point_read_scope import PointReadScopes


async def test_reads_are_shared_only_while_same_window_is_active():
    scopes = PointReadScopes()
    entered, release = asyncio.Event(), asyncio.Event()
    factory = AsyncMock()

    async def load():
        entered.set()
        await release.wait()
        return 17

    factory.side_effect = load

    async def read(loop="L1", end=1):
        async with scopes.use(loop, 0, end) as scope:
            return await scope.read(("point", "P1"), factory)

    first = asyncio.create_task(read())
    await entered.wait()
    second = asyncio.create_task(read())
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(first, second) == [17, 17]
    factory.assert_awaited_once()
    assert not scopes._active
    # 下一次调用、不同回路、不同窗口均重新读取。
    assert await asyncio.gather(read(), read("L2"), read(end=2)) == [17, 17, 17]
    assert factory.await_count == 4


async def test_one_cancelled_consumer_does_not_cancel_sibling_read():
    scopes = PointReadScopes()
    started, release = asyncio.Event(), asyncio.Event()

    async def load():
        started.set()
        await release.wait()
        return "complete"

    async def read():
        async with scopes.use("L1", 0, 1) as scope:
            return await scope.read(("P1",), load)

    first = asyncio.create_task(read())
    await started.wait()
    second = asyncio.create_task(read())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == "complete"
    assert not scopes._active


async def test_failed_read_is_not_retained_for_later_retry():
    scopes = PointReadScopes()
    factory = AsyncMock(side_effect=[RuntimeError("TD unavailable"), "recovered"])
    with pytest.raises(RuntimeError, match="TD unavailable"):
        async with scopes.use("L1", 0, 1) as scope:
            await scope.read(("P1",), factory)
    assert not scopes._active
    async with scopes.use("L1", 0, 1) as scope:
        assert await scope.read(("P1",), factory) == "recovered"
    assert factory.await_count == 2


async def test_last_cancelled_consumer_drains_io_before_scope_is_released():
    scopes = PointReadScopes()
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def load():
        started.set()
        await release.wait()
        finished.set()

    async def read():
        async with scopes.use("L1", 0, 1) as scope:
            await scope.read(("P1",), load)

    consumer = asyncio.create_task(read())
    await started.wait()
    consumer.cancel()
    await asyncio.sleep(0)
    assert not consumer.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert finished.is_set() and not scopes._active


async def test_provider_shares_queries_without_changing_grid_or_quality(monkeypatch):
    start = datetime(2026, 10, 21, tzinfo=UTC)
    end = start + timedelta(seconds=20)
    counts = Counter()

    async def metadata(statement):
        table = statement.get_final_froms()[0].name
        counts[table] += 1
        await asyncio.sleep(0)
        if table == "loop_tag_binding_history":
            rows = [
                (r.upper(), r, start - timedelta(hours=1), None) for r in ("pv", "sp", "op", "mode")
            ]
        elif table == "history_coverage_segment":
            rows = [(start + timedelta(seconds=8), start + timedelta(seconds=10))]
        else:
            raise AssertionError(table)
        return SimpleNamespace(all=lambda: rows)

    async def events(points, _start, _end, **_kwargs):
        counts["events"] += 1
        await asyncio.sleep(0)
        return {
            pid: [
                {
                    "ts": start + timedelta(seconds=i),
                    "value": float(i),
                    "quality_class": 1 if i < 16 else 0,
                }
                for i in range(0, 21, 2)
            ]
            for pid in points
        }

    async def initial(points, before):
        counts["initial"] += 1
        await asyncio.sleep(0)
        return {
            pid: {"ts": before - timedelta(seconds=1), "value": 1.0, "quality_class": 1}
            for pid in points
        }

    async def anchors(*_args, **_kwargs):
        counts["anchors"] += 1
        await asyncio.sleep(0)
        return {}

    monkeypatch.setattr(builder, "_metadata_execute", metadata)
    monkeypatch.setattr(
        builder, "_held_limits_s", AsyncMock(return_value={"fast": 60.0, "slow": 7200.0})
    )
    monkeypatch.setattr(builder.repo, "read_events", events)
    monkeypatch.setattr(builder.repo, "read_last_states_before", initial)
    monkeypatch.setattr(builder.meta, "applicable_anchors_batch", anchors)
    monkeypatch.setattr(
        "app.services.data_source.history_layout_router.resolve_window_layouts",
        AsyncMock(return_value=[SimpleNamespace(start=start, end=end)]),
    )
    groups = [
        (["pv", "sp", "op", "mode"], 5),
        (["mode"], 1),
        (["pv_quality"], 1),
        (["pv", "op"], 1),
        (["op"], 1),
    ]
    baseline = await asyncio.gather(
        *[
            provider._point_or_mixed_query(
                None, "L1", roles, start, end, period, [SimpleNamespace(start=start, end=end)]
            )
            for roles, period in groups
        ]
    )
    before = counts.copy()
    counts.clear()
    query = provider.TDengineProvider().make_query_fn(None)
    actual = await asyncio.gather(
        *[query("L1", roles, start, end, period) for roles, period in groups]
    )
    for expected, result in zip(baseline, actual, strict=True):
        assert result.timestamps == expected.timestamps
        assert result.signals == expected.signals
        assert result.quality_codes == expected.quality_codes
        assert result.series_context.to_dict() == expected.series_context.to_dict()
    assert before["events"] == 11 and counts["events"] == 4
    assert before["initial"] == 5 and counts["initial"] == 4
    assert before["loop_tag_binding_history"] == 5 and counts["loop_tag_binding_history"] == 1
    assert before["history_coverage_segment"] == 5 and counts["history_coverage_segment"] == 1
    assert before["anchors"] == 5 and counts["anchors"] == 4
    # 共享范围结束后，同窗口复读仍重新访问源（不遮蔽迟到/补入的数据）。
    await query("L1", ["pv"], start, end, 1)
    assert counts["events"] == 5 and counts["loop_tag_binding_history"] == 2
