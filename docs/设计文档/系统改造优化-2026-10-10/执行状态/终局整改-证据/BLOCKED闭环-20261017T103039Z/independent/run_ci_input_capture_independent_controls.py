"""Offline non-author CI capture controls; blocks real networking/process launch."""

import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch


def denied(*args, **kwargs):
    raise AssertionError("non-author pure controls forbid live networking/processes")


socket.socket.connect = denied
socket.create_connection = denied
subprocess.run = denied
path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("copied_capture_tests", path)
authored = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authored)
authored.tool.ROOT = Path("/Users/zhangping/DEV/clpm-remediation-20261017")


class IndependentControls(authored.EvidenceControls):
    def test_dirty_tracked_tree_rejects_before_psql(self):
        original = self.fake

        def fake(argv, folder, child):
            if argv[:3] == ["git", "diff", "--quiet"]:
                raise RuntimeError("independent dirty tracked source")
            return original(argv, folder, child)

        with patch.object(authored.tool.shutil, "which", return_value="/fake/psql"):
            with self.assertRaises(RuntimeError):
                authored.tool.capture("before", self.base / "input-evidence/before", self.env, fake)
        self.assertFalse(any(call[0] == "/fake/psql" for call in self.calls))
        self.assertFalse(json.loads((self.base / "input-evidence/before/header.json").read_text())["complete"])

    def test_actual_policy_change_is_not_equal_after(self):
        self.capture("before")
        original = self.policy

        def changed():
            rows = original()
            slow = next(row for row in rows if row.get("requestedKey") == "data.held_too_long_slow_seconds")
            slow.update(rowExists=True, rawValue="7201")
            return rows

        with patch.object(self, "policy", changed):
            with self.assertRaises(ValueError):
                self.capture("after")
        self.assertFalse(json.loads((self.base / "input-evidence/after/header.json").read_text())["complete"])

    def test_psql_child_does_not_inherit_routing_or_readwrite_override(self):
        self.env.update(PGHOSTADDR="203.0.113.8", PGSERVICE="foreign", PGSERVICEFILE="/foreign", PGOPTIONS="-c default_transaction_read_only=off", PSQLRC="/foreign")
        original = self.fake
        observed = []

        def fake(argv, folder, child):
            observed.append(child)
            self.assertEqual(set(child), {"PATH", "LANG", "PGCONNECT_TIMEOUT", "PGPASSWORD", "PGOPTIONS"})
            self.assertIn("default_transaction_read_only=on", child["PGOPTIONS"])
            self.assertFalse({"PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PSQLRC"} & set(child))
            return original(argv, folder, child)

        with patch.object(authored.tool.shutil, "which", return_value="/fake/psql"):
            authored.tool.capture("before", self.base / "input-evidence/before", self.env, fake)
        self.assertTrue(observed)

    def test_high_precision_numeric_policy_differences_are_preserved(self):
        first = authored.tool.objects('{"v":0.12345678901234567890}\n')[0]["v"]
        second = authored.tool.objects('{"v":0.12345678901234567891}\n')[0]["v"]
        self.assertNotEqual(first, second)
        self.assertEqual(str(first), "0.12345678901234567890")
        self.assertEqual(str(second), "0.12345678901234567891")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IndependentControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({"testsRun": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped), "realDbNetworkOrSignals": 0}))
    raise SystemExit(not result.wasSuccessful())
