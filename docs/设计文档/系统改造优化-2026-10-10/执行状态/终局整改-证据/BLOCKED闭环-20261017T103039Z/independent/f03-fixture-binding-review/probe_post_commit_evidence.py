"""Fixed pure-PG counterexample: committed INPUT must not be labelled uncommitted."""

import argparse
import asyncio
import copy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import types
from unittest.mock import patch


def fixture():
    now = datetime(2026, 10, 17, 13, 20, tzinfo=UTC)
    tag = "CRITICAL-E2E-12345-1-FLOW001"
    roles = ["MODE", "OP", "PID_D", "PID_I", "PID_P", "PV", "SP"]
    points = {role: [f"{tag}_{role}", f"00000000-0000-0000-0000-{i:012}"] for i, role in enumerate(roles, 1)}
    return now, tag, points


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    tools = runpy.run_path(str(args.source))
    now, tag, points = fixture()
    loop = {"tag_name": tag, "description": "synthetic CI INPUT: real algorithm, disposable runner only", "created_at": now}
    mappings = [{"tag_role": role, "tag_name": name, "point_id": point, "tag_description": f"synthetic CI INPUT {role}"} for role, (name, point) in points.items()]
    histories = [{"id": f"00000000-0000-0000-0001-{i:012}", "tag_role": role, "tag_id": point, "valid_from": now, "valid_to": None, "mapping_version": 1, "basis": "MVP_INIT", "created_at": now} for i, (role, (_, point)) in enumerate(points.items(), 1)]

    class Transaction:
        def __init__(self, connection, options):
            self.connection, self.options = connection, options

        async def __aenter__(self):
            self.connection.options.append(self.options)
            self.connection.readonly = self.options.get("readonly", False)

        async def __aexit__(self, kind, value, traceback):
            if kind is None and not self.options.get("readonly"):
                self.connection.write_commits += 1
            return False

    class Connection:
        def __init__(self):
            self.rows = copy.deepcopy(histories)
            self.write_commits = 0
            self.readonly = False
            self.closed = False
            self.options = []

        def transaction(self, **options):
            return Transaction(self, options)

        async def fetchval(self, sql):
            return "clpm_critical_e2e"

        async def fetchrow(self, sql, identity):
            return loop

        async def fetch(self, sql, *parameters):
            if "WHERE tag_id=ANY" in sql:
                return []
            if "FROM loop_tag_mapping m JOIN" in sql:
                return mappings
            answer = copy.deepcopy(self.rows)
            if self.readonly:
                # The write already committed; external divergence is detected later.
                answer[0]["basis"] = "CONTROLLED_READBACK_MISMATCH"
            return answer

        async def execute(self, sql, *parameters):
            assert sql.startswith("UPDATE loop_tag_binding_history")
            assert len(parameters[2]) == 7
            for row in self.rows:
                row.update(valid_from=parameters[1], basis="SYNTHETIC_FIXTURE")
            return "UPDATE 7"

        async def close(self):
            self.closed = True

    connection = Connection()

    async def connect(**credentials):
        assert credentials["database"] == "clpm_critical_e2e"
        return connection

    env = {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a" * 40, "POSTGRES_HOST": "localhost", "POSTGRES_PORT": "5432", "POSTGRES_USER": "unit", "POSTGRES_PASSWORD": "unit", "POSTGRES_DB": "clpm_critical_e2e", "REDIS_HOST": "localhost", "TDENGINE_HOST": "localhost", "TDENGINE_DB": "clpm_critical_e2e_ts"}
    error = None
    with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {"asyncpg": types.SimpleNamespace(connect=connect)}):
        try:
            await tools["declare_synthetic_bindings"]("http://localhost:17101/api/v1", args.out / "fixture.json", "00000000-0000-0000-0000-000000000099", tag, points, now.replace(tzinfo=None) - timedelta(hours=2), now - timedelta(seconds=1))
        except RuntimeError as caught:
            error = str(caught)
    evidence = json.loads((args.out / "fixture.bindings.json").read_text())
    result = {
        "sourceSha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "actualFakeTransactionWriteCommits": connection.write_commits,
        "readonlyRecheckAttempted": any(options.get("readonly") for options in connection.options),
        "writerRefusedReadbackMismatch": error,
        "artifactCommitted": evidence["committed"],
        "connectionClosed": connection.closed,
        "guardActuallyAcceptedSyntheticSevenRows": evidence.get("errors") == [],
        "scope": "Pure fake asyncpg; no real connection or database mutation",
    }
    (args.out / "counterexample.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)
    assert connection.write_commits == 1 and error == "persisted synthetic binding readback differs"
    assert evidence["committed"] is True, "Successful write commit was falsely labelled committed=false after readback failure"


if __name__ == "__main__":
    asyncio.run(main())
