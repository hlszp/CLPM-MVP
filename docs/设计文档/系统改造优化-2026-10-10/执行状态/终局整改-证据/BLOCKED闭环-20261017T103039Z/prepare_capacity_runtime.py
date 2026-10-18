from __future__ import annotations
import asyncio,hashlib,json,os,shutil,sys,time
from pathlib import Path
from dotenv import dotenv_values,set_key
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");RUNTIME=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
DB="clpm_capacity_20261017_s01"
private=Path("/Users/zhangping/DEV/CLPM-evidence-archive/2026-10-17-final-audit/runtime-env-backups/BLOCKED-20261017T103039Z")
envpath=RUNTIME/"backend/.env"
updates={"ENV":"development","POSTGRES_DB":DB,"REDIS_HOST":"127.0.0.1","REDIS_PORT":"17123","REDIS_PASSWORD":"","CELERY_BROKER_URL":"","CELERY_RESULT_BACKEND":"","TDENGINE_DB":DB,"SIGNALR_ENABLED":"false","REALTIME_WRITEBACK_ENABLED":"false","GAP_BACKFILL_ENABLED":"false","CLPM_TEST_MODE":"0","CLPM_G5_TRACE":"1","CLPM_BUILD_CODE_SHA":"148dd21ac8450cd85d763f89a557adf68fd29fdb","CLPM_CALC_SNAPSHOT_DIR":str(ROOT/"f02/series-s01/input-packages")}
for key,value in updates.items():set_key(str(envpath),key,value,quote_mode="auto")
envpath.chmod(0o600)
(ROOT/"f02/series-s01/input-packages").mkdir(parents=True,exist_ok=True)
files=[]
for p in (RUNTIME/"backend/logs").glob("celerybeat-schedule*"):
 dest=private/p.name;assert not dest.exists();files.append({"from":str(p),"to":str(dest),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()});shutil.move(p,dest);dest.chmod(0o600)
(ROOT/"runtime/runtime-env-capacity.json").write_text(json.dumps({"at":time.time(),"env_path":str(envpath),"env_sha256":hashlib.sha256(envpath.read_bytes()).hexdigest(),"nonsecret_overrides":updates,"beat_schedule_backups":files,"original_env_backup":str(private/"runtime.backend.env"),"old_business_database_preserved":"clpm"},indent=2)+"\n")
for k,v in dotenv_values(envpath).items():
 if v is not None:os.environ[k]=v
os.environ["CLPM_SKIP_EXIT_HOOKS"]="1"
sys.path.insert(0,str(RUNTIME/"backend"))
from app.services.data_source.point_history_repository import ensure_schema
asyncio.run(ensure_schema())
print(json.dumps({"runtime_env_configured":True,"database":DB,"td_schema_created":True,"beat_schedule_backups":len(files)}))

