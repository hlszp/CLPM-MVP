"""Offline cross-check of audit histograms against frozen source generator only.
No application module imports, DB/Redis/TD clients, generated writes or tasks.
"""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
from audit_capacity_inputs import expected_quality_histogram

source = Path(sys.argv[1])
out = Path(sys.argv[2]); out.mkdir(exist_ok=False)
raw = source.read_bytes()
(out/"generator-source.py").write_bytes(raw)
tree = ast.parse(raw)
node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "synth_series")
namespace = {"np":np,"POINTS":3600}
exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(source),"exec"),namespace)
observations = []
for index in range(801,901):
    category = "steady" if index%10<7 else "oscillation" if index%10==7 else "drift" if index%10==8 else "noisy"
    original = namespace["synth_series"](index,"insufficient",category,100.0)
    actual = dict(Counter(row[2] for row in original["PV"]))
    expected = expected_quality_histogram(index,"insufficient","PV")
    observations.append({"index":index,"seed":index,"responseCategory":category,"sourceHistogram":actual,"independentHistogram":expected,"matches":actual==expected})
result={"status":"PASS" if all(row["matches"] for row in observations) else "REWORK","source":str(source),"sourceSha256":hashlib.sha256(raw).hexdigest(),"numpyVersion":np.__version__,"checks":observations,"boundary":"Verifies source synthetic quality reconstruction only; does not establish actual TD whole-window value hashes."}
(out/"quality-reference.json").write_text(json.dumps(result,sort_keys=True,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"status":result["status"],"seedChecks":len(observations),"numpyVersion":np.__version__,"sourceSha256":result["sourceSha256"]}))
raise SystemExit(0 if result["status"]=="PASS" else 1)
