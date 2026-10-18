"""Root Keeper executes phases explicitly; plan never contacts or starts services."""

from __future__ import annotations
import argparse, asyncio, base64, datetime, hashlib, http.server, json, mimetypes, os, re, shutil, signal, socket, subprocess, time
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def catalog(root):
    return {
        str(p.relative_to(root)): {
            "bytes": p.stat().st_size,
            "sha256": sha(p.read_bytes()),
        }
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def capture(label, argv, cwd=None, env=None, check=True):
    started = now()
    p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True)
    prefix = OUT / "commands" / f"{stamp()}-{label}"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for kind, b in [("stdout", p.stdout), ("stderr", p.stderr)]:
        prefix.with_suffix("." + kind + ".log").write_bytes(b)
    write(
        prefix.with_suffix(".command.json"),
        {
            "argv": list(map(str, argv)),
            "cwd": str(cwd) if cwd else None,
            "startedAt": started,
            "finishedAt": now(),
            "rc": p.returncode,
            "stdoutSha256": sha(p.stdout),
            "stderrSha256": sha(p.stderr),
            "environmentReference": "environment-safe.json; credentials never logged",
        },
    )
    if check and p.returncode:
        raise RuntimeError(f"{label} rc={p.returncode}; see {prefix}")
    return p


def fixed_checkout():
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=CHECKOUT, text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=CHECKOUT,
        text=True,
    )
    if head != ARGS.expected_sha or dirty:
        raise RuntimeError(
            f"fixed clean checkout required: head={head}, expected={ARGS.expected_sha}, dirty={dirty!r}"
        )
    return head


def native_credentials():
    from dotenv import dotenv_values

    return dotenv_values(ARGS.native_env_file)


def environment():
    native = native_credentials()
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(
            (
                "POSTGRES_",
                "REDIS_",
                "TDENGINE_",
                "GITHUB_",
                "LOCAL_RUN_",
                "CELERY_",
                "CLPM_PARENT_PID",
            )
        ):
            env.pop(key)
    env.update(
        {
            "E2E_EXECUTION_ENV": "disposable-local",
            "LOCAL_RUN_ID": ARGS.run_id,
            "LOCAL_RUN_ATTEMPT": "1",
            "CLPM_BUILD_CODE_SHA": ARGS.expected_sha,
            "ENV": "development",
            "CLPM_TEST_MODE": "0",
            "PYTHONPATH": str(CHECKOUT / "backend"),
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "17102",
            "POSTGRES_USER": native.get("POSTGRES_USER", "clpm"),
            "POSTGRES_PASSWORD": native["POSTGRES_PASSWORD"],
            "POSTGRES_DB": "clpm_critical_20261017_local",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "17124",
            "REDIS_DB": "0",
            "REDIS_PASSWORD": "",
            "CELERY_BROKER_URL": "redis://127.0.0.1:17124/1",
            "CELERY_RESULT_BACKEND": "redis://127.0.0.1:17124/2",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
            "CLPM_SKIP_EXIT_HOOKS": "0",
            "CLPM_G5_TRACE": "0",
            "TDENGINE_HOST": "127.0.0.1",
            "TDENGINE_PORT": "17104",
            "TDENGINE_USER": native.get("TDENGINE_USER", "root"),
            "TDENGINE_PASSWORD": native.get("TDENGINE_PASSWORD", "taosdata"),
            "TDENGINE_DB": "clpm_critical_20261017_local_ts",
            "CORS_ORIGINS": '["http://127.0.0.1:17266"]',
            "JWT_SECRET_KEY": "local-f03-test-secret-20261017-at-least-32-characters",
            "E2E_BASE_URL": "http://127.0.0.1:17266",
            "E2E_API_BASE_URL": "http://127.0.0.1:17261/api/v1",
            "E2E_CRITICAL_FIXTURE": str(OUT / "fixture.json"),
            "E2E_ARTIFACT_DIR": str(OUT / "gate"),
            "CLPM_CALC_SNAPSHOT_DIR": str(OUT / "calc-snapshots"),
            "VITE_GLOB_API_URL": "http://127.0.0.1:17261/api/v1",
            "VITE_ROUTER_HISTORY": "history",
        }
    )
    for key in [
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ]:
        env[key] = "1"
    return env


def save_state(state):
    state["at"] = now()
    state["checkout"] = str(CHECKOUT)
    state["sourceSha"] = ARGS.expected_sha
    state["localRunId"] = ARGS.run_id
    write(OUT / "history" / f"{stamp()}.json", state)
    write(OUT / "current-f03-local-api.json", state)


def get_state():
    path = OUT / "current-f03-local-api.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    if state and (
        state.get("sourceSha") != ARGS.expected_sha
        or state.get("localRunId") != ARGS.run_id
    ):
        raise RuntimeError("state belongs to another identity")
    return state


def http_get(url):
    with urlopen(url, timeout=5) as response:
        return response.read()


def td_sql(sql):
    token = base64.b64encode(
        f"{ENV['TDENGINE_USER']}:{ENV['TDENGINE_PASSWORD']}".encode()
    ).decode()
    request = Request(
        "http://127.0.0.1:17115/rest/sql",
        data=sql.encode(),
        headers={"Authorization": "Basic " + token},
    )
    with urlopen(request, timeout=30) as response:
        body = json.load(response)
    write(OUT / "td-commands" / f"{stamp()}.json", {"sql": sql, "response": body})
    if body.get("code") != 0:
        raise RuntimeError(f"TDengine command rejected: {body}")
    return body


async def pg_connect(database):
    import asyncpg

    return await asyncpg.connect(
        host="127.0.0.1",
        port=17102,
        user=ENV["POSTGRES_USER"],
        password=ENV["POSTGRES_PASSWORD"],
        database=database,
    )


async def verify_empty_resources():
    import redis

    connection = await pg_connect(ENV["POSTGRES_DB"])
    try:
        identity = await connection.fetchval("SELECT current_database()")
        tables = await connection.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"
        )
        if identity != ENV["POSTGRES_DB"] or tables:
            raise RuntimeError("expected existing empty private PG database")
    finally:
        await connection.close()
    client = redis.Redis(host="127.0.0.1", port=17124, db=0)
    info = client.info()
    sizes = {
        str(db): redis.Redis(host="127.0.0.1", port=17124, db=db).dbsize()
        for db in (0, 1, 2)
    }
    if info["role"] != "master" or any(sizes.values()):
        raise RuntimeError("expected existing empty dedicated Redis master")
    inspected = json.loads(
        capture(
            "redis-owner", ["docker", "inspect", "clpm-f03-redis-20261017-local"]
        ).stdout
    )[0]
    if (
        inspected["Config"]["Labels"].get("clpm.audit.owner")
        != "final-remediation-f03-local-20261017"
    ):
        raise RuntimeError("wrong Redis owner")
    if inspected["NetworkSettings"]["Ports"]["6379/tcp"] != [
        {"HostIp": "127.0.0.1", "HostPort": "17124"}
    ]:
        raise RuntimeError("wrong Redis binding")
    write(
        OUT / "empty-resource-check.json",
        {
            "at": now(),
            "database": identity,
            "publicTables": tables,
            "redisDbsize": sizes,
            "redisRole": info["role"],
            "redisRunId": info["run_id"],
            "redisContainerId": inspected["Id"],
        },
    )


async def capacity_observation(label):
    native = native_credentials()
    database = native.get("POSTGRES_DB")
    if database != "clpm_capacity_20261017_s01":
        raise RuntimeError(
            "unexpected capacity baseline identity; refuse observation of another DB"
        )
    connection = await pg_connect(database)
    try:
        targets = [
            "kpi_snapshot_hourly",
            "loop_confidence_latest",
            "kpi_node_snapshot_hourly",
            "calculation_result_record",
        ]
        tables = [
            "loop_ledger",
            "tag_registry",
            "loop_tag_mapping",
            *targets,
            "kpi_snapshot_custom",
            "calculation_dataset_snapshot",
        ]
        observed = {}
        for table in tables:
            if not await connection.fetchval(
                "SELECT to_regclass($1)", f"public.{table}"
            ):
                raise RuntimeError("required capacity table missing: " + table)
            rows = await connection.fetch(
                f"SELECT row_to_json(t)::text AS row, to_jsonb(t)::text AS canonical FROM {table} t ORDER BY to_jsonb(t)::text"
            )
            data = (
                "\n".join(row["row"] for row in rows) + ("\n" if rows else "")
            ).encode()
            path = OUT / "capacity-raw" / label / (table + ".ndjson")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            canonical = (
                "\n".join(row["canonical"] for row in rows) + ("\n" if rows else "")
            ).encode()
            path.with_suffix(".canonical.ndjson").write_bytes(canonical)
            observed[table] = {
                "count": len(rows),
                "bytes": len(data),
                "sha256": sha(data),
                "canonicalBytes": len(canonical),
                "canonicalSha256": sha(canonical),
            }
        if observed["loop_tag_mapping"]["count"] != 7000:
            raise RuntimeError("capacity binding denominator is not 7000")
        if any(observed[t]["count"] for t in targets):
            raise RuntimeError("capacity target results must remain zero")
        payload = {"database": database, "tables": observed, "writes": False}
        write(OUT / f"capacity-readonly-{label}.json", {"at": now(), **payload})
        if label != "before":
            before = json.loads((OUT / "capacity-readonly-before.json").read_text())
            before.pop("at", None)
            if before != payload:
                raise RuntimeError("capacity baseline raw content changed")
            write(
                OUT / f"capacity-equality-{label}.json",
                {
                    "at": now(),
                    "allRequiredTablesExist": True,
                    "rawRowsBytesAndHashesEqual": True,
                    "mappingCount": 7000,
                    "fourTargetTablesEmpty": True,
                },
            )
    finally:
        await connection.close()


def bootstrap():
    fixed_checkout()
    asyncio.run(verify_empty_resources())
    asyncio.run(capacity_observation("before"))
    capture(
        "schema-bootstrap",
        [PY, "-B", "scripts/apply_bootstrap_sql.py"],
        CHECKOUT / "backend",
        ENV,
    )
    capture(
        "alembic-stamp",
        [PY, "-B", "-m", "alembic", "stamp", "head"],
        CHECKOUT / "backend",
        ENV,
    )
    capture(
        "alembic-check", [PY, "-B", "-m", "alembic", "check"], CHECKOUT / "backend", ENV
    )
    databases = td_sql("SHOW DATABASES")
    if any(
        row[0] == "clpm_critical_20261017_local_ts" for row in databases.get("data", [])
    ):
        if td_sql("SHOW clpm_critical_20261017_local_ts.TABLES").get("data"):
            raise RuntimeError(
                "private TD namespace already contains tables; refuse reuse"
            )
    else:
        td_sql(
            "CREATE DATABASE clpm_critical_20261017_local_ts KEEP 365 DURATION 10 PRECISION 'ms'"
        )
    capture(
        "disable-external-before-lifespan",
        [
            PY,
            "-B",
            str(CHECKOUT / "e2e/scripts/prepare-critical-fixture.py"),
            "--configure-services",
            "--api-url",
            ENV["E2E_API_BASE_URL"],
            "--output",
            str(OUT / "service-config.json"),
        ],
        CHECKOUT / "backend",
        ENV,
    )
    state = get_state()
    state["bootstrapped"] = True
    save_state(state)


def build():
    fixed_checkout()
    capture(
        "frontend-production-build",
        ["pnpm", "-F", "@vben/web-antd", "build"],
        CHECKOUT / "frontend",
        ENV,
    )
    original = CHECKOUT / "frontend/apps/web-antd/dist"
    site = OUT / "site"
    if site.exists():
        raise RuntimeError(
            "site exists; choose a new output directory, never overwrite old deployment evidence"
        )
    before = catalog(original)
    shutil.copytree(original, site)
    config = site / "_app.config.js"
    raw = config.read_bytes()
    text = raw.decode()
    match = re.search(r'("VITE_GLOB_API_URL"\s*:\s*)("(?:\\.|[^"\\])*")', text)
    if not match:
        raise RuntimeError("external app config has no API transport key")
    updated = (
        text[: match.start(2)]
        + json.dumps(ENV["E2E_API_BASE_URL"])
        + text[match.end(2) :]
    )
    (OUT / "app-config.before.js").write_bytes(raw)
    config.write_text(updated)
    (OUT / "app-config.after.js").write_bytes(config.read_bytes())
    after = catalog(site)
    changed = [k for k in before if before[k] != after.get(k)]
    if set(before) != set(after) or changed != ["_app.config.js"]:
        raise RuntimeError(f"only external API config may differ: {changed}")

    def decoded(value):
        return json.JSONDecoder().raw_decode(
            value.split("window._VBEN_ADMIN_PRO_APP_CONF_=", 1)[1]
        )[0]

    old = decoded(text)
    new = decoded(updated)
    if {k for k in old if old[k] != new.get(k)} != {"VITE_GLOB_API_URL"} or set(
        old
    ) != set(new):
        raise RuntimeError("more than one deployment config key changed")
    write(OUT / "site.before-manifest.json", before)
    write(OUT / "site.after-manifest.json", after)
    write(
        OUT / "deployment-transport.json",
        {
            "sourceSha": ARGS.expected_sha,
            "changedFiles": changed,
            "changedKeys": ["VITE_GLOB_API_URL"],
            "oldApiUrl": old["VITE_GLOB_API_URL"],
            "newApiUrl": new["VITE_GLOB_API_URL"],
            "allOtherBundleBytesEqual": True,
            "gzipAndBrNeverNegotiated": True,
            "sourceDotenvChanged": False,
            "businessResponsesMocked": False,
        },
    )


def serve_site():
    site = (OUT / "site").resolve()
    allowed = set(catalog(site))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            route = unquote(urlsplit(self.path).path)
            relative = route.lstrip("/")
            if (
                relative in {"_app.config.js.gz", "_app.config.js.br"}
                or route.startswith("/api/")
                or ".." in Path(relative).parts
            ):
                self.send_error(404)
                return
            if relative in allowed:
                target = site / relative
            elif (
                route == "/"
                or route
                in {
                    "/auth/login",
                    "/config/loop",
                    "/loop/workbench360",
                    "/metric/loop-evaluation",
                }
                or re.fullmatch(r"/cockpit(?:/[a-z-]+)?", route)
            ):
                target = site / "index.html"
            else:
                self.send_error(404)
                return
            if not target.resolve().is_relative_to(site):
                self.send_error(404)
                return
            data = target.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                mimetypes.guess_type(str(target))[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    http.server.ThreadingHTTPServer(("127.0.0.1", 17266), Handler).serve_forever()


def process_details(pid):
    import psutil

    root = psutil.Process(pid)
    return [
        {
            "pid": p.pid,
            "ppid": p.ppid(),
            "created": p.create_time(),
            "status": p.status(),
            "argv": p.cmdline(),
        }
        for p in [root, *root.children(recursive=True)]
    ]


def owned_api(state):
    import psutil

    process = psutil.Process(state["apiPid"])
    if (
        abs(process.create_time() - state["apiCreated"]) > 0.01
        or "--port" not in process.cmdline()
        or "17261" not in process.cmdline()
    ):
        raise RuntimeError("API PID identity changed")
    return process


def supervisor_api():
    import psutil

    cwd = OUT / "runtime" / CHECKOUT.name / "backend"
    cwd.mkdir(parents=True, exist_ok=True)
    with (OUT / "backend-api.stdout.log").open("xb") as log:
        argv = [
            PY,
            "-B",
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "17261",
        ]
        process = subprocess.Popen(
            argv, cwd=cwd, env=ENV, stdout=log, stderr=subprocess.STDOUT
        )
        write(
            OUT / "api-launch.json",
            {
                "apiPid": process.pid,
                "apiCreated": psutil.Process(process.pid).create_time(),
                "apiArgv": argv,
                "apiCwd": str(cwd),
                "supervisorPid": os.getpid(),
            },
        )
        rc = process.wait()
        write(OUT / "api-exit.json", {"pid": process.pid, "rc": rc, "at": now()})
        raise SystemExit(rc)


def start():
    import psutil

    fixed_checkout()
    state = get_state()
    if not state.get("bootstrapped") or not (OUT / "site/index.html").exists():
        raise RuntimeError("bootstrap and build phases required")
    for port in (17261, 17266):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"port already owned: {port}")
    if (OUT / "api-launch.json").exists():
        raise RuntimeError("API launch metadata already exists; refuse reuse")
    launch = [
        PY,
        "-B",
        str(Path(__file__).resolve()),
        "supervisor-api",
        "--checkout",
        str(CHECKOUT),
        "--expected-sha",
        ARGS.expected_sha,
        "--run-id",
        ARGS.run_id,
        "--out",
        str(OUT),
        "--native-env-file",
        str(ARGS.native_env_file),
        "--python",
        PY,
    ]
    with (OUT / "api-supervisor.stdout.log").open("xb") as log:
        supervisor = subprocess.Popen(
            launch,
            env=ENV,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    for _ in range(20):
        if (OUT / "api-launch.json").exists():
            break
        if supervisor.poll() is not None:
            raise RuntimeError("supervisor exited before API launch")
        time.sleep(0.25)
    else:
        raise RuntimeError("API launch metadata not created")
    state.update(json.loads((OUT / "api-launch.json").read_text()))
    state.update(
        {
            "redisPort": 17124,
            "nativeDotenvSha256": sha((ARGS.native_env_file).read_bytes()),
        }
    )
    save_state(state)
    for _ in range(90):
        if supervisor.poll() is not None:
            raise RuntimeError("API exited; raw log retained")
        try:
            http_get("http://127.0.0.1:17261/health")
            break
        except Exception:
            time.sleep(1)
    else:
        raise RuntimeError("API readiness timed out")
    for _ in range(30):
        details = process_details(state["apiPid"])
        beats = [p for p in details if "beat" in p["argv"] and "celery" in p["argv"]]
        workers = [
            p
            for p in details
            if "worker" in p["argv"]
            and "celery" in p["argv"]
            and p["ppid"] == state["apiPid"]
        ]
        if len(beats) == 1 and len(workers) == 1:
            break
        time.sleep(1)
    else:
        raise RuntimeError("expected one lifespan Worker master and Beat")
    beat = beats[0]
    os.kill(beat["pid"], signal.SIGSTOP)
    state["beatPausedPid"] = beat["pid"]
    state["beatPausedCreated"] = beat["created"]
    state["processesAtStart"] = details
    save_state(state)
    front_log = (OUT / "frontend-static.stdout.log").open("xb")
    front = subprocess.Popen(
        [
            PY,
            "-B",
            str(Path(__file__).resolve()),
            "serve-site",
            "--checkout",
            str(CHECKOUT),
            "--expected-sha",
            ARGS.expected_sha,
            "--run-id",
            ARGS.run_id,
            "--out",
            str(OUT),
            "--native-env-file",
            str(ARGS.native_env_file),
            "--python",
            PY,
        ],
        stdout=front_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    front_log.close()
    state["frontendPid"] = front.pid
    state["frontendCreated"] = psutil.Process(front.pid).create_time()
    state["frontendPgid"] = os.getpgid(front.pid)
    save_state(state)
    for _ in range(20):
        try:
            served = http_get("http://127.0.0.1:17266/_app.config.js")
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("frontend readiness timed out")
    if served != (OUT / "site/_app.config.js").read_bytes():
        raise RuntimeError("served app config differs")
    write(
        OUT / "served-transport-check.json",
        {
            "at": now(),
            "bytes": len(served),
            "sha256": sha(served),
            "apiUrl": ENV["E2E_API_BASE_URL"],
            "noProxy": True,
        },
    )
    snapshot_status("started")


def queue_observation(label, require_worker=True):
    code = "import json,redis,sys\nfrom urllib.parse import urlsplit\nfrom app.tasks.celery_app import celery_app\nu=urlsplit(celery_app.conf.broker_url)\nassert u.hostname=='127.0.0.1' and u.port==17124 and u.path=='/1', 'wrong actual broker'\nb=urlsplit(celery_app.conf.result_backend)\nassert b.hostname=='127.0.0.1' and b.port==17124 and b.path=='/2', 'wrong actual result backend'\nif sys.argv[1]=='workers':\n    i=celery_app.control.inspect(timeout=5)\n    d={k:getattr(i,k)() for k in ['active','reserved','scheduled']}\nelse:\n    d={}\nr=redis.Redis.from_url(celery_app.conf.broker_url)\nd['actualBroker']={'host':u.hostname,'port':u.port,'db':u.path,'resultDb':b.path}\nd['redis']={'role':r.info('replication')['role'],'runId':r.info('server')['run_id'],'dbsize':r.dbsize(),'brokerLists':{k.decode():r.llen(k) for k in r.scan_iter() if r.type(k)==b'list' and (k.startswith(b'default') or k.startswith(b'dead_letter'))},'unacked':r.hlen('unacked'),'unackedIndex':r.zcard('unacked_index')}\nprint(json.dumps(d))\n"
    result = capture(
        "queue-" + label,
        [PY, "-B", "-c", code, "workers" if require_worker else "broker-only"],
        CHECKOUT / "backend",
        ENV,
    )
    data = json.loads(result.stdout.decode().strip().splitlines()[-1])
    write(OUT / f"queue-{label}.json", data)
    expected_master = json.loads((OUT / "empty-resource-check.json").read_text())[
        "redisRunId"
    ]
    if data["redis"]["runId"] != expected_master:
        raise RuntimeError("dedicated Redis master identity changed")
    return data


def snapshot_status(label):
    state = get_state()
    root = owned_api(state)
    details = process_details(root.pid)
    write(OUT / f"processes-{label}.json", {"at": now(), "rows": details})
    return queue_observation(label)


def seed():
    fixed_checkout()
    snapshot_status("before-seed")
    capture(
        "prepare-seven-role-input",
        [
            PY,
            "-B",
            str(CHECKOUT / "e2e/scripts/prepare-critical-fixture.py"),
            "--execute",
            "--api-url",
            ENV["E2E_API_BASE_URL"],
            "--output",
            str(OUT / "fixture.json"),
        ],
        CHECKOUT / "backend",
        ENV,
    )


def gate():
    fixed_checkout()
    snapshot_status("before-gate")
    capture(
        "four-real-business-cases",
        ["bash", "scripts/run-critical-e2e.sh"],
        CHECKOUT,
        ENV,
    )
    snapshot_status("after-gate")
    write(OUT / "input-package-catalog.json", catalog(OUT / "calc-snapshots"))
    asyncio.run(capacity_observation("after-gate"))


def status():
    snapshot_status("status-" + stamp())


def stop():
    state = get_state()
    root = owned_api(state)
    data = snapshot_status("before-stop")
    original_processes = process_details(root.pid)
    for kind in ("active", "reserved", "scheduled"):
        if not data.get(kind) or len(data[kind]) != 1 or any(data[kind].values()):
            raise RuntimeError("not drained: " + kind)
    if (
        data["redis"]["role"] != "master"
        or any(data["redis"]["brokerLists"].values())
        or data["redis"]["unacked"]
        or data["redis"]["unackedIndex"]
    ):
        raise RuntimeError("private broker is not empty")
    cwd = Path(state["apiCwd"])
    for path in cwd.rglob("*"):
        if path.is_file() and ("celerybeat" in path.name):
            dest = OUT / "beat-state-before-stop" / path.relative_to(cwd)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    import psutil

    beat = psutil.Process(state["beatPausedPid"])
    if (
        abs(beat.create_time() - state["beatPausedCreated"]) > 0.01
        or beat.ppid() != root.pid
        or "beat" not in beat.cmdline()
    ):
        raise RuntimeError("Beat PID/parent/create-time identity changed")
    write(
        OUT / "beat-stop-signals.json",
        {
            "pid": beat.pid,
            "parent": beat.ppid(),
            "createTime": beat.create_time(),
            "signalsInOrder": ["SIGTERM", "SIGCONT"],
            "at": now(),
        },
    )
    beat.send_signal(signal.SIGTERM)
    beat.send_signal(signal.SIGCONT)
    root.send_signal(signal.SIGTERM)
    try:
        root.wait(timeout=45)
    except Exception:
        root.send_signal(signal.SIGINT)
        root.wait(timeout=30)
    import psutil

    front = psutil.Process(state["frontendPid"])
    if (
        abs(front.create_time() - state["frontendCreated"]) > 0.01
        or os.getpgid(front.pid) != state["frontendPgid"]
    ):
        raise RuntimeError("frontend PID identity changed")
    os.killpg(state["frontendPgid"], signal.SIGTERM)
    try:
        front.wait(timeout=15)
    except psutil.TimeoutExpired:
        raise RuntimeError("frontend did not stop; no force kill attempted")
    for _ in range(20):
        if (OUT / "api-exit.json").exists():
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("API supervisor did not record its true exit code")
    actual_exit = json.loads((OUT / "api-exit.json").read_text())
    if actual_exit["rc"] != 0:
        raise RuntimeError(f"API real exit rc={actual_exit['rc']}")
    if sha((ARGS.native_env_file).read_bytes()) != state["nativeDotenvSha256"]:
        raise RuntimeError("native dotenv changed")
    remaining = []
    for _ in range(40):
        remaining = []
        for item in original_processes:
            try:
                process = psutil.Process(item["pid"])
                if abs(process.create_time() - item["created"]) < 0.01:
                    remaining.append({**item, "currentStatus": process.status()})
            except psutil.NoSuchProcess:
                pass
        if not remaining:
            break
        time.sleep(0.25)
    write(
        OUT / "stopped-process-check.json",
        {"original": original_processes, "remaining": remaining, "at": now()},
    )
    if remaining:
        raise RuntimeError(
            "owned API/Worker/pool/Beat process remains; no force kill attempted"
        )
    after_broker = queue_observation("after-stop", require_worker=False)
    if (
        any(after_broker["redis"]["brokerLists"].values())
        or after_broker["redis"]["unacked"]
        or after_broker["redis"]["unackedIndex"]
    ):
        raise RuntimeError("private broker is not quiescent after stop")
    state["stopped"] = True
    state["apiExitObservedAt"] = now()
    save_state(state)
    asyncio.run(capacity_observation("after-stop"))


def main():
    global ARGS, CHECKOUT, OUT, PY, ENV
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=[
            "plan",
            "bootstrap",
            "build",
            "start",
            "seed",
            "gate",
            "status",
            "stop",
            "serve-site",
            "supervisor-api",
        ],
    )
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--native-env-file", type=Path, required=True)
    parser.add_argument(
        "--python",
        default="/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python",
    )
    ARGS = parser.parse_args()
    CHECKOUT = ARGS.checkout.resolve()
    OUT = ARGS.out.resolve()
    PY = ARGS.python
    ARGS.native_env_file = ARGS.native_env_file.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", ARGS.expected_sha) or not re.fullmatch(
        r"\d{8,20}", ARGS.run_id
    ):
        raise RuntimeError("invalid fixed source/run identity")
    if (
        not OUT.is_relative_to(Path(__file__).resolve().parent.parent)
        or OUT.name != f"local-business-{ARGS.run_id}-{ARGS.expected_sha[:12]}"
    ):
        raise RuntimeError("output must be a fresh named f03 local-business scope")
    if ARGS.phase == "serve-site":
        serve_site()
        return
    os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
    os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
    head = fixed_checkout()
    ENV = environment()
    OUT.mkdir(parents=True, exist_ok=True)
    safe = {
        k: v
        for k, v in ENV.items()
        if k.startswith(
            (
                "POSTGRES_",
                "REDIS_",
                "TDENGINE_",
                "CELERY_",
                "NO_PROXY",
                "CLPM_SKIP_EXIT_HOOKS",
                "CLPM_G5_TRACE",
                "LOCAL_RUN_",
                "E2E_",
                "CLPM_BUILD_",
                "CLPM_CALC_",
                "CORS_",
                "OPENBLAS_",
                "OMP_",
                "MKL_",
                "NUMEXPR_",
                "VECLIB_",
            )
        )
        and not any(secret in k for secret in ("PASSWORD", "TOKEN", "SECRET"))
    }
    safe.update(
        {
            "sourceSha": head,
            "nativeDotenvSha256": sha((ARGS.native_env_file).read_bytes()),
            "writesNativeDotenv": False,
            "credentialSource": str(ARGS.native_env_file),
            "at": now(),
        }
    )
    write(OUT / "environment-safe.json", safe)
    if ARGS.phase == "plan":
        print(
            json.dumps(
                {
                    "readOnly": True,
                    "serviceStarts": False,
                    "sourceSha": head,
                    "phases": [
                        "bootstrap",
                        "build",
                        "start",
                        "seed",
                        "gate",
                        "status",
                        "stop",
                    ],
                    "environment": safe,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if ARGS.phase == "supervisor-api":
        supervisor_api()
        return
    globals()[ARGS.phase]()
    print(json.dumps({"phase": ARGS.phase, "rc": 0, "sourceSha": head, "at": now()}))


if __name__ == "__main__":
    main()
