"""GET-only capture of one Actions attempt; never rerun, comment, or mutate GitHub."""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import zipfile

D = Path(__file__).resolve().parent
REPO = "repos/hlszp/CLPM-MVP"
RUN = 39041829350
EXPECTED_HEAD = "c6fa2b489af66da392ea72fa387629f1607ba613"
STOP = False
MAX_SECONDS = 2400


def utc():
    return datetime.now(UTC).isoformat()


def stamp():
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path.relative_to(D)), "bytes": len(raw), "sha256": sha(raw)}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def signal_stop(signum, frame):
    global STOP
    STOP = True


signal.signal(signal.SIGTERM, signal_stop)
signal.signal(signal.SIGINT, signal_stop)


def capture(label, endpoint, *, binary=False, destination=None):
    prefix = D / "requests" / (stamp() + "-" + label)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    output = destination or prefix.with_suffix(".zip" if binary else ".json")
    assert not output.exists(), output
    argv = ["gh", "api", "--method", "GET", endpoint]
    started = utc()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timeout = False
    try:
        stdout, stderr = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        timeout = True
        proc.kill()
        stdout, stderr = proc.communicate()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(stdout)
    error = prefix.with_suffix(".stderr.log")
    error.write_bytes(stderr)
    command = {
        "argv": argv, "startedAt": started, "finishedAt": utc(),
        "processReturncode": proc.returncode, "deadlineSeconds": 60,
        "deadlineExceeded": timeout, "stdout": ref(output), "stderr": ref(error),
        "operation": "GET_ONLY_NO_RERUN_OR_EXTERNAL_MESSAGE",
    }
    command_path = prefix.with_suffix(".command.json")
    write_json(command_path, command)
    if timeout or proc.returncode:
        return {"ok": False, "output": str(output), "command": ref(command_path)}
    try:
        data = stdout if binary else json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "output": str(output), "command": ref(command_path)}
    return {"ok": True, "data": data, "output": output, "command": ref(command_path)}


def extract_archive(body, directory):
    assert not directory.exists(), directory
    directory.mkdir(parents=True)
    entries = []
    seen = set()
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.testzip() is None
        for item in archive.infolist():
            relative = Path(item.filename)
            assert not relative.is_absolute() and ".." not in relative.parts
            assert not stat.S_ISLNK(item.external_attr >> 16)
            assert str(relative) not in seen
            seen.add(str(relative))
            target = directory / relative
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
                entries.append(ref(target))
    return entries


def checkouts(path):
    lines = path.read_text(errors="replace").splitlines()
    found = []
    for index, line in enumerate(lines):
        if "git log -1 --format=%H" in line:
            for next_index in range(index + 1, min(index + 4, len(lines))):
                match = re.search(r"\b([0-9a-f]{40})\b", lines[next_index])
                if match:
                    found.append({
                        "sha": match.group(1), "commandLine": index + 1,
                        "outputLine": next_index + 1, "rawCommand": line,
                        "rawOutput": lines[next_index], "source": ref(path),
                    })
                    break
    return found


def main():
    started = time.monotonic()
    write_json(D / "collector-process.json", {
        "pid": os.getpid(), "startedAt": utc(), "runId": RUN,
        "maxWaitSeconds": MAX_SECONDS, "perRequestDeadlineSeconds": 60,
        "backoffSeconds": [60, 120, 240], "stopOnSignal": True,
    })
    logs, artifacts, git_commits = {}, {}, {}
    last_fingerprint = None
    delay = 60
    final = None
    while not STOP and time.monotonic() - started < MAX_SECONDS:
        endpoints = {
            "run": f"{REPO}/actions/runs/{RUN}",
            "jobs": f"{REPO}/actions/runs/{RUN}/jobs?per_page=100",
            "artifacts": f"{REPO}/actions/runs/{RUN}/artifacts?per_page=100",
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = {label: pool.submit(capture, label, endpoint) for label, endpoint in endpoints.items()}
            got = {label: future.result() for label, future in futures.items()}
        if not all(item["ok"] for item in got.values()):
            write_json(D / (stamp() + "-incomplete-snapshot.json"), {label: {k: v for k, v in item.items() if k != "data"} for label, item in got.items()})
        else:
            run, jobs, art = (got[label]["data"] for label in ("run", "jobs", "artifacts"))
            assert run["id"] == RUN and run["run_attempt"] == 1 and run["head_sha"] == EXPECTED_HEAD
            assert jobs["total_count"] == len(jobs["jobs"]), "Job response would truncate"
            assert art["total_count"] == len(art["artifacts"]), "Artifact response would truncate"
            progress = {
                "observedAt": utc(), "run": {key: run.get(key) for key in ("id", "head_sha", "run_attempt", "status", "conclusion", "event")},
                "jobs": [{"id": job["id"], "name": job["name"], "status": job["status"], "conclusion": job["conclusion"],
                          "activeSteps": [{key: step.get(key) for key in ("number", "name", "status", "conclusion")} for step in job["steps"] if step["status"] == "in_progress" or step["conclusion"] == "failure"]} for job in jobs["jobs"]],
                "availableArtifacts": [{"id": item["id"], "name": item["name"], "digest": item.get("digest")} for item in art["artifacts"]],
                "rawSnapshotRefs": {label: ref(item["output"]) for label, item in got.items()},
            }
            fingerprint = json.dumps([progress["run"], progress["jobs"], progress["availableArtifacts"]], sort_keys=True)
            if fingerprint != last_fingerprint:
                print(json.dumps({"type": "PROGRESS_CHANGED", **progress}, ensure_ascii=False), flush=True)
                delay = 60
                last_fingerprint = fingerprint
            else:
                delay = min(delay * 2, 240)
            write_json(D / (stamp() + "-snapshot-summary.json"), progress)
            for job in jobs["jobs"]:
                if STOP:
                    break
                if job["status"] != "completed" or job["conclusion"] == "skipped" or str(job["id"]) in logs:
                    continue
                result = capture("job-" + str(job["id"]), f"{REPO}/actions/jobs/{job['id']}/logs", binary=True)
                if result["ok"]:
                    destination = D / "job-logs" / (str(job["id"]) + ".log")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    assert not destination.exists()
                    destination.write_bytes(result["data"])
                    logs[str(job["id"])] = {"job": job["name"], "raw": ref(destination), "checkouts": checkouts(destination), "request": result["command"]}
            for artifact in art["artifacts"]:
                if STOP:
                    break
                if str(artifact["id"]) in artifacts:
                    continue
                result = capture("artifact-" + str(artifact["id"]), f"{REPO}/actions/artifacts/{artifact['id']}/zip", binary=True)
                if not result["ok"]:
                    continue
                directory = D / "artifacts" / str(artifact["id"])
                archive = directory.with_suffix(".zip")
                archive.parent.mkdir(parents=True, exist_ok=True)
                assert not archive.exists()
                archive.write_bytes(result["data"])
                digest = artifact.get("digest")
                digest_matches = digest == "sha256:" + sha(result["data"]) if digest else None
                entries = extract_archive(result["data"], directory)
                metadata = {"githubOriginal": artifact, "archive": ref(archive), "githubDigestMatchesArchive": digest_matches,
                            "uncompressedFiles": entries, "request": result["command"], "diagnosticOnly": artifact["name"].startswith("backend-g3-numerics-")}
                write_json(directory.with_suffix(".metadata.json"), metadata)
                artifacts[str(artifact["id"])] = metadata
            for evidence in logs.values():
                for observation in evidence["checkouts"]:
                    actual = observation["sha"]
                    if actual in git_commits or STOP:
                        continue
                    commit = capture("actual-checkout-" + actual, f"{REPO}/git/commits/{actual}")
                    if commit["ok"]:
                        tree_sha = commit["data"]["tree"]["sha"]
                        tree = capture("actual-root-tree-" + tree_sha, f"{REPO}/git/trees/{tree_sha}")
                        if tree["ok"]:
                            git_commits[actual] = {"actualCheckoutEvidence": observation, "gitCommit": ref(commit["output"]), "gitRootTree": ref(tree["output"]),
                                                  "rootTreeSha": tree_sha, "backendTreeSha": next((item["sha"] for item in tree["data"]["tree"] if item["path"] == "backend"), None)}
            expected_logs = {str(job["id"]) for job in jobs["jobs"] if job["status"] == "completed" and job["conclusion"] != "skipped"}
            complete = run["status"] == "completed" and all(job["status"] == "completed" for job in jobs["jobs"]) and expected_logs == set(logs) and len(artifacts) == art["total_count"]
            write_json(D / "capture-checkpoint.json", {"progress": progress, "logs": logs, "artifacts": list(artifacts), "actualCheckouts": git_commits, "captureComplete": complete})
            if complete:
                all_logs = capture("full-run-logs", f"{REPO}/actions/runs/{RUN}/logs", binary=True)
                if all_logs["ok"]:
                    archive = D / "full-run-logs.zip"
                    assert not archive.exists()
                    archive.write_bytes(all_logs["data"])
                    entries = extract_archive(all_logs["data"], D / "full-run-logs")
                    final = {"capturedAt": utc(), "progress": progress, "jobLogs": logs, "artifacts": artifacts,
                             "actualCheckoutGitObjects": git_commits, "fullRunLogs": {"archive": ref(archive), "files": entries, "request": all_logs["command"]},
                             "collectorCompleted": True, "testOrBusinessAcceptance": "NOT_INFERRED_FROM_COLLECTOR_RC"}
                    break
        until = time.monotonic() + delay
        while not STOP and time.monotonic() < until:
            time.sleep(min(1, until - time.monotonic()))
    if final is None:
        write_json(D / "capture-interrupted-or-incomplete.json", {"observedAt": utc(), "stopSignal": STOP, "elapsedSeconds": time.monotonic() - started, "collectorCompleted": False})
        return 2
    write_json(D / "capture-final.json", final)
    rows = [ref(path) for path in sorted(D.rglob("*")) if path.is_file()]
    write_json(D / "raw-evidence-manifest.json", {"createdAt": utc(), "files": rows, "fileCount": len(rows), "totalBytes": sum(row["bytes"] for row in rows)})
    print(json.dumps({"type": "CAPTURE_COMPLETED", "run": final["progress"]["run"], "jobs": final["progress"]["jobs"], "manifest": ref(D / "raw-evidence-manifest.json")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
