"""Execute unchanged arm/except/finally AST fragments with local fake objects.

This isolates fault-control cleanup; it does not execute the operational driver.
"""

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


class Child:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout):
        assert timeout == 20
        return 0


class Handle:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def probe(failure):
    source = Path(os.environ["EXTRA_DRIVER_REVIEW_SOURCE"])
    module = ast.parse(source.read_text())
    main = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    outer = next(node for node in main.body if isinstance(node, ast.Try))
    arm = next(
        node
        for node in outer.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "fault"
    )
    selected = ast.Try(
        body=[arm], handlers=outer.handlers, orelse=[], finalbody=outer.finalbody
    )
    function = ast.FunctionDef(
        name="isolated_cleanup_probe",
        args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
        ),
        body=[
            ast.Assign(
                targets=[ast.Name(id="armed", ctx=ast.Store())],
                value=ast.Constant(False),
            ),
            selected,
        ],
        decorator_list=[],
    )
    program = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    calls, writes = [], []
    children, handles = [Child(), Child()], [Handle(), Handle()]
    sha = "a" * 40
    policies = {
        key: {"candidateSha": sha, "armed": value, "activeWindow": "recovery"}
        for key, value in [("arm", True), ("disarm", False)]
    }

    def checked(ref):
        return Path("not-a-real-policy"), json.dumps(policies[ref]).encode()

    def switch(ref, _current, _evidence):
        calls.append(ref)
        if (failure == "arm-health" and ref == "arm") or (
            failure == "disarm-health" and ref == "disarm"
        ):
            raise RuntimeError("Controlled offline health observation failure")

    namespace = {
        "fault": True,
        "role": "fault-recovery",
        "request": {
            "candidateSha": sha,
            "faultPolicy": {
                "currentPath": "not-a-real-policy",
                "arm": "arm",
                "disarm": "disarm",
            },
        },
        "Path": Path,
        "out": Path("not-a-real-output"),
        "checked": checked,
        "json": json,
        "switch_policy": switch,
        "collector": children[0],
        "receiver": children[1],
        "observer_handles": handles,
        "now": lambda: "explicit-fake-time",
        "write": lambda path, value: writes.append((str(path), value)),
        "subprocess": SimpleNamespace(TimeoutExpired=TimeoutError),
    }
    exec(compile(program, str(source), "exec"), namespace)
    raised = False
    try:
        namespace["isolated_cleanup_probe"]()
    except RuntimeError:
        raised = True
    return {
        "policyCalls": calls,
        "allOwnObserversTerminated": all(child.terminated for child in children),
        "allOwnHandlesClosed": all(handle.closed for handle in handles),
        "failureStillRaised": raised,
        "writes": writes,
    }


@pytest.mark.parametrize("failure", ["arm-health", "disarm-health", "none"])
def test_fault_policy_is_disarmed_and_observers_closed_with_failure_preserved(failure):
    actual = probe(failure)
    assert actual["policyCalls"] == ["arm", "disarm"], actual
    assert actual["allOwnObserversTerminated"], actual
    assert actual["allOwnHandlesClosed"], actual
    assert actual["failureStillRaised"] is (failure != "none"), actual
