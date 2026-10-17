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
    from app.core.tdengine_native import execute_native_effective
    from app.models.sys_config import SysConfig
    from app.services.data_source.point_history_repository import (
        POINT_STABLE,
        ensure_schema,
        point_subtable,
    )

    identity = execution_identity(dict(os.environ))
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

    role_map = await load_role_points(prefix)
    if len(role_map) != 1 or set(next(iter(role_map.values()))) != set(ROLES):
        raise RuntimeError(
            f"expected one complete seven-role synthetic loop, got {role_map}"
        )
    loop_id, points = next(iter(role_map.items()))
    start = (datetime.now(UTC) - timedelta(hours=2)).replace(
        tzinfo=None, minute=0, second=0, microsecond=0
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
    for role in ROLES:
        point_id = points[role][1]
        rows = _td_rows(point_id, series[role], start)
        sql = (
            f"INSERT INTO {settings.TDENGINE_DB}.{point_subtable(point_id)} "
            f"USING {POINT_STABLE} TAGS ('{point_id}', 'synthetic:critical-e2e') VALUES "
            + " ".join(rows)
        )
        await execute_native_effective(sql)
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
