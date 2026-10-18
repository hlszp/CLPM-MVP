"""Root read-only historical-binding preflight, dedicated synthetic capacity DB."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib,json,os,subprocess
from dotenv import dotenv_values
R=Path(__file__).resolve().parent
O=R/'historical-binding-readonly-preflight-root'
O.mkdir(exist_ok=False)
N=Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
assert hashlib.sha256(N.read_bytes()).hexdigest()=='27d6fc10cc68376b056d1c7803f1b6361e48c11bc3de46c4e8a46cc4b2abe04b'
E=dotenv_values(N);env=os.environ.copy();env['PGPASSWORD']=E['POSTGRES_PASSWORD']
sql="""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SELECT json_build_object('section','before','database',current_database(),'readOnly',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),'snapshot',txid_current_snapshot()::text);
SELECT jsonb_build_object('section','synthetic-roster','rows',(SELECT jsonb_agg(to_jsonb(l) ORDER BY l.id) FROM loop_ledger l WHERE l.tag_name LIKE 'PERF-T%'));
SELECT jsonb_build_object('section','bindings','rows',(SELECT jsonb_agg(to_jsonb(h) ORDER BY h.id) FROM loop_tag_binding_history h JOIN loop_ledger l ON l.id=h.loop_id WHERE l.tag_name LIKE 'PERF-T%'));
SELECT json_build_object('section','after','database',current_database(),'readOnly',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),'snapshot',txid_current_snapshot()::text);
COMMIT;
"""
(O/'executed.sql').write_text(sql)
a=['docker','exec','-i','-e','PGPASSWORD','clpm-mvp-postgres','psql','-h','127.0.0.1','-p','5432','-U',E['POSTGRES_USER'],'-d','clpm_capacity_20261017_s01','-X','-A','-t','-v','ON_ERROR_STOP=1','-f','-']
began=datetime.now(timezone.utc).isoformat();p=subprocess.run(a,input=sql.encode(),capture_output=True,env=env)
(O/'stdout.log').write_bytes(p.stdout);(O/'stderr.log').write_bytes(p.stderr)
record={'argv':a,'startedAt':began,'finishedAt':datetime.now(timezone.utc).isoformat(),'rc':p.returncode,'stdoutSha256':hashlib.sha256(p.stdout).hexdigest(),'stderrSha256':hashlib.sha256(p.stderr).hexdigest(),'actualDatabase':'clpm_capacity_20261017_s01','readOnly':True,'secretNotInArgumentsOrFiles':True};(O/'command.json').write_text(json.dumps(record,indent=2)+'\n');assert p.returncode==0
rows=[json.loads(x) for x in p.stdout.splitlines() if x.startswith(b'{')];b=next(x for x in rows if x['section']=='before');z=next(x for x in rows if x['section']=='after');assert b['snapshot']==z['snapshot'] and b['readOnly']==z['readOnly']=='on' and b['isolation']==z['isolation']=='repeatable read'
roster=next(x['rows'] for x in rows if x['section']=='synthetic-roster');hist=next(x['rows'] for x in rows if x['section']=='bindings');assert len(roster)==1000 and len(hist)==7000
plans=json.loads((R/'series-s01/planned-batches.json').read_text())['batches'];start=min(datetime.fromisoformat(x['ts_start']).replace(tzinfo=timezone.utc) for x in plans);end=max(datetime.fromisoformat(x['ts_end']).replace(tzinfo=timezone.utc) for x in plans);late=[x for x in hist if datetime.fromisoformat(x['valid_from'])>start]
summary={'at':datetime.now(timezone.utc).isoformat(),'rosterCount':len(roster),'bindingCount':len(hist),'earliestNormalStartUtc':start.isoformat(),'latestNormalEndUtc':end.isoformat(),'bindingsStartingAfterFirstNormalWindow':len(late),'bindingMinValidFromUtc':min(datetime.fromisoformat(x['valid_from']) for x in hist).astimezone(timezone.utc).isoformat(),'bindingMaxValidFromUtc':max(datetime.fromisoformat(x['valid_from']) for x in hist).astimezone(timezone.utc).isoformat(),'basisValues':sorted(set(x['basis'] for x in hist)),'writesPerformed':0,'capacityFormalTasksExecuted':0};(O/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(O/'executed-source.py').write_bytes(Path(__file__).read_bytes());print(json.dumps(summary))
