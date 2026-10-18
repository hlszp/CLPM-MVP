"""Freeze c6 input-only numeric plan from approved allowlist, never actual KPI outputs."""

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE / "f03/local-business-20261017223757-c6fa2b489af6"
OUT = BASE / "independent/f03-local-numeric-review/run-20261017223757-c6fa2b489af6"
REPO = Path("/Users/zhangping/DEV/clpm-remediation-20261017")
HEAD = "c6fa2b489af66da392ea72fa387629f1607ba613"


def run():
    reads = []
    copied = OUT / "inputs"
    copied.mkdir(exist_ok=False)

    def read(path):
        raw = path.read_bytes()
        reads.append({"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        return raw

    def value(path):
        return json.loads(read(path))

    def copy(path, name):
        destination = copied / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(read(path))
        return destination

    def ref(path):
        raw = path.read_bytes()
        return {"path": str(path.relative_to(BASE)), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    def moment(raw):
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    audit = value(OUT / "input-audit.json")
    assert audit["status"] == "PASS" and audit["candidateSha"] == HEAD
    input_manifest = value(ROOT / "input-only-before-gate/input-only-manifest.json")
    assert input_manifest["actualMetricsIncluded"] is False and input_manifest["mutablePhaseFilesCopiedBeforeHashing"] is True
    for item in input_manifest["files"]:
        path = Path(item["path"])
        raw = read(path)
        assert path.is_relative_to(ROOT) and "gate" not in path.relative_to(ROOT).parts
        assert len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"]
    fixture_path = copy(ROOT / "fixture.json", "fixture.json")
    raw_path = copy(ROOT / "fixture.inputs.json", "fixture.inputs.json")
    fixture = json.loads(fixture_path.read_bytes())
    package_id = "4a8117ad-f7f5-4615-8d6c-fa0622fa0565"
    package_root = ROOT / "calc-snapshots/202610"
    manifest_path = copy(package_root / f"{package_id}.manifest.json", f"{package_id}.manifest.json")
    npz_path = copy(package_root / f"{package_id}.npz", f"{package_id}.npz")
    assert ref(manifest_path)["sha256"] == "41284dd7d029e36ad9e718f610433cf7bea9993d042bbe76b082eceeff7bbe68"
    assert ref(npz_path)["sha256"] == "f214a2790f71162bf65610e7a9d71691e9936a4883161245d4548e26feb0e2fe"
    manifest = json.loads(manifest_path.read_bytes())
    effective = manifest["source"]["effectiveParams"]
    assert manifest["loopId"] == fixture["loopId"] == audit["loopId"]
    assert manifest["schemaVersion"] == "1.3" and manifest["kind"] == "KPI_GRID"
    policies = []
    for phase in ("before", "after"):
        phase_files = {}
        for path in sorted((ROOT / f"input-policy-{phase}").iterdir()):
            if path.is_file():
                target = copy(path, f"input-policy-{phase}/" + path.name)
                phase_files[path.name] = target.read_bytes()
        command = json.loads(phase_files["command.json"])
        assert command["rc"] == 0 and command["actualSourceSha"] == HEAD
        assert command["sqlSha256"] == hashlib.sha256(phase_files["executed.sql"]).hexdigest() == "d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294"
        assert command["stdoutSha256"] == hashlib.sha256(phase_files["stdout.log"]).hexdigest()
        sections = [json.loads(line) for line in phase_files["stdout.log"].decode().splitlines() if line.startswith("{")]
        assert len(sections) == 11
        for tx in (sections[0], sections[-1]):
            assert tx["database"] == "clpm_critical_20261017_local" and tx["readOnly"] == "on" and tx["isolation"] == "repeatable read" and tx["timezone"] == "UTC"
        for key in ("snapshot", "transactionTimestampUtc", "transactionIdIfAssigned"):
            assert sections[0][key] == sections[-1][key]
        assert moment(sections[0]["observedAtUtc"]) < moment(sections[-1]["observedAtUtc"])
        policies.append(sections)
    assert policies[0][1:-1] == policies[1][1:-1]
    by_name = {row["section"]: row for row in policies[0] if row["section"] != "held-policy"}
    for row in policies[0]:
        if row["section"] == "held-policy":
            assert row["rowExists"] is False and row["rawValue"] is None
    assert by_name["configuration-revision"]["maxRevision"] == effective["configRevision"] == 0
    assert by_name["active-release-input"]["rows"] == []
    assert by_name["algorithm-parameter-input"]["rows"] == []
    assert by_name["enabled-configuration-overrides"]["rows"] == []
    weights = {row["loop_type"]: {key: float(row[key]) for key in ("weight_a", "weight_f", "weight_s")} for row in by_name["loop-type-weights-input"]["rows"]}
    assert weights == effective["typeWeights"] and len(weights) == 4
    catalog = value(ROOT / "source-tracked-catalog.json")
    entries = {row["path"]: row for row in catalog["files"]}
    assert catalog["candidateSha"] == HEAD
    source_files = {}
    for name in ("algorithm_config.py", "data_planner.py", "calc_snapshot.py", "result_ledger.py"):
        relative = "backend/app/services/" + name
        target = copy(REPO / relative, "sources/" + name)
        assert ref(target)["sha256"] == entries[relative]["runtimeSha256"]
        source_files[name] = target
    defaults = None
    for node in ast.parse(source_files["algorithm_config.py"].read_text()).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_DEFAULTS":
            defaults = ast.literal_eval(node.value)
    flat = {f"{metric}|{control}": params for metric, controls in defaults.items() for control, params in controls.items()}
    assert flat == effective["params"] and len(flat) == 36
    lock = read(REPO / "backend/uv.lock")
    assert hashlib.sha256(lock).hexdigest() == entries["backend/uv.lock"]["runtimeSha256"]
    build = hashlib.sha256(f"clpm-build-v1|{HEAD}|{hashlib.sha256(lock).hexdigest()[:16]}".encode()).hexdigest()[:16]
    digest_parts = json.dumps({"typeWeights": weights, "fitness": effective["fitness"]}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    revision = "rev:0:" + hashlib.sha256(digest_parts.encode()).hexdigest()[:16]
    identity = {"assessmentProfile": "LEGACY_V2", "algorithmVersion": "KPI_CALC_v2.0", "configRevision": revision, "executableBuildDigest": build}
    assert {key: manifest["source"][key] for key in identity} == identity
    env = value(ROOT / "input-only-before-gate/environment-before-gate.json")
    assert env["CLPM_BUILD_CODE_SHA"] == env["sourceSha"] == HEAD
    row_files = {}
    for path in sorted((ROOT / "input-package-row").iterdir()):
        if path.is_file():
            target = copy(path, "input-package-row/" + path.name)
            row_files[path.name] = target.read_bytes()
    command = json.loads(row_files["command.json"])
    assert command["rc"] == 0 and command["inputOnly"] is True and command["sourceSha"] == HEAD
    assert command["snapshotId"] == package_id and command["loopId"] == fixture["loopId"]
    assert command["sqlSha256"] == hashlib.sha256(row_files["executed.sql"]).hexdigest() == "9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff"
    assert command["stdoutSha256"] == hashlib.sha256(row_files["stdout.log"]).hexdigest()
    lines = [json.loads(line) for line in row_files["stdout.log"].decode().splitlines() if line.startswith("{")]
    assert len(lines) == 2
    tx, row = lines
    assert tx["database"] == "clpm_critical_20261017_local" and tx["readOnly"] == "on" and tx["isolation"] == "repeatable read" and tx["timezone"] == "UTC"
    assert row["id"] == package_id and row["loop_id"] == fixture["loopId"]
    assert moment(row["ts_start"]) == moment(fixture["windowStart"]) and moment(row["ts_end"]) == moment(fixture["windowEnd"])
    assert row["kind"] == manifest["kind"] and row["schema_version"] == manifest["schemaVersion"] and row["cleaned_at"] is None
    assert row["size_bytes"] == manifest_path.stat().st_size + npz_path.stat().st_size
    plan = {
        "frozenAt": datetime.now(timezone.utc).isoformat(),
        "candidateSha": HEAD,
        "runId": "20261017223757",
        "fixture": ref(fixture_path),
        "rawInput": ref(raw_path),
        "datasetManifest": ref(manifest_path),
        "datasetNpz": ref(npz_path),
        "declaredDatasetInputHash": row["input_hash"],
        "identity": identity,
        "effectiveParamsSha256": hashlib.sha256(json.dumps(effective["params"], sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
        "heldLimitsSeconds": {"pv": 60, "sp": 7200, "mode": 7200},
        "inclusiveEndSeconds": 1,
        "actualKpiOutputsRead": False,
        "productCalculatorImported": False,
    }
    (OUT / "numeric-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    evidence = {
        "status": "PASS",
        "candidateSha": HEAD,
        "configPoliciesBeforeAfterEqual": True,
        "beforeAndAfterMvccSeparatelyPreserved": [phase[0]["snapshot"] for phase in policies],
        "fullFourTypeWeightsMatchActualPg": True,
        "all36PinnedParamsMatchFrozenSourceDefaults": True,
        "buildDigestIndependentlyRecomputedFromCodeAndLock": build,
        "configRevisionIndependentlyRecomputed": revision,
        "datasetRowExactIdentityAndRawSql": row,
        "actualKpiOutputsRead": False,
        "productCalculatorImported": False,
        "databaseOrNetworkOperations": False,
        "readSet": reads,
    }
    (OUT / "input-preconditions.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: val for key, val in evidence.items() if key not in ("readSet", "datasetRowExactIdentityAndRawSql")}, ensure_ascii=False))


if __name__ == "__main__":
    run()
