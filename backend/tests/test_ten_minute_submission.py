"""正式评估到影子背景请求的提交预算、身份和过期语义。"""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import ten_minute_submission as submission


async def test_submit_preserves_window_identity_and_background_priority(monkeypatch):
    from app.tasks.celery_app import celery_app

    send = Mock()
    monkeypatch.setattr(celery_app, "send_task", send)
    start = datetime(2026, 10, 21, 9, tzinfo=timezone(timedelta(hours=8)))
    end = datetime(2026, 10, 21, 2)
    before = datetime.now(UTC)
    result = await submission.submit(["loop-B", "loop-A", "loop-B"], start, end, "CUSTOM", "run-1")
    after = datetime.now(UTC)
    assert result == {"status": "SUBMITTED"}
    send.assert_called_once()
    args, options = send.call_args
    assert args == ("app.tasks.ten_minute_shadow.request",)
    assert options["priority"] == 9
    assert options["retry"] is False
    assert isinstance(options["expires"], datetime)
    payload = options["kwargs"]
    requested_at = datetime.fromisoformat(payload["requested_at"])
    assert before <= requested_at <= after
    assert options["expires"] == requested_at + timedelta(seconds=120)
    assert payload == {
        "loop_ids": ["loop-B", "loop-A"],
        "start": "2026-10-21T01:00:00+00:00",
        "end": "2026-10-21T02:00:00+00:00",
        "kind": "ASSESS:CUSTOM",
        "source_task_id": "run-1",
        "requested_at": requested_at.isoformat(),
    }


async def test_slow_broker_does_not_block_formal_completion_or_extend_expiry(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    captured = []

    def slow_publish(kwargs, expires):
        captured.append((kwargs, expires))
        started.set()
        release.wait(timeout=2)
        finished.set()

    monkeypatch.setattr(submission, "_publish", slow_publish)
    start = datetime(2026, 10, 21)
    before = time.monotonic()
    try:
        result = await submission.submit(
            ["loop-1"], start, start + timedelta(hours=1), None, "run-2"
        )
        elapsed = time.monotonic() - before
        assert started.is_set()
        assert result == {"status": "SUBMISSION_TIMEOUT"}
        assert 0.15 <= elapsed < 0.5
        assert not finished.is_set()  # 超时不会硬取消已运行的线程。
        payload, expires = captured[0]
        assert payload["kind"] == "ASSESS:AUTO"
        assert payload["source_task_id"] == "run-2"
        assert expires == datetime.fromisoformat(payload["requested_at"]) + timedelta(seconds=120)
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 2)
    assert finished.is_set()


async def test_broker_failure_does_not_fail_formal_result(monkeypatch, caplog):
    monkeypatch.setattr(submission, "_publish", Mock(side_effect=ConnectionError("broker offline")))
    start = datetime(2026, 10, 21)
    result = await submission.submit(["loop-1"], start, start + timedelta(hours=1), "SINGLE", None)
    assert result == {"status": "SUBMISSION_FAILED"}
    assert "broker offline" in caplog.text


async def test_empty_eligible_set_does_not_contact_broker(monkeypatch):
    publish = Mock(side_effect=AssertionError("unexpected broker access"))
    monkeypatch.setattr(submission, "_publish", publish)
    start = datetime(2026, 10, 21)
    assert await submission.submit([], start, start, "CUSTOM", "run-3") == {
        "status": "NOT_SELECTED"
    }
    publish.assert_not_called()


async def test_formal_wrapper_only_submits_eligible_identity_without_configuration_or_files(
    monkeypatch,
):
    from app.core import db as db_module
    from app.services import ten_minute_control as control

    publish = Mock()
    read_config = AsyncMock()
    enqueue = AsyncMock()
    save_report, atomic_write, session = Mock(), Mock(), Mock()
    monkeypatch.setattr(submission, "_publish", publish)
    monkeypatch.setattr(control, "read_config", read_config)
    monkeypatch.setattr(control, "enqueue", enqueue)
    monkeypatch.setattr(control, "save_report", save_report)
    monkeypatch.setattr(control, "_atomic_write", atomic_write)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", session)
    start = datetime(2026, 10, 21, 1)
    end = start + timedelta(hours=1)
    await control.after_assessment(
        [SimpleNamespace(id=value) for value in ("loop-A", "loop-B", "loop-C", "loop-D")],
        [{"status": "SUCCESS"}, None, {"status": "INCONCLUSIVE"}, RuntimeError("failed")],
        start,
        end,
        source="MANUAL_STANDARD",
        source_task_id="formal-run-41",
    )
    publish.assert_called_once()
    payload, expires = publish.call_args.args
    assert payload["loop_ids"] == ["loop-A", "loop-C"]
    assert payload["start"] == "2026-10-21T01:00:00+00:00"
    assert payload["end"] == "2026-10-21T02:00:00+00:00"
    assert payload["kind"] == "ASSESS:MANUAL_STANDARD"
    assert payload["source_task_id"] == "formal-run-41"
    assert expires == datetime.fromisoformat(payload["requested_at"]) + timedelta(seconds=120)
    read_config.assert_not_awaited()
    enqueue.assert_not_awaited()
    save_report.assert_not_called()
    atomic_write.assert_not_called()
    session.assert_not_called()


def freeze_request_clock(monkeypatch):
    from app.tasks import ten_minute_shadow as tasks

    now = datetime(2026, 10, 21, 2, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(tasks, "datetime", FrozenDatetime)
    return tasks, now


@pytest.mark.parametrize("offset_seconds", [-121, -120, 1])
async def test_background_request_rejects_expired_and_future_submission_without_enqueue(
    monkeypatch, offset_seconds
):
    tasks, now = freeze_request_clock(monkeypatch)
    enqueue = AsyncMock()
    read_config = AsyncMock()
    monkeypatch.setattr(tasks.control, "enqueue", enqueue)
    monkeypatch.setattr(tasks.control, "read_config", read_config)
    report = await tasks._request(
        loop_ids=["loop-A"],
        start="2026-10-21T01:00:00+00:00",
        end="2026-10-21T02:00:00+00:00",
        kind="ASSESS:CUSTOM",
        source_task_id="manual-run-12",
        requested_at=(now + timedelta(seconds=offset_seconds)).isoformat(),
    )
    assert report == {"status": "EXPIRED"}
    enqueue.assert_not_awaited()
    read_config.assert_not_awaited()


@pytest.mark.parametrize("age_seconds", [0, 119])
async def test_background_request_preserves_source_window_and_enqueues_only_fresh_request(
    monkeypatch, age_seconds
):
    tasks, now = freeze_request_clock(monkeypatch)
    enqueue = AsyncMock(return_value={"status": "QUEUED", "id": "shadow-run-9"})
    monkeypatch.setattr(tasks.control, "enqueue", enqueue)
    report = await tasks._request(
        loop_ids=["loop-B", "loop-A"],
        start="2026-10-21T09:00:00+08:00",
        end="2026-10-21T02:00:00+00:00",
        kind="ASSESS:CUSTOM_BATCH",
        source_task_id="manual-run-13",
        requested_at=(now - timedelta(seconds=age_seconds)).isoformat(),
    )
    assert report == {"status": "QUEUED", "id": "shadow-run-9"}
    enqueue.assert_awaited_once_with(
        tasks.redis_client,
        loop_ids=["loop-B", "loop-A"],
        start=datetime(2026, 10, 21, 9, tzinfo=timezone(timedelta(hours=8))),
        end=now,
        kind="ASSESS:CUSTOM_BATCH",
        source_task_id="manual-run-13",
    )


@pytest.mark.parametrize(
    "config,expected_status",
    [
        ({"enabled": False, "loopIds": ["loop-A"]}, "DISABLED"),
        ({"enabled": True, "loopIds": ["loop-B"]}, "NOT_SELECTED"),
    ],
)
async def test_background_request_checks_current_config_before_redis_or_report(
    monkeypatch, config, expected_status
):
    tasks, now = freeze_request_clock(monkeypatch)
    read_config = AsyncMock(return_value=config)
    monkeypatch.setattr(tasks.control, "read_config", read_config)
    state_write = AsyncMock()
    save_report = Mock()
    monkeypatch.setattr(tasks.redis_client, "set", state_write)
    monkeypatch.setattr(tasks.control, "save_report", save_report)
    report = await tasks._request(
        loop_ids=["loop-A"],
        start="2026-10-21T01:00:00+00:00",
        end="2026-10-21T02:00:00+00:00",
        kind="ASSESS:SINGLE",
        source_task_id="run-14",
        requested_at=now.isoformat(),
    )
    assert report == {"status": expected_status}
    read_config.assert_awaited_once_with()
    state_write.assert_not_awaited()
    save_report.assert_not_called()
