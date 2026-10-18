from pathlib import Path
import datetime,hashlib,json,os,subprocess,sys,time
ROOT=Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUT=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z')/'verification';OUT.mkdir(parents=True,exist_ok=True)
PYTHON='/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python'
name=sys.argv[1];kind=sys.argv[2]
commands={
 'backend-full':([PYTHON,'-B','-m','pytest','-q','-p','no:cacheprovider','--junitxml='+str(OUT/(name+'.junit.xml'))],ROOT/'backend',900),
 'backend-collect':([PYTHON,'-B','-m','pytest','--collect-only','-q','-p','no:cacheprovider'],ROOT/'backend',180),
 'ruff-check':([PYTHON,'-m','ruff','check','.'],ROOT/'backend',180),
 'ruff-format':([PYTHON,'-m','ruff','format','--check','.'],ROOT/'backend',180),
 'frontend-full':(['pnpm','exec','vitest','run','--dom','--no-cache','--reporter=default','--reporter=json','--outputFile.json='+str(OUT/(name+'.results.json'))],ROOT/'frontend',420),
 'frontend-build':(['pnpm','run','build:antd'],ROOT/'frontend',420),
 'frontend-eslint':(['pnpm','exec','eslint','apps/web-antd','--cache'],ROOT/'frontend',420),
 'frontend-typecheck':(['pnpm','run','check:type'],ROOT/'frontend',420),
}
cmd,cwd,limit=commands[kind]
env=os.environ.copy();env.update({'CLPM_TEST_MODE':'1','CLPM_SKIP_EXIT_HOOKS':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT/'backend'),'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':'1','REDIS_HOST':'127.0.0.1','REDIS_PORT':'1','TDENGINE_HOST':'127.0.0.1','TDENGINE_PORT':'1','JWT_SECRET_KEY':'isolated-check-synthetic-jwt-key-20261017','DEBUG':'true'})
for key in ['CLPM_BOOTSTRAP_TEST_ADMIN_DSN','CLPM_AUTHZ_TEST_DSN','DATABASE_URL','TEST_DATABASE_URL']:env.pop(key,None)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def identity():
 data={}
 for scope in (['backend/app','backend/tests'] if kind.startswith('backend') or kind.startswith('ruff') else ['frontend/apps/web-antd/src']):
  for f in (ROOT/scope).rglob('*'):
   if f.is_file() and f.suffix in {'.py','.ts','.vue','.json'}:data[str(f.relative_to(ROOT))]=digest(f)
 return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
before=identity();started=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
with (OUT/(name+'.stdout.log')).open('wb') as stdout,(OUT/(name+'.stderr.log')).open('wb') as stderr:
 try:r=subprocess.run(cmd,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=limit);rc=r.returncode
 except subprocess.TimeoutExpired:rc=124
after=identity()
record={'started_at':started,'elapsed_seconds':time.monotonic()-t,'command':cmd,'cwd':str(cwd),'exit_code':rc,'source_identity_before':before,'source_identity_after':after,'source_stable':before==after,'stdout_sha256':digest(OUT/(name+'.stdout.log')),'stderr_sha256':digest(OUT/(name+'.stderr.log')),'environment_boundary':'backend test mode; PG/Redis/TD unreachable ports; no real integration DSN'}
(OUT/(name+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(record,ensure_ascii=False),flush=True)
print('\n'.join((OUT/(name+'.stdout.log')).read_text(errors='replace').splitlines()[-5:]),flush=True)
sys.exit(rc if before==after else 125)

