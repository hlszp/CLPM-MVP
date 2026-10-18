"""Keeper exact input-package SQL; no result metrics are selected."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
import hashlib,json,os,subprocess,sys
from dotenv import dotenv_values
R=Path(__file__).resolve().parent
run=json.loads(Path(sys.argv[1]).read_text());out=Path(run["out"]);snapshot=str(UUID(sys.argv[2]));fixture=json.loads((out/'fixture.json').read_text());loop=str(UUID(fixture['loopId']))
assert fixture['candidateSha']==run['candidateSha']
assert len(list((out/'calc-snapshots').rglob(snapshot+'.manifest.json')))==1 and len(list((out/'calc-snapshots').rglob(snapshot+'.npz')))==1
sql=R.parent/'independent/f03-input-only-dataset-row.sql';assert hashlib.sha256(sql.read_bytes()).hexdigest()=='9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff'
d=out/'input-package-row';d.mkdir(exist_ok=False)
cfg=dotenv_values('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env');env=os.environ.copy();env['PGPASSWORD']=cfg['POSTGRES_PASSWORD']
a=['docker','exec','-i','-e','PGPASSWORD','clpm-mvp-postgres','psql','-h','127.0.0.1','-p','5432','-U',cfg['POSTGRES_USER'],'-d','clpm_critical_20261017_local','-X','-A','-t','-v','ON_ERROR_STOP=1','-v','loop_id='+loop,'-v','snapshot_id='+snapshot,'-f','-'];began=datetime.now(timezone.utc).isoformat();p=subprocess.run(a,input=sql.read_bytes(),capture_output=True,env=env)
(d/'stdout.log').write_bytes(p.stdout);(d/'stderr.log').write_bytes(p.stderr);(d/'executed.sql').write_bytes(sql.read_bytes());(d/'executed-source.py').write_bytes(Path(__file__).read_bytes())
r={'argv':a,'startedAt':began,'finishedAt':datetime.now(timezone.utc).isoformat(),'rc':p.returncode,'sqlSha256':hashlib.sha256(sql.read_bytes()).hexdigest(),'stdoutSha256':hashlib.sha256(p.stdout).hexdigest(),'stderrSha256':hashlib.sha256(p.stderr).hexdigest(),'inputOnly':True,'snapshotId':snapshot,'loopId':loop,'sourceSha':run['candidateSha'],'passwordNotInArgumentsOrArtifacts':True};(d/'command.json').write_text(json.dumps(r,indent=2)+'\n');assert p.returncode==0
rows=[json.loads(x) for x in p.stdout.splitlines() if x.startswith(b'{')];assert len(rows)==2;header,row=rows;assert header['database']=='clpm_critical_20261017_local' and header['readOnly']=='on' and header['isolation']=='repeatable read' and header['timezone']=='UTC';assert row['id']==snapshot and row['loop_id']==loop
print(json.dumps({'rc':0,'inputSnapshotRows':1,'snapshotId':snapshot,'loopId':loop,'out':str(d)}))
