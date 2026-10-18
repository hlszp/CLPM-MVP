"""Own witness checks on the real sampler using the author's offline DB fixture.

The fixture only supplies network-free database/clock shapes; these assertions
independently verify the transaction and timing relationships in raw artifacts.
"""

import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


DIRECTORY = Path(os.environ["COLLECTOR_FIXTURE_DIRECTORY"])
sys.path.insert(0, str(DIRECTORY))
fixture_spec = importlib.util.spec_from_file_location(
    "offline_db_fixture", DIRECTORY / "test_collector_terminal_payloads.py"
)
FIXTURE = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(FIXTURE)
source_spec = importlib.util.spec_from_file_location(
    "independent_sampler_source", os.environ["COLLECTOR_REVIEW_SOURCE"]
)
SUBJECT = importlib.util.module_from_spec(source_spec)
source_spec.loader.exec_module(SUBJECT)
FIXTURE.subject = SUBJECT


def restored(directory, ref):
    raw_gzip = (directory / ref["file"]).read_bytes()
    assert hashlib.sha256(raw_gzip).hexdigest() == ref["gzipSha256"]
    raw = gzip.decompress(raw_gzip)
    assert hashlib.sha256(raw).hexdigest() == ref["rawSha256"]
    rows = json.loads(raw)
    assert len(rows) == ref["rows"]
    return rows


@pytest.mark.parametrize("partial_first", [False, True])
def test_first_witness_is_its_own_rr_transaction_and_never_post30_backfill(
    tmp_path, monkeypatch, partial_first
):
    database, output, summary = FIXTURE.run_fake_sampler(
        tmp_path, monkeypatch, partial_first
    )
    observations = [
        json.loads(line)
        for line in (output / "observations.jsonl").read_text().splitlines()
    ]
    observations = [row for row in observations if "poll" in row]
    terminal = summary["firstTerminalFullPayloads"]
    complete = summary["firstCompleteFullPayloads"]
    final = summary["finalFullPayloads"]
    assert terminal["poll"] == 1 and complete["poll"] == (2 if partial_first else 1)
    assert terminal["completionReason"] == "TERMINAL_MISMATCH"
    assert summary["status"] == "COLLECTED", (
        "Collection status must not become a business PASS"
    )
    for key, snapshot in [
        ("firstTerminalFullPayloads", terminal),
        ("firstCompleteFullPayloads", complete),
    ]:
        observation = next(
            row for row in observations if row["poll"] == snapshot["poll"]
        )
        assert observation[key] == snapshot
        assert snapshot["transaction"] == observation["pgTransaction"]
        assert snapshot["pgStartedAt"] == observation["pgStartedAt"]
        assert snapshot["pgReadCompletedAt"] == observation["pgReadCompletedAt"]
        assert snapshot["pgEndedAt"] == observation["pgEndedAt"]
        assert (
            snapshot["taskObservedAt"]
            == observation["taskObservedAt"]
            == "separate-redis-time"
        )
        assert snapshot["transaction"]["snapshot"] != final["transaction"]["snapshot"]
        rows_by_kind = {
            kind: restored(output, ref) for kind, ref in snapshot["artifacts"].items()
        }
        assert set(rows_by_kind) == {"ledger", "latest", "nodes", "records"}
        expected_tx = int(snapshot["transaction"]["snapshot"].split("-")[-1])
        for rows in rows_by_kind.values():
            assert all(row["payload"]["sameTransaction"] == expected_tx for row in rows)
            assert all(row["payload"]["raw"] == [None, 0, "original"] for row in rows)
    assert len(restored(output, terminal["artifacts"]["ledger"])) == (
        999 if partial_first else 1000
    )
    assert len(restored(output, complete["artifacts"]["ledger"])) == 1000
    assert len(restored(output, complete["artifacts"]["latest"])) == 1000
    full_query_calls = [
        call
        for call in database.calls
        if call["sql"].startswith("SELECT * FROM ")
        and "calculation_result_attempt" not in call["sql"]
    ]
    expected_transactions = {2, 3, 5} if partial_first else {2, 5}
    assert {call["tx"] for call in full_query_calls} == expected_transactions
    assert all(
        sum(call["tx"] == transaction for call in full_query_calls) == 4
        for transaction in expected_transactions
    )
