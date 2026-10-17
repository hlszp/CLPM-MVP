"""Read-only protection tests for the CI-only input writer."""

import runpy
import json
import os
import subprocess
import sys
import unittest
import asyncio
import copy
import tempfile
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("prepare-critical-fixture.py")
CHECK = runpy.run_path(str(SCRIPT))["isolation_errors"]
SERVICE_CONFIG = runpy.run_path(str(SCRIPT))["CI_SERVICE_CONFIG"]
IDENTITY = runpy.run_path(str(SCRIPT))["execution_identity"]
INPUT_TOOLS = runpy.run_path(str(SCRIPT))


class IsolationProtectionTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": "a" * 40,
            "POSTGRES_HOST": "localhost",
            "REDIS_HOST": "127.0.0.1",
            "TDENGINE_HOST": "localhost",
            "POSTGRES_DB": "clpm_critical_e2e",
            "TDENGINE_DB": "clpm_critical_e2e_ts",
        }

    def test_only_disposable_ci_identity_is_accepted(self):
        self.assertEqual(CHECK(self.env, "http://localhost:17101/api/v1"), [])

    def test_every_service_identity_guard_is_required(self):
        for key in self.env:
            with self.subTest(key=key):
                invalid = {**self.env, key: "shared"}
                self.assertTrue(CHECK(invalid, "http://localhost:17101/api/v1"))

    def test_remote_api_is_rejected(self):
        self.assertTrue(CHECK(self.env, "https://shared.example.invalid/api/v1"))

    def test_both_writers_refuse_before_imports_or_network_outside_ci(self):
        env = {key: value for key, value in os.environ.items() if key not in self.env}
        for mode in ("--execute", "--configure-services"):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), mode],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("refusedBeforeImportsOrWrites", json.loads(result.stderr))

    def test_config_closes_external_services_without_touching_product_defaults(self):
        for key in (
            "aas.sync_enabled",
            "datasource.signalr_enabled",
            "datasource.gap_backfill_enabled",
            "llm.enabled",
        ):
            self.assertEqual(SERVICE_CONFIG[key], "false")
        for key in (
            "aas.endpoint",
            "datasource.history_api_url",
            "datasource.signalr_hub_url",
        ):
            self.assertIn("127.0.0.1:9", SERVICE_CONFIG[key])
        self.assertEqual(SERVICE_CONFIG["llm.endpoint"], "")
        self.assertEqual(SERVICE_CONFIG["datasource.history_api_token"], "")


class LocalIsolationProtectionTests(unittest.TestCase):
    def setUp(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=SCRIPT.parents[2], text=True
        ).strip()
        self.env = {
            "E2E_EXECUTION_ENV": "disposable-local",
            "LOCAL_RUN_ID": "20261017203000",
            "LOCAL_RUN_ATTEMPT": "1",
            "CLPM_BUILD_CODE_SHA": head,
            "POSTGRES_HOST": "127.0.0.1",
            "POSTGRES_PORT": "17102",
            "POSTGRES_DB": "clpm_critical_20261017_local",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "17124",
            "REDIS_DB": "0",
            "TDENGINE_HOST": "127.0.0.1",
            "TDENGINE_PORT": "17104",
            "TDENGINE_DB": "clpm_critical_20261017_local_ts",
            "E2E_BASE_URL": "http://127.0.0.1:17266",
        }
        self.api = "http://127.0.0.1:17261/api/v1"

    def test_explicit_local_profile_uses_its_true_identity(self):
        self.assertEqual(CHECK(self.env, self.api), [])
        identity = IDENTITY(self.env)
        self.assertEqual(identity["environment"], "disposable-local")
        self.assertEqual(identity["location"], "local-keeper")
        self.assertEqual(identity["sourceSha"], self.env["CLPM_BUILD_CODE_SHA"])
        self.assertEqual(identity["prefix"], "CRITICAL-LOCAL")
        self.assertNotIn("GITHUB_ACTIONS", self.env)

    def test_each_local_identity_guard_is_required(self):
        for key in self.env:
            with self.subTest(key=key):
                self.assertTrue(CHECK({**self.env, key: "shared"}, self.api))

    def test_shared_capacity_and_production_database_names_are_rejected(self):
        for database in ("clpm", "clpm_capacity1000", "clpm_prod", "clpm_critical_e2e"):
            with self.subTest(database=database):
                self.assertTrue(CHECK({**self.env, "POSTGRES_DB": database}, self.api))

    def test_wrong_api_ui_ports_remote_hosts_and_github_impersonation_are_rejected(
        self,
    ):
        for api in ("http://127.0.0.1:17101/api/v1", "https://shared.invalid/api/v1"):
            self.assertTrue(CHECK(self.env, api))
        self.assertTrue(CHECK({**self.env, "GITHUB_ACTIONS": "true"}, self.api))
        self.assertTrue(
            CHECK({**self.env, "E2E_BASE_URL": "http://127.0.0.1:15666"}, self.api)
        )

    def test_well_formed_but_incorrect_source_sha_is_rejected(self):
        self.assertTrue(CHECK({**self.env, "CLPM_BUILD_CODE_SHA": "0" * 40}, self.api))

    def test_invalid_local_attempt_is_rejected(self):
        for attempt in ("0", "-1", "shared"):
            self.assertTrue(CHECK({**self.env, "LOCAL_RUN_ATTEMPT": attempt}, self.api))

    def test_plan_is_read_only_and_both_writers_refuse_wrong_api_before_imports(self):
        env = {**os.environ, **self.env}
        env.pop("GITHUB_ACTIONS", None)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--plan", "--api-url", self.api],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["writes"])
        self.assertEqual(json.loads(result.stdout)["executionPreconditions"], [])
        for mode in ("--execute", "--configure-services"):
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    mode,
                    "--api-url",
                    "http://127.0.0.1:17101/api/v1",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("refusedBeforeImportsOrWrites", json.loads(result.stderr))


class SyntheticBindingInputTests(unittest.TestCase):
    def setUp(self):
        self.tag = "CRITICAL-LOCAL-20261017220000-1-FLOW001"
        self.now = datetime.now(UTC)
        self.created_after = self.now - timedelta(seconds=1)
        self.loop = {
            "tag_name": self.tag,
            "created_at": self.now,
            "description": "synthetic CI INPUT: real algorithm, disposable runner only",
        }
        self.points = {
            role: (f"{self.tag}_{role}", f"00000000-0000-0000-0000-{n:012}")
            for n, role in enumerate(sorted(INPUT_TOOLS["BINDING_ROLES"]), 1)
        }
        self.mappings = [
            {
                "tag_role": role,
                "tag_name": name,
                "point_id": point,
                "tag_description": f"synthetic CI INPUT {role}",
            }
            for role, (name, point) in self.points.items()
        ]
        self.histories = [
            {
                "id": f"00000000-0000-0000-0001-{n:012}",
                "tag_role": role,
                "tag_id": point,
                "valid_from": self.now,
                "valid_to": None,
                "created_at": self.now,
                "mapping_version": 1,
                "basis": "MVP_INIT",
            }
            for n, (role, (_, point)) in enumerate(self.points.items(), 1)
        ]

    def errors(self, **changes):
        args = {
            "tag_name": self.tag,
            "loop": self.loop,
            "mappings": self.mappings,
            "histories": self.histories,
            "points": self.points,
            "created_after": self.created_after,
        }
        return INPUT_TOOLS["synthetic_binding_errors"](**{**args, **changes})

    def test_exact_fresh_synthetic_roles_accept_existing_or_no_initial_history(self):
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.errors(histories=[]), [])

    def test_foreign_old_partial_or_changed_history_rejected(self):
        self.assertTrue(self.errors(tag_name="FIELD-FLOW001"))
        self.assertTrue(
            self.errors(loop={**self.loop, "created_at": self.now - timedelta(days=1)})
        )
        self.assertTrue(
            self.errors(loop={**self.loop, "description": "field evidence"})
        )
        self.assertTrue(self.errors(mappings=self.mappings[:-1]))
        self.assertTrue(self.errors(histories=self.histories + self.histories[:1]))
        for key, value in {
            "tag_id": "foreign",
            "valid_from": self.now - timedelta(days=1),
            "created_at": self.now - timedelta(days=1),
            "basis": "REBIND",
            "valid_to": self.now,
            "mapping_version": 2,
        }.items():
            with self.subTest(key=key):
                rows = copy.deepcopy(self.histories)
                rows[0][key] = value
                self.assertTrue(self.errors(histories=rows))
        mappings = copy.deepcopy(self.mappings)
        mappings[0]["point_id"] = "foreign"
        self.assertTrue(self.errors(mappings=mappings))

    def test_binding_writer_refuses_before_connection_outside_guard(self):
        writer = INPUT_TOOLS["declare_synthetic_bindings"]
        with (
            patch.dict(os.environ, {}, clear=True),
            tempfile.TemporaryDirectory() as tmp,
        ):
            with self.assertRaisesRegex(RuntimeError, "refused outside isolated"):
                asyncio.run(
                    writer(
                        "http://shared.invalid",
                        Path(tmp) / "fixture.json",
                        "bad-id",
                        self.tag,
                        self.points,
                        self.now,
                        self.created_after,
                    )
                )

    def test_td_readback_accepts_exact_ms_datetime_and_iso_input(self):
        samples = [(0, 1.25, 0, 192), (1, 2.5, 0, 192)]
        point = next(iter(self.points.values()))[1]
        start = self.now.replace(microsecond=0)
        for kind in ("ms", "datetime", "iso"):
            rows = []
            for offset, value, quality, raw in samples:
                tick = start + timedelta(seconds=offset)
                timestamp = (
                    int(tick.timestamp() * 1000)
                    if kind == "ms"
                    else tick
                    if kind == "datetime"
                    else tick.isoformat()
                )
                rows.append(
                    {
                        "ts": timestamp,
                        "value": value,
                        "quality_class": quality,
                        "quality_raw": raw,
                        "point_id": point,
                    }
                )
            self.assertEqual(
                len(INPUT_TOOLS["validate_td_readback"](rows, point, samples, start)), 2
            )
            for key, invalid in {
                "point_id": "foreign",
                "value": 9,
                "quality_class": 2,
                "quality_raw": 0,
                "ts": int(start.timestamp()),
            }.items():
                with self.subTest(kind=kind, key=key):
                    changed = copy.deepcopy(rows)
                    changed[0][key] = invalid
                    with self.assertRaises(RuntimeError):
                        INPUT_TOOLS["validate_td_readback"](
                            changed, point, samples, start
                        )
            with self.assertRaises(RuntimeError):
                INPUT_TOOLS["validate_td_readback"](
                    list(reversed(rows)), point, samples, start
                )

    def test_writer_targets_seven_owned_rows_and_rolls_back_wrong_rowcount(self):
        env = IsolationProtectionTests()
        env.setUp()
        private = {
            **env.env,
            "POSTGRES_PORT": "5432",
            "POSTGRES_USER": "unit",
            "POSTGRES_PASSWORD": "unit",
        }
        owner = self

        class Transaction:
            def __init__(self, connection, options):
                self.connection, self.options = connection, options

            async def __aenter__(self):
                self.old = copy.deepcopy(self.connection.rows)
                self.connection.readonly = self.options.get("readonly", False)

            async def __aexit__(self, kind, value, traceback):
                if kind:
                    self.connection.rows = self.old
                elif not self.options.get("readonly"):
                    self.connection.write_commits += 1
                elif self.connection.readback_failure == "exit":
                    raise ConnectionError("readback transaction exit failed")

        class Connection:
            def __init__(self, bad, empty, readback_failure):
                self.rows = [] if empty else copy.deepcopy(owner.histories)
                self.bad, self.closed, self.executions = bad, False, []
                self.readonly, self.write_commits = False, 0
                self.readback_failure = readback_failure

            def transaction(self, **options):
                return Transaction(self, options)

            async def fetchval(self, sql):
                return "clpm_critical_e2e"

            async def fetchrow(self, sql, identity):
                return owner.loop

            async def fetch(self, sql, *parameters):
                if "WHERE tag_id=ANY" in sql:
                    return []
                if "FROM loop_tag_mapping m JOIN" in sql:
                    return owner.mappings
                if self.readonly and self.readback_failure == "fetch":
                    raise ConnectionError("committed readback disconnected")
                rows = copy.deepcopy(self.rows)
                if self.readonly and self.readback_failure == "mismatch":
                    rows[0]["basis"] = "CONTROLLED_READBACK_MISMATCH"
                return rows

            async def execute(self, sql, *parameters):
                if sql.startswith("INSERT"):
                    identity, loop_id, role, point, effective = parameters
                    owner.assertEqual(str(point), owner.points[role][1])
                    self.executions.append((sql, parameters))
                    self.rows.append(
                        {
                            "id": str(identity),
                            "tag_role": role,
                            "tag_id": str(point),
                            "valid_from": effective,
                            "valid_to": None,
                            "mapping_version": 1,
                            "basis": "SYNTHETIC_FIXTURE",
                            "created_at": owner.now,
                        }
                    )
                    return "INSERT 0 0" if self.bad else "INSERT 0 1"
                owner.assertIn("WHERE loop_id=$1 AND id=ANY($3::uuid[])", sql)
                owner.assertEqual(len(parameters[2]), 7)
                self.executions.append((sql, parameters))
                for row in self.rows:
                    row.update(valid_from=parameters[1], basis="SYNTHETIC_FIXTURE")
                return "UPDATE 6" if self.bad else "UPDATE 7"

            async def close(self):
                self.closed = True

        cases = [
            (bad, empty, None)
            for bad, empty in (
                (False, False),
                (True, False),
                (False, True),
                (True, True),
            )
        ] + [
            (False, empty, failure)
            for empty in (False, True)
            for failure in ("mismatch", "fetch", "exit")
        ]
        for bad, empty, readback_failure in cases:
            connection = Connection(bad, empty, readback_failure)

            async def connect(**credentials):
                self.assertEqual(credentials["database"], "clpm_critical_e2e")
                return connection

            with (
                self.subTest(bad=bad, empty=empty, readback_failure=readback_failure),
                tempfile.TemporaryDirectory() as tmp,
                patch.dict(os.environ, private, clear=True),
                patch.dict(
                    sys.modules, {"asyncpg": types.SimpleNamespace(connect=connect)}
                ),
            ):
                output = Path(tmp) / "fixture.json"
                call = INPUT_TOOLS["declare_synthetic_bindings"](
                    "http://localhost:17101/api/v1",
                    output,
                    "00000000-0000-0000-0000-000000000009",
                    self.tag,
                    self.points,
                    self.now.replace(tzinfo=None) - timedelta(hours=2),
                    self.created_after,
                )
                if bad:
                    with self.assertRaises(RuntimeError):
                        asyncio.run(call)
                    self.assertEqual(connection.rows, [] if empty else self.histories)
                elif readback_failure:
                    message = {
                        "mismatch": "persisted synthetic binding readback differs",
                        "fetch": "committed readback disconnected",
                        "exit": "readback transaction exit failed",
                    }[readback_failure]
                    error = (
                        RuntimeError
                        if readback_failure == "mismatch"
                        else ConnectionError
                    )
                    with self.assertRaisesRegex(error, message):
                        asyncio.run(call)
                else:
                    result = asyncio.run(call)
                    self.assertEqual(result["basis"], "SYNTHETIC_FIXTURE")
                saved = json.loads(output.with_suffix(".bindings.json").read_text())
                self.assertEqual(saved["committed"], not bad)
                self.assertEqual(connection.write_commits, int(not bad))
                self.assertEqual(
                    saved["persistedReadbackVerified"],
                    not bad and readback_failure is None,
                )
                self.assertEqual(
                    len(saved["statements"]), 7 if empty and not bad else 1
                )
                self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
