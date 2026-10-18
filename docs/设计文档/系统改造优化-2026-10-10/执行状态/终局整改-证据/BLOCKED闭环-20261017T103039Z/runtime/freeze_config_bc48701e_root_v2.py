from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,subprocess,time
import httpx
E=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");R=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
out=E/'runtime/config-freeze-bc48701e-root-command-v2';out.mkdir(exist_ok=False)
helper=E/'f02/freeze_capacity_configuration_v2.py'
assert hashlib.sha256(helper.read_bytes()).hexdigest()=='2688732523d3f9300c9d11e2dbf9194248a7be195c18cbfa313f5bbf020e66e5'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()=='bc48701efe90e2a27555a4f8794f0965e82b0902'
auth_source=E/'runtime/config-freeze-bc48701e-root-command/authentication.json'
auth=json.loads(auth_source.read_text())
response_path=Path(auth['privateResponsePath'])
assert response_path.stat().st_mode & 0o077==0
assert hashlib.sha256(response_path.read_bytes()).hexdigest()==auth['responseSha256']
assert auth['httpStatus']==200
body=json.loads(response_path.read_text())
assert body.get('code') in (0,'0')
private=response_path.parent
reference={'originalAuthenticationPath':str(auth_source),'originalAuthenticationSha256':hashlib.sha256(auth_source.read_bytes()).hexdigest(),'privateResponseSha256':auth['responseSha256'],'originalHttpStatus':auth['httpStatus'],'apiCode':body['code'],'apiCodeType':type(body['code']).__name__,'reusedOriginalSuccessfulResponse':True,'newLoginIssued':False,'reason':'The original wrapper incorrectly required integer0; the actual API success envelope code is string0.'}
(out/'authentication-reference.json').write_text(json.dumps(reference,indent=2)+'\n')
token=body['data']['accessToken'];token_path=private/'bearer.private.txt'
with token_path.open('x') as f:os.chmod(token_path,0o600);f.write(token)
assert token_path.stat().st_mode & 0o077==0
argv=[str(R/'backend/.venv/bin/python'),'-B',str(helper),'--input-audit-progress',str(E/'f02/input-series-audit-v2/progress.json'),'--planned-batches',str(E/'f02/series-s01/planned-batches.json'),'--env-file',str(R/'backend/.env'),'--runtime',str(R),'--candidate-sha','bc48701efe90e2a27555a4f8794f0965e82b0902','--api-base','http://127.0.0.1:17101/api/v1','--token-file',str(token_path),'--out',str(E/'f02/series-s01/config-freeze-bc48701e')]
record={'argv':argv,'cwd':str(R/'backend'),'startedAtUtc':datetime.now(timezone.utc).isoformat(),'helperSha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'nativeEnvSha256':hashlib.sha256((R/'backend/.env').read_bytes()).hexdigest(),'formalCapacityExecuted':0}
(out/'command-before.json').write_text(json.dumps(record,indent=2)+'\n')
start=time.monotonic()
with (out/'stdout.log').open('xb') as stdout,(out/'stderr.log').open('xb') as stderr:proc=subprocess.run(argv,cwd=R/'backend',stdout=stdout,stderr=stderr)
record.update(finishedAtUtc=datetime.now(timezone.utc).isoformat(),rc=proc.returncode,durationSeconds=time.monotonic()-start)
for stream in ('stdout','stderr'):record[stream+'Sha256']=hashlib.sha256((out/(stream+'.log')).read_bytes()).hexdigest()
(out/'command.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({k:record[k] for k in ('rc','durationSeconds','startedAtUtc','finishedAtUtc')}))
print((out/'stdout.log').read_text()[-1800:])
raise SystemExit(proc.returncode)

