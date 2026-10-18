from pathlib import Path
import ast, datetime, hashlib, json, os, subprocess, time
evidence=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/非作者复验-20261017T085542Z")
root=Path("/var/folders/fj/6rslvftn1nl7k2fzq1nxrhq80000gn/T/clpm-independent-20261017T085542Z-_jgi2ts5/candidate")
python='/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python'
env=os.environ.copy()
env.update({'CLPM_TEST_MODE':'1','CLPM_SKIP_EXIT_HOOKS':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root/'backend'),'POSTGRES_HOST':'127.0.0.1','POSTGRES_PORT':'1','REDIS_HOST':'127.0.0.1','REDIS_PORT':'1','JWT_SECRET_KEY':'independent-test-synthetic-key-longer-than-32-chars','DEBUG':'true'})
for key in ['CLPM_BOOTSTRAP_TEST_ADMIN_DSN','CLPM_AUTHZ_TEST_DSN','DATABASE_URL','TEST_DATABASE_URL']:
 env.pop(key,None)
results=[]
def run(name,args,timeout):
 command=[python,'-B','-m','pytest',*args,'-p','no:cacheprovider']
 started=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
 with (evidence/(name+'.stdout.log')).open('wb') as out,(evidence/(name+'.stderr.log')).open('wb') as err:
  try:r=subprocess.run(command,cwd=root/'backend',env=env,stdout=out,stderr=err,timeout=timeout);rc=r.returncode
  except subprocess.TimeoutExpired:rc=124
 result={'name':name,'command':command,'cwd':str(root/'backend'),'environment':{k:env[k] for k in ['CLPM_TEST_MODE','CLPM_SKIP_EXIT_HOOKS','PYTHONDONTWRITEBYTECODE','PYTHONPATH','POSTGRES_HOST','POSTGRES_PORT','REDIS_HOST','REDIS_PORT']},'started_at':started,'elapsed_seconds':time.monotonic()-t,'exit_code':rc,'stdout_sha256':hashlib.sha256((evidence/(name+'.stdout.log')).read_bytes()).hexdigest(),'stderr_sha256':hashlib.sha256((evidence/(name+'.stderr.log')).read_bytes()).hexdigest()}
 (evidence/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');results.append(result)
 print(name,'rc='+str(rc),flush=True);print('\n'.join((evidence/(name+'.stdout.log')).read_text(errors='replace').splitlines()[-3:]),flush=True)
 return rc
run('f01-targeted-19',['-q','tests/test_data_planner/test_data_version_preload.py','tests/test_g5_d01_opt_batch.py'],180)
run('f01-collect-full',['--collect-only','-q'],180)
target=root/'backend/app/services/node_performance.py';original=target.read_bytes();text=original.decode();lines=text.splitlines(keepends=True)
node=next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='_classify_node_results')
remove=''.join(lines[:node.lineno-1]+lines[node.end_lineno:])
mutation={'original_sha256':hashlib.sha256(original).hexdigest(),'function_lines':[node.lineno,node.end_lineno]}
try:
 target.write_text(remove);mutation['removed_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
 (evidence/'f01-remove-classifier.patch').write_text(__import__('difflib').unified_diff and ''.join(__import__('difflib').unified_diff(text.splitlines(keepends=True),remove.splitlines(keepends=True),fromfile='candidate/node_performance.py',tofile='isolated/remove-classifier.py')))
 run('f01-reverse-symbol-removed',['-q','tests/test_g5_d01_opt_batch.py::TestNodeAggregationCancelled'],180)
finally:target.write_bytes(original)
run('f01-forward-restored',['-q','tests/test_g5_d01_opt_batch.py::TestNodeAggregationCancelled'],180)
replacement="def _classify_node_results(results):\n    counts = {'success': 0, 'skipped': 0, 'failed': 0, 'cancelled': 0}\n    for result in results:\n        if isinstance(result, Exception):\n            counts['failed'] += 1\n        elif result is None:\n            counts['skipped'] += 1\n        else:\n            counts['success'] += 1\n    return counts\n"
behavior=''.join(lines[:node.lineno-1])+replacement+''.join(lines[node.end_lineno:])
try:
 target.write_text(behavior)
 (evidence/'f01-reverse-old-behavior.patch').write_text(''.join(__import__('difflib').unified_diff(text.splitlines(keepends=True),behavior.splitlines(keepends=True),fromfile='candidate/node_performance.py',tofile='isolated/old-classification.py')))
 run('f01-reverse-old-behavior',['-q','tests/test_g5_d01_opt_batch.py::TestNodeAggregationCancelled'],180)
finally:target.write_bytes(original)
mutation['restored_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
(evidence/'f01-mutation-restoration.json').write_text(json.dumps(mutation,indent=2)+'\n')
assert target.read_bytes()==original
run('f01-full-suite',['-q','--junitxml='+str(evidence/'f01-full-suite.junit.xml')],900)
(evidence/'f01-run-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')

