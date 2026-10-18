"""Extract the actual reader only; replay real prior shutdown bytes, no services."""

from __future__ import annotations
import argparse
import ast
import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--driver", type=Path, required=True)
ARGS, REST = parser.parse_known_args()
WAITING = b"INFO:     Waiting for application shutdown.\n"
AT = datetime.datetime(2026, 10, 17, 12, tzinfo=datetime.timezone.utc)


def row(message="停止 Celery Worker 进程..."):
    return (
        json.dumps(
            {
                "timestamp": (AT + datetime.timedelta(seconds=1)).isoformat(),
                "logger": "app.main",
                "message": message,
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode()


class Barrier(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="f03-barrier-pure-")
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        tree = ast.parse(ARGS.driver.read_text())
        stop = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "stop"
        )
        reader = next(
            n
            for n in stop.body
            if isinstance(n, ast.FunctionDef) and n.name == "read_barrier"
        )
        namespace = {
            "OUT": self.out,
            "json": json,
            "datetime": datetime,
            "sha": lambda raw: hashlib.sha256(raw).hexdigest(),
            "now": lambda: AT.isoformat(),
            "write": lambda p, data: p.write_text(json.dumps(data)),
        }
        exec(
            compile(
                ast.Module(body=[reader], type_ignores=[]), "actual-reader", "exec"
            ),
            namespace,
        )
        self.reader = namespace["read_barrier"]

    def log(self, raw):
        (self.out / "backend-api.stdout.log").write_bytes(raw)

    def test_real_prior_atexit_stop_is_not_lifespan(self):
        path = (
            HERE.parent
            / "local-business-20261017203500-edfa831afd60/backend-api.stdout.log"
        )
        raw = path.read_bytes()
        self.assertNotIn(WAITING, raw)
        offset = raw.index(b"INFO:     Shutting down\n")
        self.log(raw)
        self.assertIsNone(self.reader(offset, AT)[1])

    def test_lifespan_then_stop_accepts_and_preserves_both_originals(self):
        self.log(WAITING + row())
        item = self.reader(0, AT)[1]
        self.assertIsNotNone(item)
        if "uvicornWaitingForApplicationShutdown" in item:
            waiting = item["uvicornWaitingForApplicationShutdown"]
            self.assertEqual(waiting["offsetStart"], 0)
            self.assertEqual(waiting["offsetEnd"], len(WAITING))
            self.assertEqual(waiting["rawSha256"], hashlib.sha256(WAITING).hexdigest())
            self.assertEqual(
                (self.out / "lifespan-waiting.raw.log").read_bytes(), WAITING
            )
            self.assertEqual(
                (self.out / "watchdog-stop-barrier.raw.log").read_bytes(), row()
            )

    def test_app_shutting_down_alone_does_not_replace_uvicorn_entry(self):
        self.log(row("Shutting down CLPM-MVP") + row())
        self.assertIsNone(self.reader(0, AT)[1])

    def test_uvicorn_before_signal_offset_is_not_current_entry(self):
        self.log(WAITING + row())
        self.assertIsNone(self.reader(len(WAITING), AT)[1])

    def test_stop_before_entry_cannot_be_used_retroactively(self):
        self.log(row() + WAITING)
        self.assertIsNone(self.reader(0, AT)[1])

    def test_wrong_uvicorn_raw_line_rejected(self):
        self.log(b"OTHER: Waiting for application shutdown.\n" + row())
        self.assertIsNone(self.reader(0, AT)[1])

    def test_poll_boundary_preserves_waiting_for_same_signal(self):
        self.log(WAITING)
        offset, item = self.reader(0, AT)
        self.assertIsNone(item)
        with (self.out / "backend-api.stdout.log").open("ab") as stream:
            stream.write(row())
        self.assertIsNotNone(self.reader(offset, AT)[1])

    def test_prior_signal_waiting_is_not_reused(self):
        self.log(WAITING)
        offset, _ = self.reader(0, AT)
        with (self.out / "backend-api.stdout.log").open("ab") as stream:
            stream.write(row())
        self.assertIsNone(
            self.reader(offset, AT + datetime.timedelta(microseconds=1))[1]
        )

    def test_truncated_waiting_line_cannot_qualify(self):
        self.log(WAITING[:-1])
        offset, item = self.reader(0, AT)
        self.assertIsNone(item)
        self.assertEqual(offset, 0)


if __name__ == "__main__":
    unittest.main(argv=[__file__, *REST], verbosity=2)
