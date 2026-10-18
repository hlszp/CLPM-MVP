"""No-signals proof that a unit test reaches process cleanup despite exit guards."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch

import pytest


def main():
    backend = Path("/Users/zhangping/DEV/clpm-remediation-20261017/backend")
    out = Path(__file__).resolve().parent / "f03-beat-start-review"
    out.mkdir(exist_ok=True)
    state_path = out.parent.parent / "f03/local-business-20261017213911-8a438372d213/current-f03-local-api.json"
    state = json.loads(state_path.read_text())
    original = next(row for row in state["processesAtStart"] if "beat" in row["argv"])
    sys.path.insert(0, str(backend))
    from app import main as product_main

    test_path = backend / "tests/test_runtime_regressions.py"
    spec = importlib.util.spec_from_file_location("independent_original_runtime_test", test_path)
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    probes, termination_calls = [], []

    def pgrep(pattern):
        matched = re.search(pattern, " ".join(original["argv"])) is not None
        probes.append({"pattern": pattern, "matchesActualRecordedInitialBeatArgv": matched})
        return [original["pid"]] if matched else []

    def record_termination(pids, label):
        termination_calls.append({"pids": pids, "label": label})

    def forbidden_signal(*args, **kwargs):
        raise AssertionError("A real process signal is forbidden in this independent proof")

    with tempfile.TemporaryDirectory(prefix="independent-unit-signal-") as directory:
        with pytest.MonkeyPatch.context() as monkeypatch, patch.object(product_main, "_pgrep_pids", pgrep), patch.object(product_main, "_terminate_pids_fallback", record_termination), patch.object(product_main, "_celery_beat_log_handle", None), patch("os.kill", forbidden_signal), patch("os.killpg", forbidden_signal):
            tests.test_stop_beat_keeps_pid_file_owned_by_existing_process(Path(directory), monkeypatch)
    result = {
        "testPath": str(test_path),
        "testSourceSha256": hashlib.sha256(test_path.read_bytes()).hexdigest(),
        "mainSourceSha256": hashlib.sha256(Path(product_main.__file__).read_bytes()).hexdigest(),
        "exitHookGuards": {key: os.environ.get(key) for key in ("CLPM_TEST_MODE", "CLPM_SKIP_EXIT_HOOKS")},
        "actualOriginalBeatRecord": original,
        "pgrepBoundaryWasMocked": True,
        "terminationBoundaryWasMocked": True,
        "actualSignalsSent": 0,
        "unitTestItselfPassed": True,
        "recordedPgrepCalls": probes,
        "recordedTerminationAttempts": termination_calls,
        "scope": "Demonstrates the test's unsafe reachable boundary; does not prove the historical sender of an observed signal",
    }
    (out / "unit-cleanup-boundary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
    assert probes and all(row["matchesActualRecordedInitialBeatArgv"] for row in probes)
    assert termination_calls == [{"pids": [original["pid"]], "label": "Celery Beat（遗留）"}]


if __name__ == "__main__":
    main()
