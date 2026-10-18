from pathlib import Path
import datetime,json,os,subprocess,time,hashlib
source=Path('/Users/zhangping/DEV/CLPM-MVP')
evidence=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/非作者复验-20261017T085542Z")
hashes={ref:subprocess.check_output(['git','rev-parse',ref+':frontend'],cwd=source,text=True).strip() for ref in ['86301e5e','dbb1de31','b676ea59']}
assert len(set(hashes.values()))==1,hashes
(evidence/'f07-frontend-tree-equivalence.json').write_text(json.dumps(hashes,indent=2)+'\n')
env=os.environ.copy();env.update({'CI':'1','TZ':'Asia/Shanghai'})
results=[]
for name,filters,limit in [('f07-matching-tree-targeted',['apps/web-antd/src/__tests__/loop-follow.test.ts','apps/web-antd/src/__tests__/loop-fleet-view.test.ts','apps/web-antd/src/__tests__/my-work.test.ts'],180),('f07-matching-tree-full',[],300)]:
 cmd=[str(source/'frontend/node_modules/.bin/vitest'),'run','--dom','--no-cache','--reporter=default','--reporter=json','--outputFile.json='+str(evidence/(name+'.results.json')),*filters]
 t=time.monotonic();started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (evidence/(name+'.stdout.log')).open('wb') as out,(evidence/(name+'.stderr.log')).open('wb') as err:
  try:r=subprocess.run(cmd,cwd=source/'frontend',env=env,stdout=out,stderr=err,timeout=limit);rc=r.returncode
  except subprocess.TimeoutExpired:rc=124
 data={'command':cmd,'cwd':str(source/'frontend'),'frontend_tree_hash':hashes['dbb1de31'],'started_at':started,'elapsed_seconds':time.monotonic()-t,'exit_code':rc,'stdout_sha256':hashlib.sha256((evidence/(name+'.stdout.log')).read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256((evidence/(name+'.stderr.log')).read_bytes()).hexdigest()}
 (evidence/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n');results.append(data)
 print(name,'rc='+str(rc),flush=True);print('\n'.join((evidence/(name+'.stdout.log')).read_text(errors='replace').splitlines()[-10:]),flush=True)
(evidence/'f07-matching-tree-run-results.json').write_text(json.dumps(results,indent=2)+'\n')

