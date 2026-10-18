"""Replay real Uvicorn/CLPM log bytes only. No server or process operations."""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
F03 = HERE.parent


class CleanTerm(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="f03-clean-term-")
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        source = ast.parse((HERE / "keeper-local-f03-v5.py").read_text())
        stop = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "stop")
        helper = next(n for n in stop.body if isinstance(n, ast.FunctionDef) and n.name == "clean_shutdown_chain")
        namespace = {"OUT": self.out, "sha": lambda b: hashlib.sha256(b).hexdigest(),
                     "now": lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     "write": lambda p, data: p.write_text(json.dumps(data))}
        exec(compile(ast.Module(body=[helper], type_ignores=[]), "actual-clean-term-helper", "exec"), namespace)
        self.helper = namespace["clean_shutdown_chain"]
        self.native = F03 / "native-uvicorn-term-control"
        self.control = json.loads((self.native / "control.json").read_text())
        self.raw = (self.native / "server.log").read_bytes()
        self.pid = self.control["pid"]
        self.offset = self.raw.index(b"INFO:     Shutting down\n")

    def call(self, raw, pid=None, offset=None):
        (self.out / "backend-api.stdout.log").write_bytes(raw)
        return self.helper(pid or self.pid, self.offset if offset is None else offset)

    def test_actual_native_minus15_has_complete_ordered_chain(self):
        self.assertEqual(self.control["actualExitRc"], -15)
        self.assertEqual(self.control["requestedSignal"], "SIGTERM")
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), self.control["logSha256"])
        chain = self.call(self.raw)
        self.assertIsNotNone(chain)
        self.assertEqual(len(chain["eventsInOrder"]), 3)
        for item in chain["eventsInOrder"]:
            raw = self.raw[item["offsetStart"]:item["offsetEnd"]]
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["rawSha256"])
            self.assertEqual((self.out / item["file"]).read_bytes(), raw)

    def test_actual_v4_clpm_minus15_has_same_clean_chain(self):
        actual = F03 / "local-business-20261017210240-edfa831afd60"
        state = json.loads((actual / "current-f03-local-api.json").read_text())
        exit_record = json.loads((actual / "api-exit.json").read_text())
        signal_record = json.loads((actual / "api-stop-signal.json").read_text())
        raw = (actual / "backend-api.stdout.log").read_bytes()
        self.assertEqual(exit_record["rc"], -15)
        self.assertEqual(exit_record["pid"], state["apiPid"])
        self.assertIsNotNone(self.call(raw, state["apiPid"], signal_record["logOffsetBeforeSignal"]))

    def test_complete_before_signal_offset_is_rejected(self):
        self.assertIsNone(self.call(self.raw, offset=len(self.raw)))

    def test_missing_waiting_is_rejected(self):
        self.assertIsNone(self.call(self.raw.replace(b"INFO:     Waiting for application shutdown.\n", b"")))

    def test_missing_application_shutdown_complete_is_rejected(self):
        self.assertIsNone(self.call(self.raw.replace(b"INFO:     Application shutdown complete.\n", b"")))

    def test_wrong_finished_pid_is_rejected(self):
        self.assertIsNone(self.call(self.raw, pid=self.pid + 1))

    def test_reordered_finished_before_complete_is_rejected(self):
        complete = b"INFO:     Application shutdown complete.\n"
        finish = f"INFO:     Finished server process [{self.pid}]\n".encode()
        self.assertIsNone(self.call(self.raw.replace(complete + finish, finish + complete)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
