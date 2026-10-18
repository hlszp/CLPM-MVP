"""Read actual saved TD HTTP bytes independently; never import capture helpers."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path


COLS = (
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
PROJECTION = "point_id, ts, CAST(ts AS BIGINT) AS ts_epoch_ms, `value`, quality_class, quality_raw, quality_schema, source_kind, payload_hash"
ROLES = {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


class Reader:
    def __init__(self, root):
        self.root = root.resolve()
        self.reads = {}

    def raw(self, name):
        path = (self.root / name).resolve()
        assert path.is_relative_to(self.root), f"Reference escapes capture root: {name}"
        raw = path.read_bytes()
        self.reads[str(path.relative_to(self.root))] = digest(raw)
        return raw

    def json(self, name):
        raw = self.raw(name)
        return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)

    def unchanged(self):
        return all(
            digest((self.root / name).read_bytes()) == sha
            for name, sha in self.reads.items()
        )


def original_response(reader, directory, stem, expected_sql, columns):
    prefix = directory + "/" + stem
    command = reader.json(prefix + ".command.json")
    sql = reader.raw(prefix + ".sql")
    compressed = reader.raw(prefix + ".response.raw.json.gz")
    raw = gzip.decompress(compressed)
    assert sql == expected_sql.encode(), f"Exact SQL changed: {prefix}"
    assert digest(sql) == command["sqlSha256"]
    assert digest(raw) == command["rawSha256"]
    assert digest(compressed) == command["gzipSha256"]
    assert (
        command["rc"] == 0 and command["httpStatus"] == 200 and command["tdCode"] == 0
    )
    assert (
        command["method"] == "POST"
        and command["url"] == "http://localhost:17115/rest/sql"
    )
    assert (
        command["sqlFile"] == stem + ".sql"
        and command["rawFile"] == stem + ".response.raw.json.gz"
    )
    body = json.loads(raw)
    assert body["code"] == 0
    assert tuple(meta[0] for meta in body["column_meta"]) == tuple(columns)
    assert len({meta[0] for meta in body["column_meta"]}) == len(columns)
    assert isinstance(body["data"], list) and all(
        isinstance(row, list) and len(row) == len(columns) for row in body["data"]
    )
    assert (
        type(body["rows"]) is int
        and body["rows"] == len(body["data"]) == command["rowCount"]
    )
    rows = [dict(zip(columns, row, strict=True)) for row in body["data"]]
    stored = reader.json(directory + "/" + command["canonicalRowsFile"])
    assert stored == rows, f"Canonical file loses original fields: {prefix}"
    assert digest(canonical(stored)) == command["canonicalRowsSha256"]
    assert instant(command["startedAt"]) <= instant(command["finishedAt"])
    return rows


def verify(capture, normal_plan, extra_plan, original_identity):
    reader = Reader(capture)
    manifest = reader.json("sha256-manifest.json")
    entries = manifest["files"]
    names = [entry["file"] for entry in entries]
    actual = {
        str(path.relative_to(capture))
        for path in capture.rglob("*")
        if path.is_file() and path.name != "sha256-manifest.json"
    }
    assert len(names) == len(set(names)) and set(names) == actual
    for entry in entries:
        raw = reader.raw(entry["file"])
        assert digest(raw) == entry["sha256"] and len(raw) == entry["bytes"], entry[
            "file"
        ]
    report = reader.json("endpoint-capture.json")
    assert (
        report["status"] == "CAPTURED"
        and report["normalDenominator"] == 20
        and report["separateExtraInputs"] == 4
    )
    normal_raw, extra_raw = normal_plan.read_bytes(), extra_plan.read_bytes()
    normal, extra = json.loads(normal_raw), json.loads(extra_raw)
    normal = normal["batches"] if isinstance(normal, dict) else normal
    extra = extra["inputs"]
    assert report["sourceIdentity"]["normalPlanSha256"] == digest(normal_raw)
    assert report["sourceIdentity"]["extraPlanSha256"] == digest(extra_raw)
    expected = sorted(
        [
            dict(row, purpose=f"normal-{row['batch']:02d}", formal20Denominator=True)
            for row in normal
        ]
        + extra,
        key=lambda row: instant(row["ts_start"]),
    )
    plan = reader.json("combined-24-window-plan.json")
    assert plan == expected and len(plan) == 24 and len(report["windows"]) == 24
    assert sum(row["formal20Denominator"] is True for row in plan) == 20
    assert all(
        instant(a["ts_end"]) == instant(b["ts_start"]) for a, b in zip(plan, plan[1:])
    )
    identity = reader.json("postgres-identity.json")
    original_identity_raw = original_identity.read_bytes()
    old_identity = json.loads(original_identity_raw)
    external_hashes = {
        normal_plan: digest(normal_raw),
        extra_plan: digest(extra_raw),
        original_identity: digest(original_identity_raw),
    }
    assert (
        identity["transaction"]["readonly"] == "on"
        and identity["globalEligible"] == 1000
    )
    database = identity["transaction"]["database"]
    assert database == "clpm_capacity_20261017_s01"
    loops, mappings = identity["loops"], identity["mappings"]
    tags = {row["id"]: row["tag_name"] for row in loops}
    assert len(loops) == len(tags) == 1000 and set(tags.values()) == {
        f"PERF-T{i:04d}" for i in range(1, 1001)
    }
    assert all(
        row["is_active"] and row["status"] == "READY" and row["include_in_evaluation"]
        for row in loops
    )
    assert sorted((row["id"], row["tag_name"]) for row in loops) == sorted(
        (row["id"], row["tag_name"]) for row in old_identity["loops"]
    )

    def binding(row):
        return row["loop_id"], row["tag_role"], row["tag_id"]

    assert sorted(map(binding, mappings)) == sorted(
        map(binding, old_identity["mappings"])
    )
    points = {
        row["tag_id"]: (row["loop_id"], tags[row["loop_id"]], row["tag_role"])
        for row in mappings
    }
    assert len(mappings) == len(points) == 7000
    assert all(
        {row["tag_role"] for row in mappings if row["loop_id"] == loop} == ROLES
        for loop in tags
    )
    point_ids = sorted(points)
    summaries = []
    query_count = 0
    for index, window in enumerate(plan, 1):
        directory = f"{index:02d}-{window['purpose']}"
        summary = reader.json(directory + "/endpoint-summary.json")
        assert summary["window"] == window and summary["actualMappedPoints"] == 7000
        assert (
            summary["endpoint"] == window["ts_end"]
            and summary["status"] == "CAPTURED"
            and summary["failures"] == []
        )
        assert summary["nextGeneratedWindowExists"] is (index < 24)
        endpoint = instant(window["ts_end"])
        expected_ms = int(
            (endpoint - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
            * 1000
        )
        assert summary["expectedEpochMs"] == expected_ms
        literal = endpoint.replace(tzinfo=None).isoformat(timespec="milliseconds") + "Z"
        rows = []
        assert len(summary["queries"]) == 14
        for chunk in range(14):
            ids = point_ids[chunk * 500 : (chunk + 1) * 500]
            assert (
                len(ids) == 500 and summary["queries"][chunk]["actualPointIds"] == ids
            )
            predicate = (
                "point_id IN ("
                + ", ".join("'" + point + "'" for point in ids)
                + ") AND ts = '"
                + literal
                + "'"
            )
            tail = " FROM " + database + ".st_point_data_v1 WHERE " + predicate
            count = original_response(
                reader,
                directory,
                f"chunk-{chunk + 1:02d}-count",
                "SELECT COUNT(*) AS row_count" + tail,
                ("row_count",),
            )
            data = original_response(
                reader,
                directory,
                f"chunk-{chunk + 1:02d}-data",
                "SELECT " + PROJECTION + tail,
                COLS,
            )
            query_count += 2
            assert (
                len(count) == 1
                and type(count[0]["row_count"]) is int
                and count[0]["row_count"] >= 0
            )
            assert count[0]["row_count"] == len(data)
            for row in data:
                assert (
                    row["point_id"] in ids
                    and type(row["ts_epoch_ms"]) is int
                    and row["ts_epoch_ms"] == expected_ms
                )
                assert instant(row["ts"]) == endpoint
            rows.extend(data)
        actual_points = {row["point_id"]: row for row in rows}
        assert len(rows) == len(actual_points), "Duplicate actual endpoint point"
        expected_present = set(point_ids) if index < 24 else set()
        assert set(actual_points) == expected_present
        missing = set(point_ids) - set(actual_points)
        assert summary["foundPoints"] == len(actual_points) and summary[
            "rawRowCount"
        ] == len(rows)
        assert (
            len(summary["missingPointIds"]) == len(missing)
            and set(summary["missingPointIds"]) == missing
        )
        projection = reader.json(directory + "/all-7000-point-roles.json.gz")
        assert (
            len(projection) == 7000
            and len({row["pointId"] for row in projection}) == 7000
        )
        for row in projection:
            point = row["pointId"]
            assert (row["loopId"], row["loopTag"], row["role"]) == points[point]
            assert row["present"] is (point in actual_points)
            assert row["rawRows"] == (
                [actual_points[point]] if point in actual_points else []
            )
            assert not ({"value", "quality_class", "quality_raw"} & row.keys()), (
                "Projection inserted a filled value"
            )
        summaries.append(
            {
                "directory": directory,
                "purpose": window["purpose"],
                "endpoint": window["ts_end"],
                "found": len(rows),
                "missing": len(missing),
                "qualityClasses": dict(
                    Counter(str(row["quality_class"]) for row in rows)
                ),
                "all7000MappingsAndOriginalFieldsMatch": True,
            }
        )
    assert query_count == 672 and reader.unchanged()
    assert all(
        digest(path.read_bytes()) == sha for path, sha in external_hashes.items()
    )
    return {
        "verdict": "PASS",
        "scope": "Actual closed endpoint capture integrity only; no capacity or KPI business acceptance",
        "normalBatches": 20,
        "extraBatches": 4,
        "queryResponses": query_count,
        "captureManifestSha256": reader.reads["sha256-manifest.json"],
        "externalOriginals": [
            {"path": str(path), "sha256": sha} for path, sha in external_hashes.items()
        ],
        "allManifestFilesVerified": len(entries),
        "readSet": reader.reads,
        "sourcesUnchanged": True,
        "windows": summaries,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--normal-plan", type=Path, required=True)
    parser.add_argument("--extra-plan", type=Path, required=True)
    parser.add_argument("--original-identity", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(
            args.capture, args.normal_plan, args.extra_plan, args.original_identity
        )
        rc = 0
    except FileNotFoundError as exc:
        result, rc = {"verdict": "BLOCKED", "error": str(exc)}, 2
    except Exception as exc:
        result, rc = (
            {"verdict": "REWORK", "errorType": type(exc).__name__, "error": str(exc)},
            1,
        )
    with args.out.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"readSet", "windows"}
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
