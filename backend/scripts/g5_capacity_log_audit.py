"""Offline G5 log audit: no application imports, services, or database access.

Example (log timestamps default to Asia/Shanghai; business windows are UTC)::

    python scripts/g5_capacity_log_audit.py --log /absolute/celery-worker.log \
      --start 2026-10-13T23:17:26.900 --end 2026-10-13T23:29:55.259 \
      --task-id 31176d10-8b0f-443b-a67f-8538d49be6b3 --report /absolute/audit.json

The report is diagnostic evidence, never a capacity gate or performance PASS.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_STAMP = re.compile(r"^(?:\[[^]]+\]\s+)?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})")
_REQUEST = re.compile(r"DataPlanner.request_bundles: loop=([^,]+), .*window=([^~]+)~([^,]+),")
_COMPLETE = re.compile(r"DataPlanner 完成: loop=([^,]+),")
_L2_HIT = re.compile(r"DataPlanner L2 命中，跳过查询\+组装: loop=([^,]+),")
_ORIGIN = re.compile(r"DataPlanner 回源查询: loop=([^,]+), tagGroup=([^,]+),")
_PHASE = re.compile(
    r"DataPlanner 取数\+预处理: loop=([^,]+), tagGroup=([^,]+), points=(\d+), "
    r"query=([\d.]+)s, preprocess=([\d.]+)s"
)
_EMPTY = re.compile(r"TDengine 返回空数据: loop=([^,]+), tagGroup=([^,]+), query_time=([\d.]+)s")
_CACHE = re.compile(r"L([12]) cache (HIT|MISS): key=(?:pdb_l2|pdb):([^:]+):")
_TASK = re.compile(r"Task ([^\[]+)\[([^]]+)\] (received|succeeded in|raised unexpected)")
_ENTRY = re.compile(r"KPI 计算任务开始, celery_id=([^,]+), ts_start=([^,]+),")
_DURATION = re.compile(r"succeeded in ([\d.]+)s")
_MISSING_PROBES = [
    "broker_publish_to_worker_receive_and_prefork_reservation_wait",
    "loop_semaphore_wait_and_configuration_preload",
    "provider_layout_metadata_td_calls_reconstruction_merge",
    "td_executor_queue_execute_fetch_decode_await_resume",
    "preprocess_thread_queue_execution_await_resume",
    "cache_serialization_thread_wait_and_redis_round_trips",
    "ledger_snapshot_latest_commit_progress_session_close",
    "node_preload_aggregation_projection_commit",
    "per_pid_cpu_memory_io_pg_wait_and_actual_broker_priority_buckets",
    "nine_condition_same_round_barrier_and_submit_clock",
]


def _utc(value: str, naive_zone=UTC) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed.replace(tzinfo=naive_zone) if parsed.tzinfo is None else parsed).astimezone(UTC)


def _window(start: str, end: str | None = None) -> list[str]:
    begin = _utc(start)
    finish = _utc(end) if end else begin + timedelta(hours=1)
    return [begin.isoformat(), finish.isoformat()]


def _stats(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "isWallClock": False}
    return {
        "count": len(ordered),
        "minSeconds": min(ordered),
        "p50Seconds": statistics.median(ordered),
        "p95Seconds": ordered[math.ceil(0.95 * len(ordered)) - 1],
        "percentileMethod": "nearest-rank",
        "maxSeconds": max(ordered),
        "sumWorkSeconds": round(sum(ordered), 6),
        "isWallClock": False,
    }


def _record(raw: bytes, zone: ZoneInfo, lower: str, upper: str):
    text = raw.decode("utf-8", errors="replace").rstrip()
    stamp = _STAMP.match(text)
    if stamp:
        if not lower <= stamp[1] <= upper:
            return None
        when = datetime.strptime(stamp[1], "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=zone)
        return when.astimezone(UTC), text
    if text.startswith("{"):
        try:
            row = json.loads(text)
            return _utc(row["timestamp"], zone), str(row["message"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _trace_summary(records: list[dict], task_id: str, parse_errors: list[dict]) -> dict:
    """Preserve trace identity and incomplete clocks; never infer absent measurements."""
    target, unbound = [], []
    other_tasks = 0
    for row in records:
        identity = row.get("taskId")
        if not identity:
            unbound.append(row)
            continue
        if identity != task_id:
            other_tasks += 1
            continue
        missing = [
            key
            for key in ("stage", "spanId", "pid", "outcome", "loopId", "windowStart", "windowEnd")
            if row.get(key) is None
        ]
        thread_clock = "submitMonoNs" in row
        keys = (
            ("submitMonoNs", "threadStartMonoNs", "threadEndMonoNs", "awaitReturnMonoNs")
            if thread_clock
            else ("startMonoNs", "endMonoNs")
        )
        missing += [key for key in keys if row.get(key) is None]
        clocks = [row.get(key) for key in keys]
        numeric = all(
            isinstance(value, int | float) and not isinstance(value, bool) for value in clocks
        )
        clock_order = "INCOMPLETE"
        if numeric:
            clock_order = "ORDERED" if clocks == sorted(clocks) else "INVALID_ORDER"
            if thread_clock and row.get("outcome") == "CancelledError" and clocks[2] > clocks[3]:
                clock_order = "CALLER_CANCELLED_THREAD_FINISHED_LATER"
        target.append(
            row
            | {
                "missingFields": missing,
                "clockKind": "thread-four-points" if thread_clock else "interval",
                "clockOrder": clock_order,
            }
        )
    return {
        "newProbeObservedForTargetTask": bool(target),
        "status": "TARGET_TRACE_EXTRACTED" if target else "NO_TARGET_TRACE_RECORDS",
        "allParsedTraceCount": len(records),
        "targetTraceCount": len(target),
        "otherTaskTraceCount": other_tasks,
        "targetTraceIntervalsAndThreadClocks": target,
        "unboundTraceRecords": unbound,
        "parseErrors": parse_errors,
        "missingFieldsTargetTask": [
            {"line": row["line"], "stage": row.get("stage"), "fields": row["missingFields"]}
            for row in target
            if row["missingFields"]
        ],
        "stageCountsTargetTask": dict(Counter(row.get("stage") for row in target)),
        "limitation": (
            "Trace extraction alone proves neither query completeness nor capacity. "
            "Legacy logs without G5_TRACE have no new-probe observations; "
            "cancelled or missing clocks remain explicit."
        ),
    }


def audit_log(log: Path, *, start: str, end: str, task_id: str, log_timezone: str) -> dict:
    """Stream a local log; retain only relevant parsed events from the selected range."""
    zone = ZoneInfo(log_timezone)
    begin, finish = _utc(start, zone), _utc(end, zone)
    if finish <= begin:
        raise ValueError("end must be later than start")
    if not task_id.strip():
        raise ValueError("task-id must be nonempty")
    lower, upper = (
        t.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S,%f")[:23] for t in (begin, finish)
    )
    requests, completes, origins, phases = (defaultdict(list) for _ in range(4))
    tasks, parent_windows = {}, []
    trace_records, trace_parse_errors = [], []
    cache_counts = Counter()
    digest = hashlib.sha256()
    first_line, last_line, selected_lines = None, None, 0
    with log.open("rb") as source:
        for line_no, raw in enumerate(source, 1):
            row = _record(raw, zone, lower, upper)
            if row is None or not begin <= row[0] <= finish:
                continue
            when, text = row
            stamp = when.isoformat()
            digest.update(raw)
            selected_lines += 1
            first_line = line_no if first_line is None else first_line
            last_line = line_no
            event = {"atUtc": stamp, "line": line_no}
            if "G5_TRACE " in text:
                try:
                    trace = json.loads(text.split("G5_TRACE ", 1)[1])
                    if not isinstance(trace, dict):
                        raise ValueError("trace must be an object")
                    trace_records.append(trace | event)
                except (ValueError, TypeError):
                    trace_parse_errors.append(event | {"reason": "MALFORMED_TRACE_JSON"})
            if match := _ENTRY.search(text):
                parent_windows.append(
                    {
                        "taskId": match[1],
                        "window": None if match[2] == "None" else _window(match[2]),
                        **event,
                    }
                )
            if match := _REQUEST.search(text):
                requests[match[1]].append({"window": _window(match[2], match[3]), **event})
            if match := _COMPLETE.search(text):
                completes[match[1]].append(event)
            if match := _L2_HIT.search(text):
                completes[match[1]].append(event | {"cacheState": "L2_HIT"})
            if match := _ORIGIN.search(text):
                origins[(match[1], match[2])].append(event)
            if match := _PHASE.search(text):
                phases[match[1]].append(
                    {
                        "tagGroup": match[2],
                        "points": int(match[3]),
                        "querySeconds": float(match[4]),
                        "preprocessTotalSeconds": float(match[5]),
                        **event,
                    }
                )
            if match := _EMPTY.search(text):
                phases[match[1]].append(
                    {
                        "tagGroup": match[2],
                        "points": 0,
                        "querySeconds": float(match[3]),
                        "preprocessTotalSeconds": None,
                        "status": "EMPTY",
                        **event,
                    }
                )
            if match := _CACHE.search(text):
                cache_counts[f"L{match[1]}_{match[2]}"] += 1
            if match := _TASK.search(text):
                task = tasks.setdefault(match[2], {"id": match[2], "name": match[1]})
                state = match[3]
                task["received" if state == "received" else "terminal"] = event
                if state != "received":
                    task["terminalState"] = "SUCCESS" if state == "succeeded in" else "FAILURE"
                if duration := _DURATION.search(text):
                    task["workerRunSeconds"] = float(duration[1])
            if "TaskPool: Apply" in text:
                fields = dict(re.findall(r"'(task|id|root_id|parent_id)': '([^']+)'", text))
                if "id" in fields:
                    task = tasks.setdefault(fields["id"], {"id": fields["id"]})
                    task.update(
                        name=fields.get("task"),
                        rootId=fields.get("root_id"),
                        parentId=fields.get("parent_id"),
                        applyLine=line_no,
                    )
    selected = [p for p in parent_windows if p["taskId"] == task_id]
    issues = []
    target_window = selected[0]["window"] if len(selected) == 1 else None
    if target_window is None:
        issues.append("MISSING_OR_DUPLICATE_PARENT_WINDOW_ENTRY")
    if not selected_lines:
        issues.append("NO_TIMESTAMPED_LOG_ROWS_IN_RANGE")
    if target_window and any(
        p["taskId"] != task_id and p["window"] == target_window for p in parent_windows
    ):
        issues.append("CONCURRENT_HOURLY_TASK_SAME_WINDOW")
    per_loop, ambiguous = [], []
    for loop_id, starts in requests.items():
        if not target_window or not any(s["window"] == target_window for s in starts):
            continue
        ends = completes[loop_id]
        if len(starts) != 1 or len(ends) != 1:
            ambiguous.append({"loopId": loop_id, "requests": len(starts), "completions": len(ends)})
            continue
        a, b = starts[0], ends[0]
        seconds = (_utc(b["atUtc"]) - _utc(a["atUtc"])).total_seconds()
        if seconds < 0:
            ambiguous.append({"loopId": loop_id, "reason": "COMPLETE_BEFORE_REQUEST"})
            continue
        loop_phases = []
        for phase in phases[loop_id]:
            starts_group = origins[(loop_id, phase["tagGroup"])]
            loop_phases.append(
                phase | {"origin": starts_group[0] if len(starts_group) == 1 else None}
            )
        if b.get("cacheState") != "L2_HIT" and not loop_phases:
            issues.append("MISSING_PHASE_EVIDENCE_FOR_UNCACHED_LOOP")
        per_loop.append(
            {
                "loopId": loop_id,
                "window": target_window,
                "request": a,
                "complete": b,
                "plannerSeconds": seconds,
                "groupPhases": loop_phases,
                "skippedStages": ["query", "preprocess", "assemble"]
                if b.get("cacheState") == "L2_HIT"
                else [],
            }
        )
    if ambiguous:
        issues.append("AMBIGUOUS_OR_INCOMPLETE_LOOP_SPANS")
    parent = tasks.get(task_id, {"id": task_id})
    if "received" not in parent or "terminal" not in parent:
        issues.append("MISSING_PARENT_RECEIVE_OR_TERMINAL")
    if parent.get("terminalState") == "FAILURE":
        issues.append("PARENT_FAILURE")
    children = [
        t
        for t in tasks.values()
        if t["id"] != task_id and (t.get("parentId") == task_id or t.get("rootId") == task_id)
    ]
    if not children:
        issues.append("MISSING_BOUND_CHILD_TASK_EVIDENCE")
    if any(
        "terminal" not in child or child.get("terminalState") != "SUCCESS" for child in children
    ):
        issues.append("CHILD_NON_SUCCESS_OR_TERMINAL_MISSING")
    background = [
        t for t in tasks.values() if t["id"] != task_id and t not in children and "received" in t
    ]
    phase_rows = [p for loop in per_loop for p in loop["groupPhases"]]
    return {
        "schemaVersion": "g5-log-audit-v2",
        "analysisStatus": "INCONCLUSIVE" if issues else "LOG_AUDIT_ONLY",
        "capacityGateEvaluated": False,
        "sloEvaluated": False,
        "scope": {
            "logPath": str(log.resolve()),
            "startUtc": begin.isoformat(),
            "endUtc": finish.isoformat(),
            "logTimezone": log_timezone,
            "taskId": task_id,
            "businessWindowUtc": target_window,
            "firstLine": first_line,
            "lastLine": last_line,
            "selectedTimestampedLines": selected_lines,
            "selectedLinesSha256": digest.hexdigest(),
        },
        "correlationLimit": (
            "Old group logs omit taskId/window; association needs one request per loop "
            "and an unambiguous parent window. Nested/parallel work sums are never "
            "wall time or SLO."
        ),
        "issues": sorted(set(issues)),
        "missingProbes": _MISSING_PROBES.copy(),
        "traceAudit": _trace_summary(trace_records, task_id, trace_parse_errors),
        "parentTask": parent,
        "boundChildTasks": children,
        "backgroundTaskReceives": background,
        "backgroundTaskNameCounts": dict(Counter(t.get("name") for t in background)),
        "requestCountAllWindows": sum(len(rs) for rs in requests.values()),
        "uniqueLoopCountAllWindows": len(requests),
        "ambiguousOrIncompleteLoops": ambiguous,
        "cacheReadCountersAllTasksInRange": dict(cache_counts),
        "plannerRequestToComplete": _stats([loop["plannerSeconds"] for loop in per_loop]),
        "queryTotalIncludingMetadataAndReconstruction": _stats(
            [p["querySeconds"] for p in phase_rows]
        ),
        "preprocessTotalIncludingThreadWait": _stats(
            [
                p["preprocessTotalSeconds"]
                for p in phase_rows
                if p["preprocessTotalSeconds"] is not None
            ]
        ),
        "perLoop": per_loop,
        "queueAudit": {
            "expectedDefaultQueue": "default",
            "otherQueue": "dead_letter",
            "warning": (
                "LLEN celery cannot prove this broker is empty. Inspect actual "
                "default/dead_letter priority buckets and reserved/active; "
                "collector has not contacted Redis."
            ),
        },
    }


def validate_report_path(path: Path) -> None:
    """Validate before scanning, and forbid silently creating a wrong output directory."""
    if not path.is_absolute():
        raise ValueError("report must be an absolute path")
    if not path.parent.is_dir():
        raise ValueError("report parent directory must already exist")
    if path.is_dir():
        raise ValueError("report must be a file path")


def write_report(path: Path, report: dict) -> None:
    validate_report_path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, encoding="utf-8", delete=False
        ) as out:
            temporary = Path(out.name)
            json.dump(report, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--log-timezone", default="Asia/Shanghai")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        validate_report_path(args.report)
        if args.log.resolve() == args.report.resolve() or (
            args.log.exists() and args.report.exists() and args.log.samefile(args.report)
        ):
            raise ValueError("report must not overwrite the input log")
        report = audit_log(
            args.log,
            start=args.start,
            end=args.end,
            task_id=args.task_id,
            log_timezone=args.log_timezone,
        )
        write_report(args.report, report)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    print(f"{report['analysisStatus']}: {args.report}; capacity/SLO not evaluated")
    return 2 if report["analysisStatus"] == "INCONCLUSIVE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
