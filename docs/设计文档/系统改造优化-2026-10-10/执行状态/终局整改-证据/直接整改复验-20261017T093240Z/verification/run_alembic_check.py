from pathlib import Path
import datetime,hashlib,json,os,subprocess,time
ROOT=Path('/Users/zhangping/DEV/clpm-remediation-20261017');OUT=Path(__file__).parent
cfg=json.loads(subprocess.run(['docker','inspect','clpm-mvp-postgres'],capture_output=True,text=True,check=True).stdout)[0]
pg=dict(x.split('=',1) for x in cfg['Config']['Env'] if '=' in x)
env=os.environ.copy();env.update({'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':'17102','POSTGRES_USER':pg.get('POSTGRES_USER','clpm'),'POSTGRES_PASSWORD':pg['POSTGRES_PASSWORD'],'POSTGRES_DB':'clpm_final_reverify_20261017_093240_phase2','CLPM_TEST_MODE':'0','CLPM_SKIP_EXIT_HOOKS':'1','JWT_SECRET_KEY':'isolated-final-audit-jwt-synthetic-key-20261017','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT/'backend'),'REDIS_HOST':'127.0.0.1','REDIS_PORT':'1','TDENGINE_HOST':'127.0.0.1','TDENGINE_PORT':'1','DEBUG':'true'})
for name in ['DATABASE_URL','TEST_DATABASE_URL','CLPM_BOOTSTRAP_TEST_ADMIN_DSN','CLPM_AUTHZ_TEST_DSN']:env.pop(name,None)
cmd=['/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python','-B','-m','alembic','check'];start=time.monotonic();r=subprocess.run(cmd,cwd=ROOT/'backend',env=env,capture_output=True,timeout=120)
for name,data in [('stdout',r.stdout),('stderr',r.stderr)]:(OUT/('alembic-check.'+name+'.log')).write_bytes(data)
record={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-start,'command':cmd,'cwd':str(ROOT/'backend'),'database':env['POSTGRES_DB'],'boundary':'own scratch DB schema read check; no migration/DDL/Worker/Beat invocation','exit_code':r.returncode,'stdout_sha256':hashlib.sha256(r.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(r.stderr).hexdigest()};(OUT/'alembic-check.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));print(r.stdout.decode(errors='replace')[-1000:]);print(r.stderr.decode(errors='replace')[-1000:]);raise SystemExit(r.returncode)
