from pathlib import Path
import hashlib,json,datetime,subprocess,os,time
E=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");R=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
out=E/'runtime/preformal-bc48701e-start-root-command';out.mkdir(exist_ok=False)
helper=E/'start_capacity_api_formal.py'
assert hashlib.sha256(helper.read_bytes()).hexdigest()=='50662e8a4b1a9025f4f96d15081aa43902481cc363f56d98b5c70b60261f7e23'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()=='bc48701efe90e2a27555a4f8794f0965e82b0902'
assert hashlib.sha256((R/'backend/.env').read_bytes()).hexdigest()=='27d6fc10cc68376b056d1c7803f1b6361e48c11bc3de46c4e8a46cc4b2abe04b'
argv=['ps','-ax','-o','pid=,ppid=,command='];p=subprocess.run(argv,capture_output=True)
(out/'process-preflight.stdout.log').write_bytes(p.stdout);(out/'process-preflight.stderr.log').write_bytes(p.stderr)
assert p.returncode==0
matching=[line for line in p.stdout.decode().splitlines() if str(R/'backend/.venv/bin/python') in line and ('-m celery' in line or '-m uvicorn app.main:app' in line)]
assert not matching,matching
pointer=E/'runtime/current-capacity-api.json'
if pointer.exists():(out/'previous-current-capacity-api.json').write_bytes(pointer.read_bytes())
command=[str(R/'backend/.venv/bin/python'),'-B',str(helper),'preformal-bc48701e-ready']
env=os.environ.copy();env['LC_ALL']='C'
record={'argv':command,'cwd':str(R/'backend'),'startedAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'purpose':'preformal source/config/native identity preparation only; formal0/20; pending full CI and independent admission','sourceSha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'preflightPsRc':p.returncode,'preflightNoRuntimeApiOrCelery':True,'preflightPsSha256':hashlib.sha256(p.stdout).hexdigest()}
(out/'command-before.json').write_text(json.dumps(record,indent=2)+'\n')
start=time.monotonic()
with (out/'stdout.log').open('xb') as stdout,(out/'stderr.log').open('xb') as stderr:p=subprocess.run(command,cwd=R/'backend',env=env,stdout=stdout,stderr=stderr)
record.update(finishedAtUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),rc=p.returncode,durationSeconds=time.monotonic()-start)
for stream in ('stdout','stderr'):record[stream+'Sha256']=hashlib.sha256((out/(stream+'.log')).read_bytes()).hexdigest()
(out/'command.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:record[k] for k in ('rc','durationSeconds','startedAtUtc','finishedAtUtc')}))
print((out/'stdout.log').read_text()[-1500:])
raise SystemExit(p.returncode)

