"""Replay a modified baseline with the original frozen manifest; fake PG only."""

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class Database:
    def __init__(self, rows):
        self.rows = rows

    def transaction(self, *, isolation, readonly):
        assert isolation == "repeatable_read" and readonly is True
        return Transaction()

    async def fetchrow(self, _sql):
        return {
            "readonly": "on",
            "isolation": "repeatable read",
            "database": "clpm_capacity_20261017_s01",
            "snapshot": "fake-original-snapshot",
            "clock": "2026-10-17T12:00:00Z",
        }

    async def fetchval(self, sql):
        table = "plant_node" if "FROM plant_node)" in sql else "loop_ledger"
        return json.dumps(self.rows[table], sort_keys=True)

    async def close(self):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect", choices=["reject", "accept"], default="reject")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "scope_snapshot_under_review", args.source
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    planned = json.loads((args.fixture / "original-freeze-identity.json").read_bytes())
    current = json.loads((args.fixture / "actual-pg-rows.json").read_bytes())
    connected = []

    async def connect(**_kwargs):
        connected.append(True)
        return Database(current)

    def git(command, **_kwargs):
        assert command[0] == "git"
        return planned["candidateSha"] if command[1] == "rev-parse" else b""

    module.asyncpg.connect = connect
    module.subprocess.check_output = git
    module.dotenv_values = lambda _path: {
        "POSTGRES_DB": "clpm_capacity_20261017_s01",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "17102",
        "POSTGRES_USER": "fake-not-live",
        "POSTGRES_PASSWORD": "fake-not-live",
    }
    params = SimpleNamespace(
        candidate_sha=planned["candidateSha"],
        config_freeze=str(args.fixture / "freeze"),
        out=str(args.out / "scope"),
        config_freeze_manifest_sha256=planned["frozenManifestSha256"],
    )
    args.out.mkdir(exist_ok=False)
    try:
        rc = asyncio.run(module.main(params))
        rejected, error = rc != 0, None
    except (AssertionError, ValueError) as exc:
        rejected, error = True, {"type": type(exc).__name__, "message": str(exc)}
    result = {
        "source": str(args.source),
        "fixture": str(args.fixture),
        "rejectedModifiedCanonical": rejected,
        "fakeConnectCalls": len(connected),
        "error": error,
        "expectedOriginalManifestSha256": planned["frozenManifestSha256"],
        "noActualPgOrServices": True,
    }
    (args.out / "counter-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if args.expect == "reject":
        assert rejected, (
            "Changed baseline canonical was accepted as the original frozen scope"
        )
        assert not connected, (
            "The invalid baseline must be rejected before connecting PG"
        )
    else:
        assert not rejected and len(connected) == 1, (
            "Genuine unchanged original scope was refused"
        )


if __name__ == "__main__":
    main()
