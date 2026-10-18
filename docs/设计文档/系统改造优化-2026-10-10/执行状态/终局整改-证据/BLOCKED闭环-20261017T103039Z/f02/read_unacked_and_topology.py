"""Read broker unacked headers and backend status; never ACK, revoke or inspect RPC."""
from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import re
import subprocess

from dotenv import dotenv_values
import redis

HERE=Path(__file__).resolve().parent
cfg=dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
common=dict(host=cfg.get('REDIS_HOST','localhost'),port=int(cfg.get('REDIS_PORT',17103)),password=cfg.get('REDIS_PASSWORD') or None,decode_responses=True,socket_timeout=5)
out={'capturedAt':datetime.now(timezone.utc).isoformat(),'boundary':'READ ONLY; no ACK/revoke/purge/control.inspect RPC','unacked':[]}
broker=redis.Redis(**common,db=1);backend=redis.Redis(**common,db=2);app=redis.Redis(**common,db=0)
for delivery_tag,raw in broker.hgetall('unacked').items():
    message,exchange,routing=json.loads(raw)
    headers=message.get('headers',{})
    task_id=headers.get('id')
    result=backend.get('celery-task-meta-'+str(task_id))
    result=json.loads(result) if result else None
    payload=json.loads(base64.b64decode(message['body']))
    kwargs=payload[1] if isinstance(payload,list) and len(payload)>1 else {}
    out['unacked'].append({'deliveryTag':delivery_tag,'unackedScore':broker.zscore('unacked_index',delivery_tag),'taskId':task_id,'taskName':headers.get('task'),'rootId':headers.get('root_id'),'parentId':headers.get('parent_id'),'origin':headers.get('origin'),'eta':headers.get('eta'),'retries':headers.get('retries'),'exchange':exchange,'routingKey':routing,'argumentShape':{'positionalCount':len(payload[0]) if isinstance(payload,list) else None,'kwargsKeys':list(kwargs),'window':{key:value for key,value in kwargs.items() if key in ('ts_start','ts_end','record_task','task_record_id','parent_task_id')}},'backendState':{key:value for key,value in result.items() if key in ('status','date_done','task_id')} if result else None})
out['unackedCount']=len(out['unacked'])
recent=app.zrevrange('task:index',0,49)
out['recentTaskRecords']=[{'recordId':task_id,**{key:value for key,value in app.hgetall('task:'+task_id).items() if key in ('status','task_type','created_at','started_at','completed_at','loops_total','loops_done','celery_task_id','celery_task_ids','name','triggered_by')}} for task_id in recent]
result=subprocess.run(['ps','-ax','-o','pid=,ppid=,etime=,command='],capture_output=True,text=True)
out['processes']=[re.sub(r'(--password\s+)\S+',r'\1REDACTED',line) for line in result.stdout.splitlines() if any(needle in line for needle in ('clpm-g5-runtime-20261015','celery_app worker','celery_app beat')) and 'read_unacked_and_topology.py' not in line and 'capture_readonly.py' not in line]
for client in (broker,backend,app):client.close()
(HERE/'unacked-topology-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'unacked':out['unacked'],'processes':out['processes']},ensure_ascii=False))
