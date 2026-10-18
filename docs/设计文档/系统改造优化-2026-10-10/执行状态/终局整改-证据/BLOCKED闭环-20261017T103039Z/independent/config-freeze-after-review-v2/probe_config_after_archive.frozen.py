"""Independent disk proof, borrowing only the author's synthetic edge fixture."""

import argparse
import ast
import asyncio
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def load(path, name):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


async def run(args):
    base = Path(__file__).resolve().parent.parent
    author = load(base / "f02/test_freeze_configuration_after_snapshot.py", "synthetic_pg_http_fixture")
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    for mvcc, changed, revision in [(False, False, 1), (True, False, 1), (False, True, 1), (True, True, 1), (False, False, 2), (True, False, 2)]:
        subject = load(args.source, "independent_config_subject")
        root = args.out / f"mvcc-{int(mvcc)}-changed-{int(changed)}-api-{revision}"
        checks, errors = {}, []
        with pytest.MonkeyPatch.context() as patcher:
            invocation, db = author.prepare(subject, root, patcher, different_mvcc=mvcc, changed=changed, api_revision=revision)
            rc = await subject.run(invocation)
        out = Path(invocation.out)
        report = json.loads((out / "config-freeze.json").read_bytes())
        expected_rc = 2 if changed or revision != 1 else 0
        checks["originalRejectionSemantics"] = rc == expected_rc and report["status"] == ("BLOCKED" if expected_rc else "FROZEN")
        checks["connectionClosedTwoActualReadOnlySnapshots"] = db.closed and db.phase == 1
        for index, label, raw_dir, zipped_dir in [(0, "before", "pg-raw", "pg-canonical"), (1, "after", "pg-after-raw", "pg-after-canonical")]:
            try:
                raw_files = sorted((out / raw_dir).glob("*.json"))
                assert len(raw_files) == 25, f"{label}: original table count {len(raw_files)} !=25"
                originals = {}
                for path in raw_files:
                    table = path.stem
                    raw = path.read_bytes()
                    assert raw == db.raw[index][table].encode(), (label, table, "original JSON bytes changed")
                    restored = json.loads(gzip.decompress((out / zipped_dir / (table + ".json.gz")).read_bytes()))
                    assert restored == json.loads(raw) == db.tables[index][table], (label, table, "canonical restore differs")
                    originals[table] = restored
                assert len(originals["loop_ledger"]) == 1000 and len(originals["loop_tag_mapping"]) == 7000 and len(originals["plant_node"]) == 20
                identity_raw = (out / ("pg-" + label + "-identity.json")).read_bytes()
                assert json.loads(identity_raw) == db.identities[index]
                phase = report["postgresSnapshots"][label]
                assert phase["tableCount"] == 25 and len(phase["files"]) == 51
                paths = set()
                for ref in phase["files"]:
                    assert ref["file"] not in paths
                    paths.add(ref["file"])
                    raw = (out / ref["file"]).read_bytes()
                    assert len(raw) == ref["bytes"] and digest(raw) == ref["sha256"]
                checks[label + "ArchiveRestoresAllOriginalBytesAnd51Hashes"] = True
            except (AssertionError, KeyError, FileNotFoundError) as error:
                checks[label + "ArchiveRestoresAllOriginalBytesAnd51Hashes"] = False
                errors.append(str(error))
        manifest = json.loads((out / "sha256-manifest.json").read_bytes())
        checks["allOutputManifestHashes"] = all((out / r["file"]).stat().st_size == r["bytes"] and digest((out / r["file"]).read_bytes()) == r["sha256"] for r in manifest["files"])
        results.append({"differentMvcc": mvcc, "changedDigest": changed, "apiRevision": revision, "actualRc": rc, "expectedRc": expected_rc, "checks": checks, "errors": errors, "status": report["status"], "sqlTranscriptSha256": digest(canonical(db.calls)), "configDigest": report.get("configDigest")})
    result = {"sourcePath": str(args.source), "sourceSha256": digest(args.source.read_bytes()), "counterexampleSha256": digest(Path(__file__).read_bytes()), "productImports": 0, "networkCalls": 0, "signals": 0, "allPassed": all(all(item["checks"].values()) for item in results), "cases": results}
    (args.out / "independent-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=False))
    return 0 if result["allPassed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
