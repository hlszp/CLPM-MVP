"""Only fake PS/git/lsof/clock; no actual processes or services are queried."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
from datetime import datetime, timedelta, timezone
import pytest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "sampler_under_review", ROOT / "sampler.original.py"
)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


def line(pid, ppid, kind, birth="Sat Oct 17 20:00:00 2026", state="S"):
    args = (
        "uvicorn app.main:app --port 17101"
        if kind == "api"
        else "celery -A app.tasks.celery_app " + kind + " -l info"
    )
    return f"{pid} {ppid} {birth} {state} {subject.PYTHON} -m {args}"


def test_birth_identity_changes_on_reused_pid_but_not_state():
    original = subject.parse_owned(line(22, 11, "worker"))[0]
    recycled = subject.parse_owned(
        line(22, 11, "worker", birth="Sat Oct 17 20:00:01 2026")
    )[0]
    awake = subject.parse_owned(line(22, 11, "worker", state="R+"))[0]
    assert original["identity"] != recycled["identity"]
    assert original["identity"] == awake["identity"]
    assert original["argv"] == recycled["argv"]
    assert original["rawLine"] == line(22, 11, "worker")


@pytest.mark.parametrize(
    "defect", [None, "ps-fail", "sha-drift", "env-drift", "malformed-row", "lsof-fail"]
)
def test_main_preserves_originals_and_failures_without_business_qualification(
    monkeypatch, tmp_path, defect
):
    runtime = tmp_path / "runtime"
    (runtime / "backend").mkdir(parents=True)
    native = runtime / "backend/.env"
    native.write_bytes(b"synthetic-only")
    taos = tmp_path / "taos.cfg"
    taos.write_bytes(b"synthetic-config")
    monkeypatch.setattr(subject, "RUNTIME", runtime)
    monkeypatch.setattr(subject, "TAOS", taos)
    clock = [0.0]
    monkeypatch.setattr(
        subject,
        "time",
        types.SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda delta: clock.__setitem__(0, clock[0] + delta),
        ),
    )
    base = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        subject, "utc", lambda: (base + timedelta(seconds=clock[0])).isoformat()
    )
    monkeypatch.setattr(
        subject,
        "signal",
        types.SimpleNamespace(SIGTERM=15, SIGINT=2, signal=lambda *args: None),
    )
    raw = "\n".join(
        [line(10, 1, "api"), line(11, 10, "worker"), line(12, 10, "beat", state="T")]
        + [line(i, 11, "worker") for i in range(13, 17)]
    )
    calls = []

    def fake_run(argv, timeout=3):
        calls.append(argv)
        stamp = subject.utc()
        body = {
            "argv": argv,
            "startedAt": stamp,
            "finishedAt": stamp,
            "rc": 0,
            "stdout": "",
            "stderr": "",
        }
        if argv == subject.PS_ARGV:
            body["stdout"] = raw
            if defect == "ps-fail":
                body.update(rc=1, stderr="synthetic ps failure")
            if defect == "env-drift":
                native.write_bytes(b"changed")
            if defect == "malformed-row":
                body["stdout"] = "1 2 T " + subject.PYTHON + " -m celery worker "
        elif argv[0] == "git":
            body["stdout"] = (
                ("b" * 40 if defect == "sha-drift" else "a" * 40)
                + "\n"
                + "c" * 40
                + "\n"
            )
        elif argv[0] == "date":
            body["stdout"] = "CST +0800\n"
        elif argv[0] == "lsof":
            body["stdout"] = (
                "p10\nfcwd\nn/synthetic/runtime/backend\nftxt\nn/synthetic/python\n"
            )
            if defect == "lsof-fail":
                body.update(rc=1, stderr="synthetic denied")
        else:
            raise AssertionError(argv)
        return body

    monkeypatch.setattr(subject, "run", fake_run)
    out = tmp_path / "output"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "sampler",
            "--expected-sha",
            "a" * 40,
            "--out",
            str(out),
            "--interval",
            "2",
            "--max-seconds",
            "4",
        ],
    )
    rc = subject.main()
    summary = json.loads((out / "summary.json").read_text())
    plan = json.loads((out / "plan.json").read_text())
    data = (out / "observations.jsonl").read_bytes()
    assert summary["observations"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert summary["observations"]["bytes"] == len(data)
    assert (
        summary["planSha256"]
        == hashlib.sha256((out / "plan.json").read_bytes()).hexdigest()
    )
    assert summary["acceptance"].startswith("PENDING")
    if defect in {"ps-fail", "sha-drift", "env-drift", "malformed-row"}:
        assert rc == 1 and summary["errors"]
    else:
        assert rc == 0 and summary["pollCount"] == 2
        observations = [json.loads(line) for line in data.splitlines()]
        assert len(observations[0]["members"]) == 7
        assert observations[0]["selectedRawPsLines"] == raw.splitlines()
        assert [r["role"] for r in observations[0]["members"]] == [
            "api",
            "worker_master",
            "beat",
        ] + ["worker_child"] * 4
        assert observations[1]["firstSeenLsofRefs"] == []
        ref = observations[0]["firstSeenLsofRefs"][0]
        payload = Path(ref["path"]).read_bytes()
        assert ref["sha256"] == hashlib.sha256(payload).hexdigest()
        assert json.loads(payload)["actualLsof"]["rc"] == (
            1 if defect == "lsof-fail" else 0
        )
        assert plan["psArgv"] == [
            "ps",
            "-ax",
            "-o",
            "pid=,ppid=,lstart=,stat=,command=",
        ]
    assert set(c[0] for c in calls) <= {"ps", "git", "date", "lsof"}
