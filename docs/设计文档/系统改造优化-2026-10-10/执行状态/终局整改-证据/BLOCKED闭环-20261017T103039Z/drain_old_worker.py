from __future__ import annotations
import json,os,sys,time
from pathlib import Path
from dotenv import dotenv_values
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
RUNTIME=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
for k,v in dotenv_values(RUNTIME/"backend/.env").items():
 if v is not None:os.environ[k]=v
os.environ.update(CLPM_TEST_MODE="1",CLPM_SKIP_EXIT_HOOKS="1")
sys.path.insert(0,str(RUNTIME/"backend"))
from app.tasks.celery_app import celery_app
host="clpm-g5-runtime-20261015@zhangpingdeMacBook-Pro"
p=ROOT/"runtime/drain-old-consumer.json"
history=[]
def save():p.write_text(json.dumps(history,indent=2,default=str)+"\n")
initial=celery_app.control.inspect(destination=[host],timeout=4).active()
assert set(initial or {})=={host},initial
assert all(t["name"]=="app.tasks.alert_patrol.run_alert_patrol" for t in initial[host]),initial
history.append({"at":time.time(),"initial_active":initial});save()
reply=celery_app.control.cancel_consumer("default",destination=[host],reply=True,timeout=4)
history.append({"at":time.time(),"cancel_consumer_default":reply});save();print(json.dumps(history[-1]),flush=True)
assert reply and any("ok" in r.get(host,{}) for r in reply),reply
deadline=time.monotonic()+180
while time.monotonic()<deadline:
 active=celery_app.control.inspect(destination=[host],timeout=4).active()
 history.append({"at":time.time(),"active":active});save()
 print(json.dumps({"at":time.time(),"active_count":len((active or {}).get(host,[]))}),flush=True)
 assert set(active or {})=={host},active
 if not active[host]:
  print("OLD_WORKER_DRAINED_NO_PURGE",flush=True);break
 time.sleep(5)
else:raise RuntimeError("old worker did not drain within 180 seconds")

