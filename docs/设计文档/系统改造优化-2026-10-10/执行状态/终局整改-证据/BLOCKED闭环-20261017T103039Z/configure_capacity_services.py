from __future__ import annotations
import asyncio,hashlib,json,os,sys,time
from pathlib import Path
from dotenv import dotenv_values
import asyncpg,httpx
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z");SOURCE=Path("/Users/zhangping/DEV/clpm-remediation-20261017");RUNTIME=Path("/Users/zhangping/DEV/clpm-g5-runtime-20261015")
DB="clpm_capacity_20261017_s01"
cfg=dotenv_values(RUNTIME/"backend/.env")
for k,v in cfg.items():
 if v is not None:os.environ[k]=v
os.environ.update(ENV="development",POSTGRES_DB=DB,REDIS_HOST="127.0.0.1",REDIS_PORT="17123",REDIS_PASSWORD="",TDENGINE_DB=DB,CLPM_TEST_MODE="1",CLPM_SKIP_EXIT_HOOKS="1")
sys.path.insert(0,str(SOURCE/"backend"));sys.path.insert(0,str(SOURCE/"e2e/scripts"))
from app.core.config import settings
from app.services.data_source.point_history_repository import ensure_schema
# Constants are data only. The CI execution/write guard is not bypassed.
import importlib.util
sp=importlib.util.spec_from_file_location("ci_service_values",SOURCE/"e2e/scripts/prepare-critical-fixture.py")
mod=importlib.util.module_from_spec(sp);sp.loader.exec_module(mod)
values=mod.CI_SERVICE_CONFIG
async def main():
 out=ROOT/"runtime";out.mkdir(exist_ok=True)
 conn=await asyncpg.connect(host=settings.POSTGRES_HOST,port=settings.POSTGRES_PORT,user=settings.POSTGRES_USER,password=settings.POSTGRES_PASSWORD,database=DB)
 try:
  assert await conn.fetchval("SELECT current_database()")==DB
  assert await conn.fetchval("SELECT count(*) FROM loop_ledger")==0
  before=await conn.fetch("SELECT key,value,updated_at FROM sys_config ORDER BY key")
  async with conn.transaction():
   await conn.executemany("INSERT INTO sys_config(key,value,description,updated_by,updated_at) VALUES($1,$2,'isolated capacity service configuration','closure-keeper',NOW()) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,updated_by=EXCLUDED.updated_by,updated_at=EXCLUDED.updated_at",list(values.items()))
  after=await conn.fetch("SELECT key,value,updated_at FROM sys_config ORDER BY key")
  def digest(rows):return [{"key":r["key"],"value_sha256":hashlib.sha256(r["value"].encode()).hexdigest(),"updated_at":str(r["updated_at"])} for r in rows]
  config={"database":DB,"before":digest(before),"after":digest(after),"applied_service_only":values,"algorithm_parameters_changed":False}
  (out/"capacity-service-config.json").write_text(json.dumps(config,indent=2)+"\n")
 finally:await conn.close()
 rest=f"http://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT+11}/rest/sql"
 async with httpx.AsyncClient(auth=(settings.TDENGINE_USER,settings.TDENGINE_PASSWORD),timeout=30) as client:
  records=[]
  for sql in ["SHOW DNODE 1 VARIABLES","ALTER DNODE 1 'minReservedMemorySize' '1024'","SHOW DNODE 1 VARIABLES"]:
   resp=await client.post(rest,content=sql);resp.raise_for_status();body=resp.json()
   records.append({"sql":sql,"http_status":resp.status_code,"response":body})
   (out/"td-resource-change.json").write_text(json.dumps({"at":time.time(),"target_container":"clpm-mvp-tdengine","original":0,"frozen_MB":1024,"rollback_sql":"ALTER DNODE 1 'minReservedMemorySize' '0'","records":records},indent=2)+"\n")
   assert body["code"]==0,body
  variable_rows={r[1]:r[2] for r in records[-1]["response"]["data"]}
  assert variable_rows["minReservedMemorySize"]=="1024",variable_rows.get("minReservedMemorySize")
 await ensure_schema()
 async with httpx.AsyncClient(auth=(settings.TDENGINE_USER,settings.TDENGINE_PASSWORD),timeout=30) as client:
  sql=f"SHOW CREATE DATABASE {DB}";resp=await client.post(rest,content=sql);resp.raise_for_status();body=resp.json();assert body["code"]==0,body
  (out/"capacity-td-schema.json").write_text(json.dumps({"database":DB,"ensure_schema_source_sha256":hashlib.sha256((SOURCE/"backend/app/services/data_source/point_history_repository.py").read_bytes()).hexdigest(),"sql":sql,"response":body},indent=2)+"\n")
 print(json.dumps({"database":DB,"service_configuration":"isolated external endpoints disabled","td_reserved_MB":1024,"schema_ready":True}))
asyncio.run(main())

