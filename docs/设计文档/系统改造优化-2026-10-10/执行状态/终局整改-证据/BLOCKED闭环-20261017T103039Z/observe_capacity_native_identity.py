"""Read-only native process observations; never starts/stops a product process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
from datetime import datetime, timezone

PS_ARGV = ["ps", "-ax", "-o", "pid=,ppid=,lstart=,stat=,command="]
RUNTIME = Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
PYTHON = str(RUNTIME / "backend/.venv/bin/python")
TAOS = Path("/Users/zhangping/DEV/CLPM-MVP/deploy/docker/tdengine/taos.cfg")
ROW = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+([A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})\s+(\S+)\s+(.+)$"
)


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_owned(lines):
    """Keep only this runtime interpreter's actual uvicorn/Celery command lines."""
    result = []
    for line in lines.splitlines():
        match = ROW.match(line)
        if not match:
            # A relevant malformed line is evidence of a failed observation.
            if PYTHON in line and ("-m celery " in line or "-m uvicorn " in line):
                raise ValueError("relevant native ps row cannot be parsed")
            continue
        pid, ppid, started, state, command = match.groups()
        if not command.startswith(PYTHON + " "):
            continue
        if " -m uvicorn app.main:app " in command:
            role = "api"
        elif " -m celery " in command and " beat " in command:
            role = "beat"
        elif " -m celery " in command and " worker " in command:
            role = "worker-unresolved"
        else:
            continue
        started = " ".join(started.split())
        result.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "startedAtLocal": started,
                "state": state,
                "identity": f"{pid}:{started}",
                "argv": command,
                "role": role,
                "rawLine": line,
            }
        )
    api_pids = {r["pid"] for r in result if r["role"] == "api"}
    masters = {
        r["pid"]
        for r in result
        if r["role"] == "worker-unresolved" and r["ppid"] in api_pids
    }
    for row in result:
        if row["role"] == "worker-unresolved":
            row["role"] = (
                "worker_master"
                if row["pid"] in masters
                else "worker_child"
                if row["ppid"] in masters
                else "unattached_worker"
            )
    return sorted(result, key=lambda r: r["pid"])


def run(argv, timeout=3):
    start = utc()
    try:
        p = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
        return {
            "argv": argv,
            "startedAt": start,
            "finishedAt": utc(),
            "rc": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "startedAt": start,
            "finishedAt": utc(),
            "rc": None,
            "timedOut": True,
            "stdout": (exc.stdout or b"").decode()
            if isinstance(exc.stdout, bytes)
            else exc.stdout or "",
            "stderr": (exc.stderr or b"").decode()
            if isinstance(exc.stderr, bytes)
            else exc.stderr or "",
        }


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--max-seconds", type=float, default=43200)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sha):
        parser.error("a full immutable source SHA is required")
    if not 0.5 <= args.interval <= 2 or args.max_seconds <= 0:
        parser.error("interval must be 0.5..2 and duration positive")
    args.out.mkdir(parents=True, exist_ok=False)
    lsof_dir = args.out / "first-seen-lsof"
    lsof_dir.mkdir()
    native_env = RUNTIME / "backend/.env"
    plan = {
        "schema": "clpm-native-identity-v1",
        "startedAt": utc(),
        "expectedSha": args.expected_sha,
        "runtime": str(RUNTIME),
        "psArgv": PS_ARGV,
        "intervalSeconds": args.interval,
        "maximumAcceptedIntervalSeconds": 5,
        "maxSeconds": args.max_seconds,
        "toolSha256": digest(__file__),
        "nativeEnvSha256": digest(native_env),
        "taosConfigSha256": digest(TAOS),
        "timezoneRaw": run(["date", "+%Z %z"]),
        "timezoneSymlink": os.path.realpath("/etc/localtime"),
        "scope": "actual native ps birth/ancestry; no inference of per-task executor pid",
        "stopFile": str(args.out / "STOP"),
    }
    write(args.out / "plan.json", plan)
    requested = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda number, _frame: requested.append(number))
    seen = set()
    started = time.monotonic()
    previous = None
    maximum_gap = 0
    polls = 0
    errors = []
    reason = None
    path = args.out / "observations.jsonl"
    try:
        with path.open("x", buffering=1) as output:
            while True:
                if requested or (args.out / "STOP").exists():
                    reason = "requested-stop"
                    break
                if time.monotonic() - started >= args.max_seconds:
                    reason = "duration-expired"
                    break
                tick = time.monotonic()
                gap = tick - previous if previous is not None else None
                if gap is not None:
                    maximum_gap = max(maximum_gap, gap)
                previous = tick
                ps = run(PS_ARGV)
                # Do not persist unrelated users' command lines.
                rows = parse_owned(ps.pop("stdout")) if ps["rc"] == 0 else []
                git = run(
                    ["git", "-C", str(RUNTIME), "rev-parse", "HEAD", "HEAD:backend"]
                )
                versions = git["stdout"].splitlines()
                good = (
                    ps["rc"] == 0
                    and git["rc"] == 0
                    and len(versions) == 2
                    and versions[0] == args.expected_sha
                    and digest(native_env) == plan["nativeEnvSha256"]
                    and digest(TAOS) == plan["taosConfigSha256"]
                )
                observation = {
                    "schema": "clpm-native-identity-v1",
                    "sequence": polls,
                    "observedAt": ps["finishedAt"],
                    "monotonicSeconds": tick,
                    "previousPollGapSeconds": gap,
                    "psCommand": ps,
                    "selectedRawPsLines": [r["rawLine"] for r in rows],
                    "members": rows,
                    "gitCommand": git,
                    "sourceShaObserved": versions[0] if versions else None,
                    "backendTreeObserved": versions[1] if len(versions) == 2 else None,
                    "nativeEnvSha256Observed": digest(native_env),
                    "taosConfigSha256Observed": digest(TAOS),
                    "observationValid": good,
                    "firstSeenLsofRefs": [],
                }
                new = [row for row in rows if row["identity"] not in seen]
                if new:
                    pids = ",".join(str(row["pid"]) for row in new)
                    actual = run(
                        ["lsof", "-a", "-p", pids, "-d", "cwd,txt", "-Fn"], timeout=2
                    )
                    dest = lsof_dir / f"{polls:07d}.json"
                    write(dest, {"members": new, "actualLsof": actual})
                    observation["firstSeenLsofRefs"].append(
                        {"path": str(dest), "sha256": digest(dest)}
                    )
                    seen.update(row["identity"] for row in new)
                output.write(json.dumps(observation, ensure_ascii=False) + "\n")
                polls += 1
                if not good:
                    errors.append(
                        {
                            "sequence": polls - 1,
                            "reason": "source/env/ps identity mismatch",
                        }
                    )
                    reason = "invalid-observation"
                    break
                time.sleep(max(0, args.interval - (time.monotonic() - tick)))
    except Exception as exc:
        reason = "exception"
        errors.append({"type": type(exc).__name__, "message": str(exc)})
    summary = {
        "schema": "clpm-native-identity-v1",
        "finishedAt": utc(),
        "expectedSha": args.expected_sha,
        "stopReason": reason,
        "signals": requested,
        "pollCount": polls,
        "maximumActualPollGapSeconds": maximum_gap,
        "elapsedSeconds": time.monotonic() - started,
        "errors": errors,
        "observations": {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": digest(path),
        },
        "planSha256": digest(args.out / "plan.json"),
        "acceptance": "PENDING independent per-business-span 7 fixed birth identities/ancestry and <=5s coverage; transitions outside spans allowed",
    }
    write(args.out / "summary.json", summary)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
