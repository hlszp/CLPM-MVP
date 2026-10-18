"""Replay the unchanged non-author row counterexample against an explicit source.

No network, environment configuration, application import, or data write.
The expected rejection is an assertion so the old source exits nonzero.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

source, original = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("endpoint_subject", source)
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)
case = json.loads(original.read_bytes())
actual = case["actualRow"]
failures = subject.evaluate_chunk(
    [actual["point_id"]], actual["ts"], 1, [actual]
)
print(
    json.dumps(
        {
            "source": str(source),
            "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "unchangedNonAuthorOriginal": str(original),
            "originalSha256": hashlib.sha256(original.read_bytes()).hexdigest(),
            "failures": failures,
            "scope": "Pure original row counterexample only; no network",
        }
    )
)
assert failures, "Incomplete raw endpoint row was accepted as complete"
