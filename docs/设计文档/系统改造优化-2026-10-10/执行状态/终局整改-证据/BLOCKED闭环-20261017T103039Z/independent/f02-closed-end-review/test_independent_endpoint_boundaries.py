"""Independent response and original-byte boundaries, exclusively offline."""

import gzip
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SOURCE = Path(os.environ["ENDPOINT_REVIEW_SOURCE"])
sys.path.insert(0, str(SOURCE.parent))
SPEC = importlib.util.spec_from_file_location("endpoint_boundary_review", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
POINT = "00000000-0000-4000-8000-000000000004"
END = "2026-10-16T09:00:00Z"
COLS = [
    "point_id",
    "ts",
    "ts_epoch_ms",
    "value",
    "quality_class",
    "quality_raw",
    "quality_schema",
    "source_kind",
    "payload_hash",
]
ROW = [POINT, END, 1792141200000, 0, 0, 0, None, 0, None]


class OriginalClient:
    def __init__(self, body, status=200):
        self.raw = json.dumps(body, separators=(",", ":")).encode()
        self.body, self.status = body, status

    def post(self, *_args, **_kwargs):
        return SimpleNamespace(
            content=self.raw, status_code=self.status, json=lambda: self.body
        )


def query(tmp_path, columns, values, *, kind="data", rows=1, code=0, status=200):
    body = {
        "code": code,
        "column_meta": [[name, "native", 0] for name in columns],
        "data": [values],
        "rows": rows,
    }
    client = OriginalClient(body, status)
    sql = MODULE.build_queries("clpm_capacity_review", [POINT], END)[kind]
    meta, converted = MODULE.capture_query(
        client, "http://127.0.0.1:1/not-connected", sql, tmp_path, "capture"
    )
    assert gzip.decompress((tmp_path / meta["rawFile"]).read_bytes()) == client.raw
    return meta, converted


def test_all_missing_sql_keys_are_rejected_but_present_null_and_zero_are_original():
    original = dict(zip(COLS, ROW, strict=True))
    assert MODULE.evaluate_chunk([POINT], END, 1, [original]) == []
    for key in COLS:
        missing = dict(original)
        del missing[key]
        assert MODULE.evaluate_chunk([POINT], END, 1, [missing]), key
    nullable = dict(original)
    nullable.update({key: None for key in COLS[3:]})
    assert MODULE.evaluate_chunk([POINT], END, 1, [nullable]) == []


@pytest.mark.parametrize(
    "defect", ["short", "long", "duplicate", "reordered", "missing", "rows-mismatch"]
)
def test_raw_response_cannot_drop_or_relabel_columns(tmp_path, defect):
    columns, values = list(COLS), list(ROW)
    reported = 1
    if defect == "short":
        values.pop()
    elif defect == "long":
        values.append("unrepresented-original-value")
    elif defect == "duplicate":
        columns[-1] = columns[-2]
    elif defect == "reordered":
        columns[0], columns[1] = columns[1], columns[0]
    elif defect == "missing":
        columns, values = columns[:3], values[:3]
    elif defect == "rows-mismatch":
        reported = 2
    meta, _converted = query(tmp_path, columns, values, rows=reported)
    assert meta["rc"] == 1
    assert not (tmp_path / "capture.rows.canonical.json.gz").exists()


def test_exact_data_and_count_keep_distinct_projections(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    meta, converted = query(data, COLS, ROW)
    assert meta["rc"] == 0 and converted == [dict(zip(COLS, ROW, strict=True))]
    count = tmp_path / "count"
    count.mkdir()
    meta, converted = query(count, ["row_count"], [0], kind="count")
    assert meta["rc"] == 0 and converted == [{"row_count": 0}]
    wrong = tmp_path / "count-with-extra"
    wrong.mkdir()
    meta, _converted = query(wrong, ["row_count", "unasked"], [0, 1], kind="count")
    assert meta["rc"] == 1


@pytest.mark.parametrize("code,status", [(1850, 200), (0, 503)])
def test_resource_or_http_error_remains_original_failure(tmp_path, code, status):
    meta, converted = query(tmp_path, COLS, ROW, code=code, status=status)
    assert meta["rc"] == 1 and converted is None


def test_exact_500_id_query_has_same_count_predicate_and_no_limit():
    import uuid

    points = [str(uuid.UUID(int=index)) for index in range(1, 501)]
    queries = MODULE.build_queries("clpm_capacity_review", points, END)
    assert (
        queries["count"].partition(" WHERE ")[2]
        == queries["data"].partition(" WHERE ")[2]
    )
    assert "LIMIT" not in queries["data"].upper()
    assert all(queries["data"].count("'" + point + "'") == 1 for point in points)
    assert "ts = '2026-10-16T09:00:00.000Z'" in queries["data"]
