"""Read-only PG/Redis facts from the old shared test runtime; no app import."""
import asyncio
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
from pathlib import Path

import asyncpg
from dotenv import dotenv_values
import redis

HERE = Path(__file__).resolve().parent
CFG = dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
REPORT = {'observedAt': datetime.now(timezone.utc).isoformat(), 'postgres': {}, 'redis': {}}


def serial(value):
    if isinstance(value, (dict, asyncpg.Record)):
        return {key: serial(item) for key,item in dict(value).items()}
    if isinstance(value, (list,tuple)):
        return [serial(item) for item in value]
    if value is None or isinstance(value,(int,float,str,bool)):
        return value
    return str(value)


async def pg():
    database=CFG.get('POSTGRES_DB','clpm')
    db=await asyncpg.connect(host=CFG.get('POSTGRES_HOST','localhost'),port=int(CFG.get('POSTGRES_PORT',17102)),user=CFG.get('POSTGRES_USER','clpm'),password=CFG['POSTGRES_PASSWORD'],database=database,server_settings={'application_name':'F02-BLOCKED-readonly','statement_timeout':'15000'})
    try:
        async with db.transaction(isolation='repeatable_read',readonly=True):
            queries = {
                'identity': 'SELECT current_database() AS database,current_setting(\'transaction_read_only\') AS readonly,version() AS version',
                'schema': 'SELECT version_num FROM alembic_version',
                'publication': 'SELECT max(revision) AS max_revision,count(*) AS count FROM config_publication',
                'testLoops': "SELECT id::text,tag_name,unit_id::text,status,is_active,include_in_evaluation FROM loop_ledger WHERE tag_name LIKE 'PERF-T%' ORDER BY tag_name",
                'eligibility': "SELECT is_active,status,include_in_evaluation,count(*) AS count FROM loop_ledger WHERE tag_name LIKE 'PERF-T%' GROUP BY is_active,status,include_in_evaluation",
                'latestRange': "SELECT count(*) AS count,min(data_ts_end) AS min_end,max(data_ts_end) AS max_end FROM loop_confidence_latest x JOIN loop_ledger l ON l.id=x.loop_id WHERE l.tag_name LIKE 'PERF-T%'",
                'ledgerWindows': "SELECT ts_start,count(*) AS count FROM kpi_snapshot_hourly x JOIN loop_ledger l ON l.id=x.loop_id WHERE l.tag_name LIKE 'PERF-T%' GROUP BY ts_start ORDER BY ts_start DESC LIMIT 25",
                'latestByWindowVersion': "SELECT data_ts_start,data_ts_end,algorithm_version,count(*) AS count FROM loop_confidence_latest x JOIN loop_ledger l ON l.id=x.loop_id WHERE l.tag_name LIKE 'PERF-T%' GROUP BY data_ts_start,data_ts_end,algorithm_version ORDER BY data_ts_start DESC",
                'currentMappings': "SELECT m.loop_id::text,m.tag_role,m.tag_id::text FROM loop_tag_mapping m JOIN loop_ledger l ON l.id=m.loop_id WHERE l.tag_name LIKE 'PERF-T%' ORDER BY l.tag_name,m.tag_role",
                'algorithmParameters': 'SELECT metric_code,control_type,params,is_enabled,version FROM algorithm_parameter ORDER BY metric_code,control_type',
                'safeRuntimeConfig': "SELECT key,value,updated_at FROM sys_config WHERE key LIKE 'capacity.%' OR key IN ('gapBackfillEnabled','signalrEnabled','config.revision') ORDER BY key",
                'pgConnections': "SELECT datname,application_name,state,wait_event_type,count(*) AS count FROM pg_stat_activity WHERE datname=current_database() GROUP BY datname,application_name,state,wait_event_type ORDER BY count(*) DESC",
            }
            for name,sql in queries.items():
                assert sql.startswith('SELECT ')
                rows=await db.fetch(sql)
                REPORT['postgres'][name]={'sql':sql,'rows':serial(rows)}
    finally:
        await db.close()


def redis_facts():
    common=dict(host=CFG.get('REDIS_HOST','localhost'),port=int(CFG.get('REDIS_PORT',17103)),password=CFG.get('REDIS_PASSWORD') or None,socket_connect_timeout=3,socket_timeout=3,decode_responses=True)
    for number in (0,1,2):
        client=redis.Redis(**common,db=number)
        info=client.info(section='memory')
        result={'used_memory':info.get('used_memory'),'used_memory_peak':info.get('used_memory_peak'),'maxmemory':info.get('maxmemory'),'maxmemory_policy':info.get('maxmemory_policy'),'brokerKeys':[]}
        for pattern in ('default*','unacked*','celery*'):
            for key in islice(client.scan_iter(match=pattern,count=50),20):
                kind=client.type(key)
                length=client.llen(key) if kind=='list' else client.hlen(key) if kind=='hash' else client.zcard(key) if kind=='zset' else client.scard(key) if kind=='set' else None
                result['brokerKeys'].append({'key':key,'type':kind,'length':length})
        if number==0:
            recent=client.zrevrange('task:index',0,24)
            result['recentTaskRecordHeaders']=[{'id':task_id,**{key:value for key,value in client.hgetall('task:'+task_id).items() if key in ('status','task_type','triggered_by','created_at','completed_at','celery_task_id','name','loops_total','loops_done')}} for task_id in recent]
        REPORT['redis'][str(number)]=result
        client.close()


asyncio.run(pg())
redis_facts()
encoded=json.dumps(REPORT,ensure_ascii=False,indent=2)+'\n'
(HERE/'runtime-database-results.json').write_text(encoded)
print(json.dumps({'database':REPORT['postgres']['identity']['rows'],'eligibility':REPORT['postgres']['eligibility']['rows'],'latestRange':REPORT['postgres']['latestRange']['rows'],'schema':REPORT['postgres']['schema']['rows'],'publication':REPORT['postgres']['publication']['rows'],'mappingCount':len(REPORT['postgres']['currentMappings']['rows']),'sha256':hashlib.sha256(encoded.encode()).hexdigest()},ensure_ascii=False))
