"""Independent legacy parity on frozen pure ARX modules; no app/service load."""

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch
import warnings

import numpy as np


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def observe(module, u, y, delay=0, na=1, nb=1):
    matrices = []
    solver = np.linalg.lstsq

    def capture(matrix, response, **kwargs):
        matrices.append(
            {
                "matrixSha256": hashlib.sha256(matrix.tobytes()).hexdigest(),
                "responseSha256": hashlib.sha256(response.tobytes()).hexdigest(),
                "shape": list(matrix.shape),
                "dtype": str(matrix.dtype),
                "cContiguous": bool(matrix.flags.c_contiguous),
                "lagAt21": float(matrix[20, 0])
                if matrix.shape[0] > 20 and matrix.shape[1]
                else None,
            }
        )
        return solver(matrix, response, **kwargs)

    with (
        warnings.catch_warnings(),
        np.errstate(all="ignore"),
        patch("numpy.linalg.lstsq", side_effect=capture),
    ):
        warnings.simplefilter("ignore")
        try:
            result = module.identify_arx(u, y, delay, na=na, nb=nb)
            return {
                "matrices": matrices,
                "result": asdict(result),
                "stable": result.is_stable,
            }
        except Exception as exc:
            return {
                "matrices": matrices,
                "errorType": type(exc).__name__,
                "error": str(exc),
            }


class ScalarShift(np.ndarray):
    def __getitem__(self, index):
        result = super().__getitem__(index)
        return result + 5 if isinstance(index, int) else result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--root-probe", type=Path, required=True)
    parser.add_argument("--only-root", action="store_true")
    args = parser.parse_args()
    old = load(args.legacy, "arx_independent_legacy")
    new = load(args.candidate, "arx_independent_candidate")
    checks = []

    def compare(label, u, y, delay=0, na=1, nb=1):
        before = observe(old, u, y, delay, na, nb)
        after = observe(new, u, y, delay, na, nb)
        checks.append(
            {
                "case": label,
                "equal": before == after,
                "legacy": before,
                "candidate": after,
            }
        )

    extreme = [index % 3 for index in range(40)]
    extreme[20] = -(2**63)
    root_u = np.random.default_rng(13).normal(size=40)
    compare("root-min-int64-python-list", root_u, extreme)
    root = json.loads(args.root_probe.read_bytes())
    expected_sha = next(
        row["matrixSha256"] for row in root["results"] if row["version"] == "old"
    )
    assert checks[0]["legacy"]["matrices"][0]["matrixSha256"] == expected_sha
    if not args.only_root:
        rng = np.random.default_rng(71831)
        for dtype in [np.float32, np.float64, np.int8, np.int64, np.uint64]:
            for stride in [1, 2, -1]:
                raw_u = (rng.normal(size=240) * 4).astype(dtype)
                raw_y = (rng.normal(size=240) * 4).astype(dtype)
                u, y = raw_u[::stride][:80], raw_y[::stride][:80]
                for na, nb, delay in [
                    (1, 1, 0),
                    (3, 2, 7),
                    (0, 2, 1),
                    (2, 0, 0),
                    (0, 0, 0),
                ]:
                    compare(
                        f"{np.dtype(dtype).name}:stride{stride}:a{na}:b{nb}:d{delay}",
                        u,
                        y,
                        delay,
                        na,
                        nb,
                    )
        for kind, y in [
            ("python-tuple-extreme", tuple(extreme)),
            ("numpy-int64-extreme", np.array(extreme, dtype=np.int64)),
            (
                "python-unbounded-integer",
                [
                    10**30 if index == 20 else value
                    for index, value in enumerate(extreme)
                ],
            ),
            ("numpy-scalar-subclass", np.array(extreme, dtype=float).view(ScalarShift)),
        ]:
            compare(kind, root_u, y)
        compare("short-u-index-error", np.arange(12.0), np.arange(40.0))
        compare("short-y-validation", np.arange(4.0), np.arange(4.0), 3, 2, 2)
        compare("negative-delay-old-error", np.arange(40.0), np.arange(40.0), -1)
        compare(
            "negative-order-old-semantics", np.arange(40.0), np.arange(40.0), 0, -1, 2
        )
        compare(
            "boolean-negation-old-error",
            np.ones(40, dtype=bool),
            np.ones(40, dtype=bool),
        )
    report = {
        "scope": "Pure modules; every comparison uses original scalar routine, exact matrix/response bytes and full result or exception",
        "cases": len(checks),
        "passed": sum(row["equal"] for row in checks),
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    assert all(row["equal"] for row in checks), (
        "Frozen ARX candidate changes the legacy input semantics"
    )


if __name__ == "__main__":
    main()
