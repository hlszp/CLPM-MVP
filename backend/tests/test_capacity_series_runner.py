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
