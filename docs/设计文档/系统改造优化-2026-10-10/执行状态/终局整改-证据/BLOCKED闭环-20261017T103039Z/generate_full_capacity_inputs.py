from __future__ import annotations
from datetime import datetime,timedelta,timezone
import hashlib,json,os,shutil,subprocess,time
from pathlib import Path
from dotenv import dotenv_values
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");RUNTIME=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
OUT=ROOT/"f02/series-s01";OUT.mkdir(exist_ok=True)
(OUT/"input-manifests").mkdir(exist_ok=True);(OUT/"generation").mkdir(exist_ok=True)
sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=RUNTIME,text=True).strip()
generator=RUNTIME/"backend/scripts/generate_capacity_dataset.py";generator_sha=hashlib.sha256(generator.read_bytes()).hexdigest()
start=datetime(2026,10,15,12)
plan=[]
for i in range(20):
 scenario="a" if i<10 else "b";cache="cold" if i%10<5 else "hot";ts=start+timedelta(hours=i)
 plan.append({"batch":i+1,"scenario":scenario,"cache":cache,"ts_start":ts.isoformat(),"ts_end":(ts+timedelta(hours=1)).isoformat(),"manifest_name":f"manifest_{scenario}_{ts:%Y%m%d%H}.json","required":True,"slo_seconds":600,"expected_loops":1000})
planpath=OUT/"planned-batches.json";assert not planpath.exists();planpath.write_text(json.dumps({"series":"s01","candidate_at_input_preparation":sha,"generator_sha256":generator_sha,"batches":plan,"four_user_waivers_unchanged":True},indent=2)+"\n")
env=os.environ.copy();env.update({k:v for k,v in dotenv_values(RUNTIME/"backend/.env").items() if v is not None})
env.update(NO_PROXY="127.0.0.1,localhost,::1",CLPM_TEST_MODE="0",CLPM_SKIP_EXIT_HOOKS="1",PYTHONUNBUFFERED="1")
assert env["POSTGRES_DB"]==env["TDENGINE_DB"]=="clpm_capacity_20261017_s01"
results=[]
for item in plan:
 label=f"{item['batch']:02d}-{item['scenario']}-{item['cache']}"
 cmd=[str(RUNTIME/"backend/.venv/bin/python"),"-u","scripts/generate_capacity_dataset.py","--execute","--scenario",item["scenario"],"--ts-start",item["ts_start"],"--base-url","http://127.0.0.1:17101/api/v1"]
 stdout=OUT/"generation"/(label+".stdout.log");stderr=OUT/"generation"/(label+".stderr.log")
 print(json.dumps({"preparing_input_batch":item["batch"],"scenario":item["scenario"],"ts_start":item["ts_start"]}),flush=True)
 begin=time.time()
 with stdout.open("wb") as out,stderr.open("wb") as err:
  child=subprocess.Popen(cmd,cwd=RUNTIME/"backend",env=env,stdout=out,stderr=err)
  (OUT/"input-generation-progress.json").write_text(json.dumps({"at":time.time(),"completed":len(results),"current_batch":item["batch"],"child_pid":child.pid,"stdout":str(stdout),"stderr":str(stderr)},indent=2)+"\n")
  rc=child.wait()
 record={"batch":item,"command":cmd,"cwd":str(RUNTIME/"backend"),"candidate_sha":sha,"generator_sha256":generator_sha,"started_at":begin,"finished_at":time.time(),"exit_code":rc,"stdout":str(stdout),"stderr":str(stderr),"stdout_sha256":hashlib.sha256(stdout.read_bytes()).hexdigest(),"stderr_sha256":hashlib.sha256(stderr.read_bytes()).hexdigest()}
 if rc==0:
  original=RUNTIME/"backend/capacity_dataset"/item["manifest_name"];dest=OUT/"input-manifests"/item["manifest_name"];shutil.copy2(original,dest)
  record.update(manifest=str(dest),manifest_sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
 (OUT/"generation"/(label+".json")).write_text(json.dumps(record,indent=2)+"\n")
 results.append(record);(OUT/"input-generation-results.json").write_text(json.dumps(results,indent=2)+"\n")
 print(json.dumps({"prepared_batch":item["batch"],"exit_code":rc,"elapsed_seconds":record["finished_at"]-begin}),flush=True)
 assert rc==0,f"input generation failed batch {item['batch']}; preserve logs"
assert len(results)==20
(OUT/"input-generation-complete.json").write_text(json.dumps({"at":time.time(),"prepared":20,"formal_capacity_executed":0,"plan_sha256":hashlib.sha256(planpath.read_bytes()).hexdigest(),"generator_sha256":generator_sha},indent=2)+"\n")

