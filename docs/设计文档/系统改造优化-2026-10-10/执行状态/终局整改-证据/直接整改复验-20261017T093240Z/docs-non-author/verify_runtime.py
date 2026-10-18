"""Independent observational commands only; PostgreSQL transaction is READ ONLY."""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = Path('/Users/zhangping/DEV/CLPM-MVP')
STATUS = ROOT / 'docs/设计文档/系统改造优化-2026-10-10/执行状态'
PARENT = OUT.parent / 'f06/runtime-facts.json'
OBSERVED = datetime.now(timezone.utc).isoformat()


def run(args, cwd=None):
    completed = subprocess.run(args, cwd=cwd, env={**os.environ, 'LC_ALL': 'C'}, capture_output=True, text=True)
    return {'argv': args, 'cwd': str(cwd) if cwd else None, 'rc': completed.returncode,
            'stdout': completed.stdout, 'stderr': completed.stderr}


repositories = {}
for label, path in [('main', ROOT), ('candidate', Path('/Users/zhangping/DEV/clpm-remediation-20261017')),
                    ('runtime', Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015'))]:
    repositories[label] = {'head': run(['git', 'rev-parse', 'HEAD'], path),
                           'branch': run(['git', 'branch', '--show-current'], path),
                           'trackedStatus': run(['git', 'status', '--porcelain=v1', '--untracked-files=no'], path)}

discovery = run(['ps', '-axo', 'pid=,ppid=,pgid=,lstart=,command='])
selected = []
for line in discovery['stdout'].splitlines():
    fields = line.split(None, 8)
    if len(fields) != 9 or not fields[0].isdigit():
        continue
    command = fields[8]
    if command.startswith('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/') or (
            command.startswith('node /Users/zhangping/DEV/CLPM-MVP/frontend/') and 'vite' in command):
        selected.append(int(fields[0]))
assert selected
process_command = run(['ps', '-p', ','.join(map(str, selected)), '-o', 'pid=,ppid=,pgid=,lstart=,command='])
assert process_command['rc'] == 0
rows = []
for line in process_command['stdout'].splitlines():
    fields = line.split(None, 8)
    rows.append({'pid': int(fields[0]), 'ppid': int(fields[1]), 'pgid': int(fields[2]),
                 'startedAt': ' '.join(fields[3:8]), 'command': fields[8]})
workers = [row for row in rows if ' -m celery ' in row['command'] and ' worker ' in row['command']]
worker_pids = {row['pid'] for row in workers}
masters = [row for row in workers if row['ppid'] not in worker_pids]
master_pids = {row['pid'] for row in masters}
pools = [row for row in workers if row['ppid'] in master_pids]
beats = [row for row in rows if ' -m celery ' in row['command'] and ' beat ' in row['command']]
cwd_commands = {str(row['pid']): run(['lsof', '-a', '-p', str(row['pid']), '-d', 'cwd', '-Fn']) for row in rows}
ports = {str(port): run(['lsof', '-nP', '-iTCP:' + str(port), '-sTCP:LISTEN']) for port in [17101, 15666]}

# Hash config values inside PostgreSQL so credentials never reach the raw log.
# Parent hashes NULL as the literal string "NULL"; use the same explicit rule.
query = """BEGIN TRANSACTION READ ONLY;
SELECT json_build_object(
 'revision', (SELECT version_num FROM alembic_version LIMIT 1),
 'config', (SELECT json_agg(json_build_object(
   'key',key,'valueSha256',encode(sha256(convert_to(COALESCE(value,'NULL'),'UTF8')),'hex'),
   'updatedAt',updated_at,'updatedBy',updated_by) ORDER BY key) FROM sys_config),
 'policy', (SELECT json_agg(row_to_json(p)) FROM policy_publication_guard p));
COMMIT;"""
database = run(['docker', 'exec', 'clpm-mvp-postgres', 'psql', '-U', 'clpm', '-d', 'clpm',
                '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-c', query])
assert database['rc'] == 0, database['stderr']
data = json.loads(database['stdout'].strip())
canonical = json.dumps(data['config'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
config_hash = hashlib.sha256(canonical).hexdigest()
parent = json.loads(PARENT.read_text())
parent_config = parent['database']['configuration_identity']
config_comparison = {'rowCount': len(data['config']), 'sha256': config_hash,
                     'parentRowCount': parent_config['row_count'], 'parentSha256': parent_config['sha256'],
                     'matchesParentSnapshot': config_hash == parent_config['sha256'] and data['config'] == parent_config['rows'],
                     'kind': 'sorted key/value-sha256/updatedAt/updatedBy content snapshot; not one global revision'}
health = run(['curl', '--fail', '--silent', '--show-error', '--max-time', '5', 'http://127.0.0.1:17101/health'])
equivalence = run(['git', 'diff', 'c9e01f5d', 'b676ea59', '--', 'backend'], ROOT)
protected_expected = {
    '终局对标检查报告.md': '517e91b57f2e2ff3c182be30d32ffb329d9c7b9397e2d3395188f190fe167016',
    '终局整改-非作者复验回执.md': '54cc82ca97e64fb7fd5314b14b41d5444ccaaf719b8478651d919e7a50f853e6',
}
protected = []
for name, expected in protected_expected.items():
    path = STATUS / name
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    protected.append({'path': str(path), 'expectedSha256': expected, 'actualSha256': actual, 'matches': actual == expected})

result = {'observedAt': OBSERVED, 'repositories': repositories, 'processCommand': process_command,
          'processRows': rows, 'cwdCommands': cwd_commands, 'ports': ports,
          'workerStructure': {'masters': masters, 'pools': pools, 'beats': beats,
                              'masterCount': len(masters), 'poolCount': len(pools), 'beatCount': len(beats)},
          'databaseCommand': database, 'databasePayload': data, 'configurationComparison': config_comparison,
          'health': health, 'backendDiskEquivalence': equivalence, 'protectedDocuments': protected,
          'protectedExpectedShaProvenance': 'main coordinator current delegation supplied pre-work hashes; this run independently rehashes current files',
          'parentCaptureSha256': hashlib.sha256(PARENT.read_bytes()).hexdigest(),
          'scope': 'current cwd/disk/process/readonly DB only; no process memory SHA, remote tracking, historic PID health inference, service start/stop, or deployment'}
(OUT / 'runtime-independent-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
assert len(masters) == 1 and len(pools) == 4 and len(beats) == 1
assert data['revision'] == 'p702perm01' and config_comparison['matchesParentSnapshot']
assert all(row['matches'] for row in protected)
assert equivalence['rc'] == 0 and not equivalence['stdout']
assert health['rc'] == 0
print(json.dumps({'observedAt': OBSERVED, 'workerMasters': len(masters), 'poolChildren': len(pools), 'beat': len(beats),
                  'schema': data['revision'], 'configRows': len(data['config']), 'configSha256': config_hash,
                  'protectedHashesMatch': all(row['matches'] for row in protected)}, indent=2))
