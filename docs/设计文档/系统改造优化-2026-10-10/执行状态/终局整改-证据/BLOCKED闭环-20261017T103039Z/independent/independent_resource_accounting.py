"""Read-only independent memory upper-bound arithmetic from original sampler bytes.

The accepted method is exactly three container cgroup memory.current charges plus
seven host application PID RSS values. This module never calls Docker, ps, a DB,
an API, a queue, or the Keeper's validator; all inputs are saved artifacts.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from independent_support import (
    InvalidEvidence,
    MissingEvidence,
    ReadSet,
    instant,
    result_rc,
    verdict,
    write_new,
)

CONTAINERS = {
    "clpm-mvp-postgres",
    "clpm-mvp-tdengine",
    "clpm-f02-redis-20261017-s01",
}
ROLE_COUNTS = {"api": 1, "worker_master": 1, "worker_child": 4, "beat": 1}
MEMORY_BUDGET = 6 * 1024**3
MAXIMUM_ACCOUNTING = MEMORY_BUDGET * 4 // 5
MINIMUM_AVAILABLE = 1024**3


def reconcile(
    samples, members, profile, submitted_at=None, terminal_at=None, recovery_seconds=0
):
    issues, arithmetic = [], []

    def require(condition, code, detail, missing=False):
        if not condition:
            issues.append(
                {
                    "level": "BLOCKED" if missing else "REWORK",
                    "code": code,
                    "detail": detail,
                }
            )
        return bool(condition)

    require(
        profile.get("memoryBudgetBytes") == MEMORY_BUDGET,
        "frozen-budget",
        profile.get("memoryBudgetBytes"),
    )
    require(
        profile.get("maximumAccountingBytes") == MAXIMUM_ACCOUNTING,
        "frozen-80pct",
        profile.get("maximumAccountingBytes"),
    )
    require(
        profile.get("minimumVmMemAvailableBytes") == MINIMUM_AVAILABLE,
        "frozen-available-floor",
        profile.get("minimumVmMemAvailableBytes"),
    )
    require(
        set(profile.get("containers", [])) == CONTAINERS,
        "frozen-container-scope",
        profile.get("containers"),
    )
    require(
        Counter(member.get("role") for member in members) == ROLE_COUNTS,
        "frozen-pid-scope",
        members,
        missing=True,
    )
    pid_roles = {member["pid"]: member["role"] for member in members}
    require(
        len(pid_roles) == len(members) == 7,
        "frozen-unique-pids",
        list(pid_roles),
        missing=True,
    )
    require(len(samples) >= 2, "samples-absent", len(samples), missing=True)
    if not samples:
        return {
            "resourceArtifactVerdict": verdict(issues),
            "issues": issues,
            "samples": arithmetic,
        }
    times = [instant(sample["observedAt"]) for sample in samples]
    for before, after in zip(times, times[1:]):
        require(
            0 < (after - before).total_seconds() <= 5,
            "sample-order-gap",
            {"before": before.isoformat(), "after": after.isoformat()},
        )
    if submitted_at is not None:
        require(
            (instant(submitted_at) - times[0]).total_seconds() >= 60,
            "pre60",
            submitted_at,
        )
    if terminal_at is not None:
        require(
            (times[-1] - instant(terminal_at)).total_seconds() >= 30,
            "post30",
            terminal_at,
        )
    if recovery_seconds:
        require(
            (times[-1] - times[0]).total_seconds() >= recovery_seconds >= 300,
            "recovery300",
            recovery_seconds,
        )
    for index, sample in enumerate(samples):
        label = {"index": index, "at": sample["observedAt"]}
        cgroups = sample.get("containerCgroups", {})
        require(set(cgroups) == CONTAINERS, "container-scope", label, missing=True)
        container_bytes = {}
        for name in CONTAINERS:
            info = cgroups.get(name, {})
            raw = info.get("raw", {})
            lines = raw.get("stdout", "").splitlines()
            expected_command = [
                "docker",
                "exec",
                name,
                "cat",
                "/sys/fs/cgroup/memory.current",
                "/sys/fs/cgroup/memory.stat",
                "/sys/fs/cgroup/memory.events",
            ]
            require(
                raw.get("rc") == 0 and raw.get("argv") == expected_command,
                "cgroup-raw-command",
                {**label, "container": name},
                missing=True,
            )
            current = int(lines[0]) if lines and lines[0].isdigit() else None
            require(
                current is not None,
                "cgroup-current-missing",
                {**label, "container": name},
                missing=True,
            )
            if current is not None:
                container_bytes[name] = current
            # Parsed metadata is a cross-check, never the arithmetic's authority.
            require(
                type(info.get("memoryCurrentBytes")) is int
                and current == info.get("memoryCurrentBytes"),
                "cgroup-current-raw-metadata",
                {**label, "container": name},
            )
            fields = {}
            for line in lines[1:]:
                match = re.fullmatch(r"([a-z_]+)\s+(\d+)", line.strip())
                if match:
                    fields[match[1]] = int(match[2])
            for key in ("oom", "oom_kill", "oom_group_kill"):
                require(
                    key in fields,
                    "oom-raw-missing",
                    {**label, "container": name, "key": key},
                    missing=True,
                )
                if key in fields:
                    require(
                        fields[key] == 0,
                        "oom-event",
                        {**label, "container": name, "key": key, "count": fields[key]},
                    )
                    require(
                        info.get("fields", {}).get(key) == fields[key],
                        "oom-raw-metadata",
                        {**label, "container": name, "key": key},
                    )
        host = {}
        for line in sample.get("ownedProcesses", []):
            pieces = line.strip().split(maxsplit=5)
            if len(pieces) != 6:
                continue
            try:
                pid, ppid, rss_kib = int(pieces[0]), int(pieces[1]), int(pieces[3])
            except ValueError:
                continue
            command = pieces[5]
            is_application = "-m uvicorn app.main:app" in command or (
                "-m celery " in command
                and (" worker " in command or " beat " in command)
            )
            if not is_application:
                continue  # Collector/receiver/driver are separately disclosed observers.
            require(
                pid not in host and pid in pid_roles,
                "unknown-duplicate-application-pid",
                {**label, "pid": pid},
            )
            require(rss_kib >= 0, "negative-rss", {**label, "pid": pid})
            if pid in pid_roles:
                role = pid_roles[pid]
                expected_entry = (
                    "-m uvicorn app.main:app"
                    if role == "api"
                    else " beat "
                    if role == "beat"
                    else " worker "
                )
                require(
                    expected_entry in command,
                    "pid-role-changed",
                    {**label, "pid": pid, "role": role},
                )
            host[pid] = {"rssBytes": rss_kib * 1024, "ppid": ppid, "argv": command}
        require(
            set(host) == set(pid_roles),
            "pid-sample-scope",
            {**label, "observed": sorted(host), "expected": sorted(pid_roles)},
            missing=True,
        )
        if set(host) == set(pid_roles):
            api = next(pid for pid, role in pid_roles.items() if role == "api")
            master = next(
                pid for pid, role in pid_roles.items() if role == "worker_master"
            )
            require(host[master]["ppid"] == api, "master-parent", label)
            for pid, role in pid_roles.items():
                if role == "worker_child":
                    require(
                        host[pid]["ppid"] == master,
                        "pool-parent",
                        {**label, "pid": pid},
                    )
                if role == "beat":
                    require(host[pid]["ppid"] == api, "beat-parent", label)
        meminfo = sample.get("tdProcMeminfo", {})
        require(
            meminfo.get("rc") == 0
            and meminfo.get("argv")
            == ["docker", "exec", "clpm-mvp-tdengine", "cat", "/proc/meminfo"],
            "vm-raw-command",
            label,
            missing=True,
        )
        match = re.search(
            r"^MemAvailable:\s+(\d+)\s+kB$", meminfo.get("stdout", ""), re.M
        )
        available = int(match[1]) * 1024 if match else None
        require(available is not None, "available-missing", label, missing=True)
        if available is not None:
            require(
                available >= MINIMUM_AVAILABLE,
                "vm-available-floor",
                {**label, "bytes": available},
            )
        complete = len(container_bytes) == 3 and set(host) == set(pid_roles)
        total = (
            sum(container_bytes.values())
            + sum(member["rssBytes"] for member in host.values())
            if complete
            else None
        )
        if total is not None:
            require(
                total <= MAXIMUM_ACCOUNTING,
                "accounting-over-80pct",
                {**label, "bytes": total, "maximum": MAXIMUM_ACCOUNTING},
            )
        arithmetic.append(
            {
                **label,
                "containerCurrentBytes": container_bytes,
                "hostPidRssBytes": {
                    str(pid): value["rssBytes"] for pid, value in host.items()
                },
                "conservativeUpperBoundBytes": total,
                "vmAvailableBytes": available,
            }
        )
    return {
        "resourceArtifactVerdict": verdict(issues),
        "issues": issues,
        "samples": arithmetic,
        "memoryBudgetBytes": MEMORY_BUDGET,
        "maximumAccountingBytes": MAXIMUM_ACCOUNTING,
        "minimumVmMemAvailableBytes": MINIMUM_AVAILABLE,
        "boundary": "Independent raw-byte arithmetic only. Not exact physical RSS; no anon/shmem double count. PID loaded SHA, container identity continuity, CPU/IO/connections, log exhaustion and cache/HIT contracts require their separate original evidence.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        plan = json.loads(Path(args.plan).read_text())
        reads = ReadSet(args.evidence_root)
        profile = reads.json(plan["resourceProfile"])
        runtime = reads.json(plan["runtimeProfile"])
        raw_path = reads.path(plan["resourcesJsonl"])
        samples = [
            json.loads(line)
            for line in raw_path.read_text().splitlines()
            if line.strip()
        ]
        result = reconcile(
            samples,
            runtime["runtime"]["members"],
            profile,
            plan.get("submittedAt"),
            plan.get("terminalAt"),
            plan.get("recoverySeconds", 0),
        )
        reads.verify_unchanged()
        result["readSet"] = reads.files
    except (MissingEvidence, KeyError, OSError) as exc:
        result = {
            "resourceArtifactVerdict": "BLOCKED",
            "issues": [{"level": "BLOCKED", "detail": str(exc)}],
        }
    except (InvalidEvidence, ValueError, TypeError) as exc:
        result = {
            "resourceArtifactVerdict": "REWORK",
            "issues": [{"level": "REWORK", "detail": str(exc)}],
        }
    write_new(args.out, result)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "samples"},
            ensure_ascii=False,
        )
    )
    return result_rc(result["resourceArtifactVerdict"])


if __name__ == "__main__":
    raise SystemExit(main())
