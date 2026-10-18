import hashlib,json,os,subprocess,time
from pathlib import Path
ROOT=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
SOURCE=Path("/Users/zhangping/DEV/clpm-remediation-20261017")
base="docs/设计文档/系统改造优化-2026-10-10/执行状态/"
groups=[
("docs(audit): preserve prior independent receipts",["终局整改-非作者复验回执.md","终局整改-整改后复验报告.md","终局整改-直接整改执行.md","终局整改-BLOCKED闭环执行.md"]),
("docs(audit): separate waiver and execution states",["P6-任务状态/STATE.md","P7汇总与G7结论.md","任务看板.md","终局整改-独立复验报告.md"]),
("docs(audit): retain runtime and residual boundaries",["终局整改台账.md","终局整改-运行来源台账.md","终局整改-遗留索引.md"]),
]
env=os.environ.copy();env.update(UV_PYTHON="3.12",CLPM_TEST_MODE="1",CLPM_SKIP_EXIT_HOOKS="1",LEFTHOOK_BIN=str(SOURCE/"frontend/node_modules/.bin/lefthook"))
results=[]
for idx,(subject,names) in enumerate(groups,15):
 assert not subprocess.check_output(["git","diff","--cached","--name-only"],cwd=SOURCE).strip()
 subprocess.run(["git","add","--",*[base+n for n in names]],cwd=SOURCE,check=True)
 stat=subprocess.check_output(["git","diff","--cached","--numstat"],cwd=SOURCE,text=True);lines=sum(int(v) for line in stat.splitlines() for v in line.split("\t")[:2])
 assert 0<lines<=500,(subject,lines)
 cmd=["git","commit","-m",subject,"-m","Preserve original audit findings and dated receipts. Keep live F02/F03 acceptance blocked until real evidence and independent verification exist."]
 start=time.time();p=subprocess.run(cmd,cwd=SOURCE,env=env,capture_output=True,text=True)
 prefix=ROOT/"git"/f"{idx:02}-audit-docs"
 prefix.with_suffix(".stdout.log").write_text(p.stdout);prefix.with_suffix(".stderr.log").write_text(p.stderr)
 item={"command":cmd,"exit_code":p.returncode,"elapsed_seconds":time.time()-start,"changed_lines":lines,"numstat":stat,"stdout_sha256":hashlib.sha256(p.stdout.encode()).hexdigest(),"stderr_sha256":hashlib.sha256(p.stderr.encode()).hexdigest()}
 if p.returncode==0:item["commit"]=subprocess.check_output(["git","rev-parse","HEAD"],cwd=SOURCE,text=True).strip()
 prefix.with_suffix(".json").write_text(json.dumps(item,indent=2)+"\n");results.append(item)
 print(json.dumps({"subject":subject,"changed_lines":lines,"exit_code":p.returncode}),flush=True)
 assert p.returncode==0,p.stdout+p.stderr
(ROOT/"git/docs-initial-commits.json").write_text(json.dumps(results,indent=2)+"\n")

