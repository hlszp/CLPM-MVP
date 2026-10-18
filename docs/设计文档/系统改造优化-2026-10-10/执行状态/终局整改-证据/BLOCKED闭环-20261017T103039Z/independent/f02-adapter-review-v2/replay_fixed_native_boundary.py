"""Recheck the exact frozen original files; no fixture regeneration or services."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
SOURCE=Path(os.environ["CLPM_ADAPTER_SOURCE"])
spec=importlib.util.spec_from_file_location("replay_adapter",SOURCE)
subject=importlib.util.module_from_spec(spec);spec.loader.exec_module(subject)
originals=ROOT/"fixed-negative-originals"
plan=json.loads((originals/"replay-plan.json").read_text())
out=originals/("replay-sidecars-"+uuid.uuid4().hex)
a=subject.Adapter(originals,out)
runtime=a.json(plan["runtime"])
bundle=a.native_bundle(plan["summary"],plan["sampler"],plan["interpreter"],{"envFileSha256":"synthetic-env","taosConfigSha256":"synthetic-taos"},runtime)
try:
    subject.native_span(runtime,bundle,tuple(subject.instant(v) for v in plan["span"]))
except subject.MissingEvidence as error:
    a.reads.verify_unchanged();out.rmdir()
    print(json.dumps({"refused":True,"reason":str(error),"sourceSha256":subject.digest(SOURCE.read_bytes()),"sameOriginalFilesVerified":True}))
    raise SystemExit(0)
a.reads.verify_unchanged();out.rmdir()
print(json.dumps({"refused":False,"sourceSha256":subject.digest(SOURCE.read_bytes()),"sameOriginalFilesVerified":True,"failure":"No completed-before/start-after query pair brackets the business span"}))
raise SystemExit(1)
