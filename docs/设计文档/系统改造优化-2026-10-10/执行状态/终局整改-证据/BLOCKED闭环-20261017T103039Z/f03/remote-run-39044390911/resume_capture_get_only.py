"""Resume GET acquisition from checkpoint; preserve first collector failure unchanged."""
import concurrent.futures,importlib.util,json,time
from pathlib import Path
D=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("original_capture",D/"capture_remote_readonly.py")
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
x=json.loads((D/"capture-checkpoint.json").read_text())
logs=x["logs"];commits=x["actualCheckouts"];artifacts={}
for ident in x["artifacts"]:artifacts[str(ident)]=json.loads((D/"artifacts"/(str(ident)+".metadata.json")).read_text())
start=time.monotonic()
while not c.STOP and time.monotonic()-start<2400:
 endpoints={"run":f"{c.REPO}/actions/runs/{c.RUN}","jobs":f"{c.REPO}/actions/runs/{c.RUN}/jobs?per_page=100","artifacts":f"{c.REPO}/actions/runs/{c.RUN}/artifacts?per_page=100"}
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  futures={k:pool.submit(c.capture,k,v) for k,v in endpoints.items()};got={k:f.result() for k,f in futures.items()}
 if not all(v["ok"] for v in got.values()):
  c.write_json(D/(c.stamp()+"-resume-incomplete.json"),{k:{"ok":v["ok"],"output":str(v["output"]),"command":v["command"]} for k,v in got.items()})
 else:
  run,jobs,art=(got[k]["data"] for k in ("run","jobs","artifacts"))
  assert run["id"]==c.RUN and run["run_attempt"]==1 and run["head_sha"]==c.EXPECTED_HEAD
  assert jobs["total_count"]==len(jobs["jobs"])==3 and art["total_count"]==len(art["artifacts"])
  progress={"observedAt":c.utc(),"run":{k:run.get(k) for k in ("id","head_sha","run_attempt","status","conclusion","event")},"jobs":[{"id":j["id"],"name":j["name"],"status":j["status"],"conclusion":j["conclusion"],"activeSteps":[{k:s.get(k) for k in ("number","name","status","conclusion")} for s in j["steps"] if s["status"]=="in_progress" or s["conclusion"]=="failure"]} for j in jobs["jobs"]],"availableArtifacts":[{"id":a["id"],"name":a["name"],"digest":a.get("digest")} for a in art["artifacts"]],"rawSnapshotRefs":{k:c.ref(v["output"]) for k,v in got.items()}}
  c.write_json(D/(c.stamp()+"-resume-snapshot.json"),progress)
  for job in jobs["jobs"]:
   if c.STOP:break
   ident=str(job["id"])
   if ident in logs or job["status"]!="completed" or job["conclusion"]=="skipped":continue
   result=c.capture("job-"+ident,f"{c.REPO}/actions/jobs/{ident}/logs",binary=True)
   if result["ok"]:
    target=D/"job-logs"/(ident+".log");assert not target.exists();target.parent.mkdir(exist_ok=True);target.write_bytes(result["data"])
    logs[ident]={"job":job["name"],"raw":c.ref(target),"checkouts":c.checkouts(target),"request":result["command"]}
  for a in art["artifacts"]:
   if c.STOP:break
   ident=str(a["id"])
   if ident in artifacts:continue
   result=c.capture("artifact-"+ident,f"{c.REPO}/actions/artifacts/{ident}/zip",binary=True)
   if result["ok"]:
    directory=D/"artifacts"/ident;archive=directory.with_suffix(".zip");assert not archive.exists();archive.parent.mkdir(exist_ok=True);archive.write_bytes(result["data"])
    entries=c.extract_archive(result["data"],directory)
    metadata={"githubOriginal":a,"archive":c.ref(archive),"githubDigestMatchesArchive":a.get("digest")=="sha256:"+c.sha(result["data"]),"uncompressedFiles":entries,"request":result["command"],"diagnosticOnly":a["name"].startswith("backend-g3-numerics-")}
    c.write_json(directory.with_suffix(".metadata.json"),metadata);artifacts[ident]=metadata
  for evidence in logs.values():
   for observation in evidence["checkouts"]:
    actual=observation["sha"]
    if actual in commits or c.STOP:continue
    commit=c.capture("actual-checkout-"+actual,f"{c.REPO}/git/commits/{actual}")
    if commit["ok"]:
     tree_sha=commit["data"]["tree"]["sha"];tree=c.capture("actual-root-tree-"+tree_sha,f"{c.REPO}/git/trees/{tree_sha}")
     if tree["ok"]:commits[actual]={"actualCheckoutEvidence":observation,"gitCommit":c.ref(commit["output"]),"gitRootTree":c.ref(tree["output"]),"rootTreeSha":tree_sha,"backendTreeSha":next((t["sha"] for t in tree["data"]["tree"] if t["path"]=="backend"),None)}
  expected={str(j["id"]) for j in jobs["jobs"] if j["status"]=="completed" and j["conclusion"]!="skipped"}
  complete=run["status"]=="completed" and all(j["status"]=="completed" for j in jobs["jobs"]) and expected==set(logs) and len(artifacts)==art["total_count"]
  c.write_json(D/"resume-checkpoint.json",{"progress":progress,"logs":logs,"artifacts":list(artifacts),"actualCheckouts":commits,"captureComplete":complete})
  if complete:
   all_logs=c.capture("full-run-logs",f"{c.REPO}/actions/runs/{c.RUN}/logs",binary=True)
   if all_logs["ok"]:
    archive=D/"full-run-logs.zip";assert not archive.exists();archive.write_bytes(all_logs["data"]);entries=c.extract_archive(all_logs["data"],D/"full-run-logs")
    final={"capturedAt":c.utc(),"progress":progress,"jobLogs":logs,"artifacts":artifacts,"actualCheckoutGitObjects":commits,"fullRunLogs":{"archive":c.ref(archive),"files":entries,"request":all_logs["command"]},"collectorCompleted":True,"initialCollectorExitCode":1,"initialFailure":"HTTP request failed; incomplete-snapshot serializer rejected PosixPath; original script/log preserved","testOrBusinessAcceptance":"NOT_INFERRED_FROM_COLLECTOR_RC"}
    c.write_json(D/"capture-final.json",final);print(json.dumps({"completed":True,"run":progress["run"],"jobs":progress["jobs"]},ensure_ascii=False),flush=True);raise SystemExit(0)
 until=time.monotonic()+60
 while not c.STOP and time.monotonic()<until:time.sleep(min(1,until-time.monotonic()))
c.write_json(D/"resume-interrupted.json",{"stopSignal":c.STOP,"observedAt":c.utc(),"collectorCompleted":False});raise SystemExit(2)
