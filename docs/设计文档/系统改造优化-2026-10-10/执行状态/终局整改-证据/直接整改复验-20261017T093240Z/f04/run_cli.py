from pathlib import Path
import datetime,json,os,subprocess,sys,time
out=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z')/'f04/cli'
out.mkdir(parents=True,exist_ok=True)
name=sys.argv[1];args=sys.argv[2:]
assert name.replace('-','').replace('_','').isalnum()
cmd=['npx','--offline','--package','@playwright/cli','playwright-cli','--session=clpm-final-f04',*args]
start=time.monotonic()
r=subprocess.run(cmd,cwd='/Users/zhangping/DEV/clpm-remediation-20261017',capture_output=True,text=True,timeout=180)
record={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'command':cmd,'cwd':'/Users/zhangping/DEV/clpm-remediation-20261017','exit_code':r.returncode,'elapsed_seconds':time.monotonic()-start,'stdout':r.stdout,'stderr':r.stderr}
f=out/(name+'.json');assert not f.exists()
f.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print(r.stdout);print(r.stderr,file=sys.stderr);sys.exit(r.returncode)

