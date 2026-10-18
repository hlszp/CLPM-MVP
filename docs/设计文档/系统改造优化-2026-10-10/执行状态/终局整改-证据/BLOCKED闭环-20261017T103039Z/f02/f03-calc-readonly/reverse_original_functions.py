"""Pytest shadow plugin: replace only three corrected contracts by frozen AST.

Runs in the test process only. Never edits either checkout or persisted evidence.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
from pathlib import Path

ORIGINAL = Path(__file__).resolve().parent / "original-source-edfa"


def pytest_configure(config):
    scopes = [
        ("app.services.calc_snapshot", "backend/app/services/calc_snapshot.py", ["serialize_data_blocks", "deserialize_data_blocks"]),
        ("app.tasks.kpi_calc", "backend/app/tasks/kpi_calc.py", ["_do_calculate_custom_batch"]),
        ("app.api.v1.endpoints.tasks", "backend/app/api/v1/endpoints/tasks.py", ["trigger_custom_evaluation"]),
    ]
    for module_name, relative, names in scopes:
        module = importlib.import_module(module_name)
        path = ORIGINAL / relative
        raw = path.read_bytes()
        tree = ast.parse(raw, filename=str(path))
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        assert {node.name for node in nodes} == set(names)
        for node in nodes:
            node.decorator_list = []
        shadow = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
        ast.fix_missing_locations(shadow)
        exec(compile(shadow, str(path), "exec"), module.__dict__)
        print(json.dumps({"shadowOnly": True, "module": module_name, "source": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "functions": names}))
