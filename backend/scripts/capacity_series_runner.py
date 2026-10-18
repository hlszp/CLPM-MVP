"""Auditable F02 series orchestration (RR-02 v4).

Import/check-only is read-only. Formal execution requires a frozen protocol,
fresh configuration and applicable CI. Offline adapters are explicitly marked;
they can never produce real capacity acceptance. Historical series are not read
or resumed. All operational helpers remain separately hash-bound.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

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
