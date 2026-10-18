"""Same genuine 1000-row audit aliased twenty times must never count as twenty."""
import importlib.util
import inspect
import json
from pathlib import Path
import sys

source, progress, plan = map(Path, sys.argv[1:])
sys.path.insert(0, str(source.parent))
spec = importlib.util.spec_from_file_location("reviewed_freeze", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
kwargs = {"planned_path": plan} if "planned_path" in inspect.signature(module.completed_inputs).parameters else {}
try:
    returned = module.completed_inputs(progress, **kwargs)
except ValueError as error:
    assert "Repeated" in str(error) or "duplicate" in str(error).lower(), str(error)
    print(json.dumps({"rejectedForDuplicateOriginals": True, "error": str(error)}))
else:
    raise AssertionError(f"One original 1000-object audit accepted as {returned['passed']} windows")
