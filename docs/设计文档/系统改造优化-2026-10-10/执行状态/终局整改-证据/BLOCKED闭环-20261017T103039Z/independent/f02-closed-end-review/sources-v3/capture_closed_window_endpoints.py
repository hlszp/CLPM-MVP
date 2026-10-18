"""Keeper-run read-only full actual closed-endpoint capture after 24 inputs close.
No application imports/tasks/config writes. Each <=500-ID exact ts=end SELECT has
a separate same-predicate COUNT and no LIMIT; all original responses remain.
"""

from __future__ import annotations
import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import re
import sys
import time
import uuid

import asyncpg
from dotenv import dotenv_values
import httpx
from audit_capacity_inputs import private_config, rows_of, serial
from freeze_capacity_configuration import canonical, completed_inputs, sha

ROLES = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
DATA_COLUMNS = (
    "point_id",
    "ts",
    "ts_epoch_ms",
    "value",
    "quality_class",
    "quality_raw",
    "quality_schema",
    "source_kind",
    "payload_hash",
)
DATA_PROJECTION = "point_id, ts, CAST(ts AS BIGINT) AS ts_epoch_ms, `value`, quality_class, quality_raw, quality_schema, source_kind, payload_hash"


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed.astimezone(timezone.utc).replace(tzinfo=None)
        if parsed.tzinfo
        else parsed
    )


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def json_file(path):
    return json.loads(Path(path).read_bytes())


def same_identity(identity, completed):
    loops, mappings = identity["loops"], identity["mappings"]
    pairs = sorted((row["id"], row["tag_name"]) for row in loops)
    bindings = sorted(
        (row["loop_id"], row["tag_role"], row["tag_id"]) for row in mappings
    )
    if (
        pairs != completed["verifiedLoopIdentity"]
        or bindings != completed["verifiedPointBindings"]
    ):
        raise ValueError(
            "Actual point/loop identity differs from the frozen original twenty inputs"
        )
    if any(
        not row.get("is_active")
        or row.get("status") != "READY"
        or not row.get("include_in_evaluation")
        for row in loops
    ):
        raise ValueError("All actual1000 source objects must remain eligible")
    return pairs, bindings


def extra_gate(extra_plan, extra_complete, completed):
    planned = json_file(extra_plan)["inputs"]
    done = json_file(extra_complete)
    purposes = {
        "fault-recovery",
        "fault-persistent",
        "independent-cold",
        "independent-hot",
    }
    if (
        len(planned) != 4
        or {row["purpose"] for row in planned} != purposes
        or any(row.get("formal20Denominator") is not False for row in planned)
    ):
        raise ValueError("Four separate frozen extra inputs required")
    expected_scenarios = {
        "fault-recovery": "a",
        "fault-persistent": "a",
        "independent-cold": "b",
        "independent-hot": "b",
    }
    if any(row["scenario"] != expected_scenarios[row["purpose"]] for row in planned):
        raise ValueError(
            "Extra fault A/A and independent B/B scenarios must match the frozen plan"
        )
    results = done.get("results", [])
    if (
        done.get("preparedAndAudited") != 4
        or done.get("formal20Executed") is not False
        or len(results) != 4
        or {row["input"]["purpose"] for row in results} != purposes
    ):
        raise ValueError(
            "All four extra generation/audits must close before endpoint capture"
        )
    outputs = set()
    manifests = set()
    for result in results:
        plan = next(
            row for row in planned if row["purpose"] == result["input"]["purpose"]
        )
        if result["input"] != plan or result.get("auditRc") != 0:
            raise ValueError("Extra input plan/audit rc mismatch")
        path = Path(result["manifest"]).resolve()
        audit = path.parent / "audit"
        raw = path.read_bytes()
        if audit.resolve() in outputs or sha(raw) in manifests:
            raise ValueError(
                "Repeated extra original artifacts cannot count four inputs"
            )
        outputs.add(audit.resolve())
        manifests.add(sha(raw))
        if (
            sha(raw) != result["manifestSha256"]
            or sha((audit / "input-manifest.json").read_bytes())
            != result["manifestSha256"]
            or sha((audit / "input-audit.json").read_bytes())
            != result["auditReportSha256"]
        ):
            raise ValueError("Extra manifest/audit original hash mismatch")
        report = json_file(audit / "input-audit.json")
        manifest = json.loads(raw)
        if report.get("manifestSha256") != result["manifestSha256"]:
            raise ValueError(
                "Extra actual audit report manifest identity differs from original"
            )
        generation = json_file(path.parent / "generation-command.json")
        commands = generation.get("commands", [])
        if (
            generation.get("input") != plan
            or len(commands) != 1
            or commands[0].get("rc") != 0
        ):
            raise ValueError(
                "Extra original generation command is not the planned successful input"
            )
        argv = report.get("argv", [])
        if (
            "--manifest" not in argv
            or argv.index("--manifest") + 1 >= len(argv)
            or Path(argv[argv.index("--manifest") + 1]).resolve() != path
        ):
            raise ValueError(
                "Extra original audit argv manifest differs from its actual input"
            )
        if (
            report.get("status") != "PASS"
            or report.get("globalEligible") != 1000
            or report.get("pointCount") != 7000
            or report.get("failures") != []
        ):
            raise ValueError("Extra actual full-denominator input audit not PASS")
        if (
            utc(report["windowStart"]) != utc(plan["ts_start"])
            or utc(report["windowEnd"]) != utc(plan["ts_end"])
            or utc(manifest["windowStart"]) != utc(plan["ts_start"])
            or manifest["scenario"] != plan["scenario"]
        ):
            raise ValueError(
                "Extra actual window/scenario differs from its original plan"
            )
        if len(manifest["rows"]) != 1000 or {
            row["loopTag"] for row in manifest["rows"]
        } != {f"PERF-T{i:04d}" for i in range(1, 1001)}:
            raise ValueError(
                "Extra manifest is not the full 1000 unique actual objects"
            )
        if any(
            utc(row["windowStart"]) != utc(plan["ts_start"])
            or utc(row["windowEnd"]) != utc(plan["ts_end"])
            or row.get("scenario") != plan["scenario"]
            for row in manifest["rows"]
        ):
            raise ValueError(
                "Extra per-object original window/scenario differs from plan"
            )
        same_identity(json_file(audit / "pg-input-identity.json"), completed)
    return planned, done


def build_queries(database, point_ids, endpoint):
    if not database.startswith("clpm_capacity_") or not re.fullmatch(
        r"[a-zA-Z_][a-zA-Z0-9_]*", database
    ):
        raise ValueError("Private capacity TD identifier required")
    if not 1 <= len(point_ids) <= 500 or len(set(point_ids)) != len(point_ids):
        raise ValueError("Explicit distinct chunk size1..500 required")
    if any(str(uuid.UUID(point)) != point for point in point_ids):
        raise ValueError("Actual canonical point UUIDs required")
    literal = utc(endpoint).isoformat(timespec="milliseconds") + "Z"
    predicate = (
        "point_id IN ("
        + ", ".join("'" + point + "'" for point in point_ids)
        + ") AND ts = '"
        + literal
        + "'"
    )
    table = database + ".st_point_data_v1"
    return {
        "count": "SELECT COUNT(*) AS row_count FROM " + table + " WHERE " + predicate,
        "data": "SELECT " + DATA_PROJECTION + " FROM " + table + " WHERE " + predicate,
    }


def exact_integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    raise ValueError("Exact integer or original bigint string required; no rounding")


def evaluate_chunk(point_ids, endpoint, declared_count, rows):
    failures = []
    declared_count = exact_integer(declared_count)
    if declared_count < 0 or len(rows) != declared_count:
        failures.append("Exact COUNT differs from complete raw SELECT row denominator")
    if any(
        not isinstance(row, dict) or not set(DATA_COLUMNS) <= row.keys() for row in rows
    ):
        failures.append(
            "Actual endpoint data row lacks required original value/quality columns"
        )
        return failures
    expected_ms = int(utc(endpoint).replace(tzinfo=timezone.utc).timestamp() * 1000)
    if any(row.get("point_id") not in set(point_ids) for row in rows):
        failures.append("Response contains a point outside this actual chunk")
    if any(exact_integer(row.get("ts_epoch_ms")) != expected_ms for row in rows):
        failures.append(
            "Actual returned endpoint timestamp/DB precision differs from exact end milliseconds"
        )
    duplicates = [
        point
        for point, count in Counter(row.get("point_id") for row in rows).items()
        if count > 1
    ]
    if duplicates:
        failures.append("Duplicate actual point at exact endpoint; raw rows retained")
    return failures


def validate_response_shape(payload, sql):
    if sql.startswith("SELECT COUNT(*) AS row_count FROM "):
        expected = ("row_count",)
    elif sql.startswith("SELECT " + DATA_PROJECTION + " FROM "):
        expected = DATA_COLUMNS
    else:
        raise ValueError(
            "Only the frozen exact COUNT/data SQL projections are accepted"
        )
    columns = payload.get("column_meta")
    if not isinstance(columns, list) or any(
        not isinstance(column, list) or not column or not isinstance(column[0], str)
        for column in columns
    ):
        raise ValueError(
            "TD original column_meta must explicitly describe every column"
        )
    names = [column[0] for column in columns]
    if len(names) != len(set(names)) or tuple(names) != expected:
        raise ValueError(
            "TD original columns are duplicate or differ from the actual SQL projection"
        )
    data = payload.get("data")
    if not isinstance(data, list) or any(
        not isinstance(row, list) or len(row) != len(expected) for row in data
    ):
        raise ValueError("TD original row width differs from its complete column_meta")


def project_rows(loops, mappings, rows, expect_next):
    by_loop = {row["id"]: row for row in loops}
    by_point = defaultdict(list)
    for row in rows:
        by_point[row["point_id"]].append(row)
    projected = []
    for mapping in sorted(
        mappings,
        key=lambda item: (by_loop[item["loop_id"]]["tag_name"], item["tag_role"]),
    ):
        actual = by_point.get(mapping["tag_id"], [])
        projected.append(
            {
                "loopId": mapping["loop_id"],
                "loopTag": by_loop[mapping["loop_id"]]["tag_name"],
                "role": mapping["tag_role"],
                "pointId": mapping["tag_id"],
                "present": bool(actual),
                "rawRows": actual,
            }
        )
    missing = [row["pointId"] for row in projected if not row["present"]]
    failures = (
        ["A generated next window exists but some exact-end source points are missing"]
        if expect_next and missing
        else []
    )
    return projected, missing, failures


def capture_query(client, url, sql, out, name):
    began = now()
    clock = time.monotonic()
    write(out / (name + ".sql"), sql.encode())
    metadata = {
        "startedAt": began,
        "method": "POST",
        "url": url,
        "sqlFile": name + ".sql",
        "sqlSha256": sha(sql.encode()),
        "noAuthorizationRecorded": True,
    }
    rows = None
    try:
        response = client.post(url, content=sql.encode())
        raw = response.content
        write(out / (name + ".response.raw.json.gz"), gzip.compress(raw, mtime=0))
        metadata.update(
            httpStatus=response.status_code,
            rawFile=name + ".response.raw.json.gz",
            rawSha256=sha(raw),
            gzipSha256=sha((out / (name + ".response.raw.json.gz")).read_bytes()),
        )
        payload = response.json()
        metadata["tdCode"] = payload.get("code")
        if response.status_code != 200:
            raise ValueError("TD endpoint HTTP status non200")
        if payload.get("code", 0) not in (None, 0):
            raise ValueError("TD original query returned nonzero code")
        validate_response_shape(payload, sql)
        rows = rows_of(payload)
        if type(payload.get("rows")) is not int or payload["rows"] != len(rows):
            raise ValueError(
                "TD response reported row count differs from original data"
            )
        write(
            out / (name + ".rows.canonical.json.gz"),
            gzip.compress(canonical(rows), mtime=0),
        )
        metadata.update(
            rc=0,
            rowCount=len(rows),
            canonicalRowsFile=name + ".rows.canonical.json.gz",
            canonicalRowsSha256=sha(canonical(rows)),
        )
    except BaseException as exc:
        metadata.update(
            rc=1,
            errorType=type(exc).__name__,
            error=str(exc)
            if type(exc) is ValueError
            else "TD original query failed; retained response if available",
        )
    metadata.update(finishedAt=now(), elapsedSeconds=time.monotonic() - clock)
    write(out / (name + ".command.json"), canonical(metadata))
    return metadata, rows


async def main(args):
    completed = completed_inputs(args.normal_audit_progress, args.normal_plan)
    extra, extra_done = extra_gate(args.extra_plan, args.extra_complete, completed)
    normal = json_file(args.normal_plan)
    normal = normal["batches"] if isinstance(normal, dict) else normal
    windows = [
        dict(row, purpose=f"normal-{row['batch']:02d}", formal20Denominator=True)
        for row in normal
    ] + extra
    windows = sorted(windows, key=lambda row: utc(row["ts_start"]))
    if (
        len(windows) != 24
        or len({utc(row["ts_start"]) for row in windows}) != 24
        or any(
            utc(b["ts_start"]) != utc(a["ts_end"]) for a, b in zip(windows, windows[1:])
        )
    ):
        raise ValueError("Exactly 24 actual consecutive unique windows required")
    cfg = dotenv_values(args.env_file)
    private_config(cfg)
    if cfg["POSTGRES_DB"] != cfg["TDENGINE_DB"]:
        raise ValueError("Actual PG/TD namespace must be same private series")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "startedAt": now(),
        "argv": sys.argv,
        "status": "BLOCKED",
        "normalDenominator": 20,
        "separateExtraInputs": 4,
        "windows": [],
        "sourceIdentity": {
            "normalPlanSha256": sha(Path(args.normal_plan).read_bytes()),
            "normalAuditProgressSha256": sha(
                Path(args.normal_audit_progress).read_bytes()
            ),
            "extraPlanSha256": sha(Path(args.extra_plan).read_bytes()),
            "extraCompleteSha256": sha(Path(args.extra_complete).read_bytes()),
        },
        "boundary": "Exact actual ts=end values/QC only, separate from half-open input audits. Missing source rows remain missing; no held/interpolated/generated values are filled.",
    }
    db = await asyncpg.connect(
        host=cfg.get("POSTGRES_HOST", "localhost"),
        port=int(cfg.get("POSTGRES_PORT", 17102)),
        user=cfg.get("POSTGRES_USER", "clpm"),
        password=cfg["POSTGRES_PASSWORD"],
        database=cfg["POSTGRES_DB"],
        server_settings={
            "application_name": "F02-closed-end-readonly",
            "statement_timeout": "30000",
        },
    )
    try:
        async with db.transaction(isolation="repeatable_read", readonly=True):
            identity = serial(
                await db.fetchrow(
                    "SELECT current_database() AS database,current_setting('transaction_read_only') AS readonly,txid_current_snapshot()::text AS snapshot,clock_timestamp() AS observed_at"
                )
            )
            loops = serial(
                await db.fetch(
                    "SELECT id::text,tag_name,unit_id::text,status,is_active,include_in_evaluation FROM loop_ledger WHERE tag_name LIKE 'PERF-T%' ORDER BY tag_name"
                )
            )
            mappings = serial(
                await db.fetch(
                    "SELECT m.loop_id::text,m.tag_role,m.tag_id::text FROM loop_tag_mapping m JOIN loop_ledger l ON l.id=m.loop_id WHERE l.tag_name LIKE 'PERF-T%' ORDER BY l.tag_name,m.tag_role"
                )
            )
            global_eligible = await db.fetchval(
                "SELECT count(*) FROM loop_ledger WHERE is_active AND status='READY' AND include_in_evaluation"
            )
        same_identity({"loops": loops, "mappings": mappings}, completed)
        if global_eligible != 1000:
            raise ValueError("Actual global eligible set differs from the full1000")
        write(
            out / "postgres-identity.json",
            canonical(
                {
                    "transaction": identity,
                    "loops": loops,
                    "mappings": mappings,
                    "globalEligible": global_eligible,
                }
            ),
        )
        write(out / "combined-24-window-plan.json", canonical(windows))
        point_ids = sorted(row["tag_id"] for row in mappings)
        url = f"http://{cfg.get('TDENGINE_HOST', 'localhost')}:{int(cfg.get('TDENGINE_PORT', 17104)) + 11}/rest/sql"
        with httpx.Client(
            auth=(cfg.get("TDENGINE_USER", "root"), cfg.get("TDENGINE_PASSWORD", "")),
            timeout=args.timeout,
            trust_env=False,
        ) as client:
            all_failures = []
            for index, window in enumerate(windows, 1):
                end = window["ts_end"]
                window_dir = out / f"{index:02d}-{window['purpose']}"
                window_dir.mkdir()
                actual_rows = []
                failures = []
                query_refs = []
                for chunk_index, start in enumerate(range(0, 7000, 500), 1):
                    chunk = point_ids[start : start + 500]
                    queries = build_queries(cfg["TDENGINE_DB"], chunk, end)
                    count_meta, count_rows = capture_query(
                        client,
                        url,
                        queries["count"],
                        window_dir,
                        f"chunk-{chunk_index:02d}-count",
                    )
                    data_meta, data_rows = capture_query(
                        client,
                        url,
                        queries["data"],
                        window_dir,
                        f"chunk-{chunk_index:02d}-data",
                    )
                    query_refs.append(
                        {
                            "chunk": chunk_index,
                            "actualPointIds": chunk,
                            "count": count_meta,
                            "data": data_meta,
                        }
                    )
                    if count_meta["rc"] or data_meta["rc"]:
                        raise ValueError(
                            "Exact endpoint query failed; no missing-data substitution allowed"
                        )
                    if len(count_rows) != 1 or "row_count" not in count_rows[0]:
                        raise ValueError(
                            "Exact count aggregate did not return one original row"
                        )
                    failures.extend(
                        evaluate_chunk(
                            chunk, end, count_rows[0]["row_count"], data_rows
                        )
                    )
                    actual_rows.extend(data_rows)
                projected, missing, source_failures = project_rows(
                    loops, mappings, actual_rows, index < 24
                )
                failures.extend(source_failures)
                body = {
                    "window": window,
                    "endpoint": end,
                    "expectedEpochMs": int(
                        utc(end).replace(tzinfo=timezone.utc).timestamp() * 1000
                    ),
                    "nextGeneratedWindowExists": index < 24,
                    "actualMappedPoints": 7000,
                    "foundPoints": 7000 - len(missing),
                    "rawRowCount": len(actual_rows),
                    "missingPointIds": missing,
                    "failures": failures,
                    "queries": query_refs,
                    "pointRowsFile": "all-7000-point-roles.json.gz",
                    "status": "CAPTURED" if not failures else "REWORK",
                }
                write(
                    window_dir / "all-7000-point-roles.json.gz",
                    gzip.compress(canonical(projected), mtime=0),
                )
                write(window_dir / "endpoint-summary.json", canonical(body))
                report["windows"].append(
                    {
                        "directory": str(window_dir.relative_to(out)),
                        **{
                            key: body[key]
                            for key in (
                                "endpoint",
                                "nextGeneratedWindowExists",
                                "foundPoints",
                                "rawRowCount",
                                "status",
                                "failures",
                            )
                        },
                    }
                )
                all_failures.extend(failures)
                print(
                    json.dumps(
                        {
                            "window": index,
                            "status": body["status"],
                            "foundPoints": body["foundPoints"],
                            "missingPoints": len(missing),
                        }
                    ),
                    flush=True,
                )
            report["status"] = "CAPTURED" if not all_failures else "REWORK"
    except BaseException as exc:
        report.update(
            status="BLOCKED",
            errorType=type(exc).__name__,
            error=str(exc)
            if type(exc) is ValueError
            else "Read-only endpoint capture failed; safe partial originals retained",
        )
    finally:
        await db.close()
    report["finishedAt"] = now()
    write(out / "endpoint-capture.json", canonical(report))
    files = [
        {
            "file": str(path.relative_to(out)),
            "bytes": path.stat().st_size,
            "sha256": sha(path.read_bytes()),
        }
        for path in sorted(out.rglob("*"))
        if path.is_file()
    ]
    write(out / "sha256-manifest.json", canonical({"files": files}))
    print(
        json.dumps(
            {
                "status": report["status"],
                "capturedWindows": len(report["windows"]),
                "normalDenominator": 20,
                "extraInputs": 4,
                "out": str(out),
            }
        )
    )
    return (
        0
        if report["status"] == "CAPTURED"
        else 1
        if report["status"] == "REWORK"
        else 2
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--normal-plan", required=True)
    p.add_argument("--normal-audit-progress", required=True)
    p.add_argument("--extra-plan", required=True)
    p.add_argument("--extra-complete", required=True)
    p.add_argument("--env-file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=float, default=60)
    raise SystemExit(asyncio.run(main(p.parse_args())))
