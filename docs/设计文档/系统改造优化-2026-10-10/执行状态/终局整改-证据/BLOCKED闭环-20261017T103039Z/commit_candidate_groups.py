from pathlib import Path
import ast,datetime,hashlib,json,os,subprocess,sys
R=Path('/Users/zhangping/DEV/clpm-remediation-20261017');E=Path(__file__).parent/'git';E.mkdir(exist_ok=True)
env=os.environ.copy();env.update({'UV_PYTHON':'3.12','CLPM_TEST_MODE':'1','CLPM_SKIP_EXIT_HOOKS':'1','LEFTHOOK_BIN':str(R/'frontend/node_modules/.bin/lefthook')})
assert Path(env['LEFTHOOK_BIN']).exists()
def call(args,input=None):return subprocess.run(args,cwd=R,env=env,input=input,capture_output=True)
assert call(['git','diff','--cached','--quiet']).returncode==0,'existing staged changes; do not overwrite'
groups=[
 ('fix(kpi): propagate bounded resource failures',['backend/app/core/async_tasks.py','backend/app/services/data_planner.py','backend/app/services/data_source/logical_wide_builder.py','backend/app/tasks/kpi_calc.py','backend/tests/test_backfill_recovery_and_skip.py','backend/tests/test_kpi_calc.py','backend/tests/test_p5_storm_rework.py'],None),
 ('test(kpi): cover acquisition retry boundaries',[],('backend/tests/test_g5_data_acquisition_failure.py',394)),
 ('test(kpi): reject minority resource false success',['backend/tests/test_g5_data_acquisition_failure.py'],None),
 ('feat(batch): add immutable restore snapshots',['backend/app/services/loop_batch_restore.py','backend/app/schemas/loop_batch.py'],None),
 ('feat(batch): expose guarded restore endpoints',['backend/app/services/loop_batch.py','backend/app/api/v1/endpoints/loops.py'],None),
 ('test(batch): verify exact guarded restoration',['backend/tests/test_loop_batch_restore.py'],None),
 ('feat(batch): add snapshot restore client',['frontend/apps/web-antd/src/api/engineering-template.ts','frontend/apps/web-antd/src/views/config/engineering-templates/batch-restore-modal.vue','frontend/apps/web-antd/src/views/config/engineering-templates/template-utils.ts'],None),
 ('feat(batch): wire chunked apply and restore UI',['frontend/apps/web-antd/src/views/config/engineering-templates/batch-apply-modal.vue','frontend/apps/web-antd/src/views/config/engineering-templates/index.vue'],None),
 ('test(batch): cover restore selection and keys',['frontend/apps/web-antd/src/__tests__/p6-eng-templates.test.ts'],None),
 ('fix(follow): isolate account and conflict state',['frontend/apps/web-antd/src/composables/use-loop-follow.ts','frontend/apps/web-antd/src/store/auth.ts','frontend/apps/web-antd/src/api/personal-work.ts','frontend/apps/web-antd/src/__tests__/loop-follow.test.ts','frontend/apps/web-antd/src/__tests__/store.test.ts'],None),
 ('test(follow): use real conflict error bodies',['frontend/apps/web-antd/src/__tests__/personal-work-conflict.test.ts'],None),
]
allrows=[]
for index,(subject,paths,partial) in enumerate(groups,1):
 assert len(subject)<=50
 before=call(['git','rev-parse','HEAD']).stdout.decode().strip()
 if paths:
  r=call(['git','add','--',*paths]);assert r.returncode==0,r.stderr
 if partial:
  name,limit=partial;data=''.join((R/name).read_text().splitlines(keepends=True)[:limit]);ast.parse(data)
  r=call(['git','hash-object','-w','--stdin'],data.encode());assert r.returncode==0;r=call(['git','update-index','--add','--cacheinfo','100644,'+r.stdout.decode().strip()+','+name]);assert r.returncode==0
 stat=call(['git','diff','--cached','--numstat']).stdout.decode();changed=sum(int(x) for l in stat.splitlines() for x in l.split('\t')[:2] if x!='-');assert 0<changed<=500,(subject,changed,stat)
 started=datetime.datetime.now(datetime.timezone.utc).isoformat();cmd=['git','commit','-m',subject,'-m','Preserve failed evidence and enforce audited semantics before live acceptance.\nThe complete candidate is independently tested; production is out of scope.'];r=call(cmd)
 name=f'{index:02d}';(E/(name+'.stdout.log')).write_bytes(r.stdout);(E/(name+'.stderr.log')).write_bytes(r.stderr)
 row={'started_at':started,'command':cmd,'cwd':str(R),'parent':before,'exit_code':r.returncode,'subject':subject,'changed_lines':changed,'numstat':stat,'stdout_sha256':hashlib.sha256(r.stdout).hexdigest(),'stderr_sha256':hashlib.sha256(r.stderr).hexdigest()}
 if r.returncode==0:row['commit']=call(['git','rev-parse','HEAD']).stdout.decode().strip()
 (E/(name+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');allrows.append(row);print(json.dumps({k:row[k] for k in ['subject','changed_lines','exit_code']},ensure_ascii=False),flush=True)
 if r.returncode:print(r.stderr.decode(errors='replace'),flush=True);break
(E/'commit-results.json').write_text(json.dumps(allrows,ensure_ascii=False,indent=2)+'\n');assert len(allrows)==len(groups) and all(x['exit_code']==0 for x in allrows)
