"""Independent read-only source validation and owned-temp archive recovery."""
import hashlib
import json
import pwd
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATUS = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态')
EVIDENCE = STATUS / '终局整改-证据'
CURRENT = EVIDENCE / '直接整改复验-20261017T093240Z'
OUT = CURRENT / 'docs-non-author'
ARCHIVE_ROOT = Path('/Users/zhangping/DEV/CLPM-evidence-archive/2026-10-17-final-audit')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


manifest = json.loads((EVIDENCE / 'evidence-manifest.json').read_text())
before_path = CURRENT / 'f08/evidence-manifest-before.json'
before = json.loads(before_path.read_text())
assert digest(before_path) == manifest['previous_manifest']['sha256']
source_rows = []
for entry in manifest['entries']:
    base = STATUS if entry['file'].startswith('G5') else EVIDENCE
    source = base / entry['file']
    row = {'file': entry['file'], 'source': str(source), 'bytes': source.stat().st_size,
           'sha256': digest(source), 'expectedBytes': entry['bytes'], 'expectedSha256': entry['sha256']}
    row['matches'] = row['bytes'] == row['expectedBytes'] and row['sha256'] == row['expectedSha256']
    source_rows.append(row)
old_sources = {entry['file']: (entry['bytes'], entry['sha256']) for entry in before['entries']}
new_sources = {entry['file']: (entry['bytes'], entry['sha256']) for entry in manifest['entries']}
assert len(source_rows) == 24 and all(row['matches'] for row in source_rows)
assert old_sources == new_sources

archive_manifest_path = ARCHIVE_ROOT / 'archive-manifest.json'
archive_manifest = json.loads(archive_manifest_path.read_text())
assert digest(archive_manifest_path) == digest(CURRENT / 'f08/archive-manifest.json')
archive = Path(archive_manifest['archive'])
stat = archive.stat()
archive_meta = {'path': str(archive), 'bytes': stat.st_size, 'sha256': digest(archive),
                'filesystemOwner': pwd.getpwuid(stat.st_uid).pw_name,
                'expectedBytes': archive_manifest['archive_bytes'],
                'expectedSha256': archive_manifest['archive_sha256'],
                'storageScope': archive_manifest['storage_scope']}
assert archive_meta['bytes'] == archive_meta['expectedBytes']
assert archive_meta['sha256'] == archive_meta['expectedSha256']
assert archive_meta['sha256'] == manifest['large_file_persistent_archive']['archive_sha256']
assert archive_meta['filesystemOwner'] == archive_manifest['filesystem_owner']

temporary = Path(tempfile.mkdtemp(prefix='clpm-docs-nonauthor-archive-'))
restored = []
try:
    with tarfile.open(archive, 'r:gz') as package:
        members = package.getmembers()
        expected = {row['member']: row for row in archive_manifest['entries']}
        assert len(members) == len(expected) == 3
        for member in members:
            assert member.name in expected and member.isfile()
            relative = Path(member.name)
            assert not relative.is_absolute() and '..' not in relative.parts
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
            entry = expected[member.name]
            row = {'member': member.name, 'restoredPath': str(target), 'bytes': target.stat().st_size,
                   'sha256': digest(target), 'expectedBytes': entry['bytes'], 'expectedSha256': entry['sha256']}
            row['matches'] = row['bytes'] == row['expectedBytes'] and row['sha256'] == row['expectedSha256']
            assert row['matches'] and new_sources[member.name] == (row['bytes'], row['sha256'])
            restored.append(row)
finally:
    shutil.rmtree(temporary)

result = {'observedAt': datetime.now(timezone.utc).isoformat(), 'archive': archive_meta,
          'archiveManifestSha256': digest(archive_manifest_path),
          'previousManifestSha256': digest(before_path), 'sourceEntries': source_rows,
          'sourceIdentityUnchangedFromPreviousManifest': old_sources == new_sources,
          'restored': restored, 'ownedTemporaryRemoved': not temporary.exists(),
          'sourcePassCount': sum(row['matches'] for row in source_rows),
          'restoredPassCount': len(restored), 'scope': 'same-host durable archive only; no offsite/cloud claim'}
(OUT / 'archive-independent-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'sourcePassCount': result['sourcePassCount'], 'restoredPassCount': result['restoredPassCount'],
                  'archiveSha256': archive_meta['sha256'], 'ownedTemporaryRemoved': result['ownedTemporaryRemoved']}, indent=2))
