from pathlib import Path
import datetime,hashlib,json,subprocess
OUT=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z')/'f06';OUT.mkdir(parents=True,exist_ok=True)
def run(args,cwd=None):
 p=subprocess.run(args,cwd=cwd,capture_output=True,text=True)
 return {'command':args,'cwd':cwd,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
repos={}
for name,path in [('main','/Users/zhangping/DEV/CLPM-MVP'),('candidate','/Users/zhangping/DEV/clpm-remediation-20261017'),('runtime','/Users/zhangping/DEV/clpm-g5-runtime-20261015')]:
 repos[name]={'path':path,'head':run(['git','rev-parse','HEAD'],path),'branch':run(['git','branch','--show-current'],path),'tracked_status':run(['git','status','--porcelain=v1','--untracked-files=no'],path)}
raw_ps=run(['ps','-axo','pid=,ppid=,pgid=,lstart=,command='])
processes=[line for line in raw_ps['stdout'].splitlines() if 'clpm-g5-runtime-20261015' in line or ('vite' in line and '/Users/zhangping/DEV/CLPM-MVP/frontend' in line)]
pids=[int(line.split()[0]) for line in processes]
details={}
for pid in pids:
 details[str(pid)]=run(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'])
query="BEGIN TRANSACTION READ ONLY; SELECT json_build_object('revision',(SELECT version_num FROM alembic_version LIMIT 1),'config',(SELECT json_agg(json_build_object('key',key,'value',value,'updatedAt',updated_at,'updatedBy',updated_by) ORDER BY key) FROM sys_config),'policy',(SELECT json_agg(row_to_json(p)) FROM policy_publication_guard p)); COMMIT;"
db=run(['docker','exec','clpm-mvp-postgres','psql','-U','clpm','-d','clpm','-X','-qAt','-v','ON_ERROR_STOP=1','-c',query])
assert db['exit_code']==0,db['stderr']
payload=json.loads(db['stdout'].strip())
rows=payload['config'] or []
for row in rows:
 value=row.pop('value')
 row['valueSha256']=hashlib.sha256(('NULL' if value is None else str(value)).encode()).hexdigest()
canonical=json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
config_hash=hashlib.sha256(canonical).hexdigest()
db_evidence={'command':db['command'],'exit_code':db['exit_code'],'stderr':db['stderr'],'alembic_version':payload['revision'],'policy_publication_guard':payload['policy'],'configuration_identity':{'kind':'content-and-updatedAt snapshot, not a single global revision','row_count':len(rows),'sha256':config_hash,'values_redacted':True,'rows':rows}}
result={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'repositories':repos,'processes':processes,'process_details':details,'database':db_evidence,'health':run(['curl','--fail','--silent','--show-error','--max-time','5','http://127.0.0.1:17101/health']),'runtime_backend_equivalence':run(['git','diff','c9e01f5d','b676ea59','--','backend'],repos['main']['path']),'remote_state':'not queried; no claim about github/main','process_memory_sha':'not observed; cwd/disk identity only'}
(OUT/'runtime-facts.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'observed_at':result['observed_at'],'schema':payload['revision'],'config_rows':len(rows),'config_sha256':config_hash,'processes':len(processes),'exit_code':0},ensure_ascii=False))

