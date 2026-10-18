"""Pure tool tests: no product imports, network, actual processes or signals."""

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BASE = HERE.parent.parent
SOURCE = BASE / "f03/local-startup-v6/keeper-local-f03-v6.py"
OLD = BASE / "f03/local-term-v5/keeper-local-f03-v5b.py"
ACTUAL = BASE / "f03/local-business-20261017213911-8a438372d213/current-f03-local-api.json"
spec = importlib.util.spec_from_file_location("independent_startup_v6", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def synthetic():
    state = {"apiPid": 100, "apiCreated": 1100, "apiCwd": "unused", "bootstrapped": True}

    def row(pid, parent, argv):
        return {"pid": pid, "ppid": parent, "created": 1000 + pid, "status": "sleeping", "argv": argv}

    rows = [
        row(100, 90, ["python", "-m", "uvicorn", "--port", "17261"]),
        row(101, 100, ["python", "-m", "celery", "beat"]),
        row(102, 100, ["python", "-m", "celery", "worker"]),
        *[row(pid, 102, ["python", "-m", "celery", "worker"]) for pid in range(103, 107)],
    ]
    evidence = {
        "pidfile": "101\n",
        "beat": "beat: Starting... [beat] 父进程看门狗已启动（监视 PID=100）",
        "worker": "owned@host ready. [worker] 父进程看门狗已启动（监视 PID=100）",
        "api": "Celery Beat 调度进程已启动 (PID=101) Celery Worker 进程已启动 (PID=102)",
    }
    return state, rows, evidence


class IndependentStartup(unittest.TestCase):
    def test_source_and_unchanged_scope(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "87c985f311f804c80a3beb84d78e342143e277fe18d8f5041c5fcb54c81e405f")
        allowed = {"start", "snapshot_status", "startup_issues", "startup_evidence", "same_process_identities"}
        def remaining(path):
            tree = ast.parse(path.read_bytes())
            tree.body = [item for item in tree.body if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or item.name not in allowed]
            return ast.dump(tree, include_attributes=False)
        self.assertEqual(remaining(SOURCE), remaining(OLD))
        old_stop = next(n for n in ast.parse(OLD.read_bytes()).body if isinstance(n, ast.FunctionDef) and n.name == "stop")
        new_stop = next(n for n in ast.parse(SOURCE.read_bytes()).body if isinstance(n, ast.FunctionDef) and n.name == "stop")
        self.assertEqual(ast.get_source_segment(OLD.read_text(), old_stop), ast.get_source_segment(SOURCE.read_text(), new_stop))

    def test_actual_old_three_process_evidence_refused(self):
        actual = json.loads(ACTUAL.read_text())
        state, _, evidence = synthetic()
        state["apiPid"] = actual["apiPid"]
        self.assertEqual(len(actual["processesAtStart"]), 3)
        self.assertTrue(module.startup_issues(state, actual["processesAtStart"], evidence))

    def test_positive_and_all_readiness_boundaries(self):
        state, rows, evidence = synthetic()
        self.assertEqual(module.startup_issues(state, rows, evidence), [])
        for name, value in [
            ("pidfile", "999\n"), ("pidfile", ""),
            ("beat", "beat: Starting..."),
            ("beat", evidence["beat"].replace("PID=100", "PID=999")),
            ("worker", "owned@host ready."),
            ("worker", evidence["worker"].replace(" ready.", " not-ready")),
            ("api", evidence["api"] + " Celery Beat 调度进程已启动 (PID=101)"),
            ("api", evidence["api"] + " Celery Worker 进程已启动 (PID=777)"),
        ]:
            with self.subTest(field=name, raw=value):
                self.assertTrue(module.startup_issues(state, rows, {**evidence, name: value}))
        for key, value in [("pid", 101), ("ppid", 999), ("argv", ["foreign", "worker"])]:
            changed = copy.deepcopy(rows)
            changed[-1][key] = value
            with self.subTest(processField=key):
                self.assertTrue(module.startup_issues(state, changed, evidence))

    def test_snapshot_rejects_birth_command_parent_and_pause_changes(self):
        state, rows, evidence = synthetic()
        rows[1]["status"] = "stopped"
        state.update(startupReadinessVerified=True, processesAtStart=copy.deepcopy(rows), beatPausedPid=101)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.multiple(module, create=True, OUT=Path(temporary), get_state=lambda: state, owned_api=lambda value: SimpleNamespace(pid=100), startup_evidence=lambda value: evidence):
                with patch.object(module, "process_details", return_value=rows), patch.object(module, "queue_observation", return_value={"qualified": True}) as queue:
                    self.assertEqual(module.snapshot_status("positive"), {"qualified": True})
                    queue.assert_called_once()
                for field, value in [("created", 9999), ("argv", ["other"]), ("ppid", 999), ("status", "sleeping")]:
                    changed = copy.deepcopy(rows)
                    changed[1][field] = value
                    with self.subTest(field=field), patch.object(module, "process_details", return_value=changed), patch.object(module, "queue_observation") as queue:
                        with self.assertRaises(RuntimeError):
                            module.snapshot_status("negative-" + field)
                        queue.assert_not_called()
                state["startupReadinessVerified"] = False
                with patch.object(module, "process_details", return_value=rows), patch.object(module, "queue_observation") as queue:
                    with self.assertRaises(RuntimeError):
                        module.snapshot_status("old-state-without-readiness")
                    queue.assert_not_called()

    def exercise_start(self, stopped=True, identity_changed=False, drains=True):
        state, before, evidence = synthetic()
        paused = copy.deepcopy(before)
        paused[1]["status"] = "stopped" if stopped else "sleeping"
        if identity_changed:
            paused[-1]["created"] += 1
        state_saved, signals, launches = [], [], []
        ticks = [0]
        def sleep(seconds):
            ticks[0] += seconds
        def monotonic():
            ticks[0] += 1
            return ticks[0]
        class FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *arguments): return False
            def connect_ex(self, address): return 1
        class FakeProcess:
            def __init__(self, pid): self.pid = pid
            def create_time(self): return 1000 + self.pid
            def ppid(self): return 100
            def cmdline(self): return before[1]["argv"]
            def send_signal(self, value): signals.append((self.pid, value))
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out / "site").mkdir()
            (out / "site/index.html").write_text("test-only")
            (out / "site/_app.config.js").write_bytes(b"unit-config")
            native = out / "unit-native.env"
            native.write_text("mock-only")
            def popen(argv, **kwargs):
                launches.append(argv)
                if "supervisor-api" in argv:
                    (out / "api-launch.json").write_text(json.dumps(state))
                return SimpleNamespace(pid=900 + len(launches), poll=lambda: None)
            def details(pid):
                return before if not signals else paused
            pending = {"active": {"owned": ["pending"]}, "reserved": {"owned": []}, "scheduled": {"owned": []}, "redis": {"brokerLists": {}, "unacked": 0, "unackedIndex": 0}}
            quiet = copy.deepcopy(pending)
            quiet["active"]["owned"] = []
            queue_reads = [pending, quiet] if drains else [pending] * 100
            fake_psutil = SimpleNamespace(Process=FakeProcess, STATUS_STOPPED="stopped")
            with patch.dict(sys.modules, {"psutil": fake_psutil}), patch.multiple(module, create=True, OUT=out, CHECKOUT=out, PY="unit-python", ENV={"E2E_API_BASE_URL": "unit-no-network"}, ARGS=SimpleNamespace(expected_sha="a" * 40, run_id="unit", native_env_file=native), fixed_checkout=lambda: None, get_state=lambda: copy.deepcopy(state), save_state=lambda value: state_saved.append(copy.deepcopy(value)), process_details=details, startup_evidence=lambda value: evidence, http_get=lambda url: b"unit-config", snapshot_status=lambda label: queue_reads.pop(0)), patch.object(module.socket, "socket", FakeSocket), patch.object(module.subprocess, "Popen", popen), patch.object(module.os, "getpgid", return_value=902), patch.object(module.time, "sleep", sleep), patch.object(module.time, "monotonic", monotonic):
                error = None
                try:
                    module.start()
                except RuntimeError as exception:
                    error = str(exception)
            return {"signals": signals, "launches": launches, "states": state_saved, "error": error}

    def test_actual_start_fake_positive_waits_for_drain_and_rechecks_stop(self):
        result = self.exercise_start()
        self.assertIsNone(result["error"])
        self.assertEqual(result["signals"], [(101, signal.SIGSTOP)])
        self.assertEqual(len(result["launches"]), 2)
        self.assertTrue(result["states"][-1]["startupReadinessVerified"])
        self.assertEqual(len(result["states"][-1]["processesAtStart"]), 7)

    def test_actual_start_fake_refuses_unstopped_beat_before_frontend(self):
        result = self.exercise_start(stopped=False)
        self.assertIn("did not remain stopped", result["error"])
        self.assertEqual(len(result["launches"]), 1)

    def test_actual_start_fake_refuses_identity_change_during_pause(self):
        result = self.exercise_start(identity_changed=True)
        self.assertIn("ownership changed", result["error"])
        self.assertEqual(len(result["launches"]), 1)

    def test_actual_start_fake_pending_tasks_fail_instead_of_seed(self):
        result = self.exercise_start(drains=False)
        self.assertIn("startup tasks did not drain", result["error"])


if __name__ == "__main__":
    unittest.main()
