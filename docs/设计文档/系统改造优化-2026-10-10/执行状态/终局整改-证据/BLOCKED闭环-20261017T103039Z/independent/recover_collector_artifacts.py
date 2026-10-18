"""Recover a closed evidence collector directory without product or network clients.

The collector's manifest and each gzip reference are checked before bytes are
copied to a new output directory. JSON bodies are not rewritten or projected.
This is artifact recovery, never a statement that business barriers passed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

from independent_support import InvalidEvidence, MissingEvidence, digest, write_new


def json_value(raw, path):
    def reject_constant(value):
        raise InvalidEvidence(f"nonfinite JSON constant in {path}: {value}")

    try:
        return json.loads(raw, parse_constant=reject_constant)
    except (UnicodeError, ValueError) as exc:
        raise InvalidEvidence(f"invalid JSON in {path}: {exc}") from exc


def safe_path(root, name):
    if (
        not isinstance(name, str)
        or not name
        or Path(name).is_absolute()
        or ".." in Path(name).parts
        or str(Path(name)) != name
    ):
        raise InvalidEvidence(f"not a relative artifact path: {name!r}")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise InvalidEvidence(f"missing or escaped artifact: {name!r}")
    return path


def references(value):
    if isinstance(value, dict):
        if {"file", "rawSha256", "gzipSha256"}.issubset(value):
            yield value
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def recover(source, out, max_object_bytes=256 * 1024 * 1024):
    source = Path(source).resolve()
    out = Path(out).resolve()
    if out.exists() or out.is_relative_to(source):
        raise InvalidEvidence("output must be a fresh directory outside source")
    manifest_path = source / "sha256-manifest.json"
    if not manifest_path.is_file():
        raise MissingEvidence("closed collector sha256-manifest.json is absent")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json_value(manifest_bytes, manifest_path)
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise MissingEvidence("nonempty collector manifest.files is required")
    seen = {}
    for item in entries:
        name = item.get("file")
        path = safe_path(source, name)
        if name in seen:
            raise InvalidEvidence(f"duplicate manifest artifact: {name}")
        if path.stat().st_size != item.get("bytes") or digest(path) != item.get(
            "sha256"
        ):
            raise InvalidEvidence(f"manifest byte/hash mismatch: {name}")
        seen[name] = {"path": path, "entry": item}
    actual_files = {
        str(path.relative_to(source))
        for path in source.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_files != set(seen):
        raise InvalidEvidence(
            "collector manifest does not enumerate the exact closed directory: "
            + repr(sorted(actual_files ^ set(seen)))
        )
    all_refs = []
    for name, item in seen.items():
        path = item["path"]
        if name.endswith(".json"):
            all_refs.extend(references(json_value(path.read_bytes(), name)))
        elif name.endswith(".jsonl"):
            for number, line in enumerate(path.read_bytes().splitlines(), start=1):
                if line.strip():
                    all_refs.extend(references(json_value(line, f"{name}:{number}")))
    if not all_refs:
        raise MissingEvidence("no content-addressed gzip references in collector")
    restored = {}
    for ref in all_refs:
        name = ref["file"]
        item = seen.get(name)
        if item is None:
            raise InvalidEvidence(f"gzip reference is outside manifest: {name}")
        path = item["path"]
        if (
            not name.endswith(".json.gz")
            or item["entry"]["sha256"] != ref["gzipSha256"]
        ):
            raise InvalidEvidence(f"gzip reference hash/type mismatch: {name}")
        raw_gzip = path.read_bytes()
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw_gzip)) as stream:
                raw = stream.read(max_object_bytes + 1)
        except (OSError, EOFError) as exc:
            raise InvalidEvidence(f"invalid gzip body {name}: {exc}") from exc
        if len(raw) > max_object_bytes:
            raise MissingEvidence(f"object exceeds explicit recovery limit: {name}")
        raw_sha = hashlib.sha256(raw).hexdigest()
        if raw_sha != ref["rawSha256"]:
            raise InvalidEvidence(f"raw decompressed hash mismatch: {name}")
        value = json_value(raw, name)
        row_count = len(value) if isinstance(value, list) else None
        if ref.get("rows") != row_count:
            raise InvalidEvidence(f"raw reference row count mismatch: {name}")
        if name in restored:
            if restored[name]["rawSha256"] != raw_sha:
                raise InvalidEvidence(f"inconsistent repeated reference: {name}")
            continue
        restored[name] = {
            "raw": raw,
            "rawSha256": raw_sha,
            "gzipSha256": ref["gzipSha256"],
            "rows": row_count,
        }
    object_files = {name for name in seen if name.endswith(".json.gz")}
    if object_files != set(restored):
        raise InvalidEvidence("unreferenced or absent content-addressed gzip object")
    # A running collector or changed file must not produce a successful recovery.
    if manifest_path.read_bytes() != manifest_bytes:
        raise InvalidEvidence("collector manifest changed during recovery")
    for name, item in seen.items():
        if digest(item["path"]) != item["entry"]["sha256"]:
            raise InvalidEvidence(f"collector source changed during recovery: {name}")
    out.mkdir(parents=True, exist_ok=False)
    index = []
    for name, item in sorted(restored.items()):
        relative = name.removesuffix(".gz")
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(item["raw"])
        index.append(
            {
                "sourceGzip": name,
                "gzipSha256": item["gzipSha256"],
                "restored": relative,
                "rawSha256": item["rawSha256"],
                "bytes": len(item["raw"]),
                "rows": item["rows"],
            }
        )
    result = {
        "artifactRecoveryVerdict": "PASS",
        "businessAcceptance": "NOT_EVALUATED",
        "source": str(source),
        "sourceManifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "manifestFileCount": len(entries),
        "gzipReferenceCount": len(all_refs),
        "restoredObjects": index,
        "boundary": "Raw decompressed bytes preserved; no field mapping or business conclusions",
    }
    write_new(out / "recovery-index.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-object-bytes", type=int, default=256 * 1024 * 1024)
    args = parser.parse_args()
    try:
        if args.max_object_bytes < 1:
            raise InvalidEvidence("max-object-bytes must be positive")
        result = recover(args.source, args.out, args.max_object_bytes)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InvalidEvidence, MissingEvidence, OSError) as exc:
        code = 2 if isinstance(exc, MissingEvidence) else 1
        print(
            json.dumps(
                {
                    "artifactRecoveryVerdict": "BLOCKED" if code == 2 else "REWORK",
                    "businessAcceptance": "NOT_EVALUATED",
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return code


if __name__ == "__main__":
    raise SystemExit(main())
