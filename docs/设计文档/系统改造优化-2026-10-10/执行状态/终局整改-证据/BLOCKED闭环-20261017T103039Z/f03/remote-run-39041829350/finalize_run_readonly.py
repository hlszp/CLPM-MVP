"""Project immutable originals into counts/identity only; never inspect critical KPI values."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re

D = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path.relative_to(D)), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def write_json(path, data):
    assert not path.exists()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main():
    captured = load(D / "capture-final.json")
    raw = load(D / "raw-evidence-manifest.json")
    for row in raw["files"]:
        assert ref(D / row["path"]) == row
    jobs = load(D / captured["progress"]["rawSnapshotRefs"]["jobs"]["path"])["jobs"]
    gate = D / "artifacts/12104885530/home/runner/work/_temp/critical-e2e/gate"
    execution, collection = (load(gate / name) for name in ("execution-summary.json", "collection-summary.json"))
    counts = {
        "collection": {key: collection[key] for key in ("mode", "passed", "exitCode", "required", "collected", "stats", "violations")},
        "execution": {key: execution[key] for key in ("mode", "passed", "exitCode", "required", "collected", "stats", "violations")},
        "rows": [{"id": row["id"], "expectedStatus": row["expectedStatus"], "status": row["status"],
                  "attempts": [{key: result.get(key) for key in ("status", "retry", "duration")} for result in row["results"]]} for row in execution["rows"]],
        "sourceRefs": [ref(gate / name) for name in ("collection-summary.json", "execution-summary.json")],
        "criticalActualKpiProjection": "NOT_READ_OR_EXTRACTED_NUMERIC_BLINDNESS_PRESERVED",
    }
    assert counts["execution"]["required"] == counts["execution"]["collected"] == 4
    assert counts["execution"]["stats"]["expected"] == 4 and counts["execution"]["stats"]["skipped"] == 0
    assert counts["execution"]["stats"]["flaky"] == counts["execution"]["stats"]["unexpected"] == 0
    assert {row["id"] for row in counts["rows"]} == {"CRITICAL-AUTH-001", "CRITICAL-PERM-001", "CRITICAL-CALC-001", "CRITICAL-EVIDENCE-001"}
    assert all(row["status"] == "expected" and all(x["status"] == "passed" for x in row["attempts"]) for row in counts["rows"])
    write_json(D / "critical-counts-only.json", counts)
    comparison = load(D / "offline-comparison/comparison.json")
    differences = {field: sum(not row["coefficientComparison"][field]["same"] for row in comparison["armaxCalls"]) for field in ("a", "b", "c", "lastMaxDiff", "iterationsDone")}
    identity = captured["actualCheckoutGitObjects"]["645241e983856d3012f9ca2310af260ab988646f"]
    git = load(D / identity["gitCommit"]["path"])
    request_codes = {}
    for path in (D / "requests").glob("*.command.json"):
        rc = str(load(path)["processReturncode"])
        request_codes[rc] = request_codes.get(rc, 0) + 1
    summary = {
        "createdAt": datetime.now(UTC).isoformat(), "runId": 39041829350, "runAttempt": 1,
        "url": "https://github.com/hlszp/CLPM-MVP/actions/runs/39041829350",
        "headSha": "c6fa2b489af66da392ea72fa387629f1607ba613", "actualCheckout": "645241e983856d3012f9ca2310af260ab988646f",
        "actualCheckoutProvenance": {job_id: data["checkouts"] for job_id, data in captured["jobLogs"].items()},
        "actualRootTreeSha": identity["rootTreeSha"], "actualBackendTreeSha": identity["backendTreeSha"], "actualCommitParents": [parent["sha"] for parent in git["parents"]],
        "runStatus": captured["progress"]["run"], "jobs": [{key: job.get(key) for key in ("id", "name", "status", "conclusion", "started_at", "completed_at")} for job in jobs],
        "backend": {"collected": 7489, "deselected": 166, "selected": 7323, "passed": 6971, "failed": 0, "skipped": 336, "xfailed": 12, "xpassed": 4, "elapsedSeconds": 657.56, "coveragePercent": 75.75,
                    "source": ref(D / "job-logs/117422708873.log"), "collectionLine": 3341, "summaryLine": 4753, "coverageLine": 4752, "g3OriginalFileAll14PassedLine": 3484},
        "frontend": {"unitFilesPassed": 106, "unitTestsPassed": 1021, "unitTotal": 1021, "e2ePassed": 2, "source": ref(D / "job-logs/117422708914.log"), "unitSummaryLines": [1822, 1823], "e2eSummaryLine": 3171},
        "critical": {"countsOnlyProjection": ref(D / "critical-counts-only.json"), "required": 4, "collected": 4, "passed": 4, "skipped": 0, "unexpected": 0, "flaky": 0, "exitCode": 0,
                     "remoteIndependentNumeric": "NOT_ACCEPTED_MISSING_BEFORE_AFTER_PG_POLICY_AND_DATASET_ROWS", "actualKpiValues": "NOT_READ_OR_FORWARDED"},
        "artifacts": [{"id": item["githubOriginal"]["id"], "name": item["githubOriginal"]["name"], "githubDigest": item["githubOriginal"].get("digest"), "archive": item["archive"], "digestMatches": item["githubDigestMatchesArchive"], "files": len(item["uncompressedFiles"])} for item in captured["artifacts"].values()],
        "g3Diagnostic": {"shellRc": int((D / "artifacts/12105065268/ci-exit-code.txt").read_text()), "status": load(D / "artifacts/12105065268/observation/diagnostic-status.json"), "comparison": ref(D / "offline-comparison/comparison.json"),
                         "all12AlgorithmSourcesSameAsMacBaseline": all(row["same"] for row in comparison["sourceComparison"]), "rawOpPvPayloadsSame": all(row["sameRawPayload"] for row in comparison["inputArrays"].values()),
                         "uniqueExactUyConfigMatches": sum(row["alignment"] == "EXACT_RAW_U_Y_AND_CONFIG_UNIQUE" for row in comparison["armaxCalls"]), "terminalDifferenceCounts": differences,
                         "abDeltaConverged": sum(row["metActualConvergenceCriterion"] for row in comparison["linuxArmaxTerminalFacts"]), "sortBoundary": ref(D / "offline-comparison/sort-boundary-comparison.json"),
                         "oldX86V3FailureCause": "UNPROVEN_NO_RAW_INPUT_OR_ITERATION_ORIGINALS_FROM_FAILED_RUN", "actualLocalAlgorithmReexecutions": 0},
        "collector": {"actualProcessExitCodeObserved": 0, "GETRequestProcessReturncodes": request_codes, "rawManifest": ref(D / "raw-evidence-manifest.json"), "rawFilesVerified": raw["fileCount"], "rawBytesVerified": raw["totalBytes"], "fullRunLogsZip": captured["fullRunLogs"]["archive"]},
        "boundaries": ["This is a new normal CI run after real SQL/Decimal fixes, not a rerun of a failed run or a selected runner/hardware trial.", "Current CI success does not prove the previous X86_V3 numerical failure was fixed.", "Diagnostic rc0 means collection completed, independently distinct from the original pytest outcome.", "No critical actual KPI values were inspected or sent to F04; current remote numeric acceptance lacks before/after PG policy and dataset rows.", "No product source, algorithm, original assertion, thresholds, protected hashes, shared services, DB, queue, commits, pushes, or deployments were changed by this evidence subtask."],
    }
    assert sum(summary["backend"][key] for key in ("passed", "failed", "skipped", "xfailed", "xpassed")) == summary["backend"]["selected"]
    assert summary["backend"]["selected"] + summary["backend"]["deselected"] == summary["backend"]["collected"]
    assert all(row["digestMatches"] for row in summary["artifacts"])
    write_json(D / "final-run-summary.json", summary)
    print(json.dumps({"summary": ref(D / "final-run-summary.json"), "rawFilesVerified": raw["fileCount"], "backend": {k: v for k, v in summary["backend"].items() if k != "source"}, "artifactDigestMatches": True, "GETProcessExitCodes": request_codes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
