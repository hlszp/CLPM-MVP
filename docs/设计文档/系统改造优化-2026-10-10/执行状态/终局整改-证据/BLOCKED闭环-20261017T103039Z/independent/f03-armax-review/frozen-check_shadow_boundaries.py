"""Compare immutable legacy and shadow matrix bytes/results; no wall-clock assertions."""

from __future__ import annotations
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import warnings

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, "/Users/zhangping/DEV/clpm-remediation-20261017/backend")


def load(variant):
    name = "armax_boundary_" + variant
    spec = importlib.util.spec_from_file_location(name, ROOT / f"armax.{variant}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def array_record(array):
    return {
        "shape": list(array.shape),
        "dtype": array.dtype.str,
        "strides": list(array.strides),
        "sha256": hashlib.sha256(array.tobytes(order="C")).hexdigest(),
    }


def run(module, u, y, parameters):
    matrices = []

    class LinalgProxy:
        def __getattr__(self, name):
            return getattr(np.linalg, name)

        def lstsq(self, a, b, *args, **kwargs):
            matrices.append({"a": array_record(a), "b": array_record(b)})
            return np.linalg.lstsq(a, b, *args, **kwargs)

    class NumpyProxy:
        linalg = LinalgProxy()

        def __getattr__(self, name):
            return getattr(np, name)

    module.np = NumpyProxy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            result = module.identify_armax(u, y, **parameters)
            outcome = {"result": dataclasses.asdict(result)}
        except Exception as error:
            outcome = {"exception": type(error).__name__, "message": str(error)}
    return {"outcome": outcome, "matrices": matrices}


original, prototype = load("original"), load("prototype")
rng = np.random.default_rng(13)
base_u, base_y = rng.normal(size=60), rng.normal(size=60)
cases = []


def case(name, u, y, **parameters):
    params = {"d": 0, "na": 1, "nb": 1, "nc": 1, "max_iter": 7}
    params.update(parameters)
    cases.append((name, u, y, params))


for na, nb, nc, d in [
    (1, 1, 1, 0),
    (2, 2, 2, 3),
    (3, 1, 0, 2),
    (0, 1, 0, 0),
    (1, 0, 2, 0),
    (0, 0, 0, 0),
    (1, 1, 70, 1),
]:
    case(f"order-{na}-{nb}-{nc}-{d}", base_u, base_y, na=na, nb=nb, nc=nc, d=d)
for dtype in [
    np.float16,
    np.float32,
    np.float64,
    np.int8,
    np.int64,
    np.uint8,
    np.dtype(">f8"),
    np.complex128,
]:
    case("dtype-" + str(dtype), (base_u * 5).astype(dtype), (base_y * 5).astype(dtype))
case("list", base_u.tolist(), base_y.tolist())
case("tuple", tuple(base_u), tuple(base_y))
extreme = [i % 3 for i in range(60)]
extreme[20] = -(2**63)
case("python-int-list-min64", base_u, extreme)
case("ndarray-int64-min64", base_u, np.array(extreme, dtype=np.int64))
case(
    "signed-zero",
    np.resize(np.array([0.0, -0.0]), 60),
    np.resize(np.array([-0.0, 0.0]), 60),
)
case("negative-stride", base_u[::-1], base_y[::-1])
case("column-vector", base_u.reshape(-1, 1), base_y.reshape(-1, 1))
case("strided-view", np.repeat(base_u, 2)[::2], np.repeat(base_y, 2)[::2])
case(
    "masked-subclass", np.ma.array(base_u, mask=False), np.ma.array(base_y, mask=False)
)
case("too-short-u", base_u[:20], base_y)
case("negative-delay-short-u", base_u, base_y, d=-1)
case("negative-delay-enough-u", np.r_[base_u, 1.0], base_y, d=-1)
case("negative-order", base_u, base_y, na=-1, nb=2)
case("zero-iterations", base_u, base_y, max_iter=0)
case("too-short-y", base_u[:8], base_y[:8])
read_only = base_y.copy()
read_only.setflags(write=False)
case("read-only", base_u, read_only)
for index in range(20):
    n = 32 + index * 3
    case(
        f"random-{index}",
        rng.normal(size=n),
        rng.normal(size=n),
        na=index % 3,
        nb=(index % 2) + 1,
        nc=index % 4,
        d=index % 5,
    )

records = []
for name, u, y, parameters in cases:
    old = run(original, u, y, parameters)
    new = run(prototype, u, y, parameters)
    # Serialized comparison preserves signed zero and all returned fields, including iterations.
    equal = json.dumps(old, sort_keys=True) == json.dumps(new, sort_keys=True)
    records.append(
        {
            "name": name,
            "parameters": parameters,
            "equal": equal,
            "original": old,
            "prototype": new,
        }
    )
output = {
    "scope": "immutable legacy equivalence, not old algorithm wrong/new algorithm right",
    "cases": len(records),
    "passed": sum(item["equal"] for item in records),
    "records": records,
}
(ROOT / "boundary-results.json").write_text(
    json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
)
print(
    json.dumps(
        {
            "cases": output["cases"],
            "passed": output["passed"],
            "mismatches": [r["name"] for r in records if not r["equal"]],
        }
    )
)
raise SystemExit(0 if output["cases"] == output["passed"] else 1)
