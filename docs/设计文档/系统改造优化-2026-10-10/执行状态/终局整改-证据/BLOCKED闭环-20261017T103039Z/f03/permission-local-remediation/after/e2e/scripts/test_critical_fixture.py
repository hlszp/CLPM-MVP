"""Read-only protection tests for the CI-only input writer."""

import runpy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("prepare-critical-fixture.py")
CHECK = runpy.run_path(str(SCRIPT))["isolation_errors"]
SERVICE_CONFIG = runpy.run_path(str(SCRIPT))["CI_SERVICE_CONFIG"]
IDENTITY = runpy.run_path(str(SCRIPT))["execution_identity"]


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


if __name__ == "__main__":
    unittest.main()
