import hashlib,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
SOURCE=Path("/Users/zhangping/DEV/clpm-remediation-20261017")
manifest=json.loads((ROOT/"f03/phase1-source-manifest.json").read_text())
for item in manifest["files"]:
 assert hashlib.sha256((SOURCE/item["path"]).read_bytes()).hexdigest()==item["sha256"],item["path"]
groups=[
("test(e2e): enforce exact critical case results",["e2e/package.json","e2e/tsconfig.json","e2e/critical-cases.json","e2e/playwright.critical.config.ts","e2e/scripts/validate-critical-results.mjs","e2e/scripts/test-critical-gate.mjs","scripts/run-critical-e2e.sh"]),
("test(e2e): guard real isolated fixture inputs",["e2e/scripts/prepare-critical-fixture.py","e2e/scripts/test_critical_fixture.py"]),
("ci(e2e): run authenticated business chain",[".github/workflows/ci.yml","e2e/tests/critical-business.spec.ts"]),
]
env=os.environ.copy();env.update(UV_PYTHON="3.12",CLPM_TEST_MODE="1",CLPM_SKIP_EXIT_HOOKS="1",LEFTHOOK_BIN=str(SOURCE/"frontend/node_modules/.bin/lefthook"))
results=[]
for idx,(subject,paths) in enumerate(groups,12):
 assert not subprocess.check_output(["git","diff","--cached","--name-only"],cwd=SOURCE).strip()
 subprocess.run(["git","add","--",*paths],cwd=SOURCE,check=True)
 stat=subprocess.check_output(["git","diff","--cached","--numstat"],cwd=SOURCE,text=True)
 lines=sum(int(v) for line in stat.splitlines() for v in line.split("\t")[:2]);assert 0<lines<=500,(subject,lines)
 cmd=["git","commit","-m",subject,"-m","Execute the four required cases against isolated real services; preserve failures and full input evidence."]
 started=time.time();p=subprocess.run(cmd,cwd=SOURCE,env=env,capture_output=True,text=True)
 prefix=ROOT/"git"/f"{idx:02}-critical"
 (prefix.with_suffix(".stdout.log")).write_text(p.stdout);(prefix.with_suffix(".stderr.log")).write_text(p.stderr)
 item={"command":cmd,"elapsed_seconds":time.time()-started,"exit_code":p.returncode,"subject":subject,"changed_lines":lines,"numstat":stat,"stdout_sha256":hashlib.sha256(p.stdout.encode()).hexdigest(),"stderr_sha256":hashlib.sha256(p.stderr.encode()).hexdigest()}
 if p.returncode==0:item["commit"]=subprocess.check_output(["git","rev-parse","HEAD"],cwd=SOURCE,text=True).strip()
 prefix.with_suffix(".json").write_text(json.dumps(item,indent=2)+"\n");results.append(item)
 print(json.dumps({k:item[k] for k in ("subject","changed_lines","exit_code")}),flush=True)
 assert p.returncode==0,p.stdout+p.stderr
(ROOT/"git/f03-phase1-commits.json").write_text(json.dumps(results,indent=2)+"\n")

