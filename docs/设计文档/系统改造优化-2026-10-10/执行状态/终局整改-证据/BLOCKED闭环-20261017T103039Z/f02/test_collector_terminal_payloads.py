"""Pure fake-client integration: never connects, starts, or enqueues anything."""

import asyncio
from copy import deepcopy
from datetime import datetime
import gzip
import importlib.util
import json
import os
from types import SimpleNamespace

import pytest

import collect_capacity_evidence as subject

if os.environ.get("CLPM_COLLECTOR_TEST_SOURCE"):
    spec = importlib.util.spec_from_file_location(
        "collector_subject", os.environ["CLPM_COLLECTOR_TEST_SOURCE"]
    )
    subject = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(subject)

START = datetime(2026, 10, 15, 12)
PARENT = "00000000-0000-4000-8000-000000000001"


def critical_fixture():
    ledger = [
        {"loop_id": "loop", "ts_start": START.isoformat(), "result_record_id": "record"}
    ]
    latest = [
        {
            "loop_id": "loop",
            "data_ts_start": START.isoformat(),
            "result_record_id": "record",
        }
    ]
    nodes = [
        {
            "plant_node_id": "node",
            "ts_start": START.isoformat(),
            "result_record_id": "node-record",
        }
    ]
    return ledger, latest, nodes, [{"id": "record"}, {"id": "node-record"}]


@pytest.mark.parametrize(
    "defect",
    [
        "missing-loop",
        "duplicate-loop",
        "missing-latest",
        "old-latest-window",
        "missing-node",
        "missing-record",
        "null-record-ref",
        "missing-latest-record",
    ],
)
def test_presence_trigger_rejects_partial_wrong_identity_or_unresolved_record_refs(
    defect,
):
    ledger, latest, nodes, records = deepcopy(critical_fixture())
    if defect == "missing-loop":
        ledger.clear()
    elif defect == "duplicate-loop":
        ledger.append(deepcopy(ledger[0]))
    elif defect == "missing-latest":
        latest.clear()
    elif defect == "old-latest-window":
        latest[0]["data_ts_start"] = "2026-10-15T11:00:00"
    elif defect == "missing-node":
        nodes.clear()
    elif defect == "missing-record":
        records.pop()
    elif defect == "null-record-ref":
        ledger[0]["result_record_id"] = None
    elif defect == "missing-latest-record":
        latest[0]["result_record_id"] = "different-latest-record"
    assert not subject.critical_complete(
        ledger, latest, nodes, records, ["loop"], ["node"], START
    )


def test_presence_trigger_does_not_make_product_status_version_or_score_decisions():
    ledger, latest, nodes, records = critical_fixture()
    ledger[0].update(status="FAILED", score=None, algorithm_version="wrong")
    assert subject.critical_complete(
        ledger, latest, nodes, records, ["loop"], ["node"], START
    )
    assert subject.terminal_checkpoint({"completionReason": "TERMINAL_MISMATCH"})
    assert not subject.terminal_checkpoint({"completionReason": "RUNNING"})


class FakeTransaction:
    def __init__(self, db, isolation, readonly):
        self.db = db
        assert isolation == "repeatable_read" and readonly is True

    async def __aenter__(self):
        assert not self.db.in_transaction
        self.db.tx += 1
        self.db.in_transaction = True

    async def __aexit__(self, *_):
        self.db.in_transaction = False


class FakeDb:
    def __init__(self, partial_first):
        self.in_transaction = False
        self.tx = 0
        self.calls = []
        self.partial_first = partial_first

    def transaction(self, isolation, readonly):
        return FakeTransaction(self, isolation, readonly)

    async def fetchrow(self, sql, *_):
        assert self.in_transaction
        return {
            "readonly": "on",
            "snapshot": f"snapshot-{self.tx}",
            "observed_at": f"tx-{self.tx}",
        }

    async def fetchval(self, sql, *_):
        assert self.in_transaction
        return 1000

    async def fetch(self, sql, *_):
        assert self.in_transaction
        self.calls.append({"sql": sql, "tx": self.tx})
        if "information_schema.columns" in sql:
            return [
                {"table_name": table, "column_name": column}
                for table in (
                    "kpi_snapshot_hourly",
                    "loop_confidence_latest",
                    "kpi_node_snapshot_hourly",
                    "calculation_result_record",
                )
                for column in (
                    "id",
                    "loop_id",
                    "plant_node_id",
                    "ts_start",
                    "data_ts_start",
                    "result_record_id",
                )
            ]
        if " FROM loop_ledger " in sql:
            return [
                {
                    "id": f"loop-{i}",
                    "tag_name": f"PERF-T{i + 1:04d}",
                    "unit_id": "node",
                    "is_active": True,
                    "status": "READY",
                    "include_in_evaluation": True,
                }
                for i in range(1000)
            ]
        if " FROM plant_node " in sql:
            return [
                {
                    "id": "node",
                    "parent_id": None,
                    "name": "unit",
                    "type": "UNIT",
                    "is_kpi_enabled": True,
                }
            ]
        count = 999 if self.partial_first and self.tx == 2 else 1000
        if " FROM kpi_snapshot_hourly " in sql:
            rows = [
                {
                    "id": f"ledger-{i}",
                    "loop_id": f"loop-{i}",
                    "ts_start": START,
                    "result_record_id": f"record-{i}",
                }
                for i in range(count)
            ]
        elif " FROM loop_confidence_latest " in sql:
            rows = [
                {
                    "id": f"latest-{i}",
                    "loop_id": f"loop-{i}",
                    "data_ts_start": START,
                    "result_record_id": f"record-{i}",
                }
                for i in range(count)
            ]
        elif " FROM kpi_node_snapshot_hourly " in sql:
            rows = (
                []
                if count == 999
                else [
                    {
                        "id": "node-snapshot",
                        "plant_node_id": "node",
                        "ts_start": START,
                        "result_record_id": "node-record",
                    }
                ]
            )
        elif " FROM calculation_result_record " in sql:
            rows = [{"id": f"record-{i}"} for i in range(count)]
            if count == 1000:
                rows.append({"id": "node-record"})
        elif (
            "FROM calculation_result_attempt " in sql or "FROM pg_stat_activity " in sql
        ):
            return []
        else:
            raise AssertionError(f"Unexpected fake query: {sql}")
        if sql.startswith("SELECT * "):
            for row in rows:
                row["payload"] = {
                    "sameTransaction": self.tx,
                    "raw": [None, 0, "original"],
                }
        return rows

    async def close(self):
        assert not self.in_transaction


def run_fake_sampler(tmp_path, monkeypatch, partial_first):
    db = FakeDb(partial_first)
    clock = {"value": 0}

    async def connect(**_):
        return db

    async def sleep(_):
        clock["value"] += 1

    monkeypatch.setattr(subject.asyncpg, "connect", connect)
    monkeypatch.setattr(subject.asyncio, "sleep", sleep)
    monkeypatch.setattr(subject.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(
        subject,
        "dotenv_values",
        lambda _: {
            "POSTGRES_DB": "clpm_capacity_fake",
            "TDENGINE_DB": "clpm_capacity_fake",
            "POSTGRES_PASSWORD": "fake-not-live",
        },
    )
    monkeypatch.setattr(
        subject.redis, "Redis", lambda **_: SimpleNamespace(close=lambda: None)
    )
    monkeypatch.setattr(
        subject.httpx,
        "Client",
        lambda **_: SimpleNamespace(
            post=lambda *_a, **_k: SimpleNamespace(
                status_code=200, json=lambda: {"code": 0, "rows": 0}
            ),
            close=lambda: None,
        ),
    )
    monkeypatch.setattr(
        subject,
        "redis_snapshot",
        lambda *_: {
            "observedAt": "separate-redis-time",
            "taskMeta": {PARENT: {"status": "SUCCESS"}},
            "memory": {},
            "queueLengths": {},
            "unacked": [],
        },
    )
    monkeypatch.setattr(
        subject, "cmd", lambda argv: {"argv": argv, "rc": 0, "stdout": "", "stderr": ""}
    )
    monkeypatch.setattr(
        subject,
        "report_read",
        lambda _: {
            "parentTaskId": PARENT,
            "completionReason": "TERMINAL_MISMATCH",
            "observedAt": "source-report-time",
        },
    )
    env = tmp_path / "fake.env"
    env.write_text("no real configuration")
    out = tmp_path / "output"
    args = SimpleNamespace(
        env_file=str(env),
        out=str(out),
        ts_start=START.isoformat(),
        report=str(tmp_path / "not-live-report"),
        worker_log=None,
        parent_task_id=PARENT,
        smoke=False,
        max_seconds=3,
        post_seconds=100,
        interval=2,
        containers=[],
        td_container=None,
        runtime_marker="fake-never-started",
    )
    rc = asyncio.run(subject.main(args))
    assert rc == 0
    return db, out, json.loads((out / "collector-summary.json").read_bytes())


def restore(out, ref):
    return json.loads(gzip.decompress((out / ref["file"]).read_bytes()))


@pytest.mark.parametrize("partial_first", [True, False])
def test_real_sampler_flow_keeps_terminal_and_complete_same_poll_transactions_distinct_from_final(
    tmp_path, monkeypatch, partial_first
):
    db, out, summary = run_fake_sampler(tmp_path, monkeypatch, partial_first)
    terminal = summary["firstTerminalFullPayloads"]
    complete = summary["firstCompleteFullPayloads"]
    final = summary["finalFullPayloads"]
    assert terminal["poll"] == 1 and terminal["transaction"]["snapshot"] == "snapshot-2"
    assert complete["poll"] == (2 if partial_first else 1)
    expected_complete_tx = 3 if partial_first else 2
    assert complete["transaction"]["snapshot"] == f"snapshot-{expected_complete_tx}"
    assert final["transaction"]["snapshot"] == "snapshot-5"
    assert terminal["parentTaskId"] == PARENT
    assert terminal["taskObservedAt"] == "separate-redis-time"
    assert (
        terminal["pgStartedAt"]
        <= terminal["pgReadCompletedAt"]
        <= terminal["pgEndedAt"]
    )
    assert len(restore(out, terminal["artifacts"]["ledger"])) == (
        999 if partial_first else 1000
    )
    assert len(restore(out, complete["artifacts"]["ledger"])) == 1000
    for snapshot, tx in [(terminal, 2), (complete, expected_complete_tx), (final, 5)]:
        assert set(snapshot["artifacts"]) == {"ledger", "latest", "nodes", "records"}
        for ref in snapshot["artifacts"].values():
            assert all(
                row["payload"]["sameTransaction"] == tx for row in restore(out, ref)
            )
    observations = [
        json.loads(line)
        for line in (out / "observations.jsonl").read_text().splitlines()
        if '"poll"' in line
    ]
    assert observations[0]["firstTerminalFullPayloads"] == terminal
    assert observations[complete["poll"] - 1]["firstCompleteFullPayloads"] == complete
    assert sum("firstTerminalFullPayloads" in row for row in observations) == 1
    assert sum("firstCompleteFullPayloads" in row for row in observations) == 1
    assert all(
        row.get("pgTransaction", {}).get("snapshot") != "snapshot-5"
        for row in observations
    )
    full_reads = [
        call
        for call in db.calls
        if call["sql"].startswith("SELECT * FROM ")
        and "calculation_result_attempt" not in call["sql"]
    ]
    expected_reads = 12 if partial_first else 8
    assert len(full_reads) == expected_reads
    for tx in {call["tx"] for call in full_reads}:
        assert sum(call["tx"] == tx for call in full_reads) == 4
