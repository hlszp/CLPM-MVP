"""Offline negative controls for the native identity evidence parser."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "identity", Path(__file__).with_name("observe_capacity_native_identity.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(pid, ppid, role, birth="Sat Oct 17 20:12:29 2026", state="S"):
    command = (
        f"{m.PYTHON} -m uvicorn app.main:app --port 17101"
        if role == "api"
        else f"{m.PYTHON} -m celery -A app.tasks.celery_app {role} -l info"
    )
    return f"{pid} {ppid} {birth} {state} {command}"


class IdentityParser(unittest.TestCase):
    def test_actual_birth_separate_from_state_and_ancestry(self):
        lines = [
            row(10, 1, "api"),
            row(11, 10, "beat", state="T"),
            row(12, 10, "worker"),
        ]
        lines += [row(pid, 12, "worker") for pid in range(13, 17)]
        parsed = m.parse_owned("\n".join(lines))
        self.assertEqual(len(parsed), 7)
        self.assertEqual(
            [x["role"] for x in parsed],
            ["api", "beat", "worker_master"] + ["worker_child"] * 4,
        )
        self.assertEqual(parsed[1]["state"], "T")
        self.assertEqual(parsed[1]["startedAtLocal"], "Sat Oct 17 20:12:29 2026")
        self.assertEqual([x["rawLine"] for x in parsed], lines)

    def test_pid_reuse_changes_identity_even_if_argv_equal(self):
        a = m.parse_owned(row(12, 10, "worker"))[0]
        b = m.parse_owned(row(12, 10, "worker", birth="Sat Oct 17 21:12:29 2026"))[0]
        self.assertNotEqual(a["identity"], b["identity"])
        self.assertEqual(a["argv"], b["argv"])

    def test_state_change_does_not_invent_a_new_birth(self):
        a = m.parse_owned(row(12, 10, "worker", state="S"))[0]
        b = m.parse_owned(row(12, 10, "worker", state="R+"))[0]
        self.assertEqual(a["identity"], b["identity"])

    def test_orphan_not_claimed_as_owned_pool(self):
        self.assertEqual(
            m.parse_owned(row(99, 98, "worker"))[0]["role"], "unattached_worker"
        )

    def test_other_interpreter_or_similar_prefix_excluded(self):
        text = row(10, 1, "api").replace(m.PYTHON, "/other/.venv/bin/python")
        text += "\n" + row(11, 1, "worker").replace(m.PYTHON, m.PYTHON + "3")
        self.assertEqual(m.parse_owned(text), [])

    def test_malformed_relevant_ps_cannot_silently_be_empty(self):
        with self.assertRaises(ValueError):
            m.parse_owned(f"12 1 T {m.PYTHON} -m celery worker ")


if __name__ == "__main__":
    unittest.main()
