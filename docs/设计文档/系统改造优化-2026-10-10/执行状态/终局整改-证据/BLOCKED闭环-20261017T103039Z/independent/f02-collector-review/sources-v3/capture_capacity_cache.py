"""Read actual same-window L1/L2 keys and save restorable values in private Redis."""
import argparse
import asyncio
import base64
from collections import Counter
from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import time

import asyncpg
from dotenv import dotenv_values
import redis
import zstandard

from audit_capacity_inputs import encode,private_config,serial


async def main(args):
    cfg=dotenv_values(args.env_file);private_config(cfg);out=Path(args.out);out.mkdir(parents=True,exist_ok=False);(out/'values').mkdir()
    start=datetime.fromisoformat(args.ts_start.replace('Z','+00:00'))
    if start.tzinfo:start=start.astimezone(timezone.utc).replace(tzinfo=None)
    end=start+timedelta(hours=1);window_hash=hashlib.md5(f'{start.isoformat()}|{end.isoformat()}'.encode()).hexdigest()[:8]
    db=await asyncpg.connect(host=cfg.get('POSTGRES_HOST','localhost'),port=int(cfg.get('POSTGRES_PORT',17102)),user=cfg.get('POSTGRES_USER','clpm'),password=cfg['POSTGRES_PASSWORD'],database=cfg['POSTGRES_DB'])
    try:
        async with db.transaction(readonly=True):loops=serial(await db.fetch("SELECT id::text,tag_name FROM loop_ledger WHERE tag_name LIKE 'PERF-T%' ORDER BY tag_name"))
    finally:await db.close()
    if len(loops)!=1000:raise ValueError('1000 actual objects required')
    ids={row['id'] for row in loops};client=redis.Redis(host=cfg.get('REDIS_HOST','localhost'),port=int(cfg.get('REDIS_PORT',17103)),password=cfg.get('REDIS_PASSWORD') or None,db=0,decode_responses=True,socket_timeout=5)
    rows=[]
    try:
        for prefix in ('pdb','pdb_l2'):
            for key in client.scan_iter(match=prefix+':*',count=200):
                parts=key.split(':')
                if len(parts)<5 or parts[1] not in ids:continue
                if prefix=='pdb_l2' and parts[3]!=window_hash:continue
                if prefix=='pdb' and parts[3:5]!=[str(int(start.timestamp())),str(int(end.timestamp()))]:continue
                raw=client.get(key);ttl=client.pttl(key);item={'key':key,'loopId':parts[1],'layer':prefix,'observedAt':datetime.now(timezone.utc).isoformat(),'ttlMs':ttl,'exists':raw is not None}
                if raw is not None:
                    data=raw.encode();digest=hashlib.sha256(data).hexdigest();filename='values/'+digest+'.b64.gz';(out/filename).write_bytes(gzip.compress(data,mtime=0));item.update(valueFile=filename,valueSha256=digest)
                    try:
                        decoded=json.loads(zstandard.ZstdDecompressor().decompress(base64.b64decode(raw)))
                        item['decodedType']=type(decoded).__name__
                        if prefix=='pdb_l2':
                            bundles=decoded if isinstance(decoded,list) else decoded.get('bundles',[])
                            item['bundleCount']=len(bundles);item['metricCodes']=[bundle.get('metric_code') for bundle in bundles]
                            item['bundleBlocks']=[{'loopId':bundle.get('data_block',{}).get('loop_id'),'configVersion':bundle.get('data_block',{}).get('config_version'),'pointCount':bundle.get('data_block',{}).get('point_count'),'firstTimestamp':(bundle.get('data_block',{}).get('timestamps') or [None])[0],'lastTimestamp':(bundle.get('data_block',{}).get('timestamps') or [None])[-1]} for bundle in bundles]
                    except Exception as exc:item['decodeError']=type(exc).__name__+': '+str(exc)
                rows.append(item)
    finally:client.close()
    summary={'capturedAt':datetime.now(timezone.utc).isoformat(),'postgresDatabase':cfg['POSTGRES_DB'],'redisPort':cfg.get('REDIS_PORT'),'windowStart':start.isoformat(),'windowEnd':end.isoformat(),'l2WindowHash':window_hash,'hostTZ':os.environ.get('TZ'),'hostTzname':time.tzname,'l1RawEpochs':[int(start.timestamp()),int(end.timestamp())],'loops':loops,'keys':rows,'layerKeyCounts':dict(Counter(row['layer'] for row in rows if row['exists'])),'l2PresentLoopCount':len({row['loopId'] for row in rows if row['layer']=='pdb_l2' and row['exists']}),'missingL2LoopIds':sorted(ids-{row['loopId'] for row in rows if row['layer']=='pdb_l2' and row['exists']}),'boundary':'Key presence is cache preparation evidence, not proof of actual compute HIT. Require same-window worker L2-hit logs and TTL validity; no cache values were written.'}
    (out/'cache-state.json').write_bytes(encode(summary));manifest=[{'file':str(path.relative_to(out)),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(out.rglob('*')) if path.is_file()];(out/'sha256-manifest.json').write_bytes(encode({'files':manifest}));print(json.dumps({key:summary[key] for key in ('layerKeyCounts','l2PresentLoopCount','l2WindowHash')}));return 0


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--env-file',required=True);p.add_argument('--ts-start',required=True);p.add_argument('--out',required=True)
    raise SystemExit(asyncio.run(main(p.parse_args())))
