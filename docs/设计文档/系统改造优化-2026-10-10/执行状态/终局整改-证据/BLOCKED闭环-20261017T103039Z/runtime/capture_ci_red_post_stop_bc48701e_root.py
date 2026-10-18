from pathlib import Path
from datetime import datetime,timezone
import asyncio,hashlib,json,subprocess,sys
import asyncpg
from dotenv import dotenv_values
E=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");R=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
out=E/'runtime/ci-red-bc48701e-post-stop-root';out.mkdir(exist_ok=False)
env_file=R/'backend/.env';assert hashlib.sha256(env_file.read_bytes()).hexdigest()=='27d6fc10cc68376b056d1c7803f1b6361e48c11bc3de46c4e8a46cc4b2abe04b'
cfg=dotenv_values(env_file);assert cfg['POSTGRES_DB']=='clpm_capacity_20261017_s01' and cfg['POSTGRES_HOST'] in ('127.0.0.1','localhost') and cfg['POSTGRES_PORT']=='17102' and cfg['POSTGRES_USER']=='clpm'
results=('kpi_snapshot_hourly','kpi_snapshot_custom','loop_confidence_latest','kpi_node_snapshot_hourly','kpi_node_snapshot_daily','kpi_node_snapshot_monthly','calculation_result_record','calculation_result_attempt','calculation_result_legacy_map','calculation_dataset_snapshot','workbench_loop_latest','unit_kpi_summary')
sqls=[
"SELECT jsonb_build_object('database',current_database(),'readOnly',current_setting('transaction_read_only'),'isolation',current_setting('transaction_isolation'),'timezone',current_setting('TimeZone'),'snapshot',txid_current_snapshot()::text,'atUtc',clock_timestamp())::text",
"SELECT jsonb_build_object('loops',(SELECT count(*) FROM loop_ledger),'pointMappings',(SELECT count(*) FROM point_mapping),'histories',(SELECT count(*) FROM loop_tag_binding_history),'declaredSyntheticHistories',(SELECT count(*) FROM loop_tag_binding_history WHERE basis='SYNTHETIC_FIXTURE' AND valid_from='2026-10-15T12:00:00Z'::timestamptz AND mapping_version=1 AND valid_to IS NULL),'schema',(SELECT jsonb_agg(version_num ORDER BY version_num) FROM alembic_version))::text"
]
sqls += [f"SELECT jsonb_build_object('table','{table}','rows',count(*))::text FROM {table}" for table in results]
(out/'readonly-queries.json').write_text(json.dumps({'transaction':'REPEATABLE READ READ ONLY; SET LOCAL TIME ZONE UTC','sql':sqls},indent=2)+'\n')
async def observe():
 conn=await asyncpg.connect(host=cfg['POSTGRES_HOST'],port=17102,user='clpm',password=cfg['POSTGRES_PASSWORD'],database=cfg['POSTGRES_DB'],server_settings={'application_name':'F06-post-stop-readonly','statement_timeout':'30000'})
 try:
  async with conn.transaction(isolation='repeatable_read',readonly=True):
   await conn.execute("SET LOCAL TIME ZONE 'UTC'")
   rows=[json.loads(await conn.fetchval(sql)) for sql in sqls]
 finally:await conn.close()
 return rows
before=datetime.now(timezone.utc).isoformat();rows=asyncio.run(observe());finished=datetime.now(timezone.utc).isoformat()
(out/'postgres-readonly.json').write_text(json.dumps({'startedAtUtc':before,'finishedAtUtc':finished,'rows':rows},indent=2)+'\n')
assert rows[0]['database']==cfg['POSTGRES_DB'] and rows[0]['readOnly']=='on' and rows[0]['isolation']=='repeatable read'
assert rows[1]['loops']==1000 and rows[1]['pointMappings']==7000 and rows[1]['histories']==7000 and rows[1]['declaredSyntheticHistories']==7000
assert all(r['rows']==0 for r in rows[2:])
argv=['lsof','-nP','-iTCP:17101','-iTCP:17261','-iTCP:17266','-sTCP:LISTEN'];p=subprocess.run(argv,capture_output=True)
(out/'ports.stdout.log').write_bytes(p.stdout);(out/'ports.stderr.log').write_bytes(p.stderr);assert p.returncode==1 and not p.stdout and not p.stderr
ps=subprocess.run(['ps','-ax','-o','pid=,ppid=,command='],capture_output=True);assert ps.returncode==0
matching=[line for line in ps.stdout.decode().splitlines() if str(R/'backend/.venv/bin/python') in line and ('-m celery' in line or '-m uvicorn app.main:app' in line)]
assert not matching,matching
(out/'ps.stdout.log').write_bytes(ps.stdout);(out/'ps.stderr.log').write_bytes(ps.stderr)
sys.path.insert(0,str(E));from capacity_keeper_guards import private_broker_snapshot
broker=private_broker_snapshot();assert broker['quiescent'];(out/'broker.json').write_text(json.dumps(broker,indent=2)+'\n')
facts={}
for name,root in (('main',Path("/Users/zhangping/DEV/CLPM-MVP")),('candidate',Path("/Users/zhangping/DEV/clpm-remediation-20261017")),('runtime',R)):
 facts[name]={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'status':subprocess.check_output(['git','status','--short'],cwd=root,text=True)}
record={'observedAtUtc':datetime.now(timezone.utc).isoformat(),'postgresIdentity':rows[0],'inputRoster':rows[1],'all12ResultTablesRowsZero':True,'brokerQuiescent':True,'ownedRuntimeApiCeleryProcesses':matching,'apiPortsRc':p.returncode,'apiWaitExitCode':'UNKNOWN; original Popen owner no longer available; lifecycle only observed all processes gone','git':facts,'formalCapacityBatchesExecuted':0,'formalOutputDirectoryExists':(E/'f02/series-s01/formal').exists(),'fullCi39044390911':'FAIL_BACKEND_3_G3','remoteFourCaseAndIndependentNumeric':'PASS_SUBGATES_ONLY','G6':'CONDITIONAL_PASS','G7':'CONDITIONAL_PASS','fourUserWaiversRetained':True,'productionTouched':False}
(out/'state.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
items=[]
for f in sorted(out.iterdir()):
 if f.is_file():items.append({'path':f.name,'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
(out/'evidence-manifest.json').write_text(json.dumps({'items':items},indent=2)+'\n')
print(json.dumps({'statePath':str(out/'state.json'),'manifestSha256':hashlib.sha256((out/'evidence-manifest.json').read_bytes()).hexdigest(),'schema':rows[1]['schema'],'inputRoster':rows[1],'all12ResultTablesRowsZero':True,'portsRc':p.returncode,'brokerQuiescent':True,'formalBatches':0},ensure_ascii=False))

