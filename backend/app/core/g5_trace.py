"""Optional G5 timings; no queries, payloads or changes to business errors."""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from uuid import uuid4

logger = logging.getLogger(__name__)
_context: ContextVar[dict | None] = ContextVar("g5_trace_context", default=None)


def enabled() -> bool:
    return os.environ.get("CLPM_G5_TRACE", "0") == "1"


def _emit(event: dict) -> None:
    try:
        logger.info("G5_TRACE %s", json.dumps(event, separators=(",", ":"), default=str))
    except Exception:  # noqa: BLE001 — observability must not replace a business error
        pass


@contextmanager
def span(name: str, **identity):
    """Record a bounded interval, linking nested and concurrent spans by identity."""
    if not enabled():
        yield
        return
    previous = _context.get() or {}
    current = {**previous, **{k: v for k, v in identity.items() if v is not None}}
    parent_id = current.get("spanId")
    current["spanId"] = uuid4().hex
    token = _context.set(current)
    start = time.perf_counter_ns()
    start_epoch = time.time_ns()
    outcome = "ok"
    try:
        yield
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        end = time.perf_counter_ns()
        _context.reset(token)
        _emit(
            {
                **current,
                "stage": name,
                "parentSpanId": parent_id,
                "pid": os.getpid(),
                "threadId": threading.get_ident(),
                "startMonoNs": start,
                "endMonoNs": end,
                "startEpochNs": start_epoch,
                "elapsedMs": (end - start) / 1e6,
                "outcome": outcome,
            }
        )


def traced(name: str):
    """Trace an async function using only stable IDs/window, never arbitrary kwargs."""

    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapped(*args, **kwargs):
            if not enabled():
                return await fn(*args, **kwargs)
            values = signature.bind_partial(*args, **kwargs).arguments
            loop = values.get("loop")
            window = values.get("time_window")
            task = values.get("task")
            with span(
                name,
                taskId=values.get("logical_run_id"),
                loopId=values.get("loop_id") or (str(loop.id) if loop is not None else None),
                windowStart=values.get(
                    "ts_start", values.get("start", getattr(window, "start", None))
                ),
                windowEnd=values.get("ts_end", values.get("end", getattr(window, "end", None))),
                tagGroup=getattr(getattr(task, "tag_group", None), "value", None),
            ):
                return await fn(*args, **kwargs)

        return wrapped

    return decorate


def traced_sync(name: str):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if not enabled():
                return fn(*args, **kwargs)
            with span(name):
                return fn(*args, **kwargs)

        return wrapped

    return decorate


async def awaited(name: str, operation, **identity):
    with span(name, **identity):
        return await operation


def completed_interval(name: str, start_ns: int | None, **identity) -> None:
    if start_ns is not None and enabled():
        end_ns = time.perf_counter_ns()
        _emit(
            {
                **identity,
                "spanId": uuid4().hex,
                "parentSpanId": (_context.get() or {}).get("spanId"),
                "stage": name,
                "pid": os.getpid(),
                "startMonoNs": start_ns,
                "endMonoNs": end_ns,
                "elapsedMs": (end_ns - start_ns) / 1e6,
                "outcome": "ok",
            }
        )


async def threaded(name: str, fn, *args, executor=None):
    """Use the original executor; distinguish queue, execution and await resumption."""
    if not enabled():
        if executor is None:
            return await asyncio.to_thread(fn, *args)
        return await asyncio.get_running_loop().run_in_executor(executor, fn, *args)
    previous = dict(_context.get() or {})
    identity = {**previous, "parentSpanId": previous.get("spanId"), "spanId": uuid4().hex}
    token = _context.set(identity)
    try:
        context = copy_context()
    finally:
        _context.reset(token)
    submit = time.perf_counter_ns()
    clocks: dict[str, int] = {}

    def run():
        clocks["threadStartMonoNs"] = time.perf_counter_ns()
        cpu = time.thread_time_ns()
        try:
            with span(name + ".execute"):
                return fn(*args)
        finally:
            clocks["threadEndMonoNs"] = time.perf_counter_ns()
            clocks["threadCpuNs"] = time.thread_time_ns() - cpu

    outcome = "ok"
    try:
        if executor is None:
            return await asyncio.to_thread(context.run, run)
        return await asyncio.get_running_loop().run_in_executor(executor, context.run, run)
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        returned = time.perf_counter_ns()
        start, end = clocks.get("threadStartMonoNs"), clocks.get("threadEndMonoNs")
        _emit(
            {
                **identity,
                "stage": name,
                "pid": os.getpid(),
                "submitMonoNs": submit,
                **clocks,
                "awaitReturnMonoNs": returned,
                "queueMs": (start - submit) / 1e6 if start is not None else None,
                "executionMs": (end - start) / 1e6
                if start is not None and end is not None
                else None,
                "resumeMs": (
                    (returned - end) / 1e6
                    if end is not None and end <= returned and outcome != "CancelledError"
                    else None
                ),
                "threadFinishedAfterAwaitReturn": end > returned if end is not None else None,
                "outcome": outcome,
            }
        )
