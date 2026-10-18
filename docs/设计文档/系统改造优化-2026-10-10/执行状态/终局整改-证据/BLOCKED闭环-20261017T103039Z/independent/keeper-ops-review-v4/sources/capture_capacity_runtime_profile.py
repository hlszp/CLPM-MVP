"""Keeper read-only per-batch process/resource/config identity, with no secrets."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import asyncio
from dotenv import dotenv_values
import httpx
import asyncpg

E = Path(__file__).resolve().parent
R = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')


async def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=False)
    window = datetime.fromisoformat(sys.argv[2]); end = window + timedelta(hours=1)
    cfg = dotenv_values(R / 'backend/.env')
    assert cfg['POSTGRES_DB'] == cfg['TDENGINE_DB'] == 'clpm_capacity_20261017_s01' and cfg['REDIS_PORT'] == '17123'
    state = json.loads((E / 'runtime/current-capacity-api.json').read_text())
    start_path = Path(state['start_record']); start = json.loads(start_path.read_text())
    assert state['sha'] == subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=R, text=True).strip()
    assert not subprocess.check_output(['git', 'diff', '--name-only', 'HEAD', '--', 'backend'], cwd=R).strip()
    ps = subprocess.check_output(['ps', '-ax', '-o', 'pid=,ppid=,rss=,lstart=,stat=,command='], text=True)
    rows = []
    for line in ps.splitlines():
        parts = line.strip().split(maxsplit=9)
        if len(parts) != 10: continue
        pid, ppid = int(parts[0]), int(parts[1])
        if pid == state['api_pid'] or ppid == state['api_pid'] or str(R / 'backend/.venv/bin/python') in parts[9]:
            if '-m uvicorn app.main:app' in parts[9]: role = 'api'
            elif '-m celery' in parts[9] and ' beat ' in parts[9]: role = 'beat'
            elif '-m celery' in parts[9] and ' worker ' in parts[9]: role = 'worker_master' if ppid == state['api_pid'] else 'worker_child'
            else: continue
            rows.append({'pid': pid, 'ppid': ppid, 'identity': f'{pid}:{" ".join(parts[3:8])}', 'rssBytes': int(parts[2]) * 1024, 'startedAtLocal': ' '.join(parts[3:8]), 'state': parts[8], 'argv': parts[9], 'role': role, 'loadedSha': state['sha']})
    assert len([x for x in rows if x['role'] == 'api']) == len([x for x in rows if x['role'] == 'beat']) == len([x for x in rows if x['role'] == 'worker_master']) == 1
    assert len([x for x in rows if x['role'] == 'worker_child']) == 4
    containers = {}
    for name in ('clpm-mvp-postgres', 'clpm-mvp-tdengine', 'clpm-f02-redis-20261017-s01'):
        x = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
        containers[name] = {'id': x['Id'], 'image': x['Image'], 'state': x['State'], 'memoryLimitBytes': x['HostConfig']['Memory'], 'memorySwapBytes': x['HostConfig']['MemorySwap'], 'nanoCpus': x['HostConfig']['NanoCpus'], 'ports': x['NetworkSettings']['Ports'], 'mounts': x['Mounts']}
    td_responses = []
    with httpx.Client(auth=(cfg.get('TDENGINE_USER', 'root'), cfg['TDENGINE_PASSWORD']), timeout=10, trust_env=False) as client:
        for variable in ('queryUseMemoryPool', 'minReservedMemorySize', 'queryBufferSize', 'forceReadConfig', 'version', 'gitinfo', 'numOfCores', 'totalMemoryKB'):
            sql = f"SHOW DNODE 1 VARIABLES LIKE '{variable}'"
            response = client.post('http://127.0.0.1:17115/rest/sql', content=sql.encode())
            result = {'observedAt': datetime.now(timezone.utc).isoformat(), 'sql': sql, 'httpStatus': response.status_code, 'response': response.json()}
            assert response.status_code == 200 and result['response']['code'] == 0 and result['response']['rows'] == 1
            td_responses.append(result)
    values = {x['response']['data'][0][1]: x['response']['data'][0][2] for x in td_responses}
    assert values['queryUseMemoryPool'] == '0' and values['minReservedMemorySize'] == '1024' and values['queryBufferSize'] == '4096' and values['forceReadConfig'] == '1'
    db = await asyncpg.connect(host=cfg['POSTGRES_HOST'], port=int(cfg['POSTGRES_PORT']), user=cfg['POSTGRES_USER'], password=cfg['POSTGRES_PASSWORD'], database=cfg['POSTGRES_DB'])
    try:
        async with db.transaction(isolation='repeatable_read', readonly=True):
            identity = dict(await db.fetchrow("SELECT current_database() AS database, current_setting('transaction_read_only') AS readonly, txid_current_snapshot()::text AS snapshot"))
            counts = {}
            for table, predicate in [('kpi_snapshot_hourly', 'ts_start'), ('loop_confidence_latest', 'data_ts_start'), ('kpi_node_snapshot_hourly', 'ts_start'), ('calculation_result_record', 'ts_start')]:
                counts[table] = await db.fetchval(f'SELECT count(*) FROM {table} WHERE {predicate}=$1', window)
            schema = await db.fetchval('SELECT version_num FROM alembic_version')
    finally: await db.close()
    assert all(n == 0 for n in counts.values()), counts
    data = {'observedAt': datetime.now(timezone.utc).isoformat(), 'window': {'start': window.isoformat(), 'end': end.isoformat()}, 'postgres': identity, 'schemaRevision': schema, 'beforeFormalResultCounts': counts, 'runtime': {'state': state, 'startRecord': str(start_path), 'startRecordSha256': hashlib.sha256(start_path.read_bytes()).hexdigest(), 'members': rows, 'provenance': 'All members started by recorded immutable API lifespan on unchanged backend tree; checkout changes prohibited until stopped.'}, 'containers': containers, 'tdResponses': td_responses, 'tdVariables': values, 'envFileSha256': hashlib.sha256((R / 'backend/.env').read_bytes()).hexdigest(), 'taosConfigSha256': hashlib.sha256(Path('/Users/zhangping/DEV/CLPM-MVP/deploy/docker/tdengine/taos.cfg').read_bytes()).hexdigest()}
    p = out / 'runtime-profile.json'; p.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n')
    print(json.dumps({'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'ownedProcesses': len(rows), 'preResultCounts': counts}))


if __name__ == '__main__': asyncio.run(main())
