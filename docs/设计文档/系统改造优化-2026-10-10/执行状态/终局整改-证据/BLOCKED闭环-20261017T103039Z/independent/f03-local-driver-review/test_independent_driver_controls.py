"""No product services, sockets, DB calls or real OS signals are performed."""

import ast
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import signal
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "local_driver_subject", ROOT / "driver.before.py"
)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def test_environment_erases_shared_endpoints_and_fixes_dedicated_profile(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        subject, "native_credentials", lambda: {"POSTGRES_PASSWORD": "synthetic"}
    )
    monkeypatch.setattr(
        subject,
        "ARGS",
        types.SimpleNamespace(run_id="20261017123456", expected_sha="a" * 40),
        raising=False,
    )
    monkeypatch.setattr(subject, "CHECKOUT", tmp_path, raising=False)
    monkeypatch.setattr(subject, "OUT", tmp_path / "out", raising=False)
    for key in [
        "POSTGRES_DB",
        "REDIS_HOST",
        "CELERY_BROKER_URL",
        "GITHUB_ACTIONS",
        "CLPM_PARENT_PID",
    ]:
        monkeypatch.setenv(key, "wrong-shared")
    env = subject.environment()
    assert env["POSTGRES_DB"] == "clpm_critical_20261017_local"
    assert env["TDENGINE_DB"] == "clpm_critical_20261017_local_ts"
    assert env["REDIS_PORT"] == "17124" and env["REDIS_DB"] == "0"
    assert env["CELERY_BROKER_URL"] == "redis://127.0.0.1:17124/1"
    assert env["CELERY_RESULT_BACKEND"] == "redis://127.0.0.1:17124/2"
    assert env["E2E_API_BASE_URL"] == "http://127.0.0.1:17261/api/v1"
    assert env["E2E_BASE_URL"] == "http://127.0.0.1:17266"
    assert "GITHUB_ACTIONS" not in env and "CLPM_PARENT_PID" not in env
    assert all(
        env[k] == "1"
        for k in [
            "OPENBLAS_NUM_THREADS",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "VECLIB_MAXIMUM_THREADS",
        ]
    )


def queue_program():
    tree = ast.parse((ROOT / "driver.before.py").read_text())
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "queue_observation"
    )
    return ast.literal_eval(fn.body[0].value)


@pytest.mark.parametrize(
    "broker,backend,rejected",
    [
        ("redis://127.0.0.1:17124/1", "redis://127.0.0.1:17124/2", False),
        ("redis://127.0.0.1:17123/1", "redis://127.0.0.1:17124/2", True),
        ("redis://127.0.0.1:17124/0", "redis://127.0.0.1:17124/2", True),
        ("redis://127.0.0.1:17124/1", "redis://127.0.0.1:17123/2", True),
        ("redis://foreign.invalid:17124/1", "redis://127.0.0.1:17124/2", True),
    ],
)
def test_actual_broker_and_backend_guards_precede_inspect_and_redis(
    monkeypatch, broker, backend, rejected
):
    calls = []

    class Inspect:
        def __getattr__(self, name):
            return lambda: {"worker": []}

    class Control:
        def inspect(self, **kwargs):
            calls.append("inspect")
            return Inspect()

    app = types.SimpleNamespace(
        conf=types.SimpleNamespace(broker_url=broker, result_backend=backend),
        control=Control(),
    )
    mod = types.ModuleType("app.tasks.celery_app")
    mod.celery_app = app
    monkeypatch.setitem(sys.modules, "app", types.ModuleType("app"))
    monkeypatch.setitem(sys.modules, "app.tasks", types.ModuleType("app.tasks"))
    monkeypatch.setitem(sys.modules, "app.tasks.celery_app", mod)

    class Client:
        def info(self, section):
            return {"role": "master", "run_id": "synthetic"}

        def dbsize(self):
            return 0

        def scan_iter(self):
            return iter([])

        def hlen(self, key):
            return 0

        def zcard(self, key):
            return 0

    class Redis:
        @staticmethod
        def from_url(url):
            calls.append("redis")
            return Client()

    red = types.ModuleType("redis")
    red.Redis = Redis
    monkeypatch.setitem(sys.modules, "redis", red)
    monkeypatch.setattr(sys, "argv", ["synthetic", "workers"])
    if rejected:
        with pytest.raises(AssertionError):
            exec(compile(queue_program(), "embedded-queue.py", "exec"), {})
        assert calls == []
    else:
        with redirect_stdout(io.StringIO()):
            exec(compile(queue_program(), "embedded-queue.py", "exec"), {})
        assert calls == ["inspect", "redis"]


def stopping_fixture(monkeypatch, tmp_path, defect=None):
    calls = []
    gone = set()
    state = {
        "apiPid": 1,
        "apiCreated": 100.0,
        "apiCwd": str(tmp_path / "cwd"),
        "beatPausedPid": 3,
        "beatPausedCreated": 300.0,
        "frontendPid": 8,
        "frontendCreated": 800.0,
        "frontendPgid": 8,
        "nativeDotenvSha256": subject.sha(b"synthetic-native"),
    }
    native = tmp_path / "native.env"
    native.write_bytes(b"synthetic-native")
    (tmp_path / "api-exit.json").write_text(
        json.dumps({"rc": 0 if defect != "bad-exit" else 1})
    )

    class NoSuchProcess(Exception):
        pass

    class TimeoutExpired(Exception):
        pass

    class Process:
        def __init__(self, pid):
            if pid in gone:
                raise NoSuchProcess()
            self.pid = pid

        def create_time(self):
            return float(
                self.pid * 100
                + (1 if defect == "beat-recycled" and self.pid == 3 else 0)
            )

        def ppid(self):
            return 1 if self.pid == 3 else 0

        def cmdline(self):
            return (
                ["celery", "beat"] if self.pid == 3 else ["uvicorn", "--port", "17261"]
            )

        def send_signal(self, s):
            calls.append((self.pid, s))

        def wait(self, timeout):
            if self.pid == 1:
                gone.update(range(1, 8))
            if self.pid == 8:
                gone.add(8)

        def status(self):
            return "sleeping"

    ps = types.ModuleType("psutil")
    ps.Process = Process
    ps.NoSuchProcess = NoSuchProcess
    ps.TimeoutExpired = TimeoutExpired
    monkeypatch.setitem(sys.modules, "psutil", ps)
    monkeypatch.setattr(subject, "get_state", lambda: state)
    monkeypatch.setattr(subject, "owned_api", lambda s: Process(1))
    monkeypatch.setattr(
        subject,
        "process_details",
        lambda pid: [{"pid": p, "created": float(p * 100)} for p in range(1, 8)],
    )
    data = {
        "active": {"worker": []},
        "reserved": {"worker": []},
        "scheduled": {"worker": []},
        "redis": {"role": "master", "brokerLists": {}, "unacked": 0, "unackedIndex": 0},
    }
    if defect == "active":
        data["active"]["worker"] = ["task"]
    monkeypatch.setattr(subject, "snapshot_status", lambda label: data)
    monkeypatch.setattr(
        subject,
        "queue_observation",
        lambda label, require_worker: {
            "redis": {"brokerLists": {}, "unacked": 0, "unackedIndex": 0}
        },
    )
    monkeypatch.setattr(subject, "OUT", tmp_path, raising=False)
    monkeypatch.setattr(
        subject, "ARGS", types.SimpleNamespace(native_env_file=native), raising=False
    )
    monkeypatch.setattr(subject, "save_state", lambda s: None)

    async def observe(label):
        calls.append(("capacity", label))

    monkeypatch.setattr(subject, "capacity_observation", observe)
    monkeypatch.setattr(subject.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(
        subject.os, "killpg", lambda pid, s: calls.append(("group", pid, s))
    )
    return calls


def test_stop_orders_beat_term_cont_then_immediate_api_term_and_records_exit(
    monkeypatch, tmp_path
):
    calls = stopping_fixture(monkeypatch, tmp_path)
    subject.stop()
    assert calls[:3] == [(3, signal.SIGTERM), (3, signal.SIGCONT), (1, signal.SIGTERM)]
    assert calls[-1] == ("capacity", "after-stop")
    assert (
        json.loads((tmp_path / "stopped-process-check.json").read_text())["remaining"]
        == []
    )


@pytest.mark.parametrize("defect", ["active", "beat-recycled"])
def test_not_drained_or_recycled_beat_never_receives_os_signals(
    monkeypatch, tmp_path, defect
):
    calls = stopping_fixture(monkeypatch, tmp_path, defect)
    with pytest.raises(RuntimeError):
        subject.stop()
    assert calls == []


def test_actual_api_nonzero_exit_is_not_signed_success(monkeypatch, tmp_path):
    calls = stopping_fixture(monkeypatch, tmp_path, "bad-exit")
    with pytest.raises(RuntimeError, match="real exit rc=1"):
        subject.stop()
    assert ("capacity", "after-stop") not in calls
