"""Collect one network-denied G3 numerical diagnosis after the real CI tests."""

from __future__ import annotations

import ast
import cProfile
import hashlib
import io
import json
import logging
import math
import os
import platform
import pstats
import resource
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import CodeType

THREAD_KEYS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
ROLE = "PURE_ALGORITHM_NUMERICAL_DIAGNOSIS_NOT_FORMAL_TEST_OR_ACCEPTANCE"
np = None


def utc_now():
    return datetime.now(UTC).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_value(value):
    if isinstance(value, Enum):
        return json_value(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if np is not None:
        if isinstance(value, np.ndarray):
            return json_value(value.tolist())
        if isinstance(value, np.generic):
            return json_value(value.item())
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return {
            "nonFiniteFloat": "NAN"
            if math.isnan(value)
            else "POS_INF"
            if value > 0
            else "NEG_INF"
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unsupported evidence value type: " + type(value).__name__)


def write_json(path, value):
    path.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n"
    )


def file_ref(path, base):
    raw = path.read_bytes()
    return {
        "file": str(path.relative_to(base)),
        "bytes": len(raw),
        "sha256": digest(raw),
    }


def main():
    global np
    out, root = (Path(arg).resolve() for arg in sys.argv[1:])
    if out.is_relative_to(root):
        raise ValueError(
            "Diagnostic output must be outside the checked-out source tree"
        )
    out.mkdir(parents=True, exist_ok=False)
    command = {"argv": sys.argv, "startedAt": utc_now(), "evidenceRole": ROLE}
    network_attempts, selections, armax_returns = [], [], []
    source_paths, before = [], []
    profiler = cProfile.Profile()
    rc = 2

    def no_network(event, args):
        if event in (
            "socket.connect",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.getnameinfo",
            "socket.sendto",
            "socket.sendmsg",
        ):
            network_attempts.append(event)
            raise RuntimeError("Network forbidden in numerical diagnosis")
        if event == "import" and args[0] == "app.main":
            raise RuntimeError("app.main forbidden in numerical diagnosis")

    try:
        sys.addaudithook(no_network)
        os.nice(10)
        resource.setrlimit(resource.RLIMIT_CPU, (45, 50))
        sys.path.insert(0, str(root / "backend"))
        import numpy as numpy_module
        import scipy
        from scipy import signal as sps

        from app.services.tuning_identification import pipeline

        np = numpy_module
        package = root / "backend/app/services/tuning_identification"
        fixture = root / "backend/tests/test_g3_app_applicability.py"
        source_paths = sorted(package.glob("*.py")) + [
            Path(__file__).resolve(),
            fixture,
            root / "backend/uv.lock",
            root / "backend/pyproject.toml",
            root / "backend/tests/golden/refactor_protected_manifest.json",
        ]
        before = [file_ref(path, root) for path in source_paths]
        for path in source_paths:
            copy = out / "source" / path.relative_to(root)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(path.read_bytes())
        git_commands = {}
        for label, args in {
            "head": ["rev-parse", "HEAD"],
            "root": ["rev-parse", "--show-toplevel"],
            "backendTree": ["rev-parse", "HEAD:backend"],
        }.items():
            process = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, check=False
            )
            stdout, stderr = (
                out / ("git-" + label + ".stdout.log"),
                out / ("git-" + label + ".stderr.log"),
            )
            stdout.write_bytes(process.stdout)
            stderr.write_bytes(process.stderr)
            git_commands[label] = {
                "argv": ["git", *args],
                "rc": process.returncode,
                "stdout": process.stdout.decode().strip(),
                "stdoutRef": file_ref(stdout, out),
                "stderrRef": file_ref(stderr, out),
            }
            if process.returncode:
                raise ValueError("Required Git identity read failed: " + label)
        environment = {
            "evidenceRole": ROLE,
            "githubSha": os.environ.get("GITHUB_SHA"),
            "pytestStepOutcome": os.environ.get("G3_PYTEST_OUTCOME"),
            "git": git_commands,
            "platform": platform.platform(),
            "python": sys.version,
            "executable": sys.executable,
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "numpyConfig": np.show_config(mode="dicts"),
            "numpyCpuFeatures": np._core._multiarray_umath.__cpu_features__,
            "cpuCount": os.cpu_count(),
            "affinity": sorted(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None,
            "threads": {key: os.environ.get(key) for key in THREAD_KEYS},
            "CLPM_TEST_MODE": os.environ.get("CLPM_TEST_MODE"),
            "CLPM_SKIP_EXIT_HOOKS": os.environ.get("CLPM_SKIP_EXIT_HOOKS"),
            "diagnosticCpuSoftLimitSeconds": 45,
            "niceIncrement": 10,
        }
        write_json(out / "environment.json", environment)
        if (
            environment["githubSha"]
            and environment["githubSha"] != git_commands["head"]["stdout"]
        ):
            raise ValueError("GITHUB_SHA differs from the actual checked-out HEAD")
        if any(value != "1" for value in environment["threads"].values()):
            raise ValueError("The existing frozen CI thread profile is not present")
        source = package / "pipeline.py"
        tree = ast.parse(source.read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "identify_from_history"
        )
        expected = ast.parse(
            "joint_best is not None and _candidate_sort_key(joint_best) "
            "> _candidate_sort_key(best_r.best_model)",
            mode="eval",
        ).body
        anchors = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.If) and ast.dump(node.test) == ast.dump(expected)
        ]
        compiled = next(
            value
            for value in compile(source.read_text(), str(source), "exec").co_consts
            if isinstance(value, CodeType) and value.co_name == "identify_from_history"
        )
        actual = pipeline.identify_from_history.__code__
        if (
            len(anchors) != 1
            or Path(actual.co_filename).resolve() != source
            or actual.co_firstlineno != function.lineno
            or actual.co_code != compiled.co_code
        ):
            raise ValueError(
                "Actual callable/source/unique candidate-selection AST do not correspond"
            )
        selection_line = anchors[0].lineno
        write_json(
            out / "source-identity.json",
            {
                "files": before,
                "pipelineSha256": digest(source.read_bytes()),
                "selectionLine": selection_line,
                "selectionConditionAst": ast.dump(expected),
                "callableFirstLine": actual.co_firstlineno,
            },
        )
        fixture_tree = ast.parse(fixture.read_text())
        names = {"_seg_fopdt", "_two_segments_fopdt", "_independent_joint_snr_db"}
        helpers = [
            node
            for node in fixture_tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        cls = next(
            node
            for node in fixture_tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "TestJointWinnerLowSnrAnnotated"
        )
        constants = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in cls.body
            if isinstance(node, ast.Assign)
        }
        run = next(
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "_run"
        )
        calls = [
            node
            for node in ast.walk(run)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_two_segments_fopdt"
        ]
        if (
            len(helpers) != 3
            or len(calls) != 1
            or ast.unparse(calls[0])
            != "_two_segments_fopdt(self.NOISE, 700, self.SEED)"
        ):
            raise ValueError("Original fixed fixture contract changed")
        namespace = {"math": math, "np": np, "sps": sps}
        exec(
            compile(ast.Module(body=helpers, type_ignores=[]), str(fixture), "exec"),
            namespace,
        )
        op, pv = namespace["_two_segments_fopdt"](
            constants["NOISE"], 700, constants["SEED"]
        )
        for role, array in (("op", op), ("pv", pv)):
            np.save(out / (role + ".npy"), array, allow_pickle=False)
        write_json(
            out / "input-identity.json",
            {
                "fixtureSourceSha256": digest(fixture.read_bytes()),
                "helperAstSha256": digest(
                    ast.dump(ast.Module(body=helpers, type_ignores=[])).encode()
                ),
                "noise": constants["NOISE"],
                "seed": constants["SEED"],
                "segmentPoints": 700,
                "op": file_ref(out / "op.npy", out),
                "pv": file_ref(out / "pv.npy", out),
                "opRawSha256": digest(op.tobytes()),
                "pvRawSha256": digest(pv.tobytes()),
            },
        )
        logging.basicConfig(filename=out / "algorithm-debug.log", level=logging.DEBUG)
        observed_frames = set()

        def armax_trace(frame, event, arg):
            if event == "return":
                values = frame.f_locals
                inputs = []
                for role in ("u", "y"):
                    array = values.get(role)
                    if isinstance(array, np.ndarray):
                        path = out / f"armax-{len(armax_returns) + 1:02d}-{role}.npy"
                        np.save(path, array, allow_pickle=False)
                        inputs.append(
                            {
                                "role": role,
                                **file_ref(path, out),
                                "rawSha256": digest(array.tobytes()),
                            }
                        )
                delta, tol = values.get("max_diff"), values.get("tol")
                armax_returns.append(
                    {
                        "sourceSha256": digest(
                            Path(frame.f_code.co_filename).read_bytes()
                        ),
                        "inputs": inputs,
                        "na": values.get("na"),
                        "nb": values.get("nb"),
                        "nc": values.get("nc"),
                        "d": values.get("d"),
                        "maxIter": values.get("max_iter"),
                        "tol": tol,
                        "iterationsDone": values.get("iterations_done"),
                        "lastMaxDiff": delta,
                        "metActualConvergenceCriterion": bool(delta < tol)
                        if delta is not None and tol is not None
                        else None,
                        "a": np.asarray(values.get("a_coeffs", [])).tolist(),
                        "b": np.asarray(values.get("b_coeffs", [])).tolist(),
                        "c": np.asarray(values.get("c_coeffs", [])).tolist(),
                        "returnedResult": arg,
                    }
                )
            return armax_trace

        def selection_trace(frame, event, arg):
            if (
                event == "line"
                and frame.f_lineno == selection_line
                and id(frame) not in observed_frames
            ):
                observed_frames.add(id(frame))
                values = frame.f_locals
                selections.append(
                    {
                        "sourceLine": selection_line,
                        "singleBest": values["best_r"].best_model,
                        "jointBest": values.get("joint_best"),
                        "jointCandidates": values.get("joint_cands"),
                        "windowResults": [
                            {
                                "start": start,
                                "end": end,
                                "sortKey": key,
                                "result": result,
                            }
                            for key, result, start, end in values["_ok"]
                        ],
                        "jointAudit": values.get("joint_audit"),
                    }
                )
                write_json(out / "selection-events.json", selections)
            return selection_trace

        def trace_calls(frame, event, arg):
            if event == "call" and frame.f_code is actual:
                return selection_trace
            if event == "call" and frame.f_code is pipeline.identify_armax.__code__:
                frame.f_trace_lines = False
                return armax_trace
            return None

        began = time.monotonic()
        sys.settrace(trace_calls)
        profiler.enable()
        try:
            result = pipeline.identify_from_history(
                op=op.tolist(), pv=pv.tolist(), ts=1.0
            )
        finally:
            profiler.disable()
            sys.settrace(None)
        write_json(out / "armax-return-observations.json", armax_returns)
        write_json(out / "result-full.json", result)
        write_json(out / "result-public.json", result.to_dict())
        write_json(
            out / "summary.json",
            {
                "evidenceRole": ROLE,
                "elapsedSeconds": time.monotonic() - began,
                "identificationSuccess": result.success,
                "jointSelected": bool(
                    result.best_model
                    and result.best_model.evidence
                    and result.best_model.evidence.joint_estimate
                ),
                "selectionObservations": len(selections),
                "armaxObservations": len(armax_returns),
                "networkAttempts": network_attempts,
                "appMainImported": "app.main" in sys.modules,
            },
        )
        if (
            not selections
            or not armax_returns
            or network_attempts
            or "app.main" in sys.modules
        ):
            raise ValueError(
                "Required pure-algorithm observation boundary was not satisfied"
            )
        rc = 0
    except BaseException as exc:
        write_json(
            out / "diagnostic-error.json",
            {
                "errorType": type(exc).__name__,
                "frames": [
                    {
                        "file": frame.filename,
                        "line": frame.lineno,
                        "function": frame.name,
                    }
                    for frame in traceback.extract_tb(exc.__traceback__)
                ],
                "note": "Diagnostic failed; exception values and secrets are not logged",
            },
        )
    finally:
        profiler.disable()
        sys.settrace(None)
        profiler.dump_stats(out / "algorithm.cprofile")
        stream = io.StringIO()
        if profiler.getstats():
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
                "cumulative"
            ).print_stats(60)
        else:
            stream.write(
                "No algorithm profiling samples; diagnosis did not reach execution.\n"
            )
        (out / "profile-cumulative.txt").write_text(stream.getvalue())
        after = [file_ref(path, root) for path in source_paths]
        if before != after:
            rc = 2
        command.update(
            finishedAt=utc_now(),
            rc=rc,
            sourceBefore=before,
            sourceAfter=after,
            networkAttempts=network_attempts,
        )
        write_json(out / "command.json", command)
        write_json(
            out / "diagnostic-status.json",
            {
                "evidenceRole": ROLE,
                "status": "COLLECTED" if rc == 0 else "DIAGNOSTIC_FAILED",
                "rc": rc,
                "acceptance": "NOT_PERFORMED",
            },
        )
        write_json(
            out / "sha256-manifest.json",
            {
                "files": [
                    file_ref(path, out)
                    for path in sorted(out.rglob("*"))
                    if path.is_file()
                ]
            },
        )
    print(json.dumps({"evidenceRole": ROLE, "rc": rc, "output": str(out)}))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
