"""After expectation seal, verify archived real four-case report and HTTP evidence."""

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent.parent
ROOT = BASE / "f03/remote-run-39044390911/artifacts/12105297313/home/runner/work/_temp/critical-e2e"
OUT = BASE / "independent/f03-remote-numeric-review/run-39044390911-bc48701e"


def run():
    reads = []
    dest = OUT / "actual"
    dest.mkdir(exist_ok=False)

    def read(path):
        data = path.read_bytes()
        reads.append({"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return data

    def value(path):
        return json.loads(read(path))

    seal = value(OUT / "expectation-seal.json")
    first_read = value(OUT / "actual-read-start.json")
    assert hashlib.sha256((OUT / "expectation-seal.json").read_bytes()).hexdigest() == first_read["expectationSealSha256"] == "6175b5fcab3c039f49780abbe52295ea0e026578e15090a5e50f0269f703103f"
    assert first_read["firstActualReadPhaseStartedAtUtc"] > seal["sealedAtUtc"]
    expected = value(OUT / "expected.json")
    assert hashlib.sha256((OUT / "expected.json").read_bytes()).hexdigest() == seal["expected"]["sha256"]
    reports = {}
    for name in ("executed.json", "execution-summary.json", "collected.json", "collection-summary.json", "run.stdout.log", "run.stderr.log"):
        data = read(ROOT / "gate" / name)
        (dest / name).write_bytes(data)
        if name.endswith(".json"):
            reports[name] = json.loads(data)
    raw = reports["executed.json"]
    summary = reports["execution-summary.json"]
    assert raw["errors"] == []
    assert raw["stats"]["expected"] == 4 and all(raw["stats"][key] == 0 for key in ("skipped", "unexpected", "flaky"))
    assert len(raw["config"]["projects"]) == 1
    project = raw["config"]["projects"][0]
    assert project["name"] == "chromium" and project["repeatEach"] == 1 and project["retries"] == 0

    def specs(suites):
        for suite in suites:
            yield from suite.get("specs", [])
            yield from specs(suite.get("suites", []))

    cases = {}
    for spec in specs(raw["suites"]):
        match = re.match(r"^(CRITICAL-(?:AUTH|PERM|CALC|EVIDENCE)-001):", spec["title"])
        assert match and match[1] not in cases and spec["file"] == "critical-business.spec.ts"
        assert spec["ok"] is True and len(spec["tests"]) == 1
        test = spec["tests"][0]
        assert test["expectedStatus"] == "passed" and test["status"] == "expected" and test["annotations"] == []
        assert test["projectName"] == "chromium" and len(test["results"]) == 1
        result = test["results"][0]
        assert result["status"] == "passed" and result["retry"] == 0 and result["errors"] == [] and result["annotations"] == []
        cases[match[1]] = result
    assert set(cases) == {f"CRITICAL-{name}-001" for name in ("AUTH", "PERM", "CALC", "EVIDENCE")}
    assert summary["exitCode"] == 0 and summary["passed"] is True and summary["violations"] == []
    assert summary["required"] == summary["collected"] == 4
    summary_cases = {row["id"]: row for row in summary["rows"]}
    assert set(summary_cases) == set(cases)
    attachments = {}
    for key, result in cases.items():
        assert summary_cases[key]["results"] == [result]
        for attachment in result.get("attachments", []):
            assert attachment["contentType"] == "application/json"
            name = attachment["name"]
            assert name not in attachments and Path(name).name == name
            data = base64.b64decode(attachment["body"], validate=True)
            (dest / name).write_bytes(data)
            attachments[name] = json.loads(data)
    identity = attachments["sponsor-identity.json"]
    denied = attachments["sponsor-write-denied.json"]
    assert identity["code"] == "0" and identity["data"]["role"] == "SPONSOR"
    assert "metric:task-run" not in identity["data"]["permissions"] and "page:workbench.main" not in identity["data"]["pageCodes"]
    assert denied["code"] == "ERR_PERMISSION_DENIED" and denied["data"] is None
    task, snapshot, record = [attachments[name] for name in ("task-terminal.json", "snapshot.json", "result-record.json")]
    fixture = value(ROOT / "fixture.json")
    assert task["status"] == "SUCCESS" and task["taskType"] == "CUSTOM" and task["errorMessage"] is None
    assert task["loopsTotal"] == task["loopsDone"] == task["windowCount"] == task["progress"] == 1
    assert task["loopIds"] == [fixture["loopId"]]
    assert snapshot["loopId"] == fixture["loopId"] and snapshot["source"] == "MANUAL_CUSTOM"
    assert snapshot["sourceTaskId"] == task["taskId"] == record["payload"]["customTaskId"]
    assert snapshot["status"] == record["payload"]["snapshotStatus"] == "SUCCESS"
    assert record["objectKind"] == "LOOP" and record["objectId"] == fixture["loopId"] and record["status"] == "COMPLETED"
    assert snapshot["resultRecordId"] == record["recordId"]
    assert snapshot["algorithmVersion"] == record["algorithmVersion"] == expected["identity"]["algorithmVersion"]
    assert record["configRevision"] == expected["identity"]["configRevision"]
    for entity in (snapshot, record, task):
        assert entity["tsStart"] == fixture["windowStart"] and entity["tsEnd"] == fixture["windowEnd"]
    context = record["payload"]["assessmentContext"]
    assert context["assessmentProfile"] == expected["identity"]["assessmentProfile"]
    assert context["buildDigest"] == expected["identity"]["executableBuildDigest"]
    assert context["configRevision"] == expected["identity"]["configRevision"] and context["paramsSource"] == "package"
    pre = value(OUT / "input-preconditions.json")
    dataset = pre["datasetInputRow"]
    assert record["datasetSnapshotId"] == record["payload"]["inputSnapshot"]["snapshotId"] == dataset["id"]
    assert record["payload"]["inputSnapshot"]["status"] == "SAVED"
    assert record["payload"]["inputSnapshot"]["inputHash"] == expected["recomputedDatasetInputHash"] == dataset["input_hash"]
    projected = {"loopId": record["objectId"], "algorithmVersion": record["algorithmVersion"], "status": record["payload"]["snapshotStatus"]}
    for public, stored in (("goodValueRate", "good_value_rate"), ("autoModeRate", "auto_mode_rate"), ("steadyRate", "steady_rate")):
        assert snapshot[public] == record["payload"][stored]
        projected[public] = record["payload"][stored]
    (dest / "record-numeric-projection.json").write_text(json.dumps(projected, ensure_ascii=False, indent=2) + "\n")
    result = {
        "reviewedAtUtc": datetime.now(timezone.utc).isoformat(),
        "remoteRealFourCases": "PASS",
        "cases": [{"id": name, "status": result["status"], "retry": result["retry"], "duration": result["duration"]} for name, result in cases.items()],
        "skipRetryFlaky": [0, 0, 0],
        "realPlaywrightVersion": raw["config"]["version"],
        "rawAndValidatedReportCorrespondExactly": True,
        "sponsorEvidenceAndFrozenBrowserPositiveNegativeAssertionsPassed": True,
        "customOneLoopOneWindowFullyCompleted": True,
        "snapshotRecordTaskDatasetIdentityAndHashClosed": True,
        "expectationSealBeforeActualRead": first_read,
        "remoteCiOrCapacitySignoff": False,
        "readSet": reads,
    }
    (OUT / "actual-business-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: val for key, val in result.items() if key != "readSet"}, ensure_ascii=False))


if __name__ == "__main__":
    run()
