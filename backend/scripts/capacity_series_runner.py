"""Auditable F02 series orchestration (RR-02 v4).

Import/check-only is read-only. Formal execution requires a frozen protocol,
fresh configuration and applicable CI. Offline adapters are explicitly marked;
they can never produce real capacity acceptance. Historical series are not read
or resumed. All operational helpers remain separately hash-bound.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

CONDITIONS = (
    "ledger",
    "source",
    "run_binding",
    "algorithm_version",
    "expected_status",
    "latest",
    "nodes",
    "task_terminal",
    "parent_terminal",
)
HELPERS = {
    "lifecycle": "capacity_lifecycle.py",
    "brokerGuard": "capacity_keeper_guards.py",
    "wrapper": "start_capacity_api_formal_with_wait.py",
    "starter": "start_capacity_api_formal.py",
    "validator": "validate_capacity_batch.py",
    "receiver": "f02/receive_capacity_events.py",
    "collector": "f02/collect_capacity_evidence.py",
    "cacheCapture": "f02/capture_capacity_cache.py",
    "runtimeProfile": "capture_capacity_runtime_profile.py",
    "inputAudit": "f02/audit_capacity_inputs.py",
}
INPUTS = {
    "plan": "f02/series-s01/planned-batches.json",
    "profile": "f02/series-s01/resource-profile-plan.json",
    "generated": "f02/series-s01/input-generation-complete.json",
    "extras": "f02/extra-inputs/complete.json",
    "audited": "f02/input-series-audit-v2/progress.json",
}


REQUIRED_STEPS = (
    "COLD_CACHE_CLEAR",
    "CACHE_BEFORE",
    "RUNTIME_PROFILE",
    "PRIVATE_BROKER_QUIET",
    "OWNED_OBSERVER_CLEANUP",
    "FINAL_300S_RECOVERY",
)


class ContractError(RuntimeError):
    """A preflight/evidence/series failure that must not be swallowed."""


def require(condition, message):
    if not condition:
        raise ContractError(message)


def now():
    return datetime.now(UTC).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value, *, replace=False):
    with Path(path).open("w" if replace else "x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def append(path, value):
    with Path(path).open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def instant(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def manifest_outcome(value):
    """Preserve the bound benchmark's existing EVALUATED*/INCONCLUSIVE* semantics."""
    for prefix in ("EVALUATED", "INCONCLUSIVE"):
        if value.startswith(prefix):
            return prefix
    raise ContractError(f"unknown manifest expectedStatus: {value!r}")


def bindings_digest(protocol):
    """Admission receipts may change; reviewed execution semantics may not."""
    keys = (
        "candidateSha",
        "runtimeDir",
        "eligibleIds",
        "requiredBatches",
        "sloSeconds",
        "memoryBudgetBytes",
        "tools",
        "methodBindings",
        "execution",
        "capacityMatrix",
        "resourceBudget",
        "invariants",
        "scopeLimits",
    )
    core = {key: protocol[key] for key in keys if key in protocol}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Context:
    protocol_path: Path
    protocol: dict
    evidence: Path
    runtime: Path
    python: Path
    cwd: Path
    output_parent: Path
    tools: dict
    inputs: dict
    plan: list
    profile: dict
    eligible: frozenset
    protocol_sha256: str


def verify_bindings(ctx):
    """Rechecked before every batch, including transitive helpers and inputs."""
    require(digest(ctx.protocol_path) == ctx.protocol_sha256, "protocol changed during series")
    for group in (ctx.tools, ctx.inputs, ctx.protocol["methodBindings"]):
        for name, item in group.items():
            path = Path(item["path"])
            require(path.is_absolute(), f"{name}: binding must use an absolute path")
            require(path.is_file(), f"{name}: missing bound file: {path}")
            expected = item.get("sha256", "")
            require(bool(re.fullmatch("[0-9a-f]{64}", expected)), f"{name}: invalid sha256")
            require(digest(path) == expected, f"{name}: SHA-256 mismatch: {path}")


def runtime_identity(ctx):
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ctx.runtime, text=True, timeout=10
    ).strip()
    require(head == ctx.protocol["candidateSha"], "runtime candidate SHA mismatch")
    dirty = subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD", "--", "backend"],
        cwd=ctx.runtime,
        text=True,
        timeout=10,
    ).strip()
    require(not dirty, "runtime backend has tracked changes")
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "backend"],
        cwd=ctx.runtime,
        text=True,
        timeout=10,
    ).splitlines()
    require(not any(p.endswith(".py") for p in untracked), "untracked runtime Python source")


def load_contract(protocol_path, evidence_root=None):
    path = Path(protocol_path).resolve()
    protocol_bytes = path.read_bytes()
    protocol = json.loads(protocol_bytes)
    protocol_sha256 = hashlib.sha256(protocol_bytes).hexdigest()
    execution = protocol["execution"]
    evidence = Path(execution["evidenceRoot"]).resolve()
    require(
        evidence_root is None or Path(evidence_root).resolve() == evidence,
        "evidence root differs from protocol",
    )
    runtime = Path(protocol["runtimeDir"]).resolve()
    # Keep the venv spelling when spawning; resolving this symlink loses the venv.
    python = Path(execution["python"]).absolute()
    tools = protocol["tools"]
    inputs = execution["inputs"]
    ctx = Context(
        path,
        protocol,
        evidence,
        runtime,
        python,
        Path(execution["cwd"]).resolve(),
        Path(execution["outputParent"]).resolve(),
        tools,
        inputs,
        [],
        {},
        frozenset(),
        protocol_sha256,
    )
    require(ctx.cwd == runtime / "backend", "cwd differs from runtime/backend")
    require(
        Path(tools["runner"]["path"]).resolve() == Path(__file__).resolve(),
        "runner entrypoint differs from protocol",
    )
    require(python.resolve() == Path(sys.executable).resolve(), "Python interpreter mismatch")
    require(ctx.output_parent.is_relative_to(evidence), "output must stay in test evidence root")
    require(
        not ctx.output_parent.is_relative_to(evidence / "f02/series-s01/formal"),
        "historical formal directory is forbidden",
    )
    for key, relative in HELPERS.items():
        require(
            Path(tools[key]["path"]).resolve() == evidence / relative,
            f"{key}: executable path differs from contract",
        )
    require(
        Path(tools["benchmark"]["path"]).resolve()
        == runtime / "backend/scripts/kpi_capacity_benchmark.py",
        "benchmark path mismatch",
    )
    for key, relative in INPUTS.items():
        require(
            Path(inputs[key]["path"]).resolve() == evidence / relative,
            f"{key}: input path mismatch",
        )
    require(
        set(protocol["methodBindings"])
        == {
            "methodErratum01",
            "supplement02",
            "supplement03",
            "supplement04",
            "supplement05",
            "supplement06",
            "historyInputRefs",
            "fourOracles",
        },
        "method bindings missing",
    )
    require(
        protocol["requiredBatches"] == 20 and protocol["sloSeconds"] == 600,
        "20 batches / 600s contract changed",
    )
    require(protocol["memoryBudgetBytes"] == 6 * 1024**3, "6GiB budget changed")
    require(not protocol["scopeLimits"].get("trimmedSteps"), "required steps cannot be trimmed")
    require(
        set(execution["requiredEvidenceSteps"]) == set(REQUIRED_STEPS),
        "required evidence steps missing",
    )
    ids = protocol["eligibleIds"]
    require(len(ids) == len(set(ids)) == 1000, "1000 unique eligible IDs required")
    for value in ids:
        require(str(UUID(value)) == value, "noncanonical eligible UUID")
    verify_bindings(ctx)
    plan = read(inputs["plan"]["path"])["batches"]
    require([p["batch"] for p in plan] == list(range(1, 21)), "batch IDs must be exactly 1..20")
    require(len({instant(p["ts_start"]) for p in plan}) == 20, "duplicate input windows")
    require(
        Counter((p["scenario"], p["cache"]) for p in plan)
        == Counter({(s, c): 5 for s in ("a", "b") for c in ("cold", "hot")}),
        "A/B x cold/hot x five matrix changed",
    )
    for item in plan:
        require(
            item["required"] is True
            and item["expected_loops"] == 1000
            and item["slo_seconds"] == 600,
            "batch denominator/requirement changed",
        )
        require(
            instant(item["ts_end"]) - instant(item["ts_start"]) == timedelta(hours=1),
            "batch window is not one hour",
        )
        manifest = evidence / "f02/series-s01/input-manifests" / item["manifest_name"]
        key = f"manifest_{item['batch']:02d}"
        require(Path(inputs[key]["path"]).resolve() == manifest, "manifest binding mismatch")
        rows = read(manifest)["rows"]
        require(len(rows) == len({r["loopTag"] for r in rows}) == 1000, "manifest duplicates")
        expected = (
            {"EVALUATED": 1000}
            if item["scenario"] == "a"
            else {
                "EVALUATED": 850,
                "INCONCLUSIVE": 150,
            }
        )
        require(
            Counter(manifest_outcome(r["expectedStatus"]) for r in rows) == expected,
            "manifest expected-status histogram changed",
        )
    generated, extras, audited = [
        read(inputs[k]["path"]) for k in ("generated", "extras", "audited")
    ]
    require(
        generated["prepared"] == 20 and generated["plan_sha256"] == digest(inputs["plan"]["path"]),
        "input generation is not bound to this plan",
    )
    require(extras["preparedAndAudited"] == 4, "extra input preparation incomplete")
    require(
        audited["passed"] == audited["required"] == len(audited["results"]) == 20
        and {r["batch"] for r in audited["results"]} == set(range(1, 21)),
        "input audit incomplete",
    )
    for result in audited["results"]:
        batch_id = result["batch"]
        require(
            result["status"] == "PASS"
            and result["rc"] == 0
            and result["manifestSha256"] == inputs[f"manifest_{batch_id:02d}"]["sha256"],
            "input audit/manifest mismatch",
        )
        audit_binding = inputs[f"audit_{batch_id:02d}"]
        audit_report = read(audit_binding["path"])
        require(
            result["reportSha256"] == audit_binding["sha256"]
            and audit_report["status"] == "PASS"
            and audit_report["globalEligible"] == 1000
            and audit_report["failures"] == []
            and audit_report["manifestSha256"] == result["manifestSha256"],
            "raw input audit is not bound to successful full denominator",
        )
    profile = read(inputs["profile"]["path"])
    require(
        profile["memoryBudgetBytes"] == protocol["memoryBudgetBytes"]
        and profile["maximumAccountingBytes"] <= int(protocol["memoryBudgetBytes"] * 0.8)
        and profile["minimumVmMemAvailableBytes"] >= 1024**3,
        "resource limits weakened",
    )
    require(
        profile["sampling"]
        == {
            "targetSeconds": 2,
            "maximumGapSeconds": 5,
            "preSubmissionMinimumSeconds": 60,
            "postTerminalMinimumSeconds": 30,
            "finalSeriesRecoveryMinimumSeconds": 300,
        },
        "sampling/recovery contract changed",
    )
    ctx = Context(
        path,
        protocol,
        evidence,
        runtime,
        python,
        ctx.cwd,
        ctx.output_parent,
        tools,
        inputs,
        plan,
        profile,
        frozenset(ids),
        protocol_sha256,
    )
    runtime_identity(ctx)
    return ctx


def require_ready(ctx):
    p = ctx.protocol
    require(p["formalReady"] is True and not p["readinessPending"], "formal admission is pending")
    require(p["configFreeze"]["status"] == "FROZEN", "fresh config freeze missing")
    for key in ("protocolReview", "applicableCi"):
        record = p[key]
        require(record["status"] == "PASS", f"{key} is not PASS")
        require(digest(record["path"]) == record["sha256"], f"{key} receipt hash mismatch")
    require(p["applicableCi"]["headSha"] == p["candidateSha"], "CI candidate mismatch")
    ci = read(p["applicableCi"]["path"])
    require(
        ci.get("head_sha", ci.get("headSha")) == p["candidateSha"]
        and ci.get("conclusion") == "success"
        and ci.get("status") == "completed",
        "raw CI result does not apply to candidate",
    )
    review = read(p["protocolReview"]["path"])
    require(
        review.get("status") == "PASS"
        and review.get("nonAuthor") is True
        and review.get("runnerSha256") == ctx.tools["runner"]["sha256"]
        and review.get("bindingsSha256") == bindings_digest(p),
        "independent review does not bind this execution contract",
    )


def validate_product(ctx, item, batch, rc):
    report_path = batch / "product-report.json"
    report = read(report_path)
    started = instant(read(batch / "batch-start.json")["startedAt"])
    require(
        started <= instant(report["observedAt"]) <= datetime.now(UTC) + timedelta(seconds=60),
        "stale/future product report",
    )
    require(str(UUID(report["parentTaskId"])) == report["parentTaskId"], "invalid parent identity")
    require(report["completionReason"] == "BARRIER_PASSED", "business barrier failed")
    gate = report["gate"]
    require(
        gate["ok"] is True
        and set(gate["conditions"]) == set(CONDITIONS)
        and all(gate["conditions"][k] is True for k in CONDITIONS),
        "nine-condition gate failed",
    )
    require(
        report["parentState"] == report["taskRecordStatus"] == "SUCCESS", "parent not successful"
    )
    require(
        instant(report["window"]["tsStart"]) == instant(item["ts_start"])
        and instant(report["window"]["tsEnd"]) == instant(item["ts_end"]),
        "report window mismatch",
    )
    seconds = report["totalSeconds"]
    require(
        type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
        "invalid elapsed time",
    )
    slo = report["sloPassed"]
    require(type(slo) is bool and slo == (seconds <= 600), "invalid SLO evidence")
    require(rc == 0 or (rc == 1 and slo is False), "benchmark exit is not an approved SLO gap")
    denominator = gate["denominators"]
    require(
        denominator["frozenEligible"] == denominator["ledgerRows"] == 1000
        and denominator["excluded"] == 0,
        "business denominator changed",
    )
    manifest = read(ctx.inputs[f"manifest_{item['batch']:02d}"]["path"])
    expected = dict(Counter(row["expectedStatus"] for row in manifest["rows"]))
    require(denominator["expectedHistogram"] == expected, "expected histogram changed")
    require(all(v["count"] == 0 for v in gate["violations"].values()), "gate violations present")
    freeze = Path(report["freezePath"]).resolve()
    require(freeze.is_relative_to((batch / "freeze").resolve()), "old/foreign freeze reused")
    eligible = read(freeze)["eligible"]
    require(
        len(eligible) == len({r["loopId"] for r in eligible}) == 1000
        and {r["loopId"] for r in eligible} == ctx.eligible,
        "freeze eligible IDs differ",
    )
    return {
        "batch": item,
        "finishedAt": now(),
        "benchmarkRc": rc,
        "completionReason": report["completionReason"],
        "seconds": seconds,
        "sloPassed": slo,
        "gateOk": True,
        "parentTaskId": report["parentTaskId"],
        "reportSha256": digest(report_path),
        "freezeSha256": digest(freeze),
    }


def validate_resources(ctx, batch, recovery=None):
    root = recovery or batch
    path = root / "local-resource-check.json"
    record = read(path)
    require(
        record["status"] == "LOCAL_CHECKS_PASS"
        and record["errors"] == []
        and not record["resourceFailureLines"],
        "local resource validation failed",
    )
    require(Path(record["batch"]).resolve() == batch.resolve(), "resource batch identity mismatch")
    require(
        record["recovery"] == (str(recovery) if recovery else None), "recovery identity mismatch"
    )
    require(len(record["samples"]) >= 2, "resource samples missing")
    peak = record["peakMemoryUpperBoundBytes"]
    require(
        type(peak) is int and 0 <= peak <= ctx.profile["maximumAccountingBytes"],
        "resource peak missing/out of budget",
    )
    if recovery:
        require(
            instant(record["samples"][-1]["at"]) - instant(record["samples"][0]["at"])
            >= timedelta(seconds=300),
            "final recovery below 300s",
        )
    return {"path": str(path), "sha256": digest(path), "status": record["status"]}


def run_command(argv, label, *, cwd, env, timeout=600):
    """Preserve raw rc/output even for timeout/exec failure, then propagate."""
    record = {"argv": list(map(str, argv)), "cwd": str(cwd), "startedAt": now(), "rc": None}
    error = None
    with (
        label.with_suffix(".stdout.log").open("xb") as stdout,
        label.with_suffix(".stderr.log").open("xb") as stderr,
    ):
        try:
            process = subprocess.run(
                record["argv"], cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout
            )
            record["rc"] = process.returncode
        except Exception as exc:
            error = exc
            record.update(exception=type(exc).__name__, message=str(exc))
    record["finishedAt"] = now()
    for stream in ("stdout", "stderr"):
        path = label.with_suffix(f".{stream}.log")
        record[stream] = {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
    write(label.with_suffix(".command.json"), record)
    if error:
        raise ContractError(f"{label.name}: {type(error).__name__}") from error
    require(record["rc"] == 0, f"{label.name}: rc={record['rc']}")
    return record


def validate_private_database(execution, config):
    """Allow only the protocol's dedicated test database, identically for PG/TD."""
    database = execution.get("privateDatabase", "clpm_capacity_20261017_s01")
    match = (
        re.fullmatch(r"clpm_capacity_([0-9]{8})_s([0-9]+)", database)
        if isinstance(database, str) and len(database) <= 63
        else None
    )
    require(match is not None and bool(match.group(2).strip("0")), "invalid private test database")
    try:
        datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError as exc:
        raise ContractError("invalid private test database date") from exc
    require(
        config.get("POSTGRES_DB") == config.get("TDENGINE_DB") == database,
        "database differs from the protocol's isolated test scope",
    )
    return database


class LiveRuntime:
    """Only the isolated test runtime; lifecycle helpers own API/Worker/Beat."""

    fixture_only = False

    def __init__(self, ctx):
        require(ctx.protocol["execution"]["evidenceLevel"] == "REAL_TEST", "offline protocol")
        from celery import Celery
        from dotenv import dotenv_values

        self.ctx = ctx
        self.cfg = dotenv_values(ctx.runtime / "backend/.env")
        cfg = self.cfg
        validate_private_database(ctx.protocol["execution"], cfg)
        for kind, port in (("POSTGRES", "17102"), ("TDENGINE", "17104"), ("REDIS", "17123")):
            require(
                cfg[kind + "_HOST"] in ("127.0.0.1", "localhost") and cfg[kind + "_PORT"] == port,
                f"{kind}: not the private test endpoint",
            )
        require(
            digest(ctx.runtime / "backend/.env") == ctx.protocol["configFreeze"]["envSha256"],
            "runtime environment differs from fresh freeze",
        )
        self.env = os.environ.copy()
        self.env.update({k: v for k, v in cfg.items() if v is not None})
        self.env.update(
            CLPM_TEST_MODE="0",
            CLPM_SKIP_EXIT_HOOKS="1",
            CLPM_BUILD_CODE_SHA=ctx.protocol["candidateSha"],
            CLPM_G5_TRACE="0",
            NO_PROXY="127.0.0.1,localhost,::1",
            PYTHONUNBUFFERED="1",
        )
        self.env.update({k: str(v) for k, v in ctx.profile["numericThreads"].items()})
        auth = (
            ":" + quote(cfg["REDIS_PASSWORD"], safe="") + "@" if cfg.get("REDIS_PASSWORD") else ""
        )
        self.app = Celery(
            "capacity_series_v4",
            broker=f"redis://{auth}127.0.0.1:17123/1",
            backend=f"redis://{auth}127.0.0.1:17123/2",
        )
        spec = importlib.util.spec_from_file_location(
            "bound_capacity_guard", ctx.tools["brokerGuard"]["path"]
        )
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.broker_snapshot = guard.private_broker_snapshot

    def command(self, key, args, label, *, timeout=600):
        ctx = self.ctx
        verify_bindings(ctx)
        return run_command(
            [ctx.python, ctx.tools[key]["path"], *args],
            label,
            cwd=ctx.cwd,
            env=self.env,
            timeout=timeout,
        )

    def quiet(self, path):
        snapshot = self.broker_snapshot()
        write(path, snapshot)
        require(snapshot["quiescent"] is True, "private broker is not quiescent")

    def clear_input_caches(self, path):
        import redis

        client = redis.Redis(
            host="127.0.0.1",
            port=17123,
            db=0,
            decode_responses=True,
            password=self.cfg.get("REDIS_PASSWORD") or None,
            socket_timeout=5,
        )
        removed, leftovers = [], []
        try:
            for prefix in ("pdb", "pdb_l2", "pdb_l3"):
                for key in client.scan_iter(match=prefix + ":*", count=200):
                    if len(key.split(":")) >= 3 and key.split(":")[1] in self.ctx.eligible:
                        value = client.get(key)
                        removed.append(
                            {
                                "key": key,
                                "ttlMs": client.pttl(key),
                                "valueSha256": hashlib.sha256(value.encode()).hexdigest()
                                if value is not None
                                else None,
                            }
                        )
                        client.unlink(key)
            leftovers = [
                k
                for k in client.scan_iter(match="pdb*:*", count=200)
                if len(k.split(":")) > 1 and k.split(":")[1] in self.ctx.eligible
            ]
        finally:
            client.close()
        write(
            path,
            {
                "at": now(),
                "privatePort": 17123,
                "db": 0,
                "scope": "frozen input cache only; task/result history preserved",
                "removed": removed,
                "leftovers": leftovers,
            },
        )
        require(not leftovers, "cold input caches remain")

    def collector_command(self, item, batch, out, post_seconds):
        ctx = self.ctx
        return [
            str(ctx.python),
            ctx.tools["collector"]["path"],
            "--env-file",
            str(ctx.runtime / "backend/.env"),
            "--ts-start",
            item["ts_start"],
            "--report",
            str(batch / "product-report.json"),
            "--out",
            str(out),
            "--worker-log",
            str(ctx.runtime / "backend/logs/celery-worker.log"),
            "--interval",
            "2",
            "--post-seconds",
            str(post_seconds),
            "--max-seconds",
            "3100" if post_seconds == 30 else "400",
            "--containers",
            *ctx.profile["containers"],
            "--td-container",
            "clpm-mvp-tdengine",
        ]

    def collect_batch(self, item, batch, remaining):
        ctx = self.ctx
        prefix = batch.parent.name + "-" + batch.name
        self.quiet(batch / "broker-before-preparation.json")
        if item["cache"] == "cold":
            self.command("lifecycle", ["stop", prefix + "-stop"], batch / "stop")
            self.clear_input_caches(batch / "cache-clear.json")
            self.command("lifecycle", ["restart-td", prefix + "-td"], batch / "restart-td")
            self.command("wrapper", [prefix + "-start"], batch / "start")
        state = read(ctx.evidence / "runtime/current-capacity-api.json")
        require(state["sha"] == ctx.protocol["candidateSha"], "API state SHA mismatch")
        write(
            batch / "runtime-start-ref.json",
            {"state": state, "recordSha256": digest(state["start_record"])},
        )
        ping = self.app.control.inspect(timeout=3).ping()
        require(bool(ping) and len(ping) == 1, "expected one Worker registration")
        write(
            batch / "enable-events.json",
            {
                "at": now(),
                "destination": list(ping),
                "response": self.app.control.enable_events(
                    destination=list(ping), reply=True, timeout=3
                ),
            },
        )
        stop = batch / "observer-stop"
        receiver_argv = [
            str(ctx.python),
            ctx.tools["receiver"]["path"],
            "--env-file",
            str(ctx.runtime / "backend/.env"),
            "--out",
            str(batch / "events"),
            "--stop-file",
            str(stop),
            "--max-seconds",
            "3300",
        ]
        children, handles = {}, []
        try:
            for name, argv in (
                ("receiver", receiver_argv),
                ("collector", self.collector_command(item, batch, batch / "raw", 30)),
            ):
                handle = (batch / (name + ".log")).open("xb")
                handles.append(handle)
                child = subprocess.Popen(
                    argv,
                    cwd=ctx.evidence / "f02",
                    env=self.env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
                children[name] = child
                write(
                    batch / (name + "-command.json"),
                    {"argv": argv, "pid": child.pid, "startedAt": now()},
                )
            start = time.monotonic()
            while time.monotonic() - start < 65:
                require(all(c.poll() is None for c in children.values()), "observer exited early")
                time.sleep(1)
            require((batch / "raw/bootstrap.json").is_file(), "resource bootstrap missing")
            if item["cache"] == "hot":
                submitted = now()
                warm = self.app.send_task(
                    "app.tasks.kpi_calc.prewarm_cache",
                    kwargs={"ts_start": item["ts_start"]},
                    queue="default",
                    routing_key="default",
                )
                result = warm.get(timeout=1200, propagate=False)
                write(
                    batch / "prewarm-task.json",
                    {
                        "submittedAt": submitted,
                        "finishedAt": now(),
                        "taskId": warm.id,
                        "state": warm.state,
                        "result": result,
                    },
                )
                require(
                    warm.state == "SUCCESS"
                    and result["total"] == result["succeeded"] == 1000
                    and result["failed"] == 0,
                    "prewarm denominator failed",
                )
            self.command(
                "cacheCapture",
                [
                    "--env-file",
                    ctx.runtime / "backend/.env",
                    "--ts-start",
                    item["ts_start"],
                    "--out",
                    batch / "cache-before",
                ],
                batch / "cache-before-capture",
            )
            self.command(
                "runtimeProfile",
                [batch / "runtime-profile", item["ts_start"]],
                batch / "runtime-profile-capture",
            )
            self.quiet(batch / "broker-before-formal.json")
            verify_bindings(ctx)
            argv = [
                str(ctx.python),
                "-u",
                ctx.tools["benchmark"]["path"],
                "--mode",
                "standard-hourly",
                "--ts-start",
                item["ts_start"],
                "--manifest",
                ctx.inputs[f"manifest_{item['batch']:02d}"]["path"],
                "--expect-eligible",
                "1000",
                "--barrier-loop-prefix",
                "PERF-T",
                "--slo-seconds",
                "600",
                "--timeout",
                "2400",
                "--poll-interval",
                "2",
                "--freeze-dir",
                str(batch / "freeze"),
                "--report-out",
                str(batch / "product-report.json"),
                "--plan-windows",
                ",".join(x["ts_start"] for x in remaining),
            ]
            rc = self.benchmark(argv, batch)
            validate_product(ctx, item, batch, rc)
            collector_rc = children["collector"].wait(timeout=720)
            stop.write_text(now() + "\n")
            receiver_rc = children["receiver"].wait(timeout=15)
            write(
                batch / "observer-exits.json",
                {"collectorRc": collector_rc, "receiverRc": receiver_rc},
            )
            require(collector_rc == receiver_rc == 0, "observer nonzero exit")
            return {"benchmarkRc": rc, "collectorRc": collector_rc, "receiverRc": receiver_rc}
        finally:
            cleanup = []
            cleanup_errors = []
            for name, child in children.items():
                try:
                    if child.poll() is None:
                        child.terminate()  # Observer only; never Worker/Beat/business task.
                        try:
                            child.wait(timeout=20)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=10)
                        cleanup.append({"role": name, "pid": child.pid, "terminated": True})
                except Exception as exc:
                    cleanup_errors.append({"role": name, "message": str(exc)})
            for handle in handles:
                handle.close()
            write(
                batch / "observer-cleanup.json",
                {"at": now(), "actions": cleanup, "errors": cleanup_errors},
            )
            require(not cleanup_errors, "owned observer cleanup failed")

    def benchmark(self, argv, batch):
        """Bound the client even if it stops producing output."""
        record = {"argv": argv, "cwd": str(self.ctx.cwd), "startedAt": now(), "rc": None}
        with (
            (batch / "benchmark.stdout.log").open("xb") as stdout,
            (batch / "benchmark.stderr.log").open("xb") as stderr,
            (batch / "benchmark-timestamped.jsonl").open("x") as timed,
        ):
            child = subprocess.Popen(
                argv, cwd=self.ctx.cwd, env=self.env, stdout=subprocess.PIPE, stderr=stderr
            )
            pump_errors = []

            def pump():
                try:
                    for line in iter(child.stdout.readline, b""):
                        stdout.write(line)
                        stdout.flush()
                        timed.write(
                            json.dumps(
                                {"observedAt": now(), "line": line.decode("utf-8", "replace")}
                            )
                            + "\n"
                        )
                        timed.flush()
                except Exception as exc:
                    pump_errors.append(str(exc))

            thread = threading.Thread(target=pump, daemon=True)
            thread.start()
            try:
                record["rc"] = child.wait(timeout=2600)
            except subprocess.TimeoutExpired as exc:
                record["exception"] = type(exc).__name__
                child.terminate()  # Benchmark client only; actual Celery task remains preserved.
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            finally:
                thread.join(timeout=5)
                if not thread.is_alive():
                    child.stdout.close()
                record.update(
                    finishedAt=now(),
                    stdoutSha256=digest(batch / "benchmark.stdout.log"),
                    stderrSha256=digest(batch / "benchmark.stderr.log"),
                )
                write(batch / "benchmark-command.json", record)
            require(not thread.is_alive() and not pump_errors, "benchmark output capture failed")
        require("exception" not in record, "benchmark client timed out")
        return record["rc"]

    def validate_batch(self, batch):
        self.command(
            "validator",
            ["--batch", batch, "--profile", self.ctx.inputs["profile"]["path"]],
            batch / "local-evidence-validation",
        )

    def recover(self, item, batch, out):
        verify_bindings(self.ctx)
        root = out / "final-300s-recovery"
        run_command(
            self.collector_command(item, batch, root, 300),
            out / "final-recovery",
            cwd=self.ctx.cwd,
            env=self.env,
            timeout=450,
        )
        self.command(
            "validator",
            ["--batch", batch, "--profile", self.ctx.inputs["profile"]["path"], "--recovery", root],
            out / "local-recovery-validation",
        )
        return root

    def close(self):
        self.app.close()


def run_series(ctx, runtime, output):
    require(
        bool(runtime.fixture_only)
        == (ctx.protocol["execution"]["evidenceLevel"] == "OFFLINE_FIXTURE"),
        "fixture/real evidence level mismatch",
    )
    require(output.parent.resolve() == ctx.output_parent, "series output outside bound parent")
    output.mkdir(parents=True, exist_ok=False)
    outcomes, attempted, active = [], [], None
    write(
        output / "series-start.json",
        {
            "startedAt": now(),
            "candidateSha": ctx.protocol["candidateSha"],
            "protocolSha256": digest(ctx.protocol_path),
            "requiredBatches": 20,
            "evidenceLevel": ctx.protocol["execution"]["evidenceLevel"],
            "noReuse": True,
            "independentAcceptance": "PENDING",
        },
    )
    try:
        for index, item in enumerate(ctx.plan):
            verify_bindings(ctx)
            runtime_identity(ctx)
            active = output / f"{item['batch']:02d}-{item['scenario']}-{item['cache']}"
            active.mkdir()
            write(active / "batch-start.json", {"startedAt": now(), "batch": item["batch"]})
            attempted.append(item["batch"])
            append(
                output / "journal.jsonl", {"at": now(), "batch": item["batch"], "state": "STARTED"}
            )
            exits = runtime.collect_batch(item, active, ctx.plan[index:])
            require(exits["collectorRc"] == exits["receiverRc"] == 0, "observer nonzero exit")
            result = validate_product(ctx, item, active, exits["benchmarkRc"])
            require(
                result["parentTaskId"] not in {o["parentTaskId"] for o in outcomes},
                "parent task reused across batches",
            )
            runtime.validate_batch(active)  # Any failure propagates to the outer series boundary.
            result["resourceEvidence"] = validate_resources(ctx, active)
            result["collectorRc"], result["receiverRc"] = exits["collectorRc"], exits["receiverRc"]
            outcomes.append(result)
            append(
                output / "journal.jsonl", {"at": now(), "batch": item["batch"], "state": "VERIFIED"}
            )
            write(output / "outcomes.json", outcomes, replace=True)
            if ctx.protocol["execution"].get("supervision", {}).get("requireSloPass") is True:
                require(
                    result["sloPassed"] is True and result["seconds"] <= ctx.protocol["sloSeconds"],
                    "strict supervised 600s SLO failed; batch evidence preserved",
                )
        require(
            len(outcomes) == len({o["batch"]["batch"] for o in outcomes}) == 20,
            "series lacks 20 unique verified batches",
        )
        recovery = runtime.recover(ctx.plan[-1], active, output)
        recovery_evidence = validate_resources(ctx, active, recovery)
        verify_bindings(ctx)
        runtime.close()
        summary = {
            "finishedAt": now(),
            "required": 20,
            "executed": len(attempted),
            "businessReportsPassed": len(outcomes),
            "gateOk": sum(o["gateOk"] is True for o in outcomes),
            "sloPassed": sum(o["sloPassed"] is True for o in outcomes),
            "localResourceChecks": "VALIDATION_PASSED",
            "resourceReportsVerified": len(outcomes),
            "recoveryEvidence": recovery_evidence,
            "status": "COMPLETE_AWAITING_INDEPENDENT_ACCEPTANCE",
            "evidenceLevel": ctx.protocol["execution"]["evidenceLevel"],
            "independentAcceptance": "PENDING",
        }
        write(output / "complete.json", summary)
        return summary
    except BaseException as exc:
        failure = {
            "at": now(),
            "status": "SERIES_FAILED_NOT_REUSED",
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "attemptedBatchIds": attempted,
            "verifiedBatchIds": [o["batch"]["batch"] for o in outcomes],
            "businessReportsPassed": len(outcomes),
            "independentAcceptance": "PENDING",
        }
        write(output / "series-failure.json", failure)
        if active:
            write(active / "orchestration-failure.json", failure)
        try:
            runtime.close()
        except Exception as cleanup:
            write(
                output / "runtime-cleanup-failure.json",
                {"type": type(cleanup).__name__, "message": str(cleanup)},
            )
        raise


def main(argv=None, *, runtime_factory=LiveRuntime):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--evidence-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        ctx = load_contract(args.protocol, args.evidence_root)
        if args.check_only:
            print(
                json.dumps(
                    {
                        "status": "INPUT_BINDINGS_VALIDATED",
                        "formalReady": ctx.protocol["formalReady"],
                        "readinessPending": ctx.protocol["readinessPending"],
                        "tools": len(ctx.tools),
                        "batches": len(ctx.plan),
                        "capacityAcceptance": "NOT_EVALUATED",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        require_ready(ctx)
        output = (
            Path(args.output_dir).resolve()
            if args.output_dir
            else ctx.output_parent
            / ("formal-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8])
        )
        require(
            output.parent == ctx.output_parent and not output.exists(),
            "output exists or is unbound",
        )
        runtime = runtime_factory(ctx)
    except (
        ContractError,
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            json.dumps(
                {"status": "PREFLIGHT_REJECTED", "type": type(exc).__name__, "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    try:
        print(json.dumps(run_series(ctx, runtime, output), ensure_ascii=False))
        return 0
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "SERIES_FAILED_NOT_REUSED",
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
