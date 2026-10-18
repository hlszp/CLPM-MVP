"""Corruption controls for the independent disk reader, without product code."""

import copy
import gzip
import json

import pytest

from verify_closed_endpoints_raw import COLS, canonical, digest, original_response


class MemoryReader:
    def __init__(self, files):
        self.files = files

    def raw(self, name):
        return self.files[name]

    def json(self, name):
        raw = self.raw(name)
        return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)


def fixture():
    values = [
        "actual-point-id",
        "2026-10-16T09:00:00Z",
        1792141200000,
        0,
        0,
        0,
        None,
        0,
        None,
    ]
    body = {
        "code": 0,
        "column_meta": [[key, "native", 0] for key in COLS],
        "data": [values],
        "rows": 1,
    }
    rows = [dict(zip(COLS, values, strict=True))]
    raw, sql = canonical(body), b"SELECT exact frozen query"
    compressed = gzip.compress(raw, mtime=0)
    command = {
        "rc": 0,
        "httpStatus": 200,
        "tdCode": 0,
        "method": "POST",
        "url": "http://localhost:17115/rest/sql",
        "sqlFile": "data.sql",
        "rawFile": "data.response.raw.json.gz",
        "sqlSha256": digest(sql),
        "rawSha256": digest(raw),
        "gzipSha256": digest(compressed),
        "rowCount": 1,
        "canonicalRowsFile": "data.rows.canonical.json.gz",
        "canonicalRowsSha256": digest(canonical(rows)),
        "startedAt": "2026-10-17T11:57:26Z",
        "finishedAt": "2026-10-17T11:57:27Z",
    }
    files = {
        "batch/data.command.json": canonical(command),
        "batch/data.sql": sql,
        "batch/data.response.raw.json.gz": compressed,
        "batch/data.rows.canonical.json.gz": gzip.compress(canonical(rows), mtime=0),
    }
    return files, body, rows, command


def test_true_original_null_and_zero_survive_independent_raw_recovery():
    files, _body, rows, _command = fixture()
    assert (
        original_response(
            MemoryReader(files), "batch", "data", "SELECT exact frozen query", COLS
        )
        == rows
    )


@pytest.mark.parametrize(
    "defect",
    [
        "raw-hash",
        "sql-limit",
        "missing-column",
        "duplicate-column",
        "short-row",
        "canonical-fill",
        "rows-mismatch",
        "nonzero-code",
    ],
)
def test_corruption_is_rejected_even_when_other_metadata_is_rehashed(defect):
    files, body, rows, command = fixture()
    body, rows, command = (
        copy.deepcopy(body),
        copy.deepcopy(rows),
        copy.deepcopy(command),
    )
    if defect == "sql-limit":
        files["batch/data.sql"] += b" LIMIT 1"
        command["sqlSha256"] = digest(files["batch/data.sql"])
    elif defect == "missing-column":
        body["column_meta"] = body["column_meta"][:3]
        body["data"][0] = body["data"][0][:3]
    elif defect == "duplicate-column":
        body["column_meta"][-1][0] = body["column_meta"][-2][0]
    elif defect == "short-row":
        body["data"][0].pop()
    elif defect == "canonical-fill":
        rows[0]["value"] = 999
    elif defect == "rows-mismatch":
        body["rows"] = 2
    elif defect == "nonzero-code":
        body["code"] = 1850
    raw = canonical(body)
    compressed = gzip.compress(raw, mtime=0)
    files["batch/data.response.raw.json.gz"] = compressed
    command["rawSha256"] = "0" * 64 if defect == "raw-hash" else digest(raw)
    command["gzipSha256"] = digest(compressed)
    files["batch/data.rows.canonical.json.gz"] = gzip.compress(canonical(rows), mtime=0)
    command["canonicalRowsSha256"] = digest(canonical(rows))
    files["batch/data.command.json"] = canonical(command)
    with pytest.raises((AssertionError, ValueError)):
        original_response(
            MemoryReader(files), "batch", "data", "SELECT exact frozen query", COLS
        )
