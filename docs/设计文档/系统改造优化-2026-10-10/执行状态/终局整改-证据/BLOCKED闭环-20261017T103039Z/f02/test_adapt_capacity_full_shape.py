"""A/B1000/20-node synthetic disk shapes; no actual CLPM business execution."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import uuid

import pytest

from adapt_capacity_artifacts import Adapter, MissingEvidence, canonical, digest
from test_adapt_capacity_artifacts import pool_fixture
from test_adapt_capacity_native import native_capture

START = "2026-10-15T12:00:00"
END = "2026-10-15T13:00:00"
PARENT = str(uuid.UUID(int=90001))
CHILD = str(uuid.UUID(int=90002))
TRACKER = str(uuid.UUID(int=90003))


def fixture(tmp_path, scenario):
    subject = Adapter(tmp_path, tmp_path / "sidecars")
    source = tmp_path / "original"
    source.mkdir()
    raw = source / "raw"
    raw.mkdir()
    runtime_template, resource_samples = pool_fixture()

    def file(name, body):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical(body))
        return subject.ref(path)

    def object(name, body):
        content = canonical(body)
        path = raw / "objects" / (name + "-" + digest(content) + ".json.gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(content, mtime=0))
        return {
            "file": str(path.relative_to(raw)),
            "rawSha256": digest(content),
            "gzipSha256": digest(path.read_bytes()),
            "rows": len(body) if isinstance(body, list) else None,
        }

    loops = {}
    scopes = {str(uuid.UUID(int=50000 + i)): [] for i in range(20)}
    node_ids = sorted(scopes)
    ledger = []
    latest = []
    nodes = []
    records = []
    manifest_rows = []
    eligible = []
    states = {}
    for index in range(1000):
        loop = str(uuid.UUID(int=index + 1))
        record = str(uuid.UUID(int=100000 + index))
        tag = f"PERF-T{index + 1:04d}"
        node = node_ids[index % 20]
        loops[loop] = {"id": loop, "tag_name": tag, "unit_id": node}
        scopes[node].append(loop)
        composition = (
            "normal"
            if scenario == "a" or index < 800
            else "insufficient"
            if index < 900
            else "manual"
            if index < 950
            else "gap"
        )
        insufficient = composition in {"insufficient", "gap"}
        status = "INCONCLUSIVE" if insufficient else "SUCCESS"
        expected = "INCONCLUSIVE/DATA_INSUFFICIENT" if insufficient else "EVALUATED"
        fitness = {"tags": ["DATA_INSUFFICIENT"]} if insufficient else None
        score = None if insufficient else "95.00"
        states[loop] = "incomplete" if insufficient else "scorable"
        manifest_rows.append(
            {"loopTag": tag, "composition": composition, "expectedStatus": expected}
        )
        eligible.append(
            {
                "loopId": loop,
                "loopTag": tag,
                "composition": composition,
                "expectedStatus": expected,
                "expectedSnapshotStatus": status,
            }
        )
        ledger.append(
            {
                "id": "ledger-" + loop,
                "loop_id": loop,
                "ts_start": START,
                "ts_end": END,
                "status": status,
                "score": score,
                "source": "SCHEDULED",
                "source_task_id": TRACKER,
                "result_record_id": record,
                "algorithm_version": "fixture-v",
                "fitness_tags": fitness,
            }
        )
        latest.append(
            {
                "id": "latest-" + loop,
                "loop_id": loop,
                "data_ts_start": START,
                "data_ts_end": END,
                "algorithm_version": "fixture-v",
                "result_record_id": record,
            }
        )
        records.append(
            {
                "id": record,
                "object_kind": "LOOP",
                "object_id": loop,
                "logical_run_id": PARENT,
                "ts_start": START,
                "ts_end": END,
                "status": "COMPLETED",
                "algorithm_version": "fixture-v",
                "created_at": "2026-10-17T08:00:20",
                "payload": {
                    "snapshotStatus": status,
                    "fitness_tags": fitness,
                    "originalZero": 0,
                    "originalNull": None,
                },
            }
        )
    by_loop = {row["loop_id"]: row for row in ledger}
    for index, (node, scope) in enumerate(scopes.items()):
        record = str(uuid.UUID(int=200000 + index))
        nodes.append(
            {
                "plant_node_id": node,
                "ts_start": START,
                "ts_end": END,
                "algorithm_version": "fixture-v",
                "result_record_id": record,
                "status": "GOOD",
                "score": "95.00",
            }
        )
        counts = Counter(states[loop] for loop in scope)
        payload = {
            "loopRecordIds": [
                {"loopId": loop, "recordId": by_loop[loop]["result_record_id"]}
                for loop in scope
                if states[loop] == "scorable"
            ],
            "assessment_stats": {
                "roster": {
                    "members": [
                        {"loopId": loop, "state": states[loop]} for loop in scope
                    ],
                    "states": {
                        key: counts.get(key, 0)
                        for key in (
                            "scorable",
                            "incomplete",
                            "failed",
                            "cancelled",
                            "pending",
                            "missing",
                        )
                    },
                }
            },
        }
        records.append(
            {
                "id": record,
                "object_kind": "NODE",
                "object_id": node,
                "logical_run_id": str(uuid.UUID(int=300000 + index)),
                "ts_start": START,
                "ts_end": END,
                "status": "COMPLETED",
                "algorithm_version": "fixture-v",
                "created_at": "2026-10-17T08:00:21",
                "payload": payload,
            }
        )
    arrays = {"ledger": ledger, "latest": latest, "nodes": nodes, "records": records}
    refs = {name: object(name, rows) for name, rows in arrays.items()}
    redis = {
        "observedAt": "2026-10-17T08:00:25.030+00:00",
        "taskMeta": {
            PARENT: {
                "status": "SUCCESS",
                "date_done": "2026-10-17T08:00:20+00:00",
                "result": {"total": 1000},
            },
            CHILD: {
                "status": "SUCCESS",
                "date_done": "2026-10-17T08:00:22+00:00",
                "result": {
                    "total": 20,
                    "success": 20,
                    "skipped": 0,
                    "failed": 0,
                    "cancelled": 0,
                },
            },
        },
        "sourceTaskRecords": {
            TRACKER: {"task_id": TRACKER, "celery_task_id": PARENT, "status": "SUCCESS"}
        },
    }
    refs["redis-tasks"] = object("redis-tasks", redis)
    tx = {
        "readonly": "on",
        "isolation": "repeatable read",
        "snapshot": "synthetic-one-snapshot",
        "observed_at": "2026-10-17T08:00:25+00:00",
    }
    log = (
        b"Synthetic fixture: all source rows are test data, no business task was run.\n"
    )
    log_name = "worker-log-1-0-" + str(len(log)) + ".log"
    (raw / log_name).write_bytes(log)
    log_ref = {
        "source": "synthetic-worker.log",
        "inode": 1,
        "offsetStart": 0,
        "offsetEnd": len(log),
        "bytes": len(log),
        "sha256": digest(log),
        "pendingBytes": 0,
        "rotated": False,
        "file": log_name,
    }
    observation = {
        "poll": 1,
        "parentTaskId": PARENT,
        "pgStartedAt": "2026-10-17T08:00:24.900+00:00",
        "pgReadCompletedAt": "2026-10-17T08:00:25.010+00:00",
        "pgEndedAt": "2026-10-17T08:00:25.020+00:00",
        "taskObservedAt": redis["observedAt"],
        "pgTransaction": tx,
        "artifacts": refs,
        "workerLog": log_ref,
    }
    snapshot = {
        key: deepcopy(observation[key])
        for key in (
            "poll",
            "parentTaskId",
            "pgStartedAt",
            "pgReadCompletedAt",
            "pgEndedAt",
            "taskObservedAt",
        )
    }
    snapshot.update(
        transaction=tx, artifacts={k: v for k, v in refs.items() if k != "redis-tasks"}
    )
    observation["firstCompleteFullPayloads"] = snapshot
    for name, body in [
        (
            "bootstrap.json",
            {
                "loops": list(loops.values()),
                "predictedNodes": node_ids,
                "redisHost": "127.0.0.1",
                "redisPort": 17123,
            },
        ),
        ("collector-summary.json", {"status": "COLLECTED"}),
        ("first-complete-full-payloads.json", snapshot),
        (
            "final-full-payloads.json",
            {
                "transaction": {"snapshot": "later-final"},
                "artifacts": snapshot["artifacts"],
            },
        ),
    ]:
        (raw / name).write_bytes(canonical(body))
    (raw / "observations.jsonl").write_bytes(canonical(observation) + b"\n")
    (raw / "resources.jsonl").write_bytes(
        b"".join(canonical(sample) + b"\n" for sample in resource_samples)
    )
    raw_files = [
        {
            "file": str(p.relative_to(raw)),
            "bytes": p.stat().st_size,
            "sha256": digest(p.read_bytes()),
        }
        for p in sorted(raw.rglob("*"))
        if p.is_file()
    ]
    (raw / "sha256-manifest.json").write_bytes(canonical({"files": raw_files}))
    manifest = file("manifest.json", {"scenario": scenario, "rows": manifest_rows})
    freeze = file(
        "freeze.json",
        {
            "frozenAt": "2026-10-17T07:59:00+00:00",
            "window": {"tsStart": START, "tsEnd": END},
            "manifest": {"sha256": manifest["sha256"]},
            "eligible": eligible,
            "excluded": [],
            "predictedNodes": node_ids,
            "algorithmVersion": "fixture-v",
        },
    )
    runtime_members = runtime_template["members"]
    native = native_capture(source, runtime_members, file)
    runtime = file(
        "runtime.json",
        {
            "runtime": {
                "members": runtime_members,
                "startRecord": str(source / "native-launch.json"),
                "startRecordSha256": native["launch"]["sha256"],
            },
            "envFileSha256": "synthetic-env",
            "taosConfigSha256": "synthetic-taos",
            "schemaRevision": "synthetic-revision",
            "postgres": {"database": "clpm_capacity_synthetic"},
            "tdResponses": [
                {
                    "observedAt": "2026-10-17T07:59:00+00:00",
                    "httpStatus": 200,
                    "response": {
                        "code": 0,
                        "column_meta": [
                            ["dnode_id", "INT", 4],
                            ["name", "VARCHAR", 32],
                            ["value", "VARCHAR", 100],
                        ],
                        "data": [[1, "queryUseMemoryPool", "0"]],
                        "rows": 1,
                    },
                }
            ],
        },
    )
    events = []
    for identity, name, kwargs, parent in [
        (PARENT, "app.tasks.kpi_calc.calculate_hourly_kpi", {"ts_start": START}, None),
        (
            CHILD,
            "app.tasks.kpi_calc.calculate_node_kpi_hourly",
            {"ts_start": START, "ts_end": END},
            PARENT,
        ),
    ]:
        events.extend(
            [
                {
                    "event": {
                        "type": "task-received",
                        "uuid": identity,
                        "name": name,
                        "kwargs": repr(kwargs),
                        "parent_id": parent,
                        "root_id": PARENT,
                        "pid": 101,
                        "hostname": "synthetic@host",
                        "timestamp": datetime(
                            2026, 10, 17, 8, 0, 1, tzinfo=timezone.utc
                        ).timestamp(),
                    }
                },
                {
                    "event": {
                        "type": "task-started",
                        "uuid": identity,
                        "pid": 101,
                        "hostname": "synthetic@host",
                        "timestamp": datetime(
                            2026, 10, 17, 8, 0, 2, tzinfo=timezone.utc
                        ).timestamp(),
                    }
                },
            ]
        )
    events_path = source / "events.jsonl"
    events_path.write_bytes(b"".join(canonical(e) + b"\n" for e in events))
    timed = [
        {
            "observedAt": "2026-10-17T08:00:00+00:00",
            "line": f"[submit] calculate_hourly_kpi.delay(ts_start='{START}') synthetic\n",
        },
        {
            "observedAt": "2026-10-17T08:00:00.010+00:00",
            "line": "[submit] parent_task_id=" + PARENT + "\n",
        },
    ]
    timed_path = source / "benchmark-timestamped.jsonl"
    timed_path.write_bytes(b"".join(canonical(row) + b"\n" for row in timed))
    stdout = source / "benchmark.stdout.log"
    stdout.write_text("".join(r["line"] for r in timed))
    stderr = source / "benchmark.stderr.log"
    stderr.write_bytes(b"")
    command = file(
        "benchmark-command.json",
        {
            "argv": [
                "python",
                "kpi_capacity_benchmark.py",
                "--mode",
                "standard-hourly",
                "--expect-eligible",
                "1000",
                "--slo-seconds",
                "600",
                "--timeout",
                "2400",
                "--barrier-loop-prefix",
                "PERF-T",
            ],
            "startedAt": "2026-10-17T07:59:59+00:00",
            "finishedAt": "2026-10-17T08:00:25+00:00",
            "rc": 0,
        },
    )
    source_proof = file("source.json", {"synthetic": True})
    cache = {
        axis: {
            "state": "synthetic fixture only",
            "definition": "Not an actual cold/hot result",
            "rawRefs": [source_proof],
        }
        for axis in ("input", "precompute", "redis", "process")
    }
    item = {
        "batch": 1,
        "collectorManifest": subject.ref(raw / "sha256-manifest.json"),
        "manifest": manifest,
        "freeze": freeze,
        "runtimeProfile": runtime,
        "events": subject.ref(events_path),
        "nativeIdentityObservations": native["summary"],
        "benchmarkTimestamped": subject.ref(timed_path),
        "benchmarkStdout": subject.ref(stdout),
        "benchmarkStderr": subject.ref(stderr),
        "benchmarkCommand": command,
        "cacheVector": cache,
    }
    scope = {
        "config": {
            "versions": {"algorithmVersion": "fixture-v"},
            "configDigest": "synthetic-digest",
            "envFileSha256": "synthetic-env",
        },
        "loops": loops,
        "scopes": scopes,
        "scopeRef": source_proof,
        "querySourceRef": source_proof,
        "scopeAfter": {"observedAt": "2026-10-17T08:01:00+00:00"},
    }
    plan = {
        "candidateSha": "a" * 40,
        "redisHost": "127.0.0.1",
        "redisPort": 17123,
        "runtimeStartTimezone": "Asia/Shanghai",
        "nativeIdentitySamplerSource": native["sampler"],
        "nativeInterpreterObservation": native["interpreter"],
        "runtimeCodeIdentityRefs": [
            file("synthetic-tracked-code-catalog.json", {"syntheticFixtureOnly": True}),
            file("synthetic-committed-byte-check.json", {"syntheticFixtureOnly": True}),
        ],
        "runtimeTimezoneObservation": file(
            "timezone.json",
            {
                "observedAt": "2026-10-17T07:59:00+00:00",
                "localIso": "2026-10-17T15:59:00+08:00",
                "pythonTimezoneSecondsWest": -28800,
                "etcLocaltimeSymlink": "/synthetic/zoneinfo/Asia/Shanghai",
                "dateRaw": "2026-10-17T15:59:00 CST +0800\n",
                "psSelfRaw": "synthetic raw self ps, fixture only\n",
            },
        ),
        "configurationIsolation": source_proof,
        "nodeScopeAfter": source_proof,
        "celeryEventSources": [source_proof, source_proof],
    }
    planned = {
        "batch": 1,
        "scenario": scenario,
        "cache": "cold",
        "ts_start": START,
        "ts_end": END,
    }
    return subject, item, scope, plan, planned, source_proof


@pytest.mark.parametrize("scenario", ["a", "b"])
def test_full1000_twenty_node_shape_maps_raw_without_product_gates(tmp_path, scenario):
    subject, item, scope, plan, planned, profile = fixture(tmp_path, scenario)
    before = {
        p: digest(p.read_bytes())
        for p in (tmp_path / "original").rglob("*")
        if p.is_file()
    }
    result = subject.batch(item, planned, plan, scope, profile)
    witness = subject.json(result["witness"])
    barrier = witness["barrier"]
    assert len(barrier["ledger_rows"]) == len(barrier["latest_rows"]) == 1000
    assert len(barrier["node_rows"]) == 20 and len(barrier["record_rows"]) == 1020
    assert Counter(r["status"] for r in barrier["ledger_rows"]) == (
        {"SUCCESS": 1000} if scenario == "a" else {"SUCCESS": 850, "INCONCLUSIVE": 150}
    )
    assert witness["submittedAt"] == "2026-10-17T08:00:00+00:00"
    assert witness["observedAt"] == "2026-10-17T08:00:25.030+00:00"
    assert (
        witness["originalObservation"]["pgTransaction"]["snapshot"]
        == "synthetic-one-snapshot"
    )
    assert witness["fullPayloadProvenance"]["sameObservationFullCaptures"] == [
        "firstCompleteFullPayloads"
    ]
    assert witness["barrier"]["ledger_rows"][0]["originalRow"]["score"] == "95.00"
    assert (
        len(next(t for t in witness["celeryTasks"] if t["id"] == CHILD)["nodeIds"])
        == 20
    )
    assert before == {p: digest(p.read_bytes()) for p in before}
    subject.reads.verify_unchanged()


def test_full1000_otherwise_complete_batch_without_native_originals_stays_blocked(
    tmp_path,
):
    subject, item, scope, plan, planned, profile = fixture(tmp_path, "a")
    del item["nativeIdentityObservations"]
    with pytest.raises(MissingEvidence, match="summary reference missing"):
        subject.batch(item, planned, plan, scope, profile)
    assert not (subject.out / "batch-01/witness.json").exists()
