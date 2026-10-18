import hashlib,json,os,signal,subprocess,time
from pathlib import Path
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");RUNTIME=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015");PROJECT=Path("/Users/zhangping/DEV/CLPM-MVP")
out=ROOT/"runtime"
history=json.loads((out/"drain-old-consumer-v2.json").read_text())
assert history[-1]["active"] and all(not tasks for tasks in history[-1]["active"].values()),"worker not drained"
pid=57294
cmd=subprocess.check_output(["ps","-p",str(pid),"-o","command="],text=True).strip()
assert str(RUNTIME/"backend") in cmd and "uvicorn app.main:app" in cmd,cmd
before={"at":time.time(),"api_pid":pid,"command":cmd,"drained":True}
os.kill(pid,signal.SIGTERM)
deadline=time.monotonic()+60
while time.monotonic()<deadline:
 live=[]
 for p in (57294,57324,57326):
  r=subprocess.run(["ps","-p",str(p),"-o","stat="],capture_output=True,text=True)
  if r.returncode==0 and not r.stdout.strip().startswith("Z"):live.append(p)
 if not live:break
 time.sleep(1)
record={"before":before,"finished_at":time.time(),"remaining_expected_api_beat_master":live,"old_queue_purged":False,"forced_kill":False}
(out/"old-lifespan-stop.json").write_text(json.dumps(record,indent=2)+"\n");print(json.dumps(record),flush=True)
assert not live,live
cfg=PROJECT/"deploy/docker/tdengine/taos.cfg"
old=cfg.read_bytes()
backup=out/"taos-config-original.cfg";assert not backup.exists();backup.write_bytes(old)
new=old+b"\n# Temporary F02 isolated capacity resource profile, restore after acceptance.\nqueryUseMemoryPool 0\nminReservedMemorySize 1024\nforceReadConfig 1\n"
cfg.write_bytes(new)
(out/"taos-profile-change.json").write_text(json.dumps({"target":str(cfg),"temporary_uncommitted_runtime_profile":True,"original_sha256":hashlib.sha256(old).hexdigest(),"new_sha256":hashlib.sha256(new).hexdigest(),"backup":str(backup),"queryUseMemoryPool":0,"minReservedMemorySize_MB":1024,"forceReadConfig":1,"queryBufferSize_MB":4096,"production_touched":False,"rollback":"restore exact original bytes then controlled restart and SHOW verification"},indent=2)+"\n")
print("TARGET_DEV_TD_PROFILE_WRITTEN",flush=True)

