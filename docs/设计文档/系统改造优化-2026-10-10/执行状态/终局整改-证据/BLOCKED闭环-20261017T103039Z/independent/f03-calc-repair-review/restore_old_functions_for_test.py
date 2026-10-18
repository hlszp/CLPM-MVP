"""Test-process-only baseline replacement; no disk edits or service accesses."""

import ast
import hashlib
import importlib
import json
from pathlib import Path


BASE = Path(__file__).resolve().parent / "old-edfa"


def pytest_configure(config):
    scopes = [
        ("app.services.calc_snapshot", "backend/app/services/calc_snapshot.py", {"serialize_data_blocks", "deserialize_data_blocks"}),
        ("app.tasks.kpi_calc", "backend/app/tasks/kpi_calc.py", {"_do_calculate_custom_batch"}),
        ("app.api.v1.endpoints.tasks", "backend/app/api/v1/endpoints/tasks.py", {"trigger_custom_evaluation"}),
    ]
    for name, relative, expected in scopes:
        target = importlib.import_module(name)
        path = BASE / relative
        raw = path.read_bytes()
        nodes = [node for node in ast.parse(raw).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in expected]
        assert {node.name for node in nodes} == expected
        for node in nodes:
            node.decorator_list = []
        tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
        ast.fix_missing_locations(tree)
        exec(compile(tree, str(path), "exec"), target.__dict__)
        print(json.dumps({"testProcessOnly": True, "baselinePath": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "functions": sorted(expected)}))
