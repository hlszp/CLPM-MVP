"""Compare frozen old/new ARMAX calls and every least-squares matrix exactly."""

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import warnings

import numpy as np


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.identify_armax


def array_identity(value):
    value = np.asarray(value)
    return {
        "shape": list(value.shape),
        "dtype": str(value.dtype),
        "bytesSha256": digest(value.tobytes(order="C")),
    }


def invoke(function, args, kwargs):
    matrices = []
    original = np.linalg.lstsq

    def capture(matrix, target, *extra, **options):
        matrices.append(
            {"matrix": array_identity(matrix), "target": array_identity(target)}
        )
        return original(matrix, target, *extra, **options)

    np.linalg.lstsq = capture
    try:
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always")
            try:
                result = {"value": asdict(function(*args, **kwargs))}
            except Exception as error:
                result = {
                    "errorType": type(error).__module__ + "." + type(error).__qualname__,
                    "errorMessage": str(error),
                }
        result["warnings"] = [
            [type(item.message).__module__ + "." + type(item.message).__qualname__, str(item.message)]
            for item in observed
        ]
        result["leastSquares"] = matrices
        return result
    finally:
        np.linalg.lstsq = original


def cases():
    rng = np.random.default_rng(20261017)
    raw_u = rng.normal(size=260)
    raw_y = rng.normal(size=260)
    u, y = raw_u[:128], raw_y[:128]
    normal = {"d": 1, "na": 2, "nb": 2, "nc": 2, "max_iter": 4}
    yield "float64", (u, y), normal
    yield "float32", (u.astype(np.float32), y.astype(np.float32)), normal
    yield "noncontiguous", (raw_u[::2], raw_y[::2]), normal
    yield "negative-stride", (u[::-1], y[::-1]), normal
    yield "list", (u.tolist(), y.tolist()), normal
    yield "tuple", (tuple(u.tolist()), tuple(y.tolist())), normal
    yield "minimum-int64-list", ([1, -2**63, 2, 0] * 24, [0, 1, -2**63, 2] * 24), normal
    yield "big-python-int-list", ([1, 2**80, 2, 0] * 24, [0, 1, -(2**80), 2] * 24), normal
    yield "int64-array", (np.arange(96), np.arange(96)[::-1]), normal
    yield "column-vector", (u.reshape(-1, 1), y.reshape(-1, 1)), normal
    yield "masked-array", (np.ma.array(u, mask=np.arange(128) % 7 == 0), np.ma.array(y, mask=np.arange(128) % 11 == 0)), normal
    yield "nan", (u, np.where(np.arange(128) == 10, np.nan, y)), normal
    yield "inf", (u, np.where(np.arange(128) == 10, np.inf, y)), normal
    yield "short-input", (u[:4], y[:4]), normal
    yield "short-u", (u[:5], y), normal
    yield "empty-input", (u[:0], y[:0]), normal
    yield "empty-na", (u, y), {**normal, "na": 0}
    yield "empty-nb", (u, y), {**normal, "nb": 0}
    yield "empty-nc", (u, y), {**normal, "nc": 0}
    yield "negative-delay", (u, y), {**normal, "d": -2}
    yield "negative-na", (u, y), {**normal, "na": -1}
    yield "negative-nc", (u, y), {**normal, "nc": -1}
    yield "zero-iterations", (u, y), {**normal, "max_iter": 0}
    yield "zero-tolerance", (u, y), {**normal, "tol": 0}
    yield "rows-shorter-than-nc", (u[:12], y[:12]), {**normal, "nc": 15}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.backend))
    old = load("independent_armax_old", args.old)
    new = load("independent_armax_new", args.new)
    results = []
    for name, inputs, parameters in cases():
        before = invoke(old, inputs, parameters)
        after = invoke(new, inputs, parameters)
        exact = json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
        results.append({"case": name, "parameters": parameters, "old": before, "new": after, "exact": exact})
    outcome = {
        "oldSha256": digest(args.old.read_bytes()),
        "newSha256": digest(args.new.read_bytes()),
        "arxDependencySha256": digest((args.backend / "app/services/tuning_identification/arx.py").read_bytes()),
        "cases": results,
        "passed": sum(row["exact"] for row in results),
        "failed": sum(not row["exact"] for row in results),
        "scope": "Exact boundary and least-squares input comparison; not the original 22 full integration cases or critical business KPI oracle",
    }
    args.out.write_text(json.dumps(outcome, indent=2, allow_nan=True) + "\n")
    print(json.dumps({key: outcome[key] for key in ("oldSha256", "newSha256", "passed", "failed")}, indent=2))
    return 1 if outcome["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
