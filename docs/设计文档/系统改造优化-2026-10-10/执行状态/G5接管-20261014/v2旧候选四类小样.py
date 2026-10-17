"""Read-only G5 input audit; all outbound sockets blocked; IO edges in memory."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import socket
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.dont_write_bytecode = True
os.environ.update(CLPM_TEST_MODE="1", CLPM_SKIP_EXIT_HOOKS="1", DEBUG="true", JWT_SECRET_KEY="offline-g5-audit-no-network-secret-key")
ROOT = Path("/Users/zhangping/DEV/CLPM-MVP-p1int")
sys.path.insert(0, str(ROOT / "backend"))
NETWORK_ATTEMPTS = []
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex

def blocked_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        NETWORK_ATTEMPTS.append(str(address))
        raise RuntimeError("G5 offline audit blocks outbound sockets")
    return _connect(self, address)

def blocked_connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        NETWORK_ATTEMPTS.append(str(address))
        raise RuntimeError("G5 offline audit blocks outbound sockets")
    return _connect_ex(self, address)

socket.socket.connect = blocked_connect
socket.socket.connect_ex = blocked_connect_ex

from app.contracts.data_types import LoopPreprocessConfig
from app.services.data_planner import DataPlanner, _REQUIREMENT_CODE_ALIASES
from app.services.data_source import logical_wide_builder as lwb
from app.services.metric_data_bundle import MetricDataBundleAssembler
from app.services.preprocessing.thresholds import get_threshold
from app.tasks import kpi_calc as kpi
from scripts.generate_capacity_dataset import (
    DATASET_VERSION, LOOP_TYPES, RANGES, _response_category,
    build_manifest_rows, synth_series,
)

logging.getLogger().setLevel(logging.ERROR)
T0 = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)

def source_requirements():
    # Read the current fresh-install source rows, not a live DB or guessed masks.
    seed = (ROOT / "db/postgresql/02_seed_data.sql").read_text()
    block = seed.split("INSERT INTO clpm_metric_data_requirement", 1)[1].split("ON CONFLICT", 1)[0]
    block = re.sub(r"--[^\n]*", "", block)
    cols = ["metric_code", "metric_name", "tag_group", "tags", "sampling_strategy", "quality_policy", "mask_expression", "aggregation_policy", "depends_on"]
    rows = {}
    for match in re.finditer(r"\((?:[^()']|'[^']*')+\)", block):
        try:
            values = ast.literal_eval(match.group().replace("NULL", "None"))
        except (SyntaxError, ValueError):
            continue
        if len(values) != 9:
            continue
        row = dict(zip(cols, values, strict=True))
        row["tags"] = json.loads(row["tags"])
        rows[row["metric_code"]] = SimpleNamespace(**row)
    tc = ROOT / "backend/alembic/versions/f5timec001tc_seed_time_constant_requirement.py"
    sql = tc.read_text().split("INSERT INTO clpm_metric_data_requirement", 1)[1].split("ON CONFLICT", 1)[0]
    values = ast.literal_eval(re.search(r"\('time_constant'.*?\)", sql, re.S).group().replace("NULL", "None"))
    row = dict(zip(cols, values, strict=True))
    row["tags"] = json.loads(row["tags"])
    rows[row["metric_code"]] = SimpleNamespace(**row)
    assert len(rows) == 27, len(rows)
    return rows

REQUIREMENTS = source_requirements()

async def one_case(i, composition, *, min_ratio=None, fast=60.0, slow=7200.0, registered_gap=False):
    loop_type = LOOP_TYPES[(i - 1) % 4]
    ct = kpi._loop_type_to_control_type(loop_type)
    span = RANGES[loop_type][1]
    category = _response_category(i)
    series = synth_series(i, composition, category, span)
    loop_id = f"offline-{i}-{composition}"
    by_pid = {}
    for role, values in series.items():
        by_pid[role.lower()] = [dict(ts=T0 + timedelta(seconds=t), value=v, quality_class=q, quality_raw=qr) for t, v, q, qr in values]

    async def segments(_db, _loop, roles, start, end):
        return {r.upper(): [SimpleNamespace(point_id=r.lower(), start=start, end=end + timedelta(seconds=1), initial_check=start, binding_version=1)] for r in roles}

    async def events(point_ids, start, end, **_kw):
        return {pid: [e for e in by_pid[pid] if start <= e["ts"] <= end] for pid in point_ids}

    async def limits(step):
        return {"fast": max(3.0 * step, fast), "slow": max(3.0 * step, slow)}

    query_records = []
    async def query(**kwargs):
        raw = await lwb.build_logical_wide(None, kwargs["loop_id"], kwargs["tag_roles"], kwargs["start"], kwargs["end"], kwargs["interval_s"])
        ctx = raw.series_context
        query_records.append({
            "roles": sorted(kwargs["tag_roles"]), "interval_s": kwargs["interval_s"],
            "timestamps": len(raw.timestamps), "ctx_period": ctx.grid_period_s,
            "expected_slots": ctx.expected_slots,
            "pv_known_slots": ctx.known_slots("pv"),
            "pv_unknown_slots": ctx.unknown_slots("pv"),
            "pv_source_coverage_ratio": ctx.source_coverage_ratio("pv"),
            "unknown_reasons": ctx.unknown_reasons,
        })
        return raw

    async def cfg(_loop, _ct):
        return LoopPreprocessConfig(loop_id=loop_id, control_type=ct, range_min=0.0, range_max=span, op_range_min=0.0, op_range_max=100.0)

    planner = DataPlanner(None, query, MetricDataBundleAssembler(), config_loader=cfg)
    planner._preloaded_op_limits = {loop_id: (0.0, 100.0)}
    planner._resolve_data_version = AsyncMock(return_value="offline-point:tqpv3")
    async def reqs(codes):
        return {code: REQUIREMENTS[_REQUIREMENT_CODE_ALIASES.get(code, code)] for code in codes if _REQUIREMENT_CODE_ALIASES.get(code, code) in REQUIREMENTS}
    planner._load_requirements = reqs
    observed_bundles = []
    real_request = planner.request_bundles
    async def collect(**kwargs):
        bundles = await real_request(**kwargs)
        observed_bundles.extend(bundles)
        return bundles
    planner.request_bundles = collect
    captured = {}
    async def persist(**kwargs):
        captured.update({k: v for k, v in kwargs.items() if k != "db"})
        return {"status": kwargs["status"], "score": float(kwargs["score"]) if kwargs.get("score") is not None else None}

    gaps = [(T0 + timedelta(seconds=900), T1)] if registered_gap else []
    loop = SimpleNamespace(id=loop_id, tag_name=f"PERF-T{i:04d}", loop_type=loop_type, ideal_settling_time=None, unit_id=None, engineering_template_id=None)
    with (
        patch.object(lwb, "_resolve_role_segments", segments),
        patch.object(lwb, "_load_gap_windows", AsyncMock(return_value=gaps)),
        patch.object(lwb, "_held_limits_s", limits),
        patch.object(lwb.repo, "read_events", events),
        patch.object(lwb.repo, "read_last_states_before", AsyncMock(return_value={})),
        patch.object(lwb.meta, "applicable_anchors_batch", AsyncMock(return_value={})),
        patch.object(kpi, "_persist_snapshot", persist),
        patch.object(kpi, "_resolve_loop_pinned_params", AsyncMock(return_value=({}, {}))),
    ):
        result = await kpi._calculate_loop_kpi(
            db=None, loop=loop, metric_configs={}, ts_start=T0, ts_end=T1, data_planner=planner,
            loop_cfg={"range_min": 0, "range_max": span, "op_lower": 0, "op_upper": 100},
            source="hourly", source_task_id="offline-probe", logical_run_id="offline-probe",
            runtime_overrides={"fitness": {}, "kpiMinObservedRatio": min_ratio},
            pinned_config={"configRevision": 1},
        )
    base = next(b.data_block for b in observed_bundles if b.data_block.tag_group == "BASE")
    out = dict(i=i, composition=composition, loop_type=loop_type, category=category,
               algorithm_version=kpi.ALGORITHM_VERSION, generator_version=DATASET_VERSION,
               base_interval_s=get_threshold(ct).base_sampling_freq,
               min_observed_ratio=min_ratio, held_fast_s=fast, held_slow_s=slow,
               registered_gap=registered_gap, raw_role_counts={r:len(v) for r,v in series.items()},
               quality_counts=dict(Counter(row[2] for row in series["PV"])),
               series_sha256=hashlib.sha256(json.dumps(series, sort_keys=True).encode()).hexdigest(),
               result=result, persisted={key: captured.get(key) for key in ("status", "score", "confidence_level", "valid_rate", "algorithm_version", "source", "fitness_level", "fitness_detail", "good_value_rate", "auto_mode_rate", "effective_auto_rate", "steady_rate", "accuracy_rate", "fast_rate")},
               base={"points":base.point_count,"valid_rate":base.loop_valid_rate,"confidence":base.loop_confidence_level,"missing_rate":base.quality_summary.missing_rate,"unknown_seconds":base.quality_summary.unknown_seconds,"segments":base.consecutive_segments,"pv_invalid":sum(not x for x in base.validity["pv_valid"])},
               queries=query_records)
    return out

async def main():
    cases = []
    # Freeze independent expectations BEFORE execution. EVALUATED maps SUCCESS.
    # All four physical loop types, one seed each per class; noisy seed 949 regression.
    specs = [(1,"normal"),(2,"normal"),(3,"normal"),(4,"normal"),(949,"normal"),
             (801,"insufficient"),(802,"insufficient"),(803,"insufficient"),(804,"insufficient"),
             (901,"manual"),(902,"manual"),(903,"manual"),(904,"manual"),
             (951,"gap"),(952,"gap"),(953,"gap"),(954,"gap")]
    for i, comp in specs:
        out = await one_case(i, comp)
        out["independent_expected"] = "INCONCLUSIVE" if comp in ("insufficient","gap") else "SUCCESS"
        out["matches_independent_expected"] = out["result"]["status"] == out["independent_expected"]
        cases.append(out)
        print(json.dumps({k:out[k] for k in ("i","composition","loop_type","result","matches_independent_expected")},ensure_ascii=False), flush=True)
    # Counterfactuals test configuration effects only; never rewrite old batch expectations.
    for i in (952,953,954):
        out = await one_case(i,"gap",min_ratio=0.7)
        out["independent_expected"] = "INCONCLUSIVE"
        out["matches_independent_expected"] = out["result"]["status"] == "INCONCLUSIVE"
        cases.append(out)
        print(json.dumps({k:out[k] for k in ("i","composition","loop_type","min_observed_ratio","result","matches_independent_expected")},ensure_ascii=False), flush=True)
    for i in (952,953):
        out = await one_case(i,"gap",min_ratio=0.7,registered_gap=True)
        out["independent_expected"] = "INCONCLUSIVE"
        out["matches_independent_expected"] = out["result"]["status"] == "INCONCLUSIVE"
        cases.append(out)
        print(json.dumps({k:out[k] for k in ("i","composition","loop_type","min_observed_ratio","registered_gap","result","matches_independent_expected")},ensure_ascii=False), flush=True)
    assert not NETWORK_ATTEMPTS, NETWORK_ATTEMPTS
    report = {"source_head":"e07dcf8019ea0cb65cfbe1ea0f9c1e108bc6d252","network_attempts":NETWORK_ATTEMPTS,
              "boundary":"real synth/builder/DataPlanner/Pipeline/masks/calculators/KPI decision; source IO and persist captured in memory; no runtime or DB evidence",
              "cases":cases}
    Path("/private/tmp/g5-v2-offline-probe-20261014.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))

if __name__ == "__main__":
    asyncio.run(main())
