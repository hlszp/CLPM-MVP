from pathlib import Path
import concurrent.futures,subprocess,os,datetime,json,hashlib
C=Path('/Users/zhangping/DEV/clpm-remediation-20261017')
D=Path(__file__).resolve().parent
BIN='/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/'
CHECKS={
 'isolation':([BIN+'python','-B','e2e/scripts/test_critical_fixture.py','-v'],C,{}),
 'type':(['npm','run','typecheck'],C/'e2e',{}),
 'gate-contract':(['node','--test','scripts/test-critical-gate.mjs'],C/'e2e',{'GATE_CONTRACT_ARTIFACT_DIR':str(D/'gate-contract-artifacts')}),
 'collect':(['node_modules/.bin/playwright','test','--config=playwright.critical.config.ts','--list','--reporter=json'],C/'e2e',{}),
 'ruff':([BIN+'ruff','check','e2e/scripts/prepare-critical-fixture.py','e2e/scripts/test_critical_fixture.py'],C,{}),
 'format-check':([BIN+'ruff','format','--check','e2e/scripts/prepare-critical-fixture.py','e2e/scripts/test_critical_fixture.py'],C,{})
}
def run(name):
 argv,cwd,extra=CHECKS[name];started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 p=subprocess.run(argv,cwd=cwd,env={**os.environ,**extra},capture_output=True)
 for typ,b in [('stdout',p.stdout),('stderr',p.stderr)]:(D/f'{name}.{typ}.log').write_bytes(b)
 meta={'argv':argv,'cwd':str(cwd),'envOverrides':extra,'startedAt':started,'rc':p.returncode,'stdoutSha256':hashlib.sha256(p.stdout).hexdigest(),'stderrSha256':hashlib.sha256(p.stderr).hexdigest()}
 (D/f'{name}.command.json').write_text(json.dumps(meta,indent=2)+'\n')
 return {'name':name,'rc':p.returncode,'stdoutTail':p.stdout.decode(errors='replace')[-1400:],'stderrTail':p.stderr.decode(errors='replace')[-1800:]}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(run,CHECKS))
(D/'check-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(results,ensure_ascii=False,indent=2));raise SystemExit(any(r['rc'] for r in results))
