"""Synthetic artifact-only recovery controls; never touch a business service."""

import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from independent_support import InvalidEvidence, MissingEvidence
from recover_collector_artifacts import recover


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        (self.source / "objects").mkdir(parents=True)
        self.raw = b'[{"loop_id":"synthetic-loop","payload":"{\\"untouched\\":true}"}]'
        self.name = "objects/ledger-synthetic.json.gz"
        compressed = gzip.compress(self.raw, mtime=0)
        (self.source / self.name).write_bytes(compressed)
        self.ref = {
            "file": self.name,
            "rawSha256": hashlib.sha256(self.raw).hexdigest(),
            "gzipSha256": hashlib.sha256(compressed).hexdigest(),
            "rows": 1,
        }
        self.write_observation()

    def tearDown(self):
        self.temporary.cleanup()

    def write_observation(self):
        (self.source / "observations.jsonl").write_text(
            json.dumps({"artifacts": {"ledger": self.ref}}) + "\n"
        )
        self.manifest()

    def manifest(self):
        files = []
        for path in sorted(self.source.rglob("*")):
            if path.is_file() and path.name != "sha256-manifest.json":
                files.append(
                    {
                        "file": str(path.relative_to(self.source)),
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        (self.source / "sha256-manifest.json").write_text(json.dumps({"files": files}))

    def test_exact_raw_bytes_and_nested_json_strings_survive(self):
        result = recover(self.source, self.root / "restored")
        self.assertEqual(result["artifactRecoveryVerdict"], "PASS")
        self.assertEqual(result["businessAcceptance"], "NOT_EVALUATED")
        restored = self.root / "restored" / "objects/ledger-synthetic.json"
        self.assertEqual(restored.read_bytes(), self.raw)
        self.assertIsInstance(json.loads(restored.read_bytes())[0]["payload"], str)

    def test_manifest_byte_change_refuses_without_output(self):
        (self.source / "observations.jsonl").write_text("{}\n")
        with self.assertRaisesRegex(InvalidEvidence, "byte/hash mismatch"):
            recover(self.source, self.root / "restored")
        self.assertFalse((self.root / "restored").exists())

    def test_rehashed_envelope_cannot_hide_wrong_raw_hash(self):
        self.ref["rawSha256"] = "0" * 64
        self.write_observation()
        with self.assertRaisesRegex(InvalidEvidence, "decompressed hash mismatch"):
            recover(self.source, self.root / "restored")

    def test_row_denominator_mismatch_refused(self):
        self.ref["rows"] = 1000
        self.write_observation()
        with self.assertRaisesRegex(InvalidEvidence, "row count mismatch"):
            recover(self.source, self.root / "restored")

    def test_duplicate_manifest_path_refused(self):
        path = self.source / "sha256-manifest.json"
        payload = json.loads(path.read_text())
        payload["files"].append(payload["files"][0])
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(InvalidEvidence, "duplicate manifest"):
            recover(self.source, self.root / "restored")

    def test_unlisted_file_cannot_be_ignored(self):
        (self.source / "omitted-failure.log").write_text("synthetic failure")
        with self.assertRaisesRegex(InvalidEvidence, "exact closed directory"):
            recover(self.source, self.root / "restored")

    def test_orphan_object_refused(self):
        (self.source / "objects/orphan.json.gz").write_bytes(gzip.compress(b"[]"))
        self.manifest()
        with self.assertRaisesRegex(InvalidEvidence, "unreferenced"):
            recover(self.source, self.root / "restored")

    def test_missing_closed_manifest_is_blocked(self):
        (self.source / "sha256-manifest.json").unlink()
        with self.assertRaises(MissingEvidence):
            recover(self.source, self.root / "restored")

    def test_limit_refuses_instead_of_truncation(self):
        with self.assertRaisesRegex(MissingEvidence, "explicit recovery limit"):
            recover(self.source, self.root / "restored", max_object_bytes=1)
        self.assertFalse((self.root / "restored").exists())

    def test_no_overwrite_or_source_output(self):
        with self.assertRaisesRegex(InvalidEvidence, "fresh directory"):
            recover(self.source, self.source / "restored")
        out = self.root / "restored"
        out.mkdir()
        with self.assertRaisesRegex(InvalidEvidence, "fresh directory"):
            recover(self.source, out)


if __name__ == "__main__":
    unittest.main()
