"""Approved remote INPUT allowlist only; no actual gate/results, services or network."""
import ast
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from verify_configuration_freeze_raw import git_catalog

BASE = Path(__file__).resolve().parent.parent
RUN = BASE / "f03/remote-run-39044390911"
ROOT = RUN / "artifacts/12105297313/home/runner/work/_temp/critical-e2e"
OUT = BASE / "independent/f03-remote-numeric-review/run-39044390911-bc48701e"
SHA = "bc48701efe90e2a27555a4f8794f0965e82b0902"
REPO = Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")


def run():
    OUT.mkdir(parents=True, exist_ok=False)
    reads = []
    metadata = json.loads((RUN / "artifacts/12105297313.metadata.json").read_text())
    entries = {str((RUN / row["path"]).resolve()): row for row in metadata["uncompressedFiles"]}

    def raw(path):
        data = path.read_bytes()
        item = {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if path.is_relative_to(ROOT):
            assert path.name.startswith("fixture") or "input-evidence" in path.relative_to(ROOT).parts or "calc-snapshots" in path.relative_to(ROOT).parts
            expected = entries[str(path.resolve())]
            assert item["sha256"] == expected["sha256"] and item["bytes"] == expected["bytes"]
        reads.append(item)
        return data

    def value(path, exact=False):
        return json.loads(raw(path), parse_float=Decimal if exact else float)

    def instant(text):
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)

    def ref(path):
        data = raw(path)
        return {"path": str(path.relative_to(BASE)), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    archive = raw(RUN / "artifacts/12105297313.zip")
    assert hashlib.sha256(archive).hexdigest() == metadata["archive"]["sha256"] == metadata["githubOriginal"]["digest"].removeprefix("sha256:")
    assert metadata["githubOriginal"]["workflow_run"]["id"] == 39044390911 and metadata["githubOriginal"]["workflow_run"]["head_sha"] == "8fc850802612b6077f3b07c08324e3694945aa0d"
    fixture, source, td, bindings = [value(ROOT / name) for name in ("fixture.json", "fixture.inputs.json", "fixture.td-readback.json", "fixture.bindings.json")]
    assert fixture["candidateSha"] == SHA and fixture["environment"] == "disposable-github-ci" and fixture["synthetic"] is fixture["inputOnly"] is True
    assert fixture["loopTagName"] == "CRITICAL-E2E-39044390911-1-FLOW001"
    assert fixture["rawInputSha256"] == hashlib.sha256(raw(ROOT / "fixture.inputs.json")).hexdigest()
    assert fixture["tdReadbackSha256"] == hashlib.sha256(raw(ROOT / "fixture.td-readback.json")).hexdigest()
    assert fixture["syntheticBindingDeclaration"]["sha256"] == hashlib.sha256(raw(ROOT / "fixture.bindings.json")).hexdigest()
    roles = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
    assert set(fixture["inputs"]) == set(source["roles"]) == set(td) == roles
    start, end = instant(fixture["windowStart"]), instant(fixture["windowEnd"])
    checked = []
    for role in sorted(roles):
        declared, samples, saved = fixture["inputs"][role], source["roles"][role]["samples"], td[role]
        assert source["roles"][role]["pointId"] == saved["pointId"] == declared["pointId"]
        assert len(samples) == declared["count"] == saved["count"] == len(saved["rawRows"])
        assert hashlib.sha256(json.dumps(samples).encode()).hexdigest() == declared["sha256"]
        assert " LIMIT " not in saved["sql"].upper() and "clpm_critical_e2e_ts.p_" + declared["pointId"].replace("-", "") in saved["sql"]
        normalized = []
        for row, sample in zip(saved["rawRows"], samples, strict=True):
            offset, expected, quality, raw_quality = sample
            assert instant(row["ts"]) == start + timedelta(seconds=offset)
            assert float(row["value"]).hex() == float(expected).hex()
            assert row["quality_class"] == quality and row["quality_raw"] == raw_quality and row["point_id"] == declared["pointId"]
            normalized.append([int(instant(row["ts"]).timestamp() * 1000), float(row["value"]), quality, raw_quality])
        assert hashlib.sha256(json.dumps(normalized).encode()).hexdigest() == saved["canonicalSha256"]
        checked.append({"role": role, "count": len(samples), "allFloatHexTimestampQualityAndPointExact": True})
    assert bindings["committed"] is bindings["persistedReadbackVerified"] is True and bindings["actualDatabase"] == "clpm_critical_e2e"
    assert bindings["affectedRows"] == 7 and bindings["errors"] == []
    assert len(bindings["persistedReadback"]) == len(bindings["before"]["mappings"]) == len(bindings["before"]["histories"]) == 7
    assert not bindings["before"]["foreignMappings"] and not bindings["before"]["foreignHistory"]
    original_ids = {row["id"] for row in bindings["before"]["histories"]}
    for row in bindings["persistedReadback"]:
        original = json.loads(row["raw_row"])
        assert original["id"] == row["id"] and row["id"] in original_ids
        assert original["loop_id"] == fixture["loopId"] and original["tag_id"] == row["tag_id"] == fixture["inputs"][row["tag_role"]]["pointId"]
        assert original["tag_role"] == row["tag_role"] and original["basis"] == row["basis"] == "SYNTHETIC_FIXTURE"
        assert instant(original["valid_from"]) == instant(row["valid_from"]) == start and original["valid_to"] is row["valid_to"] is None
        assert original["mapping_version"] == row["mapping_version"] == 1
    catalog_path = BASE / "runtime/deploy-bc48701e-root/source-tracked-catalog.json"
    assert hashlib.sha256(raw(catalog_path)).hexdigest() == "be8cad9a52879bb11f7bb490276a0452882de1a20bef3931e973b53962907464"
    catalog, catalog_check = git_catalog(REPO, SHA, catalog_path)
    assert catalog_check["files"] == 4050
    files = {row["path"]: row for row in catalog["files"]}
    for row in files.values():
        content = (REPO / row["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == row["runtimeSha256"] and len(content) == row["runtimeBytes"]
    policies, headers = [], []
    for phase in ("before", "after"):
        folder = ROOT / "input-evidence" / phase
        for path in folder.rglob("*"):
            if path.is_file():
                raw(path)
        header = value(folder / "header.json")
        assert header["complete"] is True and header["actualCheckoutSha"] == SHA
        assert header["fixtureSha256"] == hashlib.sha256(raw(ROOT / "fixture.json")).hexdigest()
        for path, digest in header["sources"].items():
            assert files[path]["runtimeSha256"] == digest
        for name in ("git-head", "tracked-sources", "clean-sources", "psql-version", "policy"):
            cmd = value(folder / name / "command.json")
            assert cmd["rc"] == 0
            for stream in ("stdout", "stderr"):
                assert hashlib.sha256(raw(folder / name / (stream + ".log"))).hexdigest() == cmd[stream + "Sha256"]
        assert raw(folder / "git-head/stdout.log").decode().strip() == SHA
        command = value(folder / "policy/command.json")
        assert command["executedSqlSha256"] == hashlib.sha256(raw(folder / "policy.sql")).hexdigest() == "d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294"
        rows = [json.loads(line, parse_float=Decimal) for line in raw(folder / "policy/stdout.log").decode().splitlines() if line.startswith("{")]
        assert len(rows) == 11
        for edge in (rows[0], rows[-1]):
            assert edge["database"] == "clpm_critical_e2e" and edge["readOnly"] == "on" and edge["isolation"] == "repeatable read" and edge["timezone"] == "UTC"
        for key in ("snapshot", "transactionTimestampUtc", "transactionIdIfAssigned"):
            assert rows[0][key] == rows[-1][key]
        assert instant(rows[0]["observedAtUtc"]) < instant(rows[-1]["observedAtUtc"])
        held = [row for row in rows if row["section"] == "held-policy"]
        assert len(held) == 2 and {row["requestedKey"] for row in held} == {"data.held_too_long_fast_seconds", "data.held_too_long_slow_seconds"}
        assert all(row["rowExists"] is False and row["rawValue"] is None for row in held)
        policies.append(rows); headers.append(header)
    assert policies[0][1:-1] == policies[1][1:-1]
    sections = {row["section"]: row for row in policies[0] if row["section"] != "held-policy"}
    assert sections["active-release-input"]["rows"] == sections["algorithm-parameter-input"]["rows"] == sections["enabled-configuration-overrides"]["rows"] == []
    assert sections["configuration-revision"]["maxRevision"] == 0
    package_files = [ROOT / row["path"] for row in headers[1]["package"]["files"]]
    for path, item in zip(package_files, headers[1]["package"]["files"], strict=True):
        assert len(raw(path)) == item["bytes"] and hashlib.sha256(raw(path)).hexdigest() == item["sha256"]
    manifest_path = next(path for path in package_files if path.name.endswith(".manifest.json")); npz_path = next(path for path in package_files if path.suffix == ".npz")
    manifest = value(manifest_path); effective = manifest["source"]["effectiveParams"]
    defaults = next(ast.literal_eval(n.value) for n in ast.parse((REPO / "backend/app/services/algorithm_config.py").read_text()).body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "_DEFAULTS")
    assert effective["params"] == {f"{code}|{control}": params for code, controls in defaults.items() for control, params in controls.items()}
    weights = {row["loop_type"]: {key: float(row[key]) for key in ("weight_a", "weight_f", "weight_s")} for row in sections["loop-type-weights-input"]["rows"]}
    assert weights == effective["typeWeights"] and effective["status"] == "PINNED" and effective["configRevision"] == 0
    lock = files["backend/uv.lock"]["runtimeSha256"][:16]
    build = hashlib.sha256(f"clpm-build-v1|{SHA}|{lock}".encode()).hexdigest()[:16]
    revision = "rev:0:" + hashlib.sha256(json.dumps({"typeWeights": weights, "fitness": effective["fitness"]}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]
    identity = {"assessmentProfile": "LEGACY_V2", "algorithmVersion": "KPI_CALC_v2.0", "configRevision": revision, "executableBuildDigest": build}
    assert {key: manifest["source"][key] for key in identity} == identity
    row_root = ROOT / "input-evidence/after/dataset-row"
    cmd = value(row_root / "command.json")
    assert cmd["rc"] == 0 and cmd["executedSqlSha256"] == hashlib.sha256(raw(row_root.parent / "dataset-row.sql")).hexdigest() == "9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff"
    assert hashlib.sha256(raw(row_root / "stdout.log")).hexdigest() == cmd["stdoutSha256"]
    decoded = [json.loads(line) for line in raw(row_root / "stdout.log").decode().splitlines() if line.startswith("{")]
    assert len(decoded) == 2
    tx, row = decoded
    assert tx["database"] == "clpm_critical_e2e" and tx["readOnly"] == "on" and tx["isolation"] == "repeatable read" and tx["timezone"] == "UTC"
    assert tx["requestedLoopId"] == row["loop_id"] == fixture["loopId"] == manifest["loopId"]
    assert tx["requestedSnapshotId"] == row["id"] == headers[1]["package"]["snapshotId"]
    assert row["kind"] == manifest["kind"] == "KPI_GRID" and row["schema_version"] == manifest["schemaVersion"] and row["cleaned_at"] is None
    assert instant(row["ts_start"]) == start and instant(row["ts_end"]) == end
    assert row["size_bytes"] == sum(path.stat().st_size for path in package_files)
    plan = {"frozenAtUtc": datetime.now(timezone.utc).isoformat(), "candidateSha": SHA, "githubRunId": 39044390911, "fixture": ref(ROOT / "fixture.json"), "rawInput": ref(ROOT / "fixture.inputs.json"), "datasetManifest": ref(manifest_path), "datasetNpz": ref(npz_path), "declaredDatasetInputHash": row["input_hash"], "identity": identity, "effectiveParamsSha256": hashlib.sha256(json.dumps(effective["params"], sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest(), "heldLimitsSeconds": {"pv": 60, "sp": 7200, "mode": 7200}, "inclusiveEndSeconds": 1, "actualKpiOutputsRead": False}
    (OUT / "numeric-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    result = {"status": "PASS", "mergeSha": SHA, "archiveDigest": metadata["archive"]["sha256"], "actualRemoteInputHeadersAndGitHead": True, "roleChecks": checked, "policySnapshots": [rows[0]["snapshot"] for rows in policies], "exactPgPoliciesBeforeAfterEqual": True, "heldBothExplicitlyAbsent": True, "all36ParamsRebuiltFromSourceDefaultsAndActualPgNoOverrides": True, "fourTypeWeightsMatchActualPg": True, "independentBuildDigest": build, "independentConfigRevision": revision, "datasetInputRow": row, "sourceCatalog": catalog_check, "runtime4050FilesRehashed": True, "actualKpiResultsOrScreenshotsOrGateRead": False, "noNetworkDatabaseOrServiceOperations": True, "overallCiOrLoadedWorkerIdentityNotSignedByThisPreparation": True, "readSet": reads}
    (OUT / "input-preconditions.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: val for key, val in result.items() if key not in ("readSet", "roleChecks")}, ensure_ascii=False))


if __name__ == "__main__":
    run()
