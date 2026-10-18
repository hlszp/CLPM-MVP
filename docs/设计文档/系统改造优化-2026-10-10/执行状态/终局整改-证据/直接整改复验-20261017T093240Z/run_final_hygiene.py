from pathlib import Path
import asyncio,asyncpg,datetime,hashlib,json,os,socket,subprocess,urllib.request
E=Path(__file__).parent;S=E.parent.parent
MAIN=Path('/Users/zhangping/DEV/CLPM-MVP');SRC=Path('/Users/zhangping/DEV/clpm-remediation-20261017');RUNTIME=Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')
def run(args,cwd=None):
 r=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=30)
 return {'command':args,'cwd':str(cwd) if cwd else None,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
def git(root,*args):return run(['git',*args],root)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
result={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'check_scope':'read only final snapshot; no shared service/git mutations','protected_files':[],'repositories':{},'checks':{},'owned_cleanup':{}}
for file,expected in [('终局对标检查报告.md','517e91b57f2e2ff3c182be30d32ffb329d9c7b9397e2d3395188f190fe167016'),('终局整改-非作者复验回执.md','54cc82ca97e64fb7fd5314b14b41d5444ccaaf719b8478651d919e7a50f853e6')]:
 result['protected_files'].append({'path':str(S/file),'sha256':sha(S/file),'expected':expected,'matches':sha(S/file)==expected})
expected_heads={'main':'b676ea59158aa4d1e106579017a85860add60293','candidate':'dbb1de314ae50e7373c6559ab746b690909bf1b0','runtime':'c9e01f5d85294f5ad403fac7ae2e571ee472e9ad'}
for label,root in [('main',MAIN),('candidate',SRC),('runtime',RUNTIME)]:
 h=git(root,'rev-parse','HEAD');status=git(root,'status','--short');changed=git(root,'diff','--name-only','HEAD');result['repositories'][label]={'root':str(root),'head':h,'status':status,'changed':changed,'expected_head':expected_heads[label],'head_matches':h['stdout'].strip()==expected_heads[label]}
m=json.loads((E/'candidate/source-manifest.json').read_text());rows=[]
for row in m['files']:
 actual=sha(SRC/row['path']);rows.append({'path':row['path'],'actual_sha256':actual,'expected_sha256':row['sha256'],'matches':actual==row['sha256']})
result['candidate_files']=rows
actual_paths=set(git(SRC,'diff','--name-only','HEAD')['stdout'].splitlines())|set(git(SRC,'ls-files','--others','--exclude-standard')['stdout'].splitlines())
result['checks']['candidate_path_set_matches']=actual_paths=={r['path'] for r in rows}
result['checks']['candidate_patch_hash_matches']=sha(E/'candidate/candidate.patch')==m['patch_sha256']
main_changed=result['repositories']['main']['changed']['stdout'].splitlines();prefix='docs/设计文档/系统改造优化-2026-10-10/执行状态/'
result['checks']['main_tracked_changes_docs_only']=all(p.startswith(prefix) for p in main_changed)
ps=run(['ps','-Ao','pid,ppid,pgid,lstart,command']);lines=ps['stdout'].splitlines();records=[]
for l in lines[1:]:
 parts=l.split(None,3)
 if len(parts)>=4:
  try:pid,ppid=int(parts[0]),int(parts[1])
  except ValueError:continue
  if pid in [57294,57324,57326,57651] or ppid==57326:records.append({'pid':pid,'ppid':ppid,'line':l})
result['shared_processes']={'command':ps['command'],'exit_code':ps['exit_code'],'records':records}
result['checks']['shared_parent_processes_present']=all(any(r['pid']==p for r in records) for p in [57294,57324,57326,57651])
result['checks']['one_worker_four_pool']=sum(r['ppid']==57326 for r in records)==4 and any(r['pid']==57326 and r['ppid']==57294 for r in records)
ports=[]
for port in [17261,17263,17266]:
 with socket.socket() as sock:ports.append({'port':port,'closed':sock.connect_ex(('127.0.0.1',port))!=0})
result['owned_cleanup']['ports']=ports
result['owned_cleanup']['processes']=[{'pid':p,'absent':not any(l.split(None,1)[0]==str(p) for l in lines[1:])} for p in [54398,54399,58025,64667]]
containers=[]
for name in ['clpm-final-audit-redis-093240','clpm-final-audit-redis-093240-phase2']:
 r=run(['docker','ps','-aq','--filter','name=^/'+name+'$']);containers.append({'name':name,'probe':r,'absent':r['exit_code']==0 and not r['stdout'].strip()})
result['owned_cleanup']['redis_containers']=containers
cfg=json.loads(subprocess.run(['docker','inspect','clpm-mvp-postgres'],capture_output=True,text=True,check=True).stdout)[0];env=dict(x.split('=',1) for x in cfg['Config']['Env'] if '=' in x)
async def databases():
 conn=await asyncpg.connect(host='127.0.0.1',port=17102,user=env.get('POSTGRES_USER','clpm'),password=env['POSTGRES_PASSWORD'],database=env.get('POSTGRES_DB','clpm'))
 try:
  async with conn.transaction(readonly=True):
   names=['clpm_final_reverify_20261017_093240','clpm_final_reverify_20261017_093240_phase2'];found=await conn.fetch('SELECT datname FROM pg_database WHERE datname=ANY($1::text[])',names)
   revision=await conn.fetchval('SELECT version_num FROM alembic_version')
   return {'command':'BEGIN READ ONLY; SELECT datname FROM pg_database WHERE datname=ANY(owned_names); SELECT version_num FROM alembic_version; COMMIT','requested_owned_databases':names,'existing_owned_databases':[r['datname'] for r in found],'shared_schema':revision,'readonly':True}
 finally:await conn.close()
result['database_probe']=asyncio.run(databases())
with urllib.request.urlopen('http://127.0.0.1:17101/health',timeout=5) as r:result['shared_health']={'status':r.status,'body':json.loads(r.read())}
result['checks']['owned_cleanup_complete']=all(p['closed'] for p in ports) and all(p['absent'] for p in result['owned_cleanup']['processes']) and all(r['absent'] for r in containers) and not result['database_probe']['existing_owned_databases']
result['checks']['protected_hashes_match']=all(r['matches'] for r in result['protected_files'])
result['checks']['candidate_hashes_match']=all(r['matches'] for r in rows)
result['checks']['all_heads_match']=all(r['head_matches'] for r in result['repositories'].values())
result['checks']['schema_still_p702perm01']=result['database_probe']['shared_schema']=='p702perm01'
result['all_checks_passed']=all(result['checks'].values())
result['action_boundaries']={'commit_merge_push_deploy':False,'worker_beat_start_stop_restart':False,'own_api_forced_after_grace':json.loads((E/'f07/phase2/scratch-cleanup.json').read_text())['owned_processes'][0]['exit_code']==-9,'remote_or_memory_loaded_sha_verified':False}
(E/'final-hygiene.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'all_checks_passed':result['all_checks_passed'],'checks':result['checks'],'db':result['database_probe'],'shared_pids':[r['pid'] for r in records]},ensure_ascii=False));raise SystemExit(0 if result['all_checks_passed'] else 1)
