"""Offline config-freeze gate, reconstruction, scope, and secret-selection checks."""

from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import pytest
from freeze_capacity_configuration import (
    SYS_KEYS,
    TABLES,
    canonical,
    completed_inputs,
    global_params,
    literal_assignment,
    reject_secrets,
    scope_map,
)


def test_incomplete_twenty_input_gate_refuses_before_connection(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text(
        json.dumps(
            {
                "status": "WAITING_GENERATION",
                "passed": 19,
                "required": 20,
                "results": [],
            }
        )
    )
    with pytest.raises(ValueError, match="All 20"):
        completed_inputs(path)


def complete_artifacts(tmp_path):
    rows = []
    plans = []
    loops = [{"id": f"loop-{i}", "tag_name": f"PERF-T{i:04d}"} for i in range(1, 1001)]
    mappings = [
        {"loop_id": row["id"], "tag_role": role, "tag_id": row["id"] + "-" + role}
        for row in loops
        for role in ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]
    ]
    for i in range(1, 21):
        out = tmp_path / str(i)
        out.mkdir()
        start = datetime(2026, 10, 15, 12) + timedelta(hours=i - 1)
        end = start + timedelta(hours=1)
        scenario = "a" if i <= 10 else "b"
        cache = "cold" if i <= 5 or 11 <= i <= 15 else "hot"
        name = f"manifest_{scenario}_{start.strftime('%Y%m%d%H')}.json"
        plan = {
            "batch": i,
            "scenario": scenario,
            "cache": cache,
            "ts_start": start.isoformat(),
            "ts_end": end.isoformat(),
            "manifest_name": name,
            "expected_loops": 1000,
            "slo_seconds": 600,
        }
        plans.append(plan)
        manifest = canonical(
            {
                "scenario": scenario,
                "windowStart": start.isoformat(),
                "rows": [
                    {
                        "loopTag": r["tag_name"],
                        "windowStart": start.isoformat(),
                        "windowEnd": end.isoformat(),
                        "scenario": scenario,
                    }
                    for r in loops
                ],
            }
        )
        (out / "input-manifest.json").write_bytes(manifest)
        (out / name).write_bytes(manifest)
        (out / "pg-input-identity.json").write_bytes(
            canonical({"loops": loops, "mappings": mappings})
        )
        report = canonical(
            {
                "status": "PASS",
                "manifestSha256": hashlib.sha256(manifest).hexdigest(),
                "windowStart": start.isoformat(),
                "windowEnd": end.isoformat(),
                "globalEligible": 1000,
                "pointCount": 7000,
                "failures": [],
                "argv": ["audit_capacity_inputs.py", "--manifest", str(out / name)],
            }
        )
        (out / "input-audit.json").write_bytes(report)
        rows.append(
            {
                "batch": i,
                "status": "PASS",
                "rc": 0,
                "existingOutput": str(out),
                "manifestSha256": hashlib.sha256(manifest).hexdigest(),
                "reportSha256": hashlib.sha256(report).hexdigest(),
            }
        )
    path = tmp_path / "progress.json"
    path.write_bytes(
        canonical(
            {"status": "ALL_INPUTS_PASS", "passed": 20, "required": 20, "results": rows}
        )
    )
    (tmp_path / "planned-batches.json").write_bytes(canonical(plans))
    return path


def test_complete_gate_rechecks_all_raw_audits_and_manifests(tmp_path):
    path = complete_artifacts(tmp_path)
    assert completed_inputs(path, tmp_path / "planned-batches.json")["passed"] == 20
    (tmp_path / "11/input-manifest.json").write_text("tampered")
    with pytest.raises(ValueError, match="identity"):
        completed_inputs(path, tmp_path / "planned-batches.json")


def test_default_table_metric_override_order_and_disabled_rows():
    defaults = {
        "oscillation_rate": {
            ct: {"x": 1, "y": 2} for ct in ["STABLE", "SLOW", "FAST", "LOGIC"]
        }
    }
    algo = [
        {
            "metric_code": "oscillation_rate",
            "control_type": "STABLE",
            "params": {"x": 3},
            "is_enabled": True,
        },
        {
            "metric_code": "oscillation_rate",
            "control_type": "FAST",
            "params": {"x": 999},
            "is_enabled": False,
        },
    ]
    metrics = [{"metric_code": "oscillation_rate", "threshold": {"y": 4}}]
    merged = global_params(defaults, algo, metrics)
    assert merged["oscillation_rate|STABLE"] == {"x": 3, "y": 4}
    assert merged["oscillation_rate|FAST"] == {"x": 1, "y": 4}
    assert defaults["oscillation_rate"]["STABLE"] == {"x": 1, "y": 2}


def test_scope_whole_denominator_and_template_node_loop_precedence():
    loops = []
    maps = []
    for i in range(1, 1001):
        lid = f"l-{i}"
        loops.append(
            {
                "id": lid,
                "tag_name": f"PERF-T{i:04d}",
                "is_active": True,
                "status": "READY",
                "include_in_evaluation": True,
                "unit_id": "unit",
                "control_type": "FAST",
                "engineering_template_id": "tpl",
                "engineering_template_version": 2,
            }
        )
        for role in ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"]:
            maps.append({"loop_id": lid, "tag_id": lid + "-" + role, "tag_role": role})
    tables = {
        "loop_ledger": loops,
        "loop_tag_mapping": maps,
        "plant_node": [
            {"id": "unit", "parent_id": "factory", "is_kpi_enabled": True},
            {"id": "factory", "parent_id": None, "is_kpi_enabled": False},
        ],
        "engineering_template": [{"id": "tpl", "template_key": "K"}],
        "config_override": [
            {
                "layer": layer,
                "scope_id": scope,
                "metric_code": "m",
                "is_enabled": True,
                "params": {"x": value},
            }
            for layer, scope, value in [
                ("TEMPLATE", "K", 2),
                ("NODE", "unit", 3),
                ("LOOP", "l-1", 4),
            ]
        ],
    }
    tables["tag_registry"] = [{"id": m["tag_id"]} for m in maps]
    result = scope_map(tables, {"m|FAST": {"x": 1}})
    assert len(result["nodeScopes"]["unit"]) == 1000
    assert result["effectiveParamsReconstructed"][0]["params"]["m|FAST"] == {"x": 4}
    assert result["effectiveParamsReconstructed"][1]["params"]["m|FAST"] == {"x": 3}
    assert result["contexts"][0]["pinnedTemplateVersion"] == 2
    tables["loop_tag_mapping"].append(deepcopy(maps[0]))
    with pytest.raises(ValueError, match="7000"):
        scope_map(tables, {"m|FAST": {"x": 1}})


def test_sys_config_whitelist_excludes_real_secret_sources():
    assert not any(
        any(
            word in key
            for word in ["token", "password", "secret", "url", "endpoint", "llm."]
        )
        for key in SYS_KEYS
    )
    assert "sys_user" not in TABLES and "sys_authz_policy" not in TABLES
    for bad in [
        {"password": "sentinel"},
        {"api_token": "sentinel"},
        {"nested": '{"secret":"sentinel"}'},
    ]:
        with pytest.raises(ValueError, match="Sensitive"):
            reject_secrets(bad)
    reject_secrets({"threshold": {"ratio": 0.05}, "metric_code": "oscillation_rate"})


def test_ast_only_default_read_does_not_execute_module(tmp_path):
    path = tmp_path / "source.py"
    path.write_text(
        "raise RuntimeError('DO NOT EXECUTE')\n_DEFAULTS: dict = {'m': {'STABLE': {'x': 2}}}\n"
    )
    assert literal_assignment(path, "_DEFAULTS") == {"m": {"STABLE": {"x": 2}}}


def test_twenty_labels_cannot_alias_one_valid_full_thousand_object_window(tmp_path):
    path = complete_artifacts(tmp_path)
    progress = json.loads(path.read_text())
    first = progress["results"][0]
    progress["results"] = [dict(first, batch=i) for i in range(1, 21)]
    path.write_bytes(canonical(progress))
    with pytest.raises(ValueError, match="Repeated original"):
        completed_inputs(path, tmp_path / "planned-batches.json")


def test_twenty_distinct_dirs_cannot_remap_actual_window_to_wrong_planned_batch(
    tmp_path,
):
    path = complete_artifacts(tmp_path)
    progress = json.loads(path.read_text())
    progress["results"][0]["batch"], progress["results"][1]["batch"] = 2, 1
    path.write_bytes(canonical(progress))
    with pytest.raises(ValueError, match="planned manifest|window or scenario"):
        completed_inputs(path, tmp_path / "planned-batches.json")
