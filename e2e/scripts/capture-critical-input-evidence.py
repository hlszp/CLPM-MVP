"""Read real CI input policy/package rows; never query result/metric tables."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
SQL_HASHES = {
    "critical-input-policy.sql": "d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294",
    "critical-input-dataset-row.sql": (
        "9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff"
    ),
}
SOURCES = [
    ".github/workflows/ci.yml",
    "backend/uv.lock",
    "backend/app/services/calc_snapshot.py",
    "backend/app/tasks/kpi_calc.py",
    "backend/app/services/data_planner.py",
    "backend/app/services/data_source/logical_wide_builder.py",
    "backend/app/services/metric_calculator/time_axis.py",
    "backend/app/services/algorithm_config.py",
    "backend/app/services/assessment_profile.py",
    "e2e/scripts/prepare-critical-fixture.py",
    "e2e/scripts/capture-critical-input-evidence.py",
    "scripts/run-critical-e2e.sh",
    "e2e/tests/critical-business.spec.ts",
    *["e2e/scripts/" + name for name in SQL_HASHES],
]
DISCOVERY = """\\set ON_ERROR_STOP 1
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SELECT jsonb_build_object('section','discovery-transaction','database',current_database(),
 'readOnly',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),
 'timezone',current_setting('TimeZone'));
SELECT jsonb_build_object('section','dataset-candidates','rows',
 COALESCE(jsonb_agg(to_jsonb(s) ORDER BY s.id),'[]'::jsonb))
FROM calculation_dataset_snapshot s WHERE s.loop_id=:'loop_id'::uuid
 AND s.kind='KPI_GRID' AND s.ts_start=:'ts_start'::timestamp AND s.ts_end=:'ts_end'::timestamp;
COMMIT;
"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def utc(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def scope(env, fixture, head):
    exact = {
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "E2E_EXECUTION_ENV": "disposable-github-ci",
        "POSTGRES_DB": "clpm_critical_e2e",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "clpm",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "TDENGINE_PORT": "6030",
        "TDENGINE_DB": "clpm_critical_e2e_ts",
    }
    if any(env.get(k) != v for k, v in exact.items()) or any(
        env.get(k) not in {"localhost", "127.0.0.1"}
        for k in ("POSTGRES_HOST", "REDIS_HOST", "TDENGINE_HOST")
    ):
        raise ValueError("refuse outside exact disposable CI services")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", head)
        or head != env.get("GITHUB_SHA")
        or head != env.get("CLPM_BUILD_CODE_SHA")
        or head != fixture.get("candidateSha")
        or fixture.get("environment") != "disposable-github-ci"
        or fixture.get("synthetic") is not True
        or fixture.get("inputOnly") is not True
    ):
        raise ValueError("fixture/actual checkout identity differs")
    run, attempt = env.get("GITHUB_RUN_ID", ""), env.get("GITHUB_RUN_ATTEMPT", "")
    if (
        not run.isdigit()
        or not attempt.isdigit()
        or fixture.get("loopTagName") != f"CRITICAL-E2E-{run}-{attempt}-FLOW001"
    ):
        raise ValueError("fixture run identity differs")
    UUID(fixture["loopId"])
    if utc(fixture["windowEnd"]) <= utc(fixture["windowStart"]):
        raise ValueError("invalid input window")


def command(argv, folder, child_env):
    folder.mkdir(parents=True, exist_ok=False)
    record = {"argv": argv, "startedAtUtc": datetime.now(UTC).isoformat()}
    with (folder / "stdout.log").open("wb") as out, (folder / "stderr.log").open("wb") as err:
        try:
            rc = subprocess.run(
                argv, cwd=ROOT, env=child_env, stdout=out, stderr=err, timeout=45
            ).returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            rc = 124 if isinstance(exc, subprocess.TimeoutExpired) else 127
            err.write((type(exc).__name__ + ": " + str(exc) + "\n").encode())
    record.update(
        rc=rc,
        finishedAtUtc=datetime.now(UTC).isoformat(),
        stdoutSha256=sha(folder / "stdout.log"),
        stderrSha256=sha(folder / "stderr.log"),
    )
    if "-f" in argv:
        record["executedSqlSha256"] = sha(argv[argv.index("-f") + 1])
    write(folder / "command.json", record)
    if rc:
        raise RuntimeError(f"command failed rc={rc}: {folder.name}")
    return (folder / "stdout.log").read_text()


def objects(raw):
    return [
        json.loads(line, parse_float=Decimal) for line in raw.splitlines() if line.startswith("{")
    ]


def identity(row):
    if (
        row.get("database") != "clpm_critical_e2e"
        or row.get("readOnly") != "on"
        or row.get("isolation") != "repeatable read"
        or row.get("timezone") != "UTC"
    ):
        raise ValueError("actual PG transaction is outside RR READ ONLY UTC CI database")


def policy(rows, fixture):
    wanted = {
        "transaction-before": 1,
        "transaction-after": 1,
        "held-policy": 2,
        "synthetic-loop-input": 1,
        "configuration-revision": 1,
        "active-release-input": 1,
        "algorithm-parameter-input": 1,
        "metric-config-input": 1,
        "enabled-configuration-overrides": 1,
        "loop-type-weights-input": 1,
    }
    if Counter(r.get("section") for r in rows) != wanted:
        raise ValueError("policy SQL output incomplete")
    edges = [r for r in rows if r["section"].startswith("transaction-")]
    for r in edges:
        identity(r)
    if (
        edges[0]["snapshot"] != edges[1]["snapshot"]
        or edges[0]["transactionTimestampUtc"] != edges[1]["transactionTimestampUtc"]
    ):
        raise ValueError("policy transaction identity changed")
    held = [r for r in rows if r["section"] == "held-policy"]
    if {r["requestedKey"] for r in held} != {
        "data.held_too_long_fast_seconds",
        "data.held_too_long_slow_seconds",
    }:
        raise ValueError("held-policy presence/absence facts incomplete")
    loops = next(r["rows"] for r in rows if r["section"] == "synthetic-loop-input")
    if (
        len(loops) != 1
        or loops[0]["id"] != fixture["loopId"]
        or loops[0]["tag_name"] != fixture["loopTagName"]
        or loops[0]["description"] != "synthetic CI INPUT: real algorithm, disposable runner only"
    ):
        raise ValueError("actual synthetic loop differs")
    return [r for r in rows if not r["section"].startswith("transaction-")]


def candidate(rows, fixture):
    identity(next(r for r in rows if r.get("section") == "discovery-transaction"))
    found = next(r["rows"] for r in rows if r.get("section") == "dataset-candidates")
    if len(found) != 1:
        raise ValueError(f"expected one input package row, got {len(found)}")
    row = found[0]
    if (
        row["kind"] != "KPI_GRID"
        or row["loop_id"] != fixture["loopId"]
        or utc(row["ts_start"]) != utc(fixture["windowStart"])
        or utc(row["ts_end"]) != utc(fixture["windowEnd"])
    ):
        raise ValueError("input package scope differs")
    UUID(row["id"])
    return row


def package(row, fixture, root):
    sys.path.insert(0, str(ROOT / "backend"))
    from app.services.calc_snapshot import _read_files, compute_input_hash, executable_build_digest

    files = []
    for suffix in (".npz", ".manifest.json"):
        found = list(root.rglob(row["id"] + suffix))
        if len(found) != 1 or not found[0].resolve().is_relative_to(root.resolve()):
            raise ValueError("package file missing/ambiguous/outside root")
        files.append(found[0])
    manifest, arrays = _read_files(*files)
    if (
        manifest["kind"] != "KPI_GRID"
        or manifest["loopId"] != fixture["loopId"]
        or utc(manifest["window"]["tsStart"]) != utc(fixture["windowStart"])
        or utc(manifest["window"]["tsEnd"]) != utc(fixture["windowEnd"])
        or manifest["schemaVersion"] != row["schema_version"]
        or row["cleaned_at"] is not None
        or str(manifest["binding"]["datasetRef"])[:64] != row["dataset_ref"]
        or manifest.get("createdByRecordId") != row["created_by_record_id"]
        or manifest["source"]["executableBuildDigest"] != executable_build_digest()
        or compute_input_hash(manifest, arrays) != row["input_hash"]
        or sum(f.stat().st_size for f in files) != row["size_bytes"]
    ):
        raise ValueError("real dataset row/package/hash/build binding differs")
    return {
        "snapshotId": row["id"],
        "inputHash": row["input_hash"],
        "files": [
            {"path": str(f.relative_to(root.parent)), "bytes": f.stat().st_size, "sha256": sha(f)}
            for f in files
        ],
    }


def capture(phase, out, env, run=command, bind=package):
    out.mkdir(parents=True, exist_ok=False)
    header = {"phase": phase, "complete": False, "startedAtUtc": datetime.now(UTC).isoformat()}
    write(out / "header.json", header)
    for name in SQL_HASHES:
        (out / name).write_bytes((ROOT / "e2e/scripts" / name).read_bytes())
    # This reduced child environment cannot inherit PGHOSTADDR/PGSERVICE/psqlrc overrides.
    child = {
        "PATH": env.get("PATH", ""),
        "LANG": "C.UTF-8",
        "PGCONNECT_TIMEOUT": "10",
        "PGPASSWORD": env.get("POSTGRES_PASSWORD", ""),
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=30000",
    }

    def call(argv, name):
        return run(argv, out / name, child)

    try:
        base = Path(env["RUNNER_TEMP"]).resolve() / "critical-e2e"
        if (
            Path(env["E2E_CRITICAL_FIXTURE"]).resolve() != base / "fixture.json"
            or Path(env["CLPM_CALC_SNAPSHOT_DIR"]).resolve() != base / "calc-snapshots"
            or out.resolve() != base / "input-evidence" / phase
        ):
            raise ValueError("artifacts must be in this disposable runner's critical-e2e root")
        fixture = json.loads(Path(env["E2E_CRITICAL_FIXTURE"]).read_text())
        head = call(["git", "rev-parse", "HEAD"], "git-head").strip()
        header["actualCheckoutSha"] = head
        scope(env, fixture, head)
        call(["git", "ls-files", "--error-unmatch", "--", *SOURCES], "tracked-sources")
        call(["git", "diff", "--quiet", "HEAD"], "clean-sources")
        header.update(
            actualCheckoutSha=head,
            fixtureSha256=sha(env["E2E_CRITICAL_FIXTURE"]),
            sources={name: sha(ROOT / name) for name in SOURCES},
        )
        for name, expected in SQL_HASHES.items():
            if sha(ROOT / "e2e/scripts" / name) != expected:
                raise ValueError("frozen SQL bytes changed")
        psql = shutil.which("psql", path=env.get("PATH"))
        if not psql:
            raise RuntimeError("psql tool unavailable; no query executed")
        call([psql, "--version"], "psql-version")
        prefix = [
            psql,
            "-X",
            "--no-password",
            "-A",
            "-t",
            "-h",
            env["POSTGRES_HOST"],
            "-p",
            "5432",
            "-U",
            "clpm",
            "-d",
            "clpm_critical_e2e",
        ]
        variables = {
            "loop_id": str(UUID(fixture["loopId"])),
            "ts_start": utc(fixture["windowStart"]).replace(tzinfo=None).isoformat(),
            "ts_end": utc(fixture["windowEnd"]).replace(tzinfo=None).isoformat(),
        }

        def query(name, text, values):
            path = out / (name + ".sql")
            path.write_bytes(text)
            args = [v for k, val in values.items() for v in ("-v", k + "=" + val)]
            return objects(call([*prefix, *args, "-f", str(path)], name))

        policy_rows = query(
            "policy", (ROOT / "e2e/scripts/critical-input-policy.sql").read_bytes(), variables
        )
        content = policy(policy_rows, fixture)
        header["policySections"] = dict(Counter(r["section"] for r in policy_rows))
        if phase == "after":
            row = candidate(query("dataset-discovery", DISCOVERY.encode(), variables), fixture)
            actual = query(
                "dataset-row",
                (ROOT / "e2e/scripts/critical-input-dataset-row.sql").read_bytes(),
                {**variables, "snapshot_id": row["id"]},
            )
            transaction = next(r for r in actual if r.get("section") == "input-package-transaction")
            identity(transaction)
            if (
                transaction.get("requestedLoopId") != fixture["loopId"]
                or transaction.get("requestedSnapshotId") != row["id"]
            ):
                raise ValueError("dataset row transaction requested another loop/package")
            records = [r for r in actual if "id" in r]
            if len(records) != 1 or records[0] != row:
                raise ValueError("dataset discovery/direct row differ")
            header["package"] = bind(row, fixture, Path(env["CLPM_CALC_SNAPSHOT_DIR"]))
            before = policy(objects((out.parent / "before/policy/stdout.log").read_text()), fixture)
            if before != content:
                raise ValueError("actual PG input policy changed between before and after")
            header["policyBeforeAfterEqual"] = True
        header["complete"] = True
    except BaseException as exc:
        header["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        header["finishedAtUtc"] = datetime.now(UTC).isoformat()
        write(out / "header.json", header)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    capture(args.phase, args.output, dict(os.environ))
