"""Finalize raw capture and counts only; no critical KPI value inspection."""
from pathlib import Path
from datetime import datetime,UTC
import hashlib,json,re
D=Path(__file__).resolve().parent
M="bc48701efe90e2a27555a4f8794f0965e82b0902"
H="8fc850802612b6077f3b07c08324e3694945aa0d"
def load(p):return json.loads(p.read_text())
def ref(p):return {"path":str(p.relative_to(D)),"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()}
def write(p,data):
 assert not p.exists(),p
 p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
c=load(D/"capture-final.json");controller=load(D/"controller-resume-command.json")
assert controller["rc"]==0 and c["collectorCompleted"] is True
assert c["progress"]["run"]["head_sha"]==H and c["progress"]["run"]["status"]=="completed"
jobs=load(D/c["progress"]["rawSnapshotRefs"]["jobs"]["path"])["jobs"]
assert len(jobs)==3 and all(x["status"]=="completed" for x in jobs)
assert len(c["jobLogs"])==3
for x in c["jobLogs"].values():assert len(x["checkouts"])==1 and x["checkouts"][0]["sha"]==M
identity=c["actualCheckoutGitObjects"][M];commit=load(D/identity["gitCommit"]["path"])
assert H in [x["sha"] for x in commit["parents"]]
artifacts=[]
for x in c["artifacts"].values():
 assert x["githubDigestMatchesArchive"] is True
 for entry in x["uncompressedFiles"]:assert ref(D/entry["path"])==entry
 artifacts.append({"id":x["githubOriginal"]["id"],"name":x["githubOriginal"]["name"],"officialDigest":x["githubOriginal"]["digest"],"archive":x["archive"],"digestMatches":True,"files":len(x["uncompressedFiles"])})
summaryLines={}
for jid,x in c["jobLogs"].items():
 if "Critical" in x["job"]:continue
 chosen=[]
 for n,line in enumerate((D/x["raw"]["path"]).read_text(errors="replace").splitlines(),1):
  plain=re.sub(r"\x1b\[[0-9;]*m","",line)
  if (re.search(r"=+ .*\b(?:passed|failed|skipped)\b.*\bin [0-9.]+s",plain)
      or re.search(r"\b[0-9]+ items? / [0-9]+ deselected / [0-9]+ selected",plain)
      or re.search(r"\bTest Files\s+[0-9]+ passed",plain)
      or re.search(r"\bTests\s+[0-9]+ passed",plain)
      or re.search(r"^.*[0-9]+ passed \([0-9.]+[sm]\)$",plain)):
   chosen.append({"line":n,"text":plain})
 summaryLines[x["job"]]={"source":x["raw"],"summaryLines":chosen}
rcs={}
for f in (D/"requests").glob("*.command.json"):
 q=load(f);key=str(q["processReturncode"]);rcs[key]=rcs.get(key,0)+1
summary={"capturedAtUtc":datetime.now(UTC).isoformat(),"runId":39044390911,"headSha":H,"actualCheckoutSha":M,"actualCheckoutProof":{jid:x["checkouts"] for jid,x in c["jobLogs"].items()},"actualCommitParents":[x["sha"] for x in commit["parents"]],"actualRootTreeSha":identity["rootTreeSha"],"actualBackendTreeSha":identity["backendTreeSha"],"run":c["progress"]["run"],"jobs":c["progress"]["jobs"],"artifacts":artifacts,"summaries":summaryLines,"criticalCountsAndInputPresence":ref(D/"critical-counts-and-input-presence.json"),"actualKpiValues":"NOT_READ_OR_PROJECTED; independent expected-first numeric acceptance separately required","controller":ref(D/"controller-resume-command.json"),"initialController":ref(D/"controller-command.json"),"initialControllerRc":1,"GETReturncodeCounts":rcs,"fullRunLogsArchive":c["fullRunLogs"]["archive"],"rawCollectorManifestTiming":"First collector exited rc1 on GET failure-path PosixPath metadata serialization; preserved unchanged. Resumed GET collector rc0. Final evidence-manifest is sealed after both wrappers complete.","scope":"author GET-only original acquisition; not independent or overall F03 acceptance"}
write(D/"final-run-summary.json",summary)
print(json.dumps({"summary":ref(D/"final-run-summary.json"),"run":summary["run"],"jobs":summary["jobs"],"artifacts":artifacts,"GETReturncodeCounts":rcs,"actualKpiValues":"NOT_READ"},ensure_ascii=False))
