"""Keeper-only synthetic INPUT declaration; plan is offline, apply is explicit.

No application imports, TD calls, task publication, service operations or result writes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

DATABASE = "clpm_capacity_20261017_s01"
ROLES = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
RESULTS = (
    "kpi_snapshot_hourly",
    "kpi_snapshot_custom",
    "loop_confidence_latest",
    "kpi_node_snapshot_hourly",
    "kpi_node_snapshot_daily",
    "kpi_node_snapshot_monthly",
    "calculation_result_record",
    "calculation_result_attempt",
    "calculation_result_legacy_map",
    "calculation_dataset_snapshot",
    "workbench_loop_latest",
    "unit_kpi_summary",
)
IDENTITY_SQL = """SELECT json_build_object('database',current_database(),
 'readOnly',current_setting('transaction_read_only'),
 'isolation',current_setting('transaction_isolation'),
 'snapshot',txid_current_snapshot()::text,'txidIfAssigned',txid_current_if_assigned(),
 'observedAtUtc',clock_timestamp(),'currentUser',current_user)::text"""
UPDATE_SQL = """UPDATE loop_tag_binding_history
 SET valid_from=$1,basis='SYNTHETIC_FIXTURE'
 WHERE id=ANY($2::uuid[]) AND basis='MVP_INIT'
 AND mapping_version=1 AND valid_to IS NULL"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def aware(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)


def norm(value):
    if isinstance(value, dict):
        return {k: norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [norm(v) for v in value]
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)", value):
        return aware(value).isoformat(timespec="microseconds")
    return value


def canonical(rows):
    return sorted(json.dumps(norm(r), sort_keys=True, ensure_ascii=False) for r in rows)


def private_config(cfg):
    if (
        cfg.get("POSTGRES_DB") != DATABASE
        or cfg.get("POSTGRES_HOST") not in ("127.0.0.1", "localhost")
        or str(cfg.get("POSTGRES_PORT")) != "17102"
        or cfg.get("POSTGRES_USER") != "clpm"
    ):
        raise ValueError("exact private PG endpoint/database/user required")
    if cfg.get("REDIS_URL"):
        redis_url = urlsplit(cfg["REDIS_URL"])
        valid = (
            redis_url.scheme == "redis"
            and not redis_url.query
            and not redis_url.fragment
            and redis_url.hostname in ("127.0.0.1", "localhost")
            and redis_url.port == 17123
            and redis_url.path in ("", "/0")
        )
    else:
        valid = (
            cfg.get("REDIS_HOST") in ("127.0.0.1", "localhost")
            and str(cfg.get("REDIS_PORT")) == "17123"
            and str(cfg.get("REDIS_DB")) == "0"
        )
    if not valid:
        raise ValueError("exact private Redis 17123/0 required")


def validate(tables, effective, prepared=False):
    loops, mappings, histories, points = (
        tables[n]
        for n in ("loop_ledger", "loop_tag_mapping", "loop_tag_binding_history", "tag_registry")
    )
    expected_tags = {f"PERF-T{i:04d}" for i in range(1, 1001)}
    if len(loops) != 1000 or {r["tag_name"] for r in loops} != expected_tags:
        raise ValueError("exact 1000 PERF roster required")
    by_loop = {r["id"]: r for r in loops}
    if len(by_loop) != 1000 or any(
        not r["is_active"]
        or not r["include_in_evaluation"]
        or r["status"] != "READY"
        or any(
            "synthetic" not in (r.get(k) or "").lower() or "非现场真实回路" not in (r.get(k) or "")
            for k in ("remark", "description")
        )
        for r in loops
    ):
        raise ValueError("synthetic markers/evaluation roster differs")
    if len(mappings) != 7000 or len(histories) != 7000 or len(points) != 7000:
        raise ValueError("exact 7000 mappings/history/points required")
    by_point = {r["id"]: r for r in points}
    map_pairs = {(r["loop_id"], r["tag_role"]): r["tag_id"] for r in mappings}
    history_pairs = {(r["loop_id"], r["tag_role"]): r["tag_id"] for r in histories}
    expected_pairs = {(key, role) for key in by_loop for role in ROLES}
    if (
        len(by_point) != 7000
        or set(map_pairs) != expected_pairs
        or set(history_pairs) != expected_pairs
        or map_pairs != history_pairs
        or len(set(map_pairs.values())) != 7000
        or len({r["id"] for r in mappings}) != 7000
        or len({r["id"] for r in histories}) != 7000
    ):
        raise ValueError("unique seven-role mapping/history/point identity differs")
    for (loop_id, role), point_id in map_pairs.items():
        point = by_point[point_id]
        if (
            point["tag_name"] != f"{by_loop[loop_id]['tag_name']}_{role}"
            or point["tag_type"] != role
            or "synthetic" not in point.get("tag_description", "").lower()
        ):
            raise ValueError("point identity/synthetic role differs")
    for row in histories:
        if (
            row["mapping_version"] != 1
            or row["valid_to"] is not None
            or row["basis"] != ("SYNTHETIC_FIXTURE" if prepared else "MVP_INIT")
            or (
                aware(row["valid_from"]) != effective
                if prepared
                else aware(row["valid_from"]) <= effective
            )
        ):
            raise ValueError("fresh original history/effective interval differs")


def plan(freeze, normal_path, extra_path, preflight, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    source = Path(freeze)
    tables = {p.stem: json.loads(p.read_text()) for p in (source / "pg-raw").glob("*.json")}
    if len(tables) != 25 or any(not re.fullmatch(r"[a-z_]+", name) for name in tables):
        raise ValueError("complete original 25-table freeze required")
    normal, extra = (
        json.loads(Path(normal_path).read_text()),
        json.loads(Path(extra_path).read_text()),
    )
    batches, additions = normal["batches"], extra["inputs"]
    if (
        len(batches) != 20
        or {b["batch"] for b in batches} != set(range(1, 21))
        or any(b.get("required") is not True or b.get("expected_loops") != 1000 for b in batches)
        or len(additions) != 4
        or {b["purpose"] for b in additions}
        != {"fault-recovery", "fault-persistent", "independent-cold", "independent-hot"}
        or any(b.get("formal20Denominator") is not False for b in additions)
    ):
        raise ValueError("original normal20 + separate extra4 required")
    windows = sorted((aware(b["ts_start"]), aware(b["ts_end"])) for b in batches + additions)
    if (
        len(set(windows)) != 24
        or any(b - a != timedelta(hours=1) for a, b in windows)
        or any(windows[i][1] != windows[i + 1][0] for i in range(23))
    ):
        raise ValueError("24 distinct contiguous one-hour windows required")
    effective = windows[0][0]
    validate(tables, effective)
    observations = [
        json.loads(s) for s in Path(preflight).read_text().splitlines() if s.startswith("{")
    ]
    identities = [r for r in observations if r.get("section") in ("before", "after")]
    if len(identities) != 2 or any(
        r.get("database") != DATABASE
        or r.get("readOnly") != "on"
        or r.get("isolation") != "repeatable read"
        for r in identities
    ):
        raise ValueError("actual Root private RR RO identity missing")
    for section, table in (
        ("synthetic-roster", "loop_ledger"),
        ("bindings", "loop_tag_binding_history"),
    ):
        actual = [r for r in observations if r.get("section") == section]
        if len(actual) != 1 or canonical(actual[0]["rows"]) != canonical(tables[table]):
            raise ValueError("latest Root actual observation differs from original freeze")
    sql_plan = json.loads((source / "sql-plan.json").read_text())
    keys = sql_plan["sysConfigParameters"]
    if any(re.search("password|token|secret|url|api_key", k, re.I) for k in keys):
        raise ValueError("unexpected secret sys_config key")
    reference = out / "reference.json"
    dump(reference, tables)
    original = json.loads((source / "config-freeze.json").read_text())
    result = {
        "database": DATABASE,
        "effectiveStartUtc": effective.isoformat(),
        "latestEndUtc": windows[-1][1].isoformat(),
        "normalCount": 20,
        "extraCount": 4,
        "tableNames": sorted(tables),
        "referenceSha256": sha(reference),
        "originalFiles": [
            {"path": str(p.resolve()), "sha256": sha(p)}
            for p in sorted((source / "pg-raw").glob("*.json"))
        ],
        "inputFiles": [
            {"path": str(Path(p).resolve()), "sha256": sha(p)}
            for p in (normal_path, extra_path, preflight)
        ],
        "sysKeys": keys,
        "oldConfigDigest": original["configDigest"],
        "oldFreeze": str((source / "config-freeze.json").resolve()),
        "oldFreezeSha256": sha(source / "config-freeze.json"),
        "actualMutationPerformed": False,
    }
    dump(out / "plan.json", result)
    dump(
        out / "old-freeze-not-valid-for-formal.json",
        {
            "originalPreserved": True,
            "oldConfigDigest": original["configDigest"],
            "reason": (
                "7000 MVP_INIT intervals do not cover normal20/extra4 synthetic input windows"
            ),
            "state": "INVALID_FOR_FORMAL_EXECUTION",
            "replacement": (
                "new immutable freeze/digest required after actual INPUT preparation "
                "and independent readback"
            ),
        },
    )
    dump(
        out / "dump-plan.json",
        {
            "database": DATABASE,
            "endpoint": "loopback:17102",
            "format": "PGDMP custom",
            "before": "full independent RR READ ONLY 25-table + result-count export before pg_dump",
            "command": "pg_dump --host=127.0.0.1 --port=17102 --username=clpm --dbname="
            + DATABASE
            + " --format=custom --no-owner --file=<own-out>/before.dump",
            "credentialSource": (
                "explicit native env file; password only in child process environment"
            ),
        },
    )
    return result


def load_plan(folder, expected_sha):
    folder = Path(folder)
    if sha(folder / "plan.json") != expected_sha:
        raise ValueError("reviewed plan SHA differs")
    p = json.loads((folder / "plan.json").read_text())
    if p["database"] != DATABASE or sha(folder / "reference.json") != p["referenceSha256"]:
        raise ValueError("reference identity differs")
    for item in p["originalFiles"] + p["inputFiles"]:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("immutable original/input changed")
    reference = json.loads((folder / "reference.json").read_text())
    validate(reference, aware(p["effectiveStartUtc"]))
    return p, reference


async def snapshot(conn, p, out, label, readonly):
    await conn.execute("SET LOCAL TIME ZONE 'UTC'")
    identity = json.loads(await conn.fetchval(IDENTITY_SQL))
    dump(out / label / "identity.json", identity)
    if (
        identity["database"] != DATABASE
        or identity["readOnly"] != ("on" if readonly else "off")
        or identity["isolation"] != ("repeatable read" if readonly else "serializable")
    ):
        raise ValueError("actual transaction/private database identity differs")
    rows = {}
    for table in p["tableNames"]:
        query = (
            "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),"
            f"'[]'::jsonb)::text FROM {table} t"
        )
        args = ()
        if table == "sys_config":
            query += " WHERE key=ANY($1::text[])"
            args = (p["sysKeys"],)
        raw = await conn.fetchval(query, *args)
        path = out / label / "raw" / (table + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw + "\n")
        rows[table] = json.loads(raw)
    counts = {name: await conn.fetchval(f"SELECT COUNT(*) FROM {name}") for name in RESULTS}
    dump(out / label / "result-counts.json", counts)
    if any(v != 0 for v in counts.values()):
        raise ValueError("business result/ledger/latest/snapshot is nonempty")
    return rows


def same(actual, expected):
    if set(actual) != set(expected) or any(
        canonical(actual[k]) != canonical(expected[k]) for k in expected
    ):
        raise ValueError("complete table rows differ from exact expected state")


def task_guard(observation):
    if (
        set(observation) != {"taskKeys", "brokerPendingOrUnacked", "celeryResultKeys"}
        or observation["taskKeys"] != []
        or observation["celeryResultKeys"] != []
        or type(observation["brokerPendingOrUnacked"]) is not int
        or observation["brokerPendingOrUnacked"] != 0
    ):
        raise ValueError("actual private task/broker/result observation is not empty")
    return observation


async def apply(p, reference, out, connect, backup, tasks):
    from uuid import UUID

    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    state = {
        "committed": False,
        "persistedReadbackVerified": False,
        "commitAttempted": False,
        "allowedColumns": ["valid_from", "basis"],
        "rows": 7000,
        "database": DATABASE,
    }

    def persist():
        dump(out / "state.json", state)

    expected = deepcopy(reference)
    for row in expected["loop_tag_binding_history"]:
        row.update(valid_from=p["effectiveStartUtc"], basis="SYNTHETIC_FIXTURE")
    persist()
    conn = None
    try:
        for item in p["originalFiles"] + p["inputFiles"]:
            if sha(item["path"]) != item["sha256"]:
                raise ValueError("original/input changed before execution")
        state["tasksBefore"] = task_guard(await tasks())
        persist()
        conn = await connect()
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            before = await snapshot(conn, p, out, "before", True)
            same(before, reference)
            validate(before, aware(p["effectiveStartUtc"]))
        await conn.close()
        conn = None
        state["backup"] = await backup(out)
        persist()
        conn = await connect()
        async with conn.transaction(isolation="serializable"):
            await conn.execute("SET LOCAL lock_timeout='5s'")
            await conn.execute(
                "LOCK TABLE "
                + ",".join(p["tableNames"] + list(RESULTS))
                + " IN SHARE ROW EXCLUSIVE MODE"
            )
            before = await snapshot(conn, p, out, "transaction-before", False)
            same(before, reference)
            validate(before, aware(p["effectiveStartUtc"]))
            state["tasksAtWrite"] = task_guard(await tasks())
            persist()
            parameters = [
                aware(p["effectiveStartUtc"]),
                [UUID(r["id"]) for r in reference["loop_tag_binding_history"]],
            ]
            status = await conn.execute(UPDATE_SQL, *parameters)
            state["update"] = {
                "sql": UPDATE_SQL,
                "effectiveStartUtc": p["effectiveStartUtc"],
                "ids": [str(x) for x in parameters[1]],
                "status": status,
            }
            persist()
            if status != "UPDATE 7000":
                raise ValueError("UPDATE did not affect exactly 7000 original histories")
            after = await snapshot(conn, p, out, "transaction-after", False)
            same(after, expected)
            validate(after, aware(p["effectiveStartUtc"]), prepared=True)
            state["commitAttempted"] = True
            persist()
        state["committed"] = True
        persist()
        await conn.close()
        conn = None
        conn = await connect()
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            after = await snapshot(conn, p, out, "persisted", True)
            same(after, expected)
            validate(after, aware(p["effectiveStartUtc"]), prepared=True)
        state["tasksAfter"] = task_guard(await tasks())
        state["persistedReadbackVerified"] = True
        persist()
        return state
    except BaseException as exc:
        if state["commitAttempted"] and state["committed"] is not True:
            state["committed"] = None
        state["error"] = {"type": type(exc).__name__, "message": str(exc)}
        persist()
        raise
    finally:
        if conn is not None:
            await conn.close()


async def execute(args):
    import asyncpg
    from dotenv import dotenv_values
    from redis.asyncio import Redis

    p, reference = load_plan(args.plan_dir, args.plan_sha256)
    cfg = dotenv_values(args.native_env_file)
    private_config(cfg)

    async def connect():
        return await asyncpg.connect(
            host=cfg["POSTGRES_HOST"],
            port=17102,
            user="clpm",
            password=cfg["POSTGRES_PASSWORD"],
            database=DATABASE,
            server_settings={
                "application_name": "F02-synthetic-input-preparation",
                "statement_timeout": "30000",
            },
        )

    async def tasks():
        from urllib.parse import urlunsplit

        result = {}
        for db in range(3):
            if cfg.get("REDIS_URL"):
                u = urlsplit(cfg["REDIS_URL"])
                client = Redis.from_url(
                    urlunsplit(u._replace(path=f"/{db}")), decode_responses=True
                )
            else:
                client = Redis(
                    host=cfg["REDIS_HOST"],
                    port=17123,
                    db=db,
                    username=cfg.get("REDIS_USERNAME") or None,
                    password=cfg.get("REDIS_PASSWORD") or None,
                    decode_responses=True,
                )
            try:
                if db == 0:
                    keys = [k async for k in client.scan_iter(match="task:*")]
                    result["taskKeys"] = keys
                    if keys:
                        raise ValueError("private Redis TaskRecord/index keys already exist")
                elif db == 1:
                    pending = 0
                    async for key in client.scan_iter():
                        kind = await client.type(key)
                        if kind == "list":
                            pending += await client.llen(key)
                        elif key == "unacked" and kind == "hash":
                            pending += await client.hlen(key)
                    result["brokerPendingOrUnacked"] = pending
                    if pending:
                        raise ValueError("private Redis broker is not empty")
                else:
                    keys = [k async for k in client.scan_iter(match="celery-task-meta-*")]
                    result["celeryResultKeys"] = keys
                    if keys:
                        raise ValueError("private Redis Celery results already exist")
            finally:
                await client.aclose()
        return result

    async def backup(out):
        path = out / "before.dump"
        argv = [
            args.pg_dump,
            "--host=" + cfg["POSTGRES_HOST"],
            "--port=17102",
            "--username=clpm",
            "--dbname=" + DATABASE,
            "--format=custom",
            "--no-owner",
            "--file=" + str(path),
        ]
        with (
            (out / "pg-dump.stdout.log").open("xb") as stdout,
            (out / "pg-dump.stderr.log").open("xb") as stderr,
        ):
            proc = subprocess.run(
                argv,
                env={**os.environ, "PGPASSWORD": cfg["POSTGRES_PASSWORD"]},
                stdout=stdout,
                stderr=stderr,
            )
        result = {"argv": argv, "rc": proc.returncode, "nativeEnvSha256": sha(args.native_env_file)}
        dump(out / "pg-dump-command.json", result)
        if proc.returncode or not path.is_file() or path.read_bytes()[:5] != b"PGDMP":
            raise ValueError("full original pg_dump failed")
        return {**result, "fileSha256": sha(path), "bytes": path.stat().st_size}

    if not args.keeper_ack_no_live_capacity:
        raise ValueError("Keeper must independently stop/record capacity API/Worker/Beat first")
    return await apply(p, reference, args.out, connect, backup, tasks)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    prep = sub.add_parser("plan")
    for key in ("freeze", "normal-plan", "extra-plan", "preflight", "out"):
        prep.add_argument("--" + key, required=True)
    run = sub.add_parser("apply")
    for key in ("plan-dir", "plan-sha256", "native-env-file", "out", "pg-dump"):
        run.add_argument("--" + key, required=True)
    run.add_argument("--keeper-ack-no-live-capacity", action="store_true")
    args = parser.parse_args()
    if args.mode == "plan":
        plan(args.freeze, args.normal_plan, args.extra_plan, args.preflight, args.out)
    else:
        asyncio.run(execute(args))


if __name__ == "__main__":
    main()
