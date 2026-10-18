"""Read archived input evidence only; no database, services, results or product imports."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


def run(base, output):
    reads = []

    def raw(path):
        value = path.read_bytes()
        reads.append({"path": str(path), "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()})
        return value

    def load(path):
        return json.loads(raw(path))

    def instant(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc))

    def normalize(row):
        return {
            key: instant(value).isoformat() if isinstance(value, str) and key in {"created_at", "updated_at", "valid_from", "valid_to"} else value
            for key, value in row.items()
        }

    preflight = base / "f02/historical-binding-readonly-preflight-root"
    manifest = load(preflight / "evidence-manifest.json")
    for ref in manifest["files"]:
        data = raw(preflight / ref["path"])
        assert len(data) == ref["size"] and hashlib.sha256(data).hexdigest() == ref["sha256"]
    command = load(preflight / "command.json")
    original = raw(preflight / "stdout.log")
    assert command["rc"] == 0 and command["actualDatabase"] == "clpm_capacity_20261017_s01"
    assert hashlib.sha256(original).hexdigest() == command["stdoutSha256"]
    assert hashlib.sha256(raw(preflight / "stderr.log")).hexdigest() == command["stderrSha256"]
    assert instant(command["startedAt"]) < instant(command["finishedAt"])
    sql = raw(preflight / "executed.sql").decode()
    assert "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" in sql and "SET LOCAL TIME ZONE 'UTC';" in sql
    assert " LIMIT " not in sql.upper()
    sections = [json.loads(line) for line in original.decode().splitlines() if line.startswith("{")]
    assert [row["section"] for row in sections] == ["before", "synthetic-roster", "bindings", "after"]
    first, roster_section, bindings_section, last = sections
    for row in (first, last):
        assert row["database"] == command["actualDatabase"] and row["readOnly"] == "on" and row["isolation"] == "repeatable read"
    assert first["snapshot"] == last["snapshot"]
    roster, histories = roster_section["rows"], bindings_section["rows"]
    assert len(roster) == len({row["id"] for row in roster}) == 1000
    assert {row["tag_name"] for row in roster} == {f"PERF-T{number:04}" for number in range(1, 1001)}
    assert all(row["description"] == "P5容量合成数据集(synthetic)——生成器产出，非现场真实回路" for row in roster)
    assert all(row["is_active"] is True and row["include_in_evaluation"] is True and row["status"] == "READY" for row in roster)
    ids = {row["id"] for row in roster}
    assert len(histories) == len({row["id"] for row in histories}) == len({row["tag_id"] for row in histories}) == 7000
    roles = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
    assert {row["loop_id"] for row in histories} == ids
    by_loop = {loop_id: [] for loop_id in ids}
    for row in histories:
        by_loop[row["loop_id"]].append(row)
        assert row["basis"] == "MVP_INIT" and row["mapping_version"] == 1 and row["valid_to"] is None
    assert all(len(rows) == 7 and {row["tag_role"] for row in rows} == roles for rows in by_loop.values())
    normal = load(base / "f02/series-s01/planned-batches.json")["batches"]
    extra = load(base / "f02/extra-inputs/plan.json")["inputs"]
    assert len(normal) == 20 and len(extra) == 4
    assert all(row["required"] is True and row["expected_loops"] == 1000 and row["slo_seconds"] == 600 for row in normal)
    assert all(row["formal20Denominator"] is False for row in extra)
    windows = normal + extra
    assert len({(row["ts_start"], row["ts_end"]) for row in windows}) == 24
    starts = [instant(row["ts_start"]) for row in windows]
    ends = [instant(row["ts_end"]) for row in windows]
    valid_from = [instant(row["valid_from"]) for row in histories]
    assert min(valid_from) > max(ends)
    assert all(sum(instant(row["valid_from"]) > instant(window["ts_end"]) for row in histories) == 7000 for window in windows)
    old_dir = base / "f02/series-s01/config-freeze-c452/pg-raw"
    old_histories, old_roster, mappings = [load(old_dir / name) for name in ("loop_tag_binding_history.json", "loop_ledger.json", "loop_tag_mapping.json")]
    assert {row["id"]: normalize(row) for row in old_histories} == {row["id"]: normalize(row) for row in histories}
    assert {row["id"]: normalize(row) for row in old_roster} == {row["id"]: normalize(row) for row in roster}
    assert len(mappings) == 7000
    assert {(row["loop_id"], row["tag_role"], row["tag_id"]) for row in mappings} == {(row["loop_id"], row["tag_role"], row["tag_id"]) for row in histories}
    result = {
        "findingConfirmed": "PASS",
        "capacityReadiness": "BLOCKED",
        "databaseIdentity": first,
        "timezoneEvidence": "successful SET LOCAL TIME ZONE UTC in original SQL; transaction JSON does not separately emit timezone",
        "syntheticRoster": 1000,
        "completeSevenRoleBindings": 7000,
        "allRosterAndHistoryRowsMatchEarlierC452AfterTimestampNormalization": True,
        "allHistoricalBindingsMatchEarlierCurrentMapping": True,
        "normalWindows": 20,
        "additionalWindowsNotNormalDenominator": 4,
        "earliestWindowStartUtc": min(starts).isoformat(),
        "latestWindowEndUtc": max(ends).isoformat(),
        "historyValidFromRangeUtc": [min(valid_from).isoformat(), max(valid_from).isoformat()],
        "eachOf24WindowsHasAll7000BindingsTooLate": True,
        "oldTdAvailabilityAuditsCannotProveHistoricalBindingResolution": True,
        "formalTasksExecuted": "0/20 per Keeper declaration; this original read-only SQL does not query task/result tables",
        "noDatabaseOrNetworkOperationsByReviewer": True,
        "readActualKpiOutputs": False,
        "readSet": reads,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "readSet"}, ensure_ascii=False))


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]))
