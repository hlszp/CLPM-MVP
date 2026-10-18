"""Run only frozen AST functions; no product imports, connections or real signals."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import types
import sys
from unittest.mock import MagicMock, patch

import pytest


def compile_nodes(source, names):
    tree = ast.parse(source.read_bytes())
    found = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in found} == names
    for node in found:
        node.decorator_list = []
    tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *found], type_ignores=[])
    ast.fix_missing_locations(tree)
    return compile(tree, str(source), "exec")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-source", type=Path, required=True)
    parser.add_argument("--main-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    original = json.loads((here / "unit-cleanup-boundary.json").read_text())["actualOriginalBeatRecord"]
    probes, terminations = [], []
    pattern = rf"celery.*-A app\.tasks\.celery_app.*beat.*{re.escape('clpm-remediation-20261017')}/"

    def pgrep(value):
        matched = re.search(value, " ".join(original["argv"])) is not None
        probes.append({"pattern": value, "matchesRecordedRealBeat": matched})
        return [original["pid"]] if matched else []

    def terminate(pids, label):
        terminations.append({"pids": pids, "label": label})

    def forbidden_signal(*args, **kwargs):
        raise AssertionError("No actual OS signals are permitted")

    main_module = types.ModuleType("app.main")
    main_module.__dict__.update({
        "os": os, "logger": MagicMock(), "_celery_beat_process": None,
        "_celery_beat_log_handle": None, "_celery_ever_touched": False,
        "_BEAT_PGREP_PATTERN": pattern, "_pgrep_pids": pgrep,
        "_terminate_pids_fallback": terminate, "_kill_process_group": forbidden_signal,
    })
    exec(compile_nodes(args.main_source, {"_stop_celery_beat", "_beat_pidfile_path"}), main_module.__dict__)
    app = types.ModuleType("app")
    app.main = main_module
    space = {"os": os, "patch": patch, "MagicMock": MagicMock}
    name = "test_stop_beat_keeps_pid_file_owned_by_existing_process"
    exec(compile_nodes(args.test_source, {name}), space)
    test_error = None
    with tempfile.TemporaryDirectory(prefix="pure-beat-test-") as directory:
        with patch.dict(sys.modules, {"app": app, "app.main": main_module}), patch("os.kill", forbidden_signal), patch("os.killpg", forbidden_signal), pytest.MonkeyPatch.context() as monkeypatch:
            try:
                space[name](Path(directory), monkeypatch)
            except BaseException as error:
                test_error = repr(error)
    passed = test_error is None and not probes and not terminations
    result = {
        "testSourceSha256": hashlib.sha256(args.test_source.read_bytes()).hexdigest(),
        "mainSourceSha256": hashlib.sha256(args.main_source.read_bytes()).hexdigest(),
        "originalRealBeatPid": original["pid"], "testError": test_error,
        "unmockedByTestPgrepCalls": probes,
        "unmockedByTestTerminationCalls": terminations,
        "productModulesImported": False, "networkOperations": 0,
        "actualSignalsSent": 0, "allProcessSignallingBoundariesIntercepted": True,
        "testIsolatedFromOtherProjectProcesses": passed,
        "scope": "Reachability counterexample only; historical signal sender remains unproven",
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
