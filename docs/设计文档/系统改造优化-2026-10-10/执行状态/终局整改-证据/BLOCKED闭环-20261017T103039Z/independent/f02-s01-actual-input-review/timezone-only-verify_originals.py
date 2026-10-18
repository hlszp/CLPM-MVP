"""Non-author verification of sealed originals only; no database or subprocess imports."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import traceback

D = Path(__file__).resolve().parent
E = D.parents[1]
A = E / "f02/synthetic-capacity-binding-actual-root-v1"
I = E / "f02/synthetic-capacity-binding-root-invocation-v1"
S = E / "f02/synthetic-capacity-dump-structure-root-v1"
P = E / "f02/synthetic-capacity-binding-preparation/redis-url-guard-v2/offline-plan"
ADAPTER = E / "f02/synthetic-capacity-dump-adapter-root"
CID = "4ec91098b6ce86a4a6edc34ffa7ef9dcad5e92ade6206db3df9d9ef7561aa3c0"
DUMP_SHA = "265c94e0244a392dceb78ae004d8e9fc21fc12a4df07404208b9b2546487edaf"
RESULT_TABLES = {"kpi_snapshot_hourly", "kpi_snapshot_custom", "loop_confidence_latest", "kpi_node_snapshot_hourly", "kpi_node_snapshot_daily", "kpi_node_snapshot_monthly", "calculation_result_record", "calculation_result_attempt", "calculation_result_legacy_map", "calculation_dataset_snapshot", "workbench_loop_latest", "unit_kpi_summary"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ref(path):
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": sha(data)}


def load(path):
    return json.loads(path.read_text())


def canon(rows):
    return sorted(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for row in rows)


def manifest(folder, expected):
    path = folder / "evidence-manifest.json"
    assert ref(path)["sha256"].startswith(expected), ("manifest identity", str(path))
    items = load(path)["items"]
    expected_files = {item["path"] for item in items}
    actual_files = {str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() and p != path}
    assert len(items) == len(expected_files) and expected_files == actual_files
    for item in items:
        r = ref(folder / item["path"])
        assert r["bytes"] == item["bytes"] and r["sha256"] == item["sha256"], item["path"]
    return {"manifest": ref(path), "filesVerified": len(items), "bytesVerified": sum(x["bytes"] for x in items)}


def verify():
    sealed = [manifest(A, "36bc5f00cd768a7afb343a549bdb1b9e7db368afdcb6019e90306d4a08a9939d"), manifest(I, "768381de"), manifest(S, "98b3b1c6"), manifest(ADAPTER, "1e1810c8")]
    plan, reference = load(P / "plan.json"), load(P / "reference.json")
    assert ref(P / "plan.json")["sha256"] == "bb0a2d9f2b787d8e21ecd9baabe3410e15e58b4aa5bf499ba08337ecf3334e52"
    assert ref(P / "reference.json")["sha256"] == plan["referenceSha256"] == "7f4f1bd130d8143806e73f3e3d02a8802477bab7ab0fccbb0cd5caa2a63bad2a"
    assert plan["database"] == "clpm_capacity_20261017_s01" and len(plan["tableNames"]) == 25
    assert set(plan["tableNames"]) == set(reference)
    state = load(A / "state.json")
    assert state["committed"] is True and state["persistedReadbackVerified"] is True and state["rows"] == 7000
    assert state["allowedColumns"] == ["valid_from", "basis"]
    snapshots, identities, counts = {}, {}, {}
    for label in ("before", "transaction-before", "transaction-after", "persisted"):
        snapshots[label] = {table: load(A / label / "raw" / (table + ".json")) for table in plan["tableNames"]}
        assert len(list((A / label / "raw").glob("*.json"))) == 25
        identities[label] = load(A / label / "identity.json")
        ident = identities[label]
        assert ident["database"] == plan["database"] and ident["currentUser"] == "clpm"
        assert ident["readOnly"] == ("off" if label.startswith("transaction") else "on")
        assert ident["isolation"] == ("serializable" if label.startswith("transaction") else "repeatable read")
        counts[label] = load(A / label / "result-counts.json")
        assert set(counts[label]) == RESULT_TABLES and all(value == 0 for value in counts[label].values())
    assert identities["transaction-before"]["snapshot"] == identities["transaction-after"]["snapshot"]
    table_facts = []
    for table in plan["tableNames"]:
        before = snapshots["before"][table]
        reference_rows = reference[table]
        if table == "loop_tag_binding_history":
            reference_rows = [dict(row) for row in reference_rows]
            for row in reference_rows:
                for key in ("created_at", "valid_from", "valid_to"):
                    if row[key] is not None:
                        timestamp = datetime.fromisoformat(row[key])
                        assert timestamp.tzinfo is not None
                        row[key] = timestamp.astimezone(UTC).isoformat()
        assert canon(before) == canon(reference_rows), ("reference drift", table)
        assert canon(before) == canon(snapshots["transaction-before"][table]), ("pre-write drift", table)
        if table != "loop_tag_binding_history":
            assert canon(before) == canon(snapshots["transaction-after"][table]) == canon(snapshots["persisted"][table]), ("out of scope table change", table)
        table_facts.append({"table": table, "rows": len(before), "allUnchanged": table != "loop_tag_binding_history", "originalReferenceCanonicalSha256": sha("\n".join(canon(reference[table])).encode()), "snapshots": {label: ref(A / label / "raw" / (table + ".json")) for label in snapshots}})
    old = {row["id"]: row for row in snapshots["before"]["loop_tag_binding_history"]}
    assert len(old) == len(snapshots["before"]["loop_tag_binding_history"]) == 7000
    changed = {}
    for label in ("transaction-after", "persisted"):
        rows = snapshots[label]["loop_tag_binding_history"]
        new = {row["id"]: row for row in rows}
        assert len(rows) == len(new) == 7000 and set(new) == set(old)
        for row_id, original in old.items():
            actual = new[row_id]
            assert set(actual) == set(original)
            differences = {key for key in original if original[key] != actual[key]}
            assert differences == {"valid_from", "basis"}, (row_id, differences)
            assert original["basis"] == "MVP_INIT" and actual["basis"] == "SYNTHETIC_FIXTURE"
            assert actual["valid_from"] == plan["effectiveStartUtc"] == "2026-10-15T12:00:00+00:00"
            for key in ("id", "tag_id", "loop_id", "tag_role", "mapping_version", "valid_to", "created_at"):
                assert actual[key] == original[key], (row_id, key)
        changed[label] = {"rows": len(rows), "uniqueIds": len(new), "onlyChangedFields": ["valid_from", "basis"], "allOtherFieldsUnchanged": True}
    assert state["update"]["status"] == "UPDATE 7000" and set(state["update"]["ids"]) == set(old) and len(state["update"]["ids"]) == 7000
    assert state["update"]["effectiveStartUtc"] == plan["effectiveStartUtc"]
    for key in ("tasksBefore", "tasksAtWrite", "tasksAfter"):
        assert state[key] == {"taskKeys": [], "brokerPendingOrUnacked": 0, "celeryResultKeys": []}
    invocation = load(I / "result.json")
    assert invocation["executed"] is True and invocation["rc"] == 0 and invocation["stateSha256"] == ref(A / "state.json")["sha256"]
    assert invocation["stdoutSha256"] == ref(I / "stdout.log")["sha256"] and invocation["stderrSha256"] == ref(I / "stderr.log")["sha256"]
    command = load(Path(invocation["commandFile"]))
    assert ref(Path(invocation["commandFile"]))["sha256"] == invocation["commandFileSha256"]
    assert command["argv"][command["argv"].index("--plan-dir") + 1] == str(P)
    for pin in invocation["verifiedSourcePins"]:
        if not pin["path"].endswith("/.env"):
            assert ref(Path(pin["path"]))["sha256"] == pin["sha256"]
    assert ref(P.parent / "prepare_bindings.py")["sha256"] == "e213f27d9abc631ad3cb11233366e6f631a9fb4d88bcbf3b81cdf0d60464650b"
    dump = A / "before.dump"
    assert ref(dump)["sha256"] == DUMP_SHA and dump.stat().st_size == 12905207 and dump.read_bytes()[:5] == b"PGDMP" and dump.stat().st_mode & 0o777 == 0o600
    adapter_before, adapter_result, backup = load(A / "dump-adapter-before.json"), load(A / "dump-adapter-result.json"), load(A / "pg-dump-command.json")
    assert adapter_result["rc"] == backup["rc"] == state["backup"]["rc"] == 0
    assert adapter_result["sha256"] == state["backup"]["fileSha256"] == DUMP_SHA and adapter_result["bytes"] == 12905207
    assert ref(ADAPTER / "pg_dump_test_container.py")["sha256"] == "c27d789a2c8308cfbeebc692d4565d2460b6c65fe2f3bc295fc72f51469fb3c8"
    assert backup["argv"][1:] == adapter_before["callerArgv"] and backup["argv"] == state["backup"]["argv"]
    assert adapter_before["callerArgv"] == ["--host=localhost", "--port=17102", "--username=clpm", "--dbname=" + plan["database"], "--format=custom", "--no-owner", "--file=" + str(dump)]
    assert adapter_before["actualDumpArgv"] == ["docker", "exec", "-e", "PGPASSWORD", CID, "pg_dump", "--host=127.0.0.1", "--port=5432", "--username=clpm", "--dbname=" + plan["database"], "--format=custom", "--no-owner", "--no-password"]
    for label, check in adapter_before["checks"].items():
        assert check["rc"] == 0
        for stream in ("stdout", "stderr"):
            assert ref(A / ("dump-adapter-" + label + "." + stream + ".log"))["sha256"] == check[stream + "Sha256"]
    info = load(A / "dump-adapter-identity.stdout.log")
    assert info["Id"] == CID and info["Name"] == "/clpm-mvp-postgres" and info["Image"] == "postgres:16" and all(x["HostPort"] == "17102" for x in info["Ports"]["5432/tcp"])
    assert (A / "dump-adapter-version.stdout.log").read_bytes().startswith(b"pg_dump (PostgreSQL) 16.14")
    structure = load(S / "command.json")
    assert structure["rc"] == 0 and structure["databaseConnection"] is False and structure["argv"] == ["docker", "exec", "-i", CID, "pg_restore", "--list"]
    assert structure["dumpSha256"] == DUMP_SHA and structure["dumpBytes"] == 12905207
    for item in structure["logs"]:
        assert ref(Path(item["path"])) == item
    assert (S / "stdout.log").stat().st_size == 65835
    assert ref(dump)["sha256"] == DUMP_SHA
    return {"result": "PASS_INPUT_PREPARATION_ONLY", "verifiedAtUtc": datetime.now(UTC).isoformat(), "sealedManifests": sealed, "plan": ref(P / "plan.json"), "reference": ref(P / "reference.json"), "tables": table_facts, "historyChanges": changed, "identities": identities, "resultCounts": counts, "threeQueueObservations": {key: state[key] for key in ("tasksBefore", "tasksAtWrite", "tasksAfter")}, "invocation": {"reference": ref(I / "result.json"), "rc": invocation["rc"], "durationSeconds": invocation["durationSeconds"]}, "dump": {**ref(dump), "mode": oct(dump.stat().st_mode & 0o777), "header": "PGDMP", "actualGenerationRc": adapter_result["rc"]}, "structureOnly": {"reference": ref(S / "command.json"), "rc": 0, "restorePerformed": False}, "newRealtimeDatabaseOrDockerOrServiceCalls": 0, "capacityRunsAccepted": "0/20", "newConfigFreezeAccepted": False, "oldFreezeRemainsInvalid": True}


if __name__ == "__main__":
    try:
        result = verify()
    except Exception as exc:
        result = {"result": "REWORK", "errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc(), "newRealtimeCalls": 0}
    (D / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in ("tables", "identities", "resultCounts")}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["result"] == "PASS_INPUT_PREPARATION_ONLY" else 1)
