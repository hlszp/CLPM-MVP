from __future__ import annotations
import hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
from dotenv import dotenv_values,set_key
import httpx
from capacity_keeper_guards import private_broker_snapshot
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");RUNTIME=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
label=sys.argv[1];out=ROOT/"runtime"/label;out.mkdir(exist_ok=False)
source=RUNTIME/"backend"
sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=RUNTIME,text=True).strip()
assert not subprocess.check_output(["git","diff","--name-only","HEAD","--","backend"],cwd=RUNTIME).strip()

env=os.environ.copy();env.update({k:v for k,v in dotenv_values(source/".env").items() if v is not None})
env.update(CLPM_TEST_MODE="0",CLPM_SKIP_EXIT_HOOKS="0",CLPM_BUILD_CODE_SHA=sha,CLPM_G5_TRACE="0",NO_PROXY="127.0.0.1,localhost,::1")
env.update(OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1",MKL_NUM_THREADS="1",NUMEXPR_NUM_THREADS="1",VECLIB_MAXIMUM_THREADS="1")
assert env["POSTGRES_DB"]=="clpm_capacity_20261017_s01";assert env["REDIS_PORT"]=="17123"
assert env["TDENGINE_DB"]==env["POSTGRES_DB"]
assert env["POSTGRES_HOST"] in ("localhost","127.0.0.1") and env["POSTGRES_PORT"]=="17102"
assert env["REDIS_HOST"] in ("localhost","127.0.0.1")
assert env["TDENGINE_HOST"] in ("localhost","127.0.0.1") and env["TDENGINE_PORT"]=="17104"
broker_before_start=private_broker_snapshot()
(out/"broker-before-start.json").write_text(json.dumps(broker_before_start,indent=2)+"\n")
assert broker_before_start['quiescent'], "Pending private messages retained; refusing startup"
sock=httpx.Client(timeout=1,trust_env=False)
try:sock.get("http://127.0.0.1:17101/health")
except httpx.ConnectError:pass
else:raise RuntimeError("API port already responds")
log=out/"api.log"
cmd=[str(source/".venv/bin/python"),"-m","uvicorn","app.main:app","--host","127.0.0.1","--port","17101","--timeout-graceful-shutdown","15","--no-access-log"]
fh=log.open("wb")
process=subprocess.Popen(cmd,cwd=source,env=env,stdout=fh,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
fh.close()
record={"label":label,"started_at":time.time(),"api_pid":process.pid,"command":cmd,"cwd":str(source),"candidate_sha":sha,"backend_tree":subprocess.check_output(["git","rev-parse",sha+":backend"],cwd=RUNTIME,text=True).strip(),"env_sha256":hashlib.sha256((source/".env").read_bytes()).hexdigest(),"postgres_db":env["POSTGRES_DB"],"tdengine_db":env["TDENGINE_DB"],"redis_port":env["REDIS_PORT"],"beat_paused_for_capacity":False,"log":str(log)}
(out/"start.json").write_text(json.dumps(record,indent=2)+"\n")
record["controlledRuntimeEnvironment"]={key:env[key] for key in ("CLPM_TEST_MODE","CLPM_SKIP_EXIT_HOOKS","CLPM_BUILD_CODE_SHA","CLPM_G5_TRACE","OPENBLAS_NUM_THREADS","OMP_NUM_THREADS","MKL_NUM_THREADS","NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS")}
print(json.dumps({"api_pid":process.pid,"sha":sha}),flush=True)
deadline=time.monotonic()+120;beat=None;health=None
while time.monotonic()<deadline:
 assert process.poll() is None, "new API exited, inspect its exact log"
 pf=source/"logs/celerybeat.pid"
 if beat is None and pf.exists():
  try:
   value=int(pf.read_text().strip())
   p=subprocess.run(["ps","-p",str(value),"-o","ppid=,command="],capture_output=True,text=True)
   parts=p.stdout.strip().split(maxsplit=1)
   if p.returncode==0 and int(parts[0])==process.pid and "-m celery" in parts[1] and " beat " in parts[1]:
    os.kill(value,signal.SIGSTOP);beat=value;record.update(beat_pid=beat,beat_paused_for_capacity=True,beat_paused_at=time.time());print("OWN_BEAT_PAUSED "+str(beat),flush=True)
  except (ValueError,ProcessLookupError):pass
 try:
  r=sock.get("http://127.0.0.1:17101/health")
  if r.status_code==200 and beat is not None:
   health=r.json();break
 except httpx.HTTPError:pass
 time.sleep(0.2)
assert health is not None,"API readiness timeout"
record["health"]=health;record["ready_at"]=time.time()
record["processes"]=subprocess.check_output(["ps","-axo","pid,ppid,lstart,stat,command"],text=True).splitlines()
record["processes"]=[line for line in record["processes"] if str(source) in line or str(process.pid) in line.split()[:2]]
(out/"start.json").write_text(json.dumps(record,indent=2)+"\n")
(ROOT/"runtime/current-capacity-api.json").write_text(json.dumps({"start_record":str(out/"start.json"),"api_pid":process.pid,"beat_pid":beat,"sha":sha},indent=2)+"\n")
print(json.dumps({"ready":True,"api_pid":process.pid,"beat_pid":beat,"candidate_sha":sha,"postgres_db":env["POSTGRES_DB"]}),flush=True)

