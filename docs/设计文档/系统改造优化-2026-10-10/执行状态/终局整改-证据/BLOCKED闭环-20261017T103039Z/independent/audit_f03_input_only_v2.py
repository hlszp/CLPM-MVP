"""Audit only allowlisted fixed F03 inputs; never open gate/results or services."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

from verify_configuration_freeze_raw import git_catalog


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def run(args):
    reads = []
    def raw(path):
        data = path.read_bytes()
        reads.append({"path": str(path), "bytes": len(data), "sha256": sha(data)})
        return data
    def value(path):
        return json.loads(raw(path))
    manifest_path = args.input / "input-only-before-gate/input-only-manifest.json"
    manifest = value(manifest_path)
    assert sha(manifest_path.read_bytes()) == args.manifest_sha
    assert manifest["candidateSha"] == args.sha and manifest["actualMetricsIncluded"] is False
    assert manifest["mutablePhaseFilesCopiedBeforeHashing"] is True
    mismatches = []
    for ref in manifest["files"]:
        path = Path(ref["path"])
        assert path.is_relative_to(args.input) and "gate" not in path.relative_to(args.input).parts
        data = raw(path)
        if len(data) != ref["bytes"] or sha(data) != ref["sha256"]:
            mismatches.append({"path": str(path), "expected": ref, "actualSha256": sha(data), "actualBytes": len(data)})
    fixture = value(args.input / "fixture.json")
    series = value(args.input / "fixture.inputs.json")
    bindings = value(args.input / "fixture.bindings.json")
    td = value(args.input / "fixture.td-readback.json")
    assert fixture["candidateSha"] == args.sha and fixture["synthetic"] is True and fixture["inputOnly"] is True
    assert fixture["rawInputSha256"] == sha((args.input / "fixture.inputs.json").read_bytes())
    assert fixture["tdReadbackSha256"] == sha((args.input / "fixture.td-readback.json").read_bytes())
    assert fixture["syntheticBindingDeclaration"]["sha256"] == sha((args.input / "fixture.bindings.json").read_bytes())
    roles = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
    assert set(fixture["inputs"]) == set(series["roles"]) == set(td) == roles
    assert len({x["pointId"] for x in fixture["inputs"].values()}) == 7
    start, end = instant(fixture["windowStart"]), instant(fixture["windowEnd"])
    role_checks = []
    for role in sorted(roles):
        declared, source, saved = fixture["inputs"][role], series["roles"][role], td[role]
        samples = source["samples"]
        assert source["pointId"] == saved["pointId"] == declared["pointId"]
        assert len(samples) == declared["count"] == saved["count"] == len(saved["rawRows"])
        assert sha(json.dumps(samples).encode()) == declared["sha256"]
        assert saved["sql"].startswith("SELECT ") and " LIMIT " not in saved["sql"].upper()
        assert "clpm_critical_20261017_local_ts.p_" + declared["pointId"].replace("-", "") in saved["sql"]
        normalized = []
        for row, sample in zip(saved["rawRows"], samples, strict=True):
            offset, expected_value, qc, raw_qc = sample
            assert instant(row["ts"]) == start + timedelta(seconds=offset)
            assert float(row["value"]).hex() == float(expected_value).hex()
            assert row["quality_class"] == qc and row["quality_raw"] == raw_qc and row["point_id"] == declared["pointId"]
            normalized.append([int(instant(row["ts"]).timestamp() * 1000), float(row["value"]), qc, raw_qc])
        assert sha(json.dumps(normalized).encode()) == saved["canonicalSha256"]
        role_checks.append({"role": role, "pointId": declared["pointId"], "samples": len(samples), "allValuesFloatHexExact": True, "allTimestampsAndQualityExact": True})
    assert bindings["committed"] is True and bindings["persistedReadbackVerified"] is True
    assert bindings["actualDatabase"] == "clpm_critical_20261017_local" and bindings["loopId"] == fixture["loopId"]
    before = bindings["before"]
    assert not before["foreignMappings"] and not before["foreignHistory"]
    assert len(before["mappings"]) == len(before["histories"]) == len(bindings["persistedReadback"]) == 7
    before_ids = {row["id"] for row in before["histories"]}
    for row in bindings["persistedReadback"]:
        original = json.loads(row["raw_row"])
        assert original["id"] == row["id"] and row["id"] in before_ids
        assert original["loop_id"] == fixture["loopId"]
        assert original["tag_role"] == row["tag_role"] and original["tag_id"] == row["tag_id"] == fixture["inputs"][row["tag_role"]]["pointId"]
        assert instant(original["valid_from"]) == instant(row["valid_from"]) == start
        assert original["valid_to"] is row["valid_to"] is None
        assert original["mapping_version"] == row["mapping_version"] == 1
        assert original["basis"] == row["basis"] == "SYNTHETIC_FIXTURE"
    assert bindings["affectedRows"] == 7 and bindings["errors"] == []
    sql_command = value(args.input / "input-policy-before/command.json")
    sql = raw(args.input / "input-policy-before/executed.sql")
    policy_raw = raw(args.input / "input-policy-before/stdout.log")
    assert sha(sql) == sql_command["sqlSha256"] == "d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294"
    assert sql_command["rc"] == 0 and sql_command["actualSourceSha"] == args.sha
    assert sha(policy_raw) == sql_command["stdoutSha256"]
    sections = [json.loads(line) for line in policy_raw.decode().splitlines() if line.startswith("{")]
    before_tx, after_tx = sections[0], sections[-1]
    assert before_tx["section"] == "transaction-before" and after_tx["section"] == "transaction-after"
    for row in (before_tx, after_tx):
        assert row["database"] == "clpm_critical_20261017_local" and row["readOnly"] == "on" and row["isolation"] == "repeatable read" and row["timezone"] == "UTC"
    for field in ("snapshot", "transactionTimestampUtc", "transactionIdIfAssigned"):
        assert before_tx[field] == after_tx[field]
    assert instant(before_tx["observedAtUtc"]) < instant(after_tx["observedAtUtc"])
    held = [row for row in sections if row["section"] == "held-policy"]
    assert len(held) == 2 and {row["requestedKey"] for row in held} == {"data.held_too_long_fast_seconds", "data.held_too_long_slow_seconds"}
    assert all(row["rowExists"] is False and row["rawValue"] is None for row in held)
    state = value(args.input / "input-only-before-gate/started-state.json")
    rows = state["processesAtStart"]
    assert state["sourceSha"] == args.sha and state["startupReadinessVerified"] is True
    assert len(rows) == len({row["pid"] for row in rows}) == 7
    beat = next(row for row in rows if row["pid"] == state["beatPausedPid"])
    assert beat["status"] == "stopped" and beat["created"] == state["beatPausedCreated"] and beat["ppid"] == state["apiPid"]
    master = next(row for row in rows if row["ppid"] == state["apiPid"] and "worker" in row["argv"])
    assert len([row for row in rows if row["ppid"] == master["pid"] and row["argv"] == master["argv"]]) == 4
    catalog, source_check = git_catalog(args.repo, args.sha, args.input / "source-tracked-catalog.json")
    runtime_count = 0
    for row in catalog["files"]:
        path = args.repo / row["path"]
        data = os.readlink(path).encode() if row["mode"] == "120000" else path.read_bytes()
        assert len(data) == row["runtimeBytes"] and sha(data) == row["runtimeSha256"]
        runtime_count += 1
    env = value(args.input / "input-only-before-gate/environment-before-gate.json")
    assert env["CLPM_BUILD_CODE_SHA"] == env["sourceSha"] == args.sha
    assert env["LOCAL_RUN_ID"] == manifest["runId"] and env["CLPM_G5_TRACE"] == "0"
    assert env["CELERY_BROKER_URL"] == "redis://127.0.0.1:17124/1" and env["CELERY_RESULT_BACKEND"] == "redis://127.0.0.1:17124/2"
    result = {"status": "REWORK" if mismatches else "PASS", "manifestMismatches": mismatches, "candidateSha": args.sha, "loopId": fixture["loopId"], "roleChecks": role_checks, "persistedBindingSevenExact": True, "heldKeys": held, "frozenHeldLimitsSecondsForGrid1s": {"pv": 60, "sp": 7200, "mode": 7200}, "configurationInputSections": sections, "actualReadOnlyTransaction": before_tx, "sourceCatalog": source_check, "actualCurrentTrackedFilesRehashed": runtime_count, "sevenStartupIdentities": rows, "readActualMetrics": False, "productCalculatorImported": False, "readSet": reads}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key not in {"configurationInputSections", "sevenStartupIdentities", "readSet"}}, ensure_ascii=False))
    return 0 if not mismatches else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest-sha", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    raise SystemExit(run(parser.parse_args()))
