"""Non-author, temp-file and subprocess-stub controls only. No Docker/DB execution."""

import contextlib
from datetime import UTC, datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

D = Path(__file__).resolve().parent
E = D.parents[1]
SOURCE = E / "f02/synthetic-capacity-dump-adapter-root/pg_dump_test_container.py"
EXPECTED_SHA = "c27d789a2c8308cfbeebc692d4565d2460b6c65fe2f3bc295fc72f51469fb3c8"
EXPECTED_TARGET = E / "f02/synthetic-capacity-binding-actual-root-v1/before.dump"
EXPECTED_ID = "4ec91098b6ce86a4a6edc34ffa7ef9dcad5e92ade6206db3df9d9ef7561aa3c0"
SECRET = "INDEPENDENT_SYNTHETIC_SECRET_NEVER_A_REAL_PASSWORD"
DATA = b"PGDMP_INDEPENDENT_STUB_ARCHIVE_BYTES_NOT_A_DATABASE"
OBSERVATIONS = []


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class AdapterControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="clpm-independent-pgdump-")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.target = self.folder / "before.dump"
        spec = importlib.util.spec_from_file_location("independent_dump_adapter_under_review", D / "source-original.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.assertEqual(self.module.CONTAINER, EXPECTED_ID)
        self.assertEqual(self.module.TARGET, EXPECTED_TARGET)
        self.module.TARGET = self.target
        self.calls = []
        self.info = {"Id": EXPECTED_ID, "Name": "/clpm-mvp-postgres", "Image": "postgres:16", "Ports": {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "17102"}, {"HostIp": "::", "HostPort": "17102"}]}}
        self.version = b"pg_dump (PostgreSQL) 16.14 (Debian 16.14-1.pgdg13+1)\n"
        self.preflight_rc = 0
        self.dump_rc = 0
        self.stderr = io.StringIO()
        self.module.subprocess = SimpleNamespace(run=self.fake_run)

    def argv(self, host="127.0.0.1"):
        return ["--host=" + host, "--port=17102", "--username=clpm", "--dbname=clpm_capacity_20261017_s01", "--format=custom", "--no-owner", "--file=" + str(self.target)]

    def fake_run(self, argv, **kwargs):
        # No fallback to the real subprocess module exists in this fixture.
        self.calls.append({"argv": list(argv), "timeout": kwargs.get("timeout"), "capture_output": kwargs.get("capture_output"), "hasOutputFile": "stdout" in kwargs, "passwordPresentInEnvironment": os.environ.get("PGPASSWORD") == SECRET})
        self.assertNotIn(SECRET, json.dumps(argv))
        if argv[:2] == ["docker", "inspect"]:
            self.assertEqual(argv[-1], EXPECTED_ID)
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(kwargs["timeout"], 15)
            return SimpleNamespace(returncode=self.preflight_rc, stdout=json.dumps(self.info).encode(), stderr=b"INSPECT_STDERR_RAW\n")
        if argv == ["docker", "exec", EXPECTED_ID, "pg_dump", "--version"]:
            self.assertEqual(kwargs["timeout"], 15)
            return SimpleNamespace(returncode=0, stdout=self.version, stderr=b"VERSION_STDERR_RAW\n")
        expected = ["docker", "exec", "-e", "PGPASSWORD", EXPECTED_ID, "pg_dump", "--host=127.0.0.1", "--port=5432", "--username=clpm", "--dbname=clpm_capacity_20261017_s01", "--format=custom", "--no-owner", "--no-password"]
        self.assertEqual(argv, expected)
        self.assertEqual(kwargs["timeout"], 60)
        self.assertIn("stdout", kwargs)
        # A real dump inherits adapter stderr; the exact caller captures that fd.
        print("DUMP_STDERR_RAW_MARKER", file=self.stderr)
        kwargs["stdout"].write(DATA)
        return SimpleNamespace(returncode=self.dump_rc)

    def invoke(self, argv=None, password=SECRET):
        env = {"PGPASSWORD": password} if password is not None else {}
        with patch.dict(os.environ, env, clear=False):
            if password is None:
                os.environ.pop("PGPASSWORD", None)
            return self.module.main(self.argv() if argv is None else argv)

    def record(self, **extra):
        OBSERVATIONS.append({"case": self.id().split(".")[-1], "calls": self.calls, **extra})

    def test_happy_path_exact_same_container_v16_and_private_file(self):
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.target.read_bytes(), DATA)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        before = json.loads((self.folder / "dump-adapter-before.json").read_text())
        result = json.loads((self.folder / "dump-adapter-result.json").read_text())
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["bytes"], len(DATA))
        self.assertEqual(result["sha256"], digest(DATA))
        self.assertEqual(before["checks"]["version"]["rc"], 0)
        self.assertEqual((self.folder / "dump-adapter-version.stdout.log").read_bytes(), self.version)
        self.assertEqual((self.folder / "dump-adapter-identity.stderr.log").read_bytes(), b"INSPECT_STDERR_RAW\n")
        for path in self.folder.iterdir():
            self.assertNotIn(SECRET.encode(), path.read_bytes())
        self.record(mode=oct(self.target.stat().st_mode & 0o777), before=before, result=result, originalDumpStderrInherited=self.stderr.getvalue())

    def test_exact_original_target_and_localhost_contract(self):
        argv = self.argv("localhost")
        self.module.validate(argv, self.target)
        self.assertEqual(self.invoke(argv), 0)
        # Real hard-coded target validation is tested without touching that file.
        original = self.argv()
        original[-1] = "--file=" + str(EXPECTED_TARGET)
        self.module.validate(original, EXPECTED_TARGET)
        self.record()

    def test_wrong_args_and_scope_are_refused_before_any_subprocess(self):
        controls = [[], self.argv() + ["--schema=public"]]
        for index, bad in ((0, "--host=192.0.2.1"), (1, "--port=5432"), (2, "--username=postgres"), (3, "--dbname=clpm"), (4, "--format=plain"), (5, "--clean"), (6, "--file=/tmp/outside.dump")):
            args = self.argv(); args[index] = bad; controls.append(args)
        for args in controls:
            with self.assertRaises(ValueError):
                self.invoke(args)
        self.assertFalse(self.calls)
        self.assertFalse(self.target.exists())
        self.record(rejectedControls=len(controls))

    def test_missing_password_refused_before_any_subprocess(self):
        with self.assertRaises(ValueError):
            self.invoke(password=None)
        self.assertFalse(self.calls)
        self.record()

    def test_existing_dump_is_never_overwritten(self):
        self.target.write_bytes(b"ORIGINAL_BYTES_MUST_SURVIVE")
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(self.target.read_bytes(), b"ORIGINAL_BYTES_MUST_SURVIVE")
        self.assertFalse(self.calls)
        self.record()

    def test_dangling_symlink_is_refused(self):
        self.target.symlink_to(self.folder / "absent")
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertTrue(self.target.is_symlink())
        self.assertFalse(self.calls)
        self.record()

    def test_symlink_parent_is_refused(self):
        real = self.folder / "real"; real.mkdir()
        link = self.folder / "alias"; link.symlink_to(real, target_is_directory=True)
        self.module.TARGET = link / "before.dump"
        args = self.argv(); args[-1] = "--file=" + str(self.module.TARGET)
        with self.assertRaises(ValueError):
            self.invoke(args)
        self.assertFalse(self.calls)
        self.record()

    def test_wrong_container_id_name_or_image_never_dump(self):
        for key, value in (("Id", "another-container"), ("Name", "/other-postgres"), ("Image", "postgres:15")):
            original = self.info[key]; self.info[key] = value
            with self.assertRaises(ValueError):
                self.invoke()
            self.info[key] = original
        self.assertTrue(all(call["argv"][:2] == ["docker", "inspect"] for call in self.calls))
        self.record()

    def test_missing_or_wrong_port_mapping_never_dump(self):
        for ports in ({}, {"5432/tcp": []}, {"5432/tcp": [{"HostPort": "5432"}]}, {"5432/tcp": [{"HostPort": "17102"}, {"HostPort": "17103"}]}):
            self.info["Ports"] = ports
            with self.assertRaises(ValueError):
                self.invoke()
        self.assertEqual(len(self.calls), 4)
        self.record()

    def test_wrong_major_or_unrecognized_version_never_dump(self):
        for version in (b"pg_dump (PostgreSQL) 15.17\n", b"pg_dump (PostgreSQL) 17.0\n", b"not-pg-dump\n", b""):
            self.version = version
            with self.assertRaises(ValueError):
                self.invoke()
        self.assertFalse(any(call["hasOutputFile"] for call in self.calls))
        self.record()

    def test_failed_inspect_never_runs_version_or_dump(self):
        self.preflight_rc = 71
        with self.assertRaises(ValueError):
            self.invoke()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.target.exists())
        self.record()

    def test_dump_nonzero_preserves_partial_file_and_returns_real_rc(self):
        self.dump_rc = 23
        self.assertEqual(self.invoke(), 23)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        result = json.loads((self.folder / "dump-adapter-result.json").read_text())
        self.assertEqual(result["rc"], 23)
        self.assertEqual(result["sha256"], digest(DATA))
        self.record(result=result, originalDumpStderrInherited=self.stderr.getvalue())


def main():
    raw = SOURCE.read_bytes()
    assert digest(raw) == EXPECTED_SHA
    copy = D / "source-original.py"
    assert not copy.exists()
    copy.write_bytes(raw)
    started = datetime.now(UTC).isoformat()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AdapterControls)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert SOURCE.read_bytes() == raw
    artifact = {"source": {"path": str(SOURCE), "sha256Before": EXPECTED_SHA, "sha256After": digest(SOURCE.read_bytes()), "bytes": len(raw)}, "startedAt": started, "finishedAt": datetime.now(UTC).isoformat(), "testsRun": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "actualDockerCalls": 0, "actualDatabaseCalls": 0, "actualDumpCalls": 0, "allSubprocessCallsStubbed": True, "ownTemporaryDirectoriesCleaned": True, "observations": OBSERVATIONS}
    (D / "result.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
