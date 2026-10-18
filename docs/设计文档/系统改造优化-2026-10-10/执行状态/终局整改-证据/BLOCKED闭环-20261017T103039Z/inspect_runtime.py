import os,json,sys,time,subprocess
from pathlib import Path
from dotenv import dotenv_values
runtime=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
root=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
cfg=dotenv_values(runtime/"backend/.env")
for k,v in cfg.items():
 if v is not None:os.environ[k]=v
os.environ["CLPM_TEST_MODE"]="1";os.environ["CLPM_SKIP_EXIT_HOOKS"]="1"
sys.path.insert(0,str(runtime/"backend"))
from app.tasks.celery_app import celery_app
i=celery_app.control.inspect(timeout=4)
facts={"observed_at":time.time(),"mode":"read-only celery inspect","runtime":str(runtime),"active":i.active(),"reserved":i.reserved(),"scheduled":i.scheduled(),"stats":i.stats()}
p=root/"runtime";p.mkdir(exist_ok=True);(p/"pre-deploy-celery.json").write_text(json.dumps(facts,indent=2,default=str)+"\n")
print(json.dumps({"active":facts["active"],"reserved":facts["reserved"],"scheduled":facts["scheduled"]},default=str))

