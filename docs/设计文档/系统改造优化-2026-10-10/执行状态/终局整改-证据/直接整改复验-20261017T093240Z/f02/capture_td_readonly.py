"""Current source events for exact affected windows; SELECT only, sequential.

This cannot turn current source coverage into an original immutable input snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from dotenv import dotenv_values
import httpx

HERE = Path(__file__).resolve().parent


def main():
    cfg = dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
    database = cfg.get('TDENGINE_DB', 'clpm_ts')
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', database):
        raise ValueError('unexpected database identifier')
    source = json.loads((HERE / 'readonly-db-recovery.json').read_text())
    output = {
        'observedAt': datetime.now(timezone.utc).isoformat(),
        'mode': 'read-only SELECT, serial request concurrency=1',
        'boundary': 'Current raw source, not an original frozen input. Does not establish as-of-run coverage or exclude all concurrent infrastructure faults.',
        'batches': [],
    }
    endpoint = f"http://{cfg.get('TDENGINE_HOST', 'localhost')}:{int(cfg.get('TDENGINE_PORT', '7104')) + 11}/rest/sql"
    with httpx.Client(auth=(cfg.get('TDENGINE_USER', 'root'), cfg.get('TDENGINE_PASSWORD', '')), timeout=15.0, trust_env=False) as client:
        def query(sql):
            if not sql.lstrip().upper().startswith(('SELECT ', 'SHOW ')):
                raise ValueError('only SELECT/SHOW permitted')
            response = client.post(endpoint, content=sql.encode())
            data = response.json()
            result = {'sql': sql, 'httpStatus': response.status_code, 'response': data}
            if data.get('code') not in (None, 0):
                result['error'] = True
            return result

        output['serverVersion'] = query('SELECT SERVER_VERSION()')
        for batch in source['batches']:
            recovered = {'batch': batch['batch'], 'window': batch['window'], 'parentTaskId': batch['parentTaskId'], 'points': []}
            start = batch['window']['tsStart'] + '.000Z'
            end = batch['window']['tsEnd'] + '.000Z'
            for mapping in batch['historicalBindingsForWindow']:
                if mapping['tag_role'] not in ('PV', 'OP', 'SP', 'MODE'):
                    continue
                point_id = str(UUID(mapping['tag_id']))
                table = f"{database}.p_{UUID(point_id).hex}"
                where = f"WHERE ts >= '{start}' AND ts < '{end}'"
                summary = query(f'SELECT COUNT(*) AS row_count, FIRST(ts) AS first_ts, LAST(ts) AS last_ts FROM {table} {where}')
                quality = query(f'SELECT quality_class, COUNT(*) AS row_count FROM {table} {where} GROUP BY quality_class')
                recovered['points'].append({
                    'loopId': mapping['loop_id'], 'tagName': mapping['tag_name'],
                    'role': mapping['tag_role'], 'pointId': point_id,
                    'bindingValidFrom': mapping['valid_from'], 'bindingValidTo': mapping['valid_to'],
                    'summary': summary, 'quality': quality,
                })
            output['batches'].append(recovered)
    path = HERE / 'readonly-td-source-recovery.json'
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'batches': [{'batch': b['batch'], 'points': len(b['points']), 'queryErrors': sum(bool(p['summary'].get('error') or p['quality'].get('error')) for p in b['points'])} for b in output['batches']], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    if any(p['summary'].get('error') or p['quality'].get('error') for b in output['batches'] for p in b['points']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
