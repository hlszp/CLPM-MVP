"""RR-02 control-contract regressions; no database, broker or service lifecycle."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/capacity_series_runner.py"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("capacity_runner_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "runtime_identity", lambda unused: None)
    return module


@pytest.fixture
def contract(tmp_path, mod):
    root = tmp_path / "inputs"
    runtime = tmp_path / "runtime"
    (runtime / "backend/scripts").mkdir(parents=True)
    tools = {}
    for name, relative in mod.HELPERS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# OFFLINE fixture helper; never executed\n")
        tools[name] = {"path": str(path)}
    tools["runner"] = {"path": str(SCRIPT)}
    benchmark = runtime / "backend/scripts/kpi_capacity_benchmark.py"
    benchmark.write_text("# OFFLINE benchmark fixture; never executed\n")
    tools["benchmark"] = {"path": str(benchmark)}
    methods = {}
    for key in (
        "methodErratum01",
        "supplement02",
        "supplement03",
        "supplement04",
        "supplement05",
        "supplement06",
        "historyInputRefs",
        "fourOracles",
    ):
        path = root / (key + ".md")
        path.write_text("OFFLINE method fixture\n")
        methods[key] = {"path": str(path)}
    inputs = {name: {"path": str(root / relative)} for name, relative in mod.INPUTS.items()}
    plan = []
    for i in range(1, 21):
        scenario = "a" if i <= 10 else "b"
        start = datetime(2030, 1, 1, tzinfo=UTC) + timedelta(hours=i)
        item = {
            "batch": i,
            "scenario": scenario,
            "cache": "cold" if (i - 1) % 10 < 5 else "hot",
            "ts_start": start.isoformat(),
            "ts_end": (start + timedelta(hours=1)).isoformat(),
            "manifest_name": f"manifest-{i}.json",
            "required": True,
            "slo_seconds": 600,
            "expected_loops": 1000,
        }
        plan.append(item)
        path = root / "f02/series-s01/input-manifests" / item["manifest_name"]
        rows = [
            {
                "loopTag": f"PERF-T{n:04d}",
                "expectedStatus": (
                    "INCONCLUSIVE(窗口覆盖不足——缺口段按 held/coverage 语义)"
                    if scenario == "b" and n > 950
                    else "INCONCLUSIVE(DATA_INSUFFICIENT)"
                    if scenario == "b" and n > 850
                    else "EVALUATED(ELIGIBLE内终态; MODE=MANUAL, auto表现照常评估)"
                    if scenario == "b" and n > 800
                    else "EVALUATED"
                ),
            }
            for n in range(1, 1001)
        ]
        dump(path, {"synthetic": True, "rows": rows})
        inputs[f"manifest_{i:02d}"] = {"path": str(path)}
        audit_path = root / f"audits/{i:02d}/input-audit.json"
        dump(
            audit_path,
            {"fixtureOnly": True, "status": "PASS", "globalEligible": 1000, "failures": []},
        )
        inputs[f"audit_{i:02d}"] = {"path": str(audit_path)}
    dump(Path(inputs["plan"]["path"]), {"batches": plan})
    dump(Path(inputs["generated"]["path"]), {"prepared": 20})
    dump(Path(inputs["extras"]["path"]), {"preparedAndAudited": 4})
    dump(
        Path(inputs["audited"]["path"]),
        {
            "passed": 20,
            "required": 20,
            "results": [{"batch": i, "status": "PASS", "rc": 0} for i in range(1, 21)],
        },
    )
    profile = {
        "memoryBudgetBytes": 6 * 1024**3,
        "maximumAccountingBytes": int(6 * 1024**3 * 0.8),
        "minimumVmMemAvailableBytes": 1024**3,
        "containers": ["clpm-mvp-postgres", "clpm-mvp-tdengine", "clpm-f02-redis-20261017-s01"],
        "numericThreads": {"OPENBLAS_NUM_THREADS": 1},
        "sampling": {
            "targetSeconds": 2,
            "maximumGapSeconds": 5,
            "preSubmissionMinimumSeconds": 60,
            "postTerminalMinimumSeconds": 30,
            "finalSeriesRecoveryMinimumSeconds": 300,
        },
    }
    dump(Path(inputs["profile"]["path"]), profile)
    review, ci = root / "review.json", root / "ci.json"
    dump(review, {"fixtureOnly": True, "status": "PASS"})
    dump(
        ci,
        {"fixtureOnly": True, "status": "completed", "head_sha": "a" * 40, "conclusion": "success"},
    )
    protocol = {
        "requiredBatches": 20,
        "sloSeconds": 600,
        "memoryBudgetBytes": 6 * 1024**3,
        "candidateSha": "a" * 40,
        "runtimeDir": str(runtime),
        "eligibleIds": [str(UUID(int=i)) for i in range(1, 1001)],
        "formalReady": True,
        "readinessPending": [],
        "configFreeze": {"status": "FROZEN"},
        "protocolReview": {"status": "PASS", "path": str(review), "sha256": mod.digest(review)},
        "applicableCi": {
            "status": "PASS",
            "path": str(ci),
            "sha256": mod.digest(ci),
            "headSha": "a" * 40,
        },
        "tools": tools,
        "methodBindings": methods,
        "scopeLimits": {},
        "execution": {
            "evidenceRoot": str(root),
            "cwd": str(runtime / "backend"),
            "python": sys.executable,
            "outputParent": str(root / "new-series"),
            "evidenceLevel": "OFFLINE_FIXTURE",
            "requiredEvidenceSteps": list(mod.REQUIRED_STEPS),
            "inputs": inputs,
        },
    }
    path = tmp_path / "protocol.json"

    def seal():
        generated = Path(inputs["generated"]["path"])
        data = json.loads(generated.read_text())
        data["plan_sha256"] = mod.digest(inputs["plan"]["path"])
        dump(generated, data)
        audit = Path(inputs["audited"]["path"])
        data = json.loads(audit.read_text())
        for result in data["results"]:
            result["manifestSha256"] = mod.digest(inputs[f"manifest_{result['batch']:02d}"]["path"])
            audit_path = Path(inputs[f"audit_{result['batch']:02d}"]["path"])
            report = json.loads(audit_path.read_text())
            report["manifestSha256"] = result["manifestSha256"]
            dump(audit_path, report)
            result["reportSha256"] = mod.digest(audit_path)
        dump(audit, data)
        for table in (tools, inputs, methods):
            for value in table.values():
                value["sha256"] = mod.digest(value["path"])
        dump(
            review,
            {
                "fixtureOnly": True,
                "status": "PASS",
                "nonAuthor": True,
                "runnerSha256": tools["runner"]["sha256"],
                "bindingsSha256": mod.bindings_digest(protocol),
            },
        )
        protocol["protocolReview"]["sha256"] = mod.digest(review)
        dump(path, protocol)

    seal()
    return SimpleNamespace(
        protocol=protocol,
        path=path,
        root=root,
        runtime=runtime,
        plan=plan,
        inputs=inputs,
        tools=tools,
        seal=seal,
        output=root / "new-series/formal-new",
    )


class OfflineRuntime:
    fixture_only = True

    def __init__(self, mod, fault=None):
        self.mod, self.fault = mod, fault
        self.calls, self.closed = [], 0

    def resource(self, batch, recovery=None):
        start = datetime.now(UTC)
        seconds = 299 if self.fault == "short_recovery" else 301
        return {
            "fixtureOnly": True,
            "status": "LOCAL_CHECKS_PASS",
            "errors": [],
            "resourceFailureLines": [],
            "batch": str(batch),
            "recovery": str(recovery) if recovery else None,
            "peakMemoryUpperBoundBytes": 1000,
            "samples": [
                {"at": start.isoformat()},
                {"at": (start + timedelta(seconds=seconds)).isoformat()},
            ],
        }

    def collect_batch(self, item, batch, remaining):
        self.calls.append(item["batch"])
        m, ctx = self.mod, self.ctx
        if self.fault == "executor_exception":
            raise FileNotFoundError("CONTROLLED")
        if self.fault == "timeout":
            raise subprocess.TimeoutExpired(["offline-executor"], 0.01)
        freeze = batch / "freeze/freeze.json"
        dump(freeze, {"eligible": [{"loopId": value} for value in sorted(ctx.eligible)]})
        manifest = json.loads(Path(ctx.inputs[f"manifest_{item['batch']:02d}"]["path"]).read_text())
        histogram = dict(m.Counter(row["expectedStatus"] for row in manifest["rows"]))
        report = {
            "fixtureOnly": True,
            "observedAt": m.now(),
            "completionReason": "BARRIER_PASSED",
            "parentTaskId": str(UUID(int=10000 + item["batch"])),
            "parentState": "SUCCESS",
            "taskRecordStatus": "SUCCESS",
            "window": {"tsStart": item["ts_start"], "tsEnd": item["ts_end"]},
            "totalSeconds": 715.5,
            "sloPassed": False,
            "freezePath": str(freeze),
            "gate": {
                "ok": True,
                "conditions": dict.fromkeys(m.CONDITIONS, True),
                "denominators": {
                    "frozenEligible": 1000,
                    "ledgerRows": 1000,
                    "excluded": 0,
                    "expectedHistogram": histogram,
                },
                "violations": {"missing": {"count": 0}},
            },
        }
        rc = 1  # The specifically approved SLO-only exception.
        if self.fault == "barrier":
            report["completionReason"] = "TERMINAL_MISMATCH"
        elif self.fault == "gate":
            report["gate"]["ok"] = False
        elif self.fault == "missing_gate":
            del report["gate"]
        elif self.fault == "condition":
            report["gate"]["conditions"]["nodes"] = False
        elif self.fault == "denominator":
            report["gate"]["denominators"]["frozenEligible"] = 999
        elif self.fault == "histogram":
            report["gate"]["denominators"]["expectedHistogram"] = {"EVALUATED": 999}
        elif self.fault == "window":
            report["window"]["tsStart"] = "2000-01-01T00:00:00"
        elif self.fault == "stale_report":
            report["observedAt"] = "2000-01-01T00:00:00"
        elif self.fault == "future_report":
            report["observedAt"] = "2099-01-01T00:00:00"
        elif self.fault == "foreign_freeze":
            report["freezePath"] = str(batch.parent.parent / "OLD-freeze.json")
        elif self.fault == "wrong_eligible":
            dump(freeze, {"eligible": [{"loopId": str(UUID(int=9999))}]})
        elif self.fault == "reused_parent":
            report["parentTaskId"] = str(UUID(int=777))
        elif self.fault == "bad_benchmark_rc":
            rc = 7
        elif self.fault == "bad_slo_rc":
            report.update(totalSeconds=20, sloPassed=True)
        if self.fault == "missing_report" or (
            self.fault == "missing_report_15" and item["batch"] == 15
        ):
            return {"benchmarkRc": rc, "collectorRc": 0, "receiverRc": 0}
        path = batch / "product-report.json"
        if self.fault == "malformed_report":
            path.write_text("{INVALID")
        else:
            dump(path, report)
        return {
            "benchmarkRc": rc,
            "collectorRc": 2 if self.fault == "collector" else 0,
            "receiverRc": 3 if self.fault == "receiver" else 0,
        }

    def validate_batch(self, batch):
        if self.fault in ("validator_rc1", "validator_rc7") or (
            self.fault == "validator_15" and self.calls[-1] == 15
        ):
            rc = 7 if self.fault == "validator_rc7" else 1
            self.mod.run_command(
                [sys.executable, "-c", f"import sys; print('CONTROLLED'); sys.exit({rc})"],
                batch / "validator",
                cwd=batch,
                env=os.environ.copy(),
                timeout=10,
            )
        if self.fault != "missing_resources":
            record = self.resource(batch)
            if self.fault == "resource_failure":
                record.update(status="REWORK", errors=["CONTROLLED"])
            dump(batch / "local-resource-check.json", record)

    def recover(self, item, batch, out):
        if self.fault == "recovery_failure":
            raise self.mod.ContractError("CONTROLLED recovery failure")
        root = out / "final-300s-recovery"
        dump(root / "local-resource-check.json", self.resource(batch, root))
        return root

    def close(self):
        self.closed += 1
        if self.fault == "close_failure":
            raise self.mod.ContractError("CONTROLLED cleanup failure")


def execute(mod, contract, fault=None):
    runtime = OfflineRuntime(mod, fault)

    def factory(ctx):
        runtime.ctx = ctx
        return runtime

    rc = mod.main(
        ["--protocol", str(contract.path), "--output-dir", str(contract.output)],
        runtime_factory=factory,
    )
    return rc, runtime


@pytest.mark.parametrize(
    "fault",
    [
        "validator_rc1",
        "validator_rc7",
        "executor_exception",
        "timeout",
        "missing_report",
        "malformed_report",
        "barrier",
        "gate",
        "missing_gate",
        "condition",
        "denominator",
        "histogram",
        "window",
        "stale_report",
        "future_report",
        "foreign_freeze",
        "wrong_eligible",
        "bad_benchmark_rc",
        "bad_slo_rc",
        "collector",
        "receiver",
        "missing_resources",
        "resource_failure",
    ],
)
def test_bad_first_batch_stops_whole_series(mod, contract, fault):
    rc, runtime = execute(mod, contract, fault)
    assert rc == 1
    assert runtime.calls == [1]
    assert runtime.closed == 1
    assert not (contract.output / "complete.json").exists()
    failed = json.loads((contract.output / "series-failure.json").read_text())
    assert failed["status"] == "SERIES_FAILED_NOT_REUSED"
    assert failed["businessReportsPassed"] == 0
    if fault == "validator_rc7":
        assert (
            json.loads(next(contract.output.glob("*/validator.command.json")).read_text())["rc"]
            == 7
        )


def test_twenty_distinct_verified_batches_have_truthful_counts(mod, contract):
    rc, runtime = execute(mod, contract)
    assert rc == 0 and runtime.calls == list(range(1, 21))
    result = json.loads((contract.output / "complete.json").read_text())
    assert (
        result["executed"]
        == result["businessReportsPassed"]
        == result["resourceReportsVerified"]
        == 20
    )
    assert result["sloPassed"] == 0
    assert result["independentAcceptance"] == "PENDING"
    assert result["evidenceLevel"] == "OFFLINE_FIXTURE"
    assert result["status"] == "COMPLETE_AWAITING_INDEPENDENT_ACCEPTANCE"
    assert runtime.closed == 1


@pytest.mark.parametrize("fault", ["validator_15", "missing_report_15"])
def test_fourteen_passes_cannot_be_reported_as_twenty(mod, contract, fault):
    rc, runtime = execute(mod, contract, fault)
    assert rc == 1 and runtime.calls == list(range(1, 16))
    assert len(json.loads((contract.output / "outcomes.json").read_text())) == 14
    assert (
        json.loads((contract.output / "series-failure.json").read_text())["businessReportsPassed"]
        == 14
    )
    assert not (contract.output / "complete.json").exists()


@pytest.mark.parametrize("fault", ["recovery_failure", "short_recovery", "close_failure"])
def test_series_needs_final_recovery_and_cleanup(mod, contract, fault):
    rc, runtime = execute(mod, contract, fault)
    assert rc == 1 and len(runtime.calls) == 20
    assert not (contract.output / "complete.json").exists()
    assert (contract.output / "series-failure.json").exists()


def test_parent_identity_cannot_be_reused(mod, contract):
    rc, runtime = execute(mod, contract, "reused_parent")
    assert rc == 1 and runtime.calls == [1, 2]
    assert (
        json.loads((contract.output / "series-failure.json").read_text())["businessReportsPassed"]
        == 1
    )


@pytest.mark.parametrize("existing", ["six-outcomes", "empty", "failed"])
def test_existing_series_rejected_before_factory(mod, contract, existing):
    contract.output.mkdir(parents=True)
    dump(contract.output / "old.json", {"history": existing, "outcomes": list(range(6))})
    before = (contract.output / "old.json").read_bytes()

    def forbidden(unused):
        raise AssertionError("factory called before output rejection")

    assert (
        mod.main(
            ["--protocol", str(contract.path), "--output-dir", str(contract.output)],
            runtime_factory=forbidden,
        )
        == 2
    )
    assert (contract.output / "old.json").read_bytes() == before


def test_old_six_are_neither_loaded_nor_modified(mod, contract):
    old = contract.root / "f02/series-s01/formal/outcomes.json"
    dump(old, [{"batch": i} for i in range(1, 7)])
    before = old.read_bytes()
    rc, runtime = execute(mod, contract)
    assert rc == 0 and runtime.calls == list(range(1, 21))
    assert old.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "runner_hash",
        "helper_hash",
        "method_hash",
        "missing_helper",
        "entrypoint",
        "root",
        "cwd",
        "python",
        "duplicate_ids",
        "duplicate_batches",
        "duplicate_windows",
        "denominator",
        "histogram",
        "resource_budget",
        "sampling",
        "trimmed_steps",
    ],
)
def test_bad_binding_or_plan_rejected_before_side_effects(mod, contract, mutation):
    p = contract.protocol
    if mutation in ("runner_hash", "helper_hash"):
        p["tools"]["runner" if mutation == "runner_hash" else "validator"]["sha256"] = "0" * 64
    elif mutation == "method_hash":
        p["methodBindings"]["fourOracles"]["sha256"] = "0" * 64
    elif mutation == "missing_helper":
        Path(p["tools"]["inputAudit"]["path"]).unlink()
    elif mutation == "entrypoint":
        p["tools"]["runner"]["path"] = str(contract.root / "WRONG.py")
    elif mutation in ("root", "cwd", "python"):
        key = {"root": "evidenceRoot", "cwd": "cwd", "python": "python"}[mutation]
        p["execution"][key] = str(contract.root / "WRONG")
    elif mutation == "duplicate_ids":
        p["eligibleIds"] = [p["eligibleIds"][0]] * 1000
    elif mutation in ("duplicate_batches", "duplicate_windows", "denominator"):
        plan = contract.plan
        if mutation == "duplicate_batches":
            plan[1]["batch"] = plan[0]["batch"]
        elif mutation == "duplicate_windows":
            plan[1].update(ts_start=plan[0]["ts_start"], ts_end=plan[0]["ts_end"])
        else:
            plan[0]["expected_loops"] = 999
        dump(Path(contract.inputs["plan"]["path"]), {"batches": plan})
        contract.seal()
    elif mutation == "histogram":
        path = Path(contract.inputs["manifest_01"]["path"])
        rows = json.loads(path.read_text())
        rows["rows"][0]["expectedStatus"] = "INCONCLUSIVE"
        dump(path, rows)
        contract.seal()
    elif mutation in ("resource_budget", "sampling"):
        path = Path(contract.inputs["profile"]["path"])
        profile = json.loads(path.read_text())
        if mutation == "resource_budget":
            profile["maximumAccountingBytes"] = 100 * 1024**3
        else:
            profile["sampling"]["finalSeriesRecoveryMinimumSeconds"] = 0
        dump(path, profile)
        contract.seal()
    elif mutation == "trimmed_steps":
        p["scopeLimits"]["trimmedSteps"] = {"recovery": "optional"}
    dump(contract.path, p)

    def forbidden(unused):
        raise AssertionError("factory reached for invalid contract")

    assert mod.main(["--protocol", str(contract.path)], runtime_factory=forbidden) == 2
    assert not contract.output.parent.exists()


@pytest.mark.parametrize("mutation", ["not_ready", "pending", "freeze", "review", "ci", "ci_sha"])
def test_admission_cannot_be_faked_by_formal_ready_alone(mod, contract, mutation):
    p = contract.protocol
    if mutation == "not_ready":
        p["formalReady"] = False
    elif mutation == "pending":
        p["readinessPending"] = ["fresh freeze"]
    elif mutation == "freeze":
        p["configFreeze"]["status"] = "PENDING"
    elif mutation in ("review", "ci"):
        p["protocolReview" if mutation == "review" else "applicableCi"]["status"] = "PENDING"
    else:
        p["applicableCi"]["headSha"] = "b" * 40
    dump(contract.path, p)
    rc, runtime = execute(mod, contract)
    assert rc == 2 and runtime.calls == []
    assert not contract.output.parent.exists()


def test_bound_input_drift_stops_before_next_batch(mod, contract):
    runtime = OfflineRuntime(mod)
    original = runtime.validate_batch

    def mutate(batch):
        original(batch)
        Path(contract.tools["validator"]["path"]).write_text("# changed after first batch")

    runtime.validate_batch = mutate

    def factory(ctx):
        runtime.ctx = ctx
        return runtime

    assert (
        mod.main(
            ["--protocol", str(contract.path), "--output-dir", str(contract.output)],
            runtime_factory=factory,
        )
        == 1
    )
    assert runtime.calls == [1]
    assert not (contract.output / "complete.json").exists()


@pytest.mark.parametrize("rc", [1, 7])
def test_command_records_real_returncode(mod, tmp_path, rc):
    label = tmp_path / "command"
    with pytest.raises(mod.ContractError):
        mod.run_command(
            [sys.executable, "-c", f"raise SystemExit({rc})"],
            label,
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=5,
        )
    assert json.loads(label.with_suffix(".command.json").read_text())["rc"] == rc


def test_command_timeout_has_raw_evidence(mod, tmp_path):
    label = tmp_path / "timeout"
    with pytest.raises(mod.ContractError):
        mod.run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            label,
            cwd=tmp_path,
            env=os.environ.copy(),
            timeout=0.05,
        )
    assert (
        json.loads(label.with_suffix(".command.json").read_text())["exception"] == "TimeoutExpired"
    )
    assert label.with_suffix(".stdout.log").exists()


@pytest.mark.parametrize("other_cwd", [False, True])
def test_real_cli_check_and_admission_are_read_only(contract, tmp_path, other_cwd):
    # A read-only git shim avoids any dependency on a local/CI runtime checkout.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(
        '#!/bin/sh\nif [ "$1" = rev-parse ]; then\n'
        f"  echo {'a' * 40}\n"
        'elif [ "$1" = diff ] || [ "$1" = ls-files ]; then\n  exit 0\n'
        "else\n  exit 9\nfi\n"
    )
    git.chmod(0o755)
    contract.protocol.update(formalReady=False, readinessPending=["fresh freeze"])
    dump(contract.path, contract.protocol)
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    cwd = tmp_path if other_cwd else contract.runtime / "backend"
    argv = [sys.executable, "-B", str(SCRIPT), "--protocol", str(contract.path)]
    checked = subprocess.run(
        [*argv, "--check-only"], cwd=cwd, env=env, capture_output=True, text=True, timeout=20
    )
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["capacityAcceptance"] == "NOT_EVALUATED"
    rejected = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=20)
    assert rejected.returncode == 2
    assert not contract.output.parent.exists()


@pytest.mark.parametrize(
    "kind",
    [
        "ci_raw_head",
        "review_runner",
        "review_bindings",
        "input_audit_status",
        "missing_step",
    ],
)
def test_receipt_content_and_required_steps_are_enforced(mod, contract, kind):
    p = contract.protocol
    if kind == "ci_raw_head":
        record = p["applicableCi"]
        raw = json.loads(Path(record["path"]).read_text())
        raw["head_sha"] = "b" * 40
        dump(Path(record["path"]), raw)
        record["sha256"] = mod.digest(record["path"])
    elif kind.startswith("review"):
        record = p["protocolReview"]
        raw = json.loads(Path(record["path"]).read_text())
        raw["runnerSha256" if kind == "review_runner" else "bindingsSha256"] = "0" * 64
        dump(Path(record["path"]), raw)
        record["sha256"] = mod.digest(record["path"])
    elif kind == "input_audit_status":
        path = Path(contract.inputs["audit_01"]["path"])
        raw = json.loads(path.read_text())
        raw["status"] = "REWORK"
        dump(path, raw)
        contract.seal()
    else:
        p["execution"]["requiredEvidenceSteps"].remove("FINAL_300S_RECOVERY")
    dump(contract.path, p)
    rc, runtime = execute(mod, contract)
    assert rc == 2 and runtime.calls == []
    assert not contract.output.parent.exists()


def test_protocol_byte_drift_is_rejected_even_when_json_is_equal(mod, contract):
    runtime = OfflineRuntime(mod)
    original = runtime.validate_batch

    def mutate(batch):
        original(batch)
        contract.path.write_text(contract.path.read_text() + " ")

    runtime.validate_batch = mutate

    def factory(ctx):
        runtime.ctx = ctx
        return runtime

    assert (
        mod.main(
            ["--protocol", str(contract.path), "--output-dir", str(contract.output)],
            runtime_factory=factory,
        )
        == 1
    )
    assert runtime.calls == [1]
    assert not (contract.output / "complete.json").exists()


def test_live_observer_failure_cleans_owned_observers_only(mod, contract, monkeypatch):
    ctx = mod.load_contract(contract.path)
    batch = contract.root / "observer-test"
    batch.mkdir()
    start = contract.root / "start.json"
    dump(start, {"fixtureOnly": True})
    dump(
        contract.root / "runtime/current-capacity-api.json",
        {"sha": contract.protocol["candidateSha"], "start_record": str(start)},
    )
    live = object.__new__(mod.LiveRuntime)
    live.ctx, live.env = ctx, {}
    inspector = SimpleNamespace(ping=lambda: {"offline-worker": {}})
    live.app = SimpleNamespace(
        control=SimpleNamespace(
            inspect=lambda **kwargs: inspector, enable_events=lambda **kwargs: []
        )
    )
    live.quiet = lambda path: None
    live.command = lambda *args, **kwargs: None
    live.clear_input_caches = lambda path: None
    children = []

    class Child:
        def __init__(self, receiver):
            self.receiver, self.terminated = receiver, False
            self.pid = 990000 + len(children)

        def poll(self):
            return 1 if self.receiver else 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, **kwargs):
            return 0

    def popen(argv, **kwargs):
        child = Child("receive_capacity_events.py" in str(argv))
        children.append(child)
        return child

    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    with pytest.raises(mod.ContractError, match="observer exited early"):
        live.collect_batch(ctx.plan[0], batch, ctx.plan)
    assert len(children) == 2 and children[1].terminated
    record = json.loads((batch / "observer-cleanup.json").read_text())
    assert record["errors"] == []
    assert {x["role"] for x in record["actions"]} == {"collector"}


def test_live_benchmark_timeout_is_bounded_and_preserved(mod, contract, monkeypatch):
    import io

    batch = contract.root / "benchmark-timeout"
    batch.mkdir()
    live = object.__new__(mod.LiveRuntime)
    live.ctx = mod.load_contract(contract.path)
    live.env = {}

    class Client:
        stdout = io.BytesIO(b"OFFLINE fixture line\n")
        terminated = False

        def wait(self, timeout):
            if not self.terminated:
                raise subprocess.TimeoutExpired(["offline-client"], timeout)
            return -15

        def terminate(self):
            self.terminated = True

    client = Client()
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: client)
    with pytest.raises(mod.ContractError, match="timed out"):
        live.benchmark(["offline-client"], batch)
    record = json.loads((batch / "benchmark-command.json").read_text())
    assert record["exception"] == "TimeoutExpired" and record["rc"] is None
    assert client.terminated and client.stdout.closed
