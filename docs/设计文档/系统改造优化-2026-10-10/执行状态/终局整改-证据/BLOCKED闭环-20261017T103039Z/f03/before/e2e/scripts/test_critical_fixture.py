"""Read-only protection tests for the CI-only input writer."""

import runpy
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("prepare-critical-fixture.py")
CHECK = runpy.run_path(str(SCRIPT))["isolation_errors"]


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


if __name__ == "__main__":
    unittest.main()
