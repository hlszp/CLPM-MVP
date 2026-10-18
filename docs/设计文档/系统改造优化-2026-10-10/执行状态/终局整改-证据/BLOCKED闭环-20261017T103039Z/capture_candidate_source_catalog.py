"""Read committed blobs and actual runtime bytes; no checkout or service action."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main(args):
    runtime = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')
    assert re.fullmatch('[a-f0-9]{40}', args.candidate_sha)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=runtime, text=True).strip()
    assert head == args.candidate_sha
    assert not subprocess.check_output(['git', 'diff', '--name-only', 'HEAD'], cwd=runtime).strip()
    listing = subprocess.check_output(['git', 'ls-tree', '-rz', '--full-tree', head], cwd=runtime)
    records = []
    cache = {}
    for entry in listing.split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, kind, object_id = metadata.decode().split()
        path = name.decode()
        assert kind == 'blob', 'External gitlinks must be reviewed explicitly: ' + path
        if object_id not in cache:
            cache[object_id] = subprocess.check_output(['git', 'cat-file', 'blob', object_id], cwd=runtime)
        blob = cache[object_id]
        actual_path = runtime / path
        actual = os.fsencode(os.readlink(actual_path)) if mode == '120000' else actual_path.read_bytes()
        record = {'path': path, 'mode': mode, 'gitBlob': object_id, 'committedBlobBytes': len(blob), 'committedBlobSha256': sha(blob), 'runtimeBytes': len(actual), 'runtimeSha256': sha(actual), 'runtimeMatchesCommittedBytes': actual == blob}
        if not record['runtimeMatchesCommittedBytes'] and blob.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            oid = re.search(rb'\noid sha256:([a-f0-9]{64})\n', blob)
            size = re.search(rb'\nsize ([0-9]+)\n?', blob)
            assert oid and size and oid[1].decode() == sha(actual) and int(size[1]) == len(actual), path
            record['runtimeMatchesVerifiedLfsObject'] = True
        else:
            assert actual == blob, path
        records.append(record)
    roots = {}
    for name in ['backend', 'frontend', 'e2e', 'scripts', 'deploy', '.github']:
        result = subprocess.run(['git', 'rev-parse', head + ':' + name], cwd=runtime, capture_output=True, text=True)
        roots[name] = {'rc': result.returncode, 'tree': result.stdout.strip() if result.returncode == 0 else None}
    result = {'observedAt': datetime.now(timezone.utc).isoformat(), 'candidateSha': head, 'commitIdentityRaw': subprocess.check_output(['git', 'show', '-s', '--format=raw', head], cwd=runtime, text=True), 'rootTree': subprocess.check_output(['git', 'rev-parse', head + '^{tree}'], cwd=runtime, text=True).strip(), 'moduleTrees': roots, 'trackedFileCount': len(records), 'trackedRuntimeBytes': sum(x['runtimeBytes'] for x in records), 'files': records, 'untrackedOrIgnoredNotIncluded': ['runtime backend/.env separately pinned', 'operational test TD profile mounted from main separately pinned', 'evidence tooling separately pinned', 'node_modules/venv and generated inputs separately identified'], 'boundary': 'Complete committed/runtime file identity, not process liveness or loaded-source proof'}
    out = Path(args.out)
    with out.open('x') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'path': str(out), 'sha256': sha(out.read_bytes()), 'candidateSha': head, 'trackedFiles': len(records)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--out', required=True)
    main(parser.parse_args())
