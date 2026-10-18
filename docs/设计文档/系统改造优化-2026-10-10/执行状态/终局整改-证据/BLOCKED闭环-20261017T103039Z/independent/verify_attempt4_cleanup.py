"""Only disk bytes/metadata and synthetic TD inputs; never parse PG dump or results."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SOURCE = BASE / "f03/local-attempt4-preserved-resources"
RUN = BASE / "f03/local-business-20261017215608-9f356782ef40"
OUT = BASE / "independent/f03-cleanup-review-attempt4"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def main():
    OUT.mkdir(exist_ok=False)
    references = []
    def json_file(path):
        raw = path.read_bytes()
        references.append({"path": str(path), "sha256": sha(raw), "bytes": len(raw)})
        return json.loads(raw)
    mpath = SOURCE / "evidence-manifest.json"
    manifest_raw = mpath.read_bytes()
    assert sha(manifest_raw) == "7f957498ffd37f83901e7ebc6fc32148d23d2df402baf9cf11bd39045f7cef28"
    manifest = json.loads(manifest_raw)
    mapping = []
    for ref in manifest["files"]:
        path = Path(ref["path"])
        assert path.is_relative_to(SOURCE)
        raw = path.read_bytes()  # Opaque hash only for dump/RDB, never parsed/restored.
        assert len(raw) == ref["bytes"] and sha(raw) == ref["sha256"]
        mapping.append({"originalAbsolutePath": str(path), "archiveRelativePath": str(path.relative_to(SOURCE)), "sha256": sha(raw), "bytes": len(raw)})
    assert len(mapping) == 61
    outcome = json_file(RUN / "stop-outcome.json")
    signal = json_file(RUN / "api-stop-signal.json")
    exited = json_file(RUN / "api-exit.json")
    state = json_file(RUN / "input-only-before-gate/started-state.json")
    native = json_file(RUN / "post-stop-independent-native-observation/observation.json")
    rotation_native = json_file(SOURCE / "actual-post-stop.json")
    stopped = json_file(RUN / "stopped-process-check.json")
    broker = json_file(RUN / "queue-after-stop-root-readonly.json")
    assert signal["pid"] == exited["pid"] == state["apiPid"] == 91036
    assert signal["signal"] == "SIGTERM" and signal["beatRemainsPaused"] is True and exited["rc"] == -15
    assert outcome["errors"] == [{"phase": "api-stop", "error": "psutil.AccessDenied(pid=91036)"}]
    assert outcome["actualApiExit"] == exited and not (RUN / "api-shutdown-fallback.json").exists()
    log = RUN / "backend-api.stdout.log"
    data = log.read_bytes()  # Search only three fixed lifecycle lines; do not interpret KPI logs.
    expected = [b"INFO:     Waiting for application shutdown.\n", b"INFO:     Application shutdown complete.\n", b"INFO:     Finished server process [91036]\n"]
    cursor = signal["logOffsetBeforeSignal"]
    chain = []
    for index, value in enumerate(expected):
        position = data.find(value, cursor)
        assert position >= cursor
        (OUT / f"shutdown-{index}.raw.log").write_bytes(data[position:position + len(value)])
        chain.append({"offset": position, "bytes": len(value), "sha256": sha(value)})
        cursor = position + len(value)
    assert native["selectedLines"] == [] and native["psRc"] == 0 and not any(native["ownedPortsOpen"].values())
    assert native["driverStopRc"] == 1 and native["cleanupOnly"] is True
    assert rotation_native["originalMembersStillPresent"] == rotation_native["selectedRawPsLines"] == [] and rotation_native["psRc"] == 0 and rotation_native["driverStopPassed"] is False
    original = {row["pid"] for row in state["processesAtStart"]}
    assert len(original) == 7 and original.issubset({row["pid"] for row in stopped["observed"]}) and not stopped["remaining"]
    assert broker["actualBroker"] == {"host": "127.0.0.1", "port": 17124, "db": "/1", "resultDb": "/2"}
    assert not any(broker["redis"]["brokerLists"].values()) and broker["redis"]["unacked"] == broker["redis"]["unackedIndex"] == 0
    capacities = []
    for phase in ("before", "after-root-stop"):
        summary = json_file(RUN / ("capacity-readonly-" + phase + ".json"))
        assert summary["writes"] is False and len(summary["tables"]) == 9
        for table, meta in summary["tables"].items():
            raw = (RUN / "capacity-raw" / phase / (table + ".ndjson")).read_bytes()
            canonical = (RUN / "capacity-raw" / phase / (table + ".canonical.ndjson")).read_bytes()
            assert len(raw) == meta["bytes"] and sha(raw) == meta["sha256"]
            assert len(canonical) == meta["canonicalBytes"] and sha(canonical) == meta["canonicalSha256"]
        capacities.append(summary)
    assert capacities[0]["database"] == capacities[1]["database"]
    assert capacities[0]["tables"] == capacities[1]["tables"]
    assert capacities[0]["tables"]["loop_tag_mapping"]["count"] == 7000
    for name in ["kpi_snapshot_hourly", "loop_confidence_latest", "kpi_node_snapshot_hourly", "calculation_result_record", "kpi_snapshot_custom", "calculation_dataset_snapshot"]:
        assert capacities[0]["tables"][name]["count"] == 0
    fixture = json_file(RUN / "fixture.json")
    inputs = json_file(RUN / "fixture.inputs.json")
    start = instant(fixture["windowStart"])
    by_table = {"p_" + v["pointId"].replace("-", ""): (role, v) for role, v in inputs["roles"].items()}
    td_rows, td_checks = 0, []
    for index in range(7):
        metadata = json_file(SOURCE / f"td-input-{index:02d}-all-original-rows.json")
        body = json_file(SOURCE / f"td-input-{index:02d}-all-original-rows.body.json")
        assert metadata["httpStatus"] == 200 and metadata["code"] == body["code"] == 0
        columns = [x[0] for x in body["column_meta"]]
        assert len(columns) == len(set(columns)) == 8
        assert columns == ["ts", "value", "quality_raw", "quality_class", "quality_schema", "received_at", "source_kind", "payload_hash"]
        table = next(name for name in by_table if (".`" + name + "`") in metadata["sql"])
        role, source = by_table[table]
        assert len(body["data"]) == body["rows"] == len(source["samples"])
        for raw_row, sample in zip(body["data"], source["samples"], strict=True):
            assert len(raw_row) == 8
            row = dict(zip(columns, raw_row, strict=True))
            offset, value, qc, qr = sample
            assert instant(row["ts"]) == start + timedelta(seconds=offset)
            assert float(row["value"]).hex() == float(value).hex() and row["quality_class"] == qc and row["quality_raw"] == qr
        td_rows += body["rows"]
        td_checks.append({"table": table, "role": role, "rows": body["rows"], "dataColumns": columns, "pointIdProvenance": "original fixture -> exact p_<UUID> table name; TD tags not separately selected"})
    assert td_rows == 7207
    complete = json_file(SOURCE / "complete.json")
    assert complete["originalStopRc"] == 1 and complete["originalApiRc"] == -15 and complete["originalGatePassed"] is False
    result = {"scope": "Read-only preservation and cleanup review only", "archiveManifestSha256": sha(manifest_raw), "archiveFilesRehashed": 61, "dumpAndRdbOnlyOpaqueHashedNotParsedOrRestored": True, "originalStopVerdict": "FAIL/rc1 AccessDenied retained", "actualCleanupClosure": "PASS", "actualApiRc": -15, "orderedCleanShutdownOriginalLineRefs": chain, "originalSevenProcessesAndFrontendGone": True, "ports17261And17266Closed": True, "originalPrivateBrokerEmpty": True, "capacityNineTablesBeforeAfterExactRawAndCanonical": True, "tdInputsPreservedRows": td_rows, "tdDataColumns": 8, "tdTagRecoveryBoundary": "Actual old TD tag values not independently exported; point UUID mapping is preserved by fixture and exact table names", "tdChecks": td_checks, "readActualMetrics": False, "references": references}
    (OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (OUT / "absolute-path-recovery-map.json").write_text(json.dumps({"originalManifestUnmodified": True, "sourceArchive": str(SOURCE), "files": mapping}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"tdChecks", "references"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
