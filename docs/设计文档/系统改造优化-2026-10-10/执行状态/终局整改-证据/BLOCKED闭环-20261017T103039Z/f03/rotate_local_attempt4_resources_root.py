"""Root-only rotation of the failed, dedicated F03 test scope; preserves its state."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
from types import SimpleNamespace
from urllib.request import Request, urlopen

import asyncpg
from dotenv import dotenv_values
import psutil

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "local-business-20261017215608-9f356782ef40"
ARCHIVE = ROOT / "local-attempt4-preserved-resources"
NATIVE = Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env")
PY = "/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python"
DB = "clpm_critical_20261017_local"
OLD_DB = "clpm_critical_20261017_local_attempt4"
REDIS = "clpm-f03-redis-20261017-local"
OLD_REDIS = REDIS + "-attempt4"
TD = DB + "_ts"
OWNER = "final-remediation-f03-local-20261017"


def now():
    return datetime.now(timezone.utc).isoformat()


def write(name, data):
    (ARCHIVE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def command(label, argv):
    start = now()
    p = subprocess.run(argv, capture_output=True)
    (ARCHIVE / (label + ".stdout")).write_bytes(p.stdout)
    (ARCHIVE / (label + ".stderr")).write_bytes(p.stderr)
    write(label + ".json", {"argv": argv, "startedAt": start, "finishedAt": now(),
                           "rc": p.returncode, "stdoutSha256": hashlib.sha256(p.stdout).hexdigest(),
                           "stderrSha256": hashlib.sha256(p.stderr).hexdigest()})
    if p.returncode:
        raise RuntimeError(f"{label} failed rc={p.returncode}")
    return p.stdout


def td(sql, label):
    credentials = f"{ENV.get('TDENGINE_USER', 'root')}:{ENV['TDENGINE_PASSWORD']}"
    request = Request("http://127.0.0.1:17115/rest/sql", data=sql.encode(),
                      headers={"Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()})
    started = now()
    with urlopen(request, timeout=60) as response:
        raw = response.read()
        status = response.status
    (ARCHIVE / (label + ".body.json")).write_bytes(raw)
    body = json.loads(raw)
    write(label + ".json", {"sql": sql, "startedAt": started, "finishedAt": now(),
                           "httpStatus": status, "code": body.get("code"), "rows": body.get("rows"),
                           "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    if status != 200 or body.get("code") != 0:
        raise RuntimeError("dedicated TD command rejected: " + label)
    return body


async def pg_rotate():
    connection = await asyncpg.connect(host="127.0.0.1", port=17102, user=ENV["POSTGRES_USER"],
                                      password=ENV["POSTGRES_PASSWORD"], database="postgres")
    try:
        activity = [dict(x) for x in await connection.fetch(
            "SELECT pid,datname,state,application_name FROM pg_stat_activity WHERE datname=$1", DB)]
        write("pg-active-before-rename.json", activity)
        if activity or await connection.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", OLD_DB):
            raise RuntimeError("dedicated database still in use or archive name exists")
        owner = ENV["POSTGRES_USER"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", owner):
            raise RuntimeError("unexpected owner identifier")
        for label, sql in [("preserve-old-pg", f'ALTER DATABASE "{DB}" RENAME TO "{OLD_DB}"'),
                           ("create-fresh-pg", f'CREATE DATABASE "{DB}" OWNER "{owner}" TEMPLATE template0')]:
            start = now()
            result = await connection.execute(sql)
            write(label + ".json", {"sql": sql, "result": result, "startedAt": start, "finishedAt": now()})
    finally:
        await connection.close()


if __name__ == "__main__":
    ARCHIVE.mkdir(exist_ok=False)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    ENV = dotenv_values(NATIVE)
    if hashlib.sha256(NATIVE.read_bytes()).hexdigest() != "27d6fc10cc68376b056d1c7803f1b6361e48c11bc3de46c4e8a46cc4b2abe04b":
        raise RuntimeError("native capacity environment changed")
    state = json.loads((OUT / "current-f03-local-api.json").read_text())
    actual_exit = json.loads((OUT / "api-exit.json").read_text())
    remaining = []
    for item in state["processesAtStart"] + [{"pid": state["frontendPid"], "created": state["frontendCreated"]}]:
        try:
            process = psutil.Process(item["pid"])
            if abs(process.create_time() - item["created"]) < .01:
                remaining.append(item)
        except psutil.NoSuchProcess:
            pass
    native = subprocess.run(["ps", "-ax", "-o", "pid=,ppid=,lstart=,stat=,command="], capture_output=True, text=True)
    selected = [line for line in native.stdout.splitlines()
                if PY in line and (" -m uvicorn " in line or " -m celery " in line)]
    write("actual-post-stop.json", {"at": now(), "actualApiExit": actual_exit,
                                  "originalMembersStillPresent": remaining, "selectedRawPsLines": selected,
                                  "psRc": native.returncode, "driverStopPassed": False,
                                  "scope": "actual cleanup closure; original stop rc1 AccessDenied during API exit retained; native post-stop separately confirmed all gone"})
    if remaining or selected or native.returncode:
        raise RuntimeError("a product process remains; refuse resource rotation")
    for port in (17261, 17266):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("owned local F03 port remains open")
    spec = importlib.util.spec_from_file_location("local_driver", ROOT / "local-lifespan-v4/keeper-local-f03-v4.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    driver.ARGS = SimpleNamespace(native_env_file=NATIVE, expected_sha=state["sourceSha"], run_id=state["localRunId"])
    driver.CHECKOUT = Path(state["checkout"])
    driver.OUT = OUT
    driver.PY = PY
    driver.ENV = driver.environment()
    broker = driver.queue_observation("after-stop-root-readonly", require_worker=False)
    if broker["redis"]["unacked"] or broker["redis"]["unackedIndex"]:
        raise RuntimeError("private broker still has in-flight deliveries")
    write("old-broker-quarantine-plan.json", {
        "at": now(), "actualBrokerSnapshot": broker,
        "originalBrokerEmpty": not any(broker["redis"]["brokerLists"].values()),
        "treatment": "preserve complete RDB and stop/rename old container with pending messages; never purge or consume them",
        "originalRunPassed": False,
    })
    asyncio.run(driver.capacity_observation("after-root-stop"))
    inspected = json.loads(command("redis-inspect-before", ["docker", "inspect", REDIS]))[0]
    if inspected["Config"]["Labels"].get("clpm.audit.owner") != OWNER or not inspected["Id"].startswith("77939e08d878e969e33f46c9bdbf1210a15bfe7f584411bec3b5f795442b2649"):
        raise RuntimeError("dedicated original Redis ownership mismatch")
    # Preserve all failed-run input rows before any test-scope data removal.
    tables = td(f"SHOW {TD}.TABLES", "td-original-tables")["data"]
    raw_inputs = json.loads((OUT / "fixture.inputs.json").read_text())
    expected_tables = {"p_" + role["pointId"].replace("-", "") for role in raw_inputs["roles"].values()}
    if len(expected_tables) != 7 or not tables or len(tables) > 7 or not {row[0] for row in tables}.issubset(expected_tables):
        raise RuntimeError("only present tables of the exact seven original synthetic points may rotate")
    preserved_rows = []
    for index, entry in enumerate(tables):
        table = entry[0]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise RuntimeError("unexpected TD identifier")
        body = td(f"SELECT * FROM `{TD}`.`{table}` ORDER BY ts", f"td-input-{index:02d}-all-original-rows")
        preserved_rows.append({"table": table, "rows": body["rows"]})
    command("pg-original-dump", ["docker", "exec", "clpm-mvp-postgres", "pg_dump", "-Fc", "-U", ENV["POSTGRES_USER"], DB])
    command("redis-original-save", ["docker", "exec", REDIS, "redis-cli", "SAVE"])
    command("redis-original-rdb", ["docker", "cp", f"{REDIS}:/data/dump.rdb", str(ARCHIVE / "redis-original.rdb")])
    # Old PostgreSQL and Redis remain recoverable, under explicit archive names.
    asyncio.run(pg_rotate())
    command("redis-stop-original", ["docker", "stop", "-t", "30", inspected["Id"]])
    command("redis-preserve-original-name", ["docker", "rename", inspected["Id"], OLD_REDIS])
    command("redis-create-fresh", ["docker", "run", "-d", "--name", REDIS,
        "--label", "clpm.audit.owner=" + OWNER, "--memory", "512m", "--cpus", "0.5",
        "-p", "127.0.0.1:17124:6379", "redis:7-alpine", "redis-server",
        "--maxmemory", "384mb", "--maxmemory-policy", "noeviction", "--appendonly", "no", "--save", ""])
    # Only the seven archived rows' tables are dropped; namespace and stable remain.
    for index, entry in enumerate(tables):
        td(f"DROP TABLE `{TD}`.`{entry[0]}`", f"td-rotate-owned-table-{index:02d}")
    if td(f"SHOW {TD}.TABLES", "td-after-rotation")["data"]:
        raise RuntimeError("dedicated TD namespace not empty")
    write("complete.json", {"at": now(), "oldPostgres": OLD_DB, "freshPostgres": DB,
                            "oldRedis": OLD_REDIS, "freshRedis": REDIS,
                            "tdInputTablesPreserved": len(tables), "tdAllOriginalRowCounts": preserved_rows,
                            "sourceShaUnchanged": state["sourceSha"],
                            "originalGatePassed": False, "originalStopRc": 1, "originalApiRc": actual_exit["rc"]})
    print(json.dumps({"resourceRotationRc": 0, "archive": str(ARCHIVE)}))
