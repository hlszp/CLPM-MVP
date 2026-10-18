"""Independent private-capacity input audit. SELECT only, real DB and both time bounds.

No application imports, no login, no enqueue, no result/Redis writes. Full 7000-point
count/quality/range summary; optional raw samples do not pretend to hash all raw values.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys

import asyncpg
from dotenv import dotenv_values
import httpx

ROLES = {'PV', 'SP', 'OP', 'MODE', 'PID_P', 'PID_I', 'PID_D'}


def serial(value):
    if isinstance(value, (dict, asyncpg.Record)):
        return {key: serial(item) for key, item in dict(value).items()}
    if isinstance(value, (list, tuple)):
        return [serial(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def encode(value):
    return json.dumps(serial(value), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def private_config(cfg):
    for key in ('POSTGRES_DB', 'TDENGINE_DB'):
        value = cfg.get(key, '')
        if not value.startswith('clpm_capacity_') or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
            raise ValueError(f'{key} must name the designated clpm_capacity_ private database')


def window(manifest):
    start = datetime.fromisoformat(manifest['windowStart'].replace('Z', '+00:00'))
    if start.tzinfo:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(hours=1)
    if manifest.get('windowEnd') and datetime.fromisoformat(manifest['windowEnd']) != end:
        raise ValueError('manifest windowEnd mismatch')
    return start, end


def point_summary_sql(database, start, end):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', database):
        raise ValueError('invalid DB identifier')
    return (
        'SELECT point_id, quality_class, COUNT(*) AS row_count, '
        'FIRST(ts) AS first_ts, LAST(ts) AS last_ts, '
        'MIN(`value`) AS min_value, MAX(`value`) AS max_value '
        f"FROM {database}.st_point_data_v1 WHERE ts >= '{start.isoformat()}.000Z' "
        f"AND ts < '{end.isoformat()}.000Z' GROUP BY point_id, quality_class"
    )


def rows_of(payload):
    if payload.get('code', 0) not in (None, 0):
        raise RuntimeError(f"TD query error {payload.get('code')}: {payload.get('desc')}")
    columns = [col[0] for col in payload.get('column_meta', [])]
    return [dict(zip(columns, row, strict=True)) for row in payload.get('data', [])]


def evaluate(manifest, loops, mappings, summary):
    failures = []
    by_tag = {row['tag_name']: row for row in loops}
    expected_tags = {row['loopTag'] for row in manifest['rows']}
    if len(loops) != 1000 or len(by_tag) != 1000 or set(by_tag) != expected_tags:
        failures.append('Loop identity/1000 denominator mismatch')
    if any(not row['is_active'] or row['status'] != 'READY' or not row['include_in_evaluation'] for row in loops):
        failures.append('Ineligible object in frozen 1000')
    rolemap = defaultdict(dict)
    for row in mappings:
        rolemap[row['loop_id']][row['tag_role']] = row['tag_id']
    grouped = defaultdict(list)
    for row in summary:
        grouped[str(row['point_id'])].append(row)
    expected_points = {row['tag_id'] for row in mappings}
    if len(mappings) != 7000 or len(expected_points) != 7000:
        failures.append('7000 distinct point bindings required')
    if set(grouped) != expected_points:
        failures.append(f'TD point set mismatch missing={len(expected_points-set(grouped))} extra={len(set(grouped)-expected_points)}')
    details = []
    for mrow in manifest['rows']:
        loop = by_tag.get(mrow['loopTag'])
        if loop is None:
            continue
        actual_roles = rolemap[loop['id']]
        if set(actual_roles) != ROLES:
            failures.append(f"{mrow['loopTag']}: role set mismatch")
        for role, point in actual_roles.items():
            data = grouped[point]
            count = sum(int(row['row_count']) for row in data)
            expected = mrow.get('pointsPerTag', {}).get(role)
            if role == 'SP':
                expected = 1 if mrow['composition'] == 'gap' else 3
            elif role in ('PID_P', 'PID_I', 'PID_D'):
                expected = 1
            if isinstance(expected, int) and count != expected:
                failures.append(f"{mrow['loopTag']} {role}: bounded count {count} != {expected}")
            if any(int(row['quality_class']) != 1 for row in data) and not (role == 'PV' and mrow['composition'] == 'insufficient'):
                failures.append(f"{mrow['loopTag']} {role}: unexpected non-GOOD quality")
            if role == 'MODE' and data:
                wanted = 0 if mrow['composition'] == 'manual' else 1
                if any(float(row['min_value']) != wanted or float(row['max_value']) != wanted for row in data):
                    failures.append(f"{mrow['loopTag']}: MODE != {wanted}")
            details.append({'loopTag': mrow['loopTag'], 'loopId': loop['id'], 'composition': mrow['composition'], 'role': role, 'pointId': point, 'expectedCount': expected, 'actualCount': count, 'qualityRows': data})
    return failures, details


async def run(args):
    out = Path(args.out); out.mkdir(parents=True, exist_ok=False)
    cfg = dotenv_values(args.env_file); private_config(cfg)
    manifest_path = Path(args.manifest); manifest_bytes = manifest_path.read_bytes(); manifest = json.loads(manifest_bytes)
    start, end = window(manifest)
    if end > datetime.now(timezone.utc).replace(tzinfo=None):
        raise ValueError('Only past complete windows are accepted')
    report = {'startedAt': datetime.now(timezone.utc).isoformat(), 'argv': sys.argv, 'postgresDatabase': cfg['POSTGRES_DB'], 'tdDatabase': cfg['TDENGINE_DB'], 'manifestSha256': hashlib.sha256(manifest_bytes).hexdigest(), 'windowStart': start.isoformat(), 'windowEnd': end.isoformat(), 'boundary': 'Full per-point summary; raw samples do not establish a whole raw-value archive/hash.'}
    (out/'input-manifest.json').write_bytes(manifest_bytes)
    try:
        db = await asyncpg.connect(host=cfg.get('POSTGRES_HOST','localhost'),port=int(cfg.get('POSTGRES_PORT',17102)),user=cfg.get('POSTGRES_USER','clpm'),password=cfg['POSTGRES_PASSWORD'],database=cfg['POSTGRES_DB'],server_settings={'application_name':'F02-input-readonly','statement_timeout':'30000'})
        try:
            async with db.transaction(isolation='repeatable_read',readonly=True):
                report['pgTransaction'] = serial(await db.fetchrow("SELECT current_database() AS database,current_setting('transaction_read_only') AS readonly,txid_current_snapshot()::text AS snapshot"))
                loops = serial(await db.fetch("SELECT id::text,tag_name,unit_id::text,status,is_active,include_in_evaluation FROM loop_ledger WHERE tag_name LIKE 'PERF-T%' ORDER BY tag_name"))
                mappings = serial(await db.fetch("SELECT m.loop_id::text,m.tag_role,m.tag_id::text FROM loop_tag_mapping m JOIN loop_ledger l ON l.id=m.loop_id WHERE l.tag_name LIKE 'PERF-T%' ORDER BY l.tag_name,m.tag_role"))
                report['globalEligible'] = await db.fetchval("SELECT count(*) FROM loop_ledger WHERE is_active AND status='READY' AND include_in_evaluation")
                report['schema'] = serial(await db.fetch('SELECT version_num FROM alembic_version'))
                report['configRevision'] = await db.fetchval('SELECT max(revision) FROM config_publication')
        finally:
            await db.close()
        (out/'pg-input-identity.json').write_bytes(encode({'loops':loops,'mappings':mappings}))
        endpoint = f"http://{cfg.get('TDENGINE_HOST','localhost')}:{int(cfg.get('TDENGINE_PORT',17104))+11}/rest/sql"
        report['tdEndpoint'] = endpoint
        with httpx.Client(auth=(cfg.get('TDENGINE_USER','root'),cfg.get('TDENGINE_PASSWORD','')),timeout=args.timeout,trust_env=False) as client:
            sql = point_summary_sql(cfg['TDENGINE_DB'],start,end)
            response = client.post(endpoint,content=sql.encode()); payload = response.json()
            (out/'td-bounded-summary.raw.json').write_bytes(response.content)
            report['summaryQuery'] = {'sql':sql,'httpStatus':response.status_code,'sha256':hashlib.sha256(response.content).hexdigest()}
            summary = rows_of(payload)
            failures, details = evaluate(manifest,loops,mappings,summary)
            if report['globalEligible'] != 1000:
                failures.append('Global eligible count != 1000')
            report['failures'] = failures
            report['pointCount'] = len({row['point_id'] for row in summary})
            (out/'point-details.json.gz').write_bytes(gzip.compress(encode(details),mtime=0))
            if args.raw_samples_per_composition:
                samples = []; selected = Counter(); by_tag = {row['tag_name']:row for row in loops}; rolemap=defaultdict(dict)
                for row in mappings: rolemap[row['loop_id']][row['tag_role']]=row['tag_id']
                for mrow in manifest['rows']:
                    composition=mrow['composition']
                    if selected[composition] >= args.raw_samples_per_composition:continue
                    selected[composition]+=1
                    for role,point in rolemap[by_tag[mrow['loopTag']]['id']].items():
                        table='p_'+point.replace('-','')
                        sql=f"SELECT * FROM {cfg['TDENGINE_DB']}.{table} WHERE ts >= '{start.isoformat()}.000Z' AND ts < '{end.isoformat()}.000Z' ORDER BY ts"
                        raw=client.post(endpoint,content=sql.encode()); body=raw.json(); rows_of(body)
                        name=f'raw-{mrow["loopTag"]}-{role}.json.gz'; (out/name).write_bytes(gzip.compress(raw.content,mtime=0));samples.append({'loopTag':mrow['loopTag'],'composition':composition,'role':role,'pointId':point,'sql':sql,'file':name,'rawSha256':hashlib.sha256(raw.content).hexdigest()})
                report['rawSamples']=samples
        report['status']='PASS' if not failures else 'REWORK'
    except BaseException as exc:
        report['status']='BLOCKED';report['error']={'type':type(exc).__name__,'message':str(exc)}
    report['finishedAt']=datetime.now(timezone.utc).isoformat();(out/'input-audit.json').write_bytes(encode(report))
    files=[{'file':str(path.relative_to(out)),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(out.iterdir()) if path.is_file()]
    (out/'sha256-manifest.json').write_bytes(encode({'files':files}))
    print(json.dumps({'status':report['status'],'pointCount':report.get('pointCount'),'failureCount':len(report.get('failures',[])),'out':str(out)}))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--env-file',required=True);parser.add_argument('--manifest',required=True);parser.add_argument('--out',required=True);parser.add_argument('--timeout',type=float,default=120);parser.add_argument('--raw-samples-per-composition',type=int,default=1)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
