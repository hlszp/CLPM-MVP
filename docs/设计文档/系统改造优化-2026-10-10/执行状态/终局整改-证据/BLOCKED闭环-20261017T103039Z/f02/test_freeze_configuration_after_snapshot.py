"""Offline real run()/snapshot() controls; all PG/HTTP/git edges are fakes."""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
ORIGINAL = HERE / "freeze_capacity_configuration.py"
NEW = HERE / "freeze_capacity_configuration_v2.py"
CONTROLS = ("STABLE", "SLOW", "FAST", "LOGIC")


def load(path):
    spec = importlib.util.spec_from_file_location("freeze_offline_subject", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTransaction:
    def __init__(self, db, **options):
        assert options == {"isolation": "repeatable_read", "readonly": True}
        self.db = db

    async def __aenter__(self):
        self.db.phase += 1

    async def __aexit__(self, *args):
        return None


class FakeDB:
    def __init__(self, module, tables, different_mvcc, changed):
        self.module, self.phase, self.calls, self.closed = module, -1, [], False
        after = deepcopy(tables)
        if changed:
            after["engine_rule"][0]["rule_value"]["zero"] = -1
        self.tables = [tables, after]
        self.raw = [
            {
                name: json.dumps(rows, ensure_ascii=False, indent=1) + "\n"
                for name, rows in t.items()
            }
            for t in self.tables
        ]
        self.identities = [
            {
                "database": "clpm_capacity_20261017_s01",
                "readonly": "on",
                "isolation": "repeatable read",
                "snapshot": "101:101:" if i == 0 or not different_mvcc else "102:102:",
                "captured_at": (
                    datetime(2026, 10, 17, tzinfo=timezone.utc) + timedelta(seconds=i)
                ).isoformat(),
            }
            for i in range(2)
        ]

    def transaction(self, **options):
        return FakeTransaction(self, **options)

    async def fetchrow(self, sql):
        self.calls.append((sql, ()))
        return dict(self.identities[self.phase])

    async def fetch(self, sql):
        self.calls.append((sql, ()))
        return [{"table_name": name} for name in self.module.TABLES]

    async def fetchval(self, sql, *params):
        self.calls.append((sql, params))
        if sql.startswith("SELECT count(*) FROM sys_config"):
            assert params == (list(self.module.SYS_KEYS),)
            return 7
        if "FROM sys_config t WHERE" in sql:
            assert params == (list(self.module.SYS_KEYS),)
            return self.raw[self.phase]["sys_config"]
        match = re.search(r" FROM (\w+) t$", sql)
        assert match is not None
        return self.raw[self.phase][match[1]]

    async def close(self):
        self.closed = True


def prepare(module, root, monkeypatch, *, different_mvcc, changed, api_revision=1):
    root.mkdir()
    runtime = root / "runtime"
    backend = runtime / "backend"
    for name in module.SOURCE_FILES:
        path = backend / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Pure offline source fixture, never imported.\n")
    defaults = {
        f"metric_{i}": {control: {"x": 1} for control in CONTROLS} for i in range(9)
    }
    literals = {
        "app/services/algorithm_config.py": ("_DEFAULTS", defaults),
        "app/tasks/kpi_calc.py": ("ALGORITHM_VERSION", "KPI_CALC_OFFLINE"),
        "app/services/preprocessing/pipeline.py": ("PREPROCESS_VERSION", "pre_offline"),
        "app/services/metric_calculator/time_axis.py": (
            "TIME_QUALITY_POLICY_VERSION",
            "tq_offline",
        ),
    }
    for name, (key, value) in literals.items():
        path = backend / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key + " = " + repr(value) + "\n")
    loops = [
        {
            "id": f"loop-{i}",
            "tag_name": f"PERF-T{i:04d}",
            "unit_id": f"unit-{(i - 1) // 50}",
            "is_active": True,
            "status": "READY",
            "include_in_evaluation": True,
            "control_type": "STABLE",
            "loop_type": "FLOW",
            "complex_loop_group_id": None,
        }
        for i in range(1, 1001)
    ]
    mappings = [
        {"loop_id": loop["id"], "tag_role": role, "tag_id": loop["id"] + "-" + role}
        for loop in loops
        for role in ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")
    ]
    tables = {name: [] for name in module.TABLES}
    tables.update(
        sys_config=[],
        loop_ledger=loops,
        loop_tag_mapping=mappings,
        tag_registry=[{"id": row["tag_id"]} for row in mappings],
        plant_node=[
            {"id": f"unit-{i}", "parent_id": None, "is_kpi_enabled": True}
            for i in range(20)
        ],
        metric_config=[
            {"metric_code": metric, "threshold": {"x": 1}} for metric in defaults
        ],
        config_publication=[{"revision": 1}],
        engine_rule=[
            {"rule_name": "offline-safe", "rule_value": {"zero": 0, "null": None}}
        ],
    )
    db = FakeDB(module, tables, different_mvcc, changed)
    progress, plan, token, env = [
        root / name for name in ("progress.json", "plan.json", "token.txt", "input.env")
    ]
    progress.write_text("{}")
    plan.write_text("[]")
    token.write_text("offline-only-test-token")
    token.chmod(0o600)
    env.write_text(
        "POSTGRES_HOST=127.0.0.1\nPOSTGRES_PORT=17102\nPOSTGRES_DB=clpm_capacity_20261017_s01\nPOSTGRES_USER=clpm\nPOSTGRES_PASSWORD=offline-only\nREDIS_HOST=127.0.0.1\nREDIS_PORT=17123\nTDENGINE_HOST=127.0.0.1\nTDENGINE_PORT=17104\nTDENGINE_DB=clpm_capacity_20261017_s01\n"
    )
    completed = {
        "verifiedLoopIdentity": sorted((x["id"], x["tag_name"]) for x in loops),
        "verifiedPointBindings": sorted(
            (x["loop_id"], x["tag_role"], x["tag_id"]) for x in mappings
        ),
        "verifiedPlannedBatchesSha256": module.sha(plan.read_bytes()),
        "results": [],
    }
    monkeypatch.setattr(module, "completed_inputs", lambda *args: deepcopy(completed))

    async def connect(**kwargs):
        assert kwargs["database"] == "clpm_capacity_20261017_s01"
        return db

    monkeypatch.setattr(module, "asyncpg", SimpleNamespace(connect=connect))

    def git(argv, **kwargs):
        if argv == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n")
        assert argv[:2] == ["git", "show"] and argv[2].startswith(
            "a" * 40 + ":backend/"
        )
        return SimpleNamespace(
            returncode=0,
            stdout=(backend / argv[2].split(":backend/", 1)[1]).read_bytes(),
        )

    monkeypatch.setattr(module, "subprocess", SimpleNamespace(run=git))
    merged = module.global_params(defaults, [], tables["metric_config"])
    metrics = [
        {
            "metricCode": metric,
            "items": [
                {"controlType": control, "params": merged[metric + "|" + control]}
                for control in CONTROLS
            ],
        }
        for metric in defaults
    ]

    class Http:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url):
            if url.endswith("/health"):
                body = {"status": "ok"}
            elif url.endswith("/configs/publish/revision"):
                body = {"code": "0", "data": {"revision": api_revision}}
            elif url.endswith("/configs/algorithm-params"):
                body = {"code": "0", "data": {"metrics": metrics}}
            else:
                body = {"code": "0", "data": {}}
            return SimpleNamespace(
                status_code=200,
                content=module.canonical(body),
                json=lambda: deepcopy(body),
            )

    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=Http))
    args = SimpleNamespace(
        input_audit_progress=str(progress),
        planned_batches=str(plan),
        env_file=str(env),
        runtime=str(runtime),
        candidate_sha="a" * 40,
        api_base="http://127.0.0.1:17101/api/v1",
        token_file=str(token),
        out=str(root / "output"),
    )
    return args, db


@pytest.mark.parametrize("different_mvcc", [False, True])
@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.asyncio
async def test_before_after_originals_preserved_even_when_blocked(
    tmp_path, monkeypatch, different_mvcc, changed
):
    module = load(Path(os.environ.get("CLPM_FREEZE_TOOL_SOURCE", NEW)))
    args, db = prepare(
        module,
        tmp_path / "case",
        monkeypatch,
        different_mvcc=different_mvcc,
        changed=changed,
    )
    rc = await module.run(args)
    out = Path(args.out)
    report = json.loads((out / "config-freeze.json").read_bytes())
    assert rc == (2 if changed else 0)
    assert report["status"] == ("BLOCKED" if changed else "FROZEN")
    assert db.closed and db.phase == 1
    for phase, raw_dir, canonical_dir in [
        (0, "pg-raw", "pg-canonical"),
        (1, "pg-after-raw", "pg-after-canonical"),
    ]:
        assert len(list((out / raw_dir).glob("*.json"))) == 25, (
            "actual after table bytes were not archived"
        )
        for table, expected_raw in db.raw[phase].items():
            assert (
                out / raw_dir / (table + ".json")
            ).read_bytes() == expected_raw.encode()
            restored = json.loads(
                gzip.decompress(
                    (out / canonical_dir / (table + ".json.gz")).read_bytes()
                )
            )
            assert restored == json.loads(expected_raw)
    for phase, name in enumerate(("before", "after")):
        identity = json.loads((out / ("pg-" + name + "-identity.json")).read_bytes())
        assert identity == db.identities[phase]
        evidence = report["postgresSnapshots"][name]
        assert evidence["contentDigest"] == module.sha(
            module.canonical(db.tables[phase])
        )
        assert evidence["tableCount"] == 25 and len(evidence["files"]) == 51
        for ref in evidence["files"]:
            raw = (out / ref["file"]).read_bytes()
            assert len(raw) == ref["bytes"] and module.sha(raw) == ref["sha256"]
    files = json.loads((out / "sha256-manifest.json").read_bytes())["files"]
    for ref in files:
        raw = (out / ref["file"]).read_bytes()
        assert len(raw) == ref["bytes"] and module.sha(raw) == ref["sha256"]
    if changed:
        assert "Full source/identity/config DB contents changed" in report["error"]
        assert (
            "configDigest" not in report
            and "fullDbStableAcrossObservation" not in report
        )
    else:
        derived = json.loads(
            gzip.decompress((out / "object-node-contexts.json.gz").read_bytes())
        )
        assert len(derived["effectiveParamsReconstructed"]) == 1000
        assert (
            sum(len(row["params"]) for row in derived["effectiveParamsReconstructed"])
            == 36000
        )


@pytest.mark.asyncio
async def test_after_originals_survive_api_revision_rejection(tmp_path, monkeypatch):
    module = load(NEW)
    args, db = prepare(
        module,
        tmp_path / "case",
        monkeypatch,
        different_mvcc=True,
        changed=False,
        api_revision=2,
    )
    assert await module.run(args) == 2
    report = json.loads((Path(args.out) / "config-freeze.json").read_bytes())
    assert report["status"] == "BLOCKED" and "revision mismatch" in report["error"]
    assert len(report["postgresSnapshots"]["after"]["files"]) == 51
    assert (
        json.loads((Path(args.out) / "pg-after-raw/loop_ledger.json").read_bytes())
        == db.tables[1]["loop_ledger"]
    )


def test_only_additive_run_block_other_original_ast_unchanged():
    original, updated = [ast.parse(path.read_bytes()) for path in (ORIGINAL, NEW)]
    old_run, new_run = [
        next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "run"
        )
        for tree in (original, updated)
    ]
    for old_node, new_node in zip(original.body, updated.body, strict=True):
        if old_node is old_run:
            continue
        assert ast.dump(old_node, include_attributes=False) == ast.dump(
            new_node, include_attributes=False
        )
    new_try = next(node for node in new_run.body if isinstance(node, ast.Try))
    start = (
        next(
            i
            for i, node in enumerate(new_try.body)
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "after"
        )
        + 1
    )
    end = next(
        i
        for i, node in enumerate(new_try.body)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "api_revision"
    )
    assert start < end
    del new_try.body[start:end]
    assert ast.dump(old_run, include_attributes=False) == ast.dump(
        new_run, include_attributes=False
    )


@pytest.mark.asyncio
async def test_legacy_digest_and_sql_are_unchanged(tmp_path, monkeypatch):
    results = []
    for name, source in (("original", ORIGINAL), ("new", NEW)):
        module = load(source)
        args, db = prepare(
            module, tmp_path / name, monkeypatch, different_mvcc=True, changed=False
        )
        assert await module.run(args) == 0
        report = json.loads((Path(args.out) / "config-freeze.json").read_bytes())
        results.append((report, db, args))
    old, new = results
    for key in (
        "configDigest",
        "postgresDigest",
        "versions",
        "configRevision",
        "loopCount",
        "mappingCount",
        "predictedNodeCount",
        "sysConfigWhitelist",
        "omittedSysConfigCount",
    ):
        assert old[0][key] == new[0][key]
    assert old[1].calls == new[1].calls
    for table in old[1].raw[0]:
        for directory, suffix in (("pg-raw", ".json"), ("pg-canonical", ".json.gz")):
            path = Path(directory) / (table + suffix)
            assert (Path(old[2].out) / path).read_bytes() == (
                Path(new[2].out) / path
            ).read_bytes()
