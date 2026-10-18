"""Offline exact PG numeric comparison boundary, with no service operations."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

source, output = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("input_policy_probe", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
first = module.objects('{"v":0.12345678901234567890}\n')[0]["v"]
second = module.objects('{"v":0.12345678901234567891}\n')[0]["v"]
result = {"sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(), "first": str(first), "second": str(second), "different": first != second, "liveDatabaseNetworkOrProcesses": 0}
output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
assert first != second, "distinct exact PG numeric policies must not collapse to one float"
