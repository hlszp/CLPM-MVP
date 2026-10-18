"""Independent fake-PG contract tests. No actual DB operations."""

import asyncio
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rr_driver", ROOT / "driver.v2.py")
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class Connection:
    def __init__(self, defect=None):
        self.defect = defect
        self.inside = False
        self.closed = False
        self.reads = []
        self.txargs = None

    def transaction(self, **kwargs):
        self.txargs = kwargs
        owner = self

        class Tx:
            async def __aenter__(self):
                owner.inside = True

            async def __aexit__(self, *args):
                owner.inside = False

        return Tx()

    async def fetchval(self, sql, *args):
        assert self.inside
        self.reads.append((sql, args))
        if "current_database()" in sql:
            return json.dumps(
                {
                    "database_name": "wrong"
                    if self.defect == "database"
                    else "clpm_capacity_20261017_s01",
                    "read_only": "off" if self.defect == "readonly" else "on",
                    "isolation": "read committed"
                    if self.defect == "isolation"
                    else "repeatable read",
                    "txid": 123,
                    "snapshot": "123:123:",
                    "observed_utc": "2026-10-17T00:00:00",
                }
            )
        return (
            None
            if self.defect == "missing-table"
            and args[0] == "public.calculation_dataset_snapshot"
            else args[0]
        )

    async def fetch(self, sql):
        assert self.inside
        self.reads.append((sql, ()))
        if "FROM loop_tag_mapping " in sql:
            return [{"row": "{}", "canonical": "{}"} for _ in range(7000)]
        if self.defect == "nonzero-target" and "FROM kpi_snapshot_hourly " in sql:
            return [{"row": "{}", "canonical": "{}"}]
        return []

    async def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "defect",
    [None, "database", "readonly", "isolation", "missing-table", "nonzero-target"],
)
def test_rr_ro_entire_nine_table_read_and_fail_closed(monkeypatch, tmp_path, defect):
    c = Connection(defect)

    async def connect(database):
        assert database == "clpm_capacity_20261017_s01"
        return c

    monkeypatch.setattr(subject, "pg_connect", connect)
    monkeypatch.setattr(
        subject,
        "native_credentials",
        lambda: {"POSTGRES_DB": "clpm_capacity_20261017_s01"},
    )
    monkeypatch.setattr(subject, "OUT", tmp_path, raising=False)
    if defect:
        with pytest.raises(RuntimeError):
            asyncio.run(subject.capacity_observation("before"))
    else:
        asyncio.run(subject.capacity_observation("before"))
        tx = json.loads((tmp_path / "capacity-transaction-before.json").read_text())
        assert tx["parsed"]["snapshot"] == "123:123:" and tx["rawRow"]
        body = json.loads((tmp_path / "capacity-readonly-before.json").read_text())
        assert (
            len(body["tables"]) == 9
            and body["tables"]["loop_tag_mapping"]["count"] == 7000
        )
    assert c.txargs == {"isolation": "repeatable_read", "readonly": True}
    assert c.closed and not c.inside
    assert all(sql.startswith("SELECT") for sql, args in c.reads)
