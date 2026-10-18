"""Independent raw-log boundary tests; never runs a CLPM service."""

import ast
import hashlib
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent


class IndependentCleanTerm(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="independent-v5-raw-")
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        module = ast.parse((HERE / "frozen-keeper-local-f03-v5b.py").read_text())
        stop = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "stop")
        helper = next(node for node in stop.body if isinstance(node, ast.FunctionDef) and node.name == "clean_shutdown_chain")
        space = {
            "OUT": self.out,
            "sha": lambda raw: hashlib.sha256(raw).hexdigest(),
            "now": lambda: "independent-observation",
            "write": lambda path, value: path.write_text(json.dumps(value)),
        }
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "frozen-clean-term", "exec"), space)
        self.helper = space["clean_shutdown_chain"]
        self.control = json.loads((HERE / "independent-native-control.json").read_text())
        self.raw = (HERE / "independent-native-server.log").read_bytes()
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), self.control["logSha256"])
        self.pid = self.control["pid"]
        self.offset = self.control["logOffsetBeforeSignal"]

    def call(self, raw=None, pid=None, offset=None):
        (self.out / "backend-api.stdout.log").write_bytes(self.raw if raw is None else raw)
        return self.helper(self.pid if pid is None else pid, self.offset if offset is None else offset)

    def test_independently_observed_graceful_minus15(self):
        self.assertEqual(self.control["actualExitRc"], -15)
        self.assertFalse(self.control["cleanupUsed"])
        chain = self.call()
        self.assertEqual(chain["pid"], self.pid)
        self.assertEqual(len(chain["eventsInOrder"]), 3)
        previous = self.offset
        for item in chain["eventsInOrder"]:
            self.assertGreaterEqual(item["offsetStart"], previous)
            raw = self.raw[item["offsetStart"]:item["offsetEnd"]]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["rawSha256"])
            self.assertEqual((self.out / item["file"]).read_bytes(), raw)
            previous = item["offsetEnd"]

    def test_finished_other_pid_is_not_our_process(self):
        self.assertIsNone(self.call(pid=self.pid + 1))

    def test_old_chain_before_requested_signal_is_rejected(self):
        self.assertIsNone(self.call(offset=len(self.raw)))

    def test_missing_waiting_is_rejected(self):
        self.assertIsNone(self.call(self.raw.replace(b"INFO:     Waiting for application shutdown.\n", b"")))

    def test_missing_complete_is_rejected(self):
        self.assertIsNone(self.call(self.raw.replace(b"INFO:     Application shutdown complete.\n", b"")))

    def test_missing_finished_is_rejected(self):
        self.assertIsNone(self.call(self.raw.replace(f"INFO:     Finished server process [{self.pid}]\n".encode(), b"")))

    def test_out_of_order_complete_is_rejected(self):
        waiting = b"INFO:     Waiting for application shutdown.\n"
        complete = b"INFO:     Application shutdown complete.\n"
        self.assertIsNone(self.call(self.raw.replace(waiting + complete, complete + waiting)))

    def test_partial_finished_line_is_not_complete(self):
        self.assertIsNone(self.call(self.raw.rstrip(b"\n")))

    def test_only_finished_atexit_line_is_rejected(self):
        self.assertIsNone(self.call(f"INFO:     Finished server process [{self.pid}]\n".encode(), offset=0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
