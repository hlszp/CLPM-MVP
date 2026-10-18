"""Project gate status and INPUT evidence only; never load KPI/screenshot values."""
from pathlib import Path
from decimal import Decimal
from datetime import datetime,UTC
import hashlib,json
D=Path(__file__).resolve().parent
A=D/"artifacts/12105297313/home/runner/work/_temp/critical-e2e"
M="bc48701efe90e2a27555a4f8794f0965e82b0902"
def load(p):return json.loads(p.read_text(),parse_float=Decimal)
def ref(p):return {"path":str(p.relative_to(D)),"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
def lines(p):return [json.loads(x,parse_float=Decimal) for x in p.read_text().splitlines() if x.startswith("{")]
fixture=load(A/"fixture.json")
assert fixture["candidateSha"]==M and fixture["synthetic"] is True and fixture["inputOnly"] is True
facts={"observedAtUtc":datetime.now(UTC).isoformat(),"actualKpiValues":"NOT_READ_OR_PROJECTED; input-only policy/row and status counts", "fixture":ref(A/"fixture.json"),"phase":{}}
content=[]
expected={"critical-input-policy.sql":"d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294","critical-input-dataset-row.sql":"9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff"}
for phase in ("before","after"):
 p=A/"input-evidence"/phase;h=load(p/"header.json");assert h["complete"] is True and h["actualCheckoutSha"]==M
 for name,digest in expected.items():assert ref(p/name)["sha256"]==digest
 records=[]
 for f in sorted(p.glob("*/command.json")):
  c=load(f);assert c["rc"]==0
  for n in ("stdout","stderr"):assert ref(f.parent/(n+".log"))["sha256"]==c[n+"Sha256"]
  records.append({"command":ref(f),"rc":c["rc"]})
 raw=lines(p/"policy/stdout.log");edges=[r for r in raw if r.get("section","").startswith("transaction-")]
 assert len(edges)==2
 for r in edges:assert r["database"]=="clpm_critical_e2e" and r["readOnly"]=="on" and r["isolation"]=="repeatable read" and r["timezone"]=="UTC"
 assert edges[0]["snapshot"]==edges[1]["snapshot"]
 content.append([r for r in raw if not r.get("section","").startswith("transaction-")])
 launch=A/"input-evidence"/(phase+"-launch.exit-code.txt");assert launch.read_text().strip()=="0"
 facts["phase"][phase]={"header":ref(p/"header.json"),"complete":h["complete"],"actualCheckoutSha":h["actualCheckoutSha"],"policy":ref(p/"policy/stdout.log"),"readOnlyUtcActualTransactions":True,"sqlHashesMatchFrozen":True,"commands":records,"launchRc":0,"launchExit":ref(launch)}
assert content[0]==content[1]
facts["inputPolicyBeforeAfterSemanticallyEqual"]=True
rowraw=A/"input-evidence/after/dataset-row/stdout.log";rows=[r for r in lines(rowraw) if "id" in r];assert len(rows)==1
r=rows[0];h=load(A/"input-evidence/after/header.json")
assert r["loop_id"]==fixture["loopId"] and r["kind"]=="KPI_GRID" and r["id"]==h["package"]["snapshotId"] and r["input_hash"]==h["package"]["inputHash"]
files=[]
for entry in h["package"]["files"]:
 f=A/entry["path"];v=ref(f);assert v["sha256"]==entry["sha256"] and v["bytes"]==entry["bytes"];files.append(v)
assert len(files)==2 and any(x["path"].endswith(".npz") for x in files) and any(x["path"].endswith(".manifest.json") for x in files)
facts["inputPackage"]={"datasetRow":ref(rowraw),"rowCount":1,"snapshotId":r["id"],"inputHash":r["input_hash"],"files":files,"rowHeaderBindingMatches":True,"npzArraysOrKpiMetricsLoaded":False}
for name in ("collection-summary.json","execution-summary.json"):
 g=load(A/"gate"/name)
 result={k:g[k] for k in ("mode","passed","exitCode","required","collected","stats")};result["source"]=ref(A/"gate"/name)
 if name.startswith("execution"):
  result["rows"]=[{"id":r["id"],"status":r["status"],"attempts":[{k:x.get(k) for k in ("status","retry")} for x in r["results"]]} for r in g["rows"]]
 facts[name]=result
out=D/"critical-counts-and-input-presence.json";assert not out.exists();out.write_text(json.dumps(facts,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"file":ref(out),"beforeComplete":True,"afterComplete":True,"policyEqual":True,"inputRows":1,"npzManifestPresent":True,"execution":facts["execution-summary.json"],"actualKpiValues":"NOT_READ"},ensure_ascii=False))
