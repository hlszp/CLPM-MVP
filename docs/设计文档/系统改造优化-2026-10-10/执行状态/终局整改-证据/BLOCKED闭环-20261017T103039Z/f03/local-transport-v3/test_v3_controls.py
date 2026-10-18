"""Pure fake-process controls; no sockets, CLPM imports, subprocesses, or DBs."""

from __future__ import annotations
import ast
import datetime
import hashlib
import json
from pathlib import Path
import signal
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SOURCE = HERE / "keeper-local-f03-v3.py"


class NoSuchProcess(Exception):
    pass


class Controls(unittest.TestCase):
    def scenario(self, mode="normal"):
        with tempfile.TemporaryDirectory(prefix="f03-v3-pure-") as directory:
            out = Path(directory)
            native = out / "native.env"
            native.write_bytes(b"sentinel native env; no credentials")
            log = out / "backend-api.stdout.log"
            old = {
                "timestamp": "2000-01-01T00:00:00+00:00",
                "logger": "app.main",
                "message": "停止 Celery Worker 进程...",
            }
            log.write_text(json.dumps(old) + "\n")
            state = {
                "apiPid": 100,
                "apiCreated": 100.0,
                "apiCwd": str(out / "runtime"),
                "beatPausedPid": 101,
                "beatPausedCreated": 101.0,
                "frontendPid": 200,
                "frontendCreated": 200.0,
                "frontendPgid": 200,
                "nativeDotenvSha256": hashlib.sha256(native.read_bytes()).hexdigest(),
            }
            Path(state["apiCwd"]).mkdir()
            alive = {100: True, 101: True, 102: True, 103: True, 200: True}
            events = []
            clock = [0.0]
            saved = []
            calls = {"capacity": 0, "broker": 0}

            class Process:
                def __init__(self, pid):
                    if not alive.get(pid):
                        raise NoSuchProcess(pid)
                    self.pid = pid

                def create_time(self):
                    if self.pid == 101 and mode == "wrong-beat-identity":
                        return 555.0
                    return float(self.pid)

                def ppid(self):
                    return (
                        100 if self.pid in {101, 102} else 102 if self.pid == 103 else 1
                    )

                def status(self):
                    return "stopped" if self.pid == 101 else "running"

                def cmdline(self):
                    return {
                        100: ["uvicorn", "app.main:app", "--port", "17261"],
                        101: ["celery", "beat"],
                        102: ["celery", "worker"],
                        103: ["pool"],
                        200: ["serve-site"],
                    }[self.pid]

                def children(self, recursive=True):
                    if not alive[self.pid]:
                        raise NoSuchProcess(self.pid)
                    return [Process(pid) for pid in [101, 102, 103] if alive[pid]]

                def send_signal(self, value):
                    events.append((self.pid, value))
                    if self.pid == 100 and value == signal.SIGTERM:
                        if mode != "missing-barrier":
                            row = {
                                "timestamp": (
                                    datetime.datetime.now(datetime.timezone.utc)
                                    + datetime.timedelta(seconds=1)
                                ).isoformat(),
                                "logger": "app.main",
                                "message": "停止 Celery Worker 进程...",
                            }
                            with log.open("a") as stream:
                                # Exact message on the wrong logger cannot serve as a barrier.
                                stream.write(
                                    json.dumps({**row, "logger": "other"}) + "\n"
                                )
                                stream.write("INFO: Shutting down\n")
                                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    elif self.pid == 101 and value == signal.SIGTERM:
                        self_outer.assertTrue(
                            (out / "watchdog-stop-barrier.json").exists()
                        )
                        self_outer.assertEqual(events[0], (100, signal.SIGTERM))
                    elif self.pid == 101 and value == signal.SIGCONT:
                        for pid in [100, 101, 102, 103]:
                            alive[pid] = False
                        (out / "api-exit.json").write_text(
                            json.dumps(
                                {"pid": 100, "rc": -2 if mode == "api-minus-two" else 0}
                            )
                        )
                    elif self.pid == 100 and value == signal.SIGINT:
                        alive[100] = alive[102] = alive[103] = False
                        (out / "api-exit.json").write_text(
                            json.dumps({"pid": 100, "rc": -2})
                        )

                def wait(self, timeout):
                    if self.pid == 200:
                        alive[200] = False
                        return None  # A non-child absence is not a fabricated rc=0.
                    return 0

            self_outer = self
            fake_psutil = types.SimpleNamespace(
                Process=Process,
                NoSuchProcess=NoSuchProcess,
                STATUS_STOPPED="stopped",
                STATUS_ZOMBIE="zombie",
            )

            def write(path, item):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(item, ensure_ascii=False) + "\n")

            def status(label):
                return {
                    "active": {"one-worker": ["busy"] if mode == "not-drained" else []},
                    "reserved": {"one-worker": []},
                    "scheduled": {"one-worker": []},
                    "redis": {
                        "role": "master",
                        "brokerLists": {"default": 0},
                        "unacked": 0,
                        "unackedIndex": 0,
                    },
                }

            def queue(label, require_worker=True):
                self.assertFalse(require_worker)
                calls["broker"] += 1
                return {
                    "redis": {
                        "brokerLists": {"default": 3 if mode == "pending-three" else 0},
                        "unacked": 0,
                        "unackedIndex": 0,
                    }
                }

            async def capacity(label):
                calls["capacity"] += 1

            def killpg(pid, value):
                self.assertEqual(pid, 200)
                self.assertEqual(value, signal.SIGTERM)
                events.append((pid, value))

            module = ast.parse(SOURCE.read_text())
            stop = next(
                node
                for node in module.body
                if isinstance(node, ast.FunctionDef) and node.name == "stop"
            )
            import asyncio, shutil

            namespace = {
                "OUT": out,
                "ARGS": types.SimpleNamespace(native_env_file=native),
                "get_state": lambda: state,
                "owned_api": lambda _: Process(100),
                "snapshot_status": status,
                "process_details": lambda _: [
                    {
                        "pid": p.pid,
                        "ppid": p.ppid(),
                        "created": p.create_time(),
                        "status": p.status(),
                        "argv": p.cmdline(),
                    }
                    for p in [Process(100), *Process(100).children()]
                ],
                "queue_observation": queue,
                "capacity_observation": capacity,
                "save_state": lambda item: saved.append(dict(item)),
                "write": write,
                "sha": lambda b: hashlib.sha256(b).hexdigest(),
                "now": lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "time": types.SimpleNamespace(
                    monotonic=lambda: clock[0],
                    sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                ),
                "os": types.SimpleNamespace(
                    getpgid=lambda pid: 999 if mode == "wrong-front-pgid" else pid,
                    killpg=killpg,
                ),
                "json": json,
                "datetime": datetime,
                "Path": Path,
                "shutil": shutil,
                "signal": signal,
                "asyncio": asyncio,
            }
            exec(
                compile(ast.Module(body=[stop], type_ignores=[]), str(SOURCE), "exec"),
                namespace,
            )
            error = None
            with patch.dict(sys.modules, {"psutil": fake_psutil}):
                try:
                    namespace["stop"]()
                except RuntimeError as problem:
                    error = str(problem)
            result = json.loads((out / "stop-outcome.json").read_text())
            self.assertEqual(calls, {"capacity": 1, "broker": 1})
            self.assertEqual(
                native.read_bytes(), b"sentinel native env; no credentials"
            )
            self.assertTrue((out / "stopped-process-check.json").exists())
            if mode != "wrong-front-pgid":
                self.assertIn((200, signal.SIGTERM), events)
                self.assertFalse(alive[200])
                self.assertIsNone(
                    json.loads((out / "frontend-stop.json").read_text())[
                        "waitReturnCode"
                    ]
                )
            else:
                self.assertNotIn((200, signal.SIGTERM), events)
            if mode == "normal":
                self.assertIsNone(error)
                self.assertTrue(saved[0]["stopped"])
                self.assertEqual(
                    events[:3],
                    [
                        (100, signal.SIGTERM),
                        (101, signal.SIGTERM),
                        (101, signal.SIGCONT),
                    ],
                )
                barrier = json.loads((out / "watchdog-stop-barrier.json").read_text())
                self.assertGreaterEqual(
                    barrier["offsetStart"], len((json.dumps(old) + "\n").encode())
                )
                self.assertEqual(
                    barrier["rawSha256"],
                    hashlib.sha256(
                        (out / "watchdog-stop-barrier.raw.log").read_bytes()
                    ).hexdigest(),
                )
            else:
                self.assertIsNotNone(error)
                self.assertFalse(saved)
                self.assertTrue(result["errors"])
            if mode == "missing-barrier":
                self.assertNotIn((101, signal.SIGCONT), events)
                self.assertIn((100, signal.SIGINT), events)
                self.assertFalse(result["watchdogBarrierObserved"])
            if mode in {"wrong-beat-identity", "not-drained"}:
                self.assertNotIn((100, signal.SIGTERM), events)
            return mode

    def test_normal_order_barrier_and_true_exit(self):
        self.scenario()

    def test_missing_barrier_keeps_beat_paused_and_cleans_front(self):
        self.scenario("missing-barrier")

    def test_minus_two_is_failure_and_front_still_cleaned(self):
        self.scenario("api-minus-two")

    def test_pending_three_is_failure(self):
        self.scenario("pending-three")

    def test_beat_identity_rejects_without_signal(self):
        self.scenario("wrong-beat-identity")

    def test_busy_queue_rejects_without_api_signal(self):
        self.scenario("not-drained")

    def test_wrong_front_group_not_signalled(self):
        self.scenario("wrong-front-pgid")

    def test_outside_three_functions_unchanged(self):
        old = (HERE.parent / "local-four-case-plan/keeper-local-f03-v2.py").read_text()
        new = SOURCE.read_text()

        def stripped(source):
            lines = source.splitlines(keepends=True)
            ranges = [
                (node.lineno - 1, node.end_lineno)
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef)
                and node.name in {"serve_site", "supervisor_api", "stop"}
            ]
            for begin, end in reversed(ranges):
                del lines[begin:end]
            return "".join(lines)

        self.assertEqual(stripped(old), stripped(new))
        tree = ast.parse(new)
        start = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "supervisor_api"
        )
        strings = [
            n.value
            for n in ast.walk(start)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        self.assertIn("--timeout-graceful-shutdown", strings)
        self.assertIn("15", strings)
        self.assertIn("--no-access-log", strings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
