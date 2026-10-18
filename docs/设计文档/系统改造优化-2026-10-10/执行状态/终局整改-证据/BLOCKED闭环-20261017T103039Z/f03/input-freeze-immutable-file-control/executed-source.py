"""Root input-only evidence freeze; copies mutable phase files before hashing."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

run_file = Path(sys.argv[1]).resolve()
run = json.loads(run_file.read_text())
out = Path(run["out"])
source = Path("/Users/zhangping/DEV/clpm-remediation-20261017")
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
assert head == run["candidateSha"]
assert not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source).strip()
assert not (out / "gate").exists(), "input freeze must precede actual gate"
fixture = json.loads((out / "fixture.json").read_text())
assert fixture["candidateSha"] == head
state = json.loads((out / "current-f03-local-api.json").read_text())
assert state["startupReadinessVerified"] is True and len(state["processesAtStart"]) == 7
policy = json.loads((out / "input-policy-before/summary.json").read_text())
assert policy["inputOnly"] is True and policy["actualSourceSha"] == head
assert policy["actualDatabase"] == "clpm_critical_20261017_local"
dest = out / "input-only-before-gate"
dest.mkdir(exist_ok=False)
for source_name, frozen_name in (("current-f03-local-api.json", "started-state.json"), ("environment-safe.json", "environment-before-gate.json")):
    (dest / frozen_name).write_bytes((out / source_name).read_bytes())
files = [out / name for name in ("fixture.json", "fixture.inputs.json", "fixture.imports.json", "fixture.bindings.json", "fixture.td-readback.json", "source-tracked-catalog.json", "startup-ready-before-pause.json", "startup-pidfile.raw.log", "startup-api.raw.log", "startup-beat.raw.log", "startup-worker.raw.log")]
files += sorted((out / "input-policy-before").iterdir())
files += [dest / "started-state.json", dest / "environment-before-gate.json"]
assert len(files) == 21 and all(p.is_file() for p in files)
refs = [{"path": str(p), "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
manifest = {"frozenAt": datetime.now(timezone.utc).isoformat(), "candidateSha": head, "runId": run["runId"], "calculationHasNotBeenDispatched": True, "actualMetricsIncluded": False, "mutablePhaseFilesCopiedBeforeHashing": True, "runConfig": {"path": str(run_file), "sha256": hashlib.sha256(run_file.read_bytes()).hexdigest()}, "executedSourceSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "files": refs}
f = dest / "input-only-manifest.json"
f.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "items": len(refs)}))
