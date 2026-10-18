"""Pure controls only: no sockets, subprocesses, signals or product imports."""

import importlib.util
import json
import re
import socket
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "capacity_binding_preparation", ROOT / "prepare_bindings.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("pure controls cannot use external IO")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(subprocess, "run", denied)


@pytest.fixture()
def original():
    p = json.loads((ROOT / "offline-plan/plan.json").read_text())
    reference = json.loads((ROOT / "offline-plan/reference.json").read_text())
    return p, reference


class Store:
    def __init__(self, p, tables, fault=None):
        self.p = p
        self.tables = deepcopy(tables)
        self.original = deepcopy(tables)
        self.fault = fault
        self.connections = []
        self.commands = []
        self.commits = 0
        self.rollbacks = 0
        self.backups = 0

    async def connect(self):
        c = Connection(self, len(self.connections) + 1)
        self.connections.append(c)
        if c.number == 2 and self.fault == "concurrent-change":
            self.tables["metric_config"][0]["version"] += 1
        return c

    async def backup(self, out):
        self.backups += 1
        (out / "before.dump").write_bytes(b"PGDMP-pure-control-not-a-real-backup")
        if self.fault == "backup-failed":
            raise OSError("controlled backup failure")
        return {"pureTestOnly": True, "bytes": (out / "before.dump").stat().st_size}

    async def tasks(self):
        return {"taskKeys": [], "brokerPendingOrUnacked": 0, "celeryResultKeys": []}


class Connection:
    def __init__(self, store, number):
        self.store = store
        self.number = number
        self.closed = False

    def transaction(self, isolation, readonly=False):
        self.isolation = isolation
        self.readonly = readonly
        return Transaction(self)

    async def close(self):
        self.closed = True

    async def fetchval(self, query, *args):
        s = self.store
        if query == tool.IDENTITY_SQL:
            if self.number == 3 and s.fault == "readback-disconnect":
                raise OSError("controlled postcommit disconnect")
            database = "production" if s.fault == "wrong-db" else tool.DATABASE
            return json.dumps(
                {
                    "database": database,
                    "readOnly": "on" if self.readonly else "off",
                    "isolation": self.isolation.replace("_", " "),
                    "snapshot": str(self.number),
                }
            )
        if query.startswith("SELECT COUNT(*)"):
            return 1 if s.fault == "nonempty-result" else 0
        m = re.search(r"FROM ([a-z_]+) t", query)
        if m:
            rows = deepcopy(s.tables[m[1]])
            if (
                self.number == 3
                and s.fault == "readback-mismatch"
                and m[1] == "loop_tag_binding_history"
            ):
                rows[0]["created_at"] = "2000-01-01T00:00:00+00:00"
            return json.dumps(rows)
        raise AssertionError(query)

    async def execute(self, query, *args):
        s = self.store
        s.commands.append(query)
        if query == tool.UPDATE_SQL:
            assert not self.readonly and self.isolation == "serializable"
            assert {str(v) for v in args[1]} == {
                r["id"] for r in s.tables["loop_tag_binding_history"]
            }
            for row in s.tables["loop_tag_binding_history"]:
                row.update(valid_from=args[0].isoformat(), basis="SYNTHETIC_FIXTURE")
            if s.fault == "unrelated-trigger-change":
                s.tables["metric_config"][0]["version"] += 1
            if s.fault == "history-created-at-change":
                s.tables["loop_tag_binding_history"][0]["created_at"] = "2000-01-01T00:00:00Z"
            return "UPDATE 6999" if s.fault == "partial-update" else "UPDATE 7000"
        return "OK"


class Transaction:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.before = deepcopy(self.conn.store.tables)

    async def __aexit__(self, kind, value, tb):
        s = self.conn.store
        if kind:
            s.tables = self.before
            s.rollbacks += 1
        elif not self.conn.readonly:
            s.commits += 1
            if s.fault == "commit-ack-lost":
                raise OSError("controlled commit acknowledgement lost")


@pytest.mark.asyncio
async def test_exact_success_retains_all_other_fields_and_tables(original, tmp_path):
    p, reference = original
    store = Store(p, reference)
    state = await tool.apply(
        p, reference, tmp_path / "case", store.connect, store.backup, store.tasks
    )
    assert state["committed"] is True and state["persistedReadbackVerified"] is True
    assert store.commits == 1 and store.backups == 1 and len(store.connections) == 3
    assert all(c.closed for c in store.connections)
    expected = deepcopy(reference)
    for r in expected["loop_tag_binding_history"]:
        r.update(valid_from=p["effectiveStartUtc"], basis="SYNTHETIC_FIXTURE")
    tool.same(store.tables, expected)
    assert sum(q == tool.UPDATE_SQL for q in store.commands) == 1
    assert any("SHARE ROW EXCLUSIVE" in q for q in store.commands)
    assert (tmp_path / "case/before/raw/loop_tag_binding_history.json").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "wrong-db",
        "nonempty-result",
        "backup-failed",
        "concurrent-change",
        "partial-update",
        "unrelated-trigger-change",
        "history-created-at-change",
    ],
)
@pytest.mark.asyncio
async def test_precommit_rejects_and_rolls_back(original, tmp_path, fault):
    p, reference = original
    store = Store(p, reference, fault)
    with pytest.raises((ValueError, OSError)):
        await tool.apply(p, reference, tmp_path / "case", store.connect, store.backup, store.tasks)
    state = json.loads((tmp_path / "case/state.json").read_text())
    assert state["committed"] is False and state["persistedReadbackVerified"] is False
    assert store.commits == 0 and all(c.closed for c in store.connections)
    if fault not in ("concurrent-change",):
        tool.same(store.tables, reference)


@pytest.mark.parametrize("fault", ["readback-disconnect", "readback-mismatch", "commit-ack-lost"])
@pytest.mark.asyncio
async def test_committed_and_verified_are_distinct(original, tmp_path, fault):
    p, reference = original
    store = Store(p, reference, fault)
    with pytest.raises((ValueError, OSError)):
        await tool.apply(p, reference, tmp_path / "case", store.connect, store.backup, store.tasks)
    state = json.loads((tmp_path / "case/state.json").read_text())
    assert state["committed"] is (None if fault == "commit-ack-lost" else True)
    assert state["persistedReadbackVerified"] is False and store.commits == 1
    assert all(c.closed for c in store.connections)


@pytest.mark.parametrize(
    "case",
    [
        "non-synthetic",
        "not-1000",
        "not-7000",
        "duplicate-role",
        "point-change",
        "closed-range",
        "old-version",
        "old-basis",
        "participation-off",
    ],
)
def test_roster_and_history_guards(original, case):
    p, tables = original
    if case == "non-synthetic":
        tables["loop_ledger"][0]["remark"] = "field data"
    elif case == "not-1000":
        tables["loop_ledger"].pop()
    elif case == "not-7000":
        tables["loop_tag_binding_history"].pop()
    elif case == "duplicate-role":
        tables["loop_tag_binding_history"][0]["tag_role"] = "UNRELATED"
    elif case == "point-change":
        tables["loop_tag_mapping"][0]["tag_id"] = "foreign"
    elif case == "closed-range":
        tables["loop_tag_binding_history"][0]["valid_to"] = "2026-10-18T00:00:00Z"
    elif case == "old-version":
        tables["loop_tag_binding_history"][0]["mapping_version"] = 2
    elif case == "old-basis":
        tables["loop_tag_binding_history"][0]["basis"] = "REBIND"
    elif case == "participation-off":
        tables["loop_ledger"][0]["include_in_evaluation"] = False
    with pytest.raises(ValueError):
        tool.validate(tables, tool.aware(p["effectiveStartUtc"]))


@pytest.mark.parametrize(
    "key,value",
    [
        ("POSTGRES_DB", "clpm"),
        ("POSTGRES_HOST", "remote"),
        ("POSTGRES_PORT", "5432"),
        ("POSTGRES_USER", "postgres"),
        ("REDIS_URL", "redis://127.0.0.1:17124/0"),
        ("REDIS_URL", "redis://remote:17123/0"),
    ],
)
def test_private_endpoint_guards(key, value):
    cfg = {
        "POSTGRES_DB": tool.DATABASE,
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "17102",
        "POSTGRES_USER": "clpm",
        "REDIS_URL": "redis://127.0.0.1:17123/0",
    }
    tool.private_config(cfg)
    cfg[key] = value
    with pytest.raises(ValueError):
        tool.private_config(cfg)


@pytest.mark.parametrize(
    "observation",
    [
        {},
        {"taskKeys": ["task:already"], "brokerPendingOrUnacked": 0, "celeryResultKeys": []},
        {"taskKeys": [], "brokerPendingOrUnacked": 1, "celeryResultKeys": []},
        {"taskKeys": [], "brokerPendingOrUnacked": 0, "celeryResultKeys": ["celery-task-meta-old"]},
    ],
)
def test_task_observation_guard(observation):
    with pytest.raises(ValueError):
        tool.task_guard(observation)


@pytest.mark.parametrize(
    "key,value", [("REDIS_HOST", "remote"), ("REDIS_PORT", "17124"), ("REDIS_DB", "1")]
)
def test_native_profile_separate_redis_fields(key, value):
    cfg = {
        "POSTGRES_DB": tool.DATABASE,
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "17102",
        "POSTGRES_USER": "clpm",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "17123",
        "REDIS_DB": "0",
    }
    tool.private_config(cfg)
    cfg[key] = value
    with pytest.raises(ValueError):
        tool.private_config(cfg)
