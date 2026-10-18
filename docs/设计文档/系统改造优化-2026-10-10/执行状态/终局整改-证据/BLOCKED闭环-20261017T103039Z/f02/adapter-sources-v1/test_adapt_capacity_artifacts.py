"""Synthetic disk-only adapter tests, never CLPM or real capacity acceptance."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip

import pytest

from adapt_capacity_artifacts import (
    Adapter,
    InvalidEvidence,
    MissingEvidence,
    canonical,
    closed_worker_pool,
    compare_projection,
    digest,
    map_rows,
    score_number,
    submit_from_timestamped,
)

START = "2026-10-15T12:00:00"
END = "2026-10-15T13:00:00"
OBS = "2026-10-17T08:00:25+00:00"
PARENT = "parent-original"
CHILD = "child-original"


def adapter(tmp_path):
    return Adapter(tmp_path, tmp_path / "sidecars")


def put(root, name, body):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(body))
    return {
        "path": name,
        "sha256": digest(path.read_bytes()),
        "size": path.stat().st_size,
    }


def obj(root, indexed, name, body):
    raw = canonical(body)
    content = gzip.compress(raw, mtime=0)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    indexed[name] = {"path": name, "sha256": digest(content), "size": len(content)}
    return {
        "file": name,
        "rawSha256": digest(raw),
        "gzipSha256": digest(content),
        "rows": len(body) if isinstance(body, list) else None,
    }


def test_numeric_alias_preserves_zero_null_and_original_decimal_value():
    assert score_number("0.00") == 0.0
    assert score_number(None) is None
    assert score_number("95.20") == 95.2
    for value in (True, "NaN", "Infinity", "0.123456789012345678901"):
        with pytest.raises(InvalidEvidence):
            score_number(value)


def test_field_mapping_keeps_original_row_and_derives_run_only_from_actual_fk_record():
    row = {
        "loop_id": "loop",
        "ts_start": START,
        "ts_end": END,
        "source_task_id": "tracker",
        "result_record_id": "record",
        "algorithm_version": "v",
        "fitness_tags": None,
        "score": "0.00",
        "status": "SUCCESS",
        "source": "SCHEDULED",
    }
    original = deepcopy(row)
    result = map_rows("ledger", [row], {"record": {"logical_run_id": PARENT}})[0]
    assert row == original and result["originalRow"] == original
    assert result["score"] == 0 and result["originalRow"]["score"] == "0.00"
    assert result["recordLogicalRunId"] == PARENT
    del row["ts_end"]
    with pytest.raises(MissingEvidence, match="ts_end"):
        map_rows("ledger", [row], {"record": {"logical_run_id": PARENT}})


def test_full_record_must_match_every_critical_column_including_real_payload_aliases():
    full = {
        "id": "r",
        "created_at": "2026-10-17T08:00:20",
        "payload": {
            "fitness_tags": None,
            "snapshotStatus": "SUCCESS",
            "loopRecordIds": [],
        },
    }
    compare_projection(
        {
            "id": "r",
            "payload_fitness_tags": None,
            "payload_snapshot_status": "SUCCESS",
            "payload_loop_record_ids": [],
        },
        full,
    )
    with pytest.raises(InvalidEvidence, match="critical column"):
        compare_projection({"id": "r", "payload_snapshot_status": "INCONCLUSIVE"}, full)
    with pytest.raises(MissingEvidence, match="column"):
        compare_projection({"absent_actual_column": None}, full)


def test_submit_uses_exact_original_pre_call_line_not_parent_log_or_first_met():
    rows = [
        {
            "observedAt": "2026-10-17T08:00:00+00:00",
            "line": f"[submit] calculate_hourly_kpi.delay(ts_start='{START}') original\n",
        },
        {
            "observedAt": "2026-10-17T08:00:00.050+00:00",
            "line": "[submit] parent_task_id=" + PARENT + "\n",
        },
    ]
    value, proof = submit_from_timestamped(rows, {"tsStart": START}, PARENT)
    assert value == rows[0]["observedAt"] and proof["preSubmitLine"] == rows[0]
    with pytest.raises(MissingEvidence):
        submit_from_timestamped(rows[1:], {"tsStart": START}, PARENT)
    with pytest.raises(MissingEvidence):
        submit_from_timestamped(rows + rows[:1], {"tsStart": START}, PARENT)


def test_original_compressed_object_hash_size_and_row_count_all_bind(tmp_path):
    subject = adapter(tmp_path)
    indexed = {}
    ref = obj(tmp_path, indexed, "original.gz", [{"original": None}, {"original": 0}])
    assert subject.object(tmp_path, indexed, ref) == [
        {"original": None},
        {"original": 0},
    ]
    with pytest.raises(InvalidEvidence, match="denominator"):
        subject.object(tmp_path, indexed, {**ref, "rows": 1})
    with pytest.raises(InvalidEvidence, match="SHA"):
        subject.object(tmp_path, indexed, {**ref, "rawSha256": "a" * 64})
    (tmp_path / "original.gz").write_bytes(b"changed")
    with pytest.raises(InvalidEvidence, match="SHA"):
        subject.object(tmp_path, indexed, ref)


def observation_fixture(tmp_path):
    indexed = {}
    full = {
        "id": "record",
        "status": "COMPLETED",
        "created_at": "2026-10-17T08:00:20",
        "payload": {"original": 0},
    }
    raw = {
        "ledger": [{"loop_id": "loop", "result_record_id": "record", "score": "95.00"}],
        "latest": [
            {"loop_id": "loop", "result_record_id": "record", "data_ts_start": START}
        ],
        "nodes": [],
        "records": [
            {"id": "record", "status": "COMPLETED", "created_at": "2026-10-17T08:00:20"}
        ],
    }
    observation = {
        "poll": 1,
        "parentTaskId": PARENT,
        "pgStartedAt": "2026-10-17T08:00:24+00:00",
        "pgReadCompletedAt": OBS,
        "pgEndedAt": "2026-10-17T08:00:25.010+00:00",
        "taskObservedAt": "2026-10-17T08:00:25.020+00:00",
        "pgTransaction": {
            "snapshot": "original-snapshot",
            "readonly": "on",
            "isolation": "repeatable read",
            "observed_at": OBS,
        },
        "artifacts": {},
    }
    for kind, rows in raw.items():
        observation["artifacts"][kind] = obj(tmp_path, indexed, kind + ".json.gz", rows)
    return indexed, observation, full


def test_later_full_lookup_supplements_only_immutable_id_never_later_mutable_latest(
    tmp_path,
):
    subject = adapter(tmp_path)
    indexed, observation, full = observation_fixture(tmp_path)
    arrays, provenance = subject.observation_arrays(
        tmp_path, indexed, observation, {"record": full}
    )
    assert arrays["records"] == [full] and arrays["latest"][0]["data_ts_start"] == START
    assert provenance["immutableFullOnlySupplement"] is True
    future = {**full, "created_at": "2026-10-17T08:01:00"}
    # Changing critical created_at is refused before a later body can be borrowed.
    with pytest.raises(InvalidEvidence):
        subject.observation_arrays(tmp_path, indexed, observation, {"record": future})


def test_same_observation_full_snapshot_must_have_identical_transaction_parent_and_times(
    tmp_path,
):
    subject = adapter(tmp_path)
    indexed, observation, full = observation_fixture(tmp_path)
    capture = {
        k: deepcopy(observation[k])
        for k in (
            "poll",
            "parentTaskId",
            "pgStartedAt",
            "pgReadCompletedAt",
            "pgEndedAt",
            "taskObservedAt",
        )
    }
    capture["transaction"] = {
        **observation["pgTransaction"],
        "snapshot": "later-snapshot",
    }
    capture["artifacts"] = {}
    observation["firstCompleteFullPayloads"] = capture
    with pytest.raises(MissingEvidence, match="same original observation"):
        subject.observation_arrays(tmp_path, indexed, observation, {"record": full})


def pool_fixture():
    members = []
    for index, role in enumerate(
        ["api", "worker_master", "beat"] + ["worker_child"] * 4
    ):
        pid = 100 + index
        ppid = 1 if role == "api" else 101 if role == "worker_child" else 100
        argv = (
            "/synthetic/python -m uvicorn app.main:app"
            if role == "api"
            else "/synthetic/python -m celery -A synthetic beat -l info"
            if role == "beat"
            else "/synthetic/python -m celery -A synthetic worker --hostname synthetic@%h"
        )
        members.append(
            {
                "pid": pid,
                "ppid": ppid,
                "role": role,
                "loadedSha": "a" * 40,
                "identity": str(pid),
                "startedAtLocal": "Sat Oct 17 15:00:00 2026",
                "argv": argv,
            }
        )
    samples = []
    start = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)
    for index in range(16):
        samples.append(
            {
                "observedAt": (start + timedelta(seconds=2 * index)).isoformat(),
                "ownedProcesses": [
                    f"{row['pid']} {row['ppid']} 0.0 1000 S {row['argv']}"
                    for row in members
                ],
            }
        )
    return {"members": members}, samples


def task_fixture():
    events = []
    for identity, name, kwargs, parent, root in [
        (
            PARENT,
            "app.tasks.kpi_calc.calculate_hourly_kpi",
            {"ts_start": START},
            None,
            PARENT,
        ),
        (
            CHILD,
            "app.tasks.kpi_calc.calculate_node_kpi_hourly",
            {"ts_start": START, "ts_end": END},
            PARENT,
            PARENT,
        ),
    ]:
        events.extend(
            [
                {
                    "observerUtc": "2026-10-17T08:00:01+00:00",
                    "event": {
                        "type": "task-received",
                        "uuid": identity,
                        "name": name,
                        "kwargs": repr(kwargs),
                        "parent_id": parent,
                        "root_id": root,
                        "pid": 101,
                        "hostname": "synthetic@host",
                        "timestamp": datetime(
                            2026, 10, 17, 8, 0, 1, tzinfo=timezone.utc
                        ).timestamp(),
                    },
                },
                {
                    "observerUtc": "2026-10-17T08:00:02+00:00",
                    "event": {
                        "type": "task-started",
                        "uuid": identity,
                        "pid": 101,
                        "hostname": "synthetic@host",
                        "timestamp": datetime(
                            2026, 10, 17, 8, 0, 2, tzinfo=timezone.utc
                        ).timestamp(),
                    },
                },
            ]
        )
    redis = {
        "taskMeta": {
            PARENT: {
                "status": "SUCCESS",
                "date_done": "2026-10-17T08:00:20+00:00",
                "result": {"total": 1000},
            },
            CHILD: {
                "status": "SUCCESS",
                "date_done": "2026-10-17T08:00:21+00:00",
                "result": {"total": 20, "success": 20, "skipped": 0, "failed": 0},
            },
        }
    }
    runtime, samples = pool_fixture()
    scope = {
        "scopes": {f"node-{i}": [] for i in range(20)},
        "querySourceRef": {"path": "actual-source", "sha256": "a" * 64},
        "scopeRef": {"path": "actual-scope", "sha256": "b" * 64},
        "_poolProof": closed_worker_pool(runtime, samples),
    }
    return events, redis, runtime, scope


def test_node_scope_is_explicitly_derived_and_original_request_has_no_fabricated_ids(
    tmp_path,
):
    subject = adapter(tmp_path)
    events, redis, runtime, scope = task_fixture()
    original = deepcopy(events)
    tasks = subject.tasks(
        events,
        redis,
        PARENT,
        runtime,
        {"tsStart": START, "tsEnd": END},
        OBS,
        scope,
        [],
        scope["_poolProof"],
    )
    node = next(task for task in tasks if task["id"] == CHILD)
    assert len(node["nodeIds"]) == 20
    assert node["nodeScopeProvenance"]["kind"] == "derivedFromFrozenEnabledScope"
    assert "node_ids" not in node["actualRequest"]["kwargs"]
    assert node["runtimeShaProvenance"]["executorPid"] is None
    assert node["runtimeShaProvenance"]["eventProducerPid"] == 101
    assert events == original


@pytest.mark.parametrize(
    "defect",
    [
        "missing-pool-event",
        "node-count19",
        "late-terminal",
        "duplicate-node-request",
        "wrong-parent",
        "nonterminal-backend",
        "producer-is-pool-not-master",
        "wrong-worker-hostname",
        "request-outside-resource-interval",
    ],
)
def test_task_scope_or_runtime_missing_evidence_cannot_be_filled_with_report_truth(
    tmp_path, defect
):
    subject = adapter(tmp_path)
    events, redis, runtime, scope = task_fixture()
    if defect == "missing-pool-event":
        events = [
            e
            for e in events
            if not (
                e["event"]["type"] == "task-started" and e["event"]["uuid"] == CHILD
            )
        ]
    elif defect == "node-count19":
        redis["taskMeta"][CHILD]["result"]["success"] = 19
    elif defect == "late-terminal":
        redis["taskMeta"][CHILD]["date_done"] = "2026-10-17T08:01:00+00:00"
    elif defect == "duplicate-node-request":
        events.append(deepcopy(events[2]))
    elif defect == "wrong-parent":
        events[2]["event"]["parent_id"] = "another-parent"
    elif defect == "nonterminal-backend":
        redis["taskMeta"][CHILD]["status"] = "STARTED"
    elif defect == "producer-is-pool-not-master":
        events[2]["event"]["pid"] = 103
        events[3]["event"]["pid"] = 103
    elif defect == "wrong-worker-hostname":
        events[2]["event"]["hostname"] = "other@host"
        events[3]["event"]["hostname"] = "other@host"
    elif defect == "request-outside-resource-interval":
        events[2]["event"]["timestamp"] = datetime(
            2026, 10, 17, 7, 59, tzinfo=timezone.utc
        ).timestamp()
    with pytest.raises(MissingEvidence):
        subject.tasks(
            events,
            redis,
            PARENT,
            runtime,
            {"tsStart": START, "tsEnd": END},
            OBS,
            scope,
            [],
            scope["_poolProof"],
        )


@pytest.mark.parametrize(
    "defect",
    [
        "missing-pid",
        "recycled-pid",
        "extra-worker",
        "bad-ancestry",
        "mixed-sha",
        "sample-gap",
    ],
)
def test_closed_execution_pool_rejects_unknown_possible_executor_or_sampling_gap(
    defect,
):
    runtime, samples = pool_fixture()
    if defect == "missing-pid":
        samples[1]["ownedProcesses"].pop()
    elif defect == "recycled-pid":
        samples[1]["ownedProcesses"][-1] = samples[1]["ownedProcesses"][-1].replace(
            "106 101", "999 101", 1
        )
    elif defect == "extra-worker":
        samples[1]["ownedProcesses"].append(
            "999 100 0.0 1000 S /synthetic/python -m celery -A synthetic worker --hostname other@%h"
        )
    elif defect == "bad-ancestry":
        samples[1]["ownedProcesses"][-1] = samples[1]["ownedProcesses"][-1].replace(
            "106 101", "106 999", 1
        )
    elif defect == "mixed-sha":
        runtime["members"][-1]["loadedSha"] = "b" * 40
    elif defect == "sample-gap":
        samples[1]["observedAt"] = "2026-10-17T08:00:06+00:00"
    with pytest.raises(MissingEvidence):
        closed_worker_pool(runtime, samples)


def test_exact_twenty_input_objects_required_before_any_capacity_sidecar(tmp_path):
    subject = adapter(tmp_path)
    start = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    planned = []
    for index in range(20):
        begin = start + timedelta(hours=index)
        planned.append(
            {
                "batch": index + 1,
                "scenario": "a" if index < 10 else "b",
                "cache": "cold" if index % 10 < 5 else "hot",
                "ts_start": begin.isoformat(),
                "ts_end": (begin + timedelta(hours=1)).isoformat(),
            }
        )
    ref = put(tmp_path, "original-plan.json", {"batches": planned})
    plan = {
        "candidateSha": "a" * 40,
        "dbNaiveTimezone": "UTC",
        "redisHost": "127.0.0.1",
        "redisPort": 17123,
        "plannedBatches": ref,
        "batches": [{"batch": i + 1} for i in range(19)],
    }
    with pytest.raises(MissingEvidence, match="Exact original20"):
        subject.run(plan)
    assert not (subject.out / "series.json").exists()


def test_runtime_local_time_conversion_retains_source_and_requires_explicit_convention(
    tmp_path,
):
    subject = adapter(tmp_path)
    members = []
    for index, role in enumerate(
        ["api", "worker_master", "beat"] + ["worker_child"] * 4
    ):
        members.append(
            {
                "role": role,
                "pid": index + 1,
                "identity": str(index + 1),
                "argv": "original argv",
                "loadedSha": "a" * 40,
                "startedAtLocal": "Sat Oct 17 16:00:00 2026",
            }
        )
    raw = {
        "runtime": {"members": members},
        "envFileSha256": "env-sha",
        "schemaRevision": "revision",
        "postgres": {"database": "clpm_capacity_fixture"},
    }
    plan = {
        "candidateSha": "a" * 40,
        "runtimeStartTimezone": "Asia/Shanghai",
        "redisHost": "127.0.0.1",
        "redisPort": 17123,
    }
    result = subject.runtime(
        raw,
        plan,
        {"envFileSha256": "env-sha", "configDigest": "digest"},
        {"path": "td-original", "sha256": "a" * 64},
    )
    assert result["members"][0]["startedAt"] == "2026-10-17T08:00:00+00:00"
    assert result["members"][0]["startedAtLocal"] == members[0]["startedAtLocal"]
    assert "startedAt" not in raw["runtime"]["members"][0]
    with pytest.raises(MissingEvidence, match="explicit frozen"):
        subject.runtime(
            raw,
            {**plan, "runtimeStartTimezone": None},
            {"envFileSha256": "env-sha", "configDigest": "digest"},
            {},
        )
