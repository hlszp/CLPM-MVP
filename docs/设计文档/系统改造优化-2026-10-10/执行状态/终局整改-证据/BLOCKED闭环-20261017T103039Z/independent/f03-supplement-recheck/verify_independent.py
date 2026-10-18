"""Non-author structural/executable checks for the frozen CI supplement only."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

import yaml

ROOT = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
E = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z')
OUT = E/'independent/f03-supplement-recheck'
BASE = '148dd21ac8450cd85d763f89a557adf68fd29fdb'
PYTHON = '/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python'
THREADS = ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS']
EXPECTED_MANIFEST = '5d4c04f8161252b4f1e9ad5bb70c63233ee32321b13841532ff77222e65e1fdc'

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(name, value):
    with (OUT/name).open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def run(name, argv, cwd=ROOT, env=None):
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,timeout=60)
    for kind, text in [('stdout',result.stdout),('stderr',result.stderr)]:
        with (OUT/f'{name}.{kind}.log').open('x') as stream: stream.write(text)
    save(f'{name}.command.json', {'argv':argv,'cwd':str(cwd),'startedAt':started,'endedAt':datetime.now(timezone.utc).isoformat(),'rc':result.returncode,'scope':'Read-only local checks; no CI, product service or business request'})
    return result
def git_source(name):
    return subprocess.run(['git','show',f'{BASE}:{name}'],cwd=ROOT,capture_output=True,check=True).stdout

manifest_path=E/'f03/remote-run-39027107897/supplement-source-manifest.json'
assert sha(manifest_path)==EXPECTED_MANIFEST
manifest=json.loads(manifest_path.read_text())
before={row['path']:sha(ROOT/row['path']) for row in manifest['files']}
assert all(before[row['path']]==row['sha256'] and (ROOT/row['path']).stat().st_size==row['bytes'] for row in manifest['files'])
save('source-before.json',{'manifestSha256':EXPECTED_MANIFEST,'files':before})
ci_raw=(ROOT/'.github/workflows/ci.yml').read_bytes()
base_ci=yaml.safe_load(git_source('.github/workflows/ci.yml'))
ci=yaml.safe_load(ci_raw)
jobs=ci['jobs']; checks={}
checks['exactThreeJobs']=set(jobs)=={'backend-ci','frontend-ci','critical-e2e'}
checks['allJobsIndependentAndBlocking']=all(not any(key in job for key in ('needs','if','continue-on-error','strategy')) for job in jobs.values())
checks['criticalAndFrontendAllStepsBlocking']=all(not step.get('continue-on-error',False) for key in ('critical-e2e','frontend-ci') for step in jobs[key]['steps'])
exceptions=[(key,step['name']) for key,job in jobs.items() for step in job['steps'] if step.get('continue-on-error')]
old_exceptions=[(key,step['name']) for key,job in base_ci['jobs'].items() for step in job['steps'] if step.get('continue-on-error')]
checks['onlyUnchangedMypyException']=exceptions==old_exceptions==[('backend-ci','Mypy type check')]
checks['threadsExplicitlyOneBothJobs']=all(jobs[job]['env'].get(key)=='1' for job in ('backend-ci','critical-e2e') for key in THREADS)
checks['criticalOwnThreeServices']=set(jobs['critical-e2e']['services'])=={'postgres','redis','tdengine'}
checks['backendOwnPGRedis']=set(jobs['backend-ci']['services'])=={'postgres','redis'}
critical_steps={step['name']:step for step in jobs['critical-e2e']['steps']}
required={
'Sync backend dependencies','Apply bootstrap SQL + stamp','Initialize disposable TDengine database',
'Isolate seeded external services before real application lifespan','Start backend',
'Install frontend dependencies','Install E2E dependencies from its own lockfile',
'Validate fail-closed gate contract (no product DB writes)','Build frontend','Start frontend preview',
'Prepare isolated synthetic inputs (no KPI result seeding)','Install Playwright browsers','Run critical E2E gate (blocking)',
'Preserve reports, raw exits, task/result attachments and input manifest','Cleanup'}
checks['criticalAllIndependentPrerequisites']=required.issubset(critical_steps)
checks['criticalRealModeAndExclusiveDB']=jobs['critical-e2e']['env']['CLPM_TEST_MODE']=='0' and jobs['critical-e2e']['env']['POSTGRES_DB']=='clpm_critical_e2e' and jobs['critical-e2e']['env']['TDENGINE_DB']=='clpm_critical_e2e_ts'
checks['criticalAlwaysArtifactsAndMissingError']=critical_steps['Preserve reports, raw exits, task/result attachments and input manifest']['if']=='always()' and critical_steps['Preserve reports, raw exits, task/result attachments and input manifest']['with']['if-no-files-found']=='error'
checks['criticalFailingGateCommandUnchanged']=critical_steps['Run critical E2E gate (blocking)']['run']=='bash scripts/run-critical-e2e.sh'
restored=copy.deepcopy(ci)
for key in ('backend-ci','critical-e2e'):
    for variable in THREADS:del restored['jobs'][key]['env'][variable]
    if not restored['jobs'][key]['env']:del restored['jobs'][key]['env']
    diagnostics=[step for step in restored['jobs'][key]['steps'] if step['name']=='Record fixed numeric resource profile']
    assert len(diagnostics)==1
    restored['jobs'][key]['steps']=[step for step in restored['jobs'][key]['steps'] if step['name']!='Record fixed numeric resource profile']
restored['jobs']['critical-e2e']['needs']=base_ci['jobs']['critical-e2e']['needs']
checks['onlyAuthorizedWorkflowSemanticChanges']=restored==base_ci
base_package=json.loads(git_source('frontend/apps/web-antd/package.json'))
package=json.loads((ROOT/'frontend/apps/web-antd/package.json').read_text())
checks['explicitCatalogPlaywrightDependency']=package['devDependencies']['@playwright/test']=='catalog:'
without=copy.deepcopy(package); del without['devDependencies']['@playwright/test']
checks['noOtherPackageChange']=without==base_package
lock=yaml.safe_load((ROOT/'frontend/pnpm-lock.yaml').read_text())
base_lock=yaml.safe_load(git_source('frontend/pnpm-lock.yaml'))
entry=lock['importers']['apps/web-antd']['devDependencies']['@playwright/test']
checks['lockedVersion160']=entry=={'specifier':'catalog:','version':'1.60.0'}
without_lock=copy.deepcopy(lock);del without_lock['importers']['apps/web-antd']['devDependencies']['@playwright/test']
checks['noOtherLockChange']=without_lock==base_lock
checks['existingResolutionUnchanged']=lock['packages']['@playwright/test@1.60.0']==base_lock['packages']['@playwright/test@1.60.0'] and lock['snapshots']['@playwright/test@1.60.0']==base_lock['snapshots']['@playwright/test@1.60.0']
unchanged=[row['path'] for row in manifest['files'] if row['path'] not in manifest['modifiedPaths']]
unchanged+=['backend/tests/test_annotated_loops_evaluation.py']
checks['allGateBusinessTimeoutAlgorithmInputsUnchanged']=all((ROOT/name).read_bytes()==git_source(name) for name in unchanged)
toml=(ROOT/'backend/pyproject.toml').read_text()
checks['timeout30AndCoverage60']='timeout = 30' in toml and jobs['backend-ci']['steps'][-1]['run']=='uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=60'
cases=json.loads((ROOT/'e2e/critical-cases.json').read_text())
save('critical-cases-identity.json',cases)
tool=json.loads((E/'f03/workflow-diagnosis-39026319367/tool-location.json').read_text())
assert sha(tool['binary'])=='8db11704dc296f096216db4db65d86cd7f0ebfdf4c38453a1da276b137b88388'
lint=run('actionlint',[tool['binary'],'-shellcheck=','-pyflakes=',str(ROOT/'.github/workflows/ci.yml')])
checks['independentActionlintRC0']=lint.returncode==0
diag_results={}
with tempfile.TemporaryDirectory(prefix='clpm-independent-f03-diagnostic-') as temporary:
    env=os.environ.copy(); env.update({key:'1' for key in THREADS});env['E2E_CRITICAL_FIXTURE']=temporary+'/critical-e2e/fixture.json'
    Path(env['E2E_CRITICAL_FIXTURE']).parent.mkdir()
    for job in ('backend-ci','critical-e2e'):
        step=next(step for step in jobs[job]['steps'] if step['name']=='Record fixed numeric resource profile')
        command=step['run'].replace('uv run python',shlex.quote(PYTHON),1)
        result=run('numeric-diagnostic-'+job,['bash','--noprofile','--norc','-e','-o','pipefail','-c',command],cwd=ROOT/'backend',env=env)
        try:data=json.loads(result.stdout)
        except ValueError:data={}
        diag_results[job]={'rc':result.returncode,'data':data,'interpreterOnlySubstitution':PYTHON}
    generated=Path(env['E2E_CRITICAL_FIXTURE']).with_name('numeric-runtime.json')
    checks['diagnosticRealExecutionsAndArtifact']=all(result['rc']==0 and result['data']['threads']=={key:'1' for key in THREADS} for result in diag_results.values()) and json.loads(generated.read_text())==diag_results['critical-e2e']['data']
save('numeric-diagnostics.json',diag_results)
node=shutil.which('node'); pnpm=shutil.which('pnpm')
assert node and pnpm
resolution=run('installed-playwright',[node,'-e',"console.log(JSON.stringify({version:require('@playwright/test/package.json').version,path:require.resolve('@playwright/test/package.json')}))"],cwd=ROOT/'frontend/apps/web-antd')
resolved=json.loads(resolution.stdout)
checks['installedAppPlaywright160']=resolution.returncode==0 and resolved['version']=='1.60.0' and str(ROOT/'frontend') in resolved['path']
list_env=os.environ.copy();list_env['CI']='true'
listed=run('smoke-collection',[pnpm,'exec','playwright','test','--list','--reporter=json'],cwd=ROOT/'frontend/apps/web-antd',env=list_env)
collection=json.loads(listed.stdout)
def titles(value):
    if isinstance(value,dict):
        if 'tests' in value and 'title' in value:yield value['title']
        for v in value.values():yield from titles(v)
    elif isinstance(value,list):
        for v in value:yield from titles(v)
found=list(titles(collection));save('smoke-collected-titles.json',found)
checks['originalTwoSmokeCollectedNotExecuted']=listed.returncode==0 and found==['应用应能正常启动并访问登录页','登录页应渲染基本 UI 元素']
after={row['path']:sha(ROOT/row['path']) for row in manifest['files']}
checks['sourceUnchangedDuringIndependentCheck']=before==after
save('source-after.json',{'files':after,'sameAsBefore':before==after})
save('result.json',{'localSupplementVerdict':'PASS' if all(checks.values()) else 'REWORK','checks':checks,'existingNonBlockingException':exceptions,'sourceManifestSha256':EXPECTED_MANIFEST,'actualLinuxCI':'NOT_EXECUTED','actualSmokeBusinessCases':'NOT_EXECUTED','independentNumericKPI':'NOT_EXECUTED','boundary':'Source/CI structure + diagnostics + collection only; not overall F03 acceptance'})
print(json.dumps({'allPass':all(checks.values()),'checks':checks},ensure_ascii=False))
raise SystemExit(0 if all(checks.values()) else 1)
