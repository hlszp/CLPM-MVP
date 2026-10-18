"""Offline actual generator-to-original SQL literal evidence. No SQL executes."""
import csv
from datetime import datetime, UTC
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).parent
CAND = Path("/Users/zhangping/DEV/clpm-remediation-20261017")
sys.path.insert(0, str(CAND / "backend"))
sys.path.insert(0, str(CAND / "backend/scripts"))
os.environ["CLPM_SKIP_EXIT_HOOKS"] = "1"
os.environ["CLPM_G5_TRACE"] = "0"
path = CAND / "backend/scripts/generate_capacity_dataset.py"
producer = runpy.run_path(str(path))
series = producer["synth_series"](1, "normal", "steady", 100.0)
start = datetime(2026, 10, 17, 11, tzinfo=UTC)
report = {"scope": "Pure actual synth_series -> original _td_rows -> Python IEEE754 parse of SQL literals; no DB, API, Worker or SQL execution", "generatorSha256": hashlib.sha256(path.read_bytes()).hexdigest(), "roles": {}}
all_rows = {}
for index, role in enumerate(producer["ROLES"], 1):
    point = f"00000000-0000-0000-0000-{index:012}"
    rows = producer["_td_rows"](point, series[role], start)
    all_rows[role] = rows
    mismatches = []
    evidence = []
    for sample, sql in zip(series[role], rows, strict=True):
        fields = next(csv.reader([sql[1:-1]], quotechar="'", skipinitialspace=True))
        _, value, qc, raw = sample
        literal = fields[1]
        parsed = float(literal)
        if parsed.hex() != float(value).hex() or int(fields[2]) != raw or int(fields[3]) != qc:
            mismatches.append({"sample": sample, "fields": fields})
        if len(evidence) < 5:
            evidence.append({"sample": sample, "valueLiteral": literal, "originalHex": float(value).hex(), "parsedHex": parsed.hex()})
    report["roles"][role] = {"samples": len(rows), "mismatchCount": len(mismatches), "mismatches": mismatches, "examples": evidence}
(ROOT / "original-synth-inputs.json").write_text(json.dumps(series, indent=2) + "\n")
(ROOT / "original-td-sql-rows.json").write_text(json.dumps(all_rows, indent=2) + "\n")
(ROOT / "sql-literal-roundtrip.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"roles": {role: {"samples": value["samples"], "mismatchCount": value["mismatchCount"]} for role, value in report["roles"].items()}}, indent=2))
assert all(value["mismatchCount"] == 0 for value in report["roles"].values())
