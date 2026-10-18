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
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            metadata_sql = "SELECT row_to_json(x)::text FROM (SELECT current_database() AS database_name, current_setting('transaction_read_only') AS read_only, current_setting('transaction_isolation') AS isolation, txid_current() AS txid, txid_current_snapshot()::text AS snapshot, clock_timestamp() AT TIME ZONE 'UTC' AS observed_utc) x"
            raw_identity = await connection.fetchval(metadata_sql)
            identity = json.loads(raw_identity)
            if (
                identity["database_name"] != database
                or identity["read_only"] != "on"
                or identity["isolation"] != "repeatable read"
            ):
                raise RuntimeError(
                    "capacity observation is not the required actual RR READ ONLY transaction"
                )
            write(
                OUT / f"capacity-transaction-{label}.json",
                {
                    "statement": metadata_sql,
                    "rawRow": raw_identity,
                    "parsed": identity,
                    "guarantee": "one independent RR READ ONLY transaction per observation; before and after are distinct",
                },
            )
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
        protocol_version = "HTTP/1.1"

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

    class StaticServer(http.server.ThreadingHTTPServer):
        request_queue_size = 256
        daemon_threads = True

    StaticServer(("127.0.0.1", 17266), Handler).serve_forever()


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
            "--timeout-graceful-shutdown",
            "15",
            "--no-access-log",
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


def startup_issues(state, details, evidence):
    """Pure identity/readiness checks; no product imports or side effects."""
    errors = []
    api = state["apiPid"]
    beats = [p for p in details if p["ppid"] == api and "celery" in p["argv"] and "beat" in p["argv"]]
    masters = [p for p in details if p["ppid"] == api and "celery" in p["argv"] and "worker" in p["argv"]]
    if len(details) != 7 or len({p["pid"] for p in details}) != 7 or len(beats) != 1 or len(masters) != 1:
        return ["exactly API, one Beat, one Worker master and four pools required"]
    pools = [p for p in details if p["ppid"] == masters[0]["pid"] and p["argv"] == masters[0]["argv"]]
    if len(pools) != 4 or {p["pid"] for p in details} != {api, beats[0]["pid"], masters[0]["pid"], *(p["pid"] for p in pools)}:
        errors.append("four pools must belong only to the actual master")
    if evidence["pidfile"].strip() != str(beats[0]["pid"]):
        errors.append("actual Beat pidfile must identify this child")
    if "beat: Starting..." not in evidence["beat"] or f"[beat] 父进程看门狗已启动（监视 PID={api}）" not in evidence["beat"]:
        errors.append("Beat startup and beat_init parent watcher evidence required")
    if " ready." not in evidence["worker"] or f"[worker] 父进程看门狗已启动（监视 PID={api}）" not in evidence["worker"]:
        errors.append("actual Worker ready and parent watcher evidence required")
    for role, title, expected in [("Beat", "调度", beats[0]["pid"]), ("Worker", "", masters[0]["pid"])]:
        pattern = rf"Celery {role} {title}进程已启动 \(PID=(\d+)\)"
        if re.findall(pattern, evidence["api"]) != [str(expected)]:
            errors.append(role + " must have one original lifespan start without respawn")
    return errors


def startup_evidence(state):
    base = Path(state["apiCwd"]) / "logs"
    paths = {"pidfile": base / "celerybeat.pid", "beat": base / "celery-beat.log", "worker": base / "celery-worker.log", "api": OUT / "backend-api.stdout.log"}
    return {name: path.read_text() if path.exists() else "" for name, path in paths.items()}


def same_process_identities(first, second):
    def keys(rows):
        return sorted((p["pid"], p["ppid"], p["created"], tuple(p["argv"])) for p in rows)
    return keys(first) == keys(second)


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
    for _ in range(90):
        details = process_details(state["apiPid"])
        evidence = startup_evidence(state)
        issues = startup_issues(state, details, evidence)
        if not issues:
            break
        if supervisor.poll() is not None:
            raise RuntimeError("API exited before complete readiness")
        time.sleep(1)
    else:
        write(OUT / "startup-readiness-refused.json", {"at": now(), "issues": issues, "rows": details})
        raise RuntimeError("complete seven-process readiness absent; " + "; ".join(issues))
    beat = next(p for p in details if p["ppid"] == state["apiPid"] and "beat" in p["argv"])
    for name, raw in evidence.items():
        (OUT / ("startup-" + name + ".raw.log")).write_text(raw)
    write(OUT / "startup-ready-before-pause.json", {"at": now(), "rows": details, "evidence": {name: {"sha256": sha(raw.encode()), "bytes": len(raw.encode())} for name, raw in evidence.items()}})
    live_beat = psutil.Process(beat["pid"])
    if abs(live_beat.create_time() - beat["created"]) > .01 or live_beat.ppid() != state["apiPid"] or live_beat.cmdline() != beat["argv"]:
        raise RuntimeError("Beat identity changed before controlled pause")
    live_beat.send_signal(signal.SIGSTOP)
    state["beatPausedPid"] = beat["pid"]
    state["beatPausedCreated"] = beat["created"]
    time.sleep(2)
    paused = process_details(state["apiPid"])
    if not same_process_identities(details, paused) or startup_issues(state, paused, startup_evidence(state)):
        raise RuntimeError("process ownership changed during startup pause")
    if next(p for p in paused if p["pid"] == beat["pid"])["status"] != psutil.STATUS_STOPPED:
        raise RuntimeError("actual Beat did not remain stopped")
    state["processesAtStart"] = paused
    state["startupReadinessVerified"] = True
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
    deadline = time.monotonic() + 90
    while True:
        observed = snapshot_status("started-" + stamp())
        quiet = all(observed.get(kind) and len(observed[kind]) == 1 and not any(observed[kind].values()) for kind in ("active", "reserved", "scheduled"))
        quiet = quiet and not any(observed["redis"]["brokerLists"].values()) and not observed["redis"]["unacked"] and not observed["redis"]["unackedIndex"]
        if quiet:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("startup tasks did not drain; no input seed allowed")
        time.sleep(1)


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
    if not state.get("startupReadinessVerified") or not same_process_identities(state["processesAtStart"], details):
        raise RuntimeError("frozen seven-process identities changed")
    if startup_issues(state, details, startup_evidence(state)) or next(p for p in details if p["pid"] == state["beatPausedPid"])["status"] != "stopped":
        raise RuntimeError("actual Beat readiness/paused identity changed")
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
    import psutil

    state = get_state()
    errors = []
    observed = {}
    history = OUT / "process-tree-stop-history.ndjson"
    root = None
    barrier = None
    beat_released = False

    def observe_tree():
        rows, vanished = [], []
        try:
            processes = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            processes = []
        for process in processes:
            try:
                item = {
                    "pid": process.pid,
                    "ppid": process.ppid(),
                    "created": process.create_time(),
                    "status": process.status(),
                    "argv": process.cmdline(),
                }
                rows.append(item)
                observed[(item["pid"], item["created"])] = item
            except psutil.NoSuchProcess:
                vanished.append(process.pid)
        with history.open("a") as stream:
            stream.write(
                json.dumps({"at": now(), "rows": rows, "vanishedDuringRead": vanished})
                + "\n"
            )
        return rows

    def read_barrier(offset, signal_at):
        waiting_path = OUT / "lifespan-waiting.json"
        waiting = (
            json.loads(waiting_path.read_text()) if waiting_path.exists() else None
        )
        if waiting and waiting.get("signalAt") != signal_at.isoformat():
            waiting = None
        with (OUT / "backend-api.stdout.log").open("rb") as log:
            log.seek(offset)
            while True:
                begin = log.tell()
                raw = log.readline()
                if not raw.endswith(b"\n"):
                    return begin, None
                if raw == b"INFO:     Waiting for application shutdown.\n":
                    (OUT / "lifespan-waiting.raw.log").write_bytes(raw)
                    waiting = {
                        "offsetStart": begin,
                        "offsetEnd": log.tell(),
                        "rawSha256": sha(raw),
                        "signalAt": signal_at.isoformat(),
                        "observedAt": now(),
                    }
                    write(waiting_path, waiting)
                    continue
                try:
                    record = json.loads(raw)
                    timestamp = datetime.datetime.fromisoformat(record["timestamp"])
                except (ValueError, KeyError, TypeError):
                    continue
                if (
                    waiting is not None
                    and waiting["offsetEnd"] <= begin
                    and record.get("logger") == "app.main"
                    and record.get("message")
                    in {"停止 Celery Worker 进程...", "停止 Celery Beat 调度进程..."}
                    and timestamp.tzinfo is not None
                    and timestamp >= signal_at
                ):
                    (OUT / "watchdog-stop-barrier.raw.log").write_bytes(raw)
                    item = {
                        "offsetStart": begin,
                        "offsetEnd": log.tell(),
                        "rawSha256": sha(raw),
                        "record": record,
                        "uvicornWaitingForApplicationShutdown": waiting,
                        "observedAt": now(),
                    }
                    write(OUT / "watchdog-stop-barrier.json", item)
                    return log.tell(), item

    def clean_shutdown_chain(expected_pid, start_offset):
        expected = [
            b"INFO:     Waiting for application shutdown.\n",
            b"INFO:     Application shutdown complete.\n",
            f"INFO:     Finished server process [{expected_pid}]\n".encode(),
        ]
        events = []
        with (OUT / "backend-api.stdout.log").open("rb") as log:
            log.seek(start_offset)
            while len(events) < len(expected):
                begin = log.tell()
                raw = log.readline()
                if not raw:
                    return None
                if raw == expected[len(events)]:
                    name = f"uvicorn-clean-shutdown-{len(events)}.raw.log"
                    (OUT / name).write_bytes(raw)
                    events.append(
                        {
                            "file": name,
                            "offsetStart": begin,
                            "offsetEnd": log.tell(),
                            "rawSha256": sha(raw),
                        }
                    )
        item = {
            "pid": expected_pid,
            "logOffsetAfterSignal": start_offset,
            "eventsInOrder": events,
            "observedAt": now(),
        }
        write(OUT / "uvicorn-clean-shutdown-chain.json", item)
        return item

    try:
        root = owned_api(state)
        data = snapshot_status("before-stop")
        for item in process_details(root.pid):
            observed[(item["pid"], item["created"])] = item
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
            if path.is_file() and "celerybeat" in path.name:
                dest = OUT / "beat-state-before-stop" / path.relative_to(cwd)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
        beat = psutil.Process(state["beatPausedPid"])

        def owned_paused_beat():
            if (
                abs(beat.create_time() - state["beatPausedCreated"]) > 0.01
                or beat.ppid() != root.pid
                or "beat" not in beat.cmdline()
                or beat.status() != psutil.STATUS_STOPPED
            ):
                raise RuntimeError("paused Beat PID/parent/create-time/status changed")
            return beat

        owned_paused_beat()
        offset = (OUT / "backend-api.stdout.log").stat().st_size
        signal_at = datetime.datetime.now(datetime.timezone.utc)
        write(
            OUT / "api-stop-signal.json",
            {
                "pid": root.pid,
                "signal": "SIGTERM",
                "at": signal_at.isoformat(),
                "logOffsetBeforeSignal": offset,
                "beatRemainsPaused": True,
            },
        )
        root.send_signal(signal.SIGTERM)
        fallback = False
        deadline = time.monotonic() + 75
        while True:
            rows = observe_tree()
            if not barrier:
                offset, barrier = read_barrier(offset, signal_at)
                if barrier:
                    owned_paused_beat()
                    write(
                        OUT / "beat-stop-signals.json",
                        {
                            "pid": beat.pid,
                            "parent": beat.ppid(),
                            "createTime": beat.create_time(),
                            "signalsInOrder": ["SIGTERM", "SIGCONT"],
                            "at": now(),
                            "watchdogBarrier": barrier,
                        },
                    )
                    beat.send_signal(signal.SIGTERM)
                    beat.send_signal(signal.SIGCONT)
                    beat_released = True
            if not any(
                row["pid"] == root.pid and row["status"] != psutil.STATUS_ZOMBIE
                for row in rows
            ):
                break
            if time.monotonic() >= deadline:
                if fallback:
                    raise RuntimeError(
                        "API still alive after 75s TERM and 30s SIGINT; no force kill"
                    )
                owned_api(state).send_signal(signal.SIGINT)
                write(
                    OUT / "api-shutdown-fallback.json",
                    {"signal": "SIGINT", "afterSeconds": 75, "at": now()},
                )
                fallback = True
                deadline = time.monotonic() + 30
            time.sleep(0.1)
        if not barrier or not beat_released:
            raise RuntimeError(
                "missing post-signal watchdog stop barrier; Beat was not resumed"
            )
        for _ in range(20):
            if (OUT / "api-exit.json").exists():
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("API supervisor did not record its true exit code")
        actual_exit = json.loads((OUT / "api-exit.json").read_text())
        requested = json.loads((OUT / "api-stop-signal.json").read_text())
        chain = clean_shutdown_chain(
            state["apiPid"], requested["logOffsetBeforeSignal"]
        )
        if (
            actual_exit["pid"] != state["apiPid"]
            or actual_exit["rc"] not in {0, -signal.SIGTERM}
            or requested["pid"] != state["apiPid"]
            or requested["signal"] != "SIGTERM"
            or fallback
            or (OUT / "api-shutdown-fallback.json").exists()
            or chain is None
        ):
            raise RuntimeError(
                f"API real exit identity/rc or clean TERM chain invalid: {actual_exit}"
            )
        write(
            OUT / "api-exit-acceptance.json",
            {
                "actualExit": actual_exit,
                "requestedSignal": requested,
                "cleanShutdownChain": chain,
                "noSigintFallback": True,
                "scope": "tool lifecycle only; business failure exits remain failures",
            },
        )
    except Exception as error:
        errors.append({"phase": "api-stop", "error": repr(error)})
    finally:
        # A failed API wait must never bypass cleanup of this run's static process.
        try:
            front = psutil.Process(state["frontendPid"])
            if (
                abs(front.create_time() - state["frontendCreated"]) > 0.01
                or os.getpgid(front.pid) != state["frontendPgid"]
            ):
                raise RuntimeError("frontend PID/create-time/pgid identity changed")
            os.killpg(state["frontendPgid"], signal.SIGTERM)
            actual_front_rc = front.wait(timeout=15)
            write(
                OUT / "frontend-stop.json",
                {
                    "pid": front.pid,
                    "pgid": state["frontendPgid"],
                    "waitReturnCode": actual_front_rc,
                    "at": now(),
                },
            )
        except psutil.NoSuchProcess:
            write(
                OUT / "frontend-stop.json",
                {
                    "pid": state.get("frontendPid"),
                    "observedAbsent": True,
                    "waitReturnCode": None,
                    "at": now(),
                },
            )
        except Exception as error:
            errors.append({"phase": "frontend-stop", "error": repr(error)})
        if root is not None:
            try:
                observe_tree()
            except Exception as error:
                errors.append({"phase": "final-tree-observation", "error": repr(error)})
        remaining = []
        for _ in range(40):
            remaining = []
            for item in observed.values():
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
            {"observed": list(observed.values()), "remaining": remaining, "at": now()},
        )
        if remaining:
            errors.append(
                {
                    "phase": "process-check",
                    "error": "owned process remains; no force kill",
                }
            )
        try:
            after = queue_observation("after-stop", require_worker=False)
            if (
                any(after["redis"]["brokerLists"].values())
                or after["redis"]["unacked"]
                or after["redis"]["unackedIndex"]
            ):
                raise RuntimeError("private broker is not quiescent after stop")
        except Exception as error:
            errors.append({"phase": "broker-after-stop", "error": repr(error)})
        try:
            if sha(ARGS.native_env_file.read_bytes()) != state["nativeDotenvSha256"]:
                raise RuntimeError("native dotenv changed")
            asyncio.run(capacity_observation("after-stop"))
        except Exception as error:
            errors.append({"phase": "isolation-after-stop", "error": repr(error)})
        write(
            OUT / "stop-outcome.json",
            {
                "errors": errors,
                "watchdogBarrierObserved": barrier is not None,
                "beatReleasedAfterBarrier": beat_released,
                "actualApiExit": json.loads((OUT / "api-exit.json").read_text())
                if (OUT / "api-exit.json").exists()
                else None,
                "at": now(),
            },
        )
    if errors:
        raise RuntimeError("stop failed: " + json.dumps(errors, ensure_ascii=False))
    state["stopped"] = True
    state["apiExitObservedAt"] = now()
    save_state(state)


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
