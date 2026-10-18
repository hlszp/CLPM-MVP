from pathlib import Path
import datetime,json,os,subprocess,time,hashlib,shutil
root=Path("/var/folders/fj/6rslvftn1nl7k2fzq1nxrhq80000gn/T/clpm-independent-20261017T085542Z-_jgi2ts5/candidate")
source=Path('/Users/zhangping/DEV/CLPM-MVP')
evidence=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/非作者复验-20261017T085542Z")
# Dependencies are reused read-only; source files remain the archived candidate.
for directory,dirs,files in os.walk(source/'frontend'):
 if 'node_modules' not in dirs:continue
 dirs.remove('node_modules');p=Path(directory)/'node_modules'
 rel=p.relative_to(source);target=root/rel
 if target.parent.exists() and not target.exists():target.symlink_to(p,target_is_directory=True)
env=os.environ.copy();env.update({'CI':'1','TZ':'Asia/Shanghai','VITEST_CACHE_DIR':str(Path("/var/folders/fj/6rslvftn1nl7k2fzq1nxrhq80000gn/T/clpm-independent-20261017T085542Z-_jgi2ts5")/'vitest-cache')})
binary=source/'frontend/node_modules/.bin/vitest'
runs=[]
for name,filters,limit in [
 ('f07-targeted-follow',['apps/web-antd/src/__tests__/loop-follow.test.ts','apps/web-antd/src/__tests__/loop-fleet-view.test.ts','apps/web-antd/src/__tests__/my-work.test.ts'],180),
 ('f07-full-vitest',[],300)]:
 cmd=[str(binary),'run','--config',str(root/'frontend/vitest.config.ts'),'--no-cache','--reporter=default','--reporter=json','--outputFile.json='+str(evidence/(name+'.results.json')),*filters]
 t=time.monotonic();started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (evidence/(name+'.stdout.log')).open('wb') as out,(evidence/(name+'.stderr.log')).open('wb') as err:
  try:r=subprocess.run(cmd,cwd=root/'frontend',env=env,stdout=out,stderr=err,timeout=limit);rc=r.returncode
  except subprocess.TimeoutExpired:rc=124
 data={'command':cmd,'cwd':str(root/'frontend'),'started_at':started,'elapsed_seconds':time.monotonic()-t,'exit_code':rc,'stdout_sha256':hashlib.sha256((evidence/(name+'.stdout.log')).read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256((evidence/(name+'.stderr.log')).read_bytes()).hexdigest()}
 (evidence/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n');runs.append(data)
 print(name,'rc='+str(rc),flush=True);print('\n'.join((evidence/(name+'.stdout.log')).read_text(errors='replace').splitlines()[-10:]),flush=True)
 if rc not in (0,1):break
(evidence/'f07-run-results.json').write_text(json.dumps(runs,indent=2)+'\n')

