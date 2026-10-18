from pathlib import Path
import asyncio,datetime,hashlib,json,os,signal,socket,subprocess,sys,time
import asyncpg
ROOT=Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUT=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z')/'f07'/'phase2'
OUT.mkdir(parents=True,exist_ok=True)
DB='clpm_final_reverify_20261017_093240_phase2'
REDIS='clpm-final-audit-redis-093240-phase2'
PYTHON='/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python'
LOOP='a0d17000-0000-4000-8000-000000000001'
UNIT='a0d17000-0000-4000-8000-000000000002'
PEER='a0d17000-0000-4000-8000-000000000003'
RESTRICTED='a0d17000-0000-4000-8000-000000000004'
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def run(args,**kw):return subprocess.run(args,capture_output=True,text=True,check=True,**kw)
for port in [17261,17263,17266]:
 with socket.socket() as sock:
  assert sock.connect_ex(('127.0.0.1',port))!=0,f'port in use: {port}'
config=json.loads(run(['docker','inspect','clpm-mvp-postgres']).stdout)[0]
pg_env=dict(item.split('=',1) for item in config['Config']['Env'] if '=' in item)
pg={'host':'127.0.0.1','port':17102,'user':pg_env.get('POSTGRES_USER','clpm'),'password':pg_env['POSTGRES_PASSWORD']}
env=os.environ.copy()
env.update({'POSTGRES_HOST':pg['host'],'POSTGRES_PORT':str(pg['port']),'POSTGRES_USER':pg['user'],'POSTGRES_PASSWORD':pg['password'],'POSTGRES_DB':DB,'REDIS_HOST':'127.0.0.1','REDIS_PORT':'17263','REDIS_PASSWORD':'','REDIS_DB':'0','JWT_SECRET_KEY':'isolated-final-audit-jwt-synthetic-key-20261017','DEBUG':'true','CLPM_TEST_MODE':'0','CLPM_SKIP_EXIT_HOOKS':'1','SIGNALR_ENABLED':'false','AAS_SYNC_ENABLED':'false','TDENGINE_HOST':'127.0.0.1','TDENGINE_PORT':'1','CORS_ORIGINS':'["http://127.0.0.1:17266"]','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(OUT)+os.pathsep+str(ROOT/'backend')})
for name in ['CELERY_BROKER_URL','CELERY_RESULT_BACKEND','CLPM_BOOTSTRAP_TEST_ADMIN_DSN','CLPM_AUTHZ_TEST_DSN','DATABASE_URL','TEST_DATABASE_URL']:env.pop(name,None)
os.environ.update(env)
sys.path.insert(0,str(ROOT/'backend'))
from app.core.security import hash_password
password_hash=hash_password('Audit-test-20261017!')
async def prepare():
 admin=await asyncpg.connect(**pg,database=pg_env.get('POSTGRES_DB','clpm'))
 try:
  assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)',DB)
  await admin.execute('CREATE DATABASE '+DB)
 finally:await admin.close()
 conn=await asyncpg.connect(**pg,database=DB)
 try:
  for name in ['01_schema.sql','02_seed_data.sql']:
   data=(ROOT/'db/postgresql'/name).read_text();await conn.execute(data)
  await conn.execute('CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)')
  await conn.execute("INSERT INTO alembic_version VALUES ('p702perm01') ON CONFLICT DO NOTHING")
  await conn.execute('UPDATE sys_user SET password_hash=$1',password_hash)
  await conn.execute("INSERT INTO plant_node(id,name,type) VALUES($1,'AUDIT TEST UNIT','UNIT')",UNIT)
  await conn.execute("INSERT INTO loop_ledger(id,tag_name,description,status) VALUES($1,'AUDIT-F07-0001','isolated test input, no plant data','PARTIAL')",LOOP)
  for uid,name in [(PEER,'audit_peer'),(RESTRICTED,'audit_restricted')]:
   await conn.execute("INSERT INTO sys_user(id,username,password_hash,display_name,role) VALUES($1,$2,$3,$2,'IC_ENGINEER')",uid,name,password_hash)
  await conn.execute("INSERT INTO sys_user_scope(user_id,scope_type,node_id) VALUES($1,'NODE_SUBTREE',$2)",RESTRICTED,UNIT)
  tables=await conn.fetchval("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
  save('scratch-setup.json',{'created_at':stamp(),'database':DB,'schema_revision':'p702perm01','tables':tables,'loop_id':LOOP,'peer_user':PEER,'restricted_user':RESTRICTED,'seed_input':'owned synthetic test users/loop; no measurement outputs fabricated','authz_mode':'real DB/runtime matrix; CLPM_TEST_MODE=0','lifecycle':'test wrapper disables background startup only; no Worker/Beat/SignalR start','postgres_credentials':'read in memory from existing container; not saved'})
 finally:await conn.close()
async def drop():
 conn=await asyncpg.connect(**pg,database=pg_env.get('POSTGRES_DB','clpm'))
 try:
  await conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid<>pg_backend_pid()',DB)
  await conn.execute('DROP DATABASE IF EXISTS '+DB)
 finally:await conn.close()
children=[];handles=[];created=False;redis_started=False;errors=[]
def start(args,name,cwd,child_env):
 out=(OUT/(name+'.stdout.log')).open('w');err=(OUT/(name+'.stderr.log')).open('w');handles.extend([out,err])
 proc=subprocess.Popen(args,cwd=cwd,env=child_env,stdout=out,stderr=err,start_new_session=True)
 children.append((name,proc));return proc
try:
 asyncio.run(prepare());created=True
 image=run(['docker','image','inspect','redis:7-alpine','--format','{{.Id}}']).stdout.strip()
 container=run(['docker','run','--rm','-d','--name',REDIS,'-p','127.0.0.1:17263:6379',image,'redis-server','--save','','--appendonly','no']).stdout.strip();redis_started=True
 (OUT/'isolated_app.py').write_text('from contextlib import asynccontextmanager\nfrom app.main import create_app\nfrom app.core.db import dispose_engine\napp=create_app()\n@asynccontextmanager\nasync def isolated_lifespan(app):\n    yield\n    await dispose_engine()\napp.router.lifespan_context=isolated_lifespan\n')
 api=start([PYTHON,'-B','-m','uvicorn','isolated_app:app','--host','127.0.0.1','--port','17261'],'scratch-api',ROOT/'backend',env)
 front_env=env.copy();front_env['VITE_GLOB_API_URL']='http://127.0.0.1:17261/api/v1';front_env['VITE_PORT']='17266'
 front=start(['pnpm','exec','vite','--host','127.0.0.1','--port','17266','--strictPort'],'scratch-frontend',ROOT/'frontend/apps/web-antd',front_env)
 import urllib.request
 for _ in range(180):
  assert api.poll() is None,'isolated API exited';assert front.poll() is None,'isolated frontend exited'
  try:
   a=urllib.request.urlopen('http://127.0.0.1:17261/health',timeout=2)
   b=urllib.request.urlopen('http://127.0.0.1:17266/',timeout=2)
   if a.status==200 and b.status==200:break
  except Exception:time.sleep(0.5)
 else:raise RuntimeError('isolated endpoints did not become ready')
 save('scratch-ready.json',{'ready_at':stamp(),'api':'http://127.0.0.1:17261','frontend':'http://127.0.0.1:17266','api_pid':api.pid,'frontend_pid':front.pid,'redis_container':container,'postgres_database':DB,'worker_beat_started':False})
 print('SCRATCH_READY '+str(OUT/'scratch-ready.json'),flush=True)
 until=time.monotonic()+3600
 while not (OUT/'stop-scratch').exists() and time.monotonic()<until:
  assert api.poll() is None and front.poll() is None,'owned service exited'
  time.sleep(0.5)
except Exception as exc:
 errors.append({'type':type(exc).__name__,'message':str(exc)})
 print(type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
finally:
 for name,proc in reversed(children):
  if proc.poll() is None:
   os.killpg(proc.pid,signal.SIGTERM)
   try:proc.wait(timeout=15)
   except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
 for handle in handles:handle.close()
 if redis_started:subprocess.run(['docker','stop',REDIS],capture_output=True)
 if created:asyncio.run(drop())
 save('scratch-cleanup.json',{'completed_at':stamp(),'owned_processes':[{ 'name':n,'pid':p.pid,'exit_code':p.returncode} for n,p in children],'database_dropped':created,'redis_stopped':redis_started,'errors':errors,'worker_beat_operation':False})
 print('SCRATCH_CLEANED',flush=True)
sys.exit(1 if errors else 0)

