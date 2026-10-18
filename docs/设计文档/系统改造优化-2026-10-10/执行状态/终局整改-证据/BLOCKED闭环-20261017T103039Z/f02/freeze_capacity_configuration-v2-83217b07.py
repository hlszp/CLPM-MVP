"""Keeper-run read-only capacity configuration freeze after all 20 inputs close.
No application imports, task submission, secret sys_config reads, or data writes.
Exports exact raw PG JSON, canonical recoverable copies, source defaults and real API
merged views. Stable before/after DB digest is observed, not distributed atomicity.
"""

from __future__ import annotations
import argparse
import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

import asyncpg
from dotenv import dotenv_values
import httpx

from audit_capacity_inputs import private_config

TABLES = (
    "alembic_version",
    "metric_config",
    "clpm_metric_data_requirement",
    "algorithm_parameter",
    "config_override",
    "config_publication",
    "engineering_template",
    "engineering_template_version",
    "engine_rule",
    "loop_type_weight",
    "loop_level_weight",
    "mode_definition",
    "dcs_mode_mapping",
    "dcs_pid_structure",
    "loop_mode_mapping",
    "loop_ledger",
    "loop_tag_mapping",
    "tag_registry",
    "plant_node",
    "loop_tag_binding_history",
    "history_layout_manifest",
    "point_state_anchor",
    "history_coverage_segment",
    "assessment_release_registry",
)
SYS_KEYS = (
    "metric_definition.current",
    "outlier_params.current",
    "confidence_thresholds.current",
    "kpi.min_observed_ratio",
    "history.storage_mode",
    "capacity.perf_dataset.declaration",
    "fitness.manual_dominant_pct",
    "fitness.low_auto_rate_pct",
    "fitness.op_saturated_band_pct",
    "fitness.op_saturated_time_pct",
    "fitness.sp_pv_deviation_pct",
    "fitness.sp_pv_deviation_time_pct",
    "fitness.no_excitation_op_range_pct",
    "fitness.weak_response_min_gain",
    "fitness.dimension_maps",
    "datasource.signalr_enabled",
    "datasource.realtime_writeback_enabled",
    "datasource.gap_backfill_enabled",
    "aas.sync_enabled",
    "datasource.type",
)
SOURCE_FILES = (
    "app/tasks/kpi_calc.py",
    "app/services/algorithm_config.py",
    "app/services/config_publish.py",
    "app/services/data_planner.py",
    "app/services/assessment_profile.py",
    "app/services/loop_fitness.py",
    "app/services/metric_calculator/time_axis.py",
    "app/services/cache/l1_datablock.py",
    "app/services/cache/l2_bundle.py",
    "app/services/data_source/history_layout_router.py",
    "app/services/data_source/logical_wide_builder.py",
    "app/services/data_source/point_history_repository.py",
    "app/contracts/data_types.py",
    "app/api/v1/endpoints/confidence_config.py",
    "app/api/v1/endpoints/outlier_config.py",
)
API_PATHS = (
    "configs/publish/revision",
    "configs/algorithm-params",
    "configs/outlier-params",
    "configs/confidence-thresholds",
)
SENSITIVE_KEY = re.compile(
    r"(?:^|[_\W])(password|passwd|secret|authorization|token|api_key|url|uri|endpoint)(?:$|[_\W])",
    re.I,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def reject_secrets(value):
    if isinstance(value, dict):
        if any(SENSITIVE_KEY.search(str(key)) for key in value):
            raise ValueError("Sensitive configuration field refused before archiving")
        for item in value.values():
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)
    elif isinstance(value, str):
        if re.search(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9_+/=.-]{12,}", value):
            raise ValueError("Credential-shaped value refused before archiving")
        if value.startswith(("{", "[")):
            parsed = json_value(value)
            if not isinstance(parsed, str):
                reject_secrets(parsed)


def completed_inputs(path):
    progress = json.loads(Path(path).read_text())
    rows = progress.get("results", [])
    if (
        progress.get("status") != "ALL_INPUTS_PASS"
        or progress.get("passed") != 20
        or progress.get("required") != 20
    ):
        raise ValueError(
            "All 20 input generations and audits must close before config freeze"
        )
    if (
        len(rows) != 20
        or {r["batch"] for r in rows} != set(range(1, 21))
        or any(r.get("status") != "PASS" or r.get("rc") != 0 for r in rows)
    ):
        raise ValueError("Exact 20 successful input audit identities required")
    for row in rows:
        audit_dir = (
            Path(row["existingOutput"])
            if row.get("existingOutput")
            else Path(row["output"]) / "audit"
        )
        raw = (audit_dir / "input-audit.json").read_bytes()
        if sha(raw) != row["reportSha256"]:
            raise ValueError("Completed input audit raw report hash changed")
        report = json.loads(raw)
        if (
            report["status"] != "PASS"
            or report["manifestSha256"] != row["manifestSha256"]
            or sha((audit_dir / "input-manifest.json").read_bytes())
            != row["manifestSha256"]
        ):
            raise ValueError("Completed input audit/manifest identity failed")
    return progress


def literal_assignment(source, name):
    tree = ast.parse(Path(source).read_bytes())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Missing source literal: " + name)


def global_params(defaults, algorithms, metrics):
    enabled = {
        (r["metric_code"], r["control_type"]): json_value(r.get("params")) or {}
        for r in algorithms
        if r.get("is_enabled")
    }
    thresholds = {
        r["metric_code"]: json_value(r.get("threshold"))
        for r in metrics
        if isinstance(json_value(r.get("threshold")), dict)
    }
    result = {}
    for metric, types in defaults.items():
        for ct in ("STABLE", "SLOW", "FAST", "LOGIC"):
            value = deepcopy(types.get(ct, {}))
            value.update(enabled.get((metric, ct), {}))
            value.update(thresholds.get(metric, {}))
            result[metric + "|" + ct] = value
    return result


def scope_map(tables, base):
    loops = tables["loop_ledger"]
    mappings = tables["loop_tag_mapping"]
    ids = {r["id"] for r in loops}
    if (
        len(loops) != 1000
        or len(ids) != 1000
        or {r["tag_name"] for r in loops} != {f"PERF-T{i:04d}" for i in range(1, 1001)}
    ):
        raise ValueError(
            "Frozen actual loop set must be exactly the 1000 unique PERF objects"
        )
    if any(
        not r["is_active"] or r["status"] != "READY" or not r["include_in_evaluation"]
        for r in loops
    ):
        raise ValueError("All 1000 must remain eligible")
    if any(r.get("complex_loop_group_id") for r in loops):
        raise ValueError(
            "This frozen capacity scope requires no complex groups; do not invent representative-object mappings"
        )
    if (
        len(mappings) != 7000
        or len({r["tag_id"] for r in mappings}) != 7000
        or len({(r["loop_id"], r["tag_role"]) for r in mappings}) != 7000
        or {r["loop_id"] for r in mappings} != ids
    ):
        raise ValueError("Actual 7000 unique loop-role/point mappings required")
    if len(tables["tag_registry"]) != 7000 or {
        r["id"] for r in tables["tag_registry"]
    } != {r["tag_id"] for r in mappings}:
        raise ValueError(
            "Actual 7000 registered point identities must match the bindings"
        )
    roles = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}
    if any(
        {r["tag_role"] for r in mappings if r["loop_id"] == loop["id"]} != roles
        for loop in loops
    ):
        raise ValueError("Each frozen loop needs the seven exact roles")
    nodes = {n["id"]: n for n in tables["plant_node"]}
    scopes = {}
    templates = {r["id"]: r.get("template_key") for r in tables["engineering_template"]}
    overrides = {
        (r["layer"], r["scope_id"], r["metric_code"]): json_value(r.get("params")) or {}
        for r in tables["config_override"]
        if r.get("is_enabled")
    }
    contexts = []
    effective = []
    for loop in loops:
        node_id = loop.get("unit_id")
        ancestors = []
        seen = set()
        while node_id:
            if node_id in seen or node_id not in nodes:
                raise ValueError("Missing/cyclic structural node")
            seen.add(node_id)
            node = nodes[node_id]
            ancestors.append(node_id)
            if node.get("is_kpi_enabled"):
                scopes.setdefault(node_id, []).append(loop["id"])
            node_id = node.get("parent_id")
        template = templates.get(loop.get("engineering_template_id"))
        resolved = {}
        for key, value in base.items():
            metric = key.rsplit("|", 1)[0]
            params = deepcopy(value)
            for layer, scope in (
                ("TEMPLATE", template),
                ("NODE", loop.get("unit_id")),
                ("LOOP", loop["id"]),
            ):
                params.update(overrides.get((layer, scope, metric), {}))
            resolved[key] = params
        contexts.append(
            {
                "loopId": loop["id"],
                "loopTag": loop["tag_name"],
                "unitId": loop.get("unit_id"),
                "ancestorNodeIds": ancestors,
                "controlType": loop.get("control_type"),
                "loopType": loop.get("loop_type"),
                "templateKey": template,
                "templateId": loop.get("engineering_template_id"),
                "pinnedTemplateVersion": loop.get("engineering_template_version"),
            }
        )
        effective.append({"loopId": loop["id"], "params": resolved})
    return {
        "nodeScopes": {key: sorted(value) for key, value in sorted(scopes.items())},
        "contexts": contexts,
        "effectiveParamsReconstructed": effective,
        "boundary": "Independent reconstruction from full frozen DB + source defaults; not direct observation of Worker pinned task memory. NODE scope uses the real enabled ancestor tree; parameter NODE override uses direct unit as product contract.",
    }


async def snapshot(db):
    async with db.transaction(isolation="repeatable_read", readonly=True):
        identity = dict(
            await db.fetchrow(
                "SELECT current_database() AS database,current_setting('transaction_read_only') AS readonly,current_setting('transaction_isolation') AS isolation,txid_current_snapshot()::text AS snapshot,clock_timestamp()::text AS captured_at"
            )
        )
        table_names = {
            r["table_name"]
            for r in await db.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
        if set(TABLES) - table_names:
            raise ValueError("Required algorithm/input configuration tables missing")
        raw_tables = {}
        for table in TABLES:
            raw_tables[table] = await db.fetchval(
                f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text FROM {table} t"
            )
        raw_tables["sys_config"] = await db.fetchval(
            "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY key),'[]'::jsonb)::text FROM sys_config t WHERE key=ANY($1::text[])",
            list(SYS_KEYS),
        )
        omitted = await db.fetchval(
            "SELECT count(*) FROM sys_config WHERE NOT(key=ANY($1::text[]))",
            list(SYS_KEYS),
        )
    tables = {table: json.loads(raw) for table, raw in raw_tables.items()}
    for value in tables.values():
        reject_secrets(value)
    digest = sha(canonical(tables))
    return {
        "identity": identity,
        "raw": raw_tables,
        "tables": tables,
        "digest": digest,
        "omittedSysConfigCount": omitted,
    }


async def run(args):
    completed = completed_inputs(args.input_audit_progress)
    cfg = dotenv_values(args.env_file)
    private_config(cfg)
    runtime = Path(args.runtime).resolve()
    backend = runtime / "backend"
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=runtime, capture_output=True, text=True
    )
    if git.returncode or git.stdout.strip() != args.candidate_sha:
        raise ValueError("Runtime checkout differs from final candidate")
    target = urlsplit(args.api_base)
    if (
        target.scheme != "http"
        or target.hostname != "127.0.0.1"
        or target.path.rstrip("/") != "/api/v1"
        or target.username
        or target.password
        or target.query
    ):
        raise ValueError("Only explicit local API base allowed")
    token_path = Path(args.token_file)
    if token_path.stat().st_mode & 0o077:
        raise ValueError("Bearer token file must be local mode0600")
    token = token_path.read_text().strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("Raw bearer token file required; do not pass token in argv")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    sql_plan = {
        "transaction": "REPEATABLE READ READ ONLY",
        "tableQueries": {
            table: f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text FROM {table} t"
            for table in TABLES
        },
        "sysConfigQuery": "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY key),'[]'::jsonb)::text FROM sys_config t WHERE key=ANY($1::text[])",
        "sysConfigParameters": list(SYS_KEYS),
        "secretsPolicy": "Only explicit sys_config whitelist values are fetched; URL/token/password/LLM/auth values are neither selected nor archived.",
    }
    write(out / "sql-plan.json", canonical(sql_plan))
    db = await asyncpg.connect(
        host=cfg.get("POSTGRES_HOST", "localhost"),
        port=int(cfg.get("POSTGRES_PORT", 17102)),
        user=cfg.get("POSTGRES_USER", "clpm"),
        password=cfg["POSTGRES_PASSWORD"],
        database=cfg["POSTGRES_DB"],
        server_settings={
            "application_name": "F02-config-freeze-readonly",
            "statement_timeout": "30000",
        },
    )
    report = {
        "startedAt": now(),
        "argv": sys.argv,
        "candidateSha": args.candidate_sha,
        "envFileSha256": sha(Path(args.env_file).read_bytes()),
        "inputAuditProgressSha256": sha(Path(args.input_audit_progress).read_bytes()),
        "inputAudits": completed["results"],
        "status": "BLOCKED",
        "boundary": "No tasks/config writes. API and PG have explicit separate observations with unchanged before/after complete DB bytes; not distributed atomicity, Worker memory or loaded SHA proof.",
    }
    try:
        before = await snapshot(db)
        source_paths = list(SOURCE_FILES) + [
            str(p.relative_to(backend))
            for p in sorted((backend / "app/services/preprocessing").glob("*.py"))
        ]
        source_refs = []
        for relative in sorted(set(source_paths)):
            raw = (backend / relative).read_bytes()
            expected = subprocess.run(
                ["git", "show", args.candidate_sha + ":backend/" + relative],
                cwd=runtime,
                capture_output=True,
            )
            if expected.returncode or expected.stdout != raw:
                raise ValueError(
                    "Runtime source bytes differ from committed candidate: " + relative
                )
            write(out / "sources" / relative, raw)
            source_refs.append(
                {"file": "sources/" + relative, "sha256": sha(raw), "bytes": len(raw)}
            )
        versions = {
            "algorithmVersion": literal_assignment(
                backend / "app/tasks/kpi_calc.py", "ALGORITHM_VERSION"
            ),
            "preprocessVersion": literal_assignment(
                backend / "app/services/preprocessing/pipeline.py", "PREPROCESS_VERSION"
            ),
            "timeQualityPolicyVersion": literal_assignment(
                backend / "app/services/metric_calculator/time_axis.py",
                "TIME_QUALITY_POLICY_VERSION",
            ),
        }
        defaults = literal_assignment(
            backend / "app/services/algorithm_config.py", "_DEFAULTS"
        )
        base = global_params(
            defaults,
            before["tables"]["algorithm_parameter"],
            before["tables"]["metric_config"],
        )
        derived = scope_map(before["tables"], base)
        for table, raw in before["raw"].items():
            write(out / "pg-raw" / (table + ".json"), raw.encode())
            write(
                out / "pg-canonical" / (table + ".json.gz"),
                gzip.compress(canonical(before["tables"][table]), mtime=0),
            )
        write(out / "source-defaults.json", canonical(defaults))
        write(out / "effective-global-reconstructed.json", canonical(base))
        write(
            out / "object-node-contexts.json.gz",
            gzip.compress(canonical(derived), mtime=0),
        )
        revision = max(
            (r["revision"] for r in before["tables"]["config_publication"]), default=0
        )
        responses = {}
        with httpx.Client(
            headers={"Authorization": "Bearer " + token}, timeout=30, trust_env=False
        ) as client:
            health_url = args.api_base.rsplit("/api/v1", 1)[0] + "/health"
            for name, url in [("health", health_url)] + [
                (path.replace("/", "-"), args.api_base.rstrip("/") + "/" + path)
                for path in API_PATHS
            ]:
                began = now()
                response = client.get(url)
                body = response.json()
                reject_secrets(body)
                write(out / "api" / (name + ".raw.json"), response.content)
                responses[name] = {
                    "requestedAt": began,
                    "observedAt": now(),
                    "url": url,
                    "httpStatus": response.status_code,
                    "rawSha256": sha(response.content),
                    "body": body,
                }
                if response.status_code != 200 or (
                    name != "health" and body.get("code") not in (0, "0")
                ):
                    raise ValueError("Required API read failed: " + name)
        after = await snapshot(db)
        api_revision = responses["configs-publish-revision"]["body"]["data"]["revision"]
        api_base = {}
        for metric in responses["configs-algorithm-params"]["body"]["data"]["metrics"]:
            for item in metric["items"]:
                api_base[metric["metricCode"] + "|" + item["controlType"]] = item[
                    "params"
                ]
        if api_revision != revision:
            raise ValueError("API/PG configuration revision mismatch")
        if api_base != base:
            raise ValueError(
                "Actual API merged process view differs from independently reconstructed PG/source defaults"
            )
        if after["digest"] != before["digest"]:
            raise ValueError(
                "Full source/identity/config DB contents changed across API observation interval"
            )
        digest = sha(
            canonical(
                {
                    "versions": versions,
                    "postgresConfigDigest": before["digest"],
                    "sourceFiles": source_refs,
                    "effectiveGlobal": base,
                    "candidateSha": args.candidate_sha,
                }
            )
        )
        report.update(
            status="FROZEN",
            versions=versions,
            configRevision=revision,
            configDigest=digest,
            postgresDigest=before["digest"],
            pgBefore=before["identity"],
            pgAfter=after["identity"],
            api=responses,
            sourceFiles=source_refs,
            loopCount=len(before["tables"]["loop_ledger"]),
            mappingCount=len(before["tables"]["loop_tag_mapping"]),
            predictedNodeCount=len(derived["nodeScopes"]),
            sysConfigWhitelist=list(SYS_KEYS),
            omittedSysConfigCount=before["omittedSysConfigCount"],
            apiGlobalParamsMatch=True,
            fullDbStableAcrossObservation=True,
        )
    except BaseException as exc:
        report.update(
            status="BLOCKED",
            errorType=type(exc).__name__,
            error=str(exc)
            if type(exc) is ValueError
            else "Freeze did not complete; retained safe partial evidence. No secret exception values logged.",
        )
    finally:
        await db.close()
    report["finishedAt"] = now()
    write(out / "config-freeze.json", canonical(report))
    files = [
        {
            "file": str(p.relative_to(out)),
            "bytes": p.stat().st_size,
            "sha256": sha(p.read_bytes()),
        }
        for p in sorted(out.rglob("*"))
        if p.is_file()
    ]
    write(out / "sha256-manifest.json", canonical({"files": files}))
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in (
                    "status",
                    "configRevision",
                    "configDigest",
                    "loopCount",
                    "mappingCount",
                    "predictedNodeCount",
                    "errorType",
                )
            }
        )
    )
    return 0 if report["status"] == "FROZEN" else 2


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input-audit-progress", required=True)
    p.add_argument("--env-file", required=True)
    p.add_argument("--runtime", required=True)
    p.add_argument("--candidate-sha", required=True)
    p.add_argument("--api-base", required=True)
    p.add_argument("--token-file", required=True)
    p.add_argument("--out", required=True)
    raise SystemExit(asyncio.run(run(p.parse_args())))
