from __future__ import annotations
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
SOURCE=Path("/Users/zhangping/DEV/clpm-remediation-20261017")
DB="clpm_audit_gate_20261017"
cfg=json.loads(subprocess.check_output(["docker","inspect","--format","{{json .Config.Env}}","clpm-mvp-postgres"],text=True))
values=dict(item.split("=",1) for item in cfg if "=" in item)
env=os.environ.copy()
env.update(ENV="development",POSTGRES_HOST="127.0.0.1",POSTGRES_PORT="17102",POSTGRES_USER=values["POSTGRES_USER"],POSTGRES_PASSWORD=values["POSTGRES_PASSWORD"],POSTGRES_DB=DB,REDIS_HOST="127.0.0.1",REDIS_PORT="9",REDIS_PASSWORD="",TDENGINE_HOST="127.0.0.1",TDENGINE_PORT="9",TDENGINE_PASSWORD="",UV_PYTHON="3.12",CLPM_TEST_MODE="1",CLPM_SKIP_EXIT_HOOKS="1",PYTHONPATH=str(SOURCE/"backend"),LEFTHOOK_BIN=str(SOURCE/"frontend/node_modules/.bin/lefthook"))
label=sys.argv[1];command=sys.argv[2:]
out=ROOT/"git";out.mkdir(exist_ok=True)
start=time.time()
p=subprocess.run(command,cwd=SOURCE,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
data=p.stdout
secret=values["POSTGRES_PASSWORD"]
if secret:data=data.replace(secret,"[REDACTED]")
log=out/(label+".log");log.write_text(data)
record={"command":command,"cwd":str(SOURCE),"exit_code":p.returncode,"elapsed_seconds":round(time.time()-start,3),"database":DB,"postgres_container":"clpm-mvp-postgres","shared_business_database_touched":False,"redis_and_tdengine":"disabled for isolated unit gates","log":str(log),"sha256":hashlib.sha256(log.read_bytes()).hexdigest()}
(out/(label+".json")).write_text(json.dumps(record,indent=2)+"\n")
print(json.dumps(record),flush=True)
print(data[-4000:],flush=True)
raise SystemExit(p.returncode)

