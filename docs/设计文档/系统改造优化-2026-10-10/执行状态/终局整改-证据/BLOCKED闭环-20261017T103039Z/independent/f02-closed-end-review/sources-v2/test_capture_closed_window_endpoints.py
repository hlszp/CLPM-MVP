"""Pure file/response fixtures only; no sockets, real DB, TD or service startup."""

from datetime import datetime, timezone
import gzip
import json
import uuid
from types import SimpleNamespace
import pytest
from capture_closed_window_endpoints import (
    build_queries,
    evaluate_chunk,
    exact_integer,
    project_rows,
    capture_query,
    extra_gate,
)

END = "2026-10-16T09:00:00"
MS = int(datetime.fromisoformat(END).replace(tzinfo=timezone.utc).timestamp() * 1000)


def ids(n=7):
    return [str(uuid.UUID(int=i + 1)) for i in range(n)]


def identity():
    points = ids()
    return [{"id": "loop", "tag_name": "PERF-T0004"}], [
        {"loop_id": "loop", "tag_id": point, "tag_role": role}
        for point, role in zip(
            points, ["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"], strict=True
        )
    ]


def row(point):
    return {
        "point_id": point,
        "ts": "2026-10-16T17:00:00+08:00",
        "ts_epoch_ms": MS,
        "value": 0.0,
        "quality_class": 0,
        "quality_raw": 0,
        "quality_schema": "native",
        "source_kind": 4,
        "payload_hash": "real-hash",
    }


def test_exact_endpoint_all_ids_and_no_limit_with_matching_count_query():
    queries = build_queries("clpm_capacity_20261017_s01", ids(500), END)
    assert "ts = '2026-10-16T09:00:00.000Z'" in queries["data"]
    assert "CAST(ts AS BIGINT) AS ts_epoch_ms" in queries["data"]
    assert "LIMIT" not in queries["data"].upper() and "COUNT(*)" in queries["count"]
    assert queries["data"].split(" WHERE ")[1] == queries["count"].split(" WHERE ")[1]
    for point in ids(500):
        assert point in queries["data"]


@pytest.mark.parametrize(
    "db,points",
    [
        ("clpm_ts", ids()),
        ("clpm_capacity_x;DROP", ids()),
        ("clpm_capacity_x", ids(501)),
        ("clpm_capacity_x", [ids(1)[0]] * 2),
    ],
)
def test_shared_invalid_or_oversized_point_query_is_refused(db, points):
    with pytest.raises(ValueError):
        build_queries(db, points, END)


def test_utc_and_numeric_qc_zero_values_are_actual_not_missing():
    loops, mappings = identity()
    rows = [row(point) for point in ids()]
    assert evaluate_chunk(ids(), END, 7, rows) == []
    projected, missing, failures = project_rows(loops, mappings, rows, True)
    assert not missing and not failures and all(item["present"] for item in projected)
    assert (
        projected[0]["rawRows"][0]["value"] == 0.0
        and projected[0]["rawRows"][0]["quality_class"] == 0
    )


def test_first_twenty_three_endpoint_missing_is_recorded_rework_without_fill():
    loops, mappings = identity()
    projected, missing, failures = project_rows(loops, mappings, [], True)
    assert len(missing) == 7 and failures
    assert all(item["rawRows"] == [] and "value" not in item for item in projected)


def test_final_unknown_endpoint_zero_is_recorded_and_never_held_or_generated():
    loops, mappings = identity()
    projected, missing, failures = project_rows(loops, mappings, [], False)
    assert (
        len(missing) == 7
        and failures == []
        and all(item["present"] is False for item in projected)
    )


def test_count_proves_data_response_is_not_silently_truncated():
    rows = [row(point) for point in ids(6)]
    assert any("COUNT differs" in item for item in evaluate_chunk(ids(), END, 7, rows))
    assert any(
        "Duplicate actual point" in item
        for item in evaluate_chunk(ids(), END, 2, [row(ids()[0]), row(ids()[0])])
    )


def test_other_point_wrong_epoch_and_precision_are_rejected():
    assert any(
        "outside" in item for item in evaluate_chunk(ids(1), END, 1, [row(ids(2)[1])])
    )
    actual = row(ids(1)[0])
    actual["ts_epoch_ms"] = MS * 1000
    assert any("precision" in item for item in evaluate_chunk(ids(1), END, 1, [actual]))


def test_exact_bigint_strings_allowed_without_rounding_actual_raw_values():
    actual = row(ids(1)[0])
    actual["ts_epoch_ms"] = str(MS)
    assert evaluate_chunk(ids(1), END, "1", [actual]) == []
    assert actual["ts_epoch_ms"] == str(MS)
    for value in [1.1, True, "1.5"]:
        with pytest.raises(ValueError):
            exact_integer(value)


class FakeClient:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def post(self, *_, **__):
        raw = json.dumps(self.body).encode()
        return SimpleNamespace(
            content=raw, status_code=self.status, json=lambda: self.body
        )


def test_original_response_and_canonical_rows_restorable(tmp_path):
    body = {
        "code": 0,
        "column_meta": [
            ["point_id", "BINARY", 36],
            ["ts_epoch_ms", "BIGINT", 8],
            ["value", "DOUBLE", 8],
        ],
        "data": [[ids(1)[0], MS, 0]],
        "rows": 1,
    }
    metadata, rows = capture_query(
        FakeClient(body),
        "http://127.0.0.1:1/rest/sql",
        "SELECT exact",
        tmp_path,
        "data",
    )
    assert metadata["rc"] == 0 and rows[0]["value"] == 0
    restored = gzip.decompress((tmp_path / metadata["rawFile"]).read_bytes())
    assert json.loads(restored) == body
    assert (tmp_path / "data.command.json").exists()


def test_resource_query_error_preserved_not_substituted_as_empty_data(tmp_path):
    body = {"code": 1850, "desc": "Query memory exhausted"}
    metadata, rows = capture_query(
        FakeClient(body),
        "http://127.0.0.1:1/rest/sql",
        "SELECT exact",
        tmp_path,
        "error",
    )
    assert metadata["rc"] == 1 and metadata["tdCode"] == 1850 and rows is None
    assert (
        json.loads(gzip.decompress((tmp_path / metadata["rawFile"]).read_bytes()))
        == body
    )


def test_extra_inputs_must_all_be_complete_before_any_network(tmp_path):
    plans = [
        {"purpose": purpose, "scenario": scenario, "formal20Denominator": False}
        for purpose, scenario in [
            ("fault-recovery", "a"),
            ("fault-persistent", "a"),
            ("independent-cold", "b"),
            ("independent-hot", "b"),
        ]
    ]
    (tmp_path / "plan.json").write_text(json.dumps({"inputs": plans}))
    (tmp_path / "complete.json").write_text(
        json.dumps({"preparedAndAudited": 3, "results": [], "formal20Executed": False})
    )
    with pytest.raises(ValueError, match="All four"):
        extra_gate(tmp_path / "plan.json", tmp_path / "complete.json", {})


def complete_extra_fixture(tmp_path):
    from datetime import timedelta
    from freeze_capacity_configuration import canonical, sha

    loops = [
        {
            "id": f"loop-{i}",
            "tag_name": f"PERF-T{i:04d}",
            "is_active": True,
            "status": "READY",
            "include_in_evaluation": True,
        }
        for i in range(1, 1001)
    ]
    mappings = [
        {
            "loop_id": loop["id"],
            "tag_role": role,
            "tag_id": str(uuid.UUID(int=i * 7 + j + 1)),
        }
        for i, loop in enumerate(loops)
        for j, role in enumerate(["PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"])
    ]
    completed = {
        "verifiedLoopIdentity": sorted((r["id"], r["tag_name"]) for r in loops),
        "verifiedPointBindings": sorted(
            (r["loop_id"], r["tag_role"], r["tag_id"]) for r in mappings
        ),
    }
    planned = []
    results = []
    for index, (purpose, scenario) in enumerate(
        [
            ("fault-recovery", "a"),
            ("fault-persistent", "a"),
            ("independent-cold", "b"),
            ("independent-hot", "b"),
        ]
    ):
        start = datetime(2026, 10, 16, 8 + index)
        end = start + timedelta(hours=1)
        plan = {
            "purpose": purpose,
            "scenario": scenario,
            "ts_start": start.isoformat(),
            "ts_end": end.isoformat(),
            "formal20Denominator": False,
        }
        planned.append(plan)
        out = tmp_path / purpose
        out.mkdir()
        audit = out / "audit"
        audit.mkdir()
        manifest = {
            "scenario": scenario,
            "windowStart": start.isoformat(),
            "rows": [
                {
                    "loopTag": r["tag_name"],
                    "scenario": scenario,
                    "windowStart": start.isoformat(),
                    "windowEnd": end.isoformat(),
                }
                for r in loops
            ],
        }
        manifest_path = out / f"manifest_{scenario}_{start:%Y%m%d%H}.json"
        raw = canonical(manifest)
        manifest_path.write_bytes(raw)
        (audit / "input-manifest.json").write_bytes(raw)
        report = {
            "status": "PASS",
            "manifestSha256": sha(raw),
            "windowStart": start.isoformat(),
            "windowEnd": end.isoformat(),
            "globalEligible": 1000,
            "pointCount": 7000,
            "failures": [],
            "argv": ["audit.py", "--manifest", str(manifest_path)],
        }
        (audit / "input-audit.json").write_bytes(canonical(report))
        (audit / "pg-input-identity.json").write_bytes(
            canonical({"loops": loops, "mappings": mappings})
        )
        (out / "generation-command.json").write_bytes(
            canonical({"input": plan, "commands": [{"rc": 0}]})
        )
        results.append(
            {
                "input": plan,
                "manifest": str(manifest_path),
                "manifestSha256": sha(raw),
                "auditRc": 0,
                "auditReportSha256": sha(canonical(report)),
            }
        )
    plan_path = tmp_path / "plan.json"
    complete_path = tmp_path / "complete.json"
    plan_path.write_bytes(canonical({"inputs": planned}))
    complete_path.write_bytes(
        canonical(
            {"preparedAndAudited": 4, "formal20Executed": False, "results": results}
        )
    )
    return plan_path, complete_path, completed


def test_four_distinct_real_shape_originals_and_generation_links_accept(tmp_path):
    plan, complete, identity = complete_extra_fixture(tmp_path)
    actual, done = extra_gate(plan, complete, identity)
    assert len(actual) == 4 and done["preparedAndAudited"] == 4


def test_extra_audit_manifest_crosslink_mismatch_rejected_even_when_report_hash_updated(
    tmp_path,
):
    from freeze_capacity_configuration import canonical, sha

    plan, complete, identity = complete_extra_fixture(tmp_path)
    path = tmp_path / "fault-recovery/audit/input-audit.json"
    report = json.loads(path.read_text())
    report["manifestSha256"] = "0" * 64
    path.write_bytes(canonical(report))
    done = json.loads(complete.read_text())
    done["results"][0]["auditReportSha256"] = sha(path.read_bytes())
    complete.write_bytes(canonical(done))
    with pytest.raises(ValueError, match="report manifest identity"):
        extra_gate(plan, complete, identity)


def test_extra_generation_failure_is_not_hidden_by_green_audit(tmp_path):
    from freeze_capacity_configuration import canonical

    plan, complete, identity = complete_extra_fixture(tmp_path)
    path = tmp_path / "fault-recovery/generation-command.json"
    body = json.loads(path.read_text())
    body["commands"][0]["rc"] = 1
    path.write_bytes(canonical(body))
    with pytest.raises(ValueError, match="generation command"):
        extra_gate(plan, complete, identity)
