"""Offline shadow only; fixed original 22-loop test, no candidate file edits."""

from __future__ import annotations
import argparse
import cProfile
import dataclasses
import enum
import hashlib
import importlib.util
import inspect
import json
import logging
from pathlib import Path
import pstats
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parent
CANDIDATE = Path("/Users/zhangping/DEV/clpm-remediation-20261017")
parser = argparse.ArgumentParser()
parser.add_argument("variant", choices=["original", "prototype"])
args = parser.parse_args()
sys.path.insert(0, str(CANDIDATE / "backend"))
logging.disable(logging.CRITICAL)


def module_from(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def serializable(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def array_record(array):
    return {
        "shape": list(array.shape),
        "dtype": array.dtype.str,
        "strides": list(array.strides),
        "sha256": hashlib.sha256(array.tobytes(order="C")).hexdigest(),
    }


shadow = module_from(ROOT / f"armax.{args.variant}.py", f"armax_shadow_{args.variant}")
matrices, armax_calls, frozen_arrays = [], [], {}


class LinalgProxy:
    def __getattr__(self, name):
        return getattr(np.linalg, name)

    def lstsq(self, a, b, *positional, **keywords):
        matrices.append(
            {"sequence": len(matrices), "a": array_record(a), "b": array_record(b)}
        )
        return np.linalg.lstsq(a, b, *positional, **keywords)


class NumpyProxy:
    linalg = LinalgProxy()

    def __getattr__(self, name):
        return getattr(np, name)


shadow.np = NumpyProxy()
identify_armax = shadow.identify_armax
signature = inspect.signature(identify_armax)


def record_armax(*positional, **keywords):
    bound = signature.bind(*positional, **keywords)
    bound.apply_defaults()
    call = {
        "sequence": len(armax_calls),
        "parameters": {k: v for k, v in bound.arguments.items() if k not in {"u", "y"}},
    }
    for key in ["u", "y"]:
        array = bound.arguments[key]
        name = f"call_{len(armax_calls):03}_{key}"
        frozen_arrays[name] = array.copy()
        call[key] = {"npzKey": name, **array_record(array)}
    armax_calls.append(call)
    result = identify_armax(*positional, **keywords)
    call["result"] = dataclasses.asdict(result)
    return result


from app.services.tuning_identification import pipeline

pipeline.identify_armax = record_armax
module = module_from(
    CANDIDATE / "backend/tests/test_annotated_loops_evaluation.py",
    f"annotated_{args.variant}",
)
evaluate = module._evaluate_loop
identify = module.identify_from_history
loop_results, identifications = [], []


def record_identification(**keywords):
    result = identify(**keywords)
    identifications.append(json.loads(json.dumps(result, default=serializable)))
    return result


def record_loop(loop, tolerance, index):
    result = evaluate(loop, tolerance, index)
    loop_results.append({"id": loop["id"], "result": result})
    return result


module._evaluate_loop = record_loop
module.identify_from_history = record_identification
profile = cProfile.Profile()
started = time.perf_counter()
profile.enable()
module.test_core_loops_identification_accuracy()
profile.disable()
elapsed = time.perf_counter() - started
prefix = ROOT / args.variant
profile.dump_stats(str(prefix.with_suffix(".cprofile")))
with prefix.with_suffix(".profile.txt").open("w") as stream:
    pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(50)
with prefix.with_suffix(".self-profile.txt").open("w") as stream:
    pstats.Stats(profile, stream=stream).sort_stats("tottime").print_stats(35)
prefix.with_suffix(".outputs.json").write_text(
    json.dumps(
        {"loops": loop_results, "identifications": identifications},
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    + "\n"
)
prefix.with_suffix(".matrix-bytes.json").write_text(
    json.dumps(matrices, sort_keys=True, indent=2) + "\n"
)
prefix.with_suffix(".armax-inputs.json").write_text(
    json.dumps(armax_calls, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
)
np.savez_compressed(prefix.with_suffix(".armax-inputs.npz"), **frozen_arrays)
summary = {
    "variant": args.variant,
    "profileSecondsIncludingEqualCaptureOverhead": elapsed,
    "originalLoopCount": len(loop_results),
    "fullIdentificationCount": len(identifications),
    "armaxCalls": len(armax_calls),
    "lstsqMatrices": len(matrices),
    "candidateFilesEdited": False,
    "actualBusinessTasks": 0,
    "timedPytestPass": False,
}
prefix.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary))
