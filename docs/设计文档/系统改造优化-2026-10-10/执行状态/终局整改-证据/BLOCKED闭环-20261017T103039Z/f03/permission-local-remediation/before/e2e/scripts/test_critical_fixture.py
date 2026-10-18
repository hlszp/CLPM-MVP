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


if __name__ == "__main__":
    unittest.main()
