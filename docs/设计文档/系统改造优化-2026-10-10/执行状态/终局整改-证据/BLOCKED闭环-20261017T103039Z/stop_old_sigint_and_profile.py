import hashlib,json,os,signal,subprocess,time
from pathlib import Path
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");PROJECT=Path("/Users/zhangping/DEV/CLPM-MVP");RUNTIME=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
out=ROOT/"runtime"
cmd=subprocess.check_output(["ps","-p","57294","-o","command="],text=True).strip()
assert str(RUNTIME/"backend") in cmd and "uvicorn app.main:app" in cmd
os.kill(57294,signal.SIGINT)
deadline=time.monotonic()+45
while True:
 live=[]
 for pid in (57294,57324,57326):
  p=subprocess.run(["ps","-p",str(pid),"-o","stat="],capture_output=True,text=True)
  if p.returncode==0 and not p.stdout.strip().startswith("Z"):live.append(pid)
 if not live or time.monotonic()>=deadline:break
 time.sleep(1)
r={"at":time.time(),"reason":"SIGTERM grace exceeded 60 seconds; uvicorn waiting for background tasks after websocket connections closed","escalation":"SIGINT to exact old API PID only","remaining":live,"api_sigkill":False,"original_queue_purged":False,"preparation_td_restart_before_profile":True,"formal_capacity_started":False}
(out/"old-lifespan-sigint.json").write_text(json.dumps(r,indent=2)+"\n");print(json.dumps(r),flush=True)
assert not live,live
cfg=PROJECT/"deploy/docker/tdengine/taos.cfg";old=cfg.read_bytes();backup=out/"taos-config-original.cfg";assert not backup.exists();backup.write_bytes(old)
new=old+b"\n# Temporary F02 isolated capacity resource profile; restore after acceptance.\nqueryUseMemoryPool 0\nminReservedMemorySize 1024\nforceReadConfig 1\n"
cfg.write_bytes(new)
(out/"taos-profile-change.json").write_text(json.dumps({"target":str(cfg),"temporary_uncommitted_runtime_profile":True,"original_sha256":hashlib.sha256(old).hexdigest(),"new_sha256":hashlib.sha256(new).hexdigest(),"backup":str(backup),"queryUseMemoryPool":0,"minReservedMemorySize_MB":1024,"forceReadConfig":1,"queryBufferSize_MB":4096,"production_touched":False,"rollback":"restore exact original bytes then controlled restart and verify variables"},indent=2)+"\n")
print("TARGET_DEV_TD_PROFILE_WRITTEN",flush=True)

