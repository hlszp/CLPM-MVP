"""Read-only full-fidelity capacity sampler. It never enqueues or controls services.

Each poll's ledger/latest/node/record/attempt rows come from one repeatable-read,
read-only PG transaction. Redis/task state has explicit separate observation times.
JSON bodies are content addressed and gzip-compressed; unchanged snapshots reuse bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import asyncpg
from dotenv import dotenv_values
import httpx
import redis

from audit_capacity_inputs import encode, private_config, serial


def now():
    return datetime.now(timezone.utc).isoformat()


def task_children(payload):
    found = set()

    def visit(value):
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f-]{36}", value):
            found.add(value)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit((payload or {}).get("children", []))
    return found


def append(path, value):
    with path.open("a") as stream:
        stream.write(encode(value).decode() + "\n")


def store(out, kind, value):
    raw = encode(value)
    digest = hashlib.sha256(raw).hexdigest()
    relative = f"objects/{kind}-{digest}.json.gz"
    path = out / relative
    if not path.exists():
        path.write_bytes(gzip.compress(raw, mtime=0))
    return {
        "file": relative,
        "rawSha256": digest,
        "gzipSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": len(value) if isinstance(value, list) else None,
    }


def report_read(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def terminal_checkpoint(checkpoint):
    return bool(
        checkpoint and checkpoint.get("completionReason") not in (None, "RUNNING")
    )


def critical_complete(ledger, latest, nodes, records, ids, node_ids, start):
    """Evidence-presence trigger only; no product status/version/run acceptance gate."""

    def same_window(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed == start
        except (TypeError, ValueError):
            return False

    for rows, key, expected, window in (
        (ledger, "loop_id", set(ids), "ts_start"),
        (latest, "loop_id", set(ids), "data_ts_start"),
        (nodes, "plant_node_id", set(node_ids), "ts_start"),
    ):
        if (
            len(rows) != len(expected)
            or {str(row.get(key)) for row in rows} != expected
        ):
            return False
        if any(
            not same_window(row.get(window)) or not row.get("result_record_id")
            for row in rows
        ):
            return False
    refs = {str(row["result_record_id"]) for row in ledger + latest + nodes}
    return refs <= {str(row.get("id")) for row in records}


async def read_full_payloads(db, start, ids, node_ids, parent):
    """Caller must keep the current observation's RR READ ONLY transaction open."""
    ledger = serial(
        await db.fetch(
            "SELECT * FROM kpi_snapshot_hourly WHERE ts_start=$1 AND loop_id=ANY($2::uuid[]) ORDER BY loop_id",
            start,
            ids,
        )
    )
    latest = serial(
        await db.fetch(
            "SELECT * FROM loop_confidence_latest WHERE loop_id=ANY($1::uuid[]) ORDER BY loop_id",
            ids,
        )
    )
    nodes = serial(
        await db.fetch(
            "SELECT * FROM kpi_node_snapshot_hourly WHERE ts_start=$1 AND plant_node_id=ANY($2::uuid[]) ORDER BY plant_node_id",
            start,
            sorted(node_ids),
        )
    )
    record_ids = list(
        {
            str(row["result_record_id"])
            for row in ledger + nodes
            if row.get("result_record_id")
        }
    )
    records = serial(
        await db.fetch(
            "SELECT * FROM calculation_result_record WHERE id=ANY($1::uuid[]) OR ($2::uuid IS NOT NULL AND logical_run_id=$2::uuid AND ts_start=$3) ORDER BY object_kind,object_id,id",
            record_ids,
            parent,
            start,
        )
    )
    return {"ledger": ledger, "latest": latest, "nodes": nodes, "records": records}


def retain_full_snapshot(
    out,
    label,
    poll,
    parent,
    checkpoint,
    tx,
    pg_started,
    pg_read_completed,
    pg_ended,
    task_observed,
    values,
):
    snapshot = {
        "poll": poll,
        "parentTaskId": parent,
        "productReportObservedAt": checkpoint.get("observedAt") if checkpoint else None,
        "completionReason": checkpoint.get("completionReason") if checkpoint else None,
        "pgStartedAt": pg_started,
        "pgReadCompletedAt": pg_read_completed,
        "pgEndedAt": pg_ended,
        "transaction": tx,
        "taskObservedAt": task_observed,
        "artifacts": {
            key: store(out, label + "-" + key, value) for key, value in values.items()
        },
        "boundary": "All four complete arrays read in this observation RR READ ONLY transaction; Redis taskObservedAt is separate. Presence trigger is not the nine-condition or SLO acceptance gate.",
    }
    path = out / (label + "-payloads.json")
    with path.open("xb") as stream:
        stream.write(encode(snapshot))
    return snapshot


def cmd(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return {
            "argv": argv,
            "rc": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "rc": 124,
            "error": "resource query timeout",
            "stdout": str(exc.stdout or ""),
            "stderr": str(exc.stderr or ""),
        }


def capture_log(path, out, position, parent, children):
    if not path or not path.exists():
        return position, {"path": str(path), "available": False}
    stat = path.stat()
    rotated = position.get("inode") not in (
        None,
        stat.st_ino,
    ) or stat.st_size < position.get("offset", 0)
    start = 0 if rotated else position.get("offset", stat.st_size)
    with path.open("rb") as stream:
        stream.seek(start)
        raw = stream.read(32 * 1024 * 1024)
    end = start + len(raw)
    name = f"worker-log-{stat.st_ino}-{start}-{end}.log"
    if raw:
        (out / name).write_bytes(raw)
    requests = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if "calculate_node_kpi_hourly" not in line:
            continue
        fields = {
            key: match.group(1)
            if (
                match := re.search(
                    r"['\"]" + key + r"['\"]\s*:\s*['\"]([^'\"]+)['\"]", line
                )
            )
            else None
            for key in ("id", "root_id", "parent_id")
        }
        if (
            parent
            and (fields["root_id"] == parent or fields["parent_id"] == parent)
            and fields["id"]
        ):
            children.add(fields["id"])
            requests.append(
                {
                    "kind": "node-request",
                    "taskId": fields["id"],
                    "rootId": fields["root_id"],
                    "parentId": fields["parent_id"],
                    "sourceFile": name,
                    "line": line,
                }
            )
    if requests:
        append(out / "task-evidence.jsonl", {"capturedAt": now(), "requests": requests})
    return {"inode": stat.st_ino, "offset": end}, {
        "source": str(path),
        "inode": stat.st_ino,
        "offsetStart": start,
        "offsetEnd": end,
        "rotated": rotated,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "file": name if raw else None,
        "pendingBytes": max(0, stat.st_size - end),
    }


def redis_snapshot(clients, parent, children, source_ids):
    app, broker, backend = clients
    tasks = {}
    for task_id in ([parent] if parent else []) + sorted(children):
        raw = backend.get("celery-task-meta-" + task_id)
        tasks[task_id] = json.loads(raw) if raw else None
    if parent:
        children.update(task_children(tasks.get(parent)))
    headers = []
    for delivery, raw in broker.hgetall("unacked").items():
        message, exchange, routing = json.loads(raw)
        header = message.get("headers", {})
        task_id = header.get("id")
        if parent and (
            header.get("root_id") == parent or header.get("parent_id") == parent
        ):
            children.add(task_id)
        payload = json.loads(base64.b64decode(message["body"]))
        headers.append(
            {
                "deliveryTag": delivery,
                "score": broker.zscore("unacked_index", delivery),
                "headers": header,
                "exchange": exchange,
                "routingKey": routing,
                "args": payload[0],
                "kwargs": payload[1],
            }
        )
    queues = {
        key: broker.llen(key)
        for key in (
            "default",
            "default\x06\x163",
            "default\x06\x166",
            "default\x06\x169",
            "dead_letter",
        )
    }
    tracker = {
        task_id: app.hgetall("task:" + task_id) for task_id in sorted(source_ids)
    }
    return {
        "observedAt": now(),
        "taskMeta": tasks,
        "sourceTaskRecords": tracker,
        "unacked": headers,
        "queueLengths": queues,
        "memory": {
            str(i): {
                key: value
                for key, value in client.info("memory").items()
                if key
                in ("used_memory", "used_memory_peak", "maxmemory", "maxmemory_policy")
            }
            for i, client in enumerate(clients)
        },
    }


async def main(args):
    cfg = dotenv_values(args.env_file)
    private_config(cfg)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "objects").mkdir()
    start = datetime.fromisoformat(args.ts_start.replace("Z", "+00:00"))
    if start.tzinfo:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(hours=1)
    if end > datetime.now(timezone.utc).replace(tzinfo=None):
        raise ValueError("past complete window required")
    common = dict(
        host=cfg.get("REDIS_HOST", "localhost"),
        port=int(cfg.get("REDIS_PORT", 17103)),
        password=cfg.get("REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_timeout=5,
    )
    clients = [redis.Redis(**common, db=i) for i in (0, 1, 2)]
    db = await asyncpg.connect(
        host=cfg.get("POSTGRES_HOST", "localhost"),
        port=int(cfg.get("POSTGRES_PORT", 17102)),
        user=cfg.get("POSTGRES_USER", "clpm"),
        password=cfg["POSTGRES_PASSWORD"],
        database=cfg["POSTGRES_DB"],
        server_settings={
            "application_name": "F02-capacity-readonly",
            "statement_timeout": "20000",
        },
    )
    td = httpx.Client(
        auth=(cfg.get("TDENGINE_USER", "root"), cfg.get("TDENGINE_PASSWORD", "")),
        timeout=5,
        trust_env=False,
    )
    endpoint = f"http://{cfg.get('TDENGINE_HOST', 'localhost')}:{int(cfg.get('TDENGINE_PORT', 17104)) + 11}/rest/sql"
    report_path = Path(args.report)
    log_path = Path(args.worker_log) if args.worker_log else None
    position = (
        {"inode": log_path.stat().st_ino, "offset": log_path.stat().st_size}
        if log_path and log_path.exists()
        else {}
    )
    parent = args.parent_task_id
    children = set()
    source_ids = set()
    started = time.monotonic()
    terminal_seen = None
    poll = 0
    errors = []
    final_payloads = None
    first_terminal_payloads = None
    first_complete_payloads = None
    try:
        async with db.transaction(isolation="repeatable_read", readonly=True):
            loops = serial(
                await db.fetch(
                    "SELECT id::text,tag_name,unit_id::text,is_active,status,include_in_evaluation FROM loop_ledger WHERE tag_name LIKE 'PERF-T%' ORDER BY tag_name"
                )
            )
            tree = serial(
                await db.fetch(
                    "SELECT id::text,parent_id::text,name,type,is_kpi_enabled FROM plant_node ORDER BY id"
                )
            )
            global_count = await db.fetchval(
                "SELECT count(*) FROM loop_ledger WHERE is_active AND status='READY' AND include_in_evaluation"
            )
            identity = serial(
                await db.fetchrow(
                    "SELECT current_database() AS database,current_setting('transaction_read_only') AS readonly,txid_current_snapshot()::text AS snapshot"
                )
            )
            columns = await db.fetch(
                "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY($1::text[])",
                [
                    "kpi_snapshot_hourly",
                    "loop_confidence_latest",
                    "kpi_node_snapshot_hourly",
                    "calculation_result_record",
                ],
            )
        if not args.smoke and (len(loops) != 1000 or global_count != 1000):
            raise ValueError(
                "private capacity denominator must be exactly 1000 globally and by prefix"
            )
        ids = [row["id"] for row in loops]
        by_node = {node["id"]: node for node in tree}
        node_ids = set()
        descendants = {}
        for loop in loops:
            nid = loop["unit_id"]
            seen = set()
            while nid:
                if nid in seen:
                    raise ValueError("cycle in plant tree")
                seen.add(nid)
                node = by_node[nid]
                if node["is_kpi_enabled"]:
                    node_ids.add(nid)
                    descendants.setdefault(nid, []).append(loop["id"])
                nid = node["parent_id"]
        wanted = {
            "id",
            "loop_id",
            "plant_node_id",
            "ts_start",
            "ts_end",
            "data_ts_start",
            "data_ts_end",
            "result_record_id",
            "source",
            "source_task_id",
            "algorithm_version",
            "status",
            "score",
            "confidence_score",
            "fitness_tags",
            "fitness_detail",
            "logical_run_id",
            "object_kind",
            "object_id",
            "config_revision",
            "source_record_id",
            "created_at",
            "updated_at",
            "payload_hash",
            "input_hash",
            "result_hash",
        }
        projections = {
            table: ",".join(
                row["column_name"]
                for row in columns
                if row["table_name"] == table and row["column_name"] in wanted
            )
            for table in (
                "kpi_snapshot_hourly",
                "loop_confidence_latest",
                "kpi_node_snapshot_hourly",
                "calculation_result_record",
            )
        }
        projections["calculation_result_record"] += (
            ",payload->'fitness_tags' AS payload_fitness_tags,payload->>'snapshotStatus' AS payload_snapshot_status,payload->'loopRecordIds' AS payload_loop_record_ids"
        )
        sql_plan = {
            "ledger": {
                "sql": f"SELECT {projections['kpi_snapshot_hourly']} FROM kpi_snapshot_hourly WHERE ts_start=$1 AND loop_id=ANY($2::uuid[]) ORDER BY loop_id",
                "parameters": {"1": start, "2": "bootstrap.loops[].id"},
            },
            "latest": {
                "sql": f"SELECT {projections['loop_confidence_latest']} FROM loop_confidence_latest WHERE loop_id=ANY($1::uuid[]) ORDER BY loop_id",
                "parameters": {"1": "bootstrap.loops[].id"},
            },
            "nodes": {
                "sql": f"SELECT {projections['kpi_node_snapshot_hourly']} FROM kpi_node_snapshot_hourly WHERE ts_start=$1 AND plant_node_id=ANY($2::uuid[]) ORDER BY plant_node_id",
                "parameters": {"1": start, "2": sorted(node_ids)},
            },
            "records": {
                "sql": f"SELECT {projections['calculation_result_record']} FROM calculation_result_record WHERE id=ANY($1::uuid[]) OR ($2::uuid IS NOT NULL AND logical_run_id=$2::uuid AND ts_start=$3) ORDER BY object_kind,object_id,id",
                "parameters": {
                    "1": "current ledger+node result_record_id set",
                    "2": "observations.parentTaskId",
                    "3": start,
                },
            },
            "attempts": {
                "sql": "SELECT * FROM calculation_result_attempt WHERE logical_run_id=$1 ORDER BY attempt_no",
                "parameters": {"1": "observations.parentTaskId"},
            },
            "firstTerminalFull": {
                "mode": "First terminal checkpoint observation: SELECT * ledger/latest/nodes/records inside that same RR transaction, retain even if partial"
            },
            "firstCompleteFull": {
                "mode": "First terminal-or-later observation with actual bootstrap ID sets and current-window record references complete: SELECT * all four arrays in that same RR transaction; no product gate/status/version decision"
            },
            "finalFull": {
                "mode": "Same predicates and ordering as each critical query, SELECT * once at end; post-observation persistence evidence, not an earlier SLO witness"
            },
            "transaction": "REPEATABLE READ READ ONLY; each completed observation has one snapshot ID, no result mutations",
        }
        (out / "sql-plan.json").write_bytes(encode(sql_plan))
        (out / "bootstrap.json").write_bytes(
            encode(
                {
                    "capturedAt": now(),
                    "argv": sys.argv,
                    "envFileSha256": hashlib.sha256(
                        Path(args.env_file).read_bytes()
                    ).hexdigest(),
                    "postgresIdentity": identity,
                    "redisHost": common["host"],
                    "redisPort": common["port"],
                    "tdDatabase": cfg["TDENGINE_DB"],
                    "windowStart": start,
                    "windowEnd": end,
                    "loops": loops,
                    "tree": tree,
                    "predictedNodes": sorted(node_ids),
                    "nodeDescendantLoopIds": descendants,
                    "criticalProjections": projections,
                    "boundary": "Sampler rc does not mean business PASS; compare timestamped PG and separately observed task states. First-terminal/first-complete full arrays are captured in their own timed observation transaction, separately from final post-observation full payloads.",
                }
            )
        )
        while time.monotonic() - started < args.max_seconds:
            poll += 1
            cycle = time.monotonic()
            observed_start = now()
            checkpoint = report_read(report_path)
            if checkpoint:
                parent = checkpoint.get("parentTaskId") or parent
                (out / "product-report-last.json").write_bytes(encode(checkpoint))
            position, log_ref = capture_log(log_path, out, position, parent, children)
            pg_started = now()
            full_values = None
            capture_terminal = False
            capture_complete = False
            async with db.transaction(isolation="repeatable_read", readonly=True):
                tx = serial(
                    await db.fetchrow(
                        "SELECT current_setting('transaction_read_only') AS readonly,current_setting('transaction_isolation') AS isolation,txid_current_snapshot()::text AS snapshot,clock_timestamp() AS observed_at"
                    )
                )
                ledger = serial(
                    await db.fetch(
                        f"SELECT {projections['kpi_snapshot_hourly']} FROM kpi_snapshot_hourly WHERE ts_start=$1 AND loop_id=ANY($2::uuid[]) ORDER BY loop_id",
                        start,
                        ids,
                    )
                )
                latest = serial(
                    await db.fetch(
                        f"SELECT {projections['loop_confidence_latest']} FROM loop_confidence_latest WHERE loop_id=ANY($1::uuid[]) ORDER BY loop_id",
                        ids,
                    )
                )
                nodes = serial(
                    await db.fetch(
                        f"SELECT {projections['kpi_node_snapshot_hourly']} FROM kpi_node_snapshot_hourly WHERE ts_start=$1 AND plant_node_id=ANY($2::uuid[]) ORDER BY plant_node_id",
                        start,
                        sorted(node_ids),
                    )
                )
                record_ids = list(
                    {
                        str(row["result_record_id"])
                        for row in ledger + nodes
                        if row.get("result_record_id")
                    }
                )
                records = serial(
                    await db.fetch(
                        f"SELECT {projections['calculation_result_record']} FROM calculation_result_record WHERE id=ANY($1::uuid[]) OR ($2::uuid IS NOT NULL AND logical_run_id=$2::uuid AND ts_start=$3) ORDER BY object_kind,object_id,id",
                        record_ids,
                        parent,
                        start,
                    )
                )
                attempts = (
                    serial(
                        await db.fetch(
                            "SELECT * FROM calculation_result_attempt WHERE logical_run_id=$1 ORDER BY attempt_no",
                            parent,
                        )
                    )
                    if parent
                    else []
                )
                pg_connections = serial(
                    await db.fetch(
                        "SELECT application_name,state,wait_event_type,count(*) AS count FROM pg_stat_activity WHERE datname=current_database() GROUP BY application_name,state,wait_event_type"
                    )
                )
                capture_terminal = (
                    terminal_checkpoint(checkpoint) and first_terminal_payloads is None
                )
                capture_complete = (
                    (
                        terminal_checkpoint(checkpoint)
                        or first_terminal_payloads is not None
                    )
                    and first_complete_payloads is None
                    and critical_complete(
                        ledger, latest, nodes, records, ids, node_ids, start
                    )
                )
                if capture_terminal or capture_complete:
                    full_values = await read_full_payloads(
                        db, start, ids, node_ids, parent
                    )
                pg_read_completed = now()
            tx_end = now()
            source_ids.update(
                str(row["source_task_id"])
                for row in ledger
                if row.get("source_task_id")
            )
            redis_state = redis_snapshot(clients, parent, children, source_ids)
            references = {
                name: store(out, name, value)
                for name, value in {
                    "ledger": ledger,
                    "latest": latest,
                    "nodes": nodes,
                    "records": records,
                    "attempts": attempts,
                    "redis-tasks": redis_state,
                }.items()
            }
            full_refs = {}
            if capture_terminal:
                first_terminal_payloads = retain_full_snapshot(
                    out,
                    "first-terminal-full",
                    poll,
                    parent,
                    checkpoint,
                    tx,
                    pg_started,
                    pg_read_completed,
                    tx_end,
                    redis_state["observedAt"],
                    full_values,
                )
                full_refs["firstTerminalFullPayloads"] = first_terminal_payloads
            if capture_complete:
                first_complete_payloads = retain_full_snapshot(
                    out,
                    "first-complete-full",
                    poll,
                    parent,
                    checkpoint,
                    tx,
                    pg_started,
                    pg_read_completed,
                    tx_end,
                    redis_state["observedAt"],
                    full_values,
                )
                full_refs["firstCompleteFullPayloads"] = first_complete_payloads
            append(
                out / "observations.jsonl",
                {
                    "poll": poll,
                    "startedAt": observed_start,
                    "pgStartedAt": pg_started,
                    "pgReadCompletedAt": pg_read_completed,
                    "pgEndedAt": tx_end,
                    "pgTransaction": tx,
                    "taskObservedAt": redis_state["observedAt"],
                    "parentTaskId": parent,
                    "associatedChildren": sorted(children),
                    "artifacts": references,
                    "workerLog": log_ref,
                    "productReportObservedAt": checkpoint.get("observedAt")
                    if checkpoint
                    else None,
                    **full_refs,
                },
            )
            append(
                out / "task-evidence.jsonl",
                {
                    "poll": poll,
                    "parentTaskId": parent,
                    "associatedChildren": sorted(children),
                    "raw": references["redis-tasks"],
                },
            )
            resource = {
                "poll": poll,
                "observedAt": now(),
                "postgresConnections": pg_connections,
                "redisMemory": redis_state["memory"],
                "queueLengths": redis_state["queueLengths"],
                "unackedCount": len(redis_state["unacked"]),
            }
            if args.containers:
                resource["dockerStats"] = cmd(
                    [
                        "docker",
                        "stats",
                        "--no-stream",
                        "--format",
                        "{{json .}}",
                        *args.containers,
                    ]
                )
                resource["containerCgroups"] = {}
                for container in args.containers:
                    raw = cmd(
                        [
                            "docker",
                            "exec",
                            container,
                            "cat",
                            "/sys/fs/cgroup/memory.current",
                            "/sys/fs/cgroup/memory.stat",
                            "/sys/fs/cgroup/memory.events",
                        ]
                    )
                    lines = raw.get("stdout", "").splitlines()
                    fields = {}
                    for line in lines[1:]:
                        pieces = line.split()
                        if len(pieces) == 2 and pieces[1].isdigit():
                            fields[pieces[0]] = int(pieces[1])
                    resource["containerCgroups"][container] = {
                        "raw": raw,
                        "memoryCurrentBytes": int(lines[0])
                        if lines and lines[0].isdigit()
                        else None,
                        "anonBytes": fields.get("anon"),
                        "shmemBytes": fields.get("shmem"),
                        "fields": fields,
                        "boundary": "memory.current includes all cgroup charges; stat anon/shmem are reported separately, do not sum overlapping memory fields or omit PG.",
                    }
            if args.td_container:
                resource["tdProcMeminfo"] = cmd(
                    ["docker", "exec", args.td_container, "cat", "/proc/meminfo"]
                )
                resource["tdCgroupEvents"] = cmd(
                    [
                        "docker",
                        "exec",
                        args.td_container,
                        "cat",
                        "/sys/fs/cgroup/memory.events",
                    ]
                )
            ps = cmd(["ps", "-ax", "-o", "pid=,ppid=,pcpu=,rss=,state=,command="])
            runtime_marker = (
                str(log_path.parent.parent) if log_path else args.runtime_marker
            )
            resource["ownedProcesses"] = [
                line
                for line in ps.get("stdout", "").splitlines()
                if runtime_marker
                and runtime_marker in line
                and "collect_capacity_evidence.py" not in line
            ]
            try:
                response = td.post(endpoint, content=b"SHOW QUERIES")
                resource["tdActiveQueries"] = {
                    "httpStatus": response.status_code,
                    "response": response.json(),
                }
            except Exception as exc:
                resource["tdActiveQueries"] = {
                    "error": type(exc).__name__ + ": " + str(exc)
                }
            resource["completedAt"] = now()
            append(out / "resources.jsonl", resource)
            if args.smoke:
                break
            if terminal_checkpoint(checkpoint):
                if terminal_seen is None:
                    terminal_seen = time.monotonic()
                if time.monotonic() - terminal_seen >= args.post_seconds:
                    break
            await asyncio.sleep(max(0, args.interval - (time.monotonic() - cycle)))
        position, log_ref = capture_log(log_path, out, position, parent, children)
        append(out / "observations.jsonl", {"finalLog": log_ref, "capturedAt": now()})
        async with db.transaction(isolation="repeatable_read", readonly=True):
            full_tx = serial(
                await db.fetchrow(
                    "SELECT current_setting('transaction_read_only') AS readonly,txid_current_snapshot()::text AS snapshot,clock_timestamp() AS observed_at"
                )
            )
            final_values = await read_full_payloads(db, start, ids, node_ids, parent)
        final_payloads = {
            "transaction": full_tx,
            "artifacts": {
                key: store(out, "full-" + key, value)
                for key, value in final_values.items()
            },
        }
        (out / "final-full-payloads.json").write_bytes(encode(final_payloads))
        status = (
            "SMOKE_ONLY"
            if args.smoke
            else "COLLECTED"
            if terminal_seen is not None
            else "COLLECTION_TIMEOUT"
        )
    except BaseException as exc:
        status = "COLLECTION_FAILED"
        errors.append({"type": type(exc).__name__, "message": str(exc)})
    finally:
        await db.close()
        td.close()
        for client in clients:
            client.close()
    summary = {
        "status": status,
        "polls": poll,
        "parentTaskId": parent,
        "associatedChildren": sorted(children),
        "finishedAt": now(),
        "errors": errors,
        "firstTerminalFullPayloads": first_terminal_payloads,
        "firstCompleteFullPayloads": first_complete_payloads,
        "finalFullPayloads": final_payloads,
        "boundary": "COLLECTED is evidence collection completion, never a business PASS; post-observation finalFull must not be backdated or substituted for an earlier SLO witness",
    }
    (out / "collector-summary.json").write_bytes(encode(summary))
    manifest = [
        {
            "file": str(path.relative_to(out)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(out.rglob("*"))
        if path.is_file()
    ]
    (out / "sha256-manifest.json").write_bytes(encode({"files": manifest}))
    print(json.dumps(summary))
    return 0 if status in ("COLLECTED", "SMOKE_ONLY") else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--env-file", required=True)
    p.add_argument("--ts-start", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--parent-task-id")
    p.add_argument("--worker-log")
    p.add_argument("--runtime-marker", default="clpm-g5-runtime-20261015")
    p.add_argument("--interval", type=float, default=5)
    p.add_argument("--post-seconds", type=float, default=300)
    p.add_argument("--max-seconds", type=float, default=2700)
    p.add_argument("--containers", nargs="*")
    p.add_argument("--td-container", default="clpm-mvp-tdengine")
    p.add_argument(
        "--smoke",
        action="store_true",
        help="One read-only query cycle; may inspect an empty new DB. Emits SMOKE_ONLY, never a capacity result.",
    )
    raise SystemExit(asyncio.run(main(p.parse_args())))
