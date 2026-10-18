from pathlib import Path
import datetime,hashlib,json,os,subprocess,sys,time
out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
argv=sys.argv[2:];start=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
env={**os.environ,"CLPM_TEST_MODE":"1","CLPM_SKIP_EXIT_HOOKS":"1","NO_PROXY":"127.0.0.1,localhost,::1","no_proxy":"127.0.0.1,localhost,::1"}
with (out/"stdout.log").open("wb") as so,(out/"stderr.log").open("wb") as se:
 p=subprocess.run(argv,cwd="/Users/zhangping/DEV/clpm-remediation-20261017/backend",env=env,stdout=so,stderr=se)
record={"argv":argv,"cwd":"/Users/zhangping/DEV/clpm-remediation-20261017/backend","startedAtUtc":start,"elapsedSeconds":time.monotonic()-t,"rc":p.returncode,"envOverrides":{k:env[k] for k in ("CLPM_TEST_MODE","CLPM_SKIP_EXIT_HOOKS","NO_PROXY","no_proxy")}}
record["files"]=[{"path":x.name,"bytes":x.stat().st_size,"sha256":hashlib.sha256(x.read_bytes()).hexdigest()} for x in (out/"stdout.log",out/"stderr.log")]
(out/"command.json").write_text(json.dumps(record,indent=2)+"\n")
print(json.dumps(record));sys.exit(p.returncode)
