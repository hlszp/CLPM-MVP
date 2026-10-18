"""Prepare synthetic INPUTS for real KPI E2E in explicitly disposable services.

--plan is read-only. Writers reject every identity outside the two guarded profiles.
The local profile is keeper-only and never impersonates GitHub. No result or score
is seeded: the browser suite submits a real custom calculation and checks its
immutable result record. The runner's PG/Redis/TD services are discarded at job
end, including failed runs; they must never be shared development services.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

# Only applied to the guarded disposable database before the real lifespan.
# Bootstrap seeds contain field endpoint defaults; CI must never contact them.
CI_SERVICE_CONFIG = {
    "aas.endpoint": "opc.tcp://127.0.0.1:9",
    "aas.sync_enabled": "false",
    "datasource.type": "tdengine",
    "datasource.history_api_url": "http://127.0.0.1:9/ci-disabled-history",
    "datasource.history_api_token": "",
    "datasource.signalr_hub_url": "ws://127.0.0.1:9/ci-disabled-signalr",
    "datasource.signalr_enabled": "false",
    "datasource.realtime_writeback_enabled": "false",
    "datasource.gap_backfill_enabled": "false",
    "llm.enabled": "false",
    "llm.endpoint": "",
    "llm.api_key": "",
}
BINDING_ROLES = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}


def synthetic_binding_errors(
    tag_name, loop, mappings, histories, points, created_after
):
    """Only this newly imported synthetic input may declare its own past binding."""
    errors = []

    def aware(value):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    if not re.fullmatch(r"CRITICAL-(?:LOCAL|E2E)-\d+-[1-9]\d*-FLOW001", tag_name):
        errors.append("synthetic loop namespace mismatch")
    if set(points) != BINDING_ROLES or len({p[1] for p in points.values()}) != 7:
        return errors + ["exactly seven distinct synthetic point roles required"]
    if (
        not loop
        or loop["tag_name"] != tag_name
        or loop["description"]
        != "synthetic CI INPUT: real algorithm, disposable runner only"
        or aware(loop["created_at"]) < created_after
    ):
        errors.append("loop is not newly created by this input preparation")
    if len(mappings) != 7 or {r["tag_role"] for r in mappings} != BINDING_ROLES:
        errors.append("exact seven current mappings required")
    for row in mappings:
        role = row["tag_role"]
        if role not in points or (
            row["tag_name"] != f"{tag_name}_{role}"
            or tuple(points[role]) != (row["tag_name"], row["point_id"])
            or row["tag_description"] != f"synthetic CI INPUT {role}"
        ):
            errors.append("mapping/point identity mismatch")
    if histories and (
        len(histories) != 7 or {h["tag_role"] for h in histories} != BINDING_ROLES
    ):
        errors.append("conflicting or incomplete prior binding history")
    for row in histories:
        if (
            row["tag_role"] not in points
            or row["tag_id"] != points[row["tag_role"]][1]
            or row["valid_to"] is not None
            or row["mapping_version"] != 1
            or row["basis"] != "MVP_INIT"
            or aware(row["valid_from"]) < created_after
            or aware(row["created_at"]) < created_after
        ):
            errors.append("prior history is not this fresh synthetic import")
    return errors


def validate_td_readback(rows, point_id, samples, start):
    """Verify the real inserted input bytes, without changing product timestamp parsing."""
    base_ms = int(start.replace(tzinfo=UTC).timestamp() * 1000)
    actual = []
    for row in rows:
        if row["point_id"] != point_id:
            raise RuntimeError("TD readback has a foreign point")
        value = row["ts"]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tick_ms = int(value)
            if tick_ms != value:
                raise RuntimeError("TD millisecond timestamp is not integral")
        else:
            parsed = (
                value
                if isinstance(value, datetime)
                else datetime.fromisoformat(value.replace("Z", "+00:00"))
            )
            tick_ms = int(
                (
                    parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
                ).timestamp()
                * 1000
            )
        actual.append(
            [tick_ms, float(row["value"]), row["quality_class"], row["quality_raw"]]
        )
    expected = [
        [base_ms + offset * 1000, float(value), quality, raw]
        for offset, value, quality, raw in samples
    ]
    if actual != expected:
        raise RuntimeError("TD persisted samples differ from exact synthetic INPUT")
    return actual


async def declare_synthetic_bindings(
    api_url, output, loop_id, tag_name, points, start, created_after
):
    """Explicit INPUT metadata; never changes product history resolution or results."""
    if isolation_errors(dict(os.environ), api_url):
        raise RuntimeError("synthetic binding writer refused outside isolated services")
    loop_uuid = UUID(loop_id)
    point_ids = [UUID(value[1]) for value in points.values()]
    import asyncpg

    connection = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
    )
    history_sql = (
        "SELECT id::text,tag_role,tag_id::text,valid_from,valid_to,mapping_version,basis,created_at,"
        "row_to_json(h)::text AS raw_row FROM loop_tag_binding_history h WHERE loop_id=$1 ORDER BY tag_role"
    )
    loop_sql = "SELECT id::text,tag_name,description,created_at,row_to_json(l)::text AS raw_row FROM loop_ledger l WHERE id=$1 FOR UPDATE"
    mapping_sql = (
        "SELECT m.tag_role,t.id::text AS point_id,t.tag_name,t.tag_description,"
        "row_to_json(m)::text AS mapping_raw,row_to_json(t)::text AS point_raw "
        "FROM loop_tag_mapping m JOIN tag_registry t ON t.id=m.tag_id WHERE m.loop_id=$1 "
        "ORDER BY m.tag_role FOR UPDATE OF m,t"
    )
    artifact = output.with_suffix(".bindings.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "synthetic": True,
        "inputOnly": True,
        "committed": False,
        "loopId": loop_id,
        "loopTagName": tag_name,
        "validFrom": start.replace(tzinfo=UTC).isoformat(),
        "validTo": None,
        "basis": "SYNTHETIC_FIXTURE",
        "statements": [],
    }

    def persist():
        artifact.write_text(json.dumps(payload, default=str, indent=2) + "\n")

    try:
        async with connection.transaction(isolation="serializable"):
            actual_db = await connection.fetchval("SELECT current_database()")
            if actual_db != os.environ["POSTGRES_DB"]:
                raise RuntimeError("actual private PG identity differs")
            loop = await connection.fetchrow(loop_sql, loop_uuid)
            mappings = [dict(r) for r in await connection.fetch(mapping_sql, loop_uuid)]
            histories = [
                dict(r)
                for r in await connection.fetch(history_sql + " FOR UPDATE", loop_uuid)
            ]
            foreign = await connection.fetch(
                "SELECT loop_id::text,tag_role,tag_id::text FROM loop_tag_mapping "
                "WHERE tag_id=ANY($1::uuid[]) AND loop_id<>$2 FOR UPDATE",
                point_ids,
                loop_uuid,
            )
            foreign_history = await connection.fetch(
                "SELECT loop_id::text,tag_role,tag_id::text FROM loop_tag_binding_history "
                "WHERE tag_id=ANY($1::uuid[]) AND loop_id<>$2",
                point_ids,
                loop_uuid,
            )
            errors = synthetic_binding_errors(
                tag_name, loop, mappings, histories, points, created_after
            )
            if foreign or foreign_history:
                errors.append("synthetic points are mapped by another loop")
            payload.update(
                {
                    "actualDatabase": actual_db,
                    "before": {
                        "loop": dict(loop) if loop else None,
                        "mappings": mappings,
                        "histories": histories,
                        "foreignMappings": [dict(r) for r in foreign],
                        "foreignHistory": [dict(r) for r in foreign_history],
                    },
                    "readQueries": [loop_sql, mapping_sql, history_sql],
                    "errors": errors,
                }
            )
            persist()
            if errors:
                raise RuntimeError(
                    "synthetic binding preconditions failed: " + "; ".join(errors)
                )
            effective = start.replace(tzinfo=UTC)
            if histories:
                sql = (
                    "UPDATE loop_tag_binding_history SET valid_from=$2,basis='SYNTHETIC_FIXTURE' "
                    "WHERE loop_id=$1 AND id=ANY($3::uuid[]) AND mapping_version=1 "
                    "AND basis='MVP_INIT' AND valid_to IS NULL"
                )
                parameters = [
                    loop_uuid,
                    effective,
                    [UUID(row["id"]) for row in histories],
                ]
                status = await connection.execute(sql, *parameters)
                payload["statements"].append(
                    {"sql": sql, "parameters": parameters, "status": status}
                )
                persist()
                if status != "UPDATE 7":
                    raise RuntimeError(
                        "expected exactly seven fresh history rows to declare"
                    )
            else:
                sql = (
                    "INSERT INTO loop_tag_binding_history "
                    "(id,loop_id,tag_role,tag_id,valid_from,valid_to,mapping_version,basis,created_at) "
                    "VALUES ($1,$2,$3,$4,$5,NULL,1,'SYNTHETIC_FIXTURE',NOW())"
                )
                for role, (_, point_id) in sorted(points.items()):
                    parameters = [uuid4(), loop_uuid, role, UUID(point_id), effective]
                    status = await connection.execute(sql, *parameters)
                    payload["statements"].append(
                        {"sql": sql, "parameters": parameters, "status": status}
                    )
                    persist()
                    if status != "INSERT 0 1":
                        raise RuntimeError(
                            "synthetic history insert affected unexpected rows"
                        )
            payload["afterInTransaction"] = [
                dict(r) for r in await connection.fetch(history_sql, loop_uuid)
            ]
            persist()
        async with connection.transaction(readonly=True):
            after = [dict(r) for r in await connection.fetch(history_sql, loop_uuid)]
            if (
                len(after) != 7
                or {r["tag_role"] for r in after} != BINDING_ROLES
                or any(
                    r["tag_id"] != points[r["tag_role"]][1]
                    or r["valid_from"] != effective
                    or r["valid_to"] is not None
                    or r["basis"] != "SYNTHETIC_FIXTURE"
                    for r in after
                )
            ):
                raise RuntimeError("persisted synthetic binding readback differs")
            payload.update(
                {"committed": True, "persistedReadback": after, "affectedRows": 7}
            )
            persist()
    finally:
        await connection.close()
    return {
        "artifact": artifact.name,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "validFrom": payload["validFrom"],
        "validTo": None,
        "basis": "SYNTHETIC_FIXTURE",
    }


def execution_identity(env: dict[str, str]) -> dict[str, str]:
    if env.get("E2E_EXECUTION_ENV") == "disposable-local":
        return {
            "environment": "disposable-local",
            "location": "local-keeper",
            "prefix": "CRITICAL-LOCAL",
            "runId": env.get("LOCAL_RUN_ID", ""),
            "attempt": env.get("LOCAL_RUN_ATTEMPT", "1"),
            "sourceSha": env.get("CLPM_BUILD_CODE_SHA", ""),
        }
    return {
        "environment": "disposable-github-ci",
        "location": env.get("E2E_EXECUTION_LOCATION", "github-actions"),
        "prefix": "CRITICAL-E2E",
        "runId": env.get("GITHUB_RUN_ID", ""),
        "attempt": env.get("GITHUB_RUN_ATTEMPT", "1"),
        "sourceSha": env.get("GITHUB_SHA", ""),
    }


def local_isolation_errors(env: dict[str, str], api_url: str) -> list[str]:
    errors = []
    required = {
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "17102",
        "POSTGRES_DB": "clpm_critical_20261017_local",
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "17124",
        "REDIS_DB": "0",
        "TDENGINE_HOST": "127.0.0.1",
        "TDENGINE_PORT": "17104",
        "TDENGINE_DB": "clpm_critical_20261017_local_ts",
        "E2E_BASE_URL": "http://127.0.0.1:17266",
    }
    for key, value in required.items():
        if env.get(key) != value:
            errors.append(f"{key} must be {value} for disposable-local")
    if api_url != "http://127.0.0.1:17261/api/v1":
        errors.append("disposable-local API must be http://127.0.0.1:17261/api/v1")
    if env.get("GITHUB_ACTIONS") == "true":
        errors.append("disposable-local must not impersonate GITHUB_ACTIONS")
    if not re.fullmatch(r"\d{8,20}", env.get("LOCAL_RUN_ID", "")):
        errors.append("LOCAL_RUN_ID must be 8-20 digits")
    if not re.fullmatch(r"[1-9]\d*", env.get("LOCAL_RUN_ATTEMPT", "1")):
        errors.append("LOCAL_RUN_ATTEMPT must be a positive integer")
    source_sha = env.get("CLPM_BUILD_CODE_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        errors.append("CLPM_BUILD_CODE_SHA must identify the fixed local checkout")
    else:
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        if actual.returncode or actual.stdout.strip() != source_sha:
            errors.append("CLPM_BUILD_CODE_SHA must equal this checkout's actual HEAD")
    return errors


def isolation_errors(env: dict[str, str], api_url: str) -> list[str]:
    if env.get("E2E_EXECUTION_ENV") == "disposable-local":
        return local_isolation_errors(env, api_url)
    errors = []
    if env.get("E2E_EXECUTION_ENV", "disposable-github-ci") != "disposable-github-ci":
        errors.append("unknown E2E_EXECUTION_ENV")
    for key in ("CI", "GITHUB_ACTIONS"):
        if env.get(key) != "true":
            errors.append(f"{key}=true is required")
    for key in ("POSTGRES_HOST", "REDIS_HOST", "TDENGINE_HOST"):
        if env.get(key) not in {"localhost", "127.0.0.1"}:
            errors.append(f"{key} must be a disposable loopback CI service")
    if env.get("POSTGRES_DB") != "clpm_critical_e2e":
        errors.append("POSTGRES_DB must be clpm_critical_e2e")
    if env.get("TDENGINE_DB") != "clpm_critical_e2e_ts":
        errors.append("TDENGINE_DB must be clpm_critical_e2e_ts")
    if not re.fullmatch(r"\d+", env.get("GITHUB_RUN_ID", "")):
        errors.append("GITHUB_RUN_ID must be present")
    if not re.fullmatch(r"\d+", env.get("GITHUB_RUN_ATTEMPT", "")):
        errors.append("GITHUB_RUN_ATTEMPT must be present")
    if not re.fullmatch(r"[0-9a-f]{40}", env.get("GITHUB_SHA", "")):
        errors.append("GITHUB_SHA must identify the fixed checkout")
    if urlparse(api_url).hostname not in {"localhost", "127.0.0.1"}:
        errors.append("API must be on the same disposable CI runner")
    return errors


async def configure_services(output: Path) -> None:
    """Real CI-only PG updates; no product import, dotenv or service startup."""
    import asyncpg

    connection = await asyncpg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        database=os.environ["POSTGRES_DB"],
    )
    try:
        async with connection.transaction():
            await connection.executemany(
                "INSERT INTO sys_config (key,value,description,updated_by,updated_at) "
                "VALUES ($1,$2,'disposable CI service isolation','critical-e2e',NOW()) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,"
                "updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at",
                list(CI_SERVICE_CONFIG.items()),
            )
            rows = await connection.fetch(
                "SELECT key,value FROM sys_config WHERE key=ANY($1::text[])",
                list(CI_SERVICE_CONFIG),
            )
            actual = {row["key"]: row["value"] for row in rows}
            if actual != CI_SERVICE_CONFIG:
                raise RuntimeError(
                    "CI external-service configuration did not persist exactly"
                )
    finally:
        await connection.close()
    payload = {
        "candidateSha": execution_identity(dict(os.environ))["sourceSha"],
        "configurationWritten": True,
        "database": os.environ["POSTGRES_DB"],
        "values": actual,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))


async def prepare(api_url: str, output: Path) -> None:
    # Reuse the existing deterministic input generator and real import format;
    # do not alter its frozen capacity denominator or fabricate KPI snapshots.
    from generate_capacity_dataset import (
        LOOP_HEADERS,
        ROLES,
        TAG_HEADERS,
        ApiClient,
        _td_rows,
        _xlsx,
        load_role_points,
        synth_series,
    )
    from sqlalchemy.dialects.postgresql import insert

    from app.core.config import settings
    from app.core.db import AsyncSessionLocal
    from app.core.tdengine_native import execute_native, execute_native_effective
    from app.models.sys_config import SysConfig
    from app.services.data_source.point_history_repository import (
        POINT_STABLE,
        ensure_schema,
        point_subtable,
    )

    identity = execution_identity(dict(os.environ))
    created_after = datetime.now(UTC)
    prefix = f"{identity['prefix']}-{identity['runId']}-{identity['attempt']}"
    tag_name = f"{prefix}-FLOW001"
    api = ApiClient(api_url, "admin", "admin123")
    responses: dict = {}
    parent = None
    for suffix, kind in (("FACTORY", "FACTORY"), ("AREA", "AREA"), ("UNIT", "UNIT")):
        response = api.post(
            "/plant-nodes",
            json={"name": f"{prefix}-{suffix}", "type": kind, "parentId": parent},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") not in (0, "0"):
            raise RuntimeError(data)
        responses[suffix] = data
        parent = data["data"]["id"]

    tag_rows = []
    for role in ROLES:
        bounded = role in {"PV", "SP", "OP"}
        tag_rows.append(
            [
                f"{tag_name}_{role}",
                f"synthetic CI INPUT {role}",
                "FLOW" if role in {"PV", "SP"} else "OTHER",
                0 if bounded else None,
                100 if bounded else None,
                "%" if bounded else None,
                role,
                "",
                "",
                "是",
            ]
        )
    loop_rows = [
        [
            tag_name,
            "synthetic CI INPUT: real algorithm, disposable runner only",
            f"{tag_name}_SP",
            f"{tag_name}_PV",
            f"{tag_name}_OP",
            f"{tag_name}_MODE",
            f"{prefix}-UNIT",
            "是",
            f"{tag_name}_PID_P",
            f"{tag_name}_PID_I",
            f"{tag_name}_PID_D",
            "FLOW",
            "STABLE",
            2,
            "是",
            None,
            None,
            "",
            "synthetic=true; critical E2E INPUT, never field evidence",
        ]
    ]
    for path, headers, rows in (
        ("tags", TAG_HEADERS, tag_rows),
        ("loops", LOOP_HEADERS, loop_rows),
    ):
        response = api.post_file(
            f"/{path}/import", "file", f"critical-{path}.xlsx", _xlsx(headers, rows)
        )
        response.raise_for_status()
        responses[path] = response.json()
        if responses[path].get("code") not in (0, "0"):
            raise RuntimeError(responses[path])
        receipt = responses[path]["data"]
        expected_count = 7 if path == "tags" else 1
        if (
            any(receipt.get(key) != expected_count for key in ("total", "inserted"))
            or any(receipt.get(key) != 0 for key in ("updated", "failed"))
            or receipt.get("errors")
        ):
            raise RuntimeError(
                "critical INPUT requires newly inserted tags/loop; refuse reused prefix"
            )

    role_map = await load_role_points(prefix)
    if len(role_map) != 1 or set(next(iter(role_map.values()))) != set(ROLES):
        raise RuntimeError(
            f"expected one complete seven-role synthetic loop, got {role_map}"
        )
    loop_id, points = next(iter(role_map.items()))
    start = (datetime.now(UTC) - timedelta(hours=2)).replace(
        tzinfo=None, minute=0, second=0, microsecond=0
    )
    binding_declaration = await declare_synthetic_bindings(
        api_url, output, loop_id, tag_name, points, start, created_after
    )
    series = synth_series(1, "normal", "steady", 100.0)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_input_path = output.with_suffix(".inputs.json")
    raw_input_path.write_text(
        json.dumps(
            {
                "synthetic": True,
                "inputOnly": True,
                "windowStart": start.isoformat(),
                "windowEnd": (start + timedelta(hours=1)).isoformat(),
                "syntheticBindingDeclaration": binding_declaration,
                "columns": ["offsetSeconds", "value", "qualityClass", "qualityRaw"],
                "roles": {
                    role: {"pointId": points[role][1], "samples": series[role]}
                    for role in ROLES
                },
            },
            indent=2,
        )
        + "\n"
    )
    await ensure_schema()
    inputs = {}
    td_readback = {}
    for role in ROLES:
        point_id = points[role][1]
        rows = _td_rows(point_id, series[role], start)
        sql = (
            f"INSERT INTO {settings.TDENGINE_DB}.{point_subtable(point_id)} "
            f"USING {POINT_STABLE} TAGS ('{point_id}', 'synthetic:critical-e2e') VALUES "
            + " ".join(rows)
        )
        await execute_native_effective(sql)
        start_ms = int(start.replace(tzinfo=UTC).timestamp() * 1000)
        end_ms = start_ms + 3601 * 1000
        read_sql = (
            f"SELECT ts,value,quality_class,quality_raw,point_id FROM {settings.TDENGINE_DB}.{point_subtable(point_id)} "
            f"WHERE ts>={start_ms} AND ts<{end_ms} ORDER BY ts"
        )
        persisted = await execute_native(read_sql)
        td_readback[role] = {
            "pointId": point_id,
            "sql": read_sql,
            "count": len(persisted),
            "rawRows": persisted,
        }
        readback_artifact = output.with_suffix(".td-readback.json")
        readback_artifact.write_text(
            json.dumps(td_readback, default=str, indent=2) + "\n"
        )
        canonical = validate_td_readback(persisted, point_id, series[role], start)
        td_readback[role]["canonicalSha256"] = hashlib.sha256(
            json.dumps(canonical).encode()
        ).hexdigest()
        readback_artifact.write_text(
            json.dumps(td_readback, default=str, indent=2) + "\n"
        )
        inputs[role] = {
            "pointId": point_id,
            "count": len(rows),
            "sha256": hashlib.sha256(json.dumps(series[role]).encode()).hexdigest(),
        }
    declaration = {
        "schemaVersion": 1,
        "synthetic": True,
        "inputOnly": True,
        "environment": identity["environment"],
        "executionLocation": identity["location"],
        "candidateSha": identity["sourceSha"],
        "loopId": loop_id,
        "loopTagName": tag_name,
        "windowStart": start.isoformat(),
        "windowEnd": (start + timedelta(hours=1)).isoformat(),
        "syntheticBindingDeclaration": binding_declaration,
        "tdReadbackArtifact": readback_artifact.name,
        "tdReadbackSha256": hashlib.sha256(readback_artifact.read_bytes()).hexdigest(),
        "generator": "generate_capacity_dataset.synth_series(seed=1,normal,steady,span=100)",
        "inputs": inputs,
        "rawInputArtifact": raw_input_path.name,
        "rawInputSha256": hashlib.sha256(raw_input_path.read_bytes()).hexdigest(),
    }
    async with AsyncSessionLocal() as db:
        await db.execute(
            insert(SysConfig).values(
                key="e2e.critical.synthetic_input",
                value=json.dumps(declaration),
                description="synthetic CI INPUT only; not field acceptance",
                updated_by="critical-e2e",
            )
        )
        await db.commit()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(declaration, indent=2) + "\n")
    output.with_suffix(".imports.json").write_text(
        json.dumps(responses, indent=2) + "\n"
    )
    print(json.dumps(declaration, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    mode.add_argument("--configure-services", action="store_true")
    mode.add_argument("--plan", action="store_true")
    parser.add_argument("--api-url", default="http://localhost:17101/api/v1")
    parser.add_argument("--output", type=Path, default=Path("critical-fixture.json"))
    args = parser.parse_args()
    if not args.execute and not args.configure_services:
        print(
            json.dumps(
                {
                    "planOnly": True,
                    "writes": False,
                    "loops": 1,
                    "tags": 7,
                    "data": "synthetic INPUT; no results seeded",
                    "cleanup": "GitHub job services discarded; local services owned and stopped by keeper",
                    "serviceIsolationBeforeLifespan": CI_SERVICE_CONFIG,
                    "executionPreconditions": isolation_errors(
                        dict(os.environ), args.api_url
                    ),
                },
                indent=2,
            )
        )
        return 0
    errors = isolation_errors(dict(os.environ), args.api_url)
    if errors:
        print(
            json.dumps({"refusedBeforeImportsOrWrites": errors}, indent=2),
            file=sys.stderr,
        )
        return 2
    if args.configure_services:
        asyncio.run(configure_services(args.output))
    else:
        asyncio.run(prepare(args.api_url, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
