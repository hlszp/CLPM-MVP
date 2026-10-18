"""Keeper read-only PG scope snapshot; compare actual columns to pre-frozen rows."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

import asyncpg
from dotenv import dotenv_values


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


async def main(args):
    runtime = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')
    cfg = dotenv_values(runtime / 'backend/.env')
    assert cfg['POSTGRES_DB'] == 'clpm_capacity_20261017_s01'
    assert cfg['POSTGRES_HOST'] in ('localhost', '127.0.0.1') and cfg['POSTGRES_PORT'] == '17102'
    actual_sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=runtime, text=True).strip()
    assert actual_sha == args.candidate_sha
    assert not subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'backend'], cwd=runtime).strip()
    freeze_dir = Path(args.config_freeze)
    manifest_path = freeze_dir / 'sha256-manifest.json'
    manifest_bytes = manifest_path.read_bytes()
    assert digest(manifest_bytes) == args.config_freeze_manifest_sha256
    listed = json.loads(manifest_bytes)['files']
    assert len({item['file'] for item in listed}) == len(listed)
    by_name = {item['file']: item for item in listed}
    originals = {}
    for name in ['config-freeze.json', 'pg-canonical/plant_node.json.gz', 'pg-canonical/loop_ledger.json.gz']:
        item = by_name[name]
        raw = (freeze_dir / name).read_bytes()
        assert type(item['bytes']) is int and item['bytes'] >= 0
        assert len(raw) == item['bytes'] and digest(raw) == item['sha256'], name
        originals[name] = raw
    freeze_bytes = originals['config-freeze.json']
    freeze = json.loads(freeze_bytes)
    assert freeze['status'] == 'FROZEN' and freeze['candidateSha'] == actual_sha
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    plan = {
        'plant_node': ('id', 'parent_id', 'is_kpi_enabled'),
        'loop_ledger': ('id', 'tag_name', 'unit_id', 'complex_loop_group_id'),
    }
    sql = {table: f"SELECT COALESCE(jsonb_agg(to_jsonb(s) ORDER BY id),'[]'::jsonb)::text FROM (SELECT {','.join(fields)} FROM {table}) s" for table, fields in plan.items()}
    started = now()
    db = await asyncpg.connect(host=cfg['POSTGRES_HOST'], port=int(cfg['POSTGRES_PORT']), user=cfg['POSTGRES_USER'], password=cfg['POSTGRES_PASSWORD'], database=cfg['POSTGRES_DB'])
    try:
        async with db.transaction(isolation='repeatable_read', readonly=True):
            tx = dict(await db.fetchrow("SELECT current_database() AS database, current_setting('transaction_read_only') AS readonly, current_setting('transaction_isolation') AS isolation, txid_current_snapshot()::text AS snapshot, clock_timestamp()::text AS clock"))
            assert tx['readonly'] == 'on' and tx['isolation'] == 'repeatable read'
            raw = {table: (await db.fetchval(query)).encode() for table, query in sql.items()}
            tx['readCompleteAt'] = now()
        tx['transactionEndedAt'] = now()
    finally:
        await db.close()
    checks = {}
    refs = []
    for table, fields in plan.items():
        actual = json.loads(raw[table])
        before_path = freeze_dir / 'pg-canonical' / (table + '.json.gz')
        before_raw = originals['pg-canonical/' + table + '.json.gz']
        before = json.loads(gzip.decompress(before_raw))
        expected = [{field: row[field] for field in fields} for row in before]
        expected.sort(key=lambda row: row['id'])
        checks[table] = {'equalFrozenColumns': actual == expected, 'rows': len(actual), 'frozenRows': len(expected), 'columns': fields}
        path = out / (table + '.raw.json')
        with path.open('xb') as stream:
            stream.write(raw[table])
        refs.extend([{'path': str(path), 'sha256': digest(raw[table]), 'bytes': len(raw[table])}, {'path': str(before_path), 'sha256': digest(before_raw), 'bytes': len(before_raw)}])
    assert checks['loop_ledger']['rows'] == 1000
    result = {'startedAt': started, 'finishedAt': now(), 'candidateSha': actual_sha, 'configFreeze': {'path': str(freeze_dir / 'config-freeze.json'), 'sha256': digest(freeze_bytes)}, 'configFreezeManifest': {'path': str(manifest_path), 'sha256': digest(manifest_bytes), 'bytes': len(manifest_bytes)}, 'sql': sql, 'transaction': tx, 'checks': checks, 'rawRefs': refs, 'noBusinessOrConfigurationWrites': True, 'scope': 'Post-series persisted scope equality only; combine with frozen query source, exclusive Keeper operation record and actual task/event/output scope chain'}
    result['status'] = 'SCOPE_UNCHANGED' if all(x['equalFrozenColumns'] for x in checks.values()) else 'REWORK'
    with (out / 'scope-after.json').open('x') as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'checks': checks, 'out': str(out)}))
    return 0 if result['status'] == 'SCOPE_UNCHANGED' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--config-freeze', required=True)
    parser.add_argument('--config-freeze-manifest-sha256', required=True)
    parser.add_argument('--out', required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
