from pathlib import Path
import datetime,hashlib,http.server,json,os,shutil,subprocess,threading,time
evidence=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/非作者复验-20261017T085542Z")
source=Path("/Users/zhangping/DEV/clpm-remediation-20261017")
fixture=Path("/var/folders/fj/6rslvftn1nl7k2fzq1nxrhq80000gn/T/clpm-independent-20261017T085542Z-_jgi2ts5")/'gate-control'
(fixture/'scripts').mkdir(parents=True);(fixture/'e2e/tests').mkdir(parents=True)
original=(source/'scripts/run-critical-e2e.sh').read_bytes()
script=fixture/'scripts/run-critical-e2e.sh';script.write_bytes(original)
(fixture/'e2e/node_modules').symlink_to(source/'e2e/node_modules',target_is_directory=True)
(fixture/'e2e/package.json').write_text('{"name":"independent-critical-gate-controls","private":true,"type":"module"}\n')
(fixture/'e2e/playwright.config.ts').write_text("import { defineConfig } from '@playwright/test';\nexport default defineConfig({testDir:'./tests',workers:1,fullyParallel:false,retries:0,timeout:5000,reporter:'list',outputDir:'./test-results',projects:[{name:'chromium'}]});\n")
specs=['login','roles-smoke','confidence','loop']
def write_cases(mode):
 for file_index,name in enumerate(specs):
  lines=["import { test, expect } from '@playwright/test';"]
  for j in range(5):
   i=file_index*5+j
   if mode=='skip':lines.append("test.skip('controlled case "+str(i)+"', async () => { expect(true).toBe(true); });")
   else:lines.append("test('controlled case "+str(i)+"', async () => { expect("+('false' if mode=='fail' and i==0 else 'true')+").toBe(true); });")
  (fixture/'e2e/tests'/(name+'.spec.ts')).write_text('\n'.join(lines)+'\n')
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'owned gate health fixture')
 def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
url='http://127.0.0.1:'+str(server.server_port)
env=os.environ.copy();env.update({'E2E_BASE_URL':url,'E2E_API_BASE_URL':url,'npm_config_offline':'true','CI':'1'})
results=[]
def run(name):
 command=['bash',str(script)]
 start=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
 with (evidence/(name+'.stdout.log')).open('wb') as out,(evidence/(name+'.stderr.log')).open('wb') as err:
  try:r=subprocess.run(command,cwd=fixture,env=env,stdout=out,stderr=err,timeout=180);rc=r.returncode
  except subprocess.TimeoutExpired:rc=124
 result={'name':name,'command':command,'cwd':str(fixture),'fixture_only':True,'environment':{k:env[k] for k in ['E2E_BASE_URL','E2E_API_BASE_URL','npm_config_offline','CI']},'started_at':start,'elapsed_seconds':time.monotonic()-t,'exit_code':rc,'script_sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'stdout_sha256':hashlib.sha256((evidence/(name+'.stdout.log')).read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256((evidence/(name+'.stderr.log')).read_bytes()).hexdigest()}
 (evidence/(name+'.json')).write_text(json.dumps(result,indent=2)+'\n');results.append(result)
 print(name,'rc='+str(rc),flush=True);print('\n'.join((evidence/(name+'.stdout.log')).read_text(errors='replace').splitlines()[-6:]),flush=True)
try:
 write_cases('pass');run('f03-control-all-pass')
 changed=original.decode().replace('BASE_URL="${E2E_BASE_URL:-http://localhost:15666}"','BASE_URL="http://127.0.0.1:1"')
 assert changed!=original.decode()
 script.write_text(changed)
 (evidence/'f03-env-unreachable.patch').write_text(''.join(__import__('difflib').unified_diff(original.decode().splitlines(keepends=True),changed.splitlines(keepends=True),fromfile='candidate/run-critical-e2e.sh',tofile='isolated/unreachable.sh')))
 run('f03-control-env-unreachable')
 script.write_bytes(original)
 write_cases('fail');run('f03-control-injected-failure')
 write_cases('skip');run('f03-control-all-skipped')
 run('f03-control-all-skipped-confirmation')
 for name in specs:shutil.copyfile(fixture/'e2e/tests'/(name+'.spec.ts'),evidence/('f03-skipped-fixture-'+name+'.spec.ts'))
 shutil.copyfile(fixture/'e2e/playwright.config.ts',evidence/'f03-fixture-playwright.config.ts')
finally:
 script.write_bytes(original);server.shutdown();server.server_close()
(evidence/'f03-run-results.json').write_text(json.dumps(results,indent=2)+'\n')
print('script_restored',script.read_bytes()==original,flush=True)

