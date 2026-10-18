"""Keeper exact frozen input-only SQL; no metrics/results or database mutations."""
from datetime import datetime,timezone
import hashlib,json,os,subprocess,sys
from pathlib import Path
from uuid import UUID
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parent
RUN=json.loads((ROOT/'local-decimal-fix-root-run.json').read_text())
OUT=Path(RUN['out']);SOURCE=Path('/Users/zhangping/DEV/clpm-remediation-20261017')
SQL=ROOT.parent/'independent/f03-input-only-pre-gate.sql'
assert len(sys.argv)==2 and sys.argv[1] in ('before','after')
DEST=OUT/('input-policy-'+sys.argv[1]);DEST.mkdir(exist_ok=False)
assert hashlib.sha256(SQL.read_bytes()).hexdigest()=='d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294'
fixture=json.loads((OUT/'fixture.json').read_text());loop=str(UUID(fixture['loopId']))
assert fixture['candidateSha']==RUN['candidateSha']
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()==RUN['candidateSha']
assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=SOURCE).strip()
state=json.loads((OUT/'current-f03-local-api.json').read_text());assert state['startupReadinessVerified'] is True and len(state['processesAtStart'])==7
cfg=dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
env=os.environ.copy();env.update(PGPASSWORD=cfg['POSTGRES_PASSWORD'],LC_ALL='C')
argv=['docker','exec','-i','-e','PGPASSWORD','clpm-mvp-postgres','psql','-h','127.0.0.1','-p','5432','-U',cfg['POSTGRES_USER'],'-d','clpm_critical_20261017_local','-X','-A','-t','-v','ON_ERROR_STOP=1','-v','loop_id='+loop,'-f','-']
started=datetime.now(timezone.utc).isoformat();result=subprocess.run(argv,input=SQL.read_bytes(),capture_output=True,env=env)
(DEST/'stdout.log').write_bytes(result.stdout);(DEST/'stderr.log').write_bytes(result.stderr);(DEST/'executed.sql').write_bytes(SQL.read_bytes())
record={'argv':argv,'startedAt':started,'finishedAt':datetime.now(timezone.utc).isoformat(),'rc':result.returncode,'passwordNotInArgumentsOrArtifacts':True,'sqlSha256':hashlib.sha256(SQL.read_bytes()).hexdigest(),'stdoutSha256':hashlib.sha256(result.stdout).hexdigest(),'stderrSha256':hashlib.sha256(result.stderr).hexdigest(),'actualSourceSha':RUN['candidateSha'],'loopId':loop}
(DEST/'command.json').write_text(json.dumps(record,indent=2)+'\n');assert result.returncode==0
rows=[json.loads(line) for line in result.stdout.splitlines() if line.startswith(b'{')]
before=next(x for x in rows if x['section']=='transaction-before');after=next(x for x in rows if x['section']=='transaction-after')
assert before['database']==after['database']=='clpm_critical_20261017_local'
assert before['readOnly']==after['readOnly']=='on' and before['isolation']==after['isolation']=='repeatable read'
assert before['snapshot']==after['snapshot'] and before['transactionTimestampUtc']==after['transactionTimestampUtc']
assert before['timezone']==after['timezone']=='UTC'
held=[x for x in rows if x['section']=='held-policy'];assert len(held)==2
pids=[p['pid'] for p in state['processesAtStart']]
psargv=['ps','-p',','.join(map(str,pids)),'-o','pid=,ppid=,lstart=,stat=,command='];native=subprocess.run(psargv,capture_output=True,env=env)
(DEST/'native-processes.stdout.log').write_bytes(native.stdout);(DEST/'native-processes.stderr.log').write_bytes(native.stderr)
assert native.returncode==0 and {int(line.split()[0]) for line in native.stdout.splitlines() if line.strip()}==set(pids)
summary={'at':datetime.now(timezone.utc).isoformat(),'phase':sys.argv[1],'inputOnly':True,'actualSourceSha':RUN['candidateSha'],'backendTree':subprocess.check_output(['git','rev-parse',RUN['candidateSha']+':backend'],cwd=SOURCE,text=True).strip(),'sourceCatalogSha256':hashlib.sha256((OUT/'source-tracked-catalog.json').read_bytes()).hexdigest(),'actualDatabase':before['database'],'heldPolicyRows':held,'keeperHeldOrAlgorithmPolicyWritesSinceLifespan':0,'nativeProcessCommand':psargv,'nativeProcessRc':native.returncode,'nativeProcessRawSha256':hashlib.sha256(native.stdout).hexdigest(),'nativeTimezoneRaw':subprocess.check_output(['date','+%Y-%m-%dT%H:%M:%S%z %Z'],text=True).strip(),'startupStateSha256':hashlib.sha256((OUT/'current-f03-local-api.json').read_bytes()).hexdigest(),'commandSha256':hashlib.sha256((DEST/'command.json').read_bytes()).hexdigest()}
(DEST/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');(DEST/'executed-source.py').write_bytes(Path(__file__).read_bytes());print(json.dumps({'rc':0,'phase':sys.argv[1],'database':before['database'],'rows':len(rows),'heldPolicyRows':held,'out':str(DEST)},ensure_ascii=False))
