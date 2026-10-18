from pathlib import Path
import copy,hashlib,json,os,subprocess
import yaml
root=Path("/Users/zhangping/DEV/clpm-remediation-20261017")
e=Path(__file__).parent
old=yaml.safe_load((e/"original-workflow.yml").read_text());new=yaml.safe_load((root/".github/workflows/ci.yml").read_text())
job=new["jobs"]["critical-e2e"]
names=["Capture actual input policy before the critical gate","Capture actual input policy and dataset row after the critical gate"]
steps={s["name"]:s for s in job["steps"] if s.get("name") in names}
assert len(steps)==2 and steps[names[1]]["if"]=="always()"
rest=copy.deepcopy(new);j=rest["jobs"]["critical-e2e"];j["steps"]=[s for s in j["steps"] if s.get("name") not in names];del j["env"]["E2E_EXECUTION_ENV"];del j["env"]["REDIS_DB"]
assert rest==old
results=[]
for phase,unit,capture,expected in [("before",0,0,0),("before",1,0,1),("before",0,3,3),("after",0,0,0),("after",0,2,2)]:
 name=f"{phase}-unit{unit}-capture{capture}";out=e/"shell-controls"/name;out.mkdir(parents=True)
 workspace=out/"workspace";exe=workspace/"backend/.venv/bin/python";exe.parent.mkdir(parents=True)
 exe.write_text("#!/bin/bash\nif [[ \"$1\" == *test_critical_input_evidence.py ]]; then echo controlled-unit; exit \"$UNIT_CONTROL_RC\"; fi\necho controlled-capture\necho controlled-stderr >&2\nexit \"$CAPTURE_CONTROL_RC\"\n".replace('\"','"'));exe.chmod(0o755)
 script=steps[names[0] if phase=="before" else names[1]]["run"];(out/"executed.sh").write_text(script)
 env={**os.environ,"GITHUB_WORKSPACE":str(workspace),"RUNNER_TEMP":str(out/"runner-temp"),"UNIT_CONTROL_RC":str(unit),"CAPTURE_CONTROL_RC":str(capture)}
 r=subprocess.run(["/bin/bash","--noprofile","--norc","-e","-o","pipefail",str(out/"executed.sh")],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 (out/"stdout.log").write_bytes(r.stdout);(out/"stderr.log").write_bytes(r.stderr)
 record={"phase":phase,"unitRc":unit,"captureRc":capture,"rc":r.returncode,"expected":expected,"scope":"real Bash control using own fake python executable; no product/services/DB"};(out/"result.json").write_text(json.dumps(record,indent=2)+"\n");assert r.returncode==expected
 results.append(record)
summary={"workflowOtherSemanticsIdentical":True,"originalGateAndBackendAndUploaderUnchanged":True,"shellCases":results,"allPass":True}
(e/"workflow-and-shell-result.json").write_text(json.dumps(summary,indent=2)+"\n");print(json.dumps(summary))
