#!/usr/bin/env python3
"""Offline parse only: never imports application code or contacts services."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import re
import statistics
from pathlib import Path


def dist(values):
    vals = sorted(values)
    if not vals:
        return {"count": 0}
    return {
        "count": len(vals),
        "min": vals[0],
        "p50": statistics.median(vals),
        "p95": vals[round(0.95 * (len(vals) - 1))],
        "max": vals[-1],
        "sum_work_seconds_not_wall": sum(vals),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    start, end = (dt.datetime.fromisoformat(x) for x in (args.start, args.end))
    start_stamp, end_stamp = (x.strftime("%Y-%m-%d %H:%M:%S,%f")[:23] for x in (start, end))
    stamp = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
    req = re.compile(r"DataPlanner.request_bundles: loop=([^,]+), .*window=([^~]+)~([^,]+),")
    plan = re.compile(r"DataPlanner 查询计划: loop=([^,]+), .*tagGroups=(\d+),")
    result = re.compile(r"DataPlanner 取数\+预处理: loop=([^,]+), tagGroup=([^,]+), points=(\d+), query=([\d.]+)s, preprocess=([\d.]+)s")
    empty = re.compile(r"TDengine 返回空数据: loop=([^,]+), tagGroup=([^,]+), query_time=([\d.]+)s")
    done = re.compile(r"DataPlanner 完成: loop=([^,]+), bundles=(\d+), cached_blocks=(\d+)")
    origin = re.compile(r"DataPlanner 回源查询: loop=([^,]+), tagGroup=([^,]+),")
    cache = re.compile(r"L([12]) cache (HIT|MISS): key=(?:pdb_l2|pdb):([^:]+):")
    task = re.compile(r"Task ([^\[]+)\[([^]]+)\] received")
    requests = collections.defaultdict(list)
    plans = collections.defaultdict(list)
    completions = collections.defaultdict(list)
    queries = collections.defaultdict(list)
    origins = collections.defaultdict(list)
    empty_queries = collections.defaultdict(list)
    cache_counts = collections.Counter()
    task_received = []
    marker_lines = []
    first_line = None
    last_line = None
    lines = 0
    digest = hashlib.sha256()
    with Path(args.log).open("rb") as f:
        for no, raw in enumerate(f, 1):
            line = raw.decode("utf-8", errors="replace")
            m = stamp.match(line)
            if not m:
                continue
            if m[1] < start_stamp or m[1] > end_stamp:
                continue
            when = dt.datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S,%f")
            digest.update(raw)
            lines += 1
            first_line = no if first_line is None else min(first_line, no)
            last_line = max(last_line or no, no)
            if (m := req.search(line)):
                requests[m[1]].append({"at": when.isoformat(), "window": [m[2], m[3]], "line": no})
            if (m := plan.search(line)):
                plans[m[1]].append({"tagGroups": int(m[2]), "line": no})
            if (m := origin.search(line)):
                origins[(m[1], m[2])].append({"at": when.isoformat(), "line": no})
            if (m := result.search(line)):
                queries[m[2]].append({"loopId": m[1], "points": int(m[3]), "query": float(m[4]), "preprocess": float(m[5]), "at": when.isoformat(), "line": no})
            if (m := empty.search(line)):
                empty_queries[m[2]].append({"loopId": m[1], "query": float(m[3]), "at": when.isoformat(), "line": no})
            if (m := done.search(line)):
                completions[m[1]].append({"at": when.isoformat(), "bundles": int(m[2]), "cachedBlocks": int(m[3]), "line": no})
            if (m := cache.search(line)):
                cache_counts[f"L{m[1]}_{m[2]}"] += 1
            if (m := task.search(line)):
                task_received.append({"at": when.isoformat(), "name": m[1], "id": m[2], "line": no})
            if any(s in line for s in ("[批量计时]", "并发计算完成:", "KPI 计算任务开始,", "节点级 KPI 聚合任务开始,", "KPI 聚合任务完成:", "] succeeded in", "'parent_id': '31176d10", "'root_id': '31176d10")):
                marker_lines.append({"line": no, "text": line.rstrip()})
    spans = []
    for loop_id, starts in requests.items():
        ends = completions.get(loop_id, [])
        if len(starts) == len(ends) == 1:
            a, b = starts[0], ends[0]
            spans.append({"loopId": loop_id, "seconds": (dt.datetime.fromisoformat(b["at"]) - dt.datetime.fromisoformat(a["at"])).total_seconds(), "startLine": a["line"], "endLine": b["line"], "start": a["at"], "end": b["at"]})
    all_query = [q for qs in queries.values() for q in qs]
    all_empty = [q for qs in empty_queries.values() for q in qs]
    overlap_examples = []
    for loop in sorted(spans, key=lambda s: s["seconds"], reverse=True)[:5]:
        qs = [q | {"tagGroup": group, "queryStart": (origins.get((q["loopId"], group)) or [None])[0]} for group, rows in queries.items() for q in rows if q["loopId"] == loop["loopId"]]
        overlap_examples.append(loop | {"groupPhases": qs})
    output = {
        "scope": {"path": args.log, "startLocal": start.isoformat(), "endLocal": end.isoformat(), "firstLine": first_line, "lastLine": last_line, "timestampedLines": lines, "selectedTimestampedLinesSha256": digest.hexdigest(), "correlationLimit": "Existing phase logs omit taskId/window; correlation relies on unique request per loop and exactly one hourly-KPI window during this segment. Durations include overlap/wait. Never divide summed durations by concurrency to claim wall time."},
        "requestCount": sum(map(len, requests.values())),
        "uniqueRequestLoops": len(requests),
        "windows": sorted({tuple(r["window"]) for rs in requests.values() for r in rs}),
        "duplicateRequestLoops": {k: len(v) for k, v in requests.items() if len(v) > 1},
        "completionCount": sum(map(len, completions.values())),
        "requestToComplete": dist([s["seconds"] for s in spans]),
        "queryAggregate": dist([q["query"] for q in all_query]),
        "preprocessAggregateIncludingThreadWait": dist([q["preprocess"] for q in all_query]),
        "emptyQueryAggregate": dist([q["query"] for q in all_empty]),
        "byTagGroup": {g: {"pointsHistogram": dict(collections.Counter(q["points"] for q in qs)), "query": dist([q["query"] for q in qs]), "preprocessIncludingThreadWait": dist([q["preprocess"] for q in qs])} for g, qs in sorted(queries.items())},
        "emptyByTagGroup": {g: dist([q["query"] for q in qs]) for g, qs in sorted(empty_queries.items())},
        "cacheReadCounters": dict(cache_counts),
        "taskReceived": task_received,
        "overlapExamples": overlap_examples,
        "markerLines": marker_lines,
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in output.items() if k not in ("overlapExamples", "markerLines", "taskReceived")}, ensure_ascii=False, indent=2))
    print("taskReceived", json.dumps(task_received, ensure_ascii=False))


if __name__ == "__main__":
    main()
