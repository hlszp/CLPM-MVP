from __future__ import annotations
import hashlib,json,os,socket,subprocess,time
from pathlib import Path
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
PRIVATE=Path("/Users/zhangping/DEV/CLPM-evidence-archive/2026-10-17-final-audit/runtime-env-backups/BLOCKED-20261017T103039Z")
DB="clpm_capacity_20261017_s01"
OUT=ROOT/"runtime";OUT.mkdir(exist_ok=True)
def run(label,cmd,*,stdin=None,private=False):
 start=time.time()
 p=subprocess.run(cmd,input=stdin,capture_output=True)
 target=(PRIVATE if private else OUT)/(label+".stdout.log")
 target.write_bytes(p.stdout);target.chmod(0o600 if private else 0o644)
 err=OUT/(label+".stderr.log");err.write_bytes(p.stderr)
 r={"command":cmd,"exit_code":p.returncode,"elapsed_seconds":time.time()-start,"stdout":str(target),"stdout_sha256":hashlib.sha256(p.stdout).hexdigest(),"stderr":str(err),"stderr_sha256":hashlib.sha256(p.stderr).hexdigest(),"private_reference_data":private}
 (OUT/(label+".json")).write_text(json.dumps(r,indent=2)+"\n")
 print(json.dumps({"step":label,"exit_code":p.returncode,"bytes":len(p.stdout)}),flush=True)
 assert p.returncode==0,p.stderr.decode(errors="replace")
 return p.stdout
exists=subprocess.check_output(["docker","exec","clpm-mvp-postgres","psql","-U","clpm","-d","postgres","-X","-At","-c",f"SELECT datname FROM pg_database WHERE datname='{DB}'"],text=True).strip()
assert not exists,DB+" already exists"
white="alembic_version sys_user sys_permission sys_role_permission sys_authz_policy policy_publication_guard sys_config sys_dict_item module_plugin metric_config clpm_metric_data_requirement algorithm_parameter config_override config_publication mode_definition dcs_vendor dcs_model dcs_mode_mapping dcs_pid_structure loop_type_weight loop_level_weight diagnosis_config diagnosis_rule engine_rule sla_policy report_config engineering_template engineering_template_version".split()
schema=run("capacity-schema-dump",["docker","exec","clpm-mvp-postgres","pg_dump","-U","clpm","--schema-only","--no-owner","--no-privileges","clpm"])
refs=run("capacity-reference-dump",["docker","exec","clpm-mvp-postgres","pg_dump","-U","clpm","--data-only","--no-owner","--no-privileges",*[a for t in white for a in ["--table","public."+t]],"clpm"],private=True)
run("capacity-create-db",["docker","exec","clpm-mvp-postgres","psql","-U","clpm","-d","postgres","-X","-v","ON_ERROR_STOP=1","-c",f"CREATE DATABASE {DB} OWNER clpm"])
base=["docker","exec","-i","clpm-mvp-postgres","psql","-U","clpm","-d",DB,"-X","-v","ON_ERROR_STOP=1","--single-transaction"]
run("capacity-schema-import",base,stdin=schema)
run("capacity-reference-import",base,stdin=refs)
sql="INSERT INTO sys_user_scope(user_id,scope_type,node_id,updated_by) SELECT id,'ALL',NULL,'closure-keeper' FROM sys_user WHERE username='admin'; SELECT version_num FROM alembic_version; SELECT count(*) AS initial_loops FROM loop_ledger; SELECT count(*) AS initial_records FROM calculation_result_record; SELECT count(*) AS initial_latest FROM loop_confidence_latest;"
run("capacity-identity-initial",base,stdin=sql.encode())
sock=socket.socket();sock.bind(("127.0.0.1",17123));sock.close()
redis_name="clpm-f02-redis-20261017-s01"
run("capacity-create-redis",["docker","run","-d","--name",redis_name,"--label","com.clpm.audit=BLOCKED-20261017T103039Z","--memory","128m","--cpus","0.5","-p","127.0.0.1:17123:6379","redis:7-alpine","redis-server","--save","","--appendonly","no"])
run("capacity-redis-ready",["docker","exec",redis_name,"redis-cli","ping"])
(OUT/"capacity-namespace.json").write_text(json.dumps({"postgres_container":"clpm-mvp-postgres","database":DB,"copied_reference_tables":white,"count":len(white),"old_business_database_unchanged":"clpm","redis_container":redis_name,"redis_port":17123,"tdengine_database":DB,"initial_results":0,"new_namespace_created_at":time.time(),"production_touched":False},indent=2)+"\n")

