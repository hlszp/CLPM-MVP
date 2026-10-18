from pathlib import Path
import datetime,hashlib,json,subprocess,sys,time
E=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");R=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
out=E/'runtime/deploy-bc48701e-root';out.mkdir(exist_ok=False)
M='bc48701efe90e2a27555a4f8794f0965e82b0902';H='8fc850802612b6077f3b07c08324e3694945aa0d';B='86301e5e3083db0feb5f2d9db8fb9eacc8d3165d'
def run(name,argv,cwd=R,ok=(0,)):
 t=datetime.datetime.now(datetime.timezone.utc).isoformat();start=time.monotonic()
 with (out/(name+'.stdout.log')).open('xb') as stdout,(out/(name+'.stderr.log')).open('xb') as stderr:p=subprocess.run(argv,cwd=cwd,stdout=stdout,stderr=stderr,timeout=120)
 row={'argv':argv,'cwd':str(cwd),'startedAtUtc':t,'finishedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rc':p.returncode,'durationSeconds':time.monotonic()-start}
 for st in ('stdout','stderr'):row[st+'Sha256']=hashlib.sha256((out/(name+'.'+st+'.log')).read_bytes()).hexdigest()
 (out/(name+'.json')).write_text(json.dumps(row,indent=2)+'\n')
 print(json.dumps({'step':name,'rc':p.returncode,'durationSeconds':row['durationSeconds']}),flush=True)
 assert p.returncode in ok,name
 return (out/(name+'.stdout.log')).read_text()
s=run('no-live-api',['lsof','-nP','-iTCP:17101','-iTCP:17261','-iTCP:17266','-sTCP:LISTEN'],ok=(1,))
assert not s
run('tracked-clean',['git','diff','--quiet','HEAD'])
run('fetch-pr-merge',['git','fetch','github','refs/pull/1/merge'])
actual=run('fetch-head',['git','rev-parse','FETCH_HEAD']).strip();assert actual==M,actual
parents=run('merge-parents',['git','show','-s','--format=%P',M]).strip().split();assert parents==[B,H],parents
run('checkout',['git','checkout','--detach',M])
run('locked-sync',['uv','sync','--frozen'],cwd=R/'backend')
run('deployed-head',['git','rev-parse','HEAD'])
run('deployed-status',['git','status','--short'])
run('tracked-clean-after',['git','diff','--quiet','HEAD'])
run('full-source-catalog',[str(R/'backend/.venv/bin/python'),'-B',str(E/'capture_candidate_source_catalog.py'),'--candidate-sha',M,'--out',str(out/'source-tracked-catalog.json')])
record={'finishedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runtime':str(R),'head':H,'actualRuntimeSha':M,'prBase':B,'prMetadataMergeVerifiedParents':True,'actualCiJobCheckoutProofStillPending':True,'productProcessesStarted':False,'productionTouched':False,'formalCapacityExecuted':0}
(out/'deployment-summary.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))

