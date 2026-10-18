"""Read only actual 24 input artifacts without opening any network connection."""

import json
from pathlib import Path
from capture_closed_window_endpoints import extra_gate
from freeze_capacity_configuration import completed_inputs, canonical, sha

here = Path(__file__).resolve().parent
progress = here / "input-series-audit-v2/progress.json"
normal_plan = here / "series-s01/planned-batches.json"
extra_complete = here / "extra-inputs/complete.json"
extra_plan = here / "extra-inputs/plan.json"
completed = completed_inputs(progress, normal_plan)
planned, done = extra_gate(extra_plan, extra_complete, completed)
body = {
    "scope": "Pure original-file gate only; no network, PG, API, or TD",
    "normalInputs": 20,
    "extraInputs": len(planned),
    "generationAndAuditClosed": done["preparedAndAudited"],
    "formal20Executed": done["formal20Executed"],
    "extraCompleteSha256": sha(extra_complete.read_bytes()),
    "endpointSourceSha256": sha((here / "capture_closed_window_endpoints.py").read_bytes()),
    "gate": "PASS",
    "extraPurposes": [row["purpose"] for row in planned],
}
with (here / "actual-twenty-plus-four-original-file-gate-v3.json").open("xb") as stream:
    stream.write(canonical(body))
print(json.dumps(body, ensure_ascii=False))
