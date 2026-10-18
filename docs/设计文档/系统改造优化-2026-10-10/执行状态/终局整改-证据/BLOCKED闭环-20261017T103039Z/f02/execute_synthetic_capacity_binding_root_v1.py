from pathlib import Path
import datetime,hashlib,json,subprocess,sys,time
E=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
cfg=E/'f02/synthetic-capacity-binding-root-apply-command-v1.json'
folder=E/'f02/synthetic-capacity-binding-root-invocation-v1'
folder.mkdir(exist_ok=False)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
record={'startedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'commandFile':str(cfg),'commandFileSha256':sha(cfg),'executed':False}
checks=[
(E/'f02/synthetic-capacity-binding-preparation/redis-url-guard-v2/prepare_bindings.py','e213f27d9abc631ad3cb11233366e6f631a9fb4d88bcbf3b81cdf0d60464650b'),
(E/'f02/synthetic-capacity-dump-adapter-root/pg_dump_test_container.py','c27d789a2c8308cfbeebc692d4565d2460b6c65fe2f3bc295fc72f51469fb3c8'),
(E/'independent/f02-synthetic-binding-preparation-review/receipt.md','14428de424cce1f1201926e37da3a931128c54a05d7ce624fe456ef0c3526d89'),
(E/'independent/f02-pgdump-adapter-review/evidence-manifest.json','9671b7d1fcef6c9ff81008c0f5da5d6ccfada3ff75ec2215992e8c7b615aa394'),
(Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env"),'27d6fc10cc68376b056d1c7803f1b6361e48c11bc3de46c4e8a46cc4b2abe04b')]
try:
 for p,h in checks:assert sha(p)==h,str(p)
 record['verifiedSourcePins']=[{'path':str(p),'sha256':h} for p,h in checks]
 command=['lsof','-nP','-iTCP:17101','-iTCP:17261','-iTCP:17266','-sTCP:LISTEN']
 t=datetime.datetime.now(datetime.timezone.utc).isoformat()
 p=subprocess.run(command,capture_output=True)
 for stream in ('stdout','stderr'):(folder/('no-live-api.'+stream+'.log')).write_bytes(getattr(p,stream))
 record['actualServicePreflight']={'startedAtUtc':t,'argv':command,'rc':p.returncode,'stdoutSha256':hashlib.sha256(p.stdout).hexdigest(),'stderrSha256':hashlib.sha256(p.stderr).hexdigest()}
 assert p.returncode==1 and not p.stdout and not p.stderr,'owned test API or original API remains active'
 record['commandFilePreflightTimestampIsInformalOnly']='The exact new observation above is authoritative; command-file preflight timestamp was a handoff label.'
 argv=json.loads(cfg.read_text())['argv']
 with (folder/'stdout.log').open('xb') as out,(folder/'stderr.log').open('xb') as err:
  record['executed']=True
  (folder/'command.json').write_text(json.dumps(record,indent=2)+'\n')
  start=time.monotonic();p=subprocess.run(argv,stdout=out,stderr=err)
 record.update(rc=p.returncode,durationSeconds=time.monotonic()-start)
 for stream in ('stdout','stderr'):record[stream+'Sha256']=sha(folder/(stream+'.log'))
 state=E/'f02/synthetic-capacity-binding-actual-root-v1/state.json'
 if state.exists():
  s=json.loads(state.read_text());record['stateSha256']=sha(state)
  record['stateSummary']={k:s.get(k) for k in ('committed','persistedReadbackVerified','commitAttempted','rows','database','error')}
except BaseException as exc:
 record['invocationError']={'type':type(exc).__name__,'message':str(exc)}
 raise
finally:
 record['finishedAtUtc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 (folder/'result.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in record.items() if k in ('executed','rc','durationSeconds','stateSummary','invocationError')},ensure_ascii=False))
if record.get('rc')!=0:raise SystemExit(record.get('rc',1))

