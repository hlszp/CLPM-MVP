"""影子块边界、补数失效、严格语义对照与只读出口。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import ten_minute_blocks as blocks
from app.services import ten_minute_control as control
from app.services import ten_minute_shadow as shadow
from tests.conftest import FakeRedis

T = datetime(2026, 10, 21, tzinfo=UTC)
POINT = str(uuid4())


class MemoryRedis(FakeRedis):
    async def eval(self, script, n, *args):
        if n == 2:
            key, index, value, ttl, score, limit = args
            await self.set(key, value, nx=True, ex=ttl)
            z = self._zsets.setdefault(index, {})
            z[key] = float(score)
            while len(z) > int(limit):
                old = min(z, key=z.get)
                del z[old]
                await self.delete(old)
            return 1
        key, old, new, ttl = args
        if await self.get(key) != old:
            return 0
        if new:
            await self.set(key, new, ex=ttl)
        else:
            await self.delete(key)
        return 1


def source_rows():
    return [
        {"ts": T + timedelta(seconds=i), "value": float(i), "quality_class": 1}
        for i in range(0, 3601)
    ]


def reader_for(rows):
    async def read(points, start, end, **kw):
        assert kw == {"full_columns": False}
        return {
            p: deepcopy([r for r in rows if blocks.utc(start) <= r["ts"] <= blocks.utc(end)])
            for p in points
        }

    return AsyncMock(side_effect=read)


@pytest.mark.parametrize(
    "left,right", [(0, 3600), (3, 600), (17, 3017), (600, 600), (600, 601), (0, 599), (23, 1200)]
)
async def test_block_stitch_preserves_inclusive_endpoints_and_manual_windows(left, right):
    rows = source_rows()
    read = reader_for(rows)
    reference = blocks.ReferenceEvents(read)
    start, end = T + timedelta(seconds=left), T + timedelta(seconds=right)
    expected = await reference([POINT], start, end, full_columns=False)
    cached = blocks.TenMinuteBlocks(MemoryRedis(), reference, read)
    assert await cached([POINT], start, end, full_columns=False) == expected
    await cached.finish()
    # 第二遍完整块命中，首尾依然精确回源；无边界重复/丢点。
    read.reset_mock()
    assert await cached([POINT], start, end, full_columns=False) == expected
    await cached.finish()
    if left == 0 and right == 3600:
        assert read.await_count == 1  # 尾点是包含式接口事实，不伪造。


async def test_backfill_quality_change_invalidates_content_address_and_rechecks_source():
    rows, redis = source_rows(), MemoryRedis()
    read = reader_for(rows)
    await blocks.TenMinuteBlocks(redis, reader=read).prepare(POINT, T)
    rows[8]["value"] = None
    rows[9]["quality_class"] = 0
    reference = blocks.ReferenceEvents(read)
    expected = await reference([POINT], T, T + blocks.BLOCK, full_columns=False)
    candidate = blocks.TenMinuteBlocks(redis, reference, read)
    assert await candidate([POINT], T, T + blocks.BLOCK, full_columns=False) == expected
    assert candidate.stats["invalidated"] == 1
    assert candidate.stats["misses"] == 1
    assert candidate.stats["hits"] == 0


async def test_mid_read_backfill_is_invalidated_and_corrupt_cache_is_rebuilt():
    rows, redis = source_rows(), MemoryRedis()
    read = reader_for(rows)
    reference = blocks.ReferenceEvents(read)
    await reference([POINT], T, T + blocks.BLOCK, full_columns=False)
    rows[5]["value"] = -9
    with pytest.raises(blocks.SourceChanged):
        await blocks.TenMinuteBlocks(redis, reference, read)._block(POINT, T)
    reference = blocks.ReferenceEvents(read)
    await reference([POINT], T, T + blocks.BLOCK, full_columns=False)
    revision = reference.expected(POINT, T, T + blocks.BLOCK)
    key = f"{blocks.TenMinuteBlocks.prefix(POINT, T)}:{revision}"
    await redis.set(key, "corrupt")
    candidate = blocks.TenMinuteBlocks(redis, reference, read)
    assert (await candidate._block(POINT, T))[5]["value"] == -9
    assert candidate.stats["corrupt"] == 1


async def test_cache_budget_redis_failure_and_cancelled_shared_consumer(monkeypatch):
    monkeypatch.setattr(blocks, "MAX_BLOCKS", 2)
    rows, redis = source_rows(), MemoryRedis()
    read = reader_for(rows)
    candidate = blocks.TenMinuteBlocks(redis, reader=read)
    for i in range(3):
        await candidate.prepare(POINT, T + i * blocks.BLOCK)
    assert len(redis._zsets["shadow10:block-index"]) == 2
    assert len([k for k in redis._strings if not k.endswith(":head")]) == 2
    reference = blocks.ReferenceEvents(read)
    await reference([POINT], T, T + blocks.BLOCK, full_columns=False)
    failed = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("redis offline")))
    with pytest.raises(RuntimeError, match="redis offline"):
        await blocks.TenMinuteBlocks(failed, reference, read)._block(POINT, T)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return await read(*args, **kwargs)

    candidate = blocks.TenMinuteBlocks(MemoryRedis(), reference, delayed)
    one = asyncio.create_task(candidate([POINT], T, T + blocks.BLOCK, full_columns=False))
    await started.wait()
    two = asyncio.create_task(candidate([POINT], T, T + blocks.BLOCK, full_columns=False))
    one.cancel()
    with pytest.raises(asyncio.CancelledError):
        await one
    release.set()
    assert len((await two)[POINT]) == 601
    await candidate.finish()
    assert not candidate._inflight


def result(status="SUCCESS", input_hash="input", result_hash="result"):
    return {
        "inputHash": input_hash,
        "resultHash": result_hash,
        "pointCount": 3601,
        "elapsedSeconds": 1,
        "result": {"status": status, "score": 88},
    }


def test_comparison_never_qualifies_changed_source_or_insufficient_data():
    base = result()
    assert shadow.compare_passes(base, base, base)["status"] == "EQUAL"
    assert (
        shadow.compare_passes(base, base, result(input_hash="backfill"))["status"] == "INVALIDATED"
    )
    assert shadow.compare_passes(base, result(result_hash="other"), base)["status"] == "MISMATCH"
    bad = result("INCONCLUSIVE")
    assert shadow.compare_passes(bad, bad, bad)["status"] == "INSUFFICIENT_DATA"


@pytest.mark.parametrize(
    "cfg",
    [
        {"enabled": True},
        {"enabled": "false"},
        {"enabled": True, "loopIds": ["oops"]},
        {"enabled": True, "loopIds": [POINT, POINT]},
        {"enabled": True, "loopIds": [str(uuid4()) for _ in range(25)]},
    ],
)
def test_config_rejects_implicit_or_unbounded_selection(cfg):
    import json

    with pytest.raises((TypeError, ValueError)):
        control.parse_config(json.dumps(cfg))
    assert control.parse_config(None) == {"enabled": False, "loopIds": []}


async def test_auto_manual_cohorts_share_global_budget_and_keep_task_identity(
    monkeypatch, tmp_path
):
    redis = MemoryRedis()
    monkeypatch.setattr("app.core.config.settings.CLPM_KPI_SHADOW_REPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        control, "read_config", AsyncMock(return_value={"enabled": True, "loopIds": [POINT]})
    )
    sent = []
    monkeypatch.setattr(control, "dispatch", lambda owner, cursor: sent.append((owner, cursor)))
    first = await control.enqueue(
        redis,
        loop_ids=[POINT],
        start=T,
        end=T + timedelta(hours=1),
        kind="ASSESS:AUTO",
        source_task_id="hourly-1",
    )
    second = await control.enqueue(
        redis,
        loop_ids=[POINT],
        start=T,
        end=T + timedelta(hours=1),
        kind="MANUAL_SHADOW",
        source_task_id="manual-2",
    )
    assert first["status"] == "QUEUED" and second["status"] == "BUSY"
    assert len(sent) == 1
    old = await redis.get(control.ACTIVE_KEY)
    import json

    assert json.loads(old)["sourceTaskId"] == "hourly-1"
    assert not await control.replace_state(redis, "stale-owner", None)
    assert await control.replace_state(redis, old, None)
    assert (
        await control.enqueue(
            redis, loop_ids=[POINT], start=T, end=T + timedelta(hours=2), kind="MANUAL_SHADOW"
        )
    )["status"] == "UNSUPPORTED_WINDOW"


def test_utc_alignment_preserves_offset_and_precision():
    assert blocks.floor_block(T.astimezone(timezone(timedelta(hours=8)))) == T
    assert blocks.utc(T.replace(tzinfo=None)) == T


def test_dispatch_and_schedule_cannot_inherit_manual_priority(monkeypatch):
    from unittest.mock import Mock

    from app.tasks.ten_minute_shadow import celery_app, shadow_step

    send = Mock()
    monkeypatch.setattr(shadow_step, "apply_async", send)
    control.dispatch("owner", 0)
    assert send.call_args.kwargs["priority"] == 9
    assert send.call_args.kwargs["retry"] is False
    assert celery_app.conf.beat_schedule["kpi-shadow-10min"]["options"] == {
        "priority": 9,
        "expires": 120,
    }


async def test_config_revision_change_during_validation_is_not_equal(monkeypatch):
    class Session:
        execute = AsyncMock()
        rollback = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("app.core.db.AsyncSessionLocal", Session)
    monkeypatch.setattr(
        shadow,
        "load_context",
        AsyncMock(
            side_effect=[
                {"configHash": "before"},
                {"configHash": "after"},
            ]
        ),
    )
    monkeypatch.setattr(shadow, "capture_pass", AsyncMock(return_value=result()))
    actual = await shadow.validate_loop(MemoryRedis(), POINT, T, T + timedelta(hours=1))
    assert actual == {"status": "INVALIDATED", "reason": "config_changed"}


async def test_capture_exit_uses_real_algorithms_without_result_writes(monkeypatch):
    from app.tasks import kpi_calc as kpi
    from tests.test_calc_snapshot import _make_kpi_bundles

    bundles = _make_kpi_bundles()
    fake = SimpleNamespace(request_bundles=AsyncMock(return_value=bundles), _last_request_timing={})
    monkeypatch.setattr(kpi, "_build_data_planner", lambda *a, **k: fake)
    persist = AsyncMock(side_effect=AssertionError("shadow wrote result"))
    monkeypatch.setattr(kpi, "_persist_snapshot", persist)
    context = {
        "loop": SimpleNamespace(
            id="loop-1", tag_name="test", loop_type="FLOW", unit_id=None, ideal_settling_time=None
        ),
        "metric_configs": {},
        "type_weights": {},
        "loop_cfg": {"op_lower": 0, "op_upper": 100},
        "pinned_config": {"params": {}, "overrides": {}},
        "runtime_overrides": {"fitness": {}, "kpiMinObservedRatio": 0.3},
        "template_keys": {},
        "release_context": None,
        "requirements": {},
    }
    db = SimpleNamespace(execute=AsyncMock())
    actual = await shadow.capture_pass(db, context, T, T + timedelta(minutes=20), None)
    assert actual["inputHash"] and actual["pointCount"] == 1200
    assert "metrics_semantic" in actual["result"]
    persist.assert_not_awaited()
    assert not hasattr(db, "commit")


@pytest.mark.parametrize(
    "case", ["constant", "step", "bad_unknown", "frozen", "oscillation", "rebind_gap"]
)
async def test_complete_window_preprocessing_and_context_survive_block_seams(case, monkeypatch):
    import math
    from dataclasses import asdict

    from app.contracts.data_types import ControlType, LoopPreprocessConfig, TagGroup
    from app.services.data_source import logical_wide_builder as builder
    from app.services.preprocessing.pipeline import PreprocessingPipeline

    pids = {role: str(uuid4()) for role in ("pv", "sp", "op", "mode", "replacement")}
    end = T + timedelta(minutes=20)
    seam = T + blocks.BLOCK

    async def metadata(stmt):
        table = stmt.get_final_froms()[0].name
        if table == "loop_tag_binding_history":
            rows = [
                (r.upper(), pids[r], T - blocks.BLOCK, None) for r in ("pv", "sp", "op", "mode")
            ]
            if case == "rebind_gap":
                rows[0] = ("PV", pids["pv"], T - blocks.BLOCK, seam)
                rows.append(("PV", pids["replacement"], seam, None))
        elif table == "history_coverage_segment":
            rows = (
                [(seam - timedelta(seconds=2), seam + timedelta(seconds=3))]
                if case == "rebind_gap"
                else []
            )
        else:
            raise AssertionError(table)
        return SimpleNamespace(all=lambda: rows)

    async def events(points, start, stop, **kwargs):
        output = {}
        for pid in points:
            role = next(r for r, p in pids.items() if p == pid)
            rows = []
            for i in range(1201):
                ts = T + timedelta(seconds=i)
                if not blocks.utc(start) <= ts <= blocks.utc(stop):
                    continue
                value = 1 if role == "mode" else 50.0
                if role in ("pv", "replacement"):
                    if case == "step":
                        value += 5 if i >= 599 else 0
                    elif case == "oscillation":
                        value += math.sin(i / 23)
                    elif case == "frozen":
                        value += 0 if 580 <= i <= 620 else math.sin(i / 13)
                    elif case == "bad_unknown" and i == 600:
                        value = None
                rows.append(
                    {
                        "ts": ts,
                        "value": value,
                        "quality_class": (0 if i == 599 else -1 if i == 601 else 1)
                        if case == "bad_unknown"
                        else 1,
                    }
                )
            output[pid] = rows
        return output

    monkeypatch.setattr(builder, "_metadata_execute", metadata)
    monkeypatch.setattr(
        builder, "_held_limits_s", AsyncMock(return_value={"fast": 60.0, "slow": 7200.0})
    )
    monkeypatch.setattr(
        builder.repo,
        "read_last_states_before",
        AsyncMock(
            side_effect=lambda points, before: {
                p: {"ts": before - timedelta(seconds=1), "value": 50.0, "quality_class": 1}
                for p in points
            }
        ),
    )
    monkeypatch.setattr(builder.meta, "applicable_anchors_batch", AsyncMock(return_value={}))
    reference = blocks.ReferenceEvents(events)
    roles = ["pv", "sp", "op", "mode"]
    direct = await builder.build_logical_wide(None, "L", roles, T, end, event_reader=reference)
    candidate = blocks.TenMinuteBlocks(MemoryRedis(), reference, events)
    assembled = await builder.build_logical_wide(None, "L", roles, T, end, event_reader=candidate)
    assert asdict(assembled) == asdict(direct)
    config = LoopPreprocessConfig(
        loop_id="L", control_type=ControlType.FLOW, range_min=0, range_max=100
    )
    pipeline = PreprocessingPipeline(config)
    assert asdict(pipeline.process(assembled, TagGroup.BASE, (T, end))) == asdict(
        pipeline.process(direct, TagGroup.BASE, (T, end))
    )
